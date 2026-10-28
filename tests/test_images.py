import json

import pytest

from qqbot.images import ImageLibrary


def make_library(tmp_path, files, items):
    for name in files:
        (tmp_path / name).write_bytes(b"fake-image-bytes")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8"
    )
    return ImageLibrary(tmp_path)


def test_resolve_by_tag(tmp_path):
    lib = make_library(tmp_path, ["cat.png"], [{"file": "cat.png", "tags": ["cat"]}])
    assert lib.resolve("cat") == (tmp_path / "cat.png").resolve()
    assert lib.resolve("CAT") == (tmp_path / "cat.png").resolve()  # 大小写不敏感
    assert lib.resolve("dog") is None


def test_multiple_files_same_tag(tmp_path):
    lib = make_library(
        tmp_path,
        ["a.png", "b.png"],
        [{"file": "a.png", "tags": ["cat"]}, {"file": "b.png", "tags": ["cat"]}],
    )
    assert len(lib) == 2
    picked = {lib.resolve("cat") for _ in range(20)}
    assert picked <= {(tmp_path / "a.png").resolve(), (tmp_path / "b.png").resolve()}


def test_missing_manifest_falls_back_to_filename(tmp_path):
    (tmp_path / "happy.png").write_bytes(b"x")
    lib = ImageLibrary(tmp_path)
    assert lib.resolve("happy") is not None


def test_manifest_entry_without_file_is_skipped(tmp_path):
    lib = make_library(tmp_path, ["cat.png"], [{"file": "ghost.png", "tags": ["ghost"]}])
    assert lib.resolve("ghost") is None


def test_rejects_path_traversal_in_manifest(tmp_path):
    (tmp_path.parent / "evil.png").write_bytes(b"x")
    lib = make_library(tmp_path, ["cat.png"], [{"file": "../evil.png", "tags": ["evil"]}])
    assert lib.resolve("evil") is None


def test_rejects_backslash_traversal(tmp_path):
    lib = make_library(tmp_path, ["cat.png"], [{"file": "..\\evil.png", "tags": ["evil"]}])
    assert lib.resolve("evil") is None


def test_rejects_unsupported_extension(tmp_path):
    lib = make_library(tmp_path, ["x.exe"], [{"file": "x.exe", "tags": ["bad"]}])
    assert lib.resolve("bad") is None


def test_broken_manifest_does_not_crash(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    lib = ImageLibrary(tmp_path)
    assert len(lib) == 0
    assert lib.resolve("cat") is None


def test_missing_directory_is_empty(tmp_path):
    lib = ImageLibrary(tmp_path / "nope")
    assert len(lib) == 0
    assert lib.resolve("cat") is None


def test_len_counts_files_not_tags(tmp_path):
    lib = make_library(
        tmp_path,
        ["cat.png"],
        [{"file": "cat.png", "tags": ["cat", "animal", "cute"]}],
    )
    assert len(lib) == 1
    assert len(lib.tags) == 3


def test_tags_listed(tmp_path):
    lib = make_library(
        tmp_path,
        ["cat.png", "dog.png"],
        [{"file": "cat.png", "tags": ["cat", "animal"]}, {"file": "dog.png", "tags": ["dog"]}],
    )
    assert lib.tags == ["animal", "cat", "dog"]
