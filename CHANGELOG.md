# 变更记录

本文件记录对使用者可见的变化。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号用[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 变更

- 仓库结构整理：新增 `pyproject.toml`、`.editorconfig`，pytest 配置并入 `pyproject.toml`
- 新增 `CONTRIBUTING.md`、`SECURITY.md`、本文件，以及 GitHub issue / PR 模板
- 文档按 `design/` `guides/` `ops/` 分类，索引见 `docs/README.md`

## [0.1.0] - 2026-11-16

第一个可用版本。私聊对话、长期记忆、表情包、主动消息、语音、控制台、安装包都能跑。

### 新增

- **对话**：QQ 私聊收发（经 NapCat / OneBot 11 HTTP 上报）、白名单、消息幂等、300 字分段、指令系统
- **人格**：人格卡 + 场景 overlay + 确定性 turn plan + 动态范例 + 风格守卫；`prompts/system.md` 与出厂版 `defaults/prompts/system.md` 分离
- **记忆**：卡片 + 三重权重 + 自动抽取 + 注入 + 原话召回 + 上下文压缩 + 记忆本 + 待确认事实 + 做梦/睡前反省；只增不删
- **语义记忆 v2**：事实 / 证据 / 更正，`/记忆` 指令
- **配图**：图库入库、表情包按画面自动打标、看懂你发的图、自己画图
- **语音**：edge-tts / CosyVoice 2 / GPT-SoVITS 三种后端，语音优先发送
- **ASR**：sherpa-onnx + SenseVoice，听你发的语音
- **主动消息**：记忆里的未来事件到期 + 沉默跟进，带全套闸门（每日/每周上限、静默时段、情绪）
- **外部动作**：QQ 空间说说、抖音浏览与内容理解，默认试运行或关闭
- **任务与自主性**：任务队列、完成通知、承诺校验；`AUTONOMY_MODE` 默认 `shadow` / `confirm`
- **控制台**：本地 WebUI（总览 / 对话 / 人格 / 配置 / 日志）+ WinForms + WebView2 原生外壳
- **可靠性**：开机自启、文件日志、看门狗、失败回复重放、备份与恢复、运行日报
- **安装**：NSIS 安装包，按当前用户装到 `%LOCALAPPDATA%\Dafeiyu`，附带 WebView2 运行时
- **工程**：零第三方运行时依赖；`pytest` 全量单测；`evals/` 真模型评测；Windows CI

### 说明

- 本版本未做代码签名，SmartScreen 会提示未知发布者
- 第三方组件的许可与归属见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)

[Unreleased]: https://github.com/luowenjie/dafeiyu/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/luowenjie/dafeiyu/releases/tag/v0.1.0
