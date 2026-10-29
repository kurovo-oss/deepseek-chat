import json

import pytest

from qqbot.images import (
    TAG_VOCABULARY,
    ImageLibrary,
    import_seed_library,
    save_image_file,
    sniff_image,
)
from qqbot.store import Store

PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff\xe0"
GIF = b"GIF89a"
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "d.db")
    yield db
    db.close()


def make_image(db, root, tags, source="builtin", seed=b"a", data=None):
    payload = data if data is not None else PNG + seed
    sha, mime, rel = save_image_file(root, payload)
    db.add_image(sha, rel, mime, len(payload), tags=tags, source=source)
    return db.find_image_by_sha(sha)


# ---------------------------------------------------------------- 嗅探
@pytest.mark.parametrize(
    "data, expected_ext",
    [(PNG + b"x", ".png"), (JPEG + b"x", ".jpg"), (GIF, ".gif"), (WEBP, ".webp")],
)
def test_sniff_image(data, expected_ext):
    sniffed = sniff_image(data)
    assert sniffed is not None
    assert sniffed[1] == expected_ext


def test_sniff_rejects_text():
    assert sniff_image(b"hello world") is None
    assert sniff_image(b"") is None


# ---------------------------------------------------------------- 落盘
def test_save_image_file_layout(tmp_path):
    sha, mime, rel = save_image_file(tmp_path, PNG + b"data")
    assert mime == "image/png"
    assert rel == f"{sha[:2]}/{sha}.png"
    assert (tmp_path / rel).is_file()


def test_save_image_file_is_idempotent(tmp_path):
    first = save_image_file(tmp_path, PNG + b"same")
    second = save_image_file(tmp_path, PNG + b"same")
    assert first == second


def test_save_rejects_unknown_type(tmp_path):
    assert save_image_file(tmp_path, b"not an image") is None


# ---------------------------------------------------------------- 内置导入
def seed_dir(tmp_path, items):
    src = tmp_path / "seed"
    src.mkdir()
    for item in items:
        (src / item["file"]).write_bytes(PNG + item["file"].encode())
    (src / "manifest.json").write_text(
        json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8"
    )
    return src


def test_import_seed_library(store, tmp_path):
    src = seed_dir(
        tmp_path,
        [
            {"file": "cat.png", "tags": ["cat", "cute"]},
            {"file": "dog.png", "tags": ["animal"]},
        ],
    )
    root = tmp_path / "images"
    assert import_seed_library(store, src, root) == 2
    assert store.count_images() == 2
    assert {row["source"] for row in store.list_images()} == {"builtin"}


def test_import_seed_library_is_idempotent(store, tmp_path):
    src = seed_dir(tmp_path, [{"file": "cat.png", "tags": ["cat"]}])
    root = tmp_path / "images"
    import_seed_library(store, src, root)
    assert import_seed_library(store, src, root) == 0
    assert store.count_images() == 1


def test_import_seed_library_without_manifest(store, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert import_seed_library(store, empty, tmp_path / "images") == 0


# ---------------------------------------------------------------- 解析
def test_resolve_by_tag(store, tmp_path):
    root = tmp_path / "images"
    make_image(store, root, ["cat"])
    lib = ImageLibrary(store, root)
    assert lib.resolve("cat") is not None
    assert lib.resolve("CAT") is not None  # 大小写不敏感
    assert lib.resolve("dog") is None


def test_resolve_prefers_user_images(store, tmp_path):
    root = tmp_path / "images"
    make_image(store, root, ["cat"], source="builtin", seed=b"b1")
    make_image(store, root, ["cat"], source="user", seed=b"u1")
    lib = ImageLibrary(store, root)
    picked = {lib.resolve("cat") for _ in range(20)}
    assert all(path is not None for path in picked)
    user_row = [r for r in store.list_images(tag="cat") if r["source"] == "user"][0]
    assert str(lib.path_of(user_row)) in {str(path) for path in picked}


def test_resolve_respects_max_bytes(store, tmp_path):
    root = tmp_path / "images"
    make_image(store, root, ["big"], seed=b"x" * 100)
    lib = ImageLibrary(store, root)
    assert lib.resolve("big", max_bytes=10) is None
    assert lib.resolve("big", max_bytes=1000) is not None


def test_resolve_records_use(store, tmp_path):
    root = tmp_path / "images"
    row = make_image(store, root, ["cat"])
    lib = ImageLibrary(store, root)
    lib.resolve("cat")
    assert store.get_image(row["id"])["use_count"] == 1


def test_rejects_path_traversal(store, tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    (tmp_path / "evil.png").write_bytes(PNG)
    store.add_image("deadbeef", "../evil.png", "image/png", 4, tags=["evil"], source="user")
    lib = ImageLibrary(store, root)
    assert lib.resolve("evil") is None


def test_rejects_unsupported_extension(store, tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    (root / "x.exe").write_bytes(b"MZ")
    store.add_image("cafe", "x.exe", "application/octet-stream", 2, tags=["bad"], source="user")
    lib = ImageLibrary(store, root)
    assert lib.resolve("bad") is None


def test_missing_file_on_disk_is_skipped(store, tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    store.add_image("beef", "aa/missing.png", "image/png", 10, tags=["ghost"], source="user")
    lib = ImageLibrary(store, root)
    assert lib.resolve("ghost") is None


def test_len_counts_rows(store, tmp_path):
    root = tmp_path / "images"
    make_image(store, root, ["cat", "animal", "cute"])
    lib = ImageLibrary(store, root)
    assert len(lib) == 1
    assert len(lib.tags) == 3


def test_tags_listed(store, tmp_path):
    root = tmp_path / "images"
    make_image(store, root, ["cat", "animal"], seed=b"1")
    make_image(store, root, ["dog"], seed=b"2")
    lib = ImageLibrary(store, root)
    assert lib.tags == ["animal", "cat", "dog"]


def test_vocabulary_matches_system_prompt():
    """打标词表必须和人格提示词里的「可用 tag」一致，否则收藏的图永远不会被点名。"""
    from pathlib import Path

    prompt = Path(__file__).resolve().parent.parent / "prompts" / "system.md"
    text = prompt.read_text(encoding="utf-8")
    import re

    match = re.search(r"可用 tag：(.+)", text)
    assert match
    listed = {tag.strip() for tag in match.group(1).split("、") if tag.strip()}
    assert listed == set(TAG_VOCABULARY)


# ---------------------------------------------------------------- 回收磁盘
def _engine_with_library(store, root, tmp_path):
    """只为了拿到 _clean_archived_images：LLM 和 sender 都用不到。"""
    from qqbot.config import Settings
    from qqbot.engine import Engine

    settings = Settings(
        allow_user_id=100,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
    )
    library = ImageLibrary(store, root)
    return Engine(settings, store, object(), object(), "你是大肥鱼", images=library)


def test_clean_removes_archived_files_and_rows(store, tmp_path):
    """淘汰只置 archived、文件留着，久了照样占满盘——所以要能真删。"""
    root = tmp_path / "lib"
    for seed in (b"1", b"2", b"3"):
        make_image(store, root, ["cat"], seed=seed)
    store.evict_images(max_count=1)

    engine = _engine_with_library(store, root, tmp_path)
    removed, freed = engine._clean_archived_images()

    assert removed == 2
    assert freed > 0
    assert store.list_images(status="archived") == []
    assert len(store.list_images(status="active")) == 1  # 在用的那张不能动
    assert len(list(root.rglob("*.*"))) == 1


def test_clean_says_there_is_nothing_to_do(store, tmp_path):
    root = tmp_path / "lib"
    make_image(store, root, ["cat"], seed=b"only")
    engine = _engine_with_library(store, root, tmp_path)

    assert engine._clean_archived_images() == (0, 0)
    assert "没有要回收" in engine._img_command("clean")
