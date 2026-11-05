"""把梗鲸档案馆的文本元数据抓进本地梗库。

默认走 R2 索引（两个 JSON，2956 条一次入库）；--pages 走逐页详情，作为兜底。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qqbot import memepedia  # noqa: E402
from qqbot.config import load_settings  # noqa: E402
from qqbot.store import Store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="抓梗鲸梗库")
    parser.add_argument("--pages", action="store_true", help="逐页抓详情（慢，兜底用）")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=memepedia.TOTAL)
    args = parser.parse_args()

    settings = load_settings()
    store = Store(settings.db_path)
    before = memepedia.count(store)
    if args.pages:
        written = memepedia.ingest(
            store,
            start=args.start,
            end=args.end,
            progress=lambda done, total, number: print(f"进度 {done}/{total}（#{number}）", flush=True),
        )
    else:
        print("正在读索引…", flush=True)
        written = memepedia.ingest_index(store)
    print(f"新入库 {written} 条，库内共 {memepedia.count(store)} 条（之前 {before}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
