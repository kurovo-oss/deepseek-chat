"""她不能编"你说过…"：编一次，他以后就不信她记的任何事。"""

from qqbot import recall_guard

from tests.test_engine import FakeLLM, build_env, make_message


def test_unbacked_claim_is_softened():
    out = recall_guard.soften("你之前说过要出差呀\n我记着呢", "")

    assert "出差" not in out
    assert out.split("\n")[0] in recall_guard.SOFT_LINES


def test_claim_with_evidence_is_kept():
    text = "你之前说过要出差呀"
    evidence = "他说下周要出差三天，让我别忘"

    assert recall_guard.soften(text, evidence) == text


def test_plain_reply_untouched():
    text = "刚把衣服叠完\n腰有点酸"

    assert recall_guard.soften(text, "") == text


def test_marker_without_a_claim_untouched():
    """"你还记得吗"只是在问，没说出具体的事，不算编。"""
    assert recall_guard.soften("你还记得吗", "") == "你还记得吗"


def test_only_the_fabricated_line_is_replaced():
    out = recall_guard.soften("在拼拼图呢\n你之前说过要出差呀", "")

    assert out.startswith("在拼拼图呢")
    assert "出差" not in out


def test_engine_softens_fabricated_recall(tmp_path):
    env = build_env(tmp_path)
    env.engine.llm = FakeLLM("你之前提过要出差呀\n我记着呢")
    env.engine.handle_message(make_message("你怎么知道我要出门"))

    text = env.sender.sent[0][1]
    assert "出差" not in text


def test_engine_keeps_recall_that_is_in_context(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    env.engine.llm = FakeLLM("你之前说过要出差呀")
    real = env.engine._build_messages

    def with_evidence(session):
        messages = real(session)
        return messages + [{"role": "system", "content": "他下周要出差三天"}]

    monkeypatch.setattr(env.engine, "_build_messages", with_evidence)
    env.engine.handle_message(make_message("你还记得吗"))

    assert "出差" in env.sender.sent[0][1]
