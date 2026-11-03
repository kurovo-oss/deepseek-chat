"""发语音：情绪重的时候补一条。

单独一层的原因跟 media 一样：engine 是编排层，合成语音、开线程、落库这些细节
塞进去就成杂物间了；而且这块失败率最高（没网、限流、协议端不收），必须能整体跳过。
"""

from __future__ import annotations

import logging
import threading

from . import voice as voice_mod

log = logging.getLogger("qqbot")


class SpeakingMixin:
    def _maybe_speak(self, user_id: int, session: str, part_texts: list[str]) -> None:
        """情绪重的时候补一条语音；合成慢，所以扔后台线程，不耽误下一条消息。"""
        values = self.mood.snapshot() if self.mood is not None else {}
        if not voice_mod.should_speak(part_texts, values, self._rng, self.settings):
            return
        text = voice_mod.clean_text(part_texts)
        if not text:
            return
        threading.Thread(
            target=self._speak, args=(user_id, session, text), daemon=True
        ).start()

    def _speak(self, user_id: int, session: str, text: str) -> None:
        path = voice_mod.synthesize(text, self.settings)
        if path is None:
            return
        send = getattr(self.sender, "send_private_record", None)
        if send is None or not send(user_id, path):
            log.warning("语音发送失败：%r", text[:20])
            return
        self.store.save_message(session, "assistant", f"[语音] {text}", origin="voice")
