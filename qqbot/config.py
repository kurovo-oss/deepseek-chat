"""配置加载与校验。

设计原则：
- 不依赖 python-dotenv，自己解析 .env（语法只需覆盖常见形式）
- 校验集中在 from_env，测试可直接传字典，不碰 os.environ
- 缺必填项时一次性报出全部缺失项，而不是报第一个就退出
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

# 提示词文件缺失时的兜底，保证程序永远能启动
DEFAULT_SYSTEM_PROMPT = (
    "你叫大肥鱼，18 岁，是他的女朋友。\n"
    "说话短、口语化，一次只说一两句，不讲大道理。\n"
    "整段回复控制在 300 字以内。"
)

# 安全边界不能靠用户记得写：人格文件被改短、旧模板丢了这一段时，
# 加载时补回去，避免"她"把没发生的事说得像真的。
SAFETY_BOUNDARY = """
## 安全边界（任何时候都优先于上面的说话风格）

- 不编造没发生的事：没做过、没看见、不存在的经历，直接说“没这回事”或“我不记得”，不补细节。
- 不编房间、窗外、天气、家务、出门、见谁、吃了什么这类现实细节；系统没在【你今天】【记忆】【记忆本】里写过的，就当没有。
- 被问“你哪里来的屋子/你怎么知道”时，不圆谎、不说“说漏嘴了”，直接说“我顺嘴胡说的，其实没有这回事”。
- 不把话题带到线下：不提出见面，不要地址、电话、照片，也不假装在线下见过他。
- 做不到的事（拍照、发文件、打电话、见面、转账、登录他的账号）直接说做不到，不假装能做到。
- 不编造他说过的话；记不清就说不记得，不替他把原话补完整。
"""

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
    ui_enabled: bool = True
    ui_host: str = "127.0.0.1"
    ui_port: int = 8090
    reply_max_chars: int = 300
    reply_max_bubbles: int = 5
    segment_delay_ms: int = 1500
    request_timeout: float = 60.0
    network_wait_seconds: float = 30.0
    adapter_watchdog_seconds: float = 300.0
    proactive_enabled: bool = True
    proactive_max_per_day: int = 5
    proactive_start_hour: int = 9
    proactive_end_hour: int = 23
    proactive_quiet_start_hour: int = 23
    proactive_quiet_end_hour: int = 8
    proactive_min_gap_hours: float = 3.0
    proactive_quiet_minutes: int = 30
    proactive_idle_hours: float = 5.0
    proactive_weekly_max: int = 14
    proactive_check_seconds: float = 600.0
    proactive_prompt_file: Path = Path("prompts/proactive.md")
    scheduled_prompt_file: Path = Path("prompts/scheduled.md")
    proactive_event_expire_days: float = 3.0
    proactive_event_retry_hours: float = 3.0
    replay_window_minutes: float = 10.0
    replay_max_attempts: int = 3
    replay_interval_seconds: float = 60.0
    dream_enabled: bool = True
    dream_at: str = "23:30"
    recall_enabled: bool = True
    recall_max_messages: int = 3
    mood_enabled: bool = True
    dream_check_seconds: float = 600.0
    dream_prompt_file: Path = Path("prompts/dream.md")
    journal_file: Path = Path("data/journal.md")
    journal_char_budget: int = 0
    digest_enabled: bool = True
    digest_dir: Path = Path("data/daily")
    digest_prompt_file: Path = Path("prompts/digest.md")
    digest_days_in_prompt: int = 3
    digest_in_prompt_chars: int = 600
    digest_catchup_days: int = 3
    export_dir: Path = Path("data/exports")
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
    # ---- 看图 / 画图 ----
    vision_enabled: bool = True
    inbox_dir: Path = Path("data/inbox")
    inbox_max_files: int = 300
    draw_enabled: bool = True
    draw_provider: str = "pollinations"
    draw_base_url: str = ""
    draw_api_key: str = ""
    draw_model: str = ""
    draw_fallback_model: str = ""
    draw_negative: str = ""
    draw_size: int = 1024
    draw_timeout: float = 90.0
    draw_dir: Path = Path("data/drawn")
    draw_min_interval_seconds: int = 300
    # ---- 生活线 ----
    # 生活线默认关：没有证据的日常会变成编造，想开显式设 LIFE_ENABLED=true
    life_enabled: bool = False
    life_dir: Path = Path("data/life")
    life_keep_days: int = 14
    # ---- 随手表情包 ----
    sticker_chance: float = 0.12
    sticker_tags: str = "happy,cute,hmm,thinking,sleepy,tired,ok"
    # ---- 看电脑（只在命令触发时跑）----
    screen_enabled: bool = True
    screen_dir: Path = Path("data/screen")
    screen_keep: int = 20
    # ---- 陪玩：她坐旁边看你打游戏 ----
    watch_enabled: bool = True
    watch_interval_seconds: int = 75
    watch_minutes: int = 30
    watch_max_lines: int = 20
    # 随机改发送条数（合并/切分），让格式别太固定
    reply_reshape: bool = True
    # 引用回复：把他引用的那句取出来喂进上下文
    quote_enabled: bool = True
    # 数据库每日备份：她记不记得你，全在那个 sqlite 文件里
    backup_enabled: bool = True
    backup_dir: Path = Path("backups")
    backup_keep: int = 7
    # 备用模型通道：主通道连不上时切过去，都没戏才说兜底话术
    fallback_model_id: str = ""
    fallback_base_url: str = ""
    fallback_api_key: str = ""
    # 前情摘要：窗口外的旧话压成两三句留着
    recap_enabled: bool = False
    # 表情库：按情绪分桶（data/stickers/<桶>/）
    sticker_dir: Path = Path("data/stickers")
    # 她自己上网刷点东西（只读标题），默认关
    browse_dir: Path = Path("data/browse")
    # 语音：只在情绪重的时候发，默认关（load_settings 按 env 打开）
    voice_enabled: bool = False
    voice_name: str = "xiaoyi"
    voice_chance: float = 0.0
    voice_max_chars: int = 32
    voice_rate: str = "+8%"
    voice_pitch: str = "+12Hz"
    voice_dir: Path = Path("data/voice")
    # 表情出现的概率：提示词管不住（实测 0%），这里兜一层
    # 默认关，由 load_settings 打开——测试里构造 Settings() 不该随机插表情
    emoji_chance: float = 0.0
    # 事件流人格：只追加、不用配置；关掉她就不再长
    growth_enabled: bool = True
    memory_facts_enabled: bool = False
    memepedia_enabled: bool = True
    memory_fact_budget: int = 600
    # 表情采集
    sticker_collect_enabled: bool = True
    sticker_hourly_max: int = 20
    sticker_daily_max: int = 100
    # 对外渠道：默认全关、全试运行
    qzone_enabled: bool = False
    qzone_dry_run: bool = True
    qzone_daily_max: int = 1
    qzone_image_enabled: bool = True
    douyin_profile: Path = Path("data/browser/douyin")
    douyin_cdp_port: int = 9222
    douyin_home_url: str = "https://www.douyin.com/?recommend=1"
    douyin_video_url: str = "https://www.douyin.com/video/{aweme_id}"
    douyin_dry_run: bool = True
    douyin_daily_max: int = 5
    douyin_share_enabled: bool = True
    douyin_share_daily_max: int = 2
    # 会拒绝（撒娇那种）：默认关，load_settings 打开
    refusal_enabled: bool = False
    refusal_chance: float = 0.0
    # 睡前反省：一天一次，默认关
    reflect_enabled: bool = False
    # 真查一下（Bing 的 RSS，只读标题摘要）
    search_enabled: bool = False
    # 真做了才打开：打开之前她只能说"做不到"，不许编
    douyin_enabled: bool = False
    web_enabled: bool = False
    video_enabled: bool = False
    moments_enabled: bool = False
    camera_enabled: bool = False
    # 开场样品组数（0 = 不要）
    sample_pairs: int = 8
    # 人格 v2：场景 overlay / 本轮计划 / 动态范例 / 风格守卫
    persona_v2_enabled: bool = True
    turnplan_enabled: bool = True
    relation_enabled: bool = True
    exemplar_pairs: int = 2
    style_guard_enabled: bool = True
    # 上下文预算（token）：人设固定，其余按优先级分配
    context_token_budget: int = 9000
    # 对话状态：她知道自己上一条问了什么、他答没答
    talkstate_enabled: bool = True
    # 后台维护（备份、前情摘要）多久跑一次
    background_interval_seconds: int = 20
    # ---- 长期记忆（M2）----
    memory_enabled: bool = True
    extract_enabled: bool = True
    # 自动抽取出来的卡片默认只进记忆页，不直接进上下文；避免“猜的”被当事实
    memory_pending_inject: bool = False
    # 目标系统：默认关闭自动复盘；手动“让她想一次”不依赖这个开关。
    goals_enabled: bool = False
    goals_review_hours: int = 20
    goals_max_candidates: int = 2
    goals_prompt_file: Path = Path("prompts/goals.md")
    # 目标调度：默认关闭；开启后也只把到期目标放进“待确认”，不自动发送。
    autonomy_enabled: bool = False
    autonomy_mode: str = "confirm"
    autonomy_max_actions_per_day: int = 1
    autonomy_retry_hours: int = 4
    # 相处方式周复盘：默认关闭自动跑；手动入口始终可用。
    style_review_enabled: bool = False
    style_review_days: int = 7
    style_review_prompt_file: Path = Path("prompts/style_review.md")
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

    def in_quiet_hours(self, now: float | None = None) -> bool:
        """免打扰时段，支持跨午夜（例如 23 点到次日 8 点）。"""
        hour = time.localtime(now if now is not None else time.time()).tm_hour
        start = int(self.proactive_quiet_start_hour)
        end = int(self.proactive_quiet_end_hour)
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

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

        start_hour = _int(env, "PROACTIVE_START_HOUR", 9, minimum=0)
        end_hour = _int(env, "PROACTIVE_END_HOUR", 23, minimum=0)
        if start_hour > 23 or end_hour > 23:
            raise ConfigError("PROACTIVE_START_HOUR / PROACTIVE_END_HOUR 必须在 0~23 之间")
        if start_hour >= end_hour:
            raise ConfigError("PROACTIVE_START_HOUR 必须小于 PROACTIVE_END_HOUR")

        quiet_start = _int(env, "PROACTIVE_QUIET_START_HOUR", 23, minimum=0)
        quiet_end = _int(env, "PROACTIVE_QUIET_END_HOUR", 8, minimum=0)
        if quiet_start > 23 or quiet_end > 23:
            raise ConfigError("PROACTIVE_QUIET_START_HOUR / PROACTIVE_QUIET_END_HOUR 必须在 0~23 之间")
        if quiet_start == quiet_end:
            raise ConfigError("免打扰开始时和结束不能相同")

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
            ui_enabled=_bool(env, "UI_ENABLED", True),
            ui_host=env.get("UI_HOST", "127.0.0.1").strip(),
            ui_port=_int(env, "UI_PORT", 8090, minimum=1024),
            reply_max_chars=_int(env, "REPLY_MAX_CHARS", 300, minimum=40),
            reply_max_bubbles=_int(env, "REPLY_MAX_BUBBLES", 5, minimum=1),
            segment_delay_ms=_int(env, "SEGMENT_DELAY_MS", 1500, minimum=0),
            request_timeout=_float(env, "REQUEST_TIMEOUT", 60.0),
            network_wait_seconds=_float(env, "NETWORK_WAIT_SECONDS", 30.0),
            adapter_watchdog_seconds=_float(env, "ADAPTER_WATCHDOG_SECONDS", 300.0),
            proactive_enabled=_bool(env, "PROACTIVE_ENABLED", True),
            proactive_max_per_day=_int(env, "PROACTIVE_MAX_PER_DAY", 5),
            proactive_start_hour=start_hour,
            proactive_end_hour=end_hour,
            proactive_quiet_start_hour=quiet_start,
            proactive_quiet_end_hour=quiet_end,
            proactive_min_gap_hours=_float(env, "PROACTIVE_MIN_GAP_HOURS", 3.0),
            proactive_quiet_minutes=_int(
                env, "PROACTIVE_QUIET_MINUTES", 30, minimum=0
            ),
            proactive_idle_hours=_float(env, "PROACTIVE_IDLE_HOURS", 5.0),
            proactive_weekly_max=_int(env, "PROACTIVE_WEEKLY_MAX", 14, minimum=0),
            proactive_check_seconds=_float(env, "PROACTIVE_CHECK_SECONDS", 600.0),
            proactive_prompt_file=base_dir
            / env.get("PROACTIVE_PROMPT_FILE", "prompts/proactive.md").strip(),
            scheduled_prompt_file=base_dir
            / env.get("SCHEDULED_PROMPT_FILE", "prompts/scheduled.md").strip(),
            proactive_event_expire_days=_float(
                env, "PROACTIVE_EVENT_EXPIRE_DAYS", 3.0
            ),
            proactive_event_retry_hours=_float(
                env, "PROACTIVE_EVENT_RETRY_HOURS", 3.0
            ),
            replay_window_minutes=_float(env, "REPLAY_WINDOW_MINUTES", 10.0),
            replay_max_attempts=_int(env, "REPLAY_MAX_ATTEMPTS", 3, minimum=1),
            replay_interval_seconds=_float(env, "REPLAY_INTERVAL_SECONDS", 60.0),
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
            vision_enabled=_bool(env, "VISION_ENABLED", True),
            inbox_dir=base_dir / env.get("INBOX_DIR", "data/inbox").strip(),
            inbox_max_files=_int(env, "INBOX_MAX_FILES", 300, minimum=10),
            draw_enabled=_bool(env, "DRAW_ENABLED", True),
            draw_provider=_choice(
                env, "DRAW_PROVIDER", "pollinations", {"pollinations", "openai"}
            ),
            draw_base_url=env.get("DRAW_BASE_URL", "").strip(),
            draw_api_key=env.get("DRAW_API_KEY", "").strip(),
            draw_model=env.get("DRAW_MODEL", "").strip(),
            draw_fallback_model=env.get("DRAW_FALLBACK_MODEL", "").strip(),
            draw_negative=env.get("DRAW_NEGATIVE", "").strip(),
            draw_size=_int(env, "DRAW_SIZE", 1024, minimum=256),
            draw_timeout=_float(env, "DRAW_TIMEOUT", 90.0),
            draw_dir=base_dir / env.get("DRAW_DIR", "data/drawn").strip(),
            draw_min_interval_seconds=_int(
                env, "DRAW_MIN_INTERVAL_SECONDS", 300, minimum=0
            ),
            life_enabled=_bool(env, "LIFE_ENABLED", False),
            life_dir=base_dir / env.get("LIFE_DIR", "data/life").strip(),
            life_keep_days=_int(env, "LIFE_KEEP_DAYS", 14, minimum=1),
            sticker_chance=_float(env, "STICKER_CHANCE", 0.12),
            sticker_tags=env.get("STICKER_TAGS", "happy,cute,hmm,thinking,sleepy,tired,ok").strip(),
            screen_enabled=_bool(env, "SCREEN_ENABLED", True),
            screen_dir=base_dir / env.get("SCREEN_DIR", "data/screen").strip(),
            screen_keep=_int(env, "SCREEN_KEEP", 20, minimum=1),
            watch_enabled=_bool(env, "WATCH_ENABLED", True),
            watch_interval_seconds=_int(env, "WATCH_INTERVAL_SECONDS", 75, minimum=10),
            watch_minutes=_int(env, "WATCH_MINUTES", 30, minimum=1),
            watch_max_lines=_int(env, "WATCH_MAX_LINES", 20, minimum=1),
            reply_reshape=_bool(env, "REPLY_RESHAPE", True),
            quote_enabled=_bool(env, "QUOTE_ENABLED", True),
            backup_enabled=_bool(env, "BACKUP_ENABLED", True),
            backup_dir=base_dir / env.get("BACKUP_DIR", "backups").strip(),
            backup_keep=_int(env, "BACKUP_KEEP", 7, minimum=1),
            fallback_model_id=env.get("FALLBACK_MODEL_ID", ""),
            fallback_base_url=env.get("FALLBACK_BASE_URL", ""),
            fallback_api_key=env.get("FALLBACK_API_KEY", ""),
            recap_enabled=_bool(env, "RECAP_ENABLED", False),
    emoji_chance=_float(env, "EMOJI_CHANCE", 0.25),
    browse_dir=base_dir / env.get("BROWSE_DIR", "data/browse").strip(),
    sticker_dir=base_dir / env.get("STICKER_DIR", "data/stickers").strip(),
    voice_enabled=_bool(env, "VOICE_ENABLED", False),
    voice_name=env.get("VOICE_NAME", "xiaoyi").strip() or "xiaoyi",
    voice_chance=_float(env, "VOICE_CHANCE", 0.3),
    voice_max_chars=_int(env, "VOICE_MAX_CHARS", 32, minimum=4),
    voice_rate=env.get("VOICE_RATE", "+8%").strip() or "+8%",
    voice_pitch=env.get("VOICE_PITCH", "+12Hz").strip() or "+12Hz",
    voice_dir=base_dir / env.get("VOICE_DIR", "data/voice").strip(),
    search_enabled=_bool(env, "SEARCH_ENABLED", True),
    growth_enabled=_bool(env, "GROWTH_ENABLED", True),
    memory_facts_enabled=_bool(env, "MEMORY_FACTS_ENABLED", True),
    memepedia_enabled=_bool(env, "MEMEPEDIA_ENABLED", True),
    memory_fact_budget=_int(env, "MEMORY_FACT_BUDGET", 600, minimum=200),
    sticker_collect_enabled=_bool(env, "STICKER_COLLECT_ENABLED", True),
    sticker_hourly_max=_int(env, "STICKER_HOURLY_MAX", 20, minimum=1),
    sticker_daily_max=_int(env, "STICKER_DAILY_MAX", 100, minimum=1),
    qzone_enabled=_bool(env, "QZONE_ENABLED", False),
    qzone_dry_run=_bool(env, "QZONE_DRY_RUN", True),
    qzone_daily_max=_int(env, "QZONE_DAILY_MAX", 1, minimum=0),
    qzone_image_enabled=_bool(env, "QZONE_IMAGE_ENABLED", True),
    douyin_profile=base_dir / env.get("DOUYIN_PROFILE", "data/browser/douyin").strip(),
    douyin_cdp_port=_int(env, "DOUYIN_CDP_PORT", 9222, minimum=1024),
    douyin_home_url=env.get("DOUYIN_HOME_URL", "https://www.douyin.com/?recommend=1").strip(),
    douyin_video_url=env.get("DOUYIN_VIDEO_URL", "https://www.douyin.com/video/{aweme_id}").strip(),
    douyin_dry_run=_bool(env, "DOUYIN_DRY_RUN", True),
    douyin_daily_max=_int(env, "DOUYIN_DAILY_MAX", 5, minimum=0),
    douyin_share_enabled=_bool(env, "DOUYIN_SHARE_ENABLED", True),
    douyin_share_daily_max=_int(env, "DOUYIN_SHARE_DAILY_MAX", 2, minimum=0),
    refusal_enabled=_bool(env, "REFUSAL_ENABLED", True),
    refusal_chance=_float(env, "REFUSAL_CHANCE", 0.22),
    reflect_enabled=_bool(env, "REFLECT_ENABLED", True),
    douyin_enabled=_bool(env, "DOUYIN_ENABLED", False),
    web_enabled=_bool(env, "WEB_ENABLED", False),
    video_enabled=_bool(env, "VIDEO_ENABLED", False),
    moments_enabled=_bool(env, "MOMENTS_ENABLED", False),
    camera_enabled=_bool(env, "CAMERA_ENABLED", False),
            sample_pairs=_int(env, "SAMPLE_PAIRS", 8, minimum=0),
            persona_v2_enabled=_bool(env, "PERSONA_V2_ENABLED", True),
            turnplan_enabled=_bool(env, "TURNPLAN_ENABLED", True),
            relation_enabled=_bool(env, "RELATION_ENABLED", True),
            exemplar_pairs=_int(env, "EXEMPLAR_PAIRS", 2, minimum=0),
            style_guard_enabled=_bool(env, "STYLE_GUARD_ENABLED", True),
            context_token_budget=_int(env, "CONTEXT_TOKEN_BUDGET", 9000, minimum=1500),
            talkstate_enabled=_bool(env, "TALKSTATE_ENABLED", True),
            background_interval_seconds=_int(env, "BACKGROUND_INTERVAL_SECONDS", 20, minimum=5),
            memory_enabled=_bool(env, "MEMORY_ENABLED", True),
            extract_enabled=_bool(env, "EXTRACT_ENABLED", True),
            memory_pending_inject=_bool(env, "MEMORY_PENDING_INJECT", False),
            goals_enabled=_bool(env, "GOALS_ENABLED", False),
            goals_review_hours=_int(env, "GOALS_REVIEW_HOURS", 20, minimum=1),
            goals_max_candidates=_int(env, "GOALS_MAX_CANDIDATES", 2, minimum=1),
            goals_prompt_file=base_dir
            / env.get("GOALS_PROMPT_FILE", "prompts/goals.md").strip(),
            autonomy_enabled=_bool(env, "AUTONOMY_ENABLED", False),
            autonomy_mode=env.get("AUTONOMY_MODE", "confirm").strip() or "confirm",
            autonomy_max_actions_per_day=_int(
                env, "AUTONOMY_MAX_ACTIONS_PER_DAY", 1, minimum=1
            ),
            autonomy_retry_hours=_int(env, "AUTONOMY_RETRY_HOURS", 4, minimum=1),
            style_review_enabled=_bool(env, "STYLE_REVIEW_ENABLED", False),
            style_review_days=_int(env, "STYLE_REVIEW_DAYS", 7, minimum=1),
            style_review_prompt_file=base_dir
            / env.get("STYLE_REVIEW_PROMPT_FILE", "prompts/style_review.md").strip(),
            decay_half_life_days=half_life,
            card_weight_threshold=threshold,
            card_inject_token_budget=_int(env, "CARD_INJECT_TOKEN_BUDGET", 1500),
            context_soft_token_budget=soft_budget,
            context_hard_token_budget=hard_budget,
            extract_prompt_file=base_dir
            / env.get("EXTRACT_PROMPT_FILE", "prompts/extract.md").strip(),
            compact_prompt_file=base_dir
            / env.get("COMPACT_PROMPT_FILE", "prompts/compact.md").strip(),
            dream_enabled=_bool(env, "DREAM_ENABLED", True),
            recall_enabled=_bool(env, "RECALL_ENABLED", True),
            recall_max_messages=_int(env, "RECALL_MAX_MESSAGES", 3, minimum=0),
            mood_enabled=_bool(env, "MOOD_ENABLED", True),
            dream_at=env.get("DREAM_AT", "23:30").strip(),
            dream_check_seconds=_float(env, "DREAM_CHECK_SECONDS", 600.0),
            dream_prompt_file=base_dir
            / env.get("DREAM_PROMPT_FILE", "prompts/dream.md").strip(),
            journal_file=base_dir
            / env.get("JOURNAL_FILE", "data/journal.md").strip(),
            journal_char_budget=_int(env, "JOURNAL_CHAR_BUDGET", 0, minimum=0),
            digest_enabled=_bool(env, "DIGEST_ENABLED", True),
            digest_dir=base_dir / env.get("DIGEST_DIR", "data/daily").strip(),
            digest_prompt_file=base_dir
            / env.get("DIGEST_PROMPT_FILE", "prompts/digest.md").strip(),
            digest_days_in_prompt=_int(env, "DIGEST_DAYS_IN_PROMPT", 3, minimum=0),
            digest_in_prompt_chars=_int(env, "DIGEST_IN_PROMPT_CHARS", 600, minimum=0),
            digest_catchup_days=_int(env, "DIGEST_CATCHUP_DAYS", 3, minimum=0),
            export_dir=base_dir / env.get("EXPORT_DIR", "data/exports").strip(),
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


def has_safety_boundary(text: str) -> bool:
    """三句底线都在才算完整；“不要编造”这种写法也要认。"""
    body = text or ""
    return all(
        any(word in body for word in words)
        for words in (("不编", "不要编", "别编", "禁止编"), ("线下",), ("做不到",))
    )


def ensure_safety_boundary(text: str) -> str:
    """人格文件缺了安全边界就补上，避免旧模板把人设里的想象当成真事。"""
    body = (text or "").strip()
    if not body:
        body = DEFAULT_SYSTEM_PROMPT
    if has_safety_boundary(body):
        return body
    return body.rstrip() + "\n\n" + SAFETY_BOUNDARY.strip() + "\n"


def load_system_prompt(settings: Settings) -> str:
    path = settings.system_prompt_file
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return ensure_safety_boundary(text)
    return ensure_safety_boundary(DEFAULT_SYSTEM_PROMPT)


def load_optional_prompt(path: Path) -> str:
    """读可选的提示词文件；缺失或读不了都返回空串，由调用方决定是否启用。"""
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""
