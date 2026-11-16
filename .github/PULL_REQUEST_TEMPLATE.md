## 改了什么

<!-- 一句话说清这个 PR 做了什么 -->

## 为什么

<!-- 关联 issue；如果是修 bug，写清触发条件 -->

## 怎么验证

<!-- 测试命令与结果；涉及界面给截图（放描述里，不要提交进仓库） -->

```
tools\python\python.exe -m pytest -q
```

## 自查

- [ ] 改行为的都带了测试；修 bug 带了能复现它的回归测试
- [ ] `tools\python\python.exe -m pytest -q` 全绿
- [ ] 改过人格 / 提示词 / 情绪 / 记忆注入的话，跑过 `evals.run --compare evals/compact.json` 并附对比
- [ ] `prompts/system.md` 与 `defaults/prompts/system.md` 没有意外漂移（如改动）
- [ ] 没有提交 `.env`、`data/`、`logs/`、`backups/`、`dist/`、`build/`、`tools/`
- [ ] 提交信息用中文 conventional 前缀，一个提交只做一件事
- [ ] 相关文档已同步（`docs/design/`、`docs/guides/`）
