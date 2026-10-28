# MVP 需求与设计（v0.4）

> 状态：草案，待验证项见 §13
> 目标：单人私聊场景下，让 QQ 里的机器人 = 一个带长期记忆、会从你的表情包里学习、会主动开口、能发图的 DeepSeek 对话体

**v0.3 → v0.4 变更**：新增表情包收藏与自建图库（推翻 v0.3 的"不解析用户图片"）；图库元数据从 JSON 文件改为 SQLite 表；新增打标策略、容量淘汰、路径可见性三个风险项。

**v0.2 → v0.3 变更**：新增主动消息与图片发送；记忆类型收敛为事实/偏好/事件；补回复长度与分段规则；禁用"正在输入"。

**v0.1 → v0.2 变更**：补部署验收、心跳告警、发送重放、遗忘语义；修正预算规则；补空闲抽取与 FTS 清洗；新增 `.gitignore` 与测试计划。

## 1. 定位

一款**只服务单个用户**的 QQ 私聊助手：主号私聊机器人小号，机器人用自己的 DeepSeek 模型回答，记得住过去的事，会主动找你说话，并且**用你教它的表情包跟你聊天**。

## 2. 目标 / 非目标

**目标**

- 私聊一条消息 → 一条回答，长期可用（挂机不掉线、重启不丢记忆）
- 记忆是**长期**的：事实、偏好、事件都能被记住并在需要时想起
- **会主动开口**：基于记忆中提取的未来事件与长期沉默，低频主动找你
- **会发图**：优先从自建图库（含你发过的表情包）挑图，不打生图 API
- **会学你的表情包**：你发的表情包被收藏、打标、在合适场景复用
- 回复克制：单条 ≤300 字，长内容分段，不显示"正在输入"
- 零限额：不设每日对话条数（主动消息有独立闸门）

**非目标（MVP 明确不做）**

- 群聊、多人会话、多用户
- 通用图片理解（不做"这是什么东西"的视觉问答）
- AI 生图（不接生图模型）
- 语音、文件、视频
- Web 管理后台、注册登录、计费充值
- 多机器人、插件系统、人格市场
- 联网搜索、RAG 知识库

## 3. 场景与账号

```
主号 B ──私聊──▶ 小号 A（协议端）
                    │
                    ├──▶ DeepSeek API
                    ├──▶ 自建图库 data/images/（自有 + B 发来的表情包）
                    └──▶ 事件调度（主动消息）
```

- QQ 不支持给自己发私聊消息，必须有专用小号 A。（已就绪）
- 主号与小号互加好友，避免消息进入"陌生人消息"。
- 主号 B 的 QQ 号作为唯一 `ALLOW_USER_ID`，其他人一律静默忽略。
- 只处理私聊（C2C）消息，群消息直接丢弃。

## 4. 功能范围（P0）

| 编号 | 功能 | 验收标准 |
|---|---|---|
| F1 | 私聊消息收发 | B 发文本 → A 回文本；非 B 的消息被忽略 |
| F2 | 模型调用 | 调 `MODEL_ID`，超时 60s，失败退避重试 1 次后回固定话术 |
| F3 | 消息持久化 | 每条消息落 SQLite，永不删除 |
| F4 | 短期上下文 | 按 token 预算从最新往前装载（§5.2） |
| F5 | 长期记忆 | 事实 / 偏好 / 事件三类，自动抽取并注入（§5.3） |
| F6 | 历史检索 | 超出窗口的老内容可被关键词召回 |
| F7 | 记忆整理 | 触发时产出记忆条目、主动事件、原文摘要（§5.3） |
| F8 | 指令 | `/reset`、`/memory [rm id]`、`/forget`、`/status`、`/tag`、`/img` |
| F9 | 幂等与重放 | 按 `qq_msg_id` 去重；未发送成功的回复重连后重放 |
| F10 | 回复长度与分段 | 单条 ≤300 字，超长按句边界切分，段间 1.5s（§5.7） |
| F11 | 心跳告警 | 断连超 5 分钟或连续 3 次失败即告警（§7） |
| F12 | 主动消息 | 事件到期或长期沉默时主动开口，受闸门约束（§6.1–6.3） |
| F13 | 图片发送 | 模型以 `{{img:tag}}` 标记挑图发送（§6.4） |
| F14 | 无打字提示 | 不调用任何 input_status / typing action |
| F15 | 表情包收藏 | 你发的表情包自动入库、去重、打标（§6.5） |
| F16 | 图库自管理 | `/tag`、`/img list`、`/img rm` 可查看与修正 |

## 5. 长期记忆设计（核心）

三层结构：

```
┌─ L1 长期记忆笔记   全量注入   ≤1500 tokens
├─ L2 最近对话原文   滑动窗口   ≤4000 tokens
└─ L3 历史消息库     检索召回   ≤800  tokens
```

加系统提示词 ≤800，单次请求约 **7K tokens**。
**token 估算**：`est(text) = ceil(len(text) * 0.6)`，允许 ±15% 误差。

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

CREATE TABLE proactive_events (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,                     -- scheduled / followup
  content     TEXT NOT NULL,
  trigger_at  INTEGER NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',   -- pending / sent / failed / cancelled
  created_at  INTEGER NOT NULL
);

-- 图库元数据（文件存磁盘，索引存这里）
CREATE TABLE images (
  id            INTEGER PRIMARY KEY,
  sha256        TEXT NOT NULL UNIQUE,            -- 内容去重
  rel_path      TEXT NOT NULL,                   -- 相对 IMAGE_LIB_DIR
  mime          TEXT NOT NULL,
  byte_size     INTEGER NOT NULL,
  tags          TEXT NOT NULL DEFAULT '[]',      -- JSON 数组，如 ["cat","happy"]
  source        TEXT NOT NULL,                   -- builtin / user_sent
  source_msg_id TEXT,
  status        TEXT NOT NULL DEFAULT 'active',  -- active / archived
  use_count     INTEGER NOT NULL DEFAULT 0,
  last_used_at  INTEGER,
  created_at    INTEGER NOT NULL
);
CREATE INDEX idx_images_tags ON images(status, last_used_at);

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

CREATE TABLE cursors (k TEXT PRIMARY KEY, v TEXT NOT NULL);

CREATE VIRTUAL TABLE messages_fts USING fts5(
  content, content='messages', content_rowid='id', tokenize='trigram'
);
-- 配套 AFTER INSERT / UPDATE / DELETE 触发器
```

### 5.2 上下文组装算法

1. **系统提示**：内置默认人格，`SYSTEM_PROMPT_FILE` 存在则覆盖；超 800 截断。
2. **L1 记忆**：按 `updated_at` 倒序累加，超 1500 停止，三类混排不分块。
3. **L3 检索**：以用户最新消息为查询，清洗后取 top 5；单条截 160 字，合计超 800 停止，每条前置日期。
4. **L2 窗口**：从最新往前取，user/assistant 成对，超 4000 或 20 轮停止。
5. 窗口为空（`/reset` 后）时只发 L1 + L3。

### 5.3 记忆整理

**触发（任一）**：满 8 轮 / 空闲 10 分钟 / `/reset` 前 / 进程优雅退出前。

**输入**：`cursors.memory_last_id` 之后的消息；**输出**严格 JSON：

```json
{
  "memories": [{"op": "add|update", "id": 12, "kind": "fact|preference|event", "content": "..."}],
  "events":   [{"kind": "scheduled", "content": "...", "trigger_at": "2026-11-03T09:00:00+08:00"}],
  "summary":  "150 字以内"
}
```

- 只允许 `add` / `update`，**禁止自动 delete**；删除仅 `/forget`、`/memory rm`。
- `update` 的 id 不存在时降级为 `add`。
- `kind=event` 且带未来时间 → 同时写 `proactive_events`。
- JSON 解析失败整批丢弃、游标不推进、下周期重试。
- 事务内写 memories + events + summaries + 推进游标。
- 超 80 条时合并去重（同义保留最新）。

### 5.4 遗忘语义

| 指令 | 行为 |
|---|---|
| `/reset` | 清 L2 窗口，不动 memories；回复明说"长期记忆保留" |
| `/forget <关键词>` | 删匹配记忆，并取消关联的 `proactive_events` |
| `/memory rm <id>` | 按 id 精确删除 |

### 5.5 检索与查询清洗

1. 去掉 FTS5 特殊字符 `" * ( ) : ^ -`
2. 按空白与中英文标点切分，保留长度 ≥2 的片段
3. 每段双引号包裹，`OR` 连接；为空则跳过检索
4. 任何异常捕获为"无召回"，不影响主流程

### 5.6 隐私

- 记忆、原文、图库均存本地，不落第三方。
- `data/` 禁止入库（§9）。
- `/memory`、`/img list` 可审计全部内容。

### 5.7 回复长度与分段

- 软上限 **300 字**写进系统提示词；硬切分按 `。！？；\n` 聚合，单句超长才硬切。
- 段间间隔 **1.5s**，避免瞬时连发触发风控。
- 图片单独成条，不插入文本段中间。
- 不做"正在输入"：不调用 `set_input_status`。

## 6. 主动消息与图库

### 6.1 主动消息触发源

| 类型 | 来源 |
|---|---|
| `scheduled` | 记忆整理识别出的未来事件（"下周三面试"→ 到期问候） |
| `followup` | 72 小时无对话，且 7 天内未主动过，取 memories 中真实条目开口 |

`followup` 必须基于 memories 真实内容，**禁止模型自由编造话题**。

### 6.2 闸门（缺一不可）

- 每日主动 ≤ **3 条**
- 仅 **09:00–23:00**
- 两条间隔 ≥ **4 小时**
- 用户最近 **30 分钟**有对话 → 不发
- `followup` 每周 ≤ 1 次

### 6.3 失败处理

主动消息发送失败**不重试**，置 `failed` 记日志。重试等于骚扰。

### 6.4 发图流程

- 模型在回复中写 `{{img:tag}}`，编排层解析并移除标记。
- 从 `images` 取 `status='active'` 且 tags 含该 tag 的记录，优先 `use_count` 低者随机取样。
- tag 无匹配 → 静默忽略标记，只发文本，**不报错**。
- 安全：tag 仅允许 `[A-Za-z0-9_-]`；`rel_path` 拼成绝对路径后必须 `realpath` 校验在图库目录内。
- 仅 `jpg/png/gif/webp`，单文件 ≤5MB。
- **路径可见性**：协议端若在 Docker 内，需把图库目录挂载到相同路径；否则 `IMAGE_SEND_MODE=base64` 用 base64 传输。

### 6.5 表情包收藏（v0.4 新增）

**收什么**：只自动收藏**表情包类**图片（OneBot `image` 段 `sub_type=1`，或 `summary` 含"表情/闪照"等标记）。普通照片不动——避免把真人照片囤进图库。
普通图片要收，需显式 `/save`（回复该图）。

**怎么收**：

1. 从消息段拿到图片（URL 或协议端本地路径），下载到内存
2. 计算 `sha256`，已存在则只更新 `last_used_at`，不重复存
3. 存为 `data/images/<sha256[:2]>/<sha256>.<ext>`
4. **打标**（见下）→ 写 `images` 行
5. 回复一句话（如"这张我存下了"），不打断正常对话

**打标策略（关键约束）**

> ⚠️ DeepSeek 的 chat 接口**不保证支持图片输入**。机器人无法"看懂"表情包，所以 MVP 的打标只能靠两条路：

- **上下文推断**：把该图前后各 2 条消息喂给模型，让它猜 1–3 个 tag（如你发了张猫 + 说了"累"→ 可能打 `tired`）。免费、零依赖，但**准确率一般**。
- **人工命名**：`/tag <名称> [tag2]` 对最近收藏的一张打标，或 `/img list` 找到 id 后 `/img tag <id> <名称>`。这是唯一可靠的路径。

优先级：人工标签 > 上下文推断标签；两者并存，发送时按任意 tag 命中。

**图库来源**

| 来源 | 位置 | 是否入库 |
|---|---|---|
| 内置占位图库 | `assets/seed-images/`（6 张 320×320 PNG + `manifest.json`） | ✅ 入库，体积约 30KB |
| 你发的表情包 | `data/images/<sha256[:2]>/<sha256>.<ext>` | ❌ 不入库 |
| 手动放入 | 同上，`/img add` 或直接拷贝后重建索引 | ❌ 不入库 |

首次启动（M3）把 `assets/seed-images/` 导入 `data/images/` 并写入 `images` 表（`source='builtin'`），之后占位图可以随时 `/img rm` 掉。占位图仅用于打通链路，正式使用应换成真实表情包。

**容量与淘汰**：总量 ≤ `IMAGE_MAX_COUNT`（默认 500）。超出时按"无人工标签 → `last_used_at` 最旧"顺序置 `archived`（文件保留，不再发送）。

**指令**

| 指令 | 作用 |
|---|---|
| `/tag <名称...>` | 给最近收藏的一张打标 |
| `/img list [tag]` | 列出图库（默认最近 10 张） |
| `/img tag <id> <名称...>` | 精确打标 |
| `/img rm <id>` | 删除 |

## 7. 可靠性设计

| 场景 | 处理 |
|---|---|
| 回复已入库但发送失败 | `send_state='failed'`，重连后重放（仅 10 分钟内） |
| 协议端断线 | 指数退避重连（1→2→4→…→60s 封顶） |
| 心跳 | 每 60s 探测，连续失败超 5 分钟告警 |
| 模型连续失败 | 3 次告警并回"我这边出问题了" |
| 主动消息失败 | 不重试 |
| 图片下载失败 | 放弃本次收藏，记日志，不影响对话 |
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
IMAGE_MAX_COUNT=500
IMAGE_SEND_MODE=path          # path | base64
IMAGE_COLLECT_AUTO=true       # 自动收藏表情包
IMAGE_TAG_BY_CONTEXT=true     # 用上下文推断 tag

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

- `data/` 含聊天记录、图库、协议端登录态，**禁止入库**。
- 入库 `.env.example`（仅键名）；启动校验必填项，缺失即退出。
- API Key 在日志与异常栈中统一打码。
- 图库文件按 sha256 命名，杜绝用户输入直接构成路径。

## 10. 测试计划

| 测试 | 覆盖点 |
|---|---|
| 回复切分 | 去 Markdown；≤300 字切分；无空段；单句超长硬切；`{{img:x}}` 不参与计数 |
| 图片发送 | tag 命中；无命中静默忽略；`../` 穿越被拒；超 5MB 被拒；非白名单扩展名被拒 |
| 表情包收藏 | 同图二次发送不重复入库（sha256 去重）；非表情类图片不自动收藏；下载失败不影响对话 |
| 打标 | 非法字符 tag 被清洗；人工标签优先于推断标签 |
| 容量淘汰 | 超 `IMAGE_MAX_COUNT` 时淘汰无标签 + 最久未用 |
| FTS 清洗 | 含 `- * " (` 不抛异常；短查询返回空 |
| 白名单 | 非 `ALLOW_USER_ID` 静默丢弃；群消息丢弃 |
| 幂等 | 同 `qq_msg_id` 二次投递不产生第二次回答 |
| 预算截断 | 超预算从最新往前截；user/assistant 不拆对 |
| 遗忘语义 | `/reset` 不动 memories；`/forget` 删记忆并取消事件 |
| 主动闸门 | 超日上限/静默时段/间隔不足/活跃期均不发；失败不重试 |

集成测试用 fake LLM + fake transport + fake clock + 临时图库目录。

## 11. 里程碑

| 阶段 | 内容 | 可验收 |
|---|---|---|
| M1 | 私聊 → 模型 → 回文本（白名单、幂等、切分） | 能对话 |
| M2 | 记忆三层 + 检索 + 遗忘指令 | 重启后记得住 |
| M3 | 图片发送 + 表情包收藏 + 打标 | 会用你的表情包 |
| M4 | 主动消息 + 闸门 + 心跳告警 | 会主动找人 |
| M5 | 自启、重放、灰度观察一周 | 可长期挂机 |

## 12. 风险

| # | 风险 | 验证 | 退路 |
|---|---|---|---|
| R1 | `deepseek-v4.1-flash1017` 可能非公网可用 ID | 启动 1-token 探测 | 回落 `deepseek-chat`，`/status` 显示实际模型 |
| R2 | ~~无法给自己发私聊~~ | 已解决：专用小号 | — |
| R3 | 协议端封号风险 | 小号灰度一周 | 改用官方 Bot（仅封号后启用） |
| R4 | 中文 FTS5 召回 | 20 条历史人工召回 | bge-m3 向量检索 |
| R5 | 长对话 429/超时 | 50 轮压测 | 串行 + 指数退避 |
| R6 | Windows 杀进程 | 模拟重启 | 计划任务 + 拉起脚本 |
| R7 | **主动消息触发风控** | 观察一周 | 降到每周 1 条或关功能 |
| R8 | 模型挑图 tag 幻觉 | 统计命中率 | 无命中不发图 |
| R9 | **模型无视觉能力 → 打标不准** | 人工核对 20 张标签 | 全靠人工 `/tag`；或后续接本地 CLIP |
| R10 | 表情包识别错误（把照片当表情） | 抽查 20 条消息 | 关闭自动收藏，改 `/save` 显式 |
| R11 | **协议端在容器内看不到图库路径** | 实测发送 | `IMAGE_SEND_MODE=base64` |

## 13. Definition of Done

- [ ] 连续对话 50 轮不崩溃、不重复回答
- [ ] 重启后仍能答出 3 天前聊过的细节
- [ ] 只聊 2 轮就关闭，10 分钟后记忆已有内容
- [ ] 事实/偏好/事件三类记忆都能抽取，`/memory` 可见
- [ ] 回复 ≤300 字，超长分段到达且间隔约 1.5s
- [ ] 全程无"正在输入"
- [ ] 发一张表情包 → 自动入库；再发同一张 → 不重复入库
- [ ] 给收藏的图打 `/tag 猫`，之后说"发个猫"能收到它
- [ ] `{{img:不存在}}` 不报错，只发文本
- [ ] 主动消息连续 7 天不超每日 3 条、不在静默时段发送
- [ ] 断网 2 分钟恢复后回复被重放，不丢不重
- [ ] 陌生 QQ 无任何回复
- [ ] 重启 Windows 后自动上线
- [ ] `/status` 累计 token 与 `usage_log` 一致
- [ ] `git status` 无 `.env` / `data/` 痕迹

## 14. 接入方式（已定稿）

**OneBot 协议端 + 专用小号，不用官方 Bot 平台。**
**技术栈：Python + 轻量自研（不引 NoneBot），本机 Windows 常开。**
NapCat 与 LLOneBot 同为 OneBot 11，切换只改 endpoint 与 token。

## 15. 账号侧准备清单

- [ ] 小号与主号互加好友
- [ ] 小号资料完整、绑定手机、独立密码
- [ ] 小号先正常使用数日再挂协议端
- [ ] 协议端仅监听 `127.0.0.1`，token 强随机
- [ ] 备份登录态文件
- [ ] 主动消息必须克制：事件驱动、每日 ≤3、失败不重试

## 16. 人格（已定稿）

| 项 | 内容 |
|---|---|
| 角色 | 「大肥鱼」，18 岁，对方的女儿 |
| 完整存档 | [docs/persona/dafeiyu.md](persona/dafeiyu.md)（设定、示例对话、反模式） |
| 运行时提示词 | [prompts/system.md](../prompts/system.md)（实测 1020 字 ≈ 612 tokens，在 800 预算内） |
| 默认称呼 | 爸爸；对方要求变更则跟随，并作为 `preference` 记忆长期保存 |
| 硬约束 | 单条 ≤300 字、表情最多一处、不讲大道理、不列表腔、无 AI 自述 |
| 边界 | 仅父女/母女关系，越界话题困惑拒绝后拉回日常 |

**维护规则**：改设定先改 `docs/persona/dafeiyu.md`，再压缩进 `prompts/system.md`，两者须同步；system.md 超过 800 tokens 时优先删示例性描述，保留行为规则。

**称呼的优先级**：`memories` 中 `kind='preference'` 且涉及称呼的条目，优先于系统提示词里的默认值——否则会出现"昨天叫妈妈、今天又叫回爸爸"。

