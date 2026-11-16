# 参与开发

先说清楚这个项目的定位：它是**一个人用的** QQ 陪伴机器人，不是要长成通用框架。所以接受改动的标准不是「功能多」，而是「这个人的日常没变坏」。

在动手之前请先读两处：

- [`AGENTS.md`](AGENTS.md)：交付合同（硬规则、测试要求、构建顺序），**改代码前必读**
- [`SAFETY.md`](SAFETY.md)：账号与数据风险，以及为什么建议只用小号

## 环境

| 项 | 要求 |
|---|---|
| 系统 | Windows 优先（路径、编码、OneBot 本地端口都按 Windows 写） |
| Python | 3.12，**用项目内解释器** `tools\python\python.exe`，不要用系统 python |
| 运行时依赖 | 零第三方依赖，只用标准库。新增依赖前请先开 issue 讨论 |
| 测试依赖 | `pytest`、`Pillow`（仅测试与截图相关用例用） |

首次拉取后如果还没有解释器，按 [`README.md`](README.md) 的「安装」一节准备 `tools\python`。

## 常用命令

```powershell
# 单元测试（改动后必须全绿）
tools\python\python.exe -m pytest -q

# 只跑一部分
tools\python\python.exe -m pytest tests/test_webui.py -q

# 真实模型评测（会花钱、需要 .env；不是单元测试）
$env:PYTHONIOENCODING="utf-8"
tools\python\python.exe -m evals.run --compare evals/compact.json

# 改过 C# 外壳之后
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1
powershell -ExecutionPolicy Bypass -File installer\build-installer.ps1
```

## 改动的硬要求

1. **一个提交只做一件事**，信息用中文 conventional 前缀：`feat:` `fix:` `docs:` `test:` `chore:` `refactor:`。
2. **改行为必须带测试**；修 bug 要留下能复现它的回归测试。
3. **交付前跑全量测试**，没全绿就不要说做完了。
4. **绝不提交** `.env`、`data/`、`logs/`、`backups/`、`dist/`、`build/`、`tools/`。
5. 测试里不允许调用真实 QQ / NapCat / 空间 / 抖音，也不允许给真实用户发消息。

完整的规则见 [`AGENTS.md`](AGENTS.md)，这里不重复。

## 文档

- 设计文档在 [`docs/design/`](docs/design/)，索引见 [`docs/README.md`](docs/README.md)
- 安装与配置指南在 [`docs/guides/`](docs/guides/)
- 进度与审查记录在 [`docs/ops/`](docs/ops/)

改了行为就顺手改文档：文档腐烂比代码腐烂更难发现。

## 提交 Pull Request

1. fork → 建分支（`feat/xxx`、`fix/xxx`），不要直接往 `main` 推
2. 跑 `tools\python\python.exe -m pytest -q`，贴结果
3. 说明「改了什么 / 为什么 / 怎么验证」，截图放在描述里而不是仓库里
4. 涉及人格、提示词、情绪、记忆注入的改动，附 `evals.run --compare` 的前后对比

## 纪律

- 不要在 PR 里附带无关的格式化、重命名、依赖升级
- 不要为了过测试放宽断言；如果评测标准本身有问题，改标准并在提交里说明理由
- 不要提交未经脱敏的日志、截图、聊天记录
