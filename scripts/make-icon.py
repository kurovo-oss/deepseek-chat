# -*- coding: utf-8 -*-
"""兼容旧入口：从网上二创的大肥鱼形象重新生成品牌资产。

真正的逻辑在 scripts/make-brand-assets.py；欢迎图、安装包和 exe 图标
都来自 qqbot/ui/assets/dafeiyu.png，不另画角色。
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
