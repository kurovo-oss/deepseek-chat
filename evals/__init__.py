"""对话质量评测：`python -m evals.run`。"""

from .cases import CASES, Case
from .harness import CaseResult, run_case

__all__ = ["CASES", "Case", "CaseResult", "run_case"]
