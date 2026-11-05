"""抖音：刷到 → 点赞 → 有趣的分享，全部过闸门。"""

from __future__ import annotations

import time

from qqbot import douyin, guard
from qqbot.config import Settings
from qqbot.store import Store


class FakePage:
    def __init__(self, feed=None, like=None, login=None):
        self.feed = feed or []
        self.like = like or {"ok": True, "changed": True}
        self.login = login or {"has_avatar": True, "has_login_button": False}
        self.visited = []

    def call(self, method, params=None):
        return {}

    def evaluate(self, expression, **kwargs):
        if "has_login_button" in expression:
            return self.login
        if "feed-item" in expression:
            return self.feed
        if "like-icon" in expression:
            return self.like
        return None

    def goto(self, url, timeout=30):
        self.visited.append(url)

    def close(self):
        pass


class FakeBrowser:
    def __init__(self, page):
        self.page = page

    def start(self):
        return self.page

    def stop(self):
        pass


class FakeSender:
    def __init__(self):
        self.sent = []

    def send_private(self, user_id, text):
        self.sent.append((user_id, text))
        return True


def settings(tmp_path, **overrides):
    base = dict(
        allow_user_id=7,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
        douyin_enabled=True,
        douyin_dry_run=False,
        douyin_daily_max=5,
        douyin_share_enabled=True,
        douyin_share_daily_max=2,
        douyin_profile=tmp_path / "prof",
    )
    base.update(overrides)
    return Settings(**base)


def env(tmp_path, **overrides):
    cfg = settings(tmp_path, **overrides)
    store = Store(cfg.db_path)
    return cfg, store, guard.Guard(store, cfg)


def test_likes_and_shares_interesting_video(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    cfg, store, gate = env(tmp_path)
    sender = FakeSender()
    page = FakePage(
        feed=[
            {"id": "111", "text": "一只橘猫在键盘上睡着了"},
            {"id": "222", "text": "随手拍的云"},
        ]
    )

    result = douyin.run_once(store, cfg, gate, sender, FakeBrowser(page), now=int(time.time()))

    assert result["liked"] == 2
    assert result["shared"] == 1
    assert "douyin.com/video/111" in sender.sent[0][1]
    modules = [row["module"] for row in gate.recent(10)]
    assert "douyin" in modules and "douyin_share" in modules


def test_uninteresting_videos_are_only_liked(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    cfg, store, gate = env(tmp_path)
    sender = FakeSender()
    page = FakePage(feed=[{"id": "333", "text": "随手拍的云"}])

    result = douyin.run_once(store, cfg, gate, sender, FakeBrowser(page), now=int(time.time()))

    assert result["liked"] == 1
    assert result["shared"] == 0
    assert sender.sent == []


def test_dry_run_never_sends(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    cfg, store, gate = env(tmp_path, douyin_dry_run=True)
    sender = FakeSender()
    page = FakePage(feed=[{"id": "444", "text": "猫猫做饭翻车"}])

    result = douyin.run_once(store, cfg, gate, sender, FakeBrowser(page), now=int(time.time()))

    assert result["liked"] == 1
    assert result["shared"] == 1
    assert sender.sent == []
    assert any(row["result"] == "dry_run" for row in gate.recent(5))


def test_not_logged_in_notifies_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    cfg, store, gate = env(tmp_path)
    page = FakePage(login={"has_avatar": False, "has_login_button": True})
    messages = []

    result = douyin.run_once(
        store, cfg, gate, FakeSender(), FakeBrowser(page), now=int(time.time()),
        notify=messages.append,
    )

    assert result["error"] == "not_logged_in"
    assert messages and "登录" in messages[0]


def test_daily_quota_stops_after_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    cfg, store, gate = env(tmp_path, douyin_daily_max=1)
    page = FakePage(
        feed=[
            {"id": "555", "text": "游戏高光"},
            {"id": "666", "text": "另一条游戏"},
        ]
    )

    result = douyin.run_once(store, cfg, gate, FakeSender(), FakeBrowser(page), now=int(time.time()))

    assert result["liked"] == 1
    assert result["skipped"] == 1


def test_failed_like_is_audited_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    cfg, store, gate = env(tmp_path)
    page = FakePage(
        feed=[{"id": "777", "text": "猫"}],
        like={"ok": True, "changed": False},
    )

    result = douyin.run_once(store, cfg, gate, FakeSender(), FakeBrowser(page), now=int(time.time()))

    assert result["error"] == "like_failed"
    assert gate.recent(1)[0]["result"] == "failed"


def test_share_text_keeps_link():
    text = douyin.share_text("猫猫做饭", "999")

    assert "猫猫做饭" in text
    assert "https://www.douyin.com/video/999" in text

def test_stop_browser_without_pidfile_is_false(tmp_path):
    cfg = settings(tmp_path)
    assert douyin.stop_browser(cfg) is False


def test_pid_file_lives_in_profile(tmp_path):
    cfg = settings(tmp_path)
    assert douyin.pid_file(cfg) == tmp_path / "prof" / "browser.pid"

def test_custom_home_and_video_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    cfg, store, gate = env(
        tmp_path,
        douyin_home_url="http://mock.local/",
        douyin_video_url="http://mock.local/v/{aweme_id}",
    )
    page = FakePage(feed=[{"id": "9", "text": "云"}])

    douyin.run_once(store, cfg, gate, FakeSender(), FakeBrowser(page), now=int(time.time()))

    assert page.visited[0] == "http://mock.local/"
    assert "http://mock.local/v/9" in page.visited

def test_captcha_blocks_and_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    cfg, store, gate = env(tmp_path)
    page = FakePage(login={"has_avatar": False, "has_login_button": True, "captcha": True})
    messages = []

    result = douyin.run_once(
        store, cfg, gate, FakeSender(), FakeBrowser(page), now=int(time.time()),
        notify=messages.append,
    )

    assert result["error"] == "captcha"
    assert messages and "验证" in messages[0]


def test_no_avatar_means_not_logged_in_even_without_login_button(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    cfg, store, gate = env(tmp_path)
    page = FakePage(login={"has_avatar": False, "has_login_button": False})

    result = douyin.run_once(store, cfg, gate, FakeSender(), FakeBrowser(page), now=int(time.time()))

    assert result["error"] == "not_logged_in"

