# 大肥鱼

把 DeepSeek 接进 QQ 私聊，做成一个**有长期记忆、有固定人格**的女儿。

- 产品方向：[docs/PRD.md](docs/PRD.md)
- 工程规格：[docs/MVP.md](docs/MVP.md)
- 人格卡：[docs/persona/dafeiyu.md](docs/persona/dafeiyu.md)
- 交互演示（无后端）：[demo/index.html](demo/index.html)

## 当前状态：M1

| 里程碑 | 状态 | 说明 |
|---|---|---|
| M1 能对话 | ✅ 完成 | 私聊收发、白名单、幂等、300 字分段、基础指令 |
| M2 记得住 | 🚧 部分 | 表结构与 `/memory` `/forget` 已就绪，**自动抽取与注入尚未实现** |
| M3 会配图 | 🚧 部分 | 占位图库发图 + 冷却已可用；表情包收藏未做 |
| M4 会主动 | ⬜ 未开始 | 主动消息与闸门 |
| M5 挂得住 | ⬜ 未开始 | 自启、发送重放 |

**注意**：当前版本回复里不会带记忆——它记得住"存下来了"，但还没把记忆塞进 prompt。

## 设计取舍

- **零第三方依赖**：只用标准库（`urllib` / `http.server` / `sqlite3`）。这台机器装不上包，少一个依赖就少一个坑。
- **HTTP 上报而非 WebSocket**：`http.server` 就能收事件，不必为长连接引库。
- **同步实现**：单用户、低并发，`ThreadingHTTPServer` + 每会话串行足够；过早异步化只会增加调试成本。

## 模型

公网 API 只接受两个模型名：**`deepseek-flash`**、**`deepseek-v4-pro`**。
`deepseek-v4.1-flash1017` 是 Harness 内部名，直接调用会返回 400（已实测）。
默认用 `deepseek-flash`。

## 目录

```
qqbot/
  config.py       配置解析与校验（自带 .env 解析）
  formatting.py   Markdown 清洗、分段、{{img:tag}} 提取
  llm.py          DeepSeek 客户端（urllib）
  store.py        SQLite：消息、记忆、用量、游标 + FTS5
  onebot.py       OneBot 11 适配层（解析事件、发送消息）
  engine.py       编排：白名单 → 幂等 → 指令 → 组 prompt → 发送
  main.py         HTTP 服务与命令行
tests/            97 用例，fake LLM / fake sender / 真实 socket
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
| `/memory` | 查看已存记忆 |
| `/forget <关键词>` | 删除匹配的记忆 |
| `/reset` | 清空当前上下文窗口（原文不删，长期记忆保留） |
| `/help` | 指令列表 |

## 测试

```bash
python -m pytest -q
```

覆盖：配置校验、Markdown 清洗与分段、图片 tag 安全、FTS 查询清洗、消息去重、
白名单静默、`/reset` 语义、发送失败标记、HTTP 端点与发送客户端。

## 已知限制

- 只处理**文本**消息；收到的图片/表情会被忽略（表情包收藏未做）
- 发图功能可用，但图库目前只有 `assets/seed-images/` 里的占位图
- 配图有冷却：最近 10 条消息内发过图，本次标记直接丢弃
- 记忆不会自动注入对话（M2 补）
- 发送失败只标记 `failed`，不重放（M5 补）
- 无"正在输入"提示——这是刻意的，见 PRD 体验原则
