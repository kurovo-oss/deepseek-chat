# GPT-SoVITS 接入

> 这是**独立本地服务**，不装进项目 Python，也不进安装包。
> 项目只通过 `http://127.0.0.1:9880` 调它；服务挂了就降级文字。

## 目录

```text
H:\deepseek-chat\tools\gpt-sovits\
  GPT-SoVITS\          # 官方源码或整合包
  refs\
    ref.wav            # 3～10 秒参考音频
    ref.txt            # 参考音频逐字稿
  temp\                # 临时文件
  cache\               # HF/Torch 缓存
```

启动前设置：

```powershell
$env:TMP="H:\deepseek-chat\tools\gpt-sovits\temp"
$env:TEMP="H:\deepseek-chat\tools\gpt-sovits\temp"
$env:HF_HOME="H:\deepseek-chat\tools\gpt-sovits\cache\hf"
$env:TORCH_HOME="H:\deepseek-chat\tools\gpt-sovits\cache\torch"
$env:MODELSCOPE_CACHE="H:\deepseek-chat\tools\gpt-sovits\cache\modelscope"
```

## 参考音频

- 3～10 秒，单人，无音乐，普通话；
- 逐字稿必须和音频一致；
- 放在 `tools/gpt-sovits/refs/ref.wav`；
- 不要用未经授权的真人/主播/明星声音。

## 启动 API

```powershell
cd H:\deepseek-chat\tools\gpt-sovits\GPT-SoVITS
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

确认：

```powershell
curl.exe http://127.0.0.1:9880/docs
```

## 项目配置

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

重启机器人后生效。GPT-SoVITS 返回 WAV，项目有 ffmpeg 时会转 MP3。

## 行为

- 只对短句、无图片/表情包/画图标记的聊天回复使用；
- 成功：只发语音，不重复文字；
- 超时/失败：降级文字，不影响聊天；
- 参考音频缺失：直接跳过 GPT-SoVITS。

## 注意

- 整合包约 6～7GB，先确认磁盘和网络；
- 6GB 显存建议零样本推理，不要一上来微调；
- 服务只监听 127.0.0.1；
- 不要把模型、参考音频提交进 git。
