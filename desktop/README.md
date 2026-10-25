# CapsWriter Lite — Windows 原版大模型托盘语音输入

此程序是仓库现有 CapsWriter 的新增桌面入口，保留原来的音视频转字幕功能和源码，不更换原版的语音或标点模型。

## 模型和启动策略

原版模型保持不变：
- Paraformer：paraformer-offline-zh/model.int8.onnx
- CT-Transformer：punc_ct-transformer_cn-en/

第一次运行，可以在「语音与模型」选择已有 models 目录，也可以从本仓库 [Release 1](https://github.com/shandianchengzi/CapsWriter-Video2Srt-Offline/releases/tag/1) 下载原版 models.zip（约 1.18 GB）。模型在 %LOCALAPPDATA%\\CapsWriterLite\\models 保存；断点续传，使用 Release 文件的 SHA256 校验，下载后解压并自动删除重复的 ZIP。更新应用程序不会重新下载模型。

启动时后台初始化 Paraformer 和 CT-Transformer，**只初始化一次，并保持常驻内存**。关闭设置窗口不会结束程序；托盘「完全退出」才释放模型。Windows 重启后仍然需要初始化一次；ONNX Runtime 推理会话不能简单序列化到磁盘实现零耗时重启。可设置当前用户的开机自启动，由系统登录时后台预加载，日常按键直接使用。

## 下载及安装

Windows 10/11 x64 用户请到 [v0.3.2 Release](https://github.com/shandianchengzi/CapsWriter-Video2Srt-Offline/releases/tag/v0.3.2) 直接下载并运行 `CapsWriter-Lite-0.3.2-x64-Setup.exe`。NSIS 安装程序可选择安装路径、创建桌面／开始菜单快捷方式、在 Windows 设置中卸载。无需手动解压或移动依赖文件。首次安装仍可选择原有模型文件夹。

应用每次启动由轻量后台更新管理器启动 Python 语音托盘，托盘里的「检查软件更新」和设置页按钮都会触发更新检查。若发现新版，先尝试 `.blockmap` 差分下载，遇到差分失败自动下载完整安装包并支持断点续传；下载完征询你是立即重启安装还是下次退出时安装。模型、热词配置和日志均存放在 `%LOCALAPPDATA%\\CapsWriterLite`，不受安装目录更新影响。

由于历史 v0.2.0 ZIP 版本无 NSIS 更新数据，首次迁移至 v0.3.2 必须完整安装。完成后才具备跨安装版差分更新条件。

## 操作说明

1. 下载 v0.3.2 Release 的 `CapsWriter-Lite-0.3.2-x64-Setup.exe` 并直接运行安装。
2. 首次运行配置模型目录，等待托盘提示「已就绪」。
3. 按住 CapsLock 说话，松开后识别并输入到当前程序。原版大模型常驻，关闭/再次打开设置不重复加载。
4. 系统托盘菜单可打开设置、暂停输入、手工纠正上一次识别、打开日志、完全退出。
5. 可在「热词与记忆」设置「安格 = angr」等替换规则，保存后立即生效。
6. 若输入文字之后在**支持 UI Automation 的标准文本框**中修改了本次插入内容，程序尝试识别该处单次纠错，并询问是否保存热词。不会自动保存未经确认的修改。部分富文本编辑器和权限较高的窗口无法监测，使用托盘「纠正上次识别」手动添加即可。
7. Quicker 默认关闭。在「Quicker」填写 127.0.0.1、本机 WebSocket 端口（默认 6655）、密码和「口令 = 动作 UUID」，可以测试连接。仅当语音结果与口令完全一致时才触发动作，不会把文本和动作混合执行。


## v0.3.2 图形化配置（与原版兼容）

- **快捷键与输入**：默认 CapsLock，可改成 F8、右 Alt 等单键，或 Ctrl+Alt+V 等组合键。可选长按说话/再次按键结束、屏蔽原按键、原键恢复、最短录音时间、粘贴输入/逐字键入、恢复剪贴板。组合键不能屏蔽主键的原生输入（会在设置时校验）。
- **保存与日记**：可独立启用“保存 WAV 录音”和“保存 Markdown 识别文字”，可选任意本地文件夹、文件名截取长度。只有选中才会将录音或语音文本写入该目录。默认均关闭；没有默认偷偷记录语音内容。文字按照 年/月/日.md 组织，启用关键词日记可额外按关键词分类。
- **文本格式**：可独立选择原版 CT-Transformer 自动标点、中文数字格式化、中英空格整理。句尾标点可选 strip（按照指定字符移除，旧版默认）、keep（保留）或 append（缺少时补上指定符号）。
- **热词词库**：首次启动复制仓库已有的 hot-zh.txt、hot-en.txt、hot-rule.txt、keywords.txt 到 %LOCALAPPDATA%\\CapsWriterLite\\hotwords，**不会覆盖之前保存的本地词库**。可从旧版本的程序根目录再导入这些 txt，按条目合并而不覆盖已保存的同名规则。5 个子页展示中文/英文/规则/日记关键词/纠错记忆。pinyin 多音字及声调匹配也可切换。
- **Quicker**：GUI 中可设置端口、密码、命令；旧 hot-rule.txt 中带 UUID 的原 Quicker 动作也会保留，但仅当“启用 Quicker”开启时调用。
- **高级**：听写长度与重叠秒数即时生效；模型推理线程调整需要下次重启；旧版网络客户端/服务端配置映射到原版 config.py 的对应参数，旧版程序重新启动才生效。

默认用户词库与模型分开放在用户数据目录，**安装、自动升级不会删除已有热词、个人纠错、Quicker 设置和已经保存的录音/日记**。模型仍是原版大模型，并非较小的新模型。


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
只生成安装器、blockmap 和 latest.yml 发布文件；不再发布 ZIP。master 分支更新后自动运行 Windows 构建、NSIS 静默安装验证并发布预览版。

用户配置与日志：%LOCALAPPDATA%\\CapsWriterLite\\settings.json、capswriter.log。
