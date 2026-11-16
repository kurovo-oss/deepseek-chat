# AGENTS.md

本文件是给在这个仓库里工作的编码 agent 的交付合同。先读它，再按任务读
`README.md`、`docs/`、`installer/README.md`。

## 0. 常用命令

```powershell
# 单元测试（始终用项目内解释器，不要用系统 python）
tools\python\python.exe -m pytest -q

# 真实模型评测（会花钱、需要 .env；不是单元测试）
$env:PYTHONIOENCODING="utf-8"
tools\python\python.exe -m evals.run --compare evals/compact.json

# 只跑某个场景
tools\python\python.exe -m evals.run --only 指代

# 改完 C# 外壳后，先编译外壳
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1

# 再打安装包（它只复制现有的 大肥鱼.exe，不会替你编译）
powershell -ExecutionPolicy Bypass -File installer\build-installer.ps1
```

## 1. 硬规则

1. 每次改动完成后都必须创建一个对应的 git commit，方便追踪和回滚；不要攒一堆无关改动一次性提交。
2. 改行为必须写或更新测试；修 bug 必须留下能复现它的回归测试。
3. 交付前必须跑完相关测试；没有全绿就不要说“做完了”。
4. 永不提交 `.env`、`data/`、`logs/`、`dist/`、`build/`、`tools/` 里的内容。
5. 测试不能调用真实 QQ、NapCat、QQ 空间、抖音，也不能给真实用户发消息。
6. 不要为了让测试变绿而放宽断言；如果是评测标准本身错了，改标准并在提交里说明原因。

## 2. 环境与依赖

- 运行时是 **Python 3.12 + 标准库**，不引入第三方运行时依赖；`pytest`、`Pillow` 只用于测试/脚本。
- 新增依赖前先问用户；能用标准库 `urllib`、`sqlite3`、`http.server` 就不要加包。
- 项目是 Windows 优先：路径、编码、OneBot 本地端口都按 Windows 写；不要为了“跨平台”改坏现有路径。
- `.ps1` 默认按 PowerShell 5.1 兼容写；没有 BOM 时保持纯 ASCII（`supervise.ps1` 等现有脚本就是因此全英文）。
- `.nsi` 必须是 **UTF-8 with BOM**；现有测试会检查，不要用无 BOM 编辑器保存。
- 源码统一 UTF-8；跑真实评测时设置 `PYTHONIOENCODING=utf-8`，否则 Windows 控制台会因 `✓` 等字符报 GBK 编码错误。
- 不要求管理员权限；安装器按当前用户装到 `%LOCALAPPDATA%\Dafeiyu`。

## 3. 测试

- 测试统一放 `tests/`，用 `tmp_path` 建临时库；不要读写仓库里的 `data/`。
- 单元测试不发网络请求；需要模型时用假 `LLMClient`，需要发送时用假 sender。
- 不要用 `time.sleep` 等异步结果；能冻结时间就冻结，能注入时钟就注入。
- 数据库测试必须关闭 `Store`，避免 Windows 上 WAL 文件占住临时目录。
- `evals/` 是真模型评测，只在本地跑，不进 CI；改人格/提示词/情绪/记忆注入策略时必须跑。
- 改人设后至少跑相关场景，再跑全量；和 `evals/compact.json` 比，出现退化要解释或修掉。
- 评测的 `include` 检查的是“行为意图”，不是逐字复读；确实误伤正常回复时改评测，不要硬逼模型说固定词。
- `tests/test_webui.py` 的本机 socket 偶发 `ConnectionAbortedError` 可以单独重跑一次确认；连续失败要查，不能靠重跑掩盖。

## 4. 数据、隐私与安全

- `data/` 是用户的真实数据：数据库、人格、`ui-token`、WebView2 缓存。任何测试、迁移、清理脚本都不能删它或把它重置成空库。
- `.env` 含 API Key、QQ 号、协议端 token；不要 `cat`/打印/提交，也不要把真实值写进文档或测试。
- 日志里可能有用户原话；不要把 `logs/` 内容贴进提交、issue 或最终回复。
- WebUI/API 只监听 `127.0.0.1`，首次用 `data/ui-token` 换 HttpOnly Cookie；不要为了调试方便放开监听地址、关掉 token 或允许任意顶层导航。
- 没有用户明确要求，不启动/重启真实机器人、不扫码登录、不发 QQ 消息、不访问 QQ 空间/抖音。
- 不执行 `git reset --hard`、`git clean -fd`、删除 `data/` 这类不可逆操作；需要清理先确认路径。

## 5. 数据库与记忆

- 数据库迁移只做**加法**：`ALTER TABLE ... ADD COLUMN`、新建表、新建索引；不要删列、改列名、重建用户表。
- 旧库必须能直接打开；新列用默认值，读取时容忍 `NULL`。
- 走 `Store` 的现有方法，不要在业务代码里散落裸 SQL；测试里的临时查询除外。
- 做梦/整理只允许**新增、合并、调权重**，不允许删除记忆；不要重置做梦游标（`messages_after_id` / `DREAM_BATCH`）。
- 记忆召回、摘要、备份都不能改变原始消息；需要压缩时保留可追溯的 id/时间。
- 改 schema 或记忆策略时补迁移测试：旧库升级后数据还在、重复启动不重复建表。

## 6. 人格、提示词与生活线

- `prompts/system.md` 是每轮固定成本；`tests/test_prompt.py` 限制在 2,700 token 内。加规则前先删重复规则，不要只往上堆。
- 人格 v2 的结构是：静态人设 + 场景 overlay + 确定性 turn plan + 动态范例 + style guard；**不要为了“更生动”增加每轮额外 LLM 调用**。
- 默认人格是 SFW 女友向；不加入 NSFW、模式包、露骨角色扮演。
- 生活线只写有证据的真实事件/意图/作息；没有证据就不编“今天做了什么、房间什么样、窗外天气”。
- 能力边界以 `qqbot/capability.py` 的 `CAPABILITIES` 为准：没开的功能不能说自己做过；新增能力时同时改提示词、拦截规则和测试。
- 新增表情包 tag 必须同时存在于 `assets/seed-images/manifest.json` 和 `prompts/system.md` 的 `可用 tag：` 行；`tests/test_prompt.py` 会检查。
- `prompts/system.md` 是开发/当前使用版，`defaults/prompts/system.md` 是安装包给新用户的出厂版；改默认人格时两份都要同步，别只改一份。
- 改人设后跑 `evals.run --compare evals/compact.json`；`evals/compact.json` 是 2026-11-09 的 22/22 基线，旧 `baseline.json` 只用于历史对比。

## 7. 新增配置项

- 同时改三处：`qqbot/config.py` 的 `Settings` 默认值、环境变量解析、`.env.example` 示例。
- 默认值必须保守：新功能默认关闭，或至少不主动发消息、不产生费用、不扩大权限。
- 旧 `.env` 缺这个键时必须能启动；不要把新键做成必填。
- `.env.example` 一个键一行，注释写在上面；不要写重复键。
- 改了配置行为就补测试：默认值、环境变量覆盖、非法值回退。

## 8. WebUI 与接口

- 前端在 `qqbot/ui/`，由本机 Python 服务提供；不要为了一个小改动引入前端构建链。
- 新接口默认只服务本机，校验 token/Cookie；不要把用户消息、API Key 或完整日志直接返回给未认证请求。
- 改页面后跑 `tests/test_webui.py`；需要看布局时用外壳的 `--screenshot` 生成 `docs/assets/native-ui.png`，不要把临时截图提交。
- 诊断/导出功能不能包含 API Key 明文；导出前脱敏。

## 9. 构建与安装包

- **本项目不用 PyInstaller/Nuitka 冻结 Python**。`大肥鱼.exe` 是 C# WinForms + WebView2 外壳，Python 由 `tools\python` 单独提供。
- 外壳用 Windows 自带的旧版 `csc.exe` 编译，按 .NET Framework 4.x / C# 5 语法写；不要用 `$""`、`?.`、`nameof` 这类新语法。
- 改 `scripts/native/*.cs` 后必须先 `scripts\build-exe.ps1`，再 `installer\build-installer.ps1`；后者只复制现有 exe，不会自动编译外壳。
- 安装包由 NSIS `installer\Dafeiyu.nsi` 生成；`tools\nsis\makensis.exe` 不在仓库里，缺失时构建会直接报错。
- 安装包只允许白名单复制：`qqbot/`、`defaults/prompts`、`scripts/`、`docs/`、`assets/`、`tools/python`、最新的 `tools/NapCat-*`、外壳和三个 WebView2 DLL；绝不复制 `data/`、`logs/`、`.env`、`tests/`。
- `dist/`、`build/`、`tools/` 被 `.gitignore` 忽略；根目录 `大肥鱼.exe` 是跟踪文件，只有明确改外壳时才重建并提交它。
- 安装包未做代码签名，分发时 SmartScreen 会提示未知发布者；不要伪造签名，签名是发布者的外部步骤。
- 改安装器行为后跑 `tests/test_installer.py`，并检查 NSIS 脚本仍是 UTF-8 BOM。

## 10. Git 与提交

- 提交信息用中文 conventional 前缀，例如：
  - `feat: ...`
  - `fix: ...`
  - `test: ...`
  - `docs: ...`
  - `chore: ...`
- 一个提交只做一件事；重构和行为改动尽量分开，方便回滚。
- 用 `git add` 明确列文件，不要习惯性 `git add -A`；确认 `git status` 里没有 `.env`、`data/` 或临时日志。
- 提交前至少跑一次相关测试；改公共逻辑、数据库、提示词时跑全量。
- 不重写历史、不强制推送，除非用户明确要求。

## 11. 快速自检

交付前确认：

- [ ] 改行为的地方有测试；修 bug 有回归测试
- [ ] `tools\python\python.exe -m pytest -q` 全绿
- [ ] 改人格/提示词时跑过 `evals.run --compare evals/compact.json`
- [ ] `prompts/system.md` 和 `defaults/prompts/system.md` 没有意外漂移
- [ ] 没有提交 `.env`、`data/`、`logs/`、`dist/`、`build/`
- [ ] `git status` 干净，提交信息能说明改了什么
