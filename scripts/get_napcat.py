"""一键下载 NapCat（QQ 协议端）。

用法：
    python scripts/get_napcat.py              # 自动判断要不要自带 Node 的包
    python scripts/get_napcat.py --dry-run    # 只看会下载什么，不实际下载
    python scripts/get_napcat.py --bundled-node   # 强制下自带 Node 的完整包
    python scripts/get_napcat.py --dest D:\tools  # 换目标目录

只依赖标准库，和主程序保持一致。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "NapNeko/NapCatQQ"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
USER_AGENT = "qqbot-setup"
SHELL_ASSET = "NapCat.Shell.zip"
BUNDLED_ASSET = "NapCat.Shell.Windows.Node.zip"
CHUNK = 1 << 20


def http_get(url: str, timeout: float = 60.0):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_release(opener=http_get) -> dict:
    with opener(API_URL) as response:
        data = response.read()
    return json.loads(data.decode("utf-8"))


def has_node() -> bool:
    return shutil.which("node") is not None


def choose_asset(assets: list[dict], want_bundled: bool) -> dict:
    name = BUNDLED_ASSET if want_bundled else SHELL_ASSET
    for asset in assets:
        if asset.get("name") == name:
            return asset
    available = ", ".join(a.get("name", "?") for a in assets) or "（空）"
    raise LookupError(f"发布里没有 {name}；可用资源：{available}")


def download(url: str, dest: Path, expected_size: int | None = None, opener=http_get) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    written = 0
    with opener(url) as response, open(part, "wb") as handle:
        raw_total = response.headers.get("Content-Length")
        total = int(raw_total) if raw_total else (expected_size or 0)
        while True:
            block = response.read(CHUNK)
            if not block:
                break
            handle.write(block)
            written += len(block)
            if total:
                print(
                    f"\r    已下载 {written / 1048576:.1f} / {total / 1048576:.1f} MB",
                    end="",
                    flush=True,
                )
    print()
    if expected_size is not None and written != expected_size:
        part.unlink(missing_ok=True)
        raise IOError(f"下载不完整：{written} 字节，应为 {expected_size} 字节")
    part.replace(dest)
    return dest


def safe_extract(zip_path: Path, dest: Path) -> None:
    """解压前逐个成员校验，避免压缩包里的 ../ 写到目标目录外。"""
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (dest / member.filename).resolve()
            try:
                target.relative_to(dest)
            except ValueError:
                raise ValueError(f"压缩包内含越界路径，已中止：{member.filename}")
        archive.extractall(dest)


SKIP_LAUNCHERS = {"killqq.bat"}


def pick_launcher(files: list[Path]) -> Path | None:
    """挑出该让用户双击的 bat。KillQQ 会杀 QQ 进程，绝不能推荐。"""
    candidates = [
        path
        for path in files
        if path.suffix.lower() == ".bat" and path.name.lower() not in SKIP_LAUNCHERS
    ]
    for path in candidates:
        if path.name.lower() == "launcher.bat":
            return path
    return sorted(candidates)[0] if candidates else None


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 NapCat QQ 协议端")
    parser.add_argument("--dest", default=None, help="目标目录，默认项目下 tools/")
    parser.add_argument("--bundled-node", action="store_true", help="强制下载自带 Node 的包")
    parser.add_argument("--dry-run", action="store_true", help="只显示下载计划")
    parser.add_argument("--force", action="store_true", help="目标目录已存在时仍然继续")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None, opener=http_get, node_present: bool | None = None) -> int:
    args = _parse(argv)
    project_root = Path(__file__).resolve().parent.parent
    dest = Path(args.dest).resolve() if args.dest else project_root / "tools"

    print(f"==> 查询 {REPO} 最新版本")
    try:
        release = fetch_release(opener)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"    失败：{exc}")
        print("    检查网络，或手动到 https://github.com/NapNeko/NapCatQQ/releases 下载")
        return 1

    tag = release.get("tag_name", "unknown")
    node_ok = has_node() if node_present is None else node_present
    want_bundled = args.bundled_node or not node_ok
    try:
        asset = choose_asset(release.get("assets", []), want_bundled)
    except LookupError as exc:
        print(f"    失败：{exc}")
        return 1

    size = asset.get("size") or 0
    reason = "强制" if args.bundled_node else ("本机无 node" if not node_ok else "本机已有 node")
    print(f"    版本 {tag}（{reason}，选择 {asset['name']}，{size / 1048576:.1f} MB）")

    if args.dry_run:
        print(f"    下载地址：{asset['browser_download_url']}")
        print(f"    解压到：{dest / ('NapCat-' + tag)}")
        print("    --dry-run：未实际下载")
        return 0

    target = dest / f"NapCat-{tag}"
    if target.exists() and not args.force:
        print(f"    {target} 已存在，加 --force 可覆盖")
        return 0

    zip_path = dest / asset["name"]
    print(f"==> 下载到 {zip_path}")
    try:
        download(asset["browser_download_url"], zip_path, size, opener)
    except (urllib.error.URLError, OSError) as exc:
        print(f"    失败：{exc}")
        return 1

    print(f"==> 解压到 {target}")
    try:
        safe_extract(zip_path, target)
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        print(f"    失败：{exc}")
        return 1

    print("==> 完成，顶层内容：")
    for item in sorted(target.iterdir()):
        print(f"    {'[目录]' if item.is_dir() else '      '} {item.name}")

    launcher = pick_launcher(sorted(target.glob("*.bat")))
    if launcher:
        print("\n启动方式：")
        print(f'    "{launcher}"')
    print("\n接下来：")
    print("    1. 启动上面那个 bat，用你的小号扫码登录")
    print("    2. 在 NapCat 的网络配置里开 HTTP 服务器（端口 3001）")
    print("    3. 再开 HTTP POST 上报，地址 http://127.0.0.1:8080/event")
    print("    4. 回到项目目录跑：python -m qqbot --check-adapter")
    return 0


if __name__ == "__main__":
    sys.exit(run())
