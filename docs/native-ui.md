# 原生 Windows 控制台设计

## 结论

保留 Python 机器人作为「大脑」，把界面换成 **C# WinForms 原生窗口**，通过本机 HTTP API 读取状态、提交操作。

这样做的原因：

- 用户要的是 Windows 软件窗口，不是浏览器标签页；
- 机器人已有的记忆、协议端、主动消息都留在 Python 里，不重写；
- WinForms 由 Windows 自带 .NET Framework 编译，不需要 Electron / WebView2 / PyInstaller；
- 项目仍然完全放在 `H:\deepseek-chat`，不往 C 盘装运行时。

## 结构

```text
大肥鱼.exe（WinForms）
  ├─ 启动/停止 scripts\supervise.ps1、scripts\stop-bot.ps1
  ├─ 读取 data\ui-token，调 http://127.0.0.1:8090/api/*
  └─ 五个页面：总览 / 对话 / 人格 / 配置 / 日志
        │
        ▼
qqbot 进程（Python）
  ├─ OneBot 11 HTTP ←→ NapCat
  ├─ SQLite 记忆 + 人格 + 情绪 + 主动消息
  └─ 本机 WebUI / API（网页端保留作调试）
```

## 窗口

- 尺寸：1180 × 800 客户区，最小 980 × 660；窗口可缩放；
- 左侧 220px 导航栏：立绘、名字、运行状态、模型；
- 右侧顶栏：页面标题、面包屑、连接状态、刷新；
- 下方是页面画布，全部用 `TableLayoutPanel` / `Dock` 布局，125% DPI 下不裁切；
- 深色底 `#05111F`，卡片 `#0A2034`，青色高亮 `#5AD7FF`，粉色用于「她」的消息。

## 页面

### 总览

- 顶部：问候、当前心情文案、QQ / PID / 本机标识、右侧立绘；
- 中间：心情三条（想念 / 亲近 / 疲惫）、今日消息数、主动额度、空间 / 抖音额度、异常列表；
- 底部：最近 6 条对话 + 即时控制（主动消息 / 空间 / 抖音开关、一键停机、停止 / 启动机器人）。

### 对话

只读消息流，最近 60 条；按「她 / 你」左右色条区分，显示来源（chat / voice / command / sticker）和时间。
消息气泡按文本高度自适应，长消息自动换行。

### 人格

左侧 `prompts/system.md` 编辑器（等宽字体、保留换行、可 Tab），右侧历史版本；
底部备注输入框和发布按钮。发布调用 `PUT /api/persona`，下一条消息生效。

### 配置

两列：连接（主人 QQ、Base URL、Model ID、超时、API Key）和她的动作（主动消息 / 空间 / 抖音开关与每日上限）。
保存写 `.env`，自动备份 `.env.bak`；API Key 只写不读。

### 日志

左侧 `bot.log` 最近 200 行，右侧审计列表（时间 / 动作 / 结果）。

## 线程与刷新

- UI 线程只做绘制；HTTP 请求放在线程池，回调 `BeginInvoke` 回 UI；
- 每 5 秒刷新 `/api/status`；页面切换时按需加载对话 / 人格 / 配置 / 日志；
- 写操作（暂停、停机、保存）单独走 `PUT`，成功后刷新状态；
- 启动阶段每 1.5 秒探测端口，端口就绪后连接；90 秒未就绪提示看日志。

## 安全边界

- API 只监听 `127.0.0.1`；
- 原生客户端带 `X-UI-Token`，token 从 `data/ui-token` 读取；
- 后端对带正确 token 的请求放行写操作，不再要求浏览器 `Origin`；
- 不引入远程地址，不把 token 写进 exe。

## 构建

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1
```

`csc.exe` 来自 `%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\`，引用
`System.Windows.Forms`、`System.Drawing`、`System.Web.Extensions`（JSON）。
