"""命令行入口：python -m evals.run

真模型跑，所以默认不返回失败退出码（模型有随机性），要当门禁用加 --strict。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from qqbot.config import load_settings
from qqbot.llm import LLMClient

from .cases import CASES
from .harness import CaseResult, run_case

ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict[str, dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"读不了对比基线 {path}: {exc}")
        return {}
    return {item["name"]: item for item in data.get("cases", [])}


def _compare(current: list[CaseResult], baseline: dict[str, dict]) -> list[str]:
    lines: list[str] = []
    now = {result.case.name: result.ok for result in current}
    for name, was_ok in ((item["name"], item["ok"]) for item in baseline.values()):
        if name not in now:
            continue
        if was_ok and not now[name]:
            lines.append(f"退化：{name}（原来过，现在不过）")
        elif not was_ok and now[name]:
            lines.append(f"修好了：{name}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.run", description="对话质量评测")
    parser.add_argument("--only", default="", help="只跑名字里包含这个词的场景")
    parser.add_argument("--save", default="", help="结果存成 JSON，下次用 --compare")
    parser.add_argument("--compare", default="", help="和旧的 JSON 结果对比")
    parser.add_argument("--strict", action="store_true", help="有失败就返回非零退出码")
    parser.add_argument(
        "--repeat", type=int, default=2, help="每个场景最多试几次（模型有随机性，默认 2）"
    )
    args = parser.parse_args(argv)

    selected = [case for case in CASES if args.only in case.name]
    if not selected:
        print(f"没有匹配 --only {args.only!r} 的场景")
        return 2

    base = load_settings(ROOT)
    llm = LLMClient(
        base.model_base_url, base.model_api_key, base.model_id, timeout=base.request_timeout
    )

    results: list[CaseResult] = []
    with tempfile.TemporaryDirectory() as tmp:
        for case in selected:
            attempts = max(1, args.repeat)
            for attempt in range(1, attempts + 1):
                result = run_case(case, base, llm, Path(tmp))
                if result.ok or attempt == attempts:
                    break
            results.append(result)
            print(("✓ " if result.ok else "✗ ") + case.name)
            for reply in result.text.splitlines():
                print(f"    她：{reply}")
            for failure in result.failures:
                print(f"    ! {failure}")

    passed = sum(1 for result in results if result.ok)
    print(f"\n{passed}/{len(results)} 通过")

    if args.compare:
        baseline = _load(args.compare)
        for line in _compare(results, baseline):
            print(line)

    if args.save:
        payload = {
            "passed": passed,
            "total": len(results),
            "cases": [result.as_dict() for result in results],
        }
        Path(args.save).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已写入 {args.save}")

    return 1 if (args.strict and passed < len(results)) else 0


if __name__ == "__main__":
    sys.exit(main())
