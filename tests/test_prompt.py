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
