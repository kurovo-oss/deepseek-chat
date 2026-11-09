"""粉色主题：CSS 与窗口壳用粉色，但角色原图不能被改画。"""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "qqbot" / "ui" / "app.css"
MASCOT = ROOT / "qqbot" / "ui" / "assets" / "dafeiyu.png"
UPSTREAM_MASCOT = ROOT / "docs" / "assets" / "dafeiyu.png"


def test_css_uses_light_sakura_palette():
    css = CSS.read_text(encoding="utf-8")

    assert "--brand-ink:#4a2334" in css
    assert "--brand-ocean:#e46f95" in css
    assert "--brand-cyan:#ffc9d9" in css
    assert "--cyan:#c2466c" in css
    assert "--bg:#fff7fa" in css
    assert "color-scheme:light" in css
    # 不能再回到深色梅子底或旧的海盐蓝
    assert "#1a0812" not in css
    assert "#05111f" not in css
    assert "#5ad7ff" not in css


def test_sakura_petals_are_decorative_and_respect_reduced_motion():
    css = CSS.read_text(encoding="utf-8")
    html = (ROOT / "qqbot" / "ui" / "index.html").read_text(encoding="utf-8")

    assert '<div class="petals" aria-hidden="true">' in html
    assert '<div class="hero-petals" aria-hidden="true">' in html
    assert html.count('class="petal"') >= 12
    assert ".petals{" in css
    assert "@keyframes petal-fall" in css
    assert "@keyframes hero-petal-fall" in css
    assert "@media(prefers-reduced-motion:reduce)" in css
    assert ".petal,.hero-petals{display:none}" in css


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_mascot_is_the_unmodified_upstream_artwork():
    # 面板换成粉色，但角色只能是原作者的现成图：不重绘、不调色、不改衣着。
    assert digest(MASCOT) == digest(UPSTREAM_MASCOT)


def test_native_loading_screen_matches_pink_theme():
    source = (ROOT / "scripts" / "native" / "WebShell.cs").read_text(encoding="utf-8")

    assert "#fff7fa" in source
    assert "#4a2334" in source
    assert "#c63a60" in source
    assert "#1a0812" not in source
    assert "#ff8fbd" not in source
