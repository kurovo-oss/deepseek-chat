# -*- coding: utf-8 -*-
"""把大肥鱼女仆装的蓝色布料染成粉色，保留头发、皮肤和白色围裙。

源图是 docs/assets/dafeiyu.png（YunYueSama 的原图，599×820）。
输出写回 qqbot/ui/assets/dafeiyu.png；欢迎图、图标和安装器侧栏图再由
scripts/make-brand-assets.py 从输出生成。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "assets" / "dafeiyu.png"
TARGET = ROOT / "qqbot" / "ui" / "assets" / "dafeiyu.png"

# 335° 左右，偏珊瑚粉；只改布料，不改头发。
PINK_HUE = round(335 / 360 * 255)


def outfit_mask(image: Image.Image) -> np.ndarray:
    """蓝色布料：下半身、靠近白围裙、避开两侧长发。"""
    rgba = np.array(image.convert("RGBA"))
    height, width = rgba.shape[:2]
    r, g, b, alpha = (rgba[:, :, i].astype(int) for i in range(4))
    yy, xx = np.mgrid[0:height, 0:width]

    blue = (alpha > 40) & (b > 70) & (b > r + 18) & (b > g + 3)
    white = (alpha > 40) & (r > 210) & (g > 205) & (b > 195)
    apron = Image.fromarray((white * 255).astype("uint8")).filter(
        ImageFilter.MaxFilter(61)
    )
    near_apron = np.array(apron) > 0

    central = (xx > width * 0.12) & (xx < width * 0.88) & (yy > height * 0.46)
    skirt = (yy > height * 0.74) & (xx > width * 0.08) & (xx < width * 0.92)
    # 胸前的蓝色胸衣/领结：只在中央窄带，避免把两侧长发染粉。
    bodice = (
        (yy > height * 0.42)
        & (yy < height * 0.62)
        & (xx > width * 0.30)
        & (xx < width * 0.70)
    )
    return blue & central & (near_apron | skirt | bodice)


def recolor(image: Image.Image) -> Image.Image:
    mask = outfit_mask(image)
    hsv = np.array(image.convert("HSV"))
    hsv[:, :, 0][mask] = PINK_HUE
    # 粉色比蓝色浅一点，保留原来的明暗阴影，避免变成一块平涂。
    hsv[:, :, 1][mask] = np.clip(hsv[:, :, 1][mask] * 0.92 + 12, 55, 230)
    result = Image.fromarray(hsv, "HSV").convert("RGBA")
    # convert("HSV") 丢掉 alpha，必须从原图补回来，否则透明背景会变黑。
    result.putalpha(image.getchannel("A"))
    return result


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit(f"missing source art: {SOURCE}")
    original = Image.open(SOURCE).convert("RGBA")
    result = recolor(original)
    result.save(TARGET, format="PNG", optimize=True)

    rgba = np.array(result)
    r, g, b = (rgba[:, :, i].astype(int) for i in range(3))
    pink = (r > 180) & (r > g + 35) & (r > b + 20)
    print(f"wrote {TARGET} ({TARGET.stat().st_size} bytes), pink pixels={int(pink.sum())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
