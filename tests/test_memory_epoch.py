# -*- coding: utf-8 -*-
from pathlib import Path

from qqbot import memory_epoch


def test_mark_and_read_clean_cutoff(tmp_path):
    assert memory_epoch.read(tmp_path) == 0

    stamp = memory_epoch.mark(tmp_path, now=1_700_000_000)

    assert stamp == 1_700_000_000
    assert memory_epoch.read(tmp_path) == stamp
    assert memory_epoch.after(stamp - 1, tmp_path) is False
    assert memory_epoch.after(stamp, tmp_path) is True
    assert memory_epoch.path_for(tmp_path).read_text(encoding="ascii") == str(stamp)


def test_missing_or_broken_mark_means_no_cutoff(tmp_path):
    path = memory_epoch.path_for(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not-a-time", encoding="ascii")

    assert memory_epoch.read(tmp_path) == 0
    assert memory_epoch.after(1, tmp_path) is True
