# CosyVoice 2 本地 sidecar

> 独立模型服务，不装进项目 Python；主程序只调用 `127.0.0.1:50000`。
> 服务失败时语音自动降级为文字。

## 目录

```text
H:\deepseek-chat\tools\cosyvoice\
  CosyVoice\                    # 官方源码
  venv39\                       # 独立环境，复用已有 torch
  pretrained_models\
    CosyVoice2-0.5B\            # llm.pt / flow.pt / hift.pt / campplus.onnx ...
  refs\
    ref.wav                     # 3～10 秒参考音频
```

## 安装要点

- 复用 `F:\anacoder\envs\pytorch` 的 Python 3.9 + torch 2.8 cu126；
- `pyopenjtalk` 使用 `pyopenjtalk-prebuilt`（无需 C++ 编译）；
- `jieba_fast` 用纯 Python `jieba` shim；
- CosyVoice 依赖安装时跳过 `openai-whisper`、`pyworld`、`gradio`（推理不需要）。

## 启动

```powershell
H:\deepseek-chat\tools\cosyvoice\venv39\Scripts\python.exe `
  scripts\cosyvoice-server.py
```

健康检查：

```powershell
curl.exe http://127.0.0.1:50000/health
```

## 项目配置

```dotenv
VOICE_ENABLED=true
VOICE_PROVIDER=cosyvoice
COSYVOICE_URL=http://127.0.0.1:50000
COSYVOICE_REF_AUDIO=tools/cosyvoice/refs/ref.wav
COSYVOICE_PROMPT_TEXT=希望你能改变心意
COSYVOICE_INSTRUCT=
COSYVOICE_SPEED=0.95
COSYVOICE_TIMEOUT=60
```

`COSYVOICE_INSTRUCT` 越长越容易被读进音频；当前使用很短的指令，并已用小样验证不读出。

## 注意

- `ref.wav` 必须是单人、干净、无音乐，3～10 秒；
- 模型较大，首次加载需要时间；
- 服务只监听 `127.0.0.1`；
- 参考音频授权和声音人格权仍需自行确认；
- 不要把模型、参考音频提交进 git。
