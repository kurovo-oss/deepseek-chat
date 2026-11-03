from qqbot.onebot import HttpSender, extract_text, parse_event


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


def test_image_only_message_is_parsed():
    payload = _event(
        message=[{"type": "image", "data": {"file": "abc.jpg", "sub_type": 1}}]
    )
    incoming = parse_event(payload)
    assert incoming is not None
    assert incoming.text == ""
    assert len(incoming.images) == 1
    assert incoming.images[0].file == "abc.jpg"
    assert incoming.images[0].sub_type == 1


def test_text_with_image_keeps_both():
    payload = _event(
        message=[
            {"type": "text", "data": {"text": "看我这张"}},
            {"type": "image", "data": {"url": "http://x/1.png", "summary": "[表情]"}},
        ]
    )
    incoming = parse_event(payload)
    assert incoming.text == "看我这张"
    assert incoming.images[0].url == "http://x/1.png"
    assert incoming.images[0].summary == "[表情]"


def test_non_image_segments_ignored():
    payload = _event(
        message=[
            {"type": "face", "data": {"id": 1}},
            {"type": "text", "data": {"text": "嗯"}},
        ]
    )
    incoming = parse_event(payload)
    assert incoming.images == ()
    assert incoming.text == "嗯"


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


# ---------------------------------------------------------------- 好友列表探测
def test_friend_list_returns_items():
    sender = HttpSender("http://127.0.0.1:1")
    sender._request = lambda action, payload: (True, {"data": [{"user_id": 1}]})
    assert sender.friend_list() == [{"user_id": 1}]


def test_friend_list_none_when_request_fails():
    sender = HttpSender("http://127.0.0.1:1")
    sender._request = lambda action, payload: (False, {})
    assert sender.friend_list() is None


def test_friend_list_none_when_data_is_not_a_list():
    """QQ 内核断网时 NapCat 可能回 data: null，这也算探测不到。"""
    sender = HttpSender("http://127.0.0.1:1")
    sender._request = lambda action, payload: (True, {"data": None})
    assert sender.friend_list() is None


def test_parse_reply_segment():
    payload = _event(
        message=[
            {"type": "reply", "data": {"id": "777"}},
            {"type": "text", "data": {"text": "这个呢"}},
        ]
    )

    incoming = parse_event(payload)

    assert incoming is not None
    assert incoming.reply_id == "777"
    assert incoming.text == "这个呢"


def test_quote_only_message_is_parsed():
    payload = _event(message=[{"type": "reply", "data": {"id": "777"}}])

    incoming = parse_event(payload)

    assert incoming is not None
    assert incoming.reply_id == "777"
    assert incoming.text == ""


def test_get_message_detail_returns_images(monkeypatch):
    sender = HttpSender("http://127.0.0.1:3001")
    payload = {
        "data": {
            "message": [
                {"type": "text", "data": {"text": "看这个"}},
                {"type": "image", "data": {"file": "a.jpg", "url": "http://x/a.jpg"}},
            ]
        }
    }
    monkeypatch.setattr(sender, "_request", lambda action, body: (True, payload))

    detail = sender.get_message_detail("1")

    assert detail["text"] == "看这个"
    assert len(detail["images"]) == 1
    assert detail["images"][0].url.endswith("a.jpg")


def test_get_message_returns_text_only(monkeypatch):
    sender = HttpSender("http://127.0.0.1:3001")
    payload = {"data": {"message": [{"type": "text", "data": {"text": "嗯"}}]}}
    monkeypatch.setattr(sender, "_request", lambda action, body: (True, payload))

    assert sender.get_message("1") == "嗯"


def test_send_private_record_uses_base64():
    import base64

    from qqbot.onebot import HttpSender

    client = HttpSender("http://127.0.0.1:1")
    captured = {}
    client._call = lambda action, payload: captured.update(
        action=action, payload=payload
    ) or True

    assert client.send_private_record(100, b"FAKEMP3")
    assert captured["action"] == "send_private_msg"
    assert "record" in captured["payload"]["message"]
    assert base64.b64encode(b"FAKEMP3").decode() in captured["payload"]["message"]
