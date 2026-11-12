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

## 国内镜像

已确认可用：

- **模型（推荐）**：ModelScope
  - `https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained`
  - `pretrained_models.zip` 约 4.56GB
  - `G2PWModel.zip` 约 589MB
- **源码**：GitCode 镜像
  - `https://gitcode.com/gh_mirrors/gp/GPT-SoVITS`
  - 或 `https://gitcode.com/gh_mirrors/gpt/GPT-SoVITS`
- **整合包**：`hf-mirror.com` 的 `lj1995/GPT-SoVITS-windows-package`，约 6.7GB
- **PyPI**：清华 `https://pypi.tuna.tsinghua.edu.cn/simple` 或阿里云
- **PyTorch**：NJU 镜像 `https://mirrors.nju.edu.cn/pytorch/whl/cu118`

实测这些国内源当前约 **0.4～0.5MB/s**，4.56GB 仍要数小时；支持断点续传：

```powershell
curl.exe -L -C - -o pretrained_models.zip `
  https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/pretrained_models.zip
```

## 参考音频

- 3～10 秒，单人，无音乐，普通话；
- **当前 `refs/ref.wav` 只是用于跑通链路的测试音频，不是大肥鱼音色；正式使用前必须替换；**
- 逐字稿必须和音频一致；
- 放在 `tools/gpt-sovits/refs/ref.wav`；
- 不要用未经授权的真人/主播/明星声音。

## 当前运行时

本项目实际使用：

```text
H:\deepseek-chat\tools\gpt-sovits\venv39\
```

它复用了已有的 `F:\anacoder\envs\pytorch`（Python 3.9 + torch 2.8 cu126），
没有把 PyTorch 再装一遍。

Python 3.9 下：

- `pyopenjtalk-prebuilt==0.3.0` 替代源码编译版；
- `jieba_fast` 用纯 Python `jieba` 兼容 shim 代替（只是速度略慢）；
- `x_transformers<2.0`，否则 2.x 使用了 Python 3.10 的 `int | tuple` 语法。

## 启动 API

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-gpt-sovits.ps1
```

或手动：

```powershell
cd H:\deepseek-chat\tools\gpt-sovits\GPT-SoVITS
H:\deepseek-chat\tools\gpt-sovits\venv39\Scripts\python.exe api_v2.py `
  -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
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
