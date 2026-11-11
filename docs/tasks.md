# 任务表、后台 worker 与完成通知

> 目标：她说“稍后告诉你”时，系统里真的有一个任务，而不是一句空承诺。

## 表

`tasks` 保存：

- `title` / `description`：任务内容与背景；
- `kind`：`promise` / `manual` / `reminder` / `followup`；
- `status`：`pending` / `running` / `done` / `failed` / `cancelled`；
- `due_at`：何时开始处理；
- `result` / `error`：处理结果；
- `notify_at` / `notify_status` / `notified_at`：完成通知状态；
- `source_message_id`：从哪条用户消息来的。

## 承诺校验

回复发出前，`engine` 检查是否存在：

```text
稍后 / 晚点 / 等我 / 一会儿 / 完成 / 做完 / 弄完
    + 告诉你 / 跟你说 / 发你 / 给你看 / 通知你 / 找你
```

命中且当前消息没有对应任务时，`TaskService.note_promise()` 会建一个
`promise` 任务，默认 `TASK_PROMISE_MINUTES` 分钟后处理，避免同一消息重复建任务。

## Worker

`TaskService` 在后台线程中：

1. 找到期 `pending` 任务；
2. 标记 `running`；
3. 文字类任务调用模型尝试完成；`reminder` 只生成提醒；
4. 成功 → `done`，失败 → `failed`，两者都进入通知队列；
5. `notify_due` 按免打扰和每日通知上限决定是否发送。

通知走 `engine.send_proactive()`，但有自己的闸门：

- 免打扰时段不发送，30 分钟后重试；
- 每天最多 `TASK_NOTIFY_DAILY_MAX` 条；
- 发送失败保留待通知状态，5 分钟后重试。

## API / UI

- `GET /api/tasks`
- `POST /api/tasks/create`：`{title, description, due_minutes}`
- `POST /api/tasks/cancel`：`{id}`
- 计划页「任务队列」可以看到状态、结果，并手动安排/取消任务。

## 配置

```dotenv
TASKS_ENABLED=true
TASK_POLL_SECONDS=20
TASK_PROMISE_MINUTES=15
TASK_NOTIFY_DAILY_MAX=3
```

## 边界

- 现实操作、外部工具、无法完成的事，worker 只能诚实说没完成，不能编“已经好了”；
- 任务不会自动改人格、记忆或外部账号；
- 文本任务使用一次独立模型调用，成本可控；不要让它每轮空转；
- 任务失败也会通知，不会静默消失。
