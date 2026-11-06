# 大肥鱼 v2 设计方案

> 目标读者：你，以及以后接手这份代码的人。
> 这份文档只做设计，不动实现；每个阶段落地时再按这里的分期拆 commit。
> 参考对象：dsh-dafeiyu-dot（记忆宫殿、情绪语音）、Aurabot（模块化、渠道、限流、权限）、
> 梗鲸 aigengtu.com（梗百科）、dsh-fish-river（事件流人格，已落地）。

## 0. 一句话目标

把"只有对话和记忆"的大肥鱼，补成**记得住、看得懂梗、有自己的社交生活、但不会乱来**的版本；
所有对外写操作都有一个开关、一个额度、一条审计记录，默认关着。

**不做的事**：群聊、多人格、公开分发、语音通话（这一期不做，留给下一期）、
任何需要把项目数据放到 C 盘的东西。

---

## 1. 现状与差距

| 能力 | 现在 | 参考实现 | 差距 |
|---|---|---|---|
| 长期记忆 | `cards` 30 张 + 日摘要 + 事件流 5 条；`/成长` 只能看 | dafeiyu-dot：来源、关系图、精读、更正、遗忘、备份；Aurabot：BM25 + 遗忘曲线 + 人物档案 | **没有来源、不能更正、不能定点遗忘、没有人物档案** |
| 表情包 | 已有 `collect.is_sticker`（`sub_type=1`）、vision 备注、`image_notes` 19 条、tags | Aurabot：真表情过滤 + 尺寸/体积/MD5/小时限额 + 视觉起备注 | 过滤不全（mface/emoji_id 未覆盖）、无限额、备注没进选择 |
| 梗 | `memes.GLOSSARY` 17 个网络词，只给释义 | 梗鲸：2956 枚、编号、台词、情绪、角色、释义、出处 | **没有台词库、没有"她懂这个梗"的能力** |
| 社交渠道 | 只会在聊天里说"我在学做菜" | Aurabot：真发空间、真评论 B 站、真刷抖音点赞 | **全是叙述，没有行动** |
| 工程可靠 | 888 测试、22 条评测、supervisor 自启、失败重放 | Aurabot：模块内核、权限、试运行、热重载、错误隔离、限流、按聊天选模型 | **没有权限矩阵、没有试运行、没有审计、外部调用无限流** |

---

## 2. 总体架构

```
                     ┌──────────────────────────────┐
                     │            Engine            │
                     │  收消息 → 上下文 → 模型 → 发   │
                     └───────┬──────────────┬───────┘
                             │              │
                  ┌──────────▼───┐   ┌──────▼─────────┐
                  │  记忆 v2      │   │  Guard（闸门）  │
                  │ facts/profiles│   │ 开关·试运行·额度 │
                  │ lessons       │   │ 冷却·审计·熔断   │
                  └──────┬───────┘   └──────┬─────────┘
                         │                  │
     ┌───────────────────┼──────────────────┼───────────────────┐
     │                   │                  │                   │
┌────▼─────┐      ┌──────▼──────┐    ┌──────▼──────┐    ┌──────▼──────┐
│ 表情/梗库 │      │  QQ 空间     │    │   B 站      │    │   抖音       │
│ 采集·备注 │      │ send_qzone  │    │ 读+评论     │    │ CDP 浏览+点赞 │
│ 梗百科注入│      │ 1 条/天      │    │ 3 条/天     │    │ 5 个/天      │
└────┬─────┘      └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
     │                   │                  │                   │
     └───────────────────┴────────┬─────────┴───────────────────┘
                                  │
                        ┌─────────▼─────────┐
                        │ audit 表 + 日志    │
                        │ 谁·何时·对谁·结果  │
                        └───────────────────┘
```

模块统一接口（Aurabot 的内核思路，Python 版）：

```python
@dataclass
class Capability:
    name: str                 # "qzone" / "bili" / "douyin" / "memepedia"
    enabled: bool = False     # 总开关，默认关
    dry_run: bool = True      # 试运行：只审计不真发
    owner_only: bool = True   # 只认主人私聊
    daily_quota: int = 0      # 0 = 不限
    cooldown_seconds: int = 0
    channels: tuple[str, ...] = ()   # 作用在哪些 origin

class Module:
    name: str
    capability: Capability
    def health(self) -> dict          # {"ok": bool, "detail": str}
    def tick(self, now: int) -> None  # 定时任务，按 60s 调度
    def tools(self) -> dict           # 给引擎/指令用的函数
```

模块放在 `qqbot/modules/`，用户自己写的放 `data/modules/`（升级不覆盖，启动时按 mtime 热加载，
最后一次修改时间变了就重新 import；出错只禁用这一个模块，引擎照跑）。

---

## 3. 记忆 v2

### 3.1 数据模型

```sql
-- 事实：一条可被引用、可被推翻的"我知道的事"
CREATE TABLE IF NOT EXISTS facts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  subject       TEXT NOT NULL,        -- 他 / 我 / 他妈妈 / 那只猫
  predicate     TEXT NOT NULL,        -- 喜欢 / 讨厌 / 住在 / 在做 / 答应过
  object        TEXT NOT NULL,        -- 具体内容（≤60 字）
  quote         TEXT NOT NULL,        -- 原话证据（≤80 字）
  source_msg_id TEXT,                 -- messages.id 或 QQ message_id
  source_at     INTEGER NOT NULL,     -- 证据发生时间
  confidence    REAL NOT NULL DEFAULT 0.6,
  importance    REAL NOT NULL DEFAULT 0.5,
  status        TEXT NOT NULL DEFAULT 'active',  -- active/superseded/forgotten
  superseded_by INTEGER,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL,
  last_recalled_at INTEGER NOT NULL DEFAULT 0,
  recall_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject, status);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status, importance DESC);

-- 每一次改写都留痕，永不物理删除（fish-river 的"只追加"）
CREATE TABLE IF NOT EXISTS fact_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  fact_id    INTEGER NOT NULL,
  action     TEXT NOT NULL,       -- add / correct / supersede / forget / recall
  note       TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);

-- 人物/关系小档案：每个人一张，定期重写，不参与 BM25
CREATE TABLE IF NOT EXISTS profiles (
  who        TEXT PRIMARY KEY,    -- 他 / 他妈妈 / 我
  text       TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

-- 相处经验：不是事实，是"跟他相处学到的东西"（由拒绝结算/反省产生）
CREATE TABLE IF NOT EXISTS lessons (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  topic      TEXT NOT NULL,
  text       TEXT NOT NULL,
  source     TEXT NOT NULL DEFAULT '',
  hits       INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
```

`cards` 原样保留：卡片是"发生过什么"（episode），`facts` 是"我知道什么"（semantic）。
两者互补，不互相替代。

### 3.2 写入链路

1. **抽取**：每轮对话结束后，或者每 10 轮触发一次（便宜的模型，`temperature=0`），
   要求输出 JSON 候选：
   ```json
   {"facts": [{"subject": "他", "predicate": "讨厌", "object": "香菜",
               "quote": "别放香菜", "confidence": 0.9, "importance": 0.6}]}
   ```
2. **证据校验**：`quote` 必须能在最近 20 条消息里找到（去空白后子串匹配），
   找不到就丢弃——**不许模型编证据**，这是 dafeiyu-dot 的"来源"思路。
3. **去重/冲突**：对同一 `(subject, predicate)` 且 `object` 相似度 ≥0.85 的旧事实：
   - 新事实是补充 → 合并 `object`；
   - 新事实与旧矛盾 → 旧标 `superseded`，`superseded_by` 指向新 id；
   - 只是重复 → 只更新 `confidence` 和 `recall_count`。
4. **人物档案**：当 `facts` 里某个 subject 的 active 事实 ≥5 条，触发生成/重写 `profiles.text`
   （≤300 字），由模型压缩，失败保留旧版本。

### 3.3 召回

不是把记忆全塞进去，而是每轮只取 **top 6**、总长 **≤600 字**：

```
score = 1.2 * bm25_norm
      + 0.6 * importance
      + 0.5 * exp(-age_days / 90)          # 遗忘曲线
      + 0.4 * speaker_match                # 关于当前说话人的加权
      + 0.3 * (pinned ? 1 : 0)
      - 0.5 * (status != 'active' ? 1 : 0)
```

注入格式（进 `context.py` 的记忆段，替代现在纯卡片的写法）：

```
【我知道的事】
- 他讨厌香菜（3 天前，他说的："别放香菜"）#f12
- 他下周三面试，紧张（昨天）#f18
- 他累的时候不想说话，想自己待着（相处经验）#l4
```

- 每条带 `#id` 和来源时间，方便 `/记忆 来源 <id>` 追溯；
- `lessons` 单独一段【跟他相处学到的】，最多 3 条；
- 预算仍走现有 `budget.fit`，记忆段上限 600 字，超了按 score 截断。

### 3.4 维护与命令

- 夜间（现有 `dream.bedtime`）：合并重复、给 90 天没被想起且 `confidence<0.4` 的标 `forgotten`、
  重写人物档案。
- 备份：复用现有 `backup` 模块，`facts`/`fact_events`/`profiles`/`lessons` 全进备份包。
- 指令：
  - `/记忆 香菜` —— 搜相关事实，列出 `#id | 内容 | 来源时间`
  - `/记忆 来源 12` —— 展开这条事实的原话、来源消息、被谁改过
  - `/记忆 更正 12 他其实不讨厌香菜了` —— 写新事实 + 旧标 superseded
  - `/记忆 忘了 12` —— 标 forgotten（不物理删）
  - `/记忆 统计` —— active/superseded/forgotten 各多少，档案几条

### 3.5 迁移

一次性脚本 `scripts/migrate-memory.py`：把现有 30 张卡片的 `summary/detail` 拆成候选事实，
dry-run 打印、`--apply` 才写；卡片不删。迁移后 `/记忆 统计` 应该显示"新增 N 条，冲突 0 条"。

---

## 4. 表情包与梗百科

### 4.1 采集：只收真表情

现有 `collect.is_sticker` 只认 `sub_type=1` 和 summary 关键字，补成：

| 关卡 | 规则 |
|---|---|
| 类型 | `sub_type==1` / `mface` / 带 `emoji_id` / summary 含"表情" |
| 来源 | 只认主人私聊（`user_id == allow_user_id`） |
| 尺寸 | 短边 ≥64px，长宽比 ≤3:1 |
| 体积 | ≤2MB |
| 去重 | `sha256` 已有，再补 `dHash` 近似去重（防改图重发） |
| 限额 | 每小时 ≤20 张、每天 ≤100 张（防刷屏） |
| 失败 | 下载/解码失败只记 debug，不打扰聊天 |

### 4.2 自动备注

收下后异步（不阻塞回复）调 vision 模型，产出结构化备注，写进新表：

```sql
CREATE TABLE IF NOT EXISTS sticker_meta (
  sha256      TEXT PRIMARY KEY,
  number      INTEGER,          -- 梗鲸编号（匹配上才有）
  quote       TEXT,             -- 图上的台词
  emotion     TEXT,             -- 开心/委屈/生气/无语/卖萌/嘲讽/疑惑/早晚安/日常
  caption     TEXT,             -- 画面一句话（"蓝发女孩抱着饭碗"）
  usage_note  TEXT,             -- 什么场合用（"他夸你的时候"）
  source      TEXT NOT NULL,    -- received / archive
  created_at  INTEGER NOT NULL
);
```

`caption/usage_note` 由一次视觉调用给出 JSON，失败就重试 1 次，再失败留空（不阻塞）。
现有的 16 张图 / 19 条备注跑一次 `scripts/backfill-notes.py --limit 200` 补齐。

### 4.3 梗百科入库

梗鲸档案馆 2956 枚、50 页，每枚详情页有：编号、标题（含台词）、角色标签、类型、
中文释义、出处、作者、预览图、原图。

一次性入库（`scripts/fetch-memepedia.py`）：

```sql
CREATE TABLE IF NOT EXISTS memes (
  number     INTEGER PRIMARY KEY,   -- 1..2956
  title      TEXT NOT NULL,
  quote      TEXT,                  -- 从标题 『』 里抽
  role       TEXT,                  -- DeepSeek娘 / 鲸鱼娘 / GPT娘 ...
  kind       TEXT,                  -- 梗图 / 漫画
  emotion    TEXT,                  -- 标题尾部的 气鼓鼓/委屈/...
  meaning    TEXT,                  -- 释义（≤300 字）
  origin     TEXT,                  -- 出处
  author     TEXT,
  page_url   TEXT,
  image_url  TEXT,
  fetched_at INTEGER NOT NULL
);
```

- 只存**文本元数据**，不重分发图片；标注来源"梗鲸 · aigengtu.com（非营利百科）"；
- 页面抓取限速 1 req/s，失败重试 2 次，中断可续（记录已入库编号）；
- 只抓一次，之后按需 `--refresh`。

### 4.4 表情 ↔ 梗的匹配

本地 2961 张是按 sha 命名的，和编号对不上。匹配方案（离线一次性）：

1. 下载 2956 张预览图（`img.aigengtu.com/0_preview/meme/NNN.webp`，约 60KB/张，临时目录，完事删除）；
2. 对预览图和本地图分别算 64 位 dHash，汉明距离 ≤8 视为同一张；
3. 命中写回 `sticker_meta.number`；冲突（一张本地图匹配多个编号）取距离最小的；
4. 抽样 20 张人工核对，命中率 <90% 就停，不硬上。

### 4.5 用起来：听懂 + 会用

- **听懂**：他发消息里出现梗词（`GLOSSARY` 命中）或发来一张梗图时，注入：
  ```
  【这个梗】
  台词：真当我是便宜货啊
  出处/含义：DeepSeek 低价出圈……（≤120 字）
  ```
  最多 2 条，总长 ≤240 字；她可以接梗，也可以不接。
- **会用**：选表情时，候选图优先匹配当前情绪 + 他上一句的关键词（用 `sticker_meta.usage_note`
  和 `caption` 做 BM25）；同一张 3 天内不重复；发图时不额外配文，避免"配图+台词"双重表演。
- 台词只在很低的概率（≤1/20）作为一句短话跟出去，避免变成梗复读机。

### 4.6 版权与克制

- 梗库只存文本，不打包图片；图仍走"她自己收藏/自己下载"；
- 页面注明来源，不做商业分发；
- 如果对方站点 robots 或条款不允许抓取，改为"用户手动导入 export 文件"，脚本只读本地文件。

---

## 5. QQ 空间日记

**前置**：装好的 NapCat 4.18.41 里搜不到 `qzone`，说明这个版本还没有 `send_qzone_msg`。
NapCat 的 [`SendQzoneMsg` 动作](https://cdn.jsdelivr.net/gh/NapNeko/NapCatQQ@main/packages/napcat-onebot/action/extends/SendQzoneMsg.ts)
和 [PR #2124](https://github.com/NapNeko/NapCatQQ/pull/2124) 才有完整空间 API。
所以要先把协议端升级到含该动作的版本，**这一步需要重启 NapCat，会掉线几分钟**。

- **链路**：`qqbot/modules/qzone.py` → `POST http://127.0.0.1:3001/send_qzone_msg`（OneBot 扩展动作）
- **内容**：不另起一套写作，直接用当天日摘要 + 2~3 条高显著度事件 + 当前心情，生成 120~300 字第一人称日记；
  语气跟她聊天一致，不写小标题、不写"今天真是充实的一天"这类套话。
- **配图**：可以配图（已确认）。从当天她发过的图/表情里挑 1 张；没有合适的就不配，不硬凑。
- **频率**：1 天最多 1 条（`20:00–23:00` 窗口）；最近两三条说过的事不重复写。
- **闸门**：`QZONE_ENABLED=false` 默认关；`QZONE_DRY_RUN=true` 默认只写审计；主人私聊 `/空间 预览` 可先看草稿。
- **失败**：接口 404/无权限 → 记审计并关闭当天；内容被吞 → 不重试（避免重复发）。
- **回滚**：关开关即可；已发的说说需要手动删（不做自动删，避免误删）。

---

## 6. B 站评论（本期暂停）

> 2026-11-05 决定：B 站先不做。下面的设计保留，等想做时按 P1 直接接。账号、cookie、WBI、风控这些调研结论仍然有效。

- **账号**：只用一个专用小号（主号有封号风险，需要你拍板）。
- **鉴权**：`SESSDATA`、`bili_jct`（csrf）、`buvid3`，存 `data/secrets/bili.json`（gitignore，权限 600）。
- **读**：`https://api.bilibili.com/x/web-interface/view`（视频信息）、
  `x/v2/reply`（评论列表）；需要 WBI 签名的接口按 [WBI 规则](https://github.com/SocialSisterYi/bilibili-API-collect) 签名（md5 + 混淆表，纯标准库可实现）。
- **写**：`POST https://api.bilibili.com/x/v2/reply/add`，
  参数 `oid=aid`、`type=1`、`message`、`csrf=bili_jct`，带真实 UA + `Referer: https://www.bilibili.com/`；
  成功返回 `rpid` 存库，失败按 `code` 分类（-101 未登录、-111 csrf 失败、12002 被风控）。
- **内容**：只在她真看过的视频下评论（`bili_watch` 表，来自现有"刷到"管线）；
  1 条 ≤40 字，不碰政治/价格/医疗/人身攻击；不 @ 人、不带链接。
- **频率**：3 条/天，同一 UP 主 7 天最多 1 条，两次评论间隔 ≥30 分钟；被风控立即停 24 小时。
- **闸门**：`BILI_ENABLED=false`、`BILI_DRY_RUN=true`；`/bili 预览` 看草稿，`/bili 停` 一键停。
- **删除**：提供 `x/v2/reply/del` 的封装，只删自己发的（存了 rpid）。

---

## 7. 抖音点赞

- **形态**：独立 Chrome/Edge profile（`data/browser/douyin`，放在 H 盘，绝不放 C 盘默认目录），
  `--remote-debugging-port=9222 --headless=new`（或窗口移到屏幕外）。
- **通信**：CDP。项目是零第三方依赖，Python 标准库没有 WebSocket 客户端 →
  自己写一个最小 RFC6455 客户端（握手 + 帧，约 200 行，`qqbot/cdp.py`），只支持文本帧，够用。
- **登录**：首次手动扫码（截图落到 `data/browser/douyin-login.png`），登录态留在 profile 里；
  检测到验证码/滑块立即停手，当天不再试。
- **浏览**：用页面内 `fetch` 调抖音自己的接口（签名由页面脚本自动加，不自己算 a_bogus）：
  推荐流 → 取 `aweme_id/desc/author/统计` → 按人格挑 3~5 个"她真会看"的（游戏/编程/猫/做饭）。
- **点赞**：在页面上下文里按 `aweme_id` 找按钮点击，等接口回包确认成功才算数；
  页面不可见（锁屏/最小化）时用 `Runtime.evaluate` 触发。
- **分享给主人**：点赞之后，如果这条视频她自己觉得有趣（游戏/编程/猫/做饭/离谱新闻里选），
  把链接 + 一句她自己的话私聊发给主人，1 天最多 2 条；分享也走 Guard 和审计。
  只发链接和一句话，不搬运视频、不截图。
- **频率**：点赞 5 个/天，两个动作间隔 ≥8 秒（Aurabot 的 900ms 限速更保守地取 8s），
  同一作者 3 天最多 1 次；不评论、不私信、不关注。
- **闸门**：`DOUYIN_ENABLED=false`、`DOUYIN_DRY_RUN=true`；`/抖音 停` 立即杀浏览器进程。
- **风险**：账号被限流/验证是常态；只点赞的封号风险低于评论，但仍需小号 + 每日上限 + 验证码即停。

---

## 8. 工程可靠（借鉴 Aurabot）

### 8.1 权限矩阵

| 能力 | 默认 | 谁能触发 | 额度 | 试运行 | 自动停 |
|---|---|---|---|---|---|
| QQ 空间发说说 | 关 | 她自己（定时） | 1/天 | 是 | 连续失败 2 次当天停 |
| B 站评论 | 关 | 她自己（看完视频后） | 3/天 | 是 | code 12002 停 24h |
| 抖音点赞 | 关 | 她自己（刷到后） | 5/天 | 是 | 验证码即停 |
| 抖音分享给主人 | 关 | 她自己（觉得有趣） | 2/天 | 是 | 连续失败 2 次当天停 |
| 表情收藏 | 开 | 主人私聊 | 20/时、100/天 | — | 超限丢弃 |
| 记忆写入 | 开 | 系统 | 无限 | — | 证据校验失败丢弃 |

所有"对外写"统一走 `Guard.check(capability, action, target)`：

```python
class Decision(NamedTuple):
    allowed: bool
    dry_run: bool
    reason: str          # disabled / quota / cooldown / owner_only / circuit_open

def check(cap, action, target) -> Decision
def record(cap, action, target, payload, result, error="")   # 写 audit
```

### 8.2 审计

```sql
CREATE TABLE IF NOT EXISTS audit (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  at             INTEGER NOT NULL,
  module         TEXT NOT NULL,
  action         TEXT NOT NULL,
  target         TEXT NOT NULL DEFAULT '',
  payload_digest TEXT NOT NULL DEFAULT '',   -- sha256 前 16 位，不存原文
  result         TEXT NOT NULL,              -- ok / dry_run / denied / failed
  error          TEXT NOT NULL DEFAULT ''
);
```

`/审计 20` 看最近 20 条；`logs/audit.log` 同步一行一条（JSONL，滚动 5MB × 2）。

### 8.3 外部调用

- 统一 `http_json(url, method, headers, body, timeout=10, retries=1)`，禁止裸 `urlopen`；
- 每个外部服务一个**令牌桶**（B 站 1/3s，空间 1/10min，抖音 1/8s）+ 指数退避重连；
- **熔断**：连续 3 次失败 → 打开 30 分钟，期间只记审计；
- 超时/异常分类：网络错重试，鉴权错（401/403）不重试并告警到 `/status`。

### 8.4 可靠性与可观测

- 模块 `tick` 出错只禁用自己，不影响主循环（与 Aurabot 的出错隔离一致）；
- `main.py` 启动日志打印每个模块的 `enabled/dry_run/quota`；
- 新增 `/健康` 指令：每模块 `ok / 最近成功 / 最近失败 / 配额余量`；
- supervisor 已就位（进程级拉起），本方案不重复做；NapCat 仍需手动重启（管理员权限）；
- 配置分两层：`.env`（开关/额度）+ `data/secrets/*.json`（cookie，不进 git、不打日志）。

### 8.5 测试策略

| 层 | 内容 |
|---|---|
| 单元 | 档案页/详情页解析、dHash、BM25 召回打分、WBI 签名、Guard 配额/冷却 |
| 契约 | 录一份真实响应做 fixture：B 站 reply/add 成功/风控、空间 404、抖音 CDP 回包 |
| 集成 | 假适配器 + Guard：dry-run 时**断言没有任何网络写请求** |
| 端到端 | `scripts/smoke-channels.py --dry-run`：跑一遍空间/B站/抖音的"该不该发"判断 |
| 回归 | 现有 888 测试必须全绿；记忆段改写后 eval 22 条重跑，允许重存基线（需你确认） |

---

## 9. 配置清单（新增）

```ini
# 记忆
MEMORY_FACTS_ENABLED=true
MEMORY_FACT_BUDGET=600
MEMORY_EXTRACT_EVERY=10

# 表情/梗
STICKER_COLLECT_ENABLED=true
STICKER_HOURLY_MAX=20
STICKER_DAILY_MAX=100
MEMEPEDIA_ENABLED=true

# 空间
QZONE_ENABLED=false
QZONE_DRY_RUN=true
QZONE_DAILY_MAX=1

# B 站（本期不做，保留配置位）
BILI_ENABLED=false
BILI_DRY_RUN=true
BILI_DAILY_MAX=3
BILI_SECRET_FILE=data/secrets/bili.json

# 抖音
DOUYIN_ENABLED=false
DOUYIN_DRY_RUN=true
DOUYIN_DAILY_MAX=5
DOUYIN_SHARE_ENABLED=true
DOUYIN_SHARE_DAILY_MAX=2
DOUYIN_PROFILE=data/browser/douyin
DOUYIN_CDP_PORT=9222
```

---

## 10. 分期与验收

| 期 | 内容 | 估时 | 验收（DoD） |
|---|---|---|---|
| P0 | 记忆 v2（facts/来源/更正/遗忘/档案）+ 命令 + 迁移脚本 | 2~3 天 | `/记忆 来源` 能追到原话；30 张卡片迁移 dry-run 通过；测试 +25 |
| P0 | 表情采集加固 + 备注 + 梗库入库 + 匹配（抽样 ≥90%） | 2~3 天 | 新表情自动带 `caption/emotion`；梗库 2956 条可查；`/梗 001` 返回台词+释义 |
| P0 | Guard + audit + 试运行骨架 + `/健康` | 1~2 天 | dry-run 下所有渠道零网络写；审计可查；测试覆盖配额/冷却 |
| P1 | QQ 空间：升级 NapCat + 发日记（1 条/天）+ 草稿预览 | 1~2 天 | 真发一条（dry-run 演练 3 天）；失败可查审计 |
| P1 | B 站评论 | 暂缓 | 你说先不做；设计已留，要做时直接接 |
| P2 | 抖音：CDP 客户端 + 登录 + 浏览 + 点赞 + 分享给主人 | 4~5 天 | 真点赞 3 个并分享 1 条；验证码即停；浏览器独立 profile |
| P2 | 文档 + 评测补充 + 全量回归 | 1 天 | 888+ 测试全绿，eval 22/22，README/PROGRESS 更新 |

**总估时**：10~15 个工作日，按 P0 → P1 → P2 顺序交付，每期单独 commit + 测试。

---

## 11. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| B 站/抖音账号被限流或封 | 小号报废 | 专用小号、每日上限、验证码即停、先 dry-run 一周 |
| 抖音改版 / 签名变 | 点赞失效 | 只走页面内 fetch，不自己算签名；失效时模块自动禁用并告警 |
| NapCat 升级引入不稳定 | 掉线 | 升级前备份 `tools/NapCat-*` 配置，升完先 `/status` + 一条测试消息；不行就回滚旧目录 |
| 梗鲸抓取不符条款 | 侵权风险 | 只存文本、注明来源、提供 `--no-network` 手动导入模式 |
| 记忆误记/张冠李戴 | 人设崩 | 证据必须能在原话找到；`/记忆 来源` 可追；更正/遗忘不删数据 |
| 空间/评论内容翻车 | 社交尴尬 | 一律 dry-run 预览；内容规则禁政治/价格/医疗；1 天 1 条上限 |
| 上下文被记忆撑爆 | 回复变慢/变贵 | 记忆 600 字上限 + top6；每轮 token 预算不变 |

回滚：每个模块一个开关；关掉就完全回到当前版本。数据表只增不删，回滚不丢历史。

---

## 12. 已确认的决策（2026-11-05）

| # | 问题 | 决定 |
|---|---|---|
| 1 | B 站 | **先不做**；设计保留，以后要接再接 |
| 2 | 抖音 | 接受扫码 + 独立 profile（H 盘）；**只点赞、不评论**；有趣的视频**分享给主人** |
| 3 | 空间 | **可以配图**；**最多 1 条/天** |
| 4 | 梗库 | 只存文本元数据，不重分发图片 |
| 5 | NapCat | **现在升级**到 v4.18.42（含 qzone API） |
| 6 | 记忆更正 | 在 QQ 里用 `/记忆 更正`（本地文件也能改，以 QQ 为准） |

---

## 13. 和"其他大肥鱼"的差异（这份方案之后）

| 维度 | 完成 P0 后 | 完成 P1/P2 后 |
|---|---|---|
| 记忆 | 有来源、可更正、可遗忘、有人物档案——追平 dafeiyu-dot | 记忆宫殿级别的可维护性（没有 UI，但指令齐全） |
| 表情/梗 | 真表情 + 自动备注 + 2956 条梗库——超过默认 10 张的插件 | 听得懂梗、用得上台词 |
| 社交 | 仍然只有聊天 | 真发空间、真点赞、把有趣的分享给你（B 站暂缓） |
| 工程 | 有 Guard/审计/试运行——补上最大短板 | 模块化 + 热加载 + 健康检查，接近 Aurabot 的内核 |
| 人格 | 不变：仍然会拒绝、会反省、会改口——这块我们本来就领先 | 不变 |
