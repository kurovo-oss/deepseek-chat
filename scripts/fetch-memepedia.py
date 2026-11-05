"""把梗鲸档案馆抓进本地梗库（断点续传，限速 0.7s/条）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qqbot import memepedia  # noqa: E402
from qqbot.config import load_settings  # noqa: E402
from qqbot.store import Store  # noqa: E402


def main() -> int:
    settings = load_settings()
    store = Store(settings.db_path)
    before = memepedia.count(store)

    def progress(done: int, total: int, number: int) -> None:
        if done % 50 == 0 or done == total:
            print(f"进度 {done}/{total}（#{number}）", flush=True)

    written = memepedia.ingest(store, progress=progress)
    print(f"新入库 {written} 条，库内共 {memepedia.count(store)} 条（之前 {before}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())