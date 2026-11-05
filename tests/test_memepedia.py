"""梗库：解析梗鲸详情页、断点入库、按台词/释义找梗。"""

from __future__ import annotations

import time
from pathlib import Path

from qqbot import memepedia
from qqbot.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "memepedia_001.html"


def make_store(tmp_path):
    return Store(tmp_path / "m.db")


def test_parse_detail_extracts_quote_emotion_meaning():
    data = memepedia.parse_detail(FIXTURE.read_text(encoding="utf-8"))

    assert data["title"].startswith("DeepSeek娘")
    assert data["quote"] == "真当我是便宜货啊"
    assert data["emotion"] == "气鼓鼓"
    assert data["role"] == "DeepSeek娘"
    assert data["kind"] == "梗图"
    assert "AI 界拼多多" in data["meaning"]
    assert "2025 年初" in data["origin"]
    assert data["author"] == "浪生微澜"


def test_ingest_is_resumable_and_skips_existing(tmp_path):
    store = make_store(tmp_path)
    html = FIXTURE.read_text(encoding="utf-8")
    calls = []

    def fetch(number):
        calls.append(number)
        return html

    first = memepedia.ingest(store, start=1, end=3, delay=0, fetch=fetch)
    second = memepedia.ingest(store, start=1, end=3, delay=0, fetch=fetch)

    assert first == 3
    assert second == 0
    assert sorted(calls) == [1, 2, 3]
    assert memepedia.count(store) == 3


def test_ingest_continues_after_a_failure(tmp_path):
    store = make_store(tmp_path)
    html = FIXTURE.read_text(encoding="utf-8")

    def fetch(number):
        if number == 2:
            raise OSError("boom")
        return html

    written = memepedia.ingest(store, start=1, end=3, delay=0, fetch=fetch)

    assert written == 2
    assert memepedia.count(store) == 2


def test_save_and_get(tmp_path):
    store = make_store(tmp_path)
    memepedia.save(store, 7, {"title": "t", "quote": "q", "meaning": "m"}, now=100)

    row = memepedia.get(store, 7)

    assert row["number"] == 7
    assert row["quote"] == "q"
    assert row["page_url"].endswith("/zh/meme/7")


def test_search_prefers_quote_match(tmp_path):
    store = make_store(tmp_path)
    memepedia.save(store, 1, {"title": "便宜货", "quote": "真当我是便宜货啊", "meaning": "吐槽只谈性价比", "emotion": "气鼓鼓"})
    memepedia.save(store, 2, {"title": "晚安", "quote": "该睡了", "meaning": "晚安表情", "emotion": "早晚安"})

    rows = memepedia.search(store, "便宜货")

    assert rows and rows[0]["number"] == 1
    block = memepedia.block(store, "便宜货")
    assert "真当我是便宜货啊" in block
    assert "#1" in block


def test_block_returns_empty_when_nothing_matches(tmp_path):
    store = make_store(tmp_path)
    memepedia.save(store, 1, {"title": "晚安", "quote": "该睡了", "meaning": "晚安表情"})

    assert memepedia.block(store, "量子力学") == ""

def test_ingest_index_reads_story_fields(tmp_path):
    store = make_store(tmp_path)
    data = {
        "gallery": {
            "DeepSeek娘": {
                "images": [
                    {
                        "alt": "DeepSeek娘『真当我是便宜货啊』气鼓鼓表情包",
                        "preview": "https://img.aigengtu.com/0_preview/meme/001.webp",
                        "type": "meme",
                        "story": {
                            "zh": "吐槽只谈性价比",
                            "origin": "2025 年初的定价梗",
                            "contributor": "浪生微澜",
                        },
                    }
                ]
            }
        }
    }

    written = memepedia.ingest_index(store, data=data, now=100)
    row = memepedia.get(store, 1)

    assert written == 1
    assert row["quote"] == "真当我是便宜货啊"
    assert row["emotion"] == "气鼓鼓"
    assert row["role"] == "DeepSeek娘"
    assert row["meaning"] == "吐槽只谈性价比"
    assert row["origin"] == "2025 年初的定价梗"
    assert row["author"] == "浪生微澜"


def test_ingest_index_is_idempotent(tmp_path):
    store = make_store(tmp_path)
    data = {
        "gallery": {
            "鲸鱼娘": {
                "images": [
                    {"alt": "鲸鱼娘『晚安』晚安表情包", "preview": "https://img.aigengtu.com/meme/42.webp"}
                ]
            }
        }
    }

    assert memepedia.ingest_index(store, data=data) == 1
    assert memepedia.ingest_index(store, data=data) == 1
    assert memepedia.count(store) == 1

