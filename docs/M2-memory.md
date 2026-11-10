# 长期记忆设计（M2）

> 状态：设计定稿，待实现（P1/P2/P3 三阶段）
> 目标：让她**记得住**（事实长期留存）、**会淡忘**（闲聊随时间衰减）、**有自传**（每天写自己的记忆本）
> 关系：本设计取代 `MVP.md` §5 的记忆部分，其余章节仍然有效

## 0. 核心思路

四个部件，各司其职：

```
卡片 cards ── 结构化索引，进上下文的那一层（摘要）
原文 messages ── 细节外挂，按需展开（不删、不丢）
记忆本 journal ── 叙事索引，她每天自己重写的一页
做梦 dream ── 每天一次，整理上面三样
```

**"遗忘"永远只影响"是否进上下文"，不影响存储。** 三个月前的闲聊会沉下去，但数据还在库里，翻得到。

## 1. 卡片（索引层）

### 1.1 表结构

```sql
CREATE TABLE IF NOT EXISTS cards (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL,               -- identity|preference|fact|event|chat
  summary      TEXT NOT NULL,               -- 进上下文的那一句，≤60 字
  detail       TEXT NOT NULL DEFAULT '',    -- 展开细节，平时不注入
  entities     TEXT NOT NULL DEFAULT '[]',  -- JSON 数组，人物/对象
  occurred_at  INTEGER,                     -- 事情发生的时间，未知为 NULL
  importance   REAL NOT NULL DEFAULT 0.5,   -- 0..1
  pinned       INTEGER NOT NULL DEFAULT 0,  -- 1 = 手动钉住，不衰减、不被自动改写
  hit_count    INTEGER NOT NULL DEFAULT 0,  -- 被放进上下文的次数
  last_hit_at  INTEGER,
  source_from  INTEGER,                     -- messages.id 区间起点（外挂指针）
  source_to    INTEGER,
  status       TEXT NOT NULL DEFAULT 'active',  -- active|archived|superseded
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_kind   ON cards(kind, status);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_source ON cards(source_from, source_to);
```

### 1.2 类型与判定

| kind | 含义 | 注入优先级 | 是否衰减 |
|---|---|---|---|
| `identity` | 关于"你是谁、我们是什么关系、什么不能外传" | 最高（恒 1.0） | **否** |
| `preference` | 偏好（喜欢/讨厌/习惯） | 高 | 是 |
| `fact` | 客观事实（职业、住处、养的宠物） | 中 | 是 |
| `event` | 发生过的事（"那晚加班到十点"） | 低 | 是 |
| `chat` | 一段对话的压缩摘要 | 低 | 是 |

**`identity` 的硬判定规则**（写进抽取提示词，不由模型自由发挥）：

1. 涉及用户身份、职业、家庭关系的自我陈述
2. 涉及"我们是什么关系"的陈述
3. 涉及隐私、安全、"不要把某事说出去"的约定
4. 用户明确说"记住这件事"

命中任意一条 → `kind=identity`，`importance ≥ 0.9`。

### 1.3 外挂：细节怎么展开

卡片只带一句 `summary`，细节一律不主动进上下文：

- `detail` 字段：抽取时顺手写的几行展开（可选，可空）
- `source_from` / `source_to`：指向 `messages` 的原文区间

展开方式（P3）：按 id 取原文，或按区间拉出那几轮对话。**P1 不实现展开**，只保证指针正确落库。

## 2. 三重权重

```python
age_days = max(0, (now - (occurred_at or created_at)) / 86400)
recency  = 0.5 ** (age_days / HALF_LIFE_DAYS)          # 默认 30 天减半
freq     = min(1.0, log1p(hit_count) / log1p(10))      # 10 次命中封顶
weight   = importance * (0.6 + 0.4 * freq) * (0.2 + 0.8 * recency)

if pinned or kind == 'identity':
    weight = 1.0
```

**注入阈值**：`weight >= 0.15` 才进上下文，否则沉底（不删）。

**命中计数规则**：同一张卡**每天最多计一次**。否则一天聊 20 轮，`hit_count` 立刻冲顶，
"使用频率"就失去区分度了。

验算：

| 场景 | importance | hit_count | 年龄 | weight | 是否注入 |
|---|---|---|---|---|---|
| 3 个月前的闲聊 | 0.5 | 0 | 90 天 | ≈ 0.09 | ❌ 沉底 |
| 3 个月前但很重要 | 1.0 | 3 | 90 天 | ≈ 0.25 | ✅ 仍记得 |
| 最近刚聊过的偏好 | 0.7 | 0 | 1 天 | ≈ 0.41 | ✅ |
| "我是 agent 创造者" | 1.0 | 0 | 365 天 | 1.00（identity） | ✅ 永不忘 |
| 被 `/pin` 钉住 | 0.2 | 0 | 365 天 | 1.00（pinned） | ✅ |

## 3. 上下文组装

每轮按固定顺序拼 prompt：

| 区块 | 预算（token） | 来源 |
|---|---|---|
| 人格 | 不设限（当前 612） | `prompts/system.md` |
| 记忆本 | 800 | `data/journal.md` |
| 卡片 | 1500 | 按 weight 降序，`weight ≥ 0.15` |
| 对话窗口 | 软 8000 / 硬 32000 | `messages` 游标区间 |

字符预算换算沿用现有规则：`字符数 ≈ token / 0.6`。

卡片区块格式：

```
# 你记得的事（关于对方）
[身份] 他是 agent 的创造者，很在意隐私
[偏好] 不喜欢被叫起床
[事实] 常加班到十点，在互联网公司做后端
[事件] 上周说想养猫
```

空则整块省略，不占 token。

## 4. 压缩（上下文到顶）

两个阈值，软阈值日常生效，硬阈值兜底：

| 配置 | 默认 | 行为 |
|---|---|---|
| `CONTEXT_SOFT_TOKEN_BUDGET` | 8000 | 超过就**增量压缩**最旧的一段 |
| `CONTEXT_HARD_TOKEN_BUDGET` | 32000 | 硬上限，压缩失败时强制丢弃旧窗口 |

流程：

```
窗口字符数 > soft
  → 从游标起取"最旧的一半"
  → LLM 生成 1~2 张 chat 卡（summary/detail/entities/occurred_at）
  → 写卡片；游标前移
  → 生成失败也前移游标（原文仍在 messages 里，只是不再进上下文）
```

> 把 `CONTEXT_SOFT_TOKEN_BUDGET` 设成 32000，就是"只在 32k 时才清空"的行为。默认给 8k 是为了控制费用，见 §11 风险 1。

## 5. 抽取

**时机**：每条回复发送成功之后（异步执行，失败不影响回复）。

**跳过条件**（任一命中就不抽）：
- 以 `/` 开头的指令
- 用户消息字数 < 8（"在吗""好""嗯"）
- 本轮已有抽取在跑（避免堆积）

**输入**：最近 6 条消息 + 当前 active 卡片的 `(id, kind, summary)`（最多 80 张）。

**输出**（严格 JSON）：

```json
{"cards": [
  {"op": "add", "kind": "fact", "summary": "常加班到十点",
   "detail": "在互联网公司做后端，最近项目紧", "entities": ["工作"],
   "occurred_at": "2026-10-27", "importance": 0.6},
  {"op": "update", "id": 12, "summary": "不喜欢被叫起床", "importance": 0.8}
]}
```

**约束**：

0. 自动抽取的新卡先算“听来的”，默认**只进记忆页，不进上下文**；需要旧行为时显式设 `MEMORY_PENDING_INJECT=true`。这样“猜的”不会先污染对话。
1. 只允许 `add` / `update`，`delete` / `archive` 一律在代码层过滤掉
2. 去重兜底：同 `kind` 下 `difflib.SequenceMatcher.ratio() > 0.8` 视为同一条，改走 `update`
3. `pinned=1` 的卡片不接受自动 `update`
4. 单次最多接受 5 条，防止模型一次刷屏
5. 失败：`log.warning` 后放弃本轮，下轮自然重抽

## 6. 记忆本

**位置**：`data/journal.md`（单文件）
**快照**：`data/journal/YYYY-MM-DD.md`（每天一份，保留 30 天）

**规则**：

- 每天由做梦机制**全量重写**，不允许追加
- 长度上限 800 token（约 1300 字），超了截断
- 第一人称，她自己的口吻，写"最近发生了什么、对方的状态、我记得的重要的事"
- 注入时放在人格之后、卡片之前

记忆本与卡片构成**双重索引**：
- 卡片 = 结构化、可检索、有权重
- 记忆本 = 叙事性、连续性、有人味

两者互相校验：做梦时如果记忆本提到某事但卡片里没有，补一张卡；反之亦然。

## 7. 做梦

**触发**：每天 `DREAM_AT`（默认 23:30），进程需在线；错过则在下次启动时补跑一次（判断 `dream_log` 里最近一次成功时间是否早于今天）。

**流程**：

```
1. 取"上次成功做梦之后"的所有消息
2. 取当前 active 卡片全文 + 现有记忆本
3. 一次 LLM 调用，要求输出：
   - 重写后的记忆本全文
   - 卡片操作清单（add / merge / reweight）
4. 写回数据库与文件，记一条 dream_log
5. 存记忆本快照
```

**允许的操作**：`add`、`merge`（合并两张重复卡，保留信息量大的一面并累加 `hit_count`）、`reweight`（调 `importance`）。
**禁止**：`delete`。旧卡可置 `status='archived'`，不进上下文但不丢。

`dream_log` 表：

```sql
CREATE TABLE IF NOT EXISTS dream_log (
  id             INTEGER PRIMARY KEY,
  started_at     INTEGER NOT NULL,
  finished_at    INTEGER,
  messages_seen  INTEGER NOT NULL DEFAULT 0,
  cards_added    INTEGER NOT NULL DEFAULT 0,
  cards_merged   INTEGER NOT NULL DEFAULT 0,
  cards_reweight INTEGER NOT NULL DEFAULT 0,
  journal_chars  INTEGER NOT NULL DEFAULT 0,
  ok             INTEGER NOT NULL DEFAULT 1,
  note           TEXT NOT NULL DEFAULT ''
);
```

## 8. 指令

| 指令 | 行为 |
|---|---|
| `/memory` | 列 top 20 卡片：`id [kind] weight summary` |
| `/forget <关键词或 id>` | 删除卡片（唯一会真删的路径，需二次确认：`/forget <id> confirm`） |
| `/pin <id>` / `/unpin <id>` | 钉住/解钉，`pinned=1` 时 weight 恒 1.0 |
| `/journal` | 显示记忆本全文 |
| `/dream` | 手动触发一次做梦（调试用） |
| `/status` | 增加：卡片数、记忆本字数、上次做梦时间 |

## 8.1 偏好反馈与记忆清理

- 对话页每条她说的话有「合拍 / 不准确」；点「不准确」写一句“她该记住的偏好”，会落一张已确认的 `preference` 卡，下一条消息开始生效。
- 记忆页「+ 记一条偏好」可以不经过聊天，直接写相处偏好；适合重新养成的第一阶段。
- 偏好卡和普通记忆卡一样参与权重、预算和 `/pin`，但不会把用户原话写成事实。
- `tools\python\python.exe scripts\memory-doctor.py` 只读体检；加 `--clean-pending` / `--clean-derived` / `--reset-all` 并配 `--apply` 才执行，执行前会自动备份到 `backups\memory-doctor-*`。
- 清理前先停机器人；`--reset-all` 会清空消息、卡片、日摘要、前情和记忆本，保留 `.env`、人格文件和 `data/persona/`。
- `--clean-pending` / `--clean-derived` 会写 `data/memory-clean-at` 时间点：旧消息仍留在库里可查，但不再参与新的抽取、摘要、梦、前情、原话召回和上下文，避免清完又被旧测试重新生成。删掉这个文件就恢复全部历史。

## 9. 配置项

```dotenv
MEMORY_ENABLED=1
EXTRACT_ENABLED=1
MEMORY_PENDING_INJECT=false
DREAM_ENABLED=1
DREAM_AT=23:30
DECAY_HALF_LIFE_DAYS=30
CARD_WEIGHT_THRESHOLD=0.15
CARD_INJECT_TOKEN_BUDGET=1500
JOURNAL_TOKEN_BUDGET=800
CONTEXT_SOFT_TOKEN_BUDGET=8000
CONTEXT_HARD_TOKEN_BUDGET=32000
```

兼容：若旧配置里有 `WINDOW_TOKEN_BUDGET`，作为 `CONTEXT_SOFT_TOKEN_BUDGET` 的兜底值。

## 10. 迁移

旧 `memories` 表（`kind/content/source_from/source_to/created_at/updated_at`）一次性搬进 `cards`：

| 旧 | 新 |
|---|---|
| `content` | `summary`（超 60 字截断，余下进 `detail`） |
| `kind` | 同名映射；未知值 → `fact` |
| `created_at` | `occurred_at` 与 `created_at` |
| `source_from/to` | 原样 |
| — | `importance = 0.5`，`hit_count = 0` |

迁移在 `_migrate()` 里做，幂等（`cards` 非空则跳过），并在迁移后保留旧表不删（回滚用）。

## 11. 测试计划

| 用例 | 断言 |
|---|---|
| 权重计算 | 四组验算值与 §2 表格一致 |
| identity 不衰减 | 一年前的 identity 卡 weight 仍为 1.0 |
| pinned 不衰减 | 同理 |
| 阈值过滤 | weight 0.14 的卡不进上下文 |
| 注入格式 | 区块标题、按 kind 分组、顺序正确 |
| 预算截断 | 卡片总量超预算时从低权重开始砍 |
| 空记忆 | 不产生空区块、不多余换行 |
| 抽取入库 | fake LLM 返回 2 张卡 → 落库字段正确 |
| 抽取去重 | 相似度 > 0.8 时转 update，不新增行 |
| 抽取拒绝 delete | 返回 delete 操作被过滤，库无变化 |
| pinned 保护 | pinned 卡片不被自动 update 覆盖 |
| 抽取失败 | 回复照常发出，只有 warning 日志 |
| 压缩 | 超软预算触发，生成 chat 卡且游标前移 |
| 压缩失败 | 游标仍前移，原文仍在库里 |
| 记忆本重写 | 旧内容被整体替换，不是追加 |
| 记忆本超长 | 截断到预算内 |
| 做梦 | 生成卡片操作被正确应用，dream_log 有记录 |
| 做梦补跑 | 启动时发现今天没做过 → 触发一次 |
| 迁移 | 旧表 3 条 → cards 3 条，字段映射正确 |
| 迁移幂等 | 跑两次不产生重复 |

全部用 fake LLM / fake clock，不碰网络与真实 QQ。

## 12. 分期与验收

**P1 卡片与权重**（骨架）
- `cards` 表 + 迁移、权重计算、注入、抽取、压缩、`/memory` `/pin` `/forget`
- 验收：说一件新事 → 下一轮她能用上；`/memory` 能看到；3 个月前的卡（构造数据）不进上下文

**P2 记忆本**（叙事）
- `data/journal.md` 读写、快照、注入、`/journal`
- 验收：记忆本内容出现在她的话里；重写是替换不是追加

**P3 做梦**（维护）
- 定时触发 + 补跑、`dream_log`、`/dream`、merge/reweight
- 验收：跑一次做梦后卡片更少但信息不丢；记忆本被重写

## 13. 风险

| # | 风险 | 影响 | 对策 |
|---|---|---|---|
| 1 | **每轮成本**：窗口越大每轮越贵，32k 窗口一天 100 轮约 320 万 input token | 按 ¥2/百万估约 6 元/天，一个月近 200 | 默认软阈值 8k；用 `usage_log` 观察一周再调 |
| 2 | 抽取质量：重复卡、噪声卡 | 上下文被垃圾占满 | 相似度去重 + 单次 5 条上限 + `/forget` 兜底 |
| 3 | 压缩丢信息 | 细节丢失 | 原文不删，只是不进上下文；卡片留指针 |
| 4 | 记忆本漂移 | 每天重写可能丢掉旧内容 | 每日快照 + 卡片双保险，做梦时互相校验 |
| 5 | 做梦错过时间 | 当天没整理 | 启动时补跑一次 |
| 6 | token 估算误差 | 预算不准 | 估算是粗算（1 字≈0.6 token），宁可低估预算 |
| 7 | `identity` 误判 | 把闲聊当身份级，永不衰减 | 硬规则收窄 + `/unpin` 与 `/forget` 可纠正 |
