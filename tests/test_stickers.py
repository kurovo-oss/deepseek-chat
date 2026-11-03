"""表情库：八个情绪桶，按情绪挑，她能点名。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from qqbot import stickers


def settings(tmp_path):
    return SimpleNamespace(
        sticker_dir=tmp_path / "stickers",
        image_enabled=True,
        sticker_chance=1.0,
    )


def make_png(path: Path, payload: bytes = b"\x89PNG\r\n\x1a\nfakepng") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


class Rng:
    def __init__(self, value=0.0):
        self.value = value

    def random(self):
        return self.value

    def choice(self, seq):
        return seq[0]


# ---------------------------------------------------------------- 归桶
def test_tags_map_to_the_right_bucket():
    assert stickers.bucket_from_tags(["cute", "cat"]) == "卖萌"
    assert stickers.bucket_from_tags(["tired"]) == "早晚安"
    assert stickers.bucket_from_tags(["angry"]) == "生气"
    assert stickers.bucket_from_tags(["???"]) == "卖萌"


def test_mood_maps_to_the_right_bucket():
    assert stickers.bucket_from_mood({"tired": 0.8}) == "早晚安"
    assert stickers.bucket_from_mood({"miss": 0.7}) == "卖萌"
    assert stickers.bucket_from_mood({"warmth": 0.9}) == "开心"
    assert stickers.bucket_from_mood({}) == ""


def test_reply_words_win_over_mood():
    assert stickers.bucket_from_text("哼 我生气了") == "生气"
    assert stickers.bucket_from_text("晚安呀") == "早晚安"
    assert stickers.bucket_from_text("") == ""


# ---------------------------------------------------------------- 入库
def test_add_file_copies_into_the_bucket(tmp_path):
    settings_ = settings(tmp_path)
    source = make_png(tmp_path / "src" / "a.gif")

    row = stickers.add_file(settings_, source, "委屈", ["sad"])

    assert row["bucket"] == "委屈"
    assert (settings_.sticker_dir / "委屈").is_dir()
    assert stickers.items(settings_, "委屈")[0]["file"].startswith("委屈/")


def test_same_file_is_not_added_twice(tmp_path):
    settings_ = settings(tmp_path)
    source = make_png(tmp_path / "src" / "a.gif")

    stickers.add_file(settings_, source, "委屈")
    assert stickers.add_file(settings_, source, "生气") is None
    assert len(stickers.items(settings_)) == 1


def test_unknown_bucket_is_rejected(tmp_path):
    assert stickers.add_file(settings(tmp_path), make_png(tmp_path / "a.gif"), "暴怒") is None


# ---------------------------------------------------------------- 挑选
def test_pick_falls_back_to_another_bucket_when_empty(tmp_path):
    settings_ = settings(tmp_path)
    stickers.add_file(settings_, make_png(tmp_path / "a.gif"), "开心")

    path = stickers.pick(settings_, "委屈", rng=Rng())

    assert path is not None
    assert path.parent.name == "开心"


def test_pick_returns_none_when_library_is_empty(tmp_path):
    assert stickers.pick(settings(tmp_path), "委屈", rng=Rng()) is None


def test_pick_prefers_the_least_used(tmp_path):
    settings_ = settings(tmp_path)
    first = stickers.add_file(settings_, make_png(tmp_path / "a.gif", b"aaaa"), "开心")
    second = stickers.add_file(settings_, make_png(tmp_path / "b.gif", b"bbbb"), "开心")
    index = stickers.load_index(settings_)
    for row in index["items"]:
        if row["file"] == first["file"]:
            row["uses"] = 5
    stickers.save_index(settings_, index)

    path = stickers.pick(settings_, "开心", rng=Rng())

    assert path.name == Path(second["file"]).name


def test_pick_for_reply_uses_the_emotion_in_the_reply(tmp_path):
    settings_ = settings(tmp_path)
    stickers.add_file(settings_, make_png(tmp_path / "a.gif"), "生气")
    stickers.add_file(settings_, make_png(tmp_path / "b.gif"), "卖萌")

    path = stickers.pick_for_reply(settings_, "哼 我生气了", {"warmth": 0.9}, Rng())

    assert path.parent.name == "生气"


# ---------------------------------------------------------------- 她点名
def test_she_can_ask_for_a_bucket(tmp_path):
    cleaned, buckets = stickers.extract_tags("那我不说了\n{{表情:委屈}}")

    assert cleaned == "那我不说了\n"
    assert buckets == ["委屈"]


def test_emotion_word_is_translated_to_a_bucket():
    _, buckets = stickers.extract_tags("{{表情:难过}}")

    assert buckets == ["委屈"]


# ---------------------------------------------------------------- 初始化
class FakeImages:
    def __init__(self, paths):
        self.paths = paths

    def path_of(self, row):
        return self.paths.get(row["sha256"])


class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def list_images(self, limit=500):
        return self.rows


def test_seed_imports_what_he_sent(tmp_path):
    settings_ = settings(tmp_path)
    cat = make_png(tmp_path / "lib" / "cat.gif", b"cat")
    sad = make_png(tmp_path / "lib" / "sad.gif", b"sad")
    store = FakeStore(
        [
            {"sha256": "c", "tags": ["cat", "cute"]},
            {"sha256": "s", "tags": ["sad"]},
        ]
    )
    images = FakeImages({"c": cat, "s": sad})

    added = stickers.seed_from_collected(settings_, store, images)

    assert added == 2
    assert {"卖萌", "委屈"} <= {row["bucket"] for row in stickers.items(settings_)}


def test_seed_twice_adds_nothing(tmp_path):
    settings_ = settings(tmp_path)
    cat = make_png(tmp_path / "lib" / "cat.gif", b"cat")
    store = FakeStore([{"sha256": "c", "tags": ["cat"]}])
    images = FakeImages({"c": cat})

    stickers.seed_from_collected(settings_, store, images)

    assert stickers.seed_from_collected(settings_, store, images) == 0


def test_daily_bucket_is_accepted(tmp_path):
    settings_ = settings(tmp_path)

    assert stickers.add_file(settings_, make_png(tmp_path / "a.png"), "日常")


def test_prune_drops_missing_rows_and_empty_buckets(tmp_path):
    settings_ = settings(tmp_path)
    row = stickers.add_file(settings_, make_png(tmp_path / "a.png"), "开心")
    (settings_.sticker_dir / "开心" / Path(row["file"]).name).unlink()
    (settings_.sticker_dir / "无语").mkdir()

    removed = stickers.prune(settings_)

    assert removed == 3  # 一个索引条目 + 两个空目录（开心、无语）
    assert stickers.items(settings_) == []
    assert not (settings_.sticker_dir / "无语").exists()
    assert not (settings_.sticker_dir / "开心").exists()


def test_no_emotion_falls_back_to_something_safe(tmp_path):
    """没情绪词、没心情时，别随手甩一张带字的梗图，先挑卖萌/开心。"""
    settings_ = settings(tmp_path)
    stickers.add_file(settings_, make_png(tmp_path / "rude.png", b"rude"), "日常")
    stickers.add_file(settings_, make_png(tmp_path / "cute.png", b"cute"), "卖萌")

    path = stickers.pick_for_reply(settings_, "在吗", {}, Rng())

    assert path.parent.name == "卖萌"
