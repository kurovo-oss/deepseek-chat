import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "get_napcat", ROOT / "scripts" / "get_napcat.py"
)
gn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gn)


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def make_opener(payloads):
    def opener(url, timeout=60.0):
        if url not in payloads:
            raise AssertionError(f"未预期的 URL: {url}")
        return FakeResponse(payloads[url])

    return opener


def build_zip(path: Path, members: dict[str, bytes]) -> bytes:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path.read_bytes()


# ---------------- choose_asset ----------------
ASSETS = [
    {"name": gn.SHELL_ASSET, "size": 100, "browser_download_url": "https://x/shell.zip"},
    {"name": gn.BUNDLED_ASSET, "size": 200, "browser_download_url": "https://x/node.zip"},
]


def test_choose_asset_with_node():
    assert gn.choose_asset(ASSETS, want_bundled=False)["name"] == gn.SHELL_ASSET


def test_choose_asset_without_node():
    assert gn.choose_asset(ASSETS, want_bundled=True)["name"] == gn.BUNDLED_ASSET


def test_choose_asset_missing_raises():
    with pytest.raises(LookupError):
        gn.choose_asset([{"name": "other.zip"}], want_bundled=False)


# ---------------- download ----------------
def test_download_writes_file(tmp_path):
    payload = b"x" * 4096
    dest = tmp_path / "pkg.zip"
    opener = make_opener({"https://x/pkg.zip": payload})

    gn.download("https://x/pkg.zip", dest, expected_size=4096, opener=opener)

    assert dest.read_bytes() == payload
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_size_mismatch_raises_and_cleans_up(tmp_path):
    dest = tmp_path / "pkg.zip"
    opener = make_opener({"https://x/pkg.zip": b"x" * 10})

    with pytest.raises(IOError):
        gn.download("https://x/pkg.zip", dest, expected_size=999, opener=opener)

    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


# ---------------- safe_extract ----------------
def test_safe_extract_ok(tmp_path):
    zip_path = tmp_path / "a.zip"
    build_zip(zip_path, {"launcher.bat": b"echo hi", "app/config.json": b"{}"})

    target = tmp_path / "out"
    gn.safe_extract(zip_path, target)

    assert (target / "launcher.bat").read_text() == "echo hi"
    assert (target / "app" / "config.json").exists()


def test_safe_extract_rejects_traversal(tmp_path):
    zip_path = tmp_path / "evil.zip"
    build_zip(zip_path, {"../evil.txt": b"boom"})

    with pytest.raises(ValueError):
        gn.safe_extract(zip_path, tmp_path / "out")

    assert not (tmp_path / "evil.txt").exists()


# ---------------- pick_launcher ----------------
def test_pick_launcher_prefers_launcher_bat(tmp_path):
    files = [
        tmp_path / "KillQQ.bat",
        tmp_path / "quickLoginExample.bat",
        tmp_path / "launcher.bat",
    ]
    assert gn.pick_launcher(files).name == "launcher.bat"


def test_pick_launcher_never_returns_killqq(tmp_path):
    assert gn.pick_launcher([tmp_path / "KillQQ.bat"]) is None


def test_pick_launcher_falls_back_to_other_bat(tmp_path):
    assert gn.pick_launcher([tmp_path / "launcher-user.bat"]).name == "launcher-user.bat"


def test_pick_launcher_no_bat(tmp_path):
    assert gn.pick_launcher([tmp_path / "readme.md"]) is None


# ---------------- run ----------------
def test_run_dry_run_downloads_nothing(tmp_path):
    release = {"tag_name": "v9.9.9", "assets": ASSETS}
    opener = make_opener({gn.API_URL: json.dumps(release).encode()})

    code = gn.run(["--dry-run", "--dest", str(tmp_path)], opener=opener, node_present=True)

    assert code == 0
    assert list(tmp_path.glob("*.zip")) == []


def test_run_full_flow(tmp_path):
    zip_path = tmp_path / "prebuilt.zip"
    payload = build_zip(zip_path, {"launcher.bat": b"echo hi", "README.md": b"doc"})
    release = {
        "tag_name": "v9.9.9",
        "assets": [
            {"name": gn.SHELL_ASSET, "size": len(payload), "browser_download_url": "https://x/shell.zip"}
        ],
    }
    opener = make_opener(
        {gn.API_URL: json.dumps(release).encode(), "https://x/shell.zip": payload}
    )
    dest = tmp_path / "tools"

    code = gn.run(["--dest", str(dest)], opener=opener, node_present=True)

    assert code == 0
    assert (dest / gn.SHELL_ASSET).exists()
    assert (dest / "NapCat-v9.9.9" / "launcher.bat").exists()


def test_run_existing_target_skips_without_force(tmp_path):
    zip_path = tmp_path / "prebuilt.zip"
    payload = build_zip(zip_path, {"launcher.bat": b"echo hi"})
    release = {
        "tag_name": "v9.9.9",
        "assets": [
            {"name": gn.SHELL_ASSET, "size": len(payload), "browser_download_url": "https://x/shell.zip"}
        ],
    }
    opener = make_opener(
        {gn.API_URL: json.dumps(release).encode(), "https://x/shell.zip": payload}
    )
    dest = tmp_path / "tools"
    (dest / "NapCat-v9.9.9").mkdir(parents=True)

    code = gn.run(["--dest", str(dest)], opener=opener, node_present=True)

    assert code == 0
    assert not (dest / gn.SHELL_ASSET).exists()  # 没有重复下载


def test_run_reports_network_failure(tmp_path):
    def opener(url, timeout=60.0):
        raise OSError("network down")

    assert gn.run(["--dest", str(tmp_path)], opener=opener, node_present=True) == 1
