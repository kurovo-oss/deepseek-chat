"""对外写操作的闸门。

所有"真的动别人东西"的动作（发说说、点赞、分享、收表情）都先过这里：
开关、试运行、只认主人、每日/每小时额度、冷却、熔断，最后记一条审计。

原则：默认关；试运行只写审计不发请求；连续失败先停 30 分钟。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import NamedTuple

log = logging.getLogger("qqbot")

CIRCUIT_FAILS = 3
CIRCUIT_SECONDS = 1800
DAY_SECONDS = 86400

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  at             INTEGER NOT NULL,
  module         TEXT NOT NULL,
  action         TEXT NOT NULL DEFAULT '',
  target         TEXT NOT NULL DEFAULT '',
  payload_digest TEXT NOT NULL DEFAULT '',
  result         TEXT NOT NULL,
  error          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_module ON audit(module, at DESC);
"""


@dataclass(frozen=True)
class Capability:
    name: str
    enabled: bool
    dry_run: bool
    owner_only: bool = True
    daily_quota: int = 0
    hourly_quota: int = 0
    cooldown_seconds: int = 0


class Decision(NamedTuple):
    allowed: bool
    dry_run: bool
    reason: str = ""


def capabilities(settings) -> dict[str, Capability]:
    """从配置拼出每个能力的边界。"""
    def get(name, default=None):
        return getattr(settings, name, default)

    douyin_on = bool(get("douyin_enabled", False))
    return {
        "qzone": Capability(
            "qzone",
            bool(get("qzone_enabled", False)),
            bool(get("qzone_dry_run", True)),
            daily_quota=int(get("qzone_daily_max", 3) or 0),
            cooldown_seconds=1200,
        ),
        "douyin": Capability(
            "douyin",
            douyin_on,
            bool(get("douyin_dry_run", True)),
            daily_quota=int(get("douyin_daily_max", 5) or 0),
            cooldown_seconds=0,
        ),
        "douyin_share": Capability(
            "douyin_share",
            douyin_on and bool(get("douyin_share_enabled", True)),
            bool(get("douyin_dry_run", True)),
            daily_quota=int(get("douyin_share_daily_max", 2) or 0),
            cooldown_seconds=600,
        ),
        "sticker": Capability(
            "sticker",
            bool(get("sticker_collect_enabled", True)),
            False,
            daily_quota=int(get("sticker_daily_max", 100) or 0),
            hourly_quota=int(get("sticker_hourly_max", 20) or 0),
        ),
    }


class Guard:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.caps = capabilities(settings)
        self._ensure()

    def _ensure(self) -> None:
        if getattr(self.store, "_audit_ready", False):
            return
        with self.store._lock:  # noqa: SLF001
            self.store._conn.executescript(_SCHEMA)  # noqa: SLF001
            self.store._conn.commit()  # noqa: SLF001
        self.store._audit_ready = True

    # ---------- 查询 ----------

    def _rows(self, module: str, since: int) -> list:
        with self.store._lock:  # noqa: SLF001
            return self.store._conn.execute(  # noqa: SLF001
                "SELECT result, at FROM audit WHERE module = ? AND at >= ? ORDER BY id DESC",
                (module, since),
            ).fetchall()

    def _today(self, module: str, now: int) -> int:
        cap = self.caps.get(module)
        # 试运行记录只在她还处于试运行时占额度；一旦转真，昨天的试运行不该
        # 把今天的真操作卡住（真机上切换当天就撞到过这个）。
        counting = ("ok", "dry_run") if cap is not None and cap.dry_run else ("ok",)
        return sum(1 for row in self._rows(module, now - DAY_SECONDS) if row["result"] in counting)

    def _last_success(self, module: str) -> int:
        with self.store._lock:  # noqa: SLF001
            row = self.store._conn.execute(  # noqa: SLF001
                "SELECT at FROM audit WHERE module = ? AND result IN ('ok','dry_run') ORDER BY id DESC LIMIT 1",
                (module,),
            ).fetchone()
        return int(row["at"]) if row is not None else 0

    def circuit_open(self, module: str, now: int) -> bool:
        # 看“最近三次尝试”，不按时间窗滑：否则失败后过了 30 分钟窗口一滑，
        # 又会在协议端还没恢复时重试，变成每几分钟打一次接口。
        recent = self._rows(module, 0)
        if len(recent) < CIRCUIT_FAILS:
            return False
        tail = recent[:CIRCUIT_FAILS]
        if any(row["result"] in ("ok", "dry_run") for row in tail):
            return False
        return now - int(tail[0]["at"]) < CIRCUIT_SECONDS

    # ---------- 闸门 ----------

    def check(self, module: str, action: str = "", target: str = "", user_id: int | None = None,
              now: int | None = None) -> Decision:
        stamp = int(now if now is not None else time.time())
        cap = self.caps.get(module)
        if cap is None:
            return Decision(False, True, "unknown")
        if not cap.enabled:
            return Decision(False, cap.dry_run, "disabled")
        if cap.owner_only and user_id is not None and int(user_id) != int(self.settings.allow_user_id):
            return Decision(False, cap.dry_run, "owner_only")
        if self.circuit_open(module, stamp):
            return Decision(False, cap.dry_run, "circuit_open")
        if cap.cooldown_seconds and self._last_success(module) > stamp - cap.cooldown_seconds:
            return Decision(False, cap.dry_run, "cooldown")
        if cap.daily_quota and self._today(module, stamp) >= cap.daily_quota:
            return Decision(False, cap.dry_run, "quota")
        if cap.hourly_quota:
            used = sum(
                1 for row in self._rows(module, stamp - 3600) if row["result"] in ("ok", "dry_run")
            )
            if used >= cap.hourly_quota:
                return Decision(False, cap.dry_run, "hourly_quota")
        return Decision(True, cap.dry_run, "dry_run" if cap.dry_run else "ok")

    # ---------- 审计 ----------

    def record(self, module: str, action: str, target: str = "", payload: str = "",
               result: str = "ok", error: str = "", now: int | None = None) -> None:
        stamp = int(now if now is not None else time.time())
        digest = hashlib.sha256(str(payload).encode("utf-8", "replace")).hexdigest()[:16]
        with self.store._lock:  # noqa: SLF001
            self.store._conn.execute(  # noqa: SLF001
                "INSERT INTO audit (at, module, action, target, payload_digest, result, error)"
                " VALUES (?,?,?,?,?,?,?)",
                (stamp, module, action, target[:120], digest, result, error[:300]),
            )
            self.store._conn.commit()  # noqa: SLF001
        line = json.dumps(
            {"at": stamp, "module": module, "action": action, "target": target[:120],
             "result": result, "error": error[:120]},
            ensure_ascii=False,
        )
        log.info("audit %s", line)

    def recent(self, limit: int = 20) -> list[dict]:
        with self.store._lock:  # noqa: SLF001
            rows = self.store._conn.execute(  # noqa: SLF001
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def status(self, now: int | None = None) -> dict:
        stamp = int(now if now is not None else time.time())
        result = {}
        for name, cap in self.caps.items():
            result[name] = {
                "enabled": cap.enabled,
                "dry_run": cap.dry_run,
                "used_today": self._today(name, stamp),
                "daily_quota": cap.daily_quota,
                "circuit_open": self.circuit_open(name, stamp),
            }
        return result
