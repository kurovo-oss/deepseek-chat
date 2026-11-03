"""刷网：只读标题、按她口味挑、真看见的才记下来。"""

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from qqbot import browse


class Rng:
    def __init__(self, index=0):
        self.index = index

    def choice(self, seq):
        return seq[self.index % len(seq)]


def settings(tmp_path, web=True):
    return SimpleNamespace(web_enabled=web, browse_dir=tmp_path / "browse")


BAIDU = {
    "data": {
        "cards": [
            {
                "content": [
                    {
                        "content": [
                            {"word": "男子被拐30余年认亲后拉黑养家断联"},
                            {"word": "小区里一只橘猫天天蹲在楼下等喂"},
                            {"word": "今天降温十度记得穿外套"},
                        ]
                    }
                ]
            }
        ]
    }
}

BILI = {
    "data": {
        "list": [
            {
                "title": "哟西！故乡的樱花开了！",
                "owner": {"name": "大漠叔叔"},
            },
            {
                "title": "【全网最全】DeepSeek Harness超详细教程！50分钟从零上手",
                "owner": {"name": "Git源宝"},
            },
        ]
    }
}


def test_parse_baidu_reads_three_word_rows():
    items = browse.parse_baidu(BAIDU)

    assert [item.title for item in items][0] == "男子被拐30余年认亲后拉黑养家断联"
    assert items[1].source == "baidu"


def test_parse_bilibili_keeps_owner_and_title():
    items = browse.parse_bilibili(BILI)

    assert items[0].title == "哟西！故乡的樱花开了！"
    assert items[0].extra == "大漠叔叔"


def test_heavy_news_is_never_picked():
    items = browse.parse_baidu(BAIDU)

    chose = browse.pick(items, Rng())

    assert "被拐" not in chose.title


def test_she_picks_what_she_likes():
    items = [
        browse.Item("baidu", "某地发布2026年经济发展规划纲要"),
        browse.Item("baidu", "小区里一只橘猫天天蹲在楼下等喂"),
    ]

    assert "橘猫" in browse.pick(items, Rng()).title


def test_too_long_titles_are_skipped():
    items = [browse.Item("bilibili", "这" * 60)]

    assert browse.pick(items, Rng()) is None


def test_collect_saves_what_she_saw(tmp_path, monkeypatch):
    items = [browse.Item("baidu", "小区里一只橘猫天天蹲在楼下等喂")]
    monkeypatch.setattr(browse, "fetch", lambda settings, timeout=8.0: items)

    item = browse.collect(settings(tmp_path), Rng())

    assert item.title.startswith("小区里")
    saved = browse.load(settings(tmp_path))
    assert [row.title for row in saved] == [item.title]


def test_collect_does_not_duplicate_the_same_title(tmp_path, monkeypatch):
    items = [browse.Item("baidu", "小区里一只橘猫天天蹲在楼下等喂")]
    monkeypatch.setattr(browse, "fetch", lambda settings, timeout=8.0: items)
    browse.collect(settings(tmp_path), Rng())

    browse.collect(settings(tmp_path), Rng())

    assert len(browse.load(settings(tmp_path))) == 1


def test_no_web_means_no_browsing(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(browse, "fetch", lambda *a, **k: called.append(1) or [])

    assert browse.collect(settings(tmp_path, web=False), Rng()) is None
    assert called == []


def test_block_is_empty_when_she_has_not_looked(tmp_path):
    assert browse.block(settings(tmp_path)) == ""


def test_block_shows_what_she_saw(tmp_path):
    (tmp_path / "browse").mkdir()
    browse.save(settings(tmp_path), [browse.Item("baidu", "小区里一只橘猫天天蹲在楼下等喂")])

    text = browse.block(settings(tmp_path))

    assert "橘猫" in text
    assert "热搜" in text
    assert "没点进去" in text
    assert "一天说一次" in text


def test_block_is_empty_when_web_is_off(tmp_path):
    (tmp_path / "browse").mkdir()
    browse.save(settings(tmp_path, web=True), [browse.Item("baidu", "橘猫")])

    assert browse.block(settings(tmp_path, web=False)) == ""


def test_prune_drops_old_days(tmp_path):
    folder = tmp_path / "browse"
    folder.mkdir()
    old = folder / "2026-01-01.json"
    old.write_text(json.dumps({"items": []}), encoding="utf-8")
    import os
    import time

    past = time.time() - 30 * 86400
    os.utime(old, (past, past))
    today = date.today().isoformat()
    browse.save(settings(tmp_path), [browse.Item("baidu", "橘猫")], day=today)

    assert browse.prune(settings(tmp_path), keep_days=14) == 1
    assert not old.exists()


def test_today_item_returns_the_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        browse, "fetch", lambda settings, timeout=8.0: [browse.Item("baidu", "橘猫天天蹲在楼下等喂")]
    )
    browse.collect(settings(tmp_path), Rng())

    assert browse.today_item(settings(tmp_path)).title == "橘猫天天蹲在楼下等喂"
