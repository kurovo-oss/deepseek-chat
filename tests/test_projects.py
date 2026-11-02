"""她手上的事：有状态、有进度、能放弃——不是每天换一个标签。"""

from types import SimpleNamespace

from qqbot import projects


def make_settings(tmp_path):
    return SimpleNamespace(base_dir=tmp_path)


def test_save_and_load_roundtrip(tmp_path):
    settings = make_settings(tmp_path)
    items = [{"title": "学做红烧肉", "status": "active", "note": "失败一次", "attempts": 1}]

    projects.save(settings, items)

    assert projects.load(settings) == items


def test_missing_file_loads_empty(tmp_path):
    assert projects.load(make_settings(tmp_path)) == []


def test_broken_file_does_not_raise(tmp_path):
    settings = make_settings(tmp_path)
    path = settings.base_dir / "data" / "projects.json"
    path.parent.mkdir(parents=True)
    path.write_text("{坏的", encoding="utf-8")

    assert projects.load(settings) == []


def test_start_adds_an_active_project(tmp_path):
    items = projects.apply(
        [], {"action": "start", "project": "改简历", "reason": "想换工作"}, "2026-11-02"
    )

    assert len(items) == 1
    assert items[0]["status"] == "active"
    assert items[0]["attempts"] == 1
    assert items[0]["reason"] == "想换工作"


def test_advance_counts_attempts_and_updates_note(tmp_path):
    items = [{"title": "学做红烧肉", "status": "active", "attempts": 1, "note": "糊了"}]

    items = projects.apply(
        items, {"action": "advance", "project": "学做红烧肉", "note": "这次成了"}, "2026-11-03"
    )

    assert items[0]["attempts"] == 2
    assert items[0]["note"] == "这次成了"
    assert items[0]["touched"] == "2026-11-03"


def test_she_can_give_up(tmp_path):
    items = [{"title": "救绿萝", "status": "active", "attempts": 3}]

    items = projects.apply(items, {"action": "drop", "project": "救绿萝"}, "2026-11-04")

    assert items[0]["status"] == "dropped"


def test_unknown_project_is_left_alone(tmp_path):
    items = [{"title": "改简历", "status": "active", "attempts": 1}]

    items = projects.apply(items, {"action": "advance", "project": "没这回事"}, "2026-11-02")

    assert items[0]["attempts"] == 1


def test_active_list_is_capped(tmp_path):
    items = []
    for name in ("a", "b", "c", "d"):
        items = projects.apply(
            items, {"action": "start", "project": name}, f"2026-11-0{len(items) + 1}"
        )

    assert len(projects.active(items)) == projects.MAX_ACTIVE
    assert any(item["status"] == "paused" for item in items)


def test_block_tells_her_what_she_has(tmp_path):
    items = [
        {"title": "学做红烧肉", "status": "active", "note": "糊了两次", "attempts": 2},
        {"title": "改简历", "status": "paused", "note": "写了一半"},
    ]

    text = projects.block(items, ("学做菜",))

    assert "学做红烧肉：糊了两次" in text
    assert "已经试了 2 次" in text
    assert "改简历" not in text  # 放下的不该再催她
    assert "学做菜" in text  # 灵感还在


def test_block_says_when_nothing_is_going_on():
    assert "没有正在进行" in projects.block([])


def test_describe_lists_only_active(tmp_path):
    items = [
        {"title": "学做红烧肉", "status": "active", "attempts": 2},
        {"title": "改简历", "status": "paused", "attempts": 1},
    ]

    text = projects.describe(items)

    assert "学做红烧肉（2 次）" in text
    assert "改简历" not in text


def test_describe_when_idle():
    assert "没有正在进行" in projects.describe([])
