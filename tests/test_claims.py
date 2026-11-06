"""Claim 级事实校验：她说的每句话能不能对上账本。"""

from __future__ import annotations

import time

from qqbot import claims
from qqbot.store import Store


def test_capability_claim_is_rejected():
    store = Store(":memory:")
    result = claims.verify("我今天拍了段视频，还剪了半天，手都酸了。", store)
    kinds = [claim["kind"] for claim in claims.issues(result)]
    assert "capability" in kinds
    store.close()


def test_quote_must_exist_in_recent_messages():
    store = Store(":memory:")
    store.save_message("7", "user", "周六我带你去看海")

    ok = claims.verify("你说「周六我带你去看海」，我记着呢。", store)
    assert claims.issues(ok) == []

    bad = claims.verify("你说「下周去爬山」，我记着呢。", store)
    assert [claim["kind"] for claim in claims.issues(bad)] == ["quote"]
    store.close()


def test_post_claim_needs_a_real_post():
    store = Store(":memory:")

    bad = claims.verify("我发了条说说，等你来看。", store)
    assert [claim["kind"] for claim in claims.issues(bad)] == ["post"]

    store._conn.execute(
        "CREATE TABLE qzone_posts (id INTEGER PRIMARY KEY, content TEXT, images TEXT DEFAULT '[]',"
        " tid TEXT DEFAULT '', status TEXT, error TEXT DEFAULT '', created_at INTEGER)"
    )
    store._conn.execute(
        "INSERT INTO qzone_posts (content, status, created_at) VALUES (?,?,?)",
        ("昨天那条", "ok", int(time.time()) - 3600),
    )
    store._conn.commit()

    ok = claims.verify("我发了条说说，等你来看。", store)
    assert claims.issues(ok) == []
    store.close()


def test_sticker_claim_needs_a_real_image():
    store = Store(":memory:")

    bad = claims.verify("你昨天发了个猫表情，我存下来了。", store)
    assert [claim["kind"] for claim in claims.issues(bad)] == ["sticker"]

    store.add_image("sha-x", "aa/x.png", "image/png", 10, tags=["猫"], source="user")
    ok = claims.verify("你昨天发了个猫表情，我存下来了。", store)
    assert claims.issues(ok) == []
    store.close()


def test_plain_diary_has_no_claims():
    store = Store(":memory:")
    assert claims.verify("今天把窗帘洗了，晾了一下午，摸起来有太阳味。", store) == []
    store.close()
