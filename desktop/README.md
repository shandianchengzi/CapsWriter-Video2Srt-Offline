# CapsWriter Lite — Windows 原版大模型托盘语音输入

此程序是仓库现有 CapsWriter 的新增桌面入口，保留原来的音视频转字幕功能和源码，不更换原版的语音或标点模型。

## 模型和启动策略

原版模型保持不变：
- Paraformer：paraformer-offline-zh/model.int8.onnx
- CT-Transformer：punc_ct-transformer_cn-en/

第一次运行，可以在「语音与模型」选择已有 models 目录，也可以从本仓库 [Release 1](https://github.com/shandianchengzi/CapsWriter-Video2Srt-Offline/releases/tag/1) 下载原版 models.zip（约 1.18 GB）。模型在 %LOCALAPPDATA%\\CapsWriterLite\\models 保存；断点续传，使用 Release 文件的 SHA256 校验，下载后解压并自动删除重复的 ZIP。更新应用程序不会重新下载模型。

启动时后台初始化 Paraformer 和 CT-Transformer，**只初始化一次，并保持常驻内存**。关闭设置窗口不会结束程序；托盘「完全退出」才释放模型。Windows 重启后仍然需要初始化一次；ONNX Runtime 推理会话不能简单序列化到磁盘实现零耗时重启。可设置当前用户的开机自启动，由系统登录时后台预加载，日常按键直接使用。

## 操作说明

1. 下载 [Windows Release v0.2.0](https://github.com/shandianchengzi/CapsWriter-Video2Srt-Offline/releases/tag/v0.2.0) 中的 CapsWriterLite-v0.2.0-win64.zip，完整解压后启动 CapsWriterLite.exe。请保留 ZIP 内的整个应用文件夹（并非单 EXE 打包）。
2. 首次运行配置模型目录，等待托盘提示「已就绪」。
3. 按住 CapsLock 说话，松开后识别并输入到当前程序。原版大模型常驻，关闭/再次打开设置不重复加载。
4. 系统托盘菜单可打开设置、暂停输入、手工纠正上一次识别、打开日志、完全退出。
5. 可在「热词与记忆」设置「安格 = angr」等替换规则，保存后立即生效。
6. 若输入文字之后在**支持 UI Automation 的标准文本框**中修改了本次插入内容，程序尝试识别该处单次纠错，并询问是否保存热词。不会自动保存未经确认的修改。部分富文本编辑器和权限较高的窗口无法监测，使用托盘「纠正上次识别」手动添加即可。
7. Quicker 默认关闭。在「Quicker」填写 127.0.0.1、本机 WebSocket 端口（默认 6655）、密码和「口令 = 动作 UUID」，可以测试连接。仅当语音结果与口令完全一致时才触发动作，不会把文本和动作混合执行。

## 使用边界

- 语音和识别均在本地进行，音频仅保存在内存，不上传服务器、不默认生成录音文件。
- 该版本是说完再识别的离线输入工具；超过 20 秒的录音按块识别，可能出现分段边界重复或语义不连贯。
- 系统级 CapsLock Hook 会抑制原按键事件；如需实际切换大写锁定，可暂时退出软件。部分管理员权限窗口、系统安全桌面无法输入。
- 识别后若焦点切换，不会错误地输入到新窗口，而是将结果复制到剪贴板。
- 剪贴板恢复仅处理纯文本，不能保证恢复富文本、文件或图片。
- 对于任意应用的全文修改，不保证一定能通过 UI Automation 检测。不会存储编辑器全文，只有用户确认的热词映射才写入配置。

## 构建

Windows 10/11 x64，Python 3.10，源码安装：

    pip install -r desktop/requirements.txt
    python desktop/app.py

GitHub Actions 工作流文件：.github/workflows/build-capswriter-lite.yml。
仅打包程序和运行依赖，不把模型文件塞进 EXE；master 分支上 desktop/ 的更新自动运行 Windows 构建测试并上传 prerelease。

用户配置与日志：%LOCALAPPDATA%\\CapsWriterLite\\settings.json、capswriter.log。
