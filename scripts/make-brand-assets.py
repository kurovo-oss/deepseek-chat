# -*- coding: utf-8 -*-
"""从网上二创的大肥鱼形象裁切品牌资产：欢迎图、安装包图标、NSIS 侧栏图。

用法：
    tools\\python\\python.exe scripts\\make-brand-assets.py

角色始终来自 qqbot/ui/assets/dafeiyu.png（作者 YunYueSama），
脚本只做裁切、缩放和品牌底色排版，不再另画角色。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
UI_ASSETS = ROOT / "qqbot" / "ui" / "assets"
INSTALLER_ASSETS = ROOT / "installer" / "assets"
SOURCE = UI_ASSETS / "dafeiyu.png"

INK = (6, 27, 44, 255)
INK_SOFT = (8, 42, 66, 255)
CYAN = (110, 231, 255, 255)


def mix(a: tuple[int, int, int, int], b: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    return tuple(round(a[i] * (1 - t) + b[i] * t) for i in range(4))


def vertical_gradient(
    size: tuple[int, int],
    top: tuple[int, int, int, int],
    bottom: tuple[int, int, int, int],
) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        draw.line([(0, y), (width, y)], fill=mix(top, bottom, y / max(1, height - 1)))
    return image


def radial_glow(size: int, color: tuple[int, int, int, int], alpha: int = 110) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    steps = 28
    for i in range(steps, 0, -1):
        ratio = i / steps
        radius = int(size * 0.5 * ratio)
        layer_alpha = int(alpha * (1 - ratio) ** 1.6)
        draw.ellipse(
            (size // 2 - radius, size // 2 - radius, size // 2 + radius, size // 2 + radius),
            fill=(color[0], color[1], color[2], max(0, min(255, layer_alpha))),
        )
    return image.filter(ImageFilter.GaussianBlur(size // 36))


def load_source() -> Image.Image:
    if not SOURCE.exists():
        raise SystemExit(f"missing source art: {SOURCE}")
    return Image.open(SOURCE).convert("RGBA")


def make_welcome(source: Image.Image) -> Image.Image:
    """把二创全身像放进 900×900 透明画布，保留完整角色。"""
    canvas = Image.new("RGBA", (900, 900), (0, 0, 0, 0))
    art = source.copy()
    target_height = 780
    ratio = target_height / art.height
    art = art.resize((round(art.width * ratio), target_height), Image.Resampling.LANCZOS)
    canvas.alpha_composite(
        art,
        ((900 - art.width) // 2, (900 - art.height) // 2),
    )
    return canvas


def make_character_icon(source: Image.Image) -> Image.Image:
    """安装包和 exe 的图标：直接缩放二创形象，不加任何自绘元素。"""
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    art = source.copy()
    art.thumbnail((236, 236), Image.Resampling.LANCZOS)
    canvas.alpha_composite(
        art,
        ((256 - art.width) // 2, (256 - art.height) // 2),
    )
    return canvas


def make_brand_panel(source: Image.Image) -> Image.Image:
    """NSIS 欢迎/完成页侧栏：品牌底色 + 二创全身像，不画新角色。"""
    width, height = 656, 1256
    panel = vertical_gradient((width, height), INK, INK_SOFT)

    glow = radial_glow(900, CYAN, 90)
    panel.alpha_composite(glow, ((width - glow.width) // 2, height - 760))

    art = source.copy()
    target_height = 1040
    ratio = target_height / art.height
    art = art.resize((round(art.width * ratio), target_height), Image.Resampling.LANCZOS)
    if art.width > width:
        left = (art.width - width) // 2
        art = art.crop((left, 0, left + width, art.height))
    panel.alpha_composite(art, ((width - art.width) // 2, height - art.height - 24))
    return panel.convert("RGB").resize((164, 314), Image.Resampling.LANCZOS)


def save_ico(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def main() -> int:
    UI_ASSETS.mkdir(parents=True, exist_ok=True)
    INSTALLER_ASSETS.mkdir(parents=True, exist_ok=True)
    source = load_source()

    welcome = make_welcome(source)
    welcome.save(UI_ASSETS / "welcome-mascot.png", format="PNG", optimize=True)
    save_ico(make_character_icon(source), UI_ASSETS / "dafeiyu.ico")

    panel = make_brand_panel(source)
    panel.save(INSTALLER_ASSETS / "brand-panel.bmp", format="BMP")

    print(f"welcome: {(UI_ASSETS / 'welcome-mascot.png').stat().st_size} bytes")
    print(f"character icon: {(UI_ASSETS / 'dafeiyu.ico').stat().st_size} bytes")
    print(f"panel: {(INSTALLER_ASSETS / 'brand-panel.bmp').stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
