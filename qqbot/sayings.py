"""她说的话里跟流程无关的那部分：兜底话术、帮助文本。

context.py 和 commands.py 都要用，单独放一个文件，省得互相 import 成环。
"""

from __future__ import annotations

import random

FALLBACK_REPLIES = (
    "唔…我这会儿脑子有点转不过来，等一下再说好不好？",
    "等下，刚刚走神了，你再说一遍",
    "啊…我这边卡了一下，等我一下",
    "唔，我好像没听清，再说一次嘛",
)
FALLBACK_REPLY = FALLBACK_REPLIES[0]  # 兼容旧引用
RECALL_TITLE = "# 他以前说过的原话"


def fallback_reply() -> str:
    """兜底话术也要换着说：同一句连着出现四次，比不说话还像机器人。"""
    return random.choice(FALLBACK_REPLIES)


HELP_TEXT = (
    "我会这些呀：\n"
    "/reset 清空刚才的对话\n"
    "/memory 看看我记得什么（add 加一条、edit 改一条、ok 编号 确认）\n"
    "/forget 编号 忘掉某件事\n"
    "/pin 编号 把某件事记牢一点\n"
    "/journal 看看记忆本\n"
    "/dream 手动整理一次记忆\n"
    "/mood 看看我现在的心情\n"
    "/成长 看看她自己长出了什么想法\n"
    "/life 看她今天在干嘛（/life 换 重新生成）\n"
    "/screen 让她看一眼你的屏幕\n"
    "/pc 让她看看你电脑的状态（/pc 详细 看原始数据）\n"
    "/watch 让她坐旁边看你打游戏（/watch 停 结束）\n"
    "/digest 看今天/昨天/这周留下了什么\n"
    "/export 把聊天和记忆导成一个文件\n"
    "/tag 名字 给刚存的表情包打标\n"
    "/img list 看看图库\n"
    "/img clean 清掉回收来的旧图\n"
    "/img retag 给没标签的表情包补标\n"
    "/draw 一只猫 让她画一张（测画图用）\n"
    "/proactive 让她主动说一句\n"
    "/events 看她记下的事\n"
    "/event 2026-11-04 09:00 面试 记一件事\n"
    "/status 运行状态"
)
