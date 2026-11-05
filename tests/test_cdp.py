"""最小 WebSocket 帧：编解码要能对上自己。"""

from __future__ import annotations

import io

from qqbot import cdp


def reader(data: bytes):
    stream = io.BytesIO(data)
    return lambda size: stream.read(size)


def test_small_text_frame_roundtrip():
    frame = cdp.encode_frame("你好".encode("utf-8"))

    opcode, payload = cdp.decode_frame(reader(frame))

    assert opcode == cdp.OPCODE_TEXT
    assert payload.decode("utf-8") == "你好"


def test_16bit_length_frame_roundtrip():
    payload = "x".encode("utf-8") * 300

    opcode, decoded = cdp.decode_frame(reader(cdp.encode_frame(payload)))

    assert opcode == cdp.OPCODE_TEXT
    assert decoded == payload


def test_64bit_length_frame_roundtrip():
    payload = b"y" * 70000

    _, decoded = cdp.decode_frame(reader(cdp.encode_frame(payload)))

    assert decoded == payload


def test_unmasked_frame_roundtrip():
    frame = cdp.encode_frame(b"abc", mask=False)

    _, payload = cdp.decode_frame(reader(frame))

    assert payload == b"abc"


def test_pong_frame_opcode():
    frame = cdp.encode_frame(b"p", opcode=cdp.OPCODE_PONG)

    opcode, payload = cdp.decode_frame(reader(frame))

    assert opcode == cdp.OPCODE_PONG
    assert payload == b"p"
