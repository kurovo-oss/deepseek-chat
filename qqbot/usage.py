"""用 API 返回的真实 token 数，校准我们自己的估算。

`budget.estimate_tokens` 是启发式（中日韩 1 字≈1 token），跟具体模型的 tokenizer
总有偏差。偏差方向一致的话，预算就会系统性偏松或偏紧——偏松会顶穿上下文，
偏紧会白白浪费额度。

这里把每次调用"我估了多少 / 接口报了多少"记下来，取比值的中位数，
预算按这个比例收紧。样本不够就用 1.0，不做猜测。
"""

from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path
from typing import Any

log = logging.getLogger("qqbot")

SAMPLE_LIMIT = 40
RATIO_MIN = 0.6
RATIO_MAX = 2.0


def _path(settings: Any) -> Path:
    return Path(settings.base_dir) / "data" / "token_ratio.json"


def samples(settings: Any) -> list[float]:
    try:
        raw = json.loads(_path(settings).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    values: list[float] = []
    for item in raw:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.append(value)
    return values[-SAMPLE_LIMIT:]


def _save(settings: Any, values: list[float]) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values[-SAMPLE_LIMIT:]), encoding="utf-8")


def ratio(settings: Any) -> float:
    """真实 token / 估算 token 的中位数。样本少于 3 个就当 1.0。"""
    values = samples(settings)
    if len(values) < 3:
        return 1.0
    value = statistics.median(values)
    return max(RATIO_MIN, min(RATIO_MAX, value))


def record(settings: Any, estimated: int, actual: int) -> float:
    """记一次样本。actual<=0（接口没回 usage）就跳过。"""
    if estimated <= 0 or actual <= 0:
        return ratio(settings)
    value = actual / estimated
    if not (0.1 <= value <= 10):
        return ratio(settings)
    values = samples(settings)
    values.append(round(value, 4))
    _save(settings, values)
    return ratio(settings)


def describe(settings: Any) -> str:
    """/status 用的一句话。"""
    value = ratio(settings)
    count = len(samples(settings))
    if count < 3:
        return "估算（样本还少）"
    return f"估算 ×{value:.2f}（{count} 次样本）"
