"""模型客户端：重试、错误信息、密钥不泄漏、失败要留日志。"""

import logging

import pytest

from qqbot.llm import ChatResult, LLMClient, LLMError


class Recorder(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self):
        return [record.getMessage() for record in self.records]


@pytest.fixture
def logs():
    logger = logging.getLogger("qqbot")
    handler = Recorder()
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)


def make_client(**kwargs):
    kwargs.setdefault("sleep", lambda _: None)
    return LLMClient("http://example.invalid/v1", "sk-secret", "deepseek-flash", **kwargs)


def test_repr_hides_api_key():
    client = make_client()
    assert "sk-secret" not in repr(client)
    assert "deepseek-flash" in repr(client)


def test_successful_chat():
    client = make_client()

    def fake_post(payload):
        assert payload["model"] == "deepseek-flash"
        assert payload["stream"] is False
        return {
            "model": "deepseek-flash",
            "choices": [{"message": {"content": "  好呀  "}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }

    client._post = fake_post
    result = client.chat([{"role": "user", "content": "你好"}])
    assert result == ChatResult("好呀", 12, 3, "deepseek-flash")


def test_default_retries_are_three_attempts():
    client = make_client()  # 默认 retries=2 → 最多 3 次
    attempts = []

    def boom(payload):
        attempts.append(payload)
        raise LLMError("网络错误: 连不上")

    client._post = boom
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])
    assert len(attempts) == 3


def test_no_retry_when_disabled():
    client = make_client(retries=0)
    attempts = []
    client._post = lambda payload: attempts.append(payload) or (_ for _ in ()).throw(LLMError("挂了"))
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])
    assert len(attempts) == 1


def test_every_failure_is_logged(logs):
    client = make_client(retries=1)
    client._post = lambda payload: (_ for _ in ()).throw(LLMError("网络错误: 悄悄挂了"))
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])

    messages = logs.messages()
    assert sum("模型调用失败" in message for message in messages) == 2
    assert any("悄悄挂了" in message for message in messages)


def test_empty_choices_is_an_error(logs):
    client = make_client(retries=0)
    client._post = lambda payload: {"choices": []}
    with pytest.raises(LLMError, match="choices"):
        client.chat([{"role": "user", "content": "hi"}])
    assert any("模型调用失败" in message for message in logs.messages())


def test_probe_ok():
    client = make_client()
    client._post = lambda payload: {
        "model": "deepseek-flash",
        "choices": [{"message": {"content": "h"}}],
    }
    ok, detail = client.probe()
    assert ok is True
    assert detail == "deepseek-flash"


def test_probe_reports_reason(logs):
    client = make_client(retries=0)
    client._post = lambda payload: (_ for _ in ()).throw(LLMError("网络错误: 断网了"))
    ok, detail = client.probe()
    assert ok is False
    assert "断网了" in detail
    assert any("模型调用失败" in message for message in logs.messages())
