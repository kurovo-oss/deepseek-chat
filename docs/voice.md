# 语音回复

> 真实聊天里，语音条本身就是那句话，不应该再用文字重复一遍。

## 行为

- 只在 `chat` / `proactive` 回复、文字较短、没有图片/表情/画图标记时考虑语音；
- 命中后只发语音条，**不再发送同一段文字**；
- 语音合成或发送失败，才用同一段文字兜底发一次；
- 语音消息在数据库里只落一条，`origin='voice'`，不会出现文字 + 语音两条；
- `/语音` 命令仍然可以单独发一条测试语音。

## GPT-SoVITS（可选）

```dotenv
VOICE_ENABLED=true
VOICE_PROVIDER=gpt_sovits
GPT_SOVITS_URL=http://127.0.0.1:9880
GPT_SOVITS_REF_AUDIO=tools/gpt-sovits/refs/ref.wav
GPT_SOVITS_PROMPT_TEXT=参考音频的逐字稿
GPT_SOVITS_TEXT_LANG=zh
GPT_SOVITS_PROMPT_LANG=zh
GPT_SOVITS_TIMEOUT=12
```

API 只监听 `127.0.0.1`；超时/失败会降级为文字。GPT-SoVITS 返回 WAV 时，
如果项目里没有 ffmpeg，会直接发 WAV；有 ffmpeg 会转成 MP3。

## Edge 配置

```dotenv
VOICE_ENABLED=true
VOICE_PROVIDER=edge
VOICE_NAME=xiaoyi
VOICE_CHANCE=0.3
VOICE_MAX_CHARS=32
VOICE_RATE=+8%
VOICE_PITCH=+12Hz
VOICE_DIR=data/voice
```

`VOICE_CHANCE` 是情绪/长度都满足后的概率；不是每条都发。

## 失败降级

- 没装 `edge-tts`、断网、限流、协议端不接收 → 自动改发文字；
- 语音失败不影响正常回复；
- 语音文件仍只保存在 `data/voice/`，自动保留最近 60 条。
