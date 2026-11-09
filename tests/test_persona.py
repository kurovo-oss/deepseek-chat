"""人格场景 overlay 与关系阶段。"""

from qqbot import persona


def test_pick_overlay_for_comfort():
    assert persona.pick_overlay("在难受") == "comfort"
    assert persona.pick_overlay("在逗你") == "tease"
    assert persona.pick_overlay("在问事") == "answer"


def test_tired_mood_overrides_scene():
    assert persona.pick_overlay("在逗你", {"tired": 0.9}) == "tired"


def test_missing_overlay_file_is_empty(tmp_path):
    assert persona.overlay_text("comfort", tmp_path) == ""
    assert persona.block("在难受", {}, tmp_path) == ""


def test_overlay_is_loaded_from_prompts(tmp_path):
    path = tmp_path / "prompts" / "persona" / "overlays" / "comfort.md"
    path.parent.mkdir(parents=True)
    path.write_text("先接住，别问细节。", encoding="utf-8")

    text = persona.block("在难受", {}, tmp_path)

    assert "先接住" in text
    assert "这轮的样子" in text


def test_relationship_stage_uses_days_and_messages(tmp_path):
    from qqbot.store import Store

    store = Store(tmp_path / "p.db")
    store.save_message("1", "user", "早", created_at=100)
    for index in range(25):
        store.save_message("1", "user", f"第 {index} 条", created_at=100 + index)

    assert persona.stage(store, "1", now=100) == "初识"
    # 3 天后、25 条消息：熟
    assert persona.stage(store, "1", now=100 + 3 * 86400) == "熟"
    store.close()


def test_relationship_block_is_short(tmp_path):
    from qqbot.store import Store

    store = Store(tmp_path / "p2.db")
    store.save_message("1", "user", "早", created_at=100)

    text = persona.relation_block(store, "1", now=100)

    assert "刚认识" in text
    assert len(text) < 80
    store.close()
