# 文档

这个目录按「谁要读」分类，不按时间分类：

- [`design/`](design/) —— 设计与规格。想改行为、想知道某个功能为什么这么设计时看这里
- [`guides/`](guides/) —— 安装与配置指南。装第三方组件、调语音/ASR/浏览器时看这里
- [`ops/`](ops/) —— 进度、审查与调研记录。想知道最近坏在哪、踩过什么坑时看这里
- [`persona/`](persona/) —— 人格卡与外观设定
- [`assets/`](assets/) —— 截图、形象素材和第三方归属声明

新增文档请放进对应子目录，并在本页登记（`tests/test_docs.py` 会检查有没有漏登记）。

## 设计规格

| 文档 | 内容 |
|---|---|
| [PRD.md](design/PRD.md) | 产品方向、边界、明确不做的事 |
| [MVP.md](design/MVP.md) | 工程规格：数据模型、token 预算、闸门参数、测试与验收 |
| [M2-memory.md](design/M2-memory.md) | 记忆系统：卡片、三重权重、抽取、注入、压缩、做梦 |
| [companion-v2.md](design/companion-v2.md) | 语义记忆（事实 / 证据 / 更正）与下一版方向 |
| [autonomy-v1.md](design/autonomy-v1.md) | 自主性：shadow / confirm / bounded_auto 三档与策略分 |
| [design-stages-6-9.md](design/design-stages-6-9.md) | 阶段 6–9 的设计总结 |
| [native-ui.md](design/native-ui.md) | 原生控制台（WinForms + WebView2）设计 |
| [ui-design.md](design/ui-design.md) | 控制台 WebUI 的设计方案与截图 |
| [tasks.md](design/tasks.md) | 任务队列与完成通知 |
| [ui-mockup.html](design/ui-mockup.html) | 可点原型：控制台九个页面（无后端，直接浏览器打开） |
| [chat-demo.html](design/chat-demo.html) | 可点原型：手机上的聊天体验（无后端，直接浏览器打开） |

## 使用指南

| 文档 | 内容 |
|---|---|
| [PC_ACCESS.md](guides/PC_ACCESS.md) | 让她「看得到这台电脑」的边界：截屏、状态、以及为什么默认不开 |
| [voice.md](guides/voice.md) | 语音回复：edge-tts / CosyVoice / GPT-SoVITS 三种后端 |
| [cosyvoice.md](guides/cosyvoice.md) | CosyVoice 2 本地服务：安装、参考音频、参数 |
| [asr.md](guides/asr.md) | 语音转写：sherpa-onnx + SenseVoice |
| [video-understanding.md](guides/video-understanding.md) | 视频理解：CDP 截帧与内容判断 |

## 运维与记录

| 文档 | 内容 |
|---|---|
| [PROGRESS.md](ops/PROGRESS.md) | 每个功能到底怎么实现的、当前做到哪了 |
| [audit-2026-11-08.md](ops/audit-2026-11-08.md) | 一次全功能审查：发现的问题与后续决定 |
| [RESEARCH-2026-10.md](ops/RESEARCH-2026-10.md) | 同类项目调研与缺口对照 |

## 人格与素材

| 文档 | 内容 |
|---|---|
| [persona/dafeiyu.md](persona/dafeiyu.md) | 完整角色卡：设定、示例对话、反模式 |
| [persona/look.md](persona/look.md) | 外观设定（画图与自述长相时以此为准） |
| [assets/NOTICE.md](assets/NOTICE.md) | 形象素材的署名与使用说明 |
| [assets/dafeiyu-LICENSE.md](assets/dafeiyu-LICENSE.md) | 《大肥鱼项目署名许可 1.0》全文 |

## 仓库根目录

| 文档 | 内容 |
|---|---|
| [README.md](../README.md) | 是什么、怎么装、怎么用 |
| [AGENTS.md](../AGENTS.md) | 给编码 agent 的交付合同（硬规则、测试、构建顺序） |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | 参与开发：环境、命令、PR 流程 |
| [SECURITY.md](../SECURITY.md) | 安全模型与漏洞报告方式 |
| [SAFETY.md](../SAFETY.md) | 账号与数据风险（使用前必读） |
| [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md) | 第三方组件与素材许可 |
| [CHANGELOG.md](../CHANGELOG.md) | 版本变更记录 |
