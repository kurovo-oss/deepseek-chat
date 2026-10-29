# 大肥鱼

把 DeepSeek 接进 QQ 私聊，做成一个**有长期记忆、有固定人格**的女儿。

- 产品方向：[docs/PRD.md](docs/PRD.md)
- 工程规格：[docs/MVP.md](docs/MVP.md)
- 记忆设计：[docs/M2-memory.md](docs/M2-memory.md)
- 人格卡：[docs/persona/dafeiyu.md](docs/persona/dafeiyu.md)
- 交互演示（无后端）：[demo/index.html](demo/index.html)

## 当前状态：M1

| 里程碑 | 状态 | 说明 |
|---|---|---|
| M1 能对话 | ✅ 完成 | 私聊收发、白名单、幂等、300 字分段、基础指令 |
| M2 记得住 | ✅ 完成 | 卡片 + 三重权重 + 自动抽取 + 注入 + 原话召回 + 压缩 + 记忆本 + 待确认事实 + 做梦 + 日摘要 |
| M3 会配图 | 🚧 部分 | 图库入库、表情包自动收藏、上下文打标、`/tag` `/img` 已可用；`/save` 收普通照片未做 |
| M4 会主动 | ✅ 完成 | `scheduled`（记忆里的未来事件到期）+ `followup`（72 小时沉默），全套闸门；`/events` `/event` `/proactive`；情绪状态（`/mood`）影响开口时机，并读得出你这条消息的情绪 |
| M5 挂得住 | ✅ 完成 | 开机自启 + 文件日志 + 看门狗 + 失败回复重放（10 分钟内、最多 3 次） |

**注意**：记忆以"卡片"形式注入 system prompt（权重阈值 0.15），不是全量对话检索——
所以它能自然提起旧事，但不会逐字复述几周前的对话。

**每个功能的具体实现方法**见 [docs/PROGRESS.md](docs/PROGRESS.md)。

## 设计取舍

- **零第三方依赖**：只用标准库（`urllib` / `http.server` / `sqlite3`）。这台机器装不上包，少一个依赖就少一个坑。
- **HTTP 上报而非 WebSocket**：`http.server` 就能收事件，不必为长连接引库。
- **单线程队列**：上报线程只入队、立刻回 204（NapCat 的 `baseTimeout` 只有 10 秒，超时就重推），真正的处理在单线程队列里按到达顺序跑。副作用是好的：同一会话不会并发、不会乱序、不会重复建卡。

## 模型

公网 API 只接受两个模型名：**`deepseek-flash`**、**`deepseek-v4-pro`**。
`deepseek-v4.1-flash1017` 是 Harness 内部名，直接调用会返回 400（已实测）。
默认用 `deepseek-flash`。

## 启动与自启

```bat
scripts\start-all.bat     :: 协议端 + 机器人（自启就用这个）
scripts\start-bot.bat     :: 只启机器人
```

两个脚本都会自动找解释器，**优先用项目内的 `tools\python\python.exe`**，
所以即使 DSH 运行时被卸载也不受影响。

日志写在 `logs\bot.log`（滚动，5MB × 4），不再只依赖控制台。

安装开机自启：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
powershell -ExecutionPolicy Bypass -File scripts\uninstall-autostart.ps1   # 卸载
```

> 自启会在 `%APPDATA%\...\Startup\` 放一个 **约 1KB 的快捷方式**（指向 H 盘的
> `start-all.bat`）。这是 Windows 唯一支持的自启方式，无法放在项目盘；
> 除此之外的所有数据（数据库、日志、Python、协议端）都在项目目录里。

## 数据都在哪

**全部在项目目录内**，不往 C 盘写运行时数据：

| 内容 | 位置 |
|---|---|
| 对话与记忆 | `data/chat.db`（SQLite） |
| 日志 | `logs/bot.log`（滚动 5MB × 4） |
| 图库 | `assets/seed-images/`（占位图，可替换） |
| 协议端 | `tools/NapCat-*`（约 140MB） |
| Python 运行时 | `tools/python`（约 340MB，自包含） |
| 自启快捷方式 | `%APPDATA%\...\Startup\dafeiyu-qqbot.lnk`（约 1KB，仅此一项在 C 盘） |

## 目录

```
qqbot/
  config.py       配置解析与校验（自带 .env 解析）
  formatting.py   Markdown 清洗、分段、{{img:tag}} 提取
  images.py       图库：tag → 文件，带越界校验
  llm.py          DeepSeek 客户端（urllib）
  logsetup.py     控制台 + 滚动文件日志
  store.py        SQLite：消息、记忆、用量、游标 + FTS5
  onebot.py       OneBot 11 适配层（解析事件、发送消息/图片）
  engine.py       编排：白名单 → 幂等 → 指令 → 组 prompt → 发送
  main.py         HTTP 服务与命令行
scripts/          下载协议端、启动、自启安装
tools/            NapCat 与项目内 Python（不入库）
data/ logs/       运行时数据（不入库）
```

## 快速开始

```bash
# 1. 配置
copy .env.example .env      # Windows
#    填 ALLOW_USER_ID（你的 QQ 号）与 MODEL_API_KEY

# 2. 自检模型
python -m qqbot --check

# 3. 协议端起来之后，自检连通性（会打印登录的号）
python -m qqbot --check-adapter

# 4. 启动
python -m qqbot
```

启动后监听 `http://127.0.0.1:8080/event`。

### 协议端配置

在 NapCat / LLOneBot 的「网络配置」里：

| 项 | 值 |
|---|---|
| HTTP API 服务 | `http://127.0.0.1:3001` |
| HTTP 事件上报 | `http://127.0.0.1:8080/event` |

两者都只监听 `127.0.0.1`，不要暴露到公网。

### 协议端怎么装

项目自带下载器，直接从官方 Release 拉最新版：

```bat
scripts\get-napcat.bat
```

双击也能跑。它只在需要时才下载 124MB 的完整包（自带 Node），
本机已有 Node 就只下 34MB 的 `NapCat.Shell.zip`，解压到 `tools/NapCat-<版本>/`。

等价的 Python 调用：

```bash
python scripts/get_napcat.py --dry-run   # 只看会下载什么
python scripts/get_napcat.py             # 实际下载
```

> ⚠️ 这台机器 PATH 上的 `python` 是 Microsoft Store 的空壳，直接敲会静默失败。
> 用 `.bat`（它会自己找可用的解释器），或者写全路径。

### 联调顺序

1. `python -m qqbot --check` → 模型通
2. 启动 NapCat / LLOneBot，确认小号已登录
3. 在协议端配好上面两个地址（上报端口要对得上）
4. `python -m qqbot --check-adapter` → 打印出小号昵称和 QQ 号
5. `python -m qqbot` 启动
6. 用你自己的号给小号发一条私聊

第 6 步没反应时，按顺序查：`.env` 里 `ALLOW_USER_ID` 是否填了自己的 QQ 号 →
协议端是否真的 POST 到了 `/event`（`GET /health` 能通说明我们的服务在）→
`data/chat.db` 的 `messages` 表里有没有落库（有落库但 `send_state='failed'` 就是发送端的问题）。

## 指令

| 指令 | 作用 |
|---|---|
| `/status` | 模型、消息数、记忆数、累计 tokens |
| `/memory` | 查看已存记忆（`add` 加、`edit` 改、`ok 编号` 确认） |
| `/forget <关键词>` | 删除匹配的记忆 |
| `/reset` | 清空当前上下文窗口（原文不删，长期记忆保留） |
| `/mood` | 看她此刻的状态（想念 / 亲近 / 疲惫），以及读到的你的情绪 |
| `/digest [昨天\|周]` | 看那一天的流水摘要 |
| `/export` | 把聊天和记忆导成一份 Markdown |
| `/help` | 指令列表 |

## 测试

```bash
python -m pytest -q
```

覆盖：配置校验、Markdown 清洗与分段、图片 tag 安全、FTS 查询清洗、消息去重、
白名单静默、`/reset` 语义、发送失败标记、HTTP 端点与发送客户端。

## 已知限制

- 只自动收藏**表情包类**图片，普通照片不囤（`/save` 收普通照片未做）
- 配图有冷却：最近 10 条消息内发过图，本次标记直接丢弃
- 图库淘汰只置 `archived`，文件不自动删（用 `/img clean` 手动回收磁盘）
- 重放只补文本、不补图片（历史行里只有文件名，拼错路径会发错图），且限 10 分钟内、最多 3 次
- 做梦只做 `add` / `merge` / `reweight`，**永不删除**；合并 = 归档，卡片仍留在库里
- 抽取和做梦产出的卡片默认**待确认**，注入时单独成块并注明"别当事实说"，`/memory ok 编号` 才转正；
  **同一条事实隔天又被观察到会自动转正**（同一天说两遍不算数）
- 原话召回只取**原文片段**（最多 3 条），不做摘要替代——摘要是索引，原话才是真相
- 无"正在输入"提示——这是刻意的，见 PRD 体验原则
