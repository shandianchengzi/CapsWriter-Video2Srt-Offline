from collections.abc import Iterable
from pathlib import Path


# 服务端配置
class ServerConfig:
    addr = '0.0.0.0'
    port = '6016'

    format_num = True  # 输出时是否将中文数字转为阿拉伯数字
    format_punc = True  # 输出时是否启用标点符号引擎
    format_spell = True  # 输出时是否调整中英之间的空格


# 客户端配置
class ClientConfig:
    addr = '127.0.0.1'          # Server 地址
    port = '6016'               # Server 端口

    # Quicker 配置
    QUICKER_HOST = "127.0.0.1"  # Quicker 本地服务默认主机地址
    QUICKER_PORT = 6655         # Quicker 本地服务默认端口
    QUICKER_PASSWORD = "Iloveyou"# 如果 Quicker 设置了验证码/密码，请在这里填写

    shortcut     = 'CapsLock'  # 控制录音的快捷键，默认是 CapsLock
    hold_mode    = True         # 长按模式，按下录音，松开停止，像对讲机一样用。
                                # 改为 False，则关闭长按模式，也就是单击模式
                                #       即：单击录音，再次单击停止
                                #       且：长按会执行原本的单击功能
    suppress     = False        # 是否阻塞按键事件（让其它程序收不到这个按键消息）
    restore_key  = True         # 录音完成，松开按键后，是否自动再按一遍，以恢复 CapsLock 或 Shift 等按键之前的状态
    threshold    = 0.3          # 按下快捷键后，触发语音识别的时间阈值
    paste        = True         # 是否以写入剪切板然后模拟 Ctrl-V 粘贴的方式输出结果
    restore_clip = True         # 模拟粘贴后是否恢复剪贴板

    save_audio = False           # 是否保存录音文件
    audio_name_len = 20         # 将录音识别结果的前多少个字存储到录音文件名中，建议不要超过200

    trash_punc = '，。,.'        # 识别结果要消除的末尾标点

    hot_zh = True               # 是否启用中文热词替换，中文热词存储在 hot-zh.txt 文件里
    多音字 = True                  # True 表示多音字匹配
    声调  = False                 # False 表示忽略声调区别，这样「黄章」就能匹配「慌张」

    hot_en   = True             # 是否启用英文热词替换，英文热词存储在 hot-en.txt 文件里
    hot_rule = True             # 是否启用自定义规则替换，自定义规则存储在 hot-rule.txt 文件里
    hot_kwd  = True             # 是否启用关键词日记功能，自定义关键词存储在 keyword.txt 文件里

    mic_seg_duration = 15           # 麦克风听写时分段长度：15秒
    mic_seg_overlap = 2             # 麦克风听写时分段重叠：2秒

    file_seg_duration = 25           # 转录文件时分段长度
    file_seg_overlap = 2             # 转录文件时分段重叠


class ModelPaths:
    model_dir = Path() / 'models'
    paraformer_path = Path() / 'models' / 'paraformer-offline-zh' / 'model.int8.onnx'
    tokens_path = Path() / 'models' / 'paraformer-offline-zh' / 'tokens.txt'
    punc_model_dir = Path() / 'models' / 'punc_ct-transformer_cn-en'


class ParaformerArgs:
    paraformer = f'{ModelPaths.paraformer_path}'
    tokens = f'{ModelPaths.tokens_path}'
    num_threads = 6
    sample_rate = 16000
    feature_dim = 80
    decoding_method = 'greedy_search'
    debug = False




# Windows desktop Settings bridge. Existing source installs without a settings file
# keep the original defaults. Only explicitly supported legacy fields are mapped.
# This file is loaded by the legacy client/server; the new tray reads the same JSON.
def _load_windows_desktop_overrides():
    import json
    import os
    import sys
    if sys.platform != "win32":
        return
    settings_file = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CapsWriterLite" / "settings.json"
    try:
        values = json.loads(settings_file.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            return
    except (OSError, ValueError):
        return
    client_mapping = {
        "client_addr": "addr", "client_port": "port",
        "hold_mode": "hold_mode", "suppress": "suppress",
        "restore_key": "restore_key", "threshold": "threshold",
        "paste": "paste", "restore_clipboard": "restore_clip",
        "save_audio": "save_audio", "save_text": "save_text",
        "record_dir": "record_dir", "audio_name_len": "audio_name_len",
        "trash_punc": "trash_punc",
        "hot_zh": "hot_zh", "hot_en": "hot_en", "hot_rule": "hot_rule",
        "hot_kwd": "hot_kwd",
        "heteronym": "多音字", "tone": "声调",
        "mic_seg_duration": "mic_seg_duration", "mic_seg_overlap": "mic_seg_overlap",
        "file_seg_duration": "file_seg_duration", "file_seg_overlap": "file_seg_overlap",
        "quicker_host": "QUICKER_HOST", "quicker_port": "QUICKER_PORT",
    }
    for source, field in client_mapping.items():
        if source in values:
            setattr(ClientConfig, field, values[source])
    if values.get("quicker_password"):
        ClientConfig.QUICKER_PASSWORD = values["quicker_password"]
    # Legacy keyboard hook accepts a single key, unlike the new chord parser.
    if values.get("shortcut") and "+" not in values["shortcut"]:
        ClientConfig.shortcut = values["shortcut"]
    for source, field in {"server_addr": "addr", "server_port": "port",
                          "format_num": "format_num", "format_punc": "format_punc",
                          "format_spell": "format_spell"}.items():
        if source in values:
            setattr(ServerConfig, field, values[source])


ClientConfig.save_text = True  # Preserve old default when no new settings exist.
ClientConfig.record_dir = str(Path())
_load_windows_desktop_overrides()
