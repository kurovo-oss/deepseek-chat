# -*- coding: utf-8 -*-
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "qqbot" / "ui"


def test_startup_animation_uses_existing_character_and_expected_beats():
    html = (UI / "index.html").read_text(encoding="utf-8")
    css = (UI / "startup.css").read_text(encoding="utf-8")
    script = (UI / "startup.js").read_text(encoding="utf-8")

    assert '<div id="startup" class="startup"' in html
    assert 'src="/assets/welcome-mascot.png"' in html
    assert 'class="startup-bowl"' in html
    assert 'class="startup-rice"' in html
    assert 'class="startup-eyes"' in html
    assert "满血复活！" in html
    assert "咕噜…" in html

    # 角色只做动画，不重新生成/改色：仍复用原来的透明立绘。
    assert "dafeiyu" not in css.lower()
    assert "saturate(" not in css
    assert "hue-rotate(" not in css
    assert "startup-char" in css
    assert "animation: startup-char 5.2s" in css
    assert "animation: startup-bowl 5.2s" in css
    assert "animation: startup-hp-fill 5.2s" in css
    assert "@keyframes startup-burst" in css

    assert "prefers-reduced-motion: reduce" in css
    assert ".startup { display: none !important; }" in css
    assert "prefers-reduced-motion: reduce" in script
    assert "params.get('screenshot') === '1'" in script
    assert "setTimeout(finish, 5100)" in script


def test_shell_screenshot_skips_startup_overlay():
    source = (ROOT / "scripts" / "native" / "WebShell.cs").read_text(encoding="utf-8")

    assert 'if (_shot) url += "&screenshot=1";' in source


def test_startup_animation_can_be_skipped_with_mouse_and_keyboard():
    html = (UI / "index.html").read_text(encoding="utf-8")
    script = (UI / "startup.js").read_text(encoding="utf-8")

    assert 'id="startup-skip"' in html
    assert "跳过" in html
    assert "overlay.addEventListener('click', finish)" in script
    assert "event.key === 'Escape'" in script
    assert "event.key === ' '" in script
    assert "event.key === 'Enter'" in script
    assert "overlay.parentNode.removeChild(overlay)" in script
