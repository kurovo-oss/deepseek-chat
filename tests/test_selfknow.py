"""她自己的事：模型、用量、跑了多久——问起来不许编。"""

import time

from qqbot import selfknow
from qqbot.config import Settings
from qqbot.store import Store


def settings(tmp_path):
    return Settings(
        allow_user_id=1,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "s.db",
        system_prompt_file=tmp_path / "s.md",
        model_id="deepseek-flash",
        sticker_dir=tmp_path / "stickers",
        voice_dir=tmp_path / "voice",
    )


def test_facts_read_real_numbers(tmp_path):
    store = Store(tmp_path / "f.db")
    store.save_message("100", "user", "在吗")
    store.save_message("100", "assistant", "在呀")
    store.log_usage("deepseek-flash", 1200, 300, "chat", True)
    store.add_card(kind="fact", summary="他在做 QQ 机器人", importance=0.7)

    data = selfknow.facts(store, settings(tmp_path), now=int(time.time()))

    assert data["today_messages"] == 2
    assert data["today_in"] == 1200
    assert data["today_out"] == 300
    assert data["cards"] == 1
    assert data["model"] == "deepseek-flash"
    assert data["weekday"].startswith("周")


def test_block_talks_about_herself(tmp_path):
    store = Store(tmp_path / "b.db")
    store.log_usage("deepseek-flash", 23000, 7000, "chat", True)

    text = selfknow.block(store, settings(tmp_path), now=int(time.time()))

    assert "关于你自己" in text
    assert "deepseek-flash" in text
    assert "token" in text
    assert "万" in text
    assert "照实说" in text
    assert "花了多少钱" in text


def test_block_is_cached(tmp_path, monkeypatch):
    store = Store(tmp_path / "c.db")
    calls = []
    real = selfknow.facts

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(selfknow, "facts", counting)
    selfknow._cache.clear()
    now = int(time.time())

    selfknow.block(store, settings(tmp_path), now=now)
    selfknow.block(store, settings(tmp_path), now=now)

    assert len(calls) == 1


def test_block_without_store_is_empty():
    assert selfknow.block(None, None) == ""


def test_number_formatting():
    assert selfknow._num(999) == "999"
    assert selfknow._num(12345) == "1.2 万"
    assert selfknow._num(1234567) == "123 万"
