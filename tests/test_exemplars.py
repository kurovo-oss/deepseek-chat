"""动态范例：按场景挑，能轮换，坏行不炸。"""

import json

from qqbot import exemplars


def write_examples(tmp_path, rows):
    path = tmp_path / "prompts" / "exemplars.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_select_matches_tags(tmp_path):
    write_examples(
        tmp_path,
        [
            {"tags": ["comfort"], "user": "累", "assistant": "抱抱"},
            {"tags": ["tease"], "user": "傻", "assistant": "你才傻"},
        ],
    )

    picked = exemplars.select(tmp_path, ["comfort"], limit=1)

    assert picked[0]["user"] == "累"


def test_select_rotates_with_seed(tmp_path):
    write_examples(
        tmp_path,
        [
            {"tags": ["comfort"], "user": f"累{i}", "assistant": f"抱{i}"}
            for i in range(4)
        ],
    )

    first = exemplars.select(tmp_path, ["comfort"], limit=1, seed=1)[0]["user"]
    second = exemplars.select(tmp_path, ["comfort"], limit=1, seed=2)[0]["user"]

    assert first != second


def test_bad_lines_are_skipped(tmp_path):
    path = tmp_path / "prompts" / "exemplars.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        '不是 json\n{"tags":["daily"],"user":"在吗","assistant":"在"}\n',
        encoding="utf-8",
    )

    assert exemplars.select(tmp_path, ["daily"], limit=2)[0]["user"] == "在吗"


def test_missing_file_is_empty(tmp_path):
    assert exemplars.load(tmp_path) == []
    assert exemplars.messages(tmp_path, ["daily"]) == []


def test_messages_are_user_assistant_pairs(tmp_path):
    write_examples(
        tmp_path,
        [{"tags": ["daily"], "user": "在吗", "assistant": "在\n你说"}],
    )

    rows = exemplars.messages(tmp_path, ["daily"], limit=1)

    assert rows == [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "在\n你说"},
    ]
