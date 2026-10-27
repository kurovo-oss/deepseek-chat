# MVP 需求与设计（v0.1）

> 状态：草案，待验证项见文末
> 目标：单人私聊场景下，让 QQ 里的机器人 = 一个带长期记忆的 DeepSeek 对话体

## 1. 定位

一款**只服务单个用户**的 QQ 私聊助手：主号在 QQ 里私聊机器人账号，机器人用自己的 DeepSeek 模型回答，并且**记得住过去聊过的一切**。

## 2. 目标 / 非目标

**目标**

- 私聊一条消息 → 一条回答，长期可用（挂机不掉线、重启不丢记忆）
- 记忆是**长期**的：几个月前的对话内容仍可被想起
- 零限额：不设每日条数、不做计费逻辑

**非目标（MVP 明确不做）**

- 群聊、多人会话、多用户
- 图片 / 语音 / 文件 / 多模态
- Web 管理后台、注册登录、计费充值
- 多机器人、插件系统、人格市场
- 联网搜索、RAG 知识库（文档库另说，历史对话检索不属于此类）

## 3. 场景与账号

```
主号 B（用户本人） ──私聊──▶ 机器人小号 A（挂协议端） ──HTTPS──▶ DeepSeek API
```

- QQ 不支持给自己发私聊消息，因此**必须有一个专用小号 A** 作为机器人账号。
- 主号 B 的 QQ 号写死为唯一允许的 `ALLOW_USER_ID`；其他任何人私聊一律静默忽略（防止 token 泄漏后被陌生人消耗）。
- 只处理 `C2C`（私聊）消息，群消息直接丢弃。

## 4. 功能范围（P0）

| 编号 | 功能 | 验收标准 |
|---|---|---|
| F1 | 私聊消息收发 | B 发文本 → A 回文本；非 B 发送的消息被忽略 |
| F2 | 模型调用 | 通过 OpenAI 兼容接口调用 `MODEL_ID`，超时 60s，失败重试 1 次后回固定话术 |
| F3 | 消息持久化 | 每条收发消息落 SQLite，永不删除 |
| F4 | 短期上下文 | 每次请求携带最近 20 轮原文 |
| F5 | 长期记忆笔记 | 自动抽取的事实/偏好，注入到每次请求 |
| F6 | 历史检索 | 超出窗口的老内容可通过关键词被召回 |
| F7 | 记忆压缩 | 窗口接近上限时自动把最老对话总结进笔记 |
| F8 | 指令 | `/reset`（清空短期窗口，保留长期记忆）、`/memory`（查看当前记忆笔记）、`/status`（模型、token 用量） |
| F9 | 幂等 | 按 `message_id` 去重，重复投递不重复回答 |
| F10 | 回复适配 | 去除 Markdown、按 ~800 字切分多条、`reasoner` 思维链不外发 |

## 5. 长期记忆设计（核心）

DeepSeek API 是无状态的，**"长期记忆"必须自己实现**。采用三层结构，兼顾成本与召回率：

```
┌─ L1 长期记忆笔记（全量注入，≤1500 tokens）
├─ L2 最近 20 轮原文（滑动窗口，≤4000 tokens）
└─ L3 历史消息库（SQLite 全量，检索召回，≤800 tokens）
```

单次请求 token 预算约 **7K**，远低于 128K 上下文，响应快且便宜。

### 5.1 数据模型

```sql
CREATE TABLE messages (
  id         INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL,          -- B 的 QQ 号
  qq_msg_id  TEXT UNIQUE,            -- 协议层消息 ID，用于幂等
  role       TEXT NOT NULL,          -- user / assistant
  content    TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE memories (
  id         INTEGER PRIMARY KEY,
  kind       TEXT NOT NULL,          -- fact / preference / event / relation
  content    TEXT NOT NULL,
  pinned     INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE summaries (
  id         INTEGER PRIMARY KEY,
  covers_from INTEGER NOT NULL,      -- 覆盖的 messages.id 区间
  covers_to   INTEGER NOT NULL,
  content    TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

-- 中文检索：trigram 分词器可直接对中文子串做匹配
CREATE VIRTUAL TABLE messages_fts USING fts5(content, content='messages', content_rowid='id', tokenize='trigram');
```

### 5.2 写入策略（避免每轮都抽取）

- 每 **8 轮**对话，或 `/reset` 前，触发一次记忆抽取：把新对话喂给模型，要求输出 JSON 增量（`add` / `update` / `delete`），事务内落库。
- 抽取失败不影响主流程，仅记日志，下个周期重试。
- 记忆条数超过 80 条时，触发一次合并去重（同一件事只留一条）。

### 5.3 召回策略

- L1 全量注入（超 1500 tokens 时按 `pinned` → 最近更新 排序截断）。
- L3 用当前用户消息做 FTS5 `MATCH` 检索，取最近 5 条命中，按时间衰减排序。
- 命中结果以 `[历史片段]` 段落形式注入，并注明时间，避免模型把旧信息当现状。

### 5.4 隐私与透明

- 记忆笔记对用户**完全可见可改**（`/memory` 查看），这是自用助手信任的基础。
- 记忆与原文均存本地 SQLite 单文件，不落第三方。

## 6. 架构

```
QQ 协议端(NapCat/LLOneBot)  ── 适配层 ──▶ 编排层 ──▶ 记忆层 ──▶ 模型层
                              Adapter      Engine      Memory      LLM
```

- **适配层**：把协议事件统一成 `{session_id, user_id, msg_id, text}`，向上不感知 QQ 细节，便于将来换官方 Bot 接口。
- **编排层**：白名单 → 指令分发 → 幂等 → 组 prompt → 调模型 → 落库 → 切分回复。
- **记忆层**：负责窗口、注入、抽取、检索、压缩。
- **单会话串行锁**：同一 session 的请求排队处理，保证上下文写入顺序与回复不交错。

## 7. 配置项

```env
QQ_ADAPTER=llonebot            # 或 napcat
ADAPTER_ENDPOINT=http://127.0.0.1:3001
ALLOW_USER_ID=                 # 主号 B 的 QQ 号，唯一白名单
MODEL_ID=deepseek-v4.1-flash1017
MODEL_BASE_URL=https://api.deepseek.com
MODEL_API_KEY=
SYSTEM_PROMPT_FILE=prompts/system.md
HISTORY_WINDOW_TURNS=20
MEMORY_TOKEN_BUDGET=1500
RETRIEVE_TOP_K=5
DB_PATH=data/chat.db
```

## 8. 技术风险与验证项（写代码前先跑通）

| # | 风险 | 验证方式 | 不通过的退路 |
|---|---|---|---|
| R1 | `deepseek-v4.1-flash1017` 可能不是公网 API 可用的模型 ID | 启动时用它发一次 1-token 探测请求 | 配置回落到 `deepseek-chat`，并在 `/status` 显示实际模型 |
| R2 | QQ 无法给自己发私聊 | 用小号 A 实测 | 必须走小号方案，无退路，需提前准备 |
| R3 | 非官方协议端的封号风险 | 小号灰度一周，仅私聊、不主动推送、不加群 | 改用官方 QQ 开放平台 Bot（需资质，主动消息受限） |
| R4 | 中文 FTS5 分词效果 | 用历史消息做 20 条人工召回测试 | 改用本地 embedding（bge-m3）+ 向量检索 |
| R5 | 长对话触发 429 / 超时 | 压测连续 50 轮 | 单会话串行 + 指数退避重试 |

## 9. Definition of Done

- [ ] B 与小号 A 连续对话 50 轮不崩溃、不重复回答
- [ ] 重启进程后，仍能答出 3 天前聊过的具体细节
- [ ] 记忆笔记可通过 `/memory` 查看，且内容与事实一致
- [ ] 陌生 QQ 私聊机器人，无任何回复
- [ ] `docker compose up` 一条命令拉起，日志可追溯每次请求的 token 用量
