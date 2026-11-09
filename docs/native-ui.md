# 原生 Windows 控制台设计

## 结论

保留 Python 机器人作为「大脑」，用 **C# WinForms 外壳 + WebView2** 承载原来的网页控制台。

这样做的原因：

- 用户要的是独立的 Windows 软件窗口，不是浏览器标签页；WebView2 提供的是 exe 内嵌视图，没有地址栏和浏览器边框；
- 原来的网页版排版更完整、层次更稳定，直接复用比重新用 WinForms 自绘更接近网页效果；
- 之前的纯 WinForms 自绘在频繁刷新时会出现文字重绘闪烁，WebView2 的合成器不会闪；
- Python、NapCat、素材仍留在项目目录，不往 C 盘解压运行时。

## 结构

```text
大肥鱼.exe（WinForms + WebView2）
  ├─ 启动/停止 scripts\supervise.ps1、scripts\stop-bot.ps1
  ├─ 读取 data\ui-token，在窗口内打开 http://127.0.0.1:8090/?token=…
  ├─ 托盘菜单：显示 / 刷新 / 启动 / 停止 / 日志
  └─ WebView2 用户数据在 data\webview2
        │
        ▼
qqbot 进程（Python）
  ├─ OneBot 11 HTTP ←→ NapCat
  ├─ SQLite 记忆 + 人格 + 情绪 + 主动消息
  └─ 本机 WebUI / API（浏览器调试仍可用）
```

## 窗口

- 默认 1280 × 800 客户区，最小 1100 × 700；窗口可缩放；
- 窗口标题「大肥鱼 · 她今天怎么样」，使用网上二创的大肥鱼形象图标；
- 内容区就是网页控制台的完整布局：左侧 236px 导航、顶部状态栏、她今天怎么样 / 对话 / 记忆 / 人格 / 配置 / 日志 / 诊断；
- 品牌色是浅樱花粉 `#FFC9D9`、玫瑰粉 `#C2466C`、奶油白 `#FFF7FA`、梅子墨 `#4A2334`；玻璃卡片和浅粉「她」的消息保留，背景有缓慢飘落的樱花花瓣；
- 首次引导顶部有吉祥物欢迎图，安装完成页沿用同一套品牌资产；
- WebView2 默认背景也设为 `#FFF7FA`，启动时不会闪白。

## 页面

页面由现有的 `qqbot/ui/index.html`、`app.css`、`app.js` 渲染，和浏览器里的网页端是同一套：

- 她今天怎么样：进程和 QQ 登录态、最后一次说话、心情、今日统计、额度、异常、最近对话、即时控制；
- 对话：最近 60 条消息；
- 记忆：搜索 / 筛选卡片，改内容、置顶、确认、删除，以及「你记错了」；
- 人格：内置模板预览 / 切换 / 恢复默认，外加 `prompts/system.md` 编辑、历史版本、发布；
- 配置：连接、模型、主动消息频率和免打扰时段；
- 日志：`bot.log` 和审计记录；
- 诊断：端口检查、模型连通性、导出日志和审计（不含 API Key 明文）。

不再维护第二套 WinForms 页面，避免两边样式和功能 drift。

## 启动与刷新

1. 窗口显示浅樱花色加载页；
2. 检查 `127.0.0.1:<UI_PORT>`，没开就隐藏启动 `scripts\supervise.ps1`；
3. 轮询 `data/ui-token` 和端口；就绪后用 `?token=` 打开控制台，服务端写入 HttpOnly Cookie；
4. 页面内部每 5 秒刷新状态，交互仍走原来的本机 API；
5. 点「停止机器人」时外壳调用 `scripts\stop-bot.ps1`，页面切回「已停止」，托盘菜单可重新启动；
6. 关闭窗口只退出外壳，机器人继续运行。

## 安全边界

- API 只监听 `127.0.0.1`；
- 首次打开用 `data/ui-token` 换 Cookie，token 不写进 exe；
- 外壳限制顶层导航：只允许本机 `http://127.0.0.1:<port>`，外部链接交给系统浏览器；
- 关闭 DevTools、默认右键菜单、自动填充和缩放，避免误操作；
- WebView2 用户数据固定在 `data\webview2`，不写用户目录。

## 构建与依赖

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1
```

`csc.exe` 来自 `%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\`，引用：

- `System.Windows.Forms`、`System.Drawing`；
- `Microsoft.Web.WebView2.Core.dll`；
- `Microsoft.Web.WebView2.WinForms.dll`。

根目录还需要 `WebView2Loader.dll`。这三个文件随项目一起提交，不要求联网还原 NuGet；
运行机器需要安装 **Microsoft Edge WebView2 Runtime**（Windows 11 通常自带，Windows 10 可能需单独安装）。
