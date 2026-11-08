# 安装版打包说明

## 构建

NSIS 编译器放在 `tools\nsis\makensis.exe`，该目录不入库。构建脚本会把运行所需文件
收进 `build\installer\payload`，再压缩成安装包：

```powershell
powershell -ExecutionPolicy Bypass -File installer\build-installer.ps1
```

输出：

```text
dist\Dafeiyu-Setup-1.0.0.exe
```

构建只读取项目目录，不往 C 盘安装 NSIS；安装包本身建议用代码签名证书签名后再发布，
否则 Windows SmartScreen 会提示“未知发布者”。

## 安装行为

- 默认按当前用户安装到 `%LOCALAPPDATA%\Dafeiyu`，不需要管理员权限；
- 安装目录可以改；在开发机上测试时建议指定项目盘：

  ```powershell
  dist\Dafeiyu-Setup-1.0.0.exe /D=H:\Dafeiyu
  ```

- 静默安装可以带配置参数（`/D=` 必须放最后；地址含 `://` 时要加引号）：

  ```powershell
  dist\Dafeiyu-Setup-1.0.0.exe /S /MASTERQQ=123456 /APIKEY=sk-xxx `
    /MODELBASE="https://api.deepseek.com" /MODELID=deepseek-flash /D=H:\Dafeiyu
  ```

- 安装向导会填写主人 QQ、模型地址、模型名称、API Key；
- 如果安装目录已有 `.env`，不会覆盖；
- 安装包包含 Python、NapCat、WebView2 外壳和网页界面，不要求目标机器预装 Python；
- 目标机器需要 Microsoft Edge WebView2 Runtime，安装器会检测并引导下载；
- 卸载时询问是否删除 `data` 和 `.env`；选“否”会保留聊天记录，方便重装。

## 安装后还要做什么

1. 启动大肥鱼；
2. 打开 NapCat 完成 QQ 扫码登录（协议端配置和端口由项目脚本处理）；
3. 如果模型地址不是 DeepSeek，安装时改成对应的 OpenAI 兼容地址和模型名；
4. 首次主动消息/空间/抖音默认按 `.env.example` 的保守值运行，可在控制台里调整。

安装器不内置 API Key，也不内置 QQ 账号；这两项必须由使用者自己填写/登录。
