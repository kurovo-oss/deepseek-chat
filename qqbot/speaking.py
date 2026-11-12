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

    def _try_voice_reply(
        self,
        user_id: int,
        session: str,
        parts: list[str],
        origin: str = "chat",
        is_fallback: bool = False,
        has_visual: bool = False,
    ) -> bool:
        """决定这条回复是否只发语音；返回 True 表示已经处理完。"""
        if origin not in ("chat", "proactive") or is_fallback or has_visual:
            return False
        text = voice_mod.clean_text(parts)
        if not text:
            return False
        values = self.mood.snapshot() if self.mood is not None else {}
        if not voice_mod.should_speak(parts, values, self._rng, self.settings):
            return False
        record_id = self.store.save_message(
            session,
            "assistant",
            text,
            send_state="pending",
            origin="voice",
            parts=parts,
        )
        self._speak_reply(user_id, session, text, parts, record_id)
        return True

    def _speak_reply(
        self,
        user_id: int,
        session: str,
        text: str,
        parts: list[str],
        record_id: int | None,
    ) -> None:
        """语音优先：成功只发语音；合成/发送失败才补文字。"""
        try:
            path = voice_mod.synthesize(text, self.settings)
            send = getattr(self.sender, "send_private_record", None)
            if path is not None and send is not None and send(user_id, path):
                if record_id is not None:
                    self.store.update_send_state(record_id, "sent")
                return
        except Exception:  # noqa: BLE001 - 语音是加分项，失败改文字
            log.warning("语音发送异常，改用文字", exc_info=True)

        log.info("语音不可用，改用文字：%r", text[:20])
        all_ok = True
        sent_parts = 0
        base_delay = self.settings.segment_delay_ms / 1000
        with self._send_lock:
            for index, part in enumerate(parts):
                if index:
                    self._sleep(base_delay * 0.6)
                if not self.sender.send_private(user_id, part):
                    all_ok = False
                    break
                sent_parts = index + 1
                if record_id is not None:
                    self.store.update_sent_parts(record_id, sent_parts)
        if record_id is not None:
            self.store.update_send_state(record_id, "sent" if all_ok else "failed")
