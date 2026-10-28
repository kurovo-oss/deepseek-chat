"""配置加载与校验。

设计原则：
- 不依赖 python-dotenv，自己解析 .env（语法只需覆盖常见形式）
- 校验集中在 from_env，测试可直接传字典，不碰 os.environ
- 缺必填项时一次性报出全部缺失项，而不是报第一个就退出
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

# 提示词文件缺失时的兜底，保证程序永远能启动
DEFAULT_SYSTEM_PROMPT = (
    "你叫大肥鱼，18 岁，是对方的女儿。\n"
    "说话短、口语化，一次只说一两句，不讲大道理。\n"
    "整段回复控制在 300 字以内。"
)

REQUIRED_KEYS = ("ALLOW_USER_ID", "MODEL_API_KEY")


class ConfigError(ValueError):
    """配置缺失或非法。"""


def parse_dotenv(text: str) -> dict[str, str]:
    """解析 .env 文本，支持 `export K=V`、引号包裹、# 注释。"""
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return parse_dotenv(path.read_text(encoding="utf-8"))


def _int(env: Mapping[str, str], key: str, default: int, *, minimum: int = 1) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是整数，当前为 {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{key} 不能小于 {minimum}，当前为 {value}")
    return value


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是数字，当前为 {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    allow_user_id: int
    model_api_key: str
    base_dir: Path
    db_path: Path
    system_prompt_file: Path
    model_id: str = "deepseek-v4.1-flash1017"
    model_base_url: str = "https://api.deepseek.com"
    adapter_api_url: str = "http://127.0.0.1:3001"
    adapter_token: str = ""
    listen_host: str = "127.0.0.1"
    listen_port: int = 8080
    reply_max_chars: int = 300
    segment_delay_ms: int = 1500
    request_timeout: float = 60.0
    history_max_turns: int = 20
    window_token_budget: int = 4000

    @property
    def window_char_budget(self) -> int:
        """把 token 预算折成字符预算（est: 1 字 ≈ 0.6 token）。"""
        return max(1, int(self.window_token_budget / 0.6))

    @classmethod
    def from_env(cls, env: Mapping[str, str], base_dir: Path) -> "Settings":
        missing = [k for k in REQUIRED_KEYS if not str(env.get(k, "")).strip()]
        if missing:
            raise ConfigError("缺少必填配置: " + ", ".join(missing))

        raw_allow = str(env["ALLOW_USER_ID"]).strip()
        try:
            allow_user_id = int(raw_allow)
        except ValueError as exc:
            raise ConfigError(f"ALLOW_USER_ID 必须是 QQ 号（纯数字），当前为 {raw_allow!r}") from exc

        return cls(
            allow_user_id=allow_user_id,
            model_api_key=str(env["MODEL_API_KEY"]).strip(),
            base_dir=base_dir,
            db_path=base_dir / env.get("DB_PATH", "data/chat.db").strip(),
            system_prompt_file=base_dir
            / env.get("SYSTEM_PROMPT_FILE", "prompts/system.md").strip(),
            model_id=env.get("MODEL_ID", "deepseek-v4.1-flash1017").strip(),
            model_base_url=env.get("MODEL_BASE_URL", "https://api.deepseek.com").strip(),
            adapter_api_url=env.get("ADAPTER_API", "http://127.0.0.1:3001").strip(),
            adapter_token=env.get("ADAPTER_TOKEN", "").strip(),
            listen_host=env.get("LISTEN_HOST", "127.0.0.1").strip(),
            listen_port=_int(env, "LISTEN_PORT", 8080),
            reply_max_chars=_int(env, "REPLY_MAX_CHARS", 300, minimum=40),
            segment_delay_ms=_int(env, "SEGMENT_DELAY_MS", 1500, minimum=0),
            request_timeout=_float(env, "REQUEST_TIMEOUT", 60.0),
            history_max_turns=_int(env, "HISTORY_MAX_TURNS", 20),
            window_token_budget=_int(env, "WINDOW_TOKEN_BUDGET", 4000),
        )


def load_settings(
    base_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    dotenv_name: str = ".env",
) -> Settings:
    """合并 .env 与进程环境变量（后者优先），构造并校验配置。"""
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    merged: dict[str, str] = dict(load_dotenv(base / dotenv_name))
    source = os.environ if env is None else env
    merged.update({k: v for k, v in source.items() if v is not None})
    return Settings.from_env(merged, base)


def load_system_prompt(settings: Settings) -> str:
    path = settings.system_prompt_file
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return DEFAULT_SYSTEM_PROMPT
