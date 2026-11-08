"""把控制台用的鲸鱼娘头像做成 Windows 图标（.ico）。

只在构建启动器时用，运行时不需要。用法：
    tools\\python\\python.exe scripts\\make-icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "assets" / "dafeiyu.png"
OUT = ROOT / "qqbot" / "ui" / "assets" / "dafeiyu.ico"


def main() -> int:
    image = Image.open(SRC).convert("RGBA")
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    image.thumbnail((236, 236), Image.LANCZOS)
    canvas.alpha_composite(
        image,
        ((256 - image.width) // 2, (256 - image.height) // 2),
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
