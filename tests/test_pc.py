"""看电脑：截屏、状态、说人话。不真的调 PowerShell。"""

from pathlib import Path

from qqbot import pc


def test_describe_turns_numbers_into_a_sentence():
    text = pc.describe(
        {
            "cpu": 9,
            "mem_total_mb": 16384,
            "mem_free_mb": 1024,
            "c_free_gb": 3.5,
            "c_total_gb": 126.7,
            "battery": 100,
            "on_battery": False,
            "window": "visual studio code",
            "top": ["vmmemWSL 1845MB"],
        }
    )

    assert "CPU 占用 9%" in text
    assert "内存用掉 93%" in text
    assert "C 盘剩 3.5GB" in text
    assert "电量 100%" in text
    assert "visual studio code" in text
    assert "vmmemWSL" in text


def test_describe_empty_is_empty():
    assert pc.describe({}) == ""


def test_describe_survives_partial_data():
    assert pc.describe({"cpu": 3}) == "CPU 占用 3%"


def test_prune_keeps_newest(tmp_path):
    folder = tmp_path / "screen"
    folder.mkdir()
    for index in range(6):
        path = folder / f"{index}.png"
        path.write_bytes(b"x")
        import os

        os.utime(path, (index, index))

    removed = pc.prune(folder, keep=2)

    assert removed == 4
    assert sorted(p.name for p in folder.glob("*.png")) == ["4.png", "5.png"]


def test_shrink_returns_small_jpeg(tmp_path):
    from PIL import Image

    big = tmp_path / "big.png"
    Image.new("RGB", (2000, 1200), "white").save(big)

    data = pc.shrink(big, max_side=400)

    from io import BytesIO

    assert data[:2] == b"\xff\xd8"  # JPEG
    with Image.open(BytesIO(data)) as img:
        assert max(img.size) <= 400
    assert len(data) < big.stat().st_size
