# 她怎么"访问"你的电脑

先说结论：**默认只在你敲命令的时候看，看完就停。**

代码里没有定时截屏、没有目录扫描、没有键盘记录、没有后台进程监控。她能拿到的只有两类事实：

| 命令 | 她拿到什么 | 数据去向 |
|---|---|---|
| `/screen` | 截一张全屏图（多显示器一起），缩到 1000px 的 JPEG 交给视觉模型 | 图存 `data/screen/`，只留最近 20 张；描述会进当轮 context |
| `/pc` | CPU、内存、C 盘剩余、开机时间、电量、前台窗口标题、内存 top3 进程 | 只在本轮 prompt 里用，不落盘 |
| `/pc 详细` | 原始数字，原样回给你 | 不经过模型 |

## 实现方式

`qqbot/pc.py`：

- 截屏：起一个 `powershell -NoProfile -Command`，用 `System.Drawing` 调 `CopyFromScreen`，截 `VirtualScreen`（覆盖所有显示器），存 PNG。中文字段走临时文件传参，避开 PowerShell 管道乱码。
- 状态：`Get-CimInstance Win32_Processor/Win32_OperatingSystem/Win32_LogicalDisk/Win32_Battery` + `Get-Process` 按 `WorkingSet64` 排序取前三；前台窗口用 `user32.dll` 的 `GetForegroundWindow` + `GetWindowText`（`Add-Type` 内嵌 C#）。
- 缩图：Pillow，`thumbnail((1000,1000))` + JPEG q80，全屏 PNG 156KB → JPEG 68KB。
- 画质取舍：不给原图，因为一次 4K 截图 base64 之后能吃掉几万 token。

`qqbot/engine.py`：

- `_screen_command`：截屏 → 发图给你 → 让模型看缩图说一句（`_react_to`，单独一次调用，不进对话历史）。
- `_pc_command`：读状态 → 让她就着状态说一句；`/pc 详细` 直接回数字。
- 开关：`SCREEN_ENABLED=false` 一句话关掉。

## 为什么不做成"她自己想截就截"

因为那需要两样东西：模型能主动发指令（现在这套是纯 chat completions，没有工具调用），以及一份白名单。真要做是另一层：

1. 给模型一个 `run_command(cmd)` 工具，只允许白名单（`tasklist`、`dir`、`systeminfo`…），每次调用写审计日志
2. 或者更狠：允许任意 PowerShell，等你确认后才执行

这两条我都没做，因为一旦允许任意命令，她（或者提示词注入）就能删文件、发数据出去。真需要的话，加 `docs/PC_ACCESS.md` 里说的审计层再做。

## 隐私边界

- 截屏只在你敲 `/screen` 的那一刻发生，她不会自己触发
- 图不会上传网盘，只进模型 API 这一条路（和你发图给她是同一回事）
- `data/screen/` 只留最近 `SCREEN_KEEP`（默认 20）张
- 前台窗口标题会进 prompt——这等于告诉她你正在用什么软件；不想让她知道就 `/pc` 别看

## 已知限制

- 锁屏或没有交互式桌面会话时，截图是黑的（任务计划/服务里跑就是这样）
- 多显示器会拼成一张超宽图，缩到 1000px 后小屏内容会糊
- `Get-ForegroundWindow` 在 UAC 提权窗口上拿不到标题（正常现象）
