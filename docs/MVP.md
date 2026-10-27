# MVP 需求与设计（v0.2）

> 状态：草案，待验证项见 §12
> 目标：单人私聊场景下，让 QQ 里的机器人 = 一个带长期记忆的 DeepSeek 对话体

**v0.1 → v0.2 变更**：补部署形态验收、心跳告警、发送重放、遗忘语义；修正"20 轮 vs token 预算"的实现规则；补空闲超时抽取与 FTS 查询清洗；新增 `.gitignore`/密钥工程约束与测试计划。

## 1. 定位

一款**只服务单个用户**的 QQ 私聊助手：主号在 QQ 里私聊机器人账号，机器人用自己的 DeepSeek 模型回答，并且**记得住过去聊过的一切**。

## 2. 目标 / 非目标

**目标**

- 私聊一条消息 → 一条回答，长期可用（挂机不掉线、重启不丢记忆）
- 记忆是**长期**的：几个月前的对话内容仍可被想起
- 零限额：不设每日条数、不做计费逻辑（但保留用量观测）

**非目标（MVP 明确不做）**

- 群聊、多人会话、多用户
- 图片 / 语音 / 文件 / 多模态
- Web 管理后台、注册登录、计费充值
- 多机器人、插件系统、人格市场
- 联网搜索、RAG 知识库（历史对话检索不属于此类）

## 3. 场景与账号

```
主号 B（用户本人） ──私聊──▶ 机器人小号 A（挂协议端） ──HTTPS──▶ DeepSeek API
```

- QQ 不支持给自己发私聊消息，因此**必须有一个专用小号 A** 作为机器人账号。**（已就绪：用户已单独注册小号）**
- 主号与小号互加好友，避免消息落入"陌生人消息"折叠而收不到推送。
- 主号 B 的 QQ 号作为唯一允许的 `ALLOW_USER_ID`；其他任何人私聊一律静默忽略。
- 只处理私聊（C2C）消息，群消息直接丢弃。

## 4. 功能范围（P0）

| 编号 | 功能 | 验收标准 |
|---|---|---|
| F1 | 私聊消息收发 | B 发文本 → A 回文本；非 B 发送的消息被忽略 |
| F2 | 模型调用 | 调 `MODEL_ID`，超时 60s，失败退避重试 1 次后回固定话术 |
| F3 | 消息持久化 | 每条消息落 SQLite，永不删除 |
| F4 | 短期上下文 | 按 token 预算从最新往前装载（见 §5.2） |
| F5 | 长期记忆笔记 | 自动抽取的事实/偏好，注入每次请求 |
| F6 | 历史检索 | 超出窗口的老内容可被关键词召回 |
| F7 | 记忆整理 | 触发时同时产出记忆条目与原文摘要（见 §5.4） |
| F8 | 指令 | `/reset`、`/memory [rm id]`、`/forget <关键词>`、`/status` |
| F9 | 幂等与重放 | 按 `qq_msg_id` 去重；未发送成功的回复重连后重放 |
| F10 | 回复适配 | 去 Markdown、按 ~800 字切分、思维链不外发 |
| F11 | 心跳告警 | 协议端断连超 5 分钟或连续 3 次调用失败即告警（§7） |

## 5. 长期记忆设计（核心）

DeepSeek API 无状态，**"长期记忆"必须自己实现**。三层结构：

```
┌─ L1 长期记忆笔记   全量注入   ≤1500 tokens
├─ L2 最近对话原文   滑动窗口   ≤4000 tokens
└─ L3 历史消息库     检索召回   ≤800  tokens
```

加系统提示词 ≤800，单次请求约 **7K tokens**。

**token 估算规则**：不引 tokenizer 依赖，按 `est(text) = ceil(len(text) * 0.6)` 估算（中文为主场景的经验系数）。所有预算判断用该函数，允许 ±15% 误差。

### 5.1 数据模型

```sql
CREATE TABLE messages (
  id          INTEGER PRIMARY KEY,
  session_id  TEXT NOT NULL,
  qq_msg_id   TEXT UNIQUE,                       -- 协议层消息 ID，幂等用
  role        TEXT NOT NULL,                     -- user / assistant
  content     TEXT NOT NULL,
  send_state  TEXT NOT NULL DEFAULT 'sent',      -- pending / sent / failed
  created_at  INTEGER NOT NULL
);

CREATE TABLE memories (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,                     -- fact / preference / event / relation
  content     TEXT NOT NULL,
  source_from INTEGER,                           -- 溯源：来源 messages.id 区间
  source_to   INTEGER,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);

CREATE TABLE summaries (
  id          INTEGER PRIMARY KEY,
  covers_from INTEGER NOT NULL,                  -- 覆盖的 messages.id 区间
  covers_to   INTEGER NOT NULL,
  content     TEXT NOT NULL,
  created_at  INTEGER NOT NULL
);

CREATE TABLE usage_log (
  id         INTEGER PRIMARY KEY,
  model      TEXT NOT NULL,
  tokens_in  INTEGER NOT NULL,
  tokens_out INTEGER NOT NULL,
  purpose    TEXT NOT NULL,                      -- chat / memory_extract
  ok         INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);

-- 记忆整理游标：记录已处理到哪条消息，避免重复抽取
CREATE TABLE cursors (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

CREATE VIRTUAL TABLE messages_fts USING fts5(
  content, content='messages', content_rowid='id', tokenize='trigram'
);
-- 需配套 AFTER INSERT / UPDATE / DELETE 触发器保持同步
```

> 相比 v0.1：删掉无写入路径的 `pinned`；`memories` 增加溯源字段；新增 `usage_log`（`/status` 与 DoD 依赖）和 `cursors`；`send_state` 支撑 F9。

### 5.2 上下文组装算法（替代"最近 20 轮"的模糊表述）

按顺序组装，各自独立预算，**超预算即截断，不整体放弃**：

1. **系统提示**：内置默认人格，`SYSTEM_PROMPT_FILE` 存在则覆盖；超 800 截断。
2. **L1 记忆**：按 `updated_at` 倒序累加，超过 1500 停止。
3. **L3 检索**：以用户最新消息为查询，清洗后检索 top 5（§5.5）；单条截 160 字，合计超 800 停止，每条前置日期。
4. **L2 窗口**：从最新消息往前取，**user/assistant 必须成对**，累计超 4000 停止，最多 20 轮。
5. 窗口为空（`/reset` 后）时只发 L1 + L3。

### 5.3 记忆整理（F7 统一入口）

同一个任务同时产出**记忆条目**和**原文摘要**，解决 v0.1 中 `summaries` 表无人使用的问题。

**触发条件（任一）**

- 距上次整理满 **8 轮**对话
- **空闲 10 分钟**（单用户碎片化使用的主路径，v0.1 缺失）
- `/reset` 前、进程优雅退出前

**输入**：`cursors.memory_last_id` 之后的所有消息。
**输出**：模型返回严格 JSON

```json
{
  "memories": [{"op": "add|update", "id": 12, "kind": "fact", "content": "..."}],
  "summary": "这段对话的要点，150 字以内"
}
```

**约束**

- 只允许 `add` / `update`，**禁止自动 `delete`**——一次误判就永久丢事实，删除只能由 `/forget` 或 `/memory rm` 人工触发。
- `op=update` 时 id 必须存在于库中，否则降级为 `add`。
- JSON 解析失败：整批丢弃，游标不推进，下个周期重试。
- 事务内写 `memories` + `summaries` + 推进 `cursors`。
- 记忆条数超过 80 时，在整理任务里额外做一次合并去重（同义条目保留最新）。

### 5.4 遗忘语义（必须与用户预期一致）

| 指令 | 行为 | 用户预期 |
|---|---|---|
| `/reset` | 清空 L2 窗口（写入游标标记），**不动** `memories` | 语气重新开始，不等于遗忘 |
| `/forget <关键词>` | 删除 `memories` 中匹配条目，返回删除条数 | 真正忘掉这件事 |
| `/memory rm <id>` | 按 id 精确删除 | 精细修正 |

`/reset` 的回复必须明说"已清空上下文，长期记忆保留"，避免误解。

### 5.5 检索与查询清洗（v0.1 会直接报错的地方）

FTS5 `MATCH` 不接受原始句子，必须先清洗：

1. 去掉 FTS5 特殊字符：`" * ( ) : ^ -`
2. 按空白与中英文标点切分成片段
3. 保留长度 ≥2 的片段，每段用双引号包裹
4. 以 `OR` 连接；结果为空则**跳过检索**（不要回退成全句匹配）
5. 查询异常一律捕获为"无召回"，不得影响主流程

trigram 分词器对 <3 字的片段无法命中，属预期行为，由 L1 记忆兜底。

### 5.6 隐私

- 记忆与原文均存本地 SQLite，不落第三方。
- `data/` 目录含全部聊天记录与协议端登录态，**禁止入库**（§9）。
- `/memory` 可查看全部记忆条目，保证可审计。

## 6. 架构

```
QQ 协议端 ──▶ 适配层 ──▶ 编排层 ──▶ 记忆层 ──▶ 模型层
   (WS)       Adapter      Engine      Memory      LLM
                              │
                              └──▶ 发送队列(outbox) ──▶ 协议端 HTTP API
```

- **适配层**：把协议事件统一为 `{session_id, user_id, msg_id, text}`，向上不感知 QQ 细节。
- **编排层**：白名单 → 幂等 → 指令分发 → 组 prompt → 调模型 → 落库 → 入 outbox → 发送。
- **记忆层**：窗口、注入、检索、整理、遗忘。
- **单会话串行锁**：同一 session 排队处理，保证上下文写入与回复顺序。

## 7. 可靠性设计

| 场景 | 处理 |
|---|---|
| 回复已入库但发送失败 | `send_state='failed'`，重连后扫描 `pending/failed` 重放（仅重放最近 10 分钟内的） |
| 协议端断线 | 指数退避重连（1→2→4→…→60s 封顶） |
| 心跳 | 每 60s 探测协议端；连续失败超 5 分钟告警 |
| 模型连续失败 | 连续 3 次失败告警，并回复"我这边出问题了" |
| 进程启动 | 恢复 `pending` 队列 + 校验模型可用性（探测 R1） |

告警通道 MVP 用**本地日志 + 可选 webhook**（企业微信/Server 酱均可，一行 httpx 调用）。这是唯一能在"机器人不回消息"时让你知道的通道。

## 8. 配置项

```env
QQ_ADAPTER=llonebot            # 或 napcat（同为 OneBot 11，代码一致）
ADAPTER_ENDPOINT=http://127.0.0.1:3001
ADAPTER_TOKEN=
ALLOW_USER_ID=                 # 主号 B 的 QQ 号，必填
MODEL_ID=deepseek-v4.1-flash1017
MODEL_BASE_URL=https://api.deepseek.com
MODEL_API_KEY=
SYSTEM_PROMPT_FILE=prompts/system.md   # 不存在则用内置默认人格
HISTORY_MAX_TURNS=20
WINDOW_TOKEN_BUDGET=4000
MEMORY_TOKEN_BUDGET=1500
RETRIEVE_TOP_K=5
EXTRACT_EVERY_TURNS=8
IDLE_EXTRACT_MINUTES=10
SEND_SPLIT_CHARS=800
HEARTBEAT_INTERVAL=60
ALERT_WEBHOOK=                 # 可空
DB_PATH=data/chat.db
```

## 9. 工程约束与安全（v0.1 缺失，必须先落地）

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

- 入库 `.env.example`（只有键名、无值），`.env` 永不入库。
- 启动时校验 `.env` 存在且 `ALLOW_USER_ID`/`MODEL_API_KEY` 非空，缺失即退出并打印缺失项。
- API Key 不得出现在任何日志与异常栈中（统一打码）。
- `data/` 仅本机用户可读写；协议端登录态文件单独放 `data/adapter/`，定期备份。

## 10. 测试计划

AGENTS.md 要求每次改动带测试。P0 单测边界：

| 测试 | 覆盖点 |
|---|---|
| 回复切分 | 去 Markdown、按 800 字切分、不产生空段、超长单句可硬切 |
| FTS 清洗 | 含 `- * " (` 的查询不抛异常；短查询返回空而非报错 |
| 白名单 | 非 `ALLOW_USER_ID` 静默丢弃；群消息丢弃 |
| 幂等 | 同一 `qq_msg_id` 二次投递不产生第二次回答 |
| 预算截断 | 超预算时从最新往前截；user/assistant 不拆对 |
| 遗忘语义 | `/reset` 不动 memories；`/forget` 删匹配项 |

集成测试用 fake LLM + fake transport，不依赖真实 QQ 与 API。

## 11. 技术风险与验证项

| # | 风险 | 验证方式 | 不通过的退路 |
|---|---|---|---|
| R1 | `deepseek-v4.1-flash1017` 可能不是公网可用模型 ID | 启动时发 1-token 探测 | 回落 `deepseek-chat`，`/status` 显示实际模型 |
| R2 | ~~QQ 无法给自己发私聊~~ | 已解决：专用小号已注册 | — |
| R3 | 非官方协议端封号风险 | 小号灰度一周，仅私聊、不主动推送、不加群 | 改用官方 Bot 平台（**仅在封号后启用**） |
| R4 | 中文 FTS5 召回效果 | 20 条历史消息人工召回测试 | 本地 embedding（bge-m3）+ 向量检索 |
| R5 | 长对话 429 / 超时 | 连续 50 轮压测 | 串行 + 指数退避 |
| R6 | Windows 休眠/更新杀掉进程 | 模拟重启，验证自启 | 计划任务 + 崩溃拉起脚本 |

## 12. Definition of Done

- [ ] B 与小号 A 连续对话 50 轮不崩溃、不重复回答
- [ ] 重启进程后，仍能答出 3 天前聊过的具体细节
- [ ] 只聊 2 轮就关闭，10 分钟后记忆里已有这段内容（验证空闲抽取）
- [ ] 断网 2 分钟再恢复，期间的回复被自动重放，不丢不重
- [ ] 陌生 QQ 私聊机器人，无任何回复
- [ ] 重启 Windows 后无需人工干预自动上线（计划任务自启）
- [ ] `/status` 显示累计 token 用量，与 `usage_log` 一致
- [ ] 仓库 `git status` 无 `.env` / `data/` 痕迹

## 13. 接入方式决策（已定稿）

**结论：走 OneBot 协议端 + 专用小号，不使用官方 QQ 开放平台 Bot。**
**技术栈：Python + 轻量自研（不引 NoneBot），本机 Windows 常开。**

| 方案 | 是否采用 | 理由 |
|---|---|---|
| OneBot 协议端 + 小号 | ✅ 采用 | 无需资质审核；单聊直达；模型与记忆完全自控；封号损失限于小号 |
| 官方 QQ Bot 平台 | ❌ 备选 | 需过审；主动消息受限；AI 内容审核更深；模型自定受平台约束 |

协议端实现仍未定，但 NapCat 与 LLOneBot 同为 OneBot 11，**切换只改 `ADAPTER_ENDPOINT` 与 token，不影响业务代码**。

## 14. 账号侧准备清单

- [ ] 小号与主号互加好友（否则可能进"陌生人消息"）
- [ ] 小号资料填完整、绑定手机、设独立密码
- [ ] 小号先正常使用数日再挂协议端（新号直挂易触发风控）
- [ ] 协议端仅监听 `127.0.0.1`，token 用强随机值
- [ ] 备份协议端登录态文件（掉线需重新扫码）
