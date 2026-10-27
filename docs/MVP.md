# MVP 需求与设计（v0.3）

> 状态：草案，待验证项见 §13
> 目标：单人私聊场景下，让 QQ 里的机器人 = 一个带长期记忆、会主动开口、能发图的 DeepSeek 对话体

**v0.2 → v0.3 变更**：新增主动消息（事件驱动）与图片发送；记忆类型收敛为事实/偏好/事件；新增回复长度约束、分段发送规则；明确禁用"正在输入"提示。

**v0.1 → v0.2 变更**：补部署形态验收、心跳告警、发送重放、遗忘语义；修正"20 轮 vs token 预算"；补空闲抽取与 FTS 查询清洗；新增 `.gitignore`/密钥约束与测试计划。

## 1. 定位

一款**只服务单个用户**的 QQ 私聊助手：主号私聊机器人小号，机器人用自己的 DeepSeek 模型回答，**记得住过去的事**，并且会**在合适的时机主动找你说话**。

## 2. 目标 / 非目标

**目标**

- 私聊一条消息 → 一条回答，长期可用（挂机不掉线、重启不丢记忆）
- 记忆是**长期**的：事实、偏好、事件都能被记住并在需要时想起
- **会主动开口**：基于记忆中提取出的未来事件与长期沉默，低频主动找你
- **会发图**：从本地图库按语义挑选表情包/图片，不调用生图模型
- 回复克制：单条不超过 300 字，长内容分段发，不显示"正在输入"
- 零限额：不设每日对话条数（但主动消息有独立闸门）

**非目标（MVP 明确不做）**

- 群聊、多人会话、多用户
- 用户发来的图片/语音/文件的解析（只做"机器人发图"，不做"机器人看图"）
- AI 生图（不接第三个模型）
- Web 管理后台、注册登录、计费充值
- 多机器人、插件系统、人格市场
- 联网搜索、RAG 知识库

## 3. 场景与账号

```
主号 B（用户本人） ──私聊──▶ 机器人小号 A（挂协议端） ──HTTPS──▶ DeepSeek API
                                      │
                                      ├──▶ 本地图库 data/images/
                                      └──▶ 事件调度（主动消息）
```

- QQ 不支持给自己发私聊消息，因此**必须有一个专用小号 A** 作为机器人账号。**（已就绪）**
- 主号与小号互加好友，避免消息落入"陌生人消息"折叠。
- 主号 B 的 QQ 号作为唯一 `ALLOW_USER_ID`；其他任何人私聊一律静默忽略。
- 只处理私聊（C2C）消息，群消息直接丢弃。

## 4. 功能范围（P0）

| 编号 | 功能 | 验收标准 |
|---|---|---|
| F1 | 私聊消息收发 | B 发文本 → A 回文本；非 B 发送的消息被忽略 |
| F2 | 模型调用 | 调 `MODEL_ID`，超时 60s，失败退避重试 1 次后回固定话术 |
| F3 | 消息持久化 | 每条消息落 SQLite，永不删除 |
| F4 | 短期上下文 | 按 token 预算从最新往前装载（§5.2） |
| F5 | 长期记忆 | 事实 / 偏好 / 事件三类，自动抽取并注入（§5.3） |
| F6 | 历史检索 | 超出窗口的老内容可被关键词召回 |
| F7 | 记忆整理 | 触发时同时产出记忆条目与原文摘要（§5.3） |
| F8 | 指令 | `/reset`、`/memory [rm id]`、`/forget <关键词>`、`/status` |
| F9 | 幂等与重放 | 按 `qq_msg_id` 去重；未发送成功的回复重连后重放 |
| F10 | 回复长度与分段 | 单条 ≤300 字；超长按句边界切分，段间间隔 1.5s（§5.7） |
| F11 | 心跳告警 | 断连超 5 分钟或连续 3 次调用失败即告警（§7） |
| F12 | 主动消息 | 事件到期或长期沉默时主动开口；受每日上限与静默时段约束（§6） |
| F13 | 图片发送 | 模型以 `{{img:tag}}` 标记挑图，从本地图库发送（§6.4） |
| F14 | 无打字提示 | 不调用任何 input_status / typing action |

## 5. 长期记忆设计（核心）

DeepSeek API 无状态，**"长期记忆"必须自己实现**。三层结构：

```
┌─ L1 长期记忆笔记   全量注入   ≤1500 tokens
├─ L2 最近对话原文   滑动窗口   ≤4000 tokens
└─ L3 历史消息库     检索召回   ≤800  tokens
```

加系统提示词 ≤800，单次请求约 **7K tokens**。

**token 估算**：不引 tokenizer 依赖，`est(text) = ceil(len(text) * 0.6)`，允许 ±15% 误差。

### 5.1 数据模型

```sql
CREATE TABLE messages (
  id          INTEGER PRIMARY KEY,
  session_id  TEXT NOT NULL,
  qq_msg_id   TEXT UNIQUE,
  role        TEXT NOT NULL,                     -- user / assistant
  content     TEXT NOT NULL,
  send_state  TEXT NOT NULL DEFAULT 'sent',      -- pending / sent / failed
  created_at  INTEGER NOT NULL
);

CREATE TABLE memories (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,                     -- fact / preference / event
  content     TEXT NOT NULL,
  source_from INTEGER,
  source_to   INTEGER,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);

-- 主动消息的事件队列，由记忆整理阶段写入
CREATE TABLE proactive_events (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,                     -- scheduled / followup
  content     TEXT NOT NULL,                     -- 要说什么（给模型的素材）
  trigger_at  INTEGER NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',   -- pending / sent / failed / cancelled
  created_at  INTEGER NOT NULL
);

CREATE TABLE summaries (
  id          INTEGER PRIMARY KEY,
  covers_from INTEGER NOT NULL,
  covers_to   INTEGER NOT NULL,
  content     TEXT NOT NULL,
  created_at  INTEGER NOT NULL
);

CREATE TABLE usage_log (
  id         INTEGER PRIMARY KEY,
  model      TEXT NOT NULL,
  tokens_in  INTEGER NOT NULL,
  tokens_out INTEGER NOT NULL,
  purpose    TEXT NOT NULL,                      -- chat / memory_extract / proactive
  ok         INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE cursors (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

CREATE VIRTUAL TABLE messages_fts USING fts5(
  content, content='messages', content_rowid='id', tokenize='trigram'
);
-- 配套 AFTER INSERT / UPDATE / DELETE 触发器保持同步
```

### 5.2 上下文组装算法

按顺序组装，各自独立预算，**超预算截断而非整体放弃**：

1. **系统提示**：内置默认人格，`SYSTEM_PROMPT_FILE` 存在则覆盖；超 800 截断。
2. **L1 记忆**：按 `updated_at` 倒序累加，超 1500 停止；三类记忆合并排序，不按类型分块。
3. **L3 检索**：以用户最新消息为查询，清洗后检索 top 5；单条截 160 字，合计超 800 停止，每条前置日期。
4. **L2 窗口**：从最新往前取，user/assistant 成对，超 4000 或 20 轮停止。
5. 窗口为空（`/reset` 后）时只发 L1 + L3。

### 5.3 记忆整理（同时产出记忆、事件、摘要）

**触发（任一）**：距上次满 8 轮 / 空闲 10 分钟 / `/reset` 前 / 进程优雅退出前。

**输入**：`cursors.memory_last_id` 之后的消息。
**输出**：严格 JSON

```json
{
  "memories": [{"op": "add|update", "id": 12, "kind": "fact|preference|event", "content": "..."}],
  "events":   [{"kind": "scheduled", "content": "...", "trigger_at": "2026-11-03T09:00:00+08:00"}],
  "summary":  "这段对话的要点，150 字以内"
}
```

**约束**

- 只允许 `add` / `update`，**禁止自动 delete**；删除仅 `/forget`、`/memory rm`。
- `op=update` 且 id 不存在时降级为 `add`。
- `kind=event` 且带未来时间 → 同时写入 `proactive_events`（`kind=scheduled`）。
- JSON 解析失败整批丢弃，游标不推进，下周期重试。
- 事务内写 `memories` + `proactive_events` + `summaries` + 推进游标。
- 记忆超 80 条时做一次合并去重（同义保留最新）。

### 5.4 遗忘语义

| 指令 | 行为 | 用户预期 |
|---|---|---|
| `/reset` | 清空 L2 窗口，**不动** memories | 语气重开，不等于遗忘 |
| `/forget <关键词>` | 删除匹配记忆，返回条数 | 真正忘掉 |
| `/memory rm <id>` | 按 id 精确删除 | 精细修正 |

`/reset` 回复必须明说"已清空上下文，长期记忆保留"。
`/forget` 同时取消相关 `proactive_events`（否则"忘掉了"还会拿它来主动找你）。

### 5.5 检索与查询清洗

1. 去掉 FTS5 特殊字符 `" * ( ) : ^ -`
2. 按空白与中英文标点切分
3. 保留长度 ≥2 的片段，每段双引号包裹
4. 以 `OR` 连接；结果为空则跳过检索
5. 任何查询异常捕获为"无召回"，不影响主流程

### 5.6 隐私

- 记忆、原文、图库均存本地，不落第三方。
- `data/` 禁止入库（§9）。
- `/memory` 可查看全部记忆，保证可审计。

### 5.7 回复长度与分段（F10）

- **软上限 300 字**：写入系统提示词「回答尽量控制在 300 字内，能短则短」。
- **硬切分**：模型仍超长时，按 `。！？；\n` 句边界聚合到 ≤300 字；单句超 300 则硬切。
- **段间间隔 1.5s**，避免瞬时连发触发风控。
- 图片单独成条发送，不拼接在长文本中间。
- 不做"正在输入"：不调用 `set_input_status` 一类 action；配置项 `TYPING_INDICATOR=false` 仅作显式占位。

## 6. 主动消息与图片

### 6.1 触发源

| 类型 | 来源 | 说明 |
|---|---|---|
| `scheduled` | 记忆整理识别出的未来事件 | 如"下周三面试"→ 到期问候 |
| `followup` | 长期沉默 + 记忆中正在做的事 | 72 小时无对话，且 7 天内未主动过 |

`followup` 必须基于 memories 里的真实条目生成，**禁止模型自由发挥编造话题**。

### 6.2 闸门（缺一不可）

- 每日主动 ≤ **3 条**
- 仅在 **09:00–23:00** 发送（`PROACTIVE_QUIET_HOURS`）
- 两条间隔 ≥ **4 小时**
- 用户最近 **30 分钟内**有对话 → 不发（正在聊就别插话）
- `followup` 每周 ≤ 1 次

### 6.3 失败处理

主动消息**发送失败不重试**——重试等于骚扰。失败置 `failed` 记日志，不补发。

### 6.4 图片发送

- 图库：`data/images/`，索引 `data/images/index.json`：

```json
{ "cat": ["cat_01.jpg", "cat_02.png"], "happy": ["smile.gif"] }
```

- 模型在回复中写 `{{img:tag}}`，编排层解析并移除标记，从该 tag 下**随机**取一张发送。
- tag 不存在 → 静默忽略标记，**不报错、不影响文本**。
- **安全**：tag 与文件名仅允许 `[A-Za-z0-9_-]`；发送前校验 `realpath` 必须位于图库目录内，杜绝路径穿越。
- 仅支持 `jpg / png / gif / webp`，单文件 ≤5MB。
- 图片与文本的关系：图片先发或后发均可，但**不打断**分段文本的顺序。

## 7. 可靠性设计

| 场景 | 处理 |
|---|---|
| 回复已入库但发送失败 | `send_state='failed'`，重连后重放 `pending/failed`（仅 10 分钟内） |
| 协议端断线 | 指数退避重连（1→2→4→…→60s 封顶） |
| 心跳 | 每 60s 探测；连续失败超 5 分钟告警 |
| 模型连续失败 | 连续 3 次告警，并回"我这边出问题了" |
| 主动消息失败 | 不重试，直接置 failed（§6.3） |
| 进程启动 | 恢复 pending 队列 + 探测模型 + 扫描到期事件 |

## 8. 配置项

```env
QQ_ADAPTER=llonebot
ADAPTER_ENDPOINT=http://127.0.0.1:3001
ADAPTER_TOKEN=
ALLOW_USER_ID=
MODEL_ID=deepseek-v4.1-flash1017
MODEL_BASE_URL=https://api.deepseek.com
MODEL_API_KEY=
SYSTEM_PROMPT_FILE=prompts/system.md

HISTORY_MAX_TURNS=20
WINDOW_TOKEN_BUDGET=4000
MEMORY_TOKEN_BUDGET=1500
RETRIEVE_TOP_K=5
EXTRACT_EVERY_TURNS=8
IDLE_EXTRACT_MINUTES=10

REPLY_MAX_CHARS=300
SEGMENT_DELAY_MS=1500
TYPING_INDICATOR=false

IMAGE_LIB_DIR=data/images
IMAGE_MAX_BYTES=5242880

PROACTIVE_ENABLED=true
PROACTIVE_DAILY_MAX=3
PROACTIVE_MIN_GAP_MINUTES=240
PROACTIVE_QUIET_HOURS=23:00-09:00
PROACTIVE_SILENCE_HOURS=72

HEARTBEAT_INTERVAL=60
ALERT_WEBHOOK=
DB_PATH=data/chat.db
```

## 9. 工程约束与安全

**仓库只存代码与文档，绝不存数据与密钥。** 首日即建 `.gitignore`：

```gitignore
.env
data/
*.db
*.db-wal
*.db-shm
logs/
.venv/
__pycache__/
```

- 入库 `.env.example`（只有键名），`.env` 永不入库。
- 启动校验 `.env` 存在且必填项非空，缺失即退出并打印缺失项。
- API Key 不得出现在日志与异常栈（统一打码）。
- `data/` 仅本机用户可读写；协议端登录态放 `data/adapter/`，定期备份。

## 10. 测试计划

| 测试 | 覆盖点 |
|---|---|
| 回复切分 | 去 Markdown；≤300 字切分；不产生空段；单句超长可硬切；图片标记不参与文本切分 |
| 图片发送 | tag 合法匹配；不存在 tag 静默忽略；`../` 路径穿越被拒；超 5MB 被拒 |
| FTS 清洗 | 含 `- * " (` 的查询不抛异常；短查询返回空 |
| 白名单 | 非 `ALLOW_USER_ID` 静默丢弃；群消息丢弃 |
| 幂等 | 同 `qq_msg_id` 二次投递不产生第二次回答 |
| 预算截断 | 超预算从最新往前截；user/assistant 不拆对 |
| 遗忘语义 | `/reset` 不动 memories；`/forget` 删记忆并取消关联事件 |
| 主动闸门 | 超日上限不发；静默时段不发；间隔不足不发；活跃期不发；失败不重试 |

集成测试用 fake LLM + fake transport + fake clock，不依赖真实 QQ、API 和系统时间。

## 11. 里程碑

| 阶段 | 内容 | 可验收 |
|---|---|---|
| M1 | 收私聊 → 调模型 → 回文本（含白名单、幂等、切分） | 能对话 |
| M2 | 记忆三层 + 检索 + 遗忘指令 | 重启后记得住 |
| M3 | 图片发送 + 长度/分段打磨 | 会配图 |
| M4 | 主动消息 + 闸门 + 心跳告警 | 会主动找人 |
| M5 | 自启、重放、灰度观察一周 | 可长期挂机 |

## 12. 技术风险与验证项

| # | 风险 | 验证方式 | 不通过的退路 |
|---|---|---|---|
| R1 | `deepseek-v4.1-flash1017` 可能非公网可用 ID | 启动 1-token 探测 | 回落 `deepseek-chat`，`/status` 显示实际模型 |
| R2 | ~~QQ 无法给自己发私聊~~ | 已解决：专用小号已注册 | — |
| R3 | 协议端封号风险 | 小号灰度一周，仅私聊 | 改用官方 Bot 平台（仅封号后启用） |
| R4 | 中文 FTS5 召回效果 | 20 条历史消息人工召回 | 本地 embedding（bge-m3） |
| R5 | 长对话 429 / 超时 | 连续 50 轮压测 | 串行 + 指数退避 |
| R6 | Windows 休眠/更新杀进程 | 模拟重启验证自启 | 计划任务 + 拉起脚本 |
| R7 | **主动消息触发风控** | 观察一周是否被限流 | 降到每周 1 条或 `PROACTIVE_ENABLED=false` |
| R8 | 模型挑图 tag 幻觉 | 统计 tag 命中率 | 无命中时回退为不发图，仅文本 |

## 13. Definition of Done

- [ ] 连续对话 50 轮不崩溃、不重复回答
- [ ] 重启进程后仍能答出 3 天前聊过的具体细节
- [ ] 只聊 2 轮就关闭，10 分钟后记忆里已有内容
- [ ] 事实 / 偏好 / 事件三类记忆都能被抽取，`/memory` 可见
- [ ] 回复长度 ≤300 字，超长内容分段到达且间隔约 1.5s
- [ ] 全程无"正在输入"状态
- [ ] 说"给我看看猫"，收到图库中随机一张猫图；`{{img:不存在}}` 不报错
- [ ] 连续 7 天主动消息不超每日 3 条、不在静默时段发送
- [ ] 断网 2 分钟恢复后，期间回复被重放，不丢不重
- [ ] 陌生 QQ 无任何回复
- [ ] 重启 Windows 后无需人工自动上线
- [ ] `/status` 累计 token 与 `usage_log` 一致
- [ ] 仓库 `git status` 无 `.env` / `data/` 痕迹

## 14. 接入方式决策（已定稿）

**结论：OneBot 协议端 + 专用小号，不使用官方 QQ 开放平台 Bot。**
**技术栈：Python + 轻量自研（不引 NoneBot），本机 Windows 常开。**

NapCat 与 LLOneBot 同为 OneBot 11，切换只改 `ADAPTER_ENDPOINT` 与 token，不影响业务代码。

## 15. 账号侧准备清单

- [ ] 小号与主号互加好友
- [ ] 小号资料完整、绑定手机、独立密码
- [ ] 小号先正常使用数日再挂协议端
- [ ] 协议端仅监听 `127.0.0.1`，token 强随机
- [ ] 备份登录态文件
- [ ] **主动消息必须克制**：事件驱动、每日 ≤3、失败不重试——这是小号最可能被封的诱因
