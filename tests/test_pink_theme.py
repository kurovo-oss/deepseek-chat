"""粉色女仆主题：CSS 与品牌图不能悄悄回到蓝色。"""

from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "qqbot" / "ui" / "app.css"
MASCOT = ROOT / "qqbot" / "ui" / "assets" / "dafeiyu.png"


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


def pink_stats(path: Path) -> tuple[int, int, int]:
    image = Image.open(path).convert("RGBA")
    arr = np.array(image)
    height = arr.shape[0]
    r, g, b, alpha = (arr[:, :, i].astype(int) for i in range(4))
    pink = (alpha > 40) & (r > 160) & (r > g + 30) & (r > b + 15)
    return (
        int(pink.sum()),
        int(pink[: int(height * 0.40)].sum()),
        int(pink[int(height * 0.60) :].sum()),
    )


def test_mascot_has_pink_outfit_but_blue_hair():
    before = pink_stats(ROOT / "docs" / "assets" / "dafeiyu.png")
    after = pink_stats(MASCOT)

    assert after[0] > before[0] + 10_000, "女仆装没有染成粉色"
    # 头发在画面上半部，保持蓝色；粉色只应来自原有的肤色/嘴唇。
    assert after[1] <= before[1] + 100
    assert after[2] > before[2] + 8_000

def test_native_loading_screen_matches_pink_theme():
    source = (ROOT / "scripts" / "native" / "WebShell.cs").read_text(encoding="utf-8")

    assert "#1a0812" in source
    assert "#ff8fbd" in source
    assert "#05111f" not in source
