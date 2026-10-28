"""DeepSeek（OpenAI 兼容）客户端。

只用标准库 urllib：这个项目跑在一台没有 pip 网络的环境里，
少一个依赖就少一个装不上的理由。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(RuntimeError):
    """模型调用失败（网络、鉴权、协议、解析）。"""


@dataclass(frozen=True)
class ChatResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        retries: int = 1,
        sleep=time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self.timeout = timeout
        self.retries = max(0, retries)
        self._sleep = sleep

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def __repr__(self) -> str:  # 防密钥泄漏到日志
        return f"LLMClient(model={self.model!r}, base_url={self.base_url!r})"

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        max_tokens: int = 800,
    ) -> ChatResult:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        last_error: LLMError | None = None
        for attempt in range(self.retries + 1):
            try:
                data = self._post(payload)
                choices = data.get("choices") or []
                if not choices:
                    raise LLMError("响应中没有 choices")
                content = (choices[0].get("message") or {}).get("content") or ""
                usage = data.get("usage") or {}
                return ChatResult(
                    text=str(content).strip(),
                    tokens_in=int(usage.get("prompt_tokens") or 0),
                    tokens_out=int(usage.get("completion_tokens") or 0),
                    model=str(data.get("model") or self.model),
                )
            except LLMError as exc:
                last_error = exc
                if attempt < self.retries:
                    self._sleep(0.5 * (2**attempt))
        raise last_error or LLMError("未知错误")

    def probe(self) -> tuple[bool, str]:
        """启动自检：确认模型 ID 与 key 可用（对应 MVP 风险 R1）。"""
        try:
            result = self.chat(
                [{"role": "user", "content": "hi"}], temperature=0.0, max_tokens=1
            )
            return True, result.model
        except LLMError as exc:
            return False, str(exc)

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"网络错误: {exc}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"响应不是合法 JSON: {raw[:120]!r}") from exc
