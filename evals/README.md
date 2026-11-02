# 对话质量评测

单元测试证明"管线不崩"，这里证明"她说话对"。

## 怎么跑

```powershell
$env:PYTHONPATH = "H:\deepseek-chat"
tools\python\python.exe -m evals.run                      # 全跑（真模型，约 2 分钟）
tools\python\python.exe -m evals.run --only 指代           # 只跑一个
tools\python\python.exe -m evals.run --save evals/base.json
tools\python\python.exe -m evals.run --compare evals/base.json   # 看有没有退化
tools\python\python.exe -m evals.run --repeat 3                   # 每个场景最多试 3 次（默认 2）
```

改完 prompt 或引擎后跑一次：分数掉了就是改坏了，别靠肉眼。

## 检查点

每个场景（`cases.py`）可以有：

| 字段 | 含义 |
|---|---|
| `include` | 全部回复里至少命中一个正则 |
| `exclude` | 一个都不许出现 |
| `min_bubbles` / `max_bubbles` | 最后一条的条数 |
| `max_chars` | 最后一条的总字数 |

`include`/`exclude` 会编译，写错正则 `tests/test_evals.py` 会挂。

## 加场景

一条 `Case` 对应一次真实的翻车现场，别凭空编：

```python
Case(
    name="不复读梗",
    turns=("我已老实，求放过",),
    include=(r"放过|怎么|好嘛",),
    exclude=(r"已老实",),
    note="听懂就行，别跟着说",
)
```

## 注意

- 跑的是真模型，**同一个场景每次措辞不同**，所以检查点写宽松点（关键词，不是整句）。
- 评测时关掉了生活线、忙闲、表情包：这几个跟着日期/随机数变，会让分数不可复现。
- `baseline.json` 是 2026-11-02 的基线（18/18），拿它做对比。
