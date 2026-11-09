"""提示词与图库的一致性检查。

提示词里让模型使用的 tag 必须真实存在，否则模型会写出配不出来的图，
用户只会觉得"她说要发图却没发"。
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_prompt() -> str:
    return (ROOT / "prompts" / "system.md").read_text(encoding="utf-8")


def library_tags() -> set[str]:
    manifest = json.loads(
        (ROOT / "assets" / "seed-images" / "manifest.json").read_text(encoding="utf-8")
    )
    return {tag for item in manifest["items"] for tag in item["tags"]}


def listed_tags() -> list[str]:
    match = re.search(r"可用 tag：(.+)", read_prompt())
    assert match, "提示词里没找到「可用 tag：」这一行"
    return [t.strip() for t in match.group(1).split("、") if t.strip()]


def test_prompt_lists_tags():
    assert len(listed_tags()) >= 5


def test_every_listed_tag_exists_in_library():
    missing = [tag for tag in listed_tags() if tag not in library_tags()]
    assert not missing, f"提示词里的 tag 在图库中不存在：{missing}"


def test_prompt_forbids_pretending_to_take_photos():
    text = read_prompt()
    assert "不能拍照" in text or "没有相机" in text
    # 明确禁止"预告发图"，否则冷却拦下图时用户会困惑
    assert "不要用文字解说配图" in text


def test_prompt_states_length_limit():
    assert "300 字" in read_prompt()


def test_prompt_forbids_voice_templates():
    """模板腔是这类人设最容易退化的地方，规则掉了就等于没管。"""
    text = read_prompt()
    for phrase in ("不要每句都用", "关心 + 反问", "不空行", "问句别连发", "不用括号"):
        assert phrase in text, f"提示词缺少防模板规则：{phrase}"


def test_prompt_points_at_message_samples():
    """样品已经从 system 搬到消息列表了，这里只留一句指路。"""
    text = read_prompt()
    assert text.count("他：") == 0, "样品不该再塞回 system prompt"
    assert "消息列表最前面" in text
    assert "别照抄" in text


def test_extract_prompt_exists_and_forbids_delete():
    text = (ROOT / "prompts" / "extract.md").read_text(encoding="utf-8")
    assert "identity" in text
    assert "delete" in text  # 明确告知不要输出 delete
    assert "省略主语" in text


def test_compact_prompt_exists():
    text = (ROOT / "prompts" / "compact.md").read_text(encoding="utf-8")
    assert "chat" in text
    assert "delete" in text


def test_extract_prompt_documents_all_kinds():
    text = (ROOT / "prompts" / "extract.md").read_text(encoding="utf-8")
    from qqbot.memory import KIND_ORDER

    for kind in KIND_ORDER:
        assert kind in text, f"extract.md 没有提到 kind={kind}"


def test_prompt_teaches_feminine_register():
    from pathlib import Path

    text = Path("prompts/system.md").read_text(encoding="utf-8")

    assert "女生的说法" in text
    assert "骂你啥啦" in text
    assert "句尾" in text


def test_prompt_teaches_pronoun_resolution():
    from pathlib import Path

    text = Path("prompts/system.md").read_text(encoding="utf-8")

    assert "他说\"这个\"\"那个\"" in text
    assert "这个\"功能\"就是刷抖音" in text


def test_prompt_says_pictures_follow_context():
    from pathlib import Path

    text = Path("prompts/system.md").read_text(encoding="utf-8")

    assert "他发图片或表情包，先接上一两句的话" in text
    assert "别复述「图上有什么」" in text or "别复述" in text


def test_proactive_prompt_allows_her_own_day():
    """主动开口不能只靠回忆审问，她也可以说自己的事。"""
    prompt = (Path(__file__).resolve().parents[1] / "prompts" / "proactive.md").read_text(
        encoding="utf-8"
    )

    assert "你今天" in prompt
    assert "在干嘛" in prompt  # 空话仍然被点名禁止


def test_persona_is_centered_on_him():
    """不要"她有自己的立场"那一套：他要的是以他为中心的人。"""
    prompt = (Path(__file__).resolve().parents[1] / "prompts" / "system.md").read_text(
        encoding="utf-8"
    )

    assert "# 你有自己的看法" not in prompt
    assert "围着他过的" in prompt


def test_persona_allows_a_little_emoji():
    """一点表情是真人味；每句都带就成了客服模板。"""
    prompt = (Path(__file__).resolve().parents[1] / "prompts" / "system.md").read_text(
        encoding="utf-8"
    )

    assert "表情是有节奏的" in prompt
    assert "十条里" in prompt
    assert "一个" in prompt


def test_emoji_survive_formatting():
    from qqbot.formatting import strip_markdown

    assert strip_markdown("好呀 🥺") == "好呀 🥺"


def test_active_persona_has_no_fabrication_boundary():
    """当前生效的人格也必须有不编造边界，不能只靠默认模板。"""
    prompt = read_prompt()

    assert "安全边界" in prompt
    assert "不编造" in prompt
    assert "不编房间" in prompt
    assert "我顺嘴胡说的" in prompt
    assert "你有自己的生活" not in prompt

