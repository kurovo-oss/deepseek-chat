"""生活线：她自己的日子。没这条线，她每句话都挂在他身上。"""

import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from qqbot import life, projects
from qqbot.llm import ChatResult
from qqbot.store import Store


class FakeLLM:
    def __init__(self, text="洗了窗帘\n削笔刀找不到了\n泡了第二杯茶\n楼下新开了面包店"):
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return ChatResult(self.text, 10, 20, "fake-model")


def make_settings(tmp_path, **overrides):
    base = dict(
        life_enabled=True,
        base_dir=tmp_path,
        life_dir=tmp_path / "life",
        life_keep_days=3,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_today_generates_and_caches(tmp_path):
    settings = make_settings(tmp_path)
    llm = FakeLLM()

    text = life.today(settings, llm)

    assert "洗了窗帘" in text
    assert len(llm.calls) == 1
    # 同一天不重复花钱
    assert life.today(settings, llm) == text
    assert len(llm.calls) == 1


def test_today_strips_numbering_and_bullets(tmp_path):
    settings = make_settings(tmp_path)
    llm = FakeLLM("1. 洗了窗帘\n2) 笔找不到\n- 泡了茶\n\n4. 面包店")

    text = life.today(settings, llm)

    assert not any(line[:1].isdigit() for line in text.splitlines())
    assert "洗了窗帘" in text


def test_today_without_llm_is_empty(tmp_path):
    settings = make_settings(tmp_path)
    assert life.today(settings, None) == ""


def test_empty_result_is_not_cached(tmp_path):
    settings = make_settings(tmp_path)
    llm = FakeLLM("   ")

    assert life.today(settings, llm) == ""
    assert not list(Path(settings.life_dir).glob("*.md"))


def test_snippet_is_empty_before_generation(tmp_path):
    settings = make_settings(tmp_path)
    assert life.snippet(settings) == ""


def test_snippet_wraps_today(tmp_path):
    settings = make_settings(tmp_path)
    life.today(settings, FakeLLM())

    text = life.snippet(settings)

    assert text.startswith("【你今天】")
    assert "洗了窗帘" in text


def test_prune_keeps_recent_files(tmp_path):
    settings = make_settings(tmp_path)
    folder = Path(settings.life_dir)
    folder.mkdir(parents=True)
    for name in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"):
        (folder / f"{name}.md").write_text("x", encoding="utf-8")

    removed = life.prune(settings)

    assert removed == 2
    assert sorted(p.name for p in folder.glob("*.md")) == [
        "2026-10-03.md",
        "2026-10-04.md",
        "2026-10-05.md",
    ]


def test_chat_like_reply_is_rejected(tmp_path):
    """模型有时候会像聊天一样回一句"好呀。"，这不是日子，不能落盘。"""
    settings = make_settings(tmp_path)
    llm = FakeLLM("好呀。")

    assert life.today(settings, llm) == ""
    assert not list(Path(settings.life_dir).glob("*.md"))


def test_busy_things_rotate_by_day():
    from datetime import date

    first = life.busy_things(date(2026, 11, 2))
    again = life.busy_things(date(2026, 11, 2))
    other = life.busy_things(date(2026, 11, 3))

    assert first == again
    assert first != other
    assert len(first.split("、")) == 2


def test_recent_days_feed_back(tmp_path, monkeypatch):
    from datetime import date

    settings = type("S", (), {"life_dir": tmp_path})()
    (tmp_path / "2026-11-01.md").write_text("昨天在收拾屋子", encoding="utf-8")
    (tmp_path / "2026-11-02.md").write_text("今天在学做菜", encoding="utf-8")

    text = life.recent(settings, date(2026, 11, 2), days=1)

    assert "昨天在收拾屋子" in text


def _decision(**overrides):
    import json

    data = {
        "action": "advance",
        "project": "学做红烧肉",
        "note": "第三次终于成了，就是有点咸",
        "reason": "前两天都失败了，今天想再试一次",
        "today": [
            "在厨房站了一下午，锅里终于不糊了",
            "盐放多了，喝了两杯水",
            "手边还摆着那本菜谱",
            "下次做给你尝尝",
        ],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def test_today_records_her_own_choice(tmp_path):
    """她自己挑的事要写进今天，而且说得出为什么。"""
    from qqbot import projects

    settings = make_settings(tmp_path)
    text = life.today(settings, FakeLLM(_decision()))

    assert "今天挑的是：学做红烧肉" in text
    assert "前两天都失败了" in text
    assert "在厨房站了一下午" in text


def test_her_choice_is_saved_for_tomorrow(tmp_path):
    from qqbot import projects

    settings = make_settings(tmp_path)
    life.today(settings, FakeLLM(_decision(action="start", project="学做红烧肉")))

    items = projects.load(settings)
    assert items[0]["title"] == "学做红烧肉"
    assert items[0]["attempts"] == 1


def test_starting_something_new_is_recorded(tmp_path):
    from qqbot import projects

    settings = make_settings(tmp_path)
    life.today(
        settings,
        FakeLLM(_decision(action="start", project="研究怎么冲咖啡", reason="想学着喝苦的")),
    )

    text = life._read(life._day_path(settings, __import__("datetime").date.today()))
    assert "今天开了件新事：研究怎么冲咖啡" in text
    assert projects.load(settings)[0]["title"] == "研究怎么冲咖啡"


def test_a_day_without_projects_is_allowed(tmp_path):
    from qqbot import projects

    settings = make_settings(tmp_path)
    life.today(settings, FakeLLM(_decision(action="none", project="", reason="")))

    assert "今天没特意推进手上的事" in life.snippet(settings)
    assert projects.load(settings) == []


def test_plain_prose_falls_back_without_touching_projects(tmp_path):
    from qqbot import projects

    settings = make_settings(tmp_path)
    text = life.today(settings, FakeLLM())

    assert "洗了窗帘" in text
    assert projects.load(settings) == []


def test_broken_json_falls_back_to_prose(tmp_path):
    settings = make_settings(tmp_path)
    text = life.today(settings, FakeLLM('{"action": "advance", 坏的\n洗了窗帘\n笔找不到'))

    assert "洗了窗帘" in text  # 解析失败也不丢一天


def test_life_call_has_enough_tokens(tmp_path):
    """推理 token 也占额度：给少了会偶发空回复，一天的日子就白丢了。"""
    settings = make_settings(tmp_path)
    llm = FakeLLM(_decision())

    life.today(settings, llm)

    assert llm.calls[0][1]["max_tokens"] >= 1500


def test_truncated_json_is_salvaged(tmp_path):
    """模型偶尔把 JSON 截断——不能因为少个括号就丢掉一整天。"""
    settings = make_settings(tmp_path)
    llm = FakeLLM(
        '{"action": "done", "project": "番茄牛腩", "note": "终于成了", '
        '"reason": "炖了三回", "today": ["肉终于烂了", "汤还是有点咸"'
    )

    text = life.today(settings, llm)

    assert "肉终于烂了" in text
    assert "今天把" not in text  # 标题要按 done 的说法来
    assert "番茄牛腩" in text


# ------------------------------------------------ 别拿同一件事硬拐话题
def _with_stew(settings):
    folder = settings.life_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{date.today().isoformat()}.md").write_text(
        "今天挑的是：学做番茄牛腩（想再试一次）\n"
        "番茄下锅炒半天不出沙\n灶上小火咕嘟着\n",
        encoding="utf-8",
    )


def test_keywords_come_from_her_own_day(tmp_path):
    settings = make_settings(tmp_path)
    _with_stew(settings)
    projects.save(settings, [{"title": "学做番茄牛腩", "status": "active"}])

    words = life.keywords(settings)

    assert "牛腩" in words
    assert "今天" not in words  # 太泛的词不算，否则每条回复都会被当成"提过"


def test_mentions_today_counts_her_talk(tmp_path):
    settings = make_settings(tmp_path)
    _with_stew(settings)
    store = Store(tmp_path / "chat.db")
    store.save_message("100", "assistant", "牛腩炖上了，就是有点咸")
    store.save_message("100", "assistant", "今天好累")
    store.save_message("100", "user", "牛腩好吃吗")

    assert life.mentions_today(store, "100", settings) == 1
    store.close()


def test_snippet_presses_when_she_repeats_herself(tmp_path):
    settings = make_settings(tmp_path)
    _with_stew(settings)

    note = life.snippet(settings, repeat=2)

    assert "已经说过 2 次" in note
    assert "别提它" in note


def test_after_saying_it_too_often_the_note_is_withheld(tmp_path):
    settings = make_settings(tmp_path)
    _with_stew(settings)

    assert life.snippet(settings, repeat=3) == ""


def test_snippet_is_quiet_the_first_couple_of_times(tmp_path):
    settings = make_settings(tmp_path)
    _with_stew(settings)

    assert "已经说过" not in life.snippet(settings, repeat=1)


def test_day_with_unreal_activity_is_rejected(tmp_path):
    """她写"刷到一个视频"——系统做不到，这一天就不能要。"""
    settings = make_settings(tmp_path)
    text = life.today(
        settings,
        FakeLLM(_decision(today=["刷到一个超好笑的视频", "笑到肚子疼", "手机没电了", "想给他看"])),
    )

    assert text == ""
    assert life.snippet(settings) == ""


def test_prompt_tells_her_what_she_cannot_do(tmp_path):
    settings = make_settings(tmp_path)
    llm = FakeLLM(_decision())

    life.today(settings, llm)

    prompt = llm.calls[0][0][1]["content"]
    assert "你现在做不到" in prompt
    assert "刷抖音" in prompt

def test_prompt_tells_her_to_stop_after_three_days(tmp_path):
    settings = make_settings(tmp_path)
    projects.save(settings, [{
        "title": "学做番茄牛腩", "status": "active", "note": "第三锅", "reason": "不服气",
        "attempts": 3, "started": "2026-11-02", "touched": "2026-11-05",
    }])
    llm = FakeLLM()

    life.today(settings, llm)

    prompt = llm.calls[0][0][1]["content"]
    assert "已经试了 3 次" in prompt
    assert "最多连着推进 3 天" in prompt
    assert "至少两句写别的" in prompt

