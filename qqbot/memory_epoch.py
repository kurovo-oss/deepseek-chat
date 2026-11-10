"""记忆清理时间点。

清理旧记忆时，消息原文通常还留在库里；但那些旧对话不该继续被摘要、
抽取、召回。这里用一个本地时间戳文件划一条线：只有它之后的消息才参与
新的记忆生成。文件本身不含任何聊天内容，删掉它就恢复全部历史。
"""

from __future__ import annotations

import time
from pathlib import Path

FILE_NAME = "memory-clean-at"


def path_for(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / FILE_NAME


def read(base_dir: Path) -> int:
    try:
        text = path_for(base_dir).read_text(encoding="ascii").strip()
        return max(0, int(text))
    except (OSError, ValueError):
        return 0


def mark(base_dir: Path, now: int | None = None) -> int:
    stamp = int(now if now is not None else time.time())
    path = path_for(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(str(stamp), encoding="ascii")
    tmp.replace(path)
    return stamp


def after(created_at: int | None, base_dir: Path) -> bool:
    stamp = read(base_dir)
    return stamp == 0 or int(created_at or 0) >= stamp
