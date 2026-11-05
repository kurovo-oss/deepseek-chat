"""对外写操作的闸门：开关、试运行、额度、冷却、熔断、审计。"""

from __future__ import annotations

import time

from qqbot import guard
from qqbot.config import Settings
from qqbot.store import Store


def settings(tmp_path, **overrides):
    base = dict(
        allow_user_id=7,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "g.db",
        system_prompt_file=tmp_path / "s.md",
        image_dir=tmp_path / "images",
        qzone_enabled=True,
        qzone_dry_run=True,
        qzone_daily_max=2,
        douyin_enabled=True,
        douyin_dry_run=True,
        douyin_daily_max=2,
    )
    base.update(overrides)
    return Settings(**base)


def make_guard(tmp_path, **overrides):
    return guard.Guard(Store(tmp_path / "g.db"), settings(tmp_path, **overrides))


def test_disabled_is_denied(tmp_path):
    g = make_guard(tmp_path, qzone_enabled=False)

    decision = g.check("qzone", "post")

    assert decision.allowed is False
    assert decision.reason == "disabled"


def test_dry_run_is_allowed_but_flagged(tmp_path):
    g = make_guard(tmp_path)

    decision = g.check("qzone", "post", user_id=7)

    assert decision.allowed is True
    assert decision.dry_run is True
    assert decision.reason == "dry_run"


def test_owner_only(tmp_path):
    g = make_guard(tmp_path)

    assert g.check("qzone", "post", user_id=999).reason == "owner_only"


def test_daily_quota(tmp_path):
    g = make_guard(tmp_path, qzone_dry_run=False)
    now = int(time.time())

    first = g.check("qzone", "post", user_id=7, now=now)
    g.record("qzone", "post", payload="一", result="ok", now=now)
    second = g.check("qzone", "post", user_id=7, now=now + 1300)
    g.record("qzone", "post", payload="二", result="ok", now=now + 1300)
    third = g.check("qzone", "post", user_id=7, now=now + 2600)

    assert first.allowed and second.allowed
    assert third.allowed is False
    assert third.reason == "quota"


def test_cooldown_blocks_back_to_back(tmp_path):
    g = make_guard(tmp_path, qzone_dry_run=False)
    now = int(time.time())
    g.record("qzone", "post", payload="一", result="ok", now=now)

    decision = g.check("qzone", "post", user_id=7, now=now + 60)

    assert decision.allowed is False
    assert decision.reason == "cooldown"


def test_circuit_opens_after_three_failures(tmp_path):
    g = make_guard(tmp_path, qzone_dry_run=False)
    now = int(time.time())
    for i in range(3):
        g.check("qzone", "post", user_id=7, now=now + i)
        g.record("qzone", "post", payload="x", result="failed", error="boom", now=now + i)

    decision = g.check("qzone", "post", user_id=7, now=now + 5)

    assert decision.allowed is False
    assert decision.reason == "circuit_open"


def test_sticker_hourly_quota(tmp_path):
    g = make_guard(tmp_path, sticker_hourly_max=2, sticker_daily_max=100)
    now = int(time.time())

    for _ in range(2):
        assert g.check("sticker", "save", user_id=7, now=now).allowed
        g.record("sticker", "save", result="ok", now=now)

    assert g.check("sticker", "save", user_id=7, now=now).reason == "hourly_quota"


def test_record_and_recent(tmp_path):
    g = make_guard(tmp_path)

    g.record("qzone", "post", target="定时", payload="今天真好", result="dry_run", now=1000)

    rows = g.recent(5)
    assert rows[0]["module"] == "qzone"
    assert rows[0]["result"] == "dry_run"
    assert rows[0]["payload_digest"]
    assert "今天真好" not in rows[0]["payload_digest"]


def test_status_counts(tmp_path):
    g = make_guard(tmp_path)
    g.record("qzone", "post", result="ok", now=int(time.time()))

    status = g.status()

    assert status["qzone"]["enabled"] is True
    assert status["qzone"]["used_today"] == 1
