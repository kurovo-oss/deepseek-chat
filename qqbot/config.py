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
        elif not value.startswith(("\"", "'")):
            # 行内注释：`KEY=value  # 说明` —— 只认「空格 + #」，
            # 避免把密码里合法的 # 误当注释
            hash_pos = value.find(" #")
            if hash_pos != -1:
                value = value[:hash_pos].rstrip()
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


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{key} 必须是 true/false，当前为 {raw!r}")


def _choice(env: Mapping[str, str], key: str, default: str, allowed: set[str]) -> str:
    raw = env.get(key, "").strip().lower()
    if not raw:
        return default
    if raw not in allowed:
        raise ConfigError(f"{key} 只能是 {'/'.join(sorted(allowed))}，当前为 {raw!r}")
    return raw


@dataclass(frozen=True)
class Settings:
    allow_user_id: int
    model_api_key: str
    base_dir: Path
    db_path: Path
    system_prompt_file: Path
    model_id: str = "deepseek-flash"
    log_dir: Path = Path("logs")
    model_base_url: str = "https://api.deepseek.com"
    adapter_api_url: str = "http://127.0.0.1:3001"
    adapter_token: str = ""
    listen_host: str = "127.0.0.1"
    listen_port: int = 8080
    reply_max_chars: int = 300
    segment_delay_ms: int = 1500
    request_timeout: float = 60.0
    network_wait_seconds: float = 30.0
    history_max_turns: int = 20
    window_token_budget: int = 4000
    image_enabled: bool = True
    image_lib_dir: Path = Path("assets/seed-images")
    image_dir: Path = Path("data/images")
    image_collect_enabled: bool = True
    image_auto_tag: bool = True
    image_max_count: int = 500
    image_cooldown_messages: int = 10
    image_send_mode: str = "path"
    image_max_bytes: int = 5 * 1024 * 1024
    tag_prompt_file: Path = Path("prompts/tag.md")
    # ---- 长期记忆（M2）----
    memory_enabled: bool = True
    extract_enabled: bool = True
    decay_half_life_days: float = 30.0
    card_weight_threshold: float = 0.15
    card_inject_token_budget: int = 1500
    context_soft_token_budget: int = 8000
    context_hard_token_budget: int = 32000
    extract_prompt_file: Path = Path("prompts/extract.md")
    compact_prompt_file: Path = Path("prompts/compact.md")

    @property
    def window_char_budget(self) -> int:
        """把 token 预算折成字符预算（est: 1 字 ≈ 0.6 token）。"""
        return max(1, int(self.window_token_budget / 0.6))

    @property
    def card_inject_char_budget(self) -> int:
        return max(1, int(self.card_inject_token_budget / 0.6))

    @property
    def context_soft_char_budget(self) -> int:
        return max(1, int(self.context_soft_token_budget / 0.6))

    @property
    def context_hard_char_budget(self) -> int:
        return max(1, int(self.context_hard_token_budget / 0.6))

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

        # 旧的 WINDOW_TOKEN_BUDGET 作为软预算的兜底，保证老配置仍能跑
        soft_default = _int(env, "WINDOW_TOKEN_BUDGET", 8000)
        soft_budget = _int(env, "CONTEXT_SOFT_TOKEN_BUDGET", soft_default)
        hard_budget = _int(env, "CONTEXT_HARD_TOKEN_BUDGET", 32000)
        if hard_budget < soft_budget:
            raise ConfigError(
                f"CONTEXT_HARD_TOKEN_BUDGET({hard_budget}) 不能小于 "
                f"CONTEXT_SOFT_TOKEN_BUDGET({soft_budget})"
            )

        threshold = _float(env, "CARD_WEIGHT_THRESHOLD", 0.15)
        if not 0.0 <= threshold <= 1.0:
            raise ConfigError(f"CARD_WEIGHT_THRESHOLD 必须在 0~1 之间，当前为 {threshold}")

        half_life = _float(env, "DECAY_HALF_LIFE_DAYS", 30.0)
        if half_life <= 0:
            raise ConfigError(f"DECAY_HALF_LIFE_DAYS 必须大于 0，当前为 {half_life}")

        return cls(
            allow_user_id=allow_user_id,
            model_api_key=str(env["MODEL_API_KEY"]).strip(),
            base_dir=base_dir,
            db_path=base_dir / env.get("DB_PATH", "data/chat.db").strip(),
            system_prompt_file=base_dir
            / env.get("SYSTEM_PROMPT_FILE", "prompts/system.md").strip(),
            log_dir=base_dir / env.get("LOG_DIR", "logs").strip(),
            model_id=env.get("MODEL_ID", "deepseek-flash").strip(),
            model_base_url=env.get("MODEL_BASE_URL", "https://api.deepseek.com").strip(),
            adapter_api_url=env.get("ADAPTER_API", "http://127.0.0.1:3001").strip(),
            adapter_token=env.get("ADAPTER_TOKEN", "").strip(),
            listen_host=env.get("LISTEN_HOST", "127.0.0.1").strip(),
            listen_port=_int(env, "LISTEN_PORT", 8080),
            reply_max_chars=_int(env, "REPLY_MAX_CHARS", 300, minimum=40),
            segment_delay_ms=_int(env, "SEGMENT_DELAY_MS", 1500, minimum=0),
            request_timeout=_float(env, "REQUEST_TIMEOUT", 60.0),
            network_wait_seconds=_float(env, "NETWORK_WAIT_SECONDS", 30.0),
            history_max_turns=_int(env, "HISTORY_MAX_TURNS", 20),
            window_token_budget=_int(env, "WINDOW_TOKEN_BUDGET", 4000),
            image_enabled=_bool(env, "IMAGE_ENABLED", True),
            image_lib_dir=base_dir
            / env.get("IMAGE_LIB_DIR", "assets/seed-images").strip(),
            image_dir=base_dir / env.get("IMAGE_DIR", "data/images").strip(),
            image_collect_enabled=_bool(env, "IMAGE_COLLECT_ENABLED", True),
            image_auto_tag=_bool(env, "IMAGE_AUTO_TAG", True),
            image_max_count=_int(env, "IMAGE_MAX_COUNT", 500),
            tag_prompt_file=base_dir
            / env.get("TAG_PROMPT_FILE", "prompts/tag.md").strip(),
            image_cooldown_messages=_int(env, "IMAGE_COOLDOWN_MESSAGES", 10, minimum=0),
            image_send_mode=_choice(env, "IMAGE_SEND_MODE", "path", {"path", "base64"}),
            image_max_bytes=_int(env, "IMAGE_MAX_BYTES", 5 * 1024 * 1024),
            memory_enabled=_bool(env, "MEMORY_ENABLED", True),
            extract_enabled=_bool(env, "EXTRACT_ENABLED", True),
            decay_half_life_days=half_life,
            card_weight_threshold=threshold,
            card_inject_token_budget=_int(env, "CARD_INJECT_TOKEN_BUDGET", 1500),
            context_soft_token_budget=soft_budget,
            context_hard_token_budget=hard_budget,
            extract_prompt_file=base_dir
            / env.get("EXTRACT_PROMPT_FILE", "prompts/extract.md").strip(),
            compact_prompt_file=base_dir
            / env.get("COMPACT_PROMPT_FILE", "prompts/compact.md").strip(),
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


def load_optional_prompt(path: Path) -> str:
    """读可选的提示词文件；缺失或读不了都返回空串，由调用方决定是否启用。"""
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""
