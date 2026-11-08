# -*- coding: utf-8 -*-
"""生成大肥鱼品牌资产：欢迎图、鱼形图标、NSIS 侧栏图。

用法：
    tools\\python\\python.exe scripts\\make-brand-assets.py

所有资产都生成在仓库内，构建时可重复生成，不依赖外部图片。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
UI_ASSETS = ROOT / "qqbot" / "ui" / "assets"
INSTALLER_ASSETS = ROOT / "installer" / "assets"

INK = (6, 27, 44, 255)
INK_SOFT = (8, 42, 66, 255)
OCEAN = (47, 140, 255, 255)
OCEAN_DEEP = (18, 83, 170, 255)
CYAN = (110, 231, 255, 255)
CYAN_LIGHT = (185, 244, 255, 255)
CREAM = (255, 242, 212, 255)
CORAL = (255, 138, 160, 255)
CORAL_LIGHT = (255, 184, 198, 255)
WHITE = (255, 255, 255, 255)


def rgba(color: tuple[int, int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return color[0], color[1], color[2], max(0, min(255, alpha))


def mix(a: tuple[int, int, int, int], b: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    return tuple(round(a[i] * (1 - t) + b[i] * t) for i in range(4))


def vertical_gradient(size: tuple[int, int], top: tuple[int, int, int, int], bottom: tuple[int, int, int, int]) -> Image.Image:
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
            fill=rgba(color, layer_alpha),
        )
    return image.filter(ImageFilter.GaussianBlur(size // 36))


def heart(canvas: Image.Image, box: tuple[int, int, int, int], fill: tuple[int, int, int, int], outline: tuple[int, int, int, int], width: int) -> None:
    """在 box 内画一颗圆润的心。"""
    x0, y0, x1, y1 = box
    width = max(1, width)
    w = x1 - x0
    h = y1 - y0
    draw = ImageDraw.Draw(canvas)
    left = (x0 + int(w * 0.05), y0 + int(h * 0.10), x0 + int(w * 0.54), y0 + int(h * 0.60))
    right = (x0 + int(w * 0.46), y0 + int(h * 0.10), x0 + int(w * 0.95), y0 + int(h * 0.60))
    draw.ellipse(left, fill=fill, outline=outline, width=width)
    draw.ellipse(right, fill=fill, outline=outline, width=width)
    draw.polygon(
        [
            (x0 + int(w * 0.07), y0 + int(h * 0.40)),
            (x0 + int(w * 0.93), y0 + int(h * 0.40)),
            (x0 + int(w * 0.50), y1),
        ],
        fill=fill,
        outline=outline,
    )
    # 两个圆和三角形之间的接缝补一下，避免出现细线。
    draw.rectangle(
        (x0 + int(w * 0.16), y0 + int(h * 0.34), x0 + int(w * 0.84), y0 + int(h * 0.47)),
        fill=fill,
    )


def draw_bubbles(draw: ImageDraw.ImageDraw, points: list[tuple[float, float, float]]) -> None:
    for x, y, r in points:
        draw.ellipse(
            (x - r, y - r, x + r, y + r),
            fill=rgba(CYAN_LIGHT, 38),
            outline=rgba(CYAN_LIGHT, 120),
            width=max(1, int(r * 0.18)),
        )
        draw.arc(
            (x - r * 0.58, y - r * 0.58, x + r * 0.28, y + r * 0.28),
            205,
            310,
            fill=rgba(WHITE, 150),
            width=max(1, int(r * 0.2)),
        )


def draw_sparkles(draw: ImageDraw.ImageDraw, points: list[tuple[float, float, float, tuple[int, int, int, int]]]) -> None:
    for x, y, r, color in points:
        draw.polygon(
            [(x, y - r), (x + r * 0.22, y - r * 0.22), (x + r, y), (x + r * 0.22, y + r * 0.22),
             (x, y + r), (x - r * 0.22, y + r * 0.22), (x - r, y), (x - r * 0.22, y - r * 0.22)],
            fill=color,
        )


def draw_whale(size: int, icon: bool = False) -> Image.Image:
    """透明底的大肥鱼：圆滚滚的蓝鲸，抱着珊瑚色爱心。"""
    scale = 2
    s = size * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    if icon:
        bg = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.23), fill=255)
        gradient = vertical_gradient((s, s), (5, 24, 42, 255), (10, 67, 105, 255))
        bg.paste(gradient, (0, 0), mask)
        glow = radial_glow(int(s * 0.92), CYAN, 80)
        bg.alpha_composite(glow, (int(s * 0.04), int(s * 0.03)))
        image = bg
        draw = ImageDraw.Draw(image)
        draw.arc((int(s * 0.06), int(s * 0.18), int(s * 0.94), int(s * 1.05)), 18, 162, fill=rgba(CYAN, 70), width=max(2, s // 160))
        draw.arc((int(s * 0.10), int(s * 0.24), int(s * 0.90), int(s * 1.00)), 20, 160, fill=rgba(CORAL, 38), width=max(2, s // 220))
    else:
        draw = ImageDraw.Draw(image)
        shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse((int(s * 0.22), int(s * 0.83), int(s * 0.84), int(s * 0.94)), fill=(2, 14, 25, 92))
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(s // 50)))

    draw = ImageDraw.Draw(image)

    # 尾鳍：三片小扇子，先画在身体后面。
    tail_fill = OCEAN_DEEP if not icon else mix(OCEAN, INK, 0.2)
    tail_points = [
        [(int(s * 0.27), int(s * 0.56)), (int(s * 0.04), int(s * 0.36)), (int(s * 0.11), int(s * 0.53)), (int(s * 0.28), int(s * 0.62))],
        [(int(s * 0.27), int(s * 0.58)), (int(s * 0.03), int(s * 0.66)), (int(s * 0.12), int(s * 0.70)), (int(s * 0.28), int(s * 0.65))],
        [(int(s * 0.28), int(s * 0.56)), (int(s * 0.16), int(s * 0.78)), (int(s * 0.31), int(s * 0.72)), (int(s * 0.34), int(s * 0.62))],
    ]
    for points in tail_points:
        draw.polygon(points, fill=tail_fill, outline=INK, width=max(2, s // 180))
    draw.line(
        [(int(s * 0.27), int(s * 0.55)), (int(s * 0.12), int(s * 0.47))],
        fill=rgba(CYAN_LIGHT, 150),
        width=max(2, s // 240),
    )

    # 身体。
    body_box = (int(s * 0.17), int(s * 0.28), int(s * 0.92), int(s * 0.87))
    body_mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(body_mask).ellipse(body_box, fill=255)
    body_grad = vertical_gradient((s, s), (32, 132, 245, 255), (110, 231, 255, 255))
    body_grad = Image.composite(
        body_grad,
        Image.new("RGBA", (s, s), OCEAN_DEEP),
        body_mask,
    )
    image.alpha_composite(Image.composite(body_grad, Image.new("RGBA", (s, s), (0, 0, 0, 0)), body_mask))

    # 奶油色肚皮。
    draw = ImageDraw.Draw(image)
    belly_mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(belly_mask).ellipse(
        (int(s * 0.25), int(s * 0.48), int(s * 0.85), int(s * 0.84)),
        fill=255,
    )
    belly_mask = Image.composite(belly_mask, Image.new("L", (s, s), 0), body_mask)
    belly = Image.new("RGBA", (s, s), CREAM)
    image.alpha_composite(Image.composite(belly, Image.new("RGBA", (s, s), (0, 0, 0, 0)), belly_mask))

    draw = ImageDraw.Draw(image)
    draw.ellipse(body_box, outline=INK, width=max(3, s // 120))
    draw.arc(
        (int(s * 0.22), int(s * 0.31), int(s * 0.86), int(s * 0.78)),
        195,
        335,
        fill=rgba(WHITE, 120),
        width=max(2, s // 160),
    )

    # 背鳍和胸鳍。
    draw.polygon(
        [(int(s * 0.42), int(s * 0.29)), (int(s * 0.50), int(s * 0.19)), (int(s * 0.61), int(s * 0.29))],
        fill=OCEAN_DEEP,
        outline=INK,
        width=max(2, s // 180),
    )
    draw.ellipse(
        (int(s * 0.58), int(s * 0.63), int(s * 0.75), int(s * 0.77)),
        fill=mix(OCEAN, WHITE, 0.18),
        outline=INK,
        width=max(2, s // 180),
    )
    draw.line(
        [(int(s * 0.62), int(s * 0.69)), (int(s * 0.70), int(s * 0.70))],
        fill=rgba(OCEAN_DEEP, 160),
        width=max(2, s // 220),
    )

    # 眼睛、腮红、笑。
    eye_x, eye_y = int(s * 0.69), int(s * 0.53)
    eye_r = int(s * 0.058)
    draw.ellipse(
        (eye_x - eye_r, eye_y - eye_r, eye_x + eye_r, eye_y + eye_r),
        fill=WHITE,
        outline=INK,
        width=max(2, s // 180),
    )
    pupil_r = int(eye_r * 0.54)
    draw.ellipse(
        (eye_x - pupil_r + int(s * 0.006), eye_y - pupil_r, eye_x + pupil_r + int(s * 0.006), eye_y + pupil_r),
        fill=INK,
    )
    draw.ellipse(
        (eye_x - pupil_r + int(s * 0.010), eye_y - pupil_r + int(s * 0.010),
         eye_x - pupil_r + int(s * 0.030), eye_y - pupil_r + int(s * 0.034)),
        fill=WHITE,
    )
    draw.ellipse(
        (int(s * 0.57), int(s * 0.63), int(s * 0.65), int(s * 0.68)),
        fill=rgba(CORAL, 105),
    )
    draw.ellipse(
        (int(s * 0.79), int(s * 0.63), int(s * 0.86), int(s * 0.68)),
        fill=rgba(CORAL, 95),
    )
    draw.arc(
        (int(s * 0.70), int(s * 0.63), int(s * 0.82), int(s * 0.75)),
        20,
        150,
        fill=INK,
        width=max(2, s // 180),
    )

    # 爱心和气泡。
    heart(
        image,
        (int(s * 0.66), int(s * 0.05), int(s * 0.88), int(s * 0.27)),
        CORAL,
        INK,
        max(2, s // 220),
    )
    heart(
        image,
        (int(s * 0.71), int(s * 0.10), int(s * 0.78), int(s * 0.17)),
        CORAL_LIGHT,
        rgba(CORAL_LIGHT, 0),
        max(1, s // 300),
    )
    draw = ImageDraw.Draw(image)
    draw_bubbles(draw, [
        (s * 0.84, s * 0.34, s * 0.018),
        (s * 0.90, s * 0.27, s * 0.012),
        (s * 0.22, s * 0.25, s * 0.011),
        (s * 0.30, s * 0.18, s * 0.007),
    ])
    draw_sparkles(draw, [
        (s * 0.19, s * 0.35, s * 0.018, rgba(CORAL, 210)),
        (s * 0.88, s * 0.55, s * 0.014, rgba(CYAN_LIGHT, 210)),
        (s * 0.58, s * 0.90, s * 0.010, rgba(CREAM, 210)),
    ])

    return image.resize((size, size), Image.LANCZOS)


def make_welcome() -> Image.Image:
    size = 900
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    image.alpha_composite(radial_glow(size, CYAN, 62), (0, 0))
    whale = draw_whale(int(size * 0.92), icon=False)
    image.alpha_composite(whale, (int(size * 0.04), int(size * 0.02)))
    return image


def make_brand_panel() -> Image.Image:
    width, height = 164, 314
    panel = vertical_gradient((width, height), (4, 18, 32, 255), (8, 60, 94, 255))
    draw = ImageDraw.Draw(panel)

    # 海面光带和气泡。
    for y, alpha in ((54, 26), (76, 20), (250, 24), (276, 18)):
        draw.arc((-40, y - 34, width + 40, y + 34), 12, 168, fill=rgba(CYAN, alpha), width=2)
    for x, y, r in ((26, 70, 2), (138, 48, 3), (28, 118, 4), (146, 138, 2), (52, 176, 2), (120, 196, 3)):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=rgba(CYAN_LIGHT, 70), outline=rgba(CYAN_LIGHT, 100))

    # 顶部的心形光点。
    heart(
        panel,
        (int(width * 0.68), 20, int(width * 0.90), 42),
        CORAL,
        rgba(CORAL_LIGHT, 255),
        1,
    )

    whale = draw_whale(150, icon=False)
    panel.alpha_composite(whale, (7, 146))

    # 底部奶油色波浪。
    draw = ImageDraw.Draw(panel)
    draw.arc((-28, 276, width + 28, 350), 200, 340, fill=rgba(CREAM, 120), width=2)
    draw.arc((-42, 286, width + 42, 366), 200, 340, fill=rgba(CORAL, 90), width=2)
    return panel


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

    welcome = make_welcome()
    welcome.save(UI_ASSETS / "welcome-mascot.png", format="PNG", optimize=True)

    fish = draw_whale(512, icon=False)
    fish.save(UI_ASSETS / "dafeiyu-fish.png", format="PNG", optimize=True)
    fish.save(ROOT / "docs" / "assets" / "dafeiyu-fish.png", format="PNG", optimize=True)

    icon = draw_whale(256, icon=True)
    icon.save(UI_ASSETS / "dafeiyu-fish-icon.png", format="PNG", optimize=True)
    save_ico(icon, UI_ASSETS / "dafeiyu.ico")

    panel = make_brand_panel()
    panel.convert("RGB").save(INSTALLER_ASSETS / "brand-panel.bmp", format="BMP")

    print(f"welcome: {(UI_ASSETS / 'welcome-mascot.png').stat().st_size} bytes")
    print(f"icon: {(UI_ASSETS / 'dafeiyu.ico').stat().st_size} bytes")
    print(f"panel: {(INSTALLER_ASSETS / 'brand-panel.bmp').stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
