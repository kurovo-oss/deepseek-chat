# -*- coding: utf-8 -*-
"""兼容旧入口：重新生成大肥鱼图标与品牌资产。

真正的绘制逻辑在 scripts/make-brand-assets.py；这里只负责转调，
避免旧的 docs/assets/dafeiyu.png 把人像重新写回安装包图标。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("make-brand-assets.py")


def main() -> int:
    return subprocess.call([sys.executable, str(SCRIPT)])


if __name__ == "__main__":
    raise SystemExit(main())
