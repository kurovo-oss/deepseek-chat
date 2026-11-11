# 抖音视频理解（关键帧）

> 目标：她分享视频前，至少看过几个真实画面，而不是只复读标题。

## 流程

1. 抖音播放页已经打开（分享流程本来就会打开）；
2. `video_understand.capture_frames` 找到 `<video>`，按 15% / 40% / 65% / 88% 暂停截帧；
3. 截图只裁剪播放器区域，压缩成 JPEG；
4. `vision.describe_frames` 把 4 张图一次交给多模态模型，结合标题/文案总结；
5. 摘要按 `aweme_id` 缓存到 `video_notes`；
6. `douyin.comment_on` 有摘要时按画面写短评，没有时保持原来的“只读标题”提示词；
7. 主人发来抖音链接时，`douyin.analyze_link` 先解析短链、打开视频页、截帧总结，再把摘要作为“他发来的视频”写进消息；失败则明确写“只看到链接/标题，别编视频内容”。

## 内容驱动选片

- 打开候选视频后先截 1 帧，让模型给 `score / tags / summary`；
- 分数低于 `DOUYIN_CONTENT_PROBE_MIN_SCORE` 就不点赞、不分享，直接跳过；
- 每轮最多预判 `DOUYIN_CONTENT_PROBE_MAX` 个候选，避免模型费用失控；
- 预判失败（模型/截图不可用）不阻塞旧流程，仍按标题处理；
- 分享前的完整 4 帧摘要优先；完整摘要失败时，才用预判摘要兜底。

## 连续浏览

`read_feed` 会按 `DOUYIN_FEED_SCROLL_ROUNDS` 向下滚动，按 `aweme_id` 去重，
最多读 12 条候选；滚不动或没有新卡片就停止。

## 观看记忆

分享/链接分析得到摘要后写入 `video_notes`，最近 2 天的摘要会以
`【最近看过的视频】` 进入上下文，最多 2 条 / 240 字。它只帮助接话，
不会把视频内容写成事实卡。

## 降级

- 找不到 `<video>`、视频未加载、验证码、截图失败 → 返回空摘要；
- 空摘要时，短评继续禁止“视频里/看完了”等说法；
- 不下载原视频，不绕过验证码，不访问外部平台。

## 缓存

`video_notes` 以 `aweme_id` 为主键，保存 `desc_hash`、`summary`、`frames`、时间。
`desc_hash` 变化时缓存失效，避免标题/文案变了还复用旧摘要。

## 配置

```dotenv
VIDEO_UNDERSTANDING_ENABLED=true
DOUYIN_CONTENT_PROBE_ENABLED=true
DOUYIN_CONTENT_PROBE_MIN_SCORE=0.42
DOUYIN_CONTENT_PROBE_MAX=3
DOUYIN_FEED_SCROLL_ROUNDS=2
```

关闭后抖音分享恢复只读文案。`VISION_ENABLED` 仍然是总开关。

## 局限

- 关键帧能看画面、字幕、操作步骤，听不到口播；
- 纯口头讲解、没有字幕的视频仍需要 ASR（当前未接入）；
- 抖音验证码/风控时只能降级，不硬闯；
- 多模态调用会增加一次模型费用，只在真正分享时触发并缓存。
