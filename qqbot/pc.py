"""让她"看得到"这台电脑：截屏和状态。

边界先写清楚：
- 只在你敲命令时执行（`/screen`、`/pc`），不会自己偷偷跑
- 只存 `data/screen/`，只读系统状态，不碰你的文件
- 截屏走 PowerShell + .NET，多屏一起截；读完立刻缩图再给视觉模型，省 token

真让她随便跑命令是另一层事，见 `docs/guides/PC_ACCESS.md`，默认不开。
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger("qqbot")

# 截屏脚本：VirtualScreen 覆盖所有显示器
SHOT_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$bmp.Save($env:QQBOT_SHOT_PATH, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
"ok:$($b.Width)x$($b.Height)"
"""

# 状态脚本：CPU/内存/磁盘/开机时长/电池/前台窗口/占用最高的三个进程
STATUS_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class QqbotWin {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  public static string Title() {
    var sb = new StringBuilder(512);
    GetWindowText(GetForegroundWindow(), sb, 512);
    return sb.ToString();
  }
}
"@
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$bat = Get-CimInstance Win32_Battery | Select-Object -First 1
$top = Get-Process | Sort-Object -Property WorkingSet64 -Descending | Select-Object -First 3 |
  ForEach-Object { "{0} {1:N0}MB" -f $_.ProcessName, ($_.WorkingSet64 / 1MB) }
[pscustomobject]@{
  cpu = [int]$cpu.Average
  mem_total_mb = [int]($os.TotalVisibleMemorySize / 1KB)
  mem_free_mb = [int]($os.FreePhysicalMemory / 1KB)
  c_free_gb = [math]::Round($disk.FreeSpace / 1GB, 1)
  c_total_gb = [math]::Round($disk.Size / 1GB, 1)
  boot = $os.LastBootUpTime.ToString("yyyy-MM-dd HH:mm")
  battery = if ($bat) { $bat.EstimatedChargeRemaining } else { $null }
  on_battery = if ($bat) { $bat.BatteryStatus -eq 1 } else { $null }
  window = [QqbotWin]::Title()
  top = @($top)
} | ConvertTo-Json -Compress
"""


def _run_ps(script: str, extra_env: dict[str, str] | None = None, timeout: int = 30) -> str:
    """跑一段 PowerShell，返回值走临时文件（避开中文乱码）。"""
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace")[:300]
        raise RuntimeError(f"PowerShell 失败：{detail or proc.returncode}")
    return (proc.stdout or b"").decode("utf-8", "replace").strip()


def screenshot(dest_dir: Path | str) -> Path:
    """截一张全屏图，返回文件路径。"""
    folder = Path(dest_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / time.strftime("%Y%m%d-%H%M%S.png")
    out = _run_ps(SHOT_PS, {"QQBOT_SHOT_PATH": str(target)}, timeout=20)
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError(f"截屏没生成文件（{out!r}）")
    log.info("截屏 %s（%s）", target.name, out)
    return target


def status() -> dict:
    """电脑状态。拿不到的部分留空，不要因为一项失败就整块挂掉。"""
    import json

    raw = _run_ps(STATUS_PS, timeout=25)
    start = raw.find("{")
    if start < 0:
        raise RuntimeError(f"状态输出看不懂：{raw[:120]!r}")
    return json.loads(raw[start:])


def describe(data: dict) -> str:
    """把状态说成人话，给她当上下文用。"""
    if not data:
        return ""
    bits: list[str] = []
    cpu = data.get("cpu")
    if cpu is not None:
        bits.append(f"CPU 占用 {cpu}%")
    total = data.get("mem_total_mb") or 0
    free = data.get("mem_free_mb") or 0
    if total:
        used = int((total - free) / total * 100)
        bits.append(f"内存用掉 {used}%（{total // 1024}GB 里剩 {free // 1024}GB）")
    if data.get("c_free_gb") is not None:
        bits.append(f"C 盘剩 {data['c_free_gb']}GB / {data.get('c_total_gb')}GB")
    if data.get("battery") is not None:
        state = "插着电" if data.get("on_battery") is False else "用电池"
        bits.append(f"电量 {data['battery']}%（{state}）")
    window = (data.get("window") or "").strip()
    if window:
        bits.append(f"前台窗口是「{window[:60]}」")
    top = data.get("top") or []
    if top:
        bits.append("占内存最多：" + "、".join(str(item) for item in top))
    return "；".join(bits)


def prune(dest_dir: Path | str, keep: int = 20) -> int:
    """只留最近几张，别把盘塞满。"""
    folder = Path(dest_dir)
    if not folder.exists():
        return 0
    files = sorted(folder.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for path in files[max(1, keep):]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def shrink(path: Path | str, max_side: int = 1000) -> bytes:
    """缩图再给视觉模型：全屏 PNG 动辄几 MB，直接发又慢又贵。"""
    raw = Path(path).read_bytes()
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(raw)) as img:
            img = img.convert("RGB")
            img.thumbnail((max_side, max_side))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            return buf.getvalue()
    except Exception:  # noqa: BLE001 - 缩图失败就发原图，别让命令挂掉
        log.exception("缩图失败，发原图")
        return raw


def frame_hash(data: bytes, size: int = 16) -> str:
    """把画面压成 256 位指纹，用来判断两帧像不像。"""
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as img:
        small = img.convert("L").resize((size, size))
        pixels = list(small.getdata())
    average = sum(pixels) / len(pixels)
    return "".join("1" if value > average else "0" for value in pixels)


def changed(before: str, after: str, threshold: int = 12) -> bool:
    """两帧差多少位才算"画面变了"。默认 256 位里差 12 位以上。"""
    if not before or not after or len(before) != len(after):
        return True
    return sum(1 for a, b in zip(before, after) if a != b) > threshold
