"""导出：数据得能拿得出来。"""

import pytest

from qqbot import digest
from qqbot.config import Settings
from qqbot.export import export_all
from qqbot.store import Store


def make_settings(tmp_path):
    return Settings(
        allow_user_id=100,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
        digest_dir=tmp_path / "daily",
        export_dir=tmp_path / "exports",
    )


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    yield settings, store
    store.close()


def test_export_contains_what_matters(env):
    settings, store = env
    store.add_card("fact", "他不吃香菜", importance=0.8)
    store.add_card("preference", "他喜欢猫", confirmed=False)
    store.save_message("100", "user", "今天面完了")
    store.save_message("100", "assistant", "怎么样呀")
    digest.write(settings, digest.day_of(__import__("time").time()), "这一天", "他去面试了。")

    path, stats = export_all(store, settings)

    text = path.read_text(encoding="utf-8")
    assert path.exists() and path.parent == settings.export_dir
    assert "记忆卡片" in text
    assert "不吃香菜" in text
    assert "待确认" in text  # 未确认的卡要标出来
    assert "最近 200 条对话" in text
    assert "他去面试了。" in text
    assert stats["messages"] == 2
    assert stats["cards"] == 2
    assert stats["summaries"] == 1


def test_export_with_no_data_still_works(env):
    settings, store = env
    path, stats = export_all(store, settings)

    text = path.read_text(encoding="utf-8")
    assert "（还没有）" in text
    assert stats["messages"] == 0
