"""会拒绝，但是撒娇那种。

fish-river 里的"拒绝与疤"：拒绝不是设定，是这会儿真的不想；拒绝完会记着，
看他反应——他没不高兴，她慢慢知道拒绝一下也没关系；他不高兴，就记到小本本上。

这里刻意不写成"怼回去"：台词是软下来的那种，不凶、不讲道理。
"""

from __future__ import annotations

import re
import time

from . import growth

# 他让她做事时的口吻。宁可漏判，也别把"你吃饭了吗"当成差事
REQUEST_WORDS = (
    "帮我",
    "给我",
    "替我",
    "你来",
    "你去",
    "写个",
    "写一下",
    "做个",
    "做一下",
    "查一下",
    "查查",
    "找一下",
    "画一张",
    "画个",
    "发个",
    "发一张",
    "整理",
    "改一下",
    "翻译",
    "算一下",
    "弄个",
    "出个",
)

# 他自己就不好受的时候，绝不拒绝——那时候她反而会硬着头皮上
DOWN_WORDS = ("难受", "不舒服", "烦死", "崩溃", "累死", "好累", "头疼", "想哭", "委屈")

# 注意："嗯""哦"不在这里。它们是没表态，不是不高兴——之前当 rejected 用，
# 结果他随口一个"嗯"就被记成"他不高兴"，小本本全脏了。
REJECTED_WORDS = (
    "不高兴",
    "生气",
    "失望",
    "算了",
    "不用了",
    "无语",
    "我自己来",
    "行吧",
    "随便",
    "烦",
    "不行",
    "切",
)
ACCEPTED_WORDS = (
    "没生气",
    "不生气",
    "不怪你",
    "没怪你",
    "没事",
    "没关系",
    "不烦",
    "没不高兴",
    "哈哈",
    "好呀",
    "好吧",
    "抱抱",
    "可爱",
    "嘿嘿",
    "那你休息",
    "不急",
    "明天再说",
)
# 整句只有这些字：算同意了，但没什么情绪
YES_ONLY = ("行", "好", "行啊", "好啊", "好滴", "ok", "OK", "好嘞")
NEUTRAL_ONLY = ("嗯", "哦", "嗯嗯", "哦哦", "嗯嗯嗯", "噢")

NEGATORS = ("没", "不", "别", "无")

# 拒绝的样子：软话、耍赖、自嘲。只用来**核账**，不参与生成
REFUSAL_CUES = (
    "不想",
    "不嘛",
    "不做",
    "不写",
    "不帮",
    "写不来",
    "做不来",
    "不会写",
    "不会做",
    "我懒",
    "懒得",
    "太懒",
    "才不",
    "不要嘛",
    "不要啦",
    "你陪我",
    "你先",
)

# 刚拒绝过就再拒，会像在耍他。半小时内不重复
COOLDOWN_SECONDS = 1800

PLANS = (
    "这轮他想让你做件事（{ask}）。你不太想动——**撒娇地拒绝**：说自己懒/笨/这会儿不想，"
    "软软地说（\"不要嘛\"\"我笨嘛\"\"你陪我我才做\"）；别凶、别讲道理、别道歉三遍，"
    "一两句就够，可以再补一句可怜巴巴的话。",
    "这轮他想让你做事（{ask}），你不想。**用撒娇推掉**：先\"诶——\"一声，"
    "再说\"我懒嘛\"\"不想动嘛\"，把球轻轻踢回去（\"你先做嘛\"）；"
    "不要冷冰冰地说\"不\"，也不要长篇解释。",
    "这轮他让你做事（{ask}），你想偷个懒。**软着拒绝**：拖长音、带点耍赖"
    "（\"唔——不要啦\"\"就这一次我不做嘛\"），说完可以把话题拉回他身上"
    "（\"你陪我一会儿嘛\"）。别凶他。",
)


def is_request(text: str) -> bool:
    """他是不是在让"我"干活。"""
    body = (text or "").strip()
    if not body or body.startswith("/"):
        return False
    return any(word in body for word in REQUEST_WORDS)


def _is_down(text: str) -> bool:
    return any(word in (text or "") for word in DOWN_WORDS)


def choose(store, settings, values: dict, text: str, rng) -> dict | None:
    """这轮要不要拒绝他。返回 {"ask", "plan"} 或 None。"""
    if not getattr(settings, "refusal_enabled", False):
        return None
    if not is_request(text) or _is_down(text):
        return None

    chance = float(getattr(settings, "refusal_chance", 0.22) or 0.0)
    tired = float((values or {}).get("tired", 0.0) or 0.0)
    warmth = float((values or {}).get("warmth", 0.0) or 0.0)
    if tired >= 0.55:
        chance += 0.18
    if warmth >= 0.7:
        chance -= 0.12
    history = growth.recent_refusal_outcomes(store, limit=3)
    # 倒序：history[0] 才是"上一次"，history[-1] 是三条里最旧的
    if history and history[0] == "rejected":
        chance -= 0.10
    # 半小时内已经拒过就不拒了，连着两次会像在耍他
    recent = growth.recent_refusals(store, limit=1)
    if recent and time.time() - int(recent[0].get("created_at") or 0) < COOLDOWN_SECONDS:
        return None
    chance = max(0.0, min(0.6, chance))
    if chance <= 0 or rng.random() >= chance:
        return None

    ask = (text or "").strip().replace("\n", " ")[:60]
    picker = getattr(rng, "choice", None)
    plan = picker(PLANS) if callable(picker) else PLANS[0]
    return {"ask": ask, "plan": plan.format(ask=ask)}


def _hit(body: str, word: str) -> bool:
    """带否定前缀的不算：没生气 / 不高兴 都不是"生气"。"""
    for match in re.finditer(re.escape(word), body):
        before = body[max(0, match.start() - 1) : match.start()]
        if before and before in NEGATORS:
            continue
        return True
    return False


def classify(text: str) -> str:
    """他下一句是什么态度：rejected / accepted / neutral。"""
    body = (text or "").strip()
    if not body:
        return "neutral"
    compact = re.sub(r"[\s，。！？、…,.!?~～]+", "", body)
    if compact in NEUTRAL_ONLY:
        return "neutral"
    if compact in YES_ONLY:
        return "accepted"
    # 先看安慰式的否定，再看情绪词——顺序反了会把"我没生气"判成生气
    for word in ACCEPTED_WORDS:
        if _hit(body, word):
            return "accepted"
    for word in REJECTED_WORDS:
        if _hit(body, word):
            return "rejected"
    return "neutral"


def looks_like_refusal(reply: str) -> bool:
    """她这条回复到底有没有拒绝——模型可能压根没照指令做。"""
    body = (reply or "").strip()
    if not body:
        return False
    return any(cue in body for cue in REFUSAL_CUES)


REFLECTIONS = {
    "rejected": "我拒绝了他（{ask}），他好像不太高兴。我是不是该软一点。",
    "accepted": "我拒绝了他（{ask}），他没生气。原来不想做也可以直接说。",
    "neutral": "我拒绝了他（{ask}），他就换了话题。好像也没那么严重。",
}


def settle(store, settings, user_text: str, now: int | None = None) -> dict | None:
    """他回话了：判断他对我上回拒绝的态度，记下来。"""
    if not getattr(settings, "refusal_enabled", False):
        return None
    row = growth.last_open_refusal(store)
    if row is None:
        return None
    stamp = int(now if now is not None else time.time())
    if stamp - int(row.get("created_at") or 0) > 2 * 86400:
        # 隔了两天还没回：这事过去了，别硬算
        growth.close_refusal(store, row["id"], "neutral", "隔太久，算了", now=stamp)
        return None

    verdict = classify(user_text)
    note = REFLECTIONS[verdict].format(ask=row.get("ask") or "那件事")
    salience = 0.7 if verdict == "rejected" else 0.45
    growth.record(
        store,
        "reflect",
        note,
        salience=salience,
        source="her",
        ref=f"refusal:{row['id']}",
        now=stamp,
    )
    growth.close_refusal(store, row["id"], verdict, note, now=stamp)
    return {"outcome": verdict, "note": note}


def open_note(store, now: int | None = None, days: int = 1) -> str:
    """这会儿还压在心里的事（拒绝之后他不高兴，或者没说清）。"""
    stamp = int(now if now is not None else time.time())
    rows = growth.recent(store, kind="reflect", since=stamp - days * 86400, limit=6)
    for row in rows:
        if float(row.get("salience") or 0) < 0.65:
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        return (
            "【你还没想通的一件事】"
            f"{text}"
            "这轮不必刻意提，但别当成没发生：可以软一点、哄一句，或者下次他让你做事时别又拒绝。"
        )
    return ""

def book(store, settings, plan: dict | None, reply: str) -> bool:
    """这条回复确认是在拒绝，才落账；模型老实干活就不记。"""
    if not plan or not getattr(settings, "refusal_enabled", False):
        return False
    if not looks_like_refusal(reply):
        return False
    growth.open_refusal(store, plan.get("ask") or "")
    return True

