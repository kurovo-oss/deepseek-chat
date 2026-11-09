"""粉色主题：CSS 与窗口壳用粉色，但角色原图不能被改画。"""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "qqbot" / "ui" / "app.css"
MASCOT = ROOT / "qqbot" / "ui" / "assets" / "dafeiyu.png"
UPSTREAM_MASCOT = ROOT / "docs" / "assets" / "dafeiyu.png"


def test_css_uses_pink_brand_palette():
    css = CSS.read_text(encoding="utf-8")

    assert "--brand-ink:#2a0d1c" in css
    assert "--brand-ocean:#ff6f9f" in css
    assert "--brand-cyan:#ffb3d1" in css
    assert "--cyan:#ff8fbd" in css
    # 旧的海盐蓝主色不能再大面积回来
    assert "rgba(90,215,255," not in css
    assert "#05111f" not in css
    assert "#5ad7ff" not in css


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_mascot_is_the_unmodified_upstream_artwork():
    # 面板换成粉色，但角色只能是原作者的现成图：不重绘、不调色、不改衣着。
    assert digest(MASCOT) == digest(UPSTREAM_MASCOT)


def test_native_loading_screen_matches_pink_theme():
    source = (ROOT / "scripts" / "native" / "WebShell.cs").read_text(encoding="utf-8")

    assert "#1a0812" in source
    assert "#ff8fbd" in source
    assert "#05111f" not in source
