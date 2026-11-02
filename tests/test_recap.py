"""前情摘要：窗口外的旧话压成几句，别让"我们聊过什么"凭空消失。"""

import time

from qqbot import recap


def rows(count, start_id=1):
    made = []
    for index in range(count):
        made.append(
            {
                "id": start_id + index,
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"第{index}句",
            }
        )
    return made


def test_pending_is_everything_outside_the_window():
    todo = recap.pending(rows(100), upto_id=0, keep=40)

    assert len(todo) == 60
    assert todo[0]["id"] == 1
    assert todo[-1]["id"] == 60


def test_pending_skips_what_is_already_summarized():
    todo = recap.pending(rows(100), upto_id=50, keep=40)

    assert [row["id"] for row in todo] == list(range(51, 61))


def test_pending_is_empty_when_history_is_short():
    assert recap.pending(rows(10), upto_id=0, keep=40) == []


def test_transcript_marks_who_is_speaking():
    text = recap.transcript(rows(4))

    assert text.startswith("他：第0句")
    assert "你：第1句" in text
    assert "\n" in text


def test_transcript_keeps_the_latest_when_too_long():
    text = recap.transcript(rows(200), limit=50)

    assert len(text) <= 51
    assert text.startswith("…")


def test_trim_shortens_to_limit():
    text = recap.trim("啊" * 300)

    assert len(text) == recap.MAX_CHARS
    assert text.endswith("…")


def test_note_is_empty_without_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(recap, "_path", lambda settings: tmp_path / "recap.json")

    assert recap.note(type("S", (), {"base_dir": tmp_path})(), "100") == ""


def test_note_goes_stale_after_three_days(tmp_path, monkeypatch):
    monkeypatch.setattr(recap, "_path", lambda settings: tmp_path / "recap.json")
    settings = type("S", (), {"base_dir": tmp_path})()
    recap.put(settings, "100", recap.Recap(upto_id=5, text="他在加班", at=time.time() - 4 * 86400))

    assert recap.note(settings, "100") == ""


def test_note_reads_back_what_was_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(recap, "_path", lambda settings: tmp_path / "recap.json")
    settings = type("S", (), {"base_dir": tmp_path})()
    recap.put(settings, "100", recap.Recap(upto_id=5, text="约好周六去那家店", at=time.time()))

    text = recap.note(settings, "100")

    assert "前面聊过的" in text
    assert "周六" in text
