# 语音回复

> 真实聊天里，语音条本身就是那句话，不应该再用文字重复一遍。

## 语音标记

模型偶尔会写出：

```text
{{语音：喂，能听到吗？}}
```

系统会把它解析成“这句话用语音发”，并从正文里移除标记，绝不会把
`{{语音：...}}` 当文字发给用户。也兼容英文 `{{voice:...}}`。

## 行为

- 只在 `chat` / `proactive` 回复、文字较短、没有图片/表情/画图标记时考虑语音；
- 命中后只发语音条，**不再发送同一段文字**；
- 语音合成或发送失败，才用同一段文字兜底发一次；
- 语音消息在数据库里只落一条，`origin='voice'`，不会出现文字 + 语音两条；
- `/语音` 命令仍然可以单独发一条测试语音。

## CosyVoice 2

本地 sidecar，安装、参考音频和配置见 [cosyvoice.md](cosyvoice.md)。

```dotenv
VOICE_PROVIDER=cosyvoice
COSYVOICE_URL=http://127.0.0.1:50000
COSYVOICE_REF_AUDIO=tools/cosyvoice/refs/ref.wav
COSYVOICE_PROMPT_TEXT=参考音频逐字稿
COSYVOICE_INSTRUCT=用轻柔、害羞、18岁女生的语气说这句话。<|endofprompt|>
COSYVOICE_TIMEOUT=60
```

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
