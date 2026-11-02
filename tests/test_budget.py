"""上下文预算：人设固定，其余按优先级拿配额。"""

from qqbot import budget as budget_mod


def test_estimate_tokens_counts_cjk_and_ascii():
    assert budget_mod.estimate_tokens("") == 0
    assert 6 <= budget_mod.estimate_tokens("今天好累呀真的") <= 10
    assert budget_mod.estimate_tokens("a" * 35) < budget_mod.estimate_tokens("啊" * 35)


def test_fit_keeps_everything_when_budget_is_loose():
    sections = [
        budget_mod.Section("人设", "你是大肥鱼", priority=1000, fixed=True),
        budget_mod.Section("记忆", "他喜欢猫", priority=60, quota=800),
        budget_mod.Section("梗", "已老实=认输", priority=40, quota=360),
    ]

    result = budget_mod.fit(sections, 5000)

    assert "你是大肥鱼" in result.text
    assert "他喜欢猫" in result.text
    assert "已老实" in result.text
    assert result.trimmed == ()
    assert result.dropped == ()


def test_fit_drops_lowest_priority_first():
    sections = [
        budget_mod.Section("人设", "你是大肥鱼", priority=1000, fixed=True),
        budget_mod.Section("重要", "重" * 40, priority=90, quota=100),
        budget_mod.Section("次要", "次" * 400, priority=10, quota=500),
    ]

    result = budget_mod.fit(sections, 60)

    assert "重" * 10 in result.text
    assert "次" not in result.text
    assert "次要" in result.dropped


def test_fit_trims_section_to_its_quota():
    sections = [budget_mod.Section("梗", "梗" * 400, priority=40, quota=100)]

    result = budget_mod.fit(sections, 5000)

    assert result.trimmed == ("梗",)
    assert budget_mod.estimate_tokens(result.text) <= 110


def test_persona_survives_a_budget_smaller_than_itself():
    sections = [
        budget_mod.Section("人设", "人" * 300, priority=1000, fixed=True),
        budget_mod.Section("记忆", "记" * 50, priority=60, quota=800),
    ]

    result = budget_mod.fit(sections, 100)

    assert "人" * 300 in result.text
    assert "记忆" in result.dropped


def test_fit_is_deterministic_about_order():
    sections = [
        budget_mod.Section("人设", "人设", priority=1000, fixed=True),
        budget_mod.Section("A", "甲", priority=90),
        budget_mod.Section("B", "乙", priority=80),
    ]

    result = budget_mod.fit(sections, 5000)

    assert result.text.index("甲") < result.text.index("乙")


def test_trim_to_tokens_keeps_sentence_boundary():
    text = "第一句话。第二句话。第三句话。" * 3

    trimmed = budget_mod.trim_to_tokens(text, 30)

    assert budget_mod.estimate_tokens(trimmed) <= 31
    assert trimmed.endswith("…")


def test_history_budget_leaves_room_after_system():
    assert budget_mod.history_budget(9000, 4000) == 5000
    assert budget_mod.history_budget(1000, 4000) == 200  # 兜底，不能是负数
