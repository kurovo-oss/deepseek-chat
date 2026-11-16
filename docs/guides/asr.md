# 可选本地 ASR：SenseVoiceSmall + sherpa-onnx

> 默认关闭。模型/二进制缺失时自动退回关键帧理解。  
> **所有临时音频、下载和中间文件都在项目目录，不写 C 盘。**

## 目录

```text
tools/
  asr/
    sensevoice/
      model.int8.onnx
      tokens.txt
    bin/
      sherpa-onnx-offline.exe
      *.dll
  ffmpeg/
    ffmpeg.exe
```

`tools/` 已被 gitignore；模型、ffmpeg、音频都不要提交。

## 安装

优先支持两种运行时：

- **Python 包**：`tools\python\python.exe -m pip install sherpa-onnx`，本项目已安装时无需 CLI；
- **CLI**：从 `sherpa-onnx` 官方 release 下载 Windows 版 `sherpa-onnx-offline`，放进 `tools/asr/bin/`。

模型：从 `sherpa-onnx` 官方 release 或 Hugging Face 下载 SenseVoiceSmall，确认版本后把
`model.int8.onnx`、`tokens.txt` 放进 `tools/asr/sensevoice/`。

ffmpeg：放进 `tools/ffmpeg/ffmpeg.exe`。如果安装了 `imageio-ffmpeg`，也可以直接用它的
`get_ffmpeg_exe()` 路径。

4. 在 `.env` 中开启：

```dotenv
ASR_ENABLED=true
ASR_BIN=tools/asr/bin/sherpa-onnx-offline.exe
ASR_MODEL=tools/asr/sensevoice/model.int8.onnx
ASR_TOKENS=tools/asr/sensevoice/tokens.txt
ASR_FFMPEG=tools/ffmpeg/ffmpeg.exe
ASR_LANGUAGE=zh
ASR_MAX_SECONDS=180
ASR_TIMEOUT=120
ASR_TMP_DIR=data/tmp/asr
```

5. 重启机器人。

## 临时目录保护

`qqbot/asr.py` 会：

- 把 `ASR_TMP_DIR` 限制在项目目录内；
- 如果配置误填 `C:`，自动回退到项目的 `data/tmp/asr`；
- 给 ffmpeg / sherpa-onnx 子进程设置 `TMP`、`TEMP`、`TMPDIR`；
- 转写/下载结束后删除本次任务的临时目录。

因此模型和音频不会默认落到 `C:\Users\...\AppData\Local\Temp`。

## 工作流

```text
抖音视频页
  → 取 play_addr
  → 下载到项目 data/tmp/asr（临时）
  → ffmpeg 转 16k 单声道 WAV
  → sherpa-onnx-offline + SenseVoiceSmall
  → transcript
  → 与关键帧一起交给多模态模型总结
  → 删除临时音频
```

ASR 失败时只返回空 transcript，视频仍会按关键帧总结；不会阻塞聊天。

## 运行时选择

包装器会优先使用存在的 `sherpa-onnx-offline.exe`；没有 CLI 时自动退回
`sherpa_onnx` Python 包。两种方式共用同一套模型和临时目录约束。

## 命令行约定

```text
sherpa-onnx-offline.exe
  --tokens=...
  --sense-voice-model=...
  --use-itn=true
  --language=zh
  audio.wav
```

不同 release 的参数可能不同，先跑一次 `--help`。如果参数变了，只需要改
`qqbot/asr.py` 里的 `transcribe_video()`，不影响上层。

## 限制

- 只取前 `ASR_MAX_SECONDS` 秒，默认 180 秒；
- 下载上限 80MB；
- 背景音乐、多人说话、强噪声仍可能识别错；
- 纯音乐/无口播视频会得到空文本；
- 模型和 ffmpeg 的许可证需要单独核对；
- 不把模型塞进安装包，保持安装包体积可控。

## 测试

单元测试不加载真实模型：

```powershell
tools\python\python.exe -m pytest -q tests\test_asr.py
```

真实体验需要手动放好模型和 ffmpeg，发一条抖音链接或让她分享一次。
