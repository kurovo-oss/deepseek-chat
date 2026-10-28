from qqbot.onebot import extract_text, parse_event


def _event(message_type="private", message=None, user_id=100, message_id=1):
    return {
        "post_type": "message",
        "message_type": message_type,
        "user_id": user_id,
        "message_id": message_id,
        "message": message
        if message is not None
        else [{"type": "text", "data": {"text": "你好"}}],
    }


def test_parse_private_text_message():
    incoming = parse_event(_event())
    assert incoming is not None
    assert incoming.user_id == 100
    assert incoming.text == "你好"
    assert incoming.message_id == "1"


def test_group_message_ignored():
    assert parse_event(_event(message_type="group")) is None


def test_non_message_event_ignored():
    assert parse_event({"post_type": "notice"}) is None
    assert parse_event("not a dict") is None
    assert parse_event(None) is None


def test_image_only_message_ignored():
    payload = _event(message=[{"type": "image", "data": {"file": "abc.jpg"}}])
    assert parse_event(payload) is None


def test_blank_text_ignored():
    payload = _event(message=[{"type": "text", "data": {"text": "   "}}])
    assert parse_event(payload) is None


def test_invalid_user_id_ignored():
    payload = _event()
    payload["user_id"] = "abc"
    assert parse_event(payload) is None


def test_extract_text_joins_segments():
    segments = [
        {"type": "text", "data": {"text": "你好"}},
        {"type": "face", "data": {"id": "1"}},
        {"type": "text", "data": {"text": "呀"}},
    ]
    assert extract_text(segments) == "你好呀"


def test_extract_text_accepts_raw_string():
    assert extract_text("直接字符串") == "直接字符串"
    assert extract_text(None) == ""
