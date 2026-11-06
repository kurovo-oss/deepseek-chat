"""QQ 空间：写草稿、配图、过闸门、真发（用假协议端）。"""

from __future__ import annotations

import time
from pathlib import Path

from qqbot import guard, qzone
from qqbot.config import Settings
from qqbot.llm import ChatResult
from qqbot.store import Store


class FakeLLM:
    def __init__(self, text="说说：\n\n今天他给我看了他写的代码，\n我其实没太看懂。\n"):
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return ChatResult(self.text, 10, 10, "fake")


class FakeSender:
    def __init__(self, ok=True, data=None):
        self.ok = ok
        self.data = data if data is not None else {"tid": "T123", "time": 1}
        self.calls = []

    def request(self, action, payload):
        self.calls.append((action, payload))
        return self.ok, self.data


def settings(tmp_path, **overrides):
    base = dict(
        allow_user_id=7,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "q.db",
        system_prompt_file=tmp_path / "s.md",
        image_dir=tmp_path / "images",
        qzone_enabled=True,
        qzone_dry_run=True,
        qzone_daily_max=3,
        qzone_image_enabled=True,
    )
    base.update(overrides)
    return Settings(**base)


def env(tmp_path, **overrides):
    cfg = settings(tmp_path, **overrides)
    store = Store(cfg.db_path)
    return cfg, store, guard.Guard(store, cfg)


def test_draft_strips_prefix_and_blank_lines(tmp_path):
    cfg, store, _ = env(tmp_path)

    matter = qzone.draft(store, FakeLLM(), cfg)

    assert matter["text"].startswith("今天他给我看了他写的代码")
    assert "\n\n" not in matter["text"]
    assert "说说：" not in matter["text"]


def test_dry_run_does_not_touch_the_api(tmp_path):
    cfg, store, g = env(tmp_path)
    sender = FakeSender()

    result = qzone.publish(store, g, sender, "今天真好", None, reason="测试")

    assert result["status"] == "dry_run"
    assert sender.calls == []
    assert g.recent(1)[0]["result"] == "dry_run"
    assert qzone.last_posts(store)[0]["status"] == "dry_run"


def test_publish_sends_content_and_stores_tid(tmp_path):
    cfg, store, g = env(tmp_path, qzone_dry_run=False)
    sender = FakeSender()

    result = qzone.publish(store, g, sender, "今天真好", None, reason="测试")

    assert result["status"] == "ok"
    action, payload = sender.calls[0]
    assert action == "send_qzone_msg"
    assert payload["content"] == "今天真好"
    assert payload["ugc_right"] == 1
    assert qzone.last_posts(store)[0]["tid"] == "T123"


def test_publish_attaches_one_image(tmp_path):
    cfg, store, g = env(tmp_path, qzone_dry_run=False)
    image = Path(cfg.image_dir) / "a.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"png")
    sender = FakeSender()

    qzone.publish(store, g, sender, "配张图", image, reason="测试")

    payload = sender.calls[0][1]
    assert len(payload["images"]) == 1
    assert payload["images"][0].startswith("file://")


def test_failed_publish_is_audited(tmp_path):
    cfg, store, g = env(tmp_path, qzone_dry_run=False)
    sender = FakeSender(ok=False, data={"message": "no permission"})

    result = qzone.publish(store, g, sender, "发不出去", None, reason="测试")

    assert result["status"] == "failed"
    assert g.recent(1)[0]["result"] == "failed"
    assert qzone.last_posts(store)[0]["status"] == "failed"


def test_daily_max_stops_more_posts(tmp_path):
    cfg, store, g = env(tmp_path, qzone_dry_run=False, qzone_daily_max=1)
    sender = FakeSender()
    now = int(time.time())

    first = qzone.publish(store, g, sender, "第一条", None, now=now, reason="测试")
    second = qzone.publish(store, g, sender, "第二条", None, now=now + 3600, reason="测试")

    assert first["status"] == "ok"
    assert second["status"] == "denied"
    assert second["detail"] == "quota"


def test_pick_image_prefers_today_only(tmp_path):
    cfg, store, _ = env(tmp_path)
    image = Path(cfg.image_dir) / "today.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"png")
    now = int(time.time())
    with store._lock:  # noqa: SLF001
        store._conn.execute(  # noqa: SLF001
            "INSERT INTO images (sha256, rel_path, mime, byte_size, tags, source, status, use_count, created_at, manual)"
            " VALUES ('s1','today.png','image/png',3,'','test','ok',0,?,'')",
            (now,),
        )
        store._conn.execute(  # noqa: SLF001
            "INSERT INTO images (sha256, rel_path, mime, byte_size, tags, source, status, use_count, created_at, manual)"
            " VALUES ('s2','old.png','image/png',3,'','test','ok',0,?,'')",
            (now - 3 * 86400,),
        )
        store._conn.commit()  # noqa: SLF001

    picked = qzone.pick_image(store, cfg, now)

    assert picked is not None
    assert picked.name == "today.png"


def test_maybe_post_respects_window_and_switch(tmp_path):
    cfg, store, g = env(tmp_path, qzone_dry_run=False)
    sender = FakeSender()
    noon = int(time.mktime((2026, 11, 5, 12, 0, 0, 0, 0, -1)))
    evening = int(time.mktime((2026, 11, 5, 21, 0, 0, 0, 0, -1)))

    assert qzone.maybe_post(store, FakeLLM(), cfg, g, sender, now=noon) is None
    result = qzone.maybe_post(store, FakeLLM(), cfg, g, sender, now=evening)

    assert result is not None
    assert result["status"] == "ok"

    assert qzone.maybe_post(store, FakeLLM(), cfg, g, sender, now=evening) is None

def test_pick_image_accepts_active_status(tmp_path):
    settings_ = settings(tmp_path)
    store = Store(settings_.db_path)
    image = settings_.image_dir / "x" / "y.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    now = 1793868800
    store.add_image("sha-active", "x/y.png", "image/png", 8, tags=[], source="user", created_at=now)

    assert qzone.pick_image(store, settings_, now) == image


def test_pick_image_without_todays_image_is_none(tmp_path):
    settings_ = settings(tmp_path)
    store = Store(settings_.db_path)
    store.add_image("sha-old", "a/b.png", "image/png", 8, tags=[], source="user", created_at=1000)

    assert qzone.pick_image(store, settings_, 1793868800) is None

def test_draft_tells_her_not_to_repeat_recent_posts(tmp_path):
    from qqbot.store import Store

    store = Store(tmp_path / "chat.db")
    qzone.ensure(store)
    with store._lock:
        store._conn.execute(
            "INSERT INTO qzone_posts (content, status, created_at) VALUES (?,?,?)",
            ("第三锅牛腩，汤终于浓了", "ok", 100),
        )
        store._conn.execute(
            "INSERT INTO qzone_posts (content, status, created_at) VALUES (?,?,?)",
            ("番茄膏挤多了，锅边糊了一圈", "ok", 200),
        )
        store._conn.commit()
    llm = FakeLLM("今天翻了两页书，窗外下雨。")
    cfg = settings(tmp_path)

    qzone.draft(store, llm, cfg, now=int(time.time()))

    prompt = llm.calls[0][0]["content"]
    assert "第三锅牛腩" in prompt
    assert "同一件事别连着写" in prompt
    store.close()

def test_draft_includes_todays_life(tmp_path):
    store = Store(tmp_path / "chat.db")
    qzone.ensure(store)
    life_dir = tmp_path / "life"
    life_dir.mkdir(parents=True, exist_ok=True)
    cfg = settings(tmp_path, life_dir=life_dir)
    (life_dir / (time.strftime("%Y-%m-%d") + ".md")).write_text(
        "今天挑的是：把窗帘洗了\n泡了第二杯茶\n楼下新开了面包店", encoding="utf-8")
    llm = FakeLLM("今天晒了窗帘。")

    qzone.draft(store, llm, cfg, now=int(time.time()))

    prompt = llm.calls[0][0]["content"]
    assert "泡了第二杯茶" in prompt
    store.close()

