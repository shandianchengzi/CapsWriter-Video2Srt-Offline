"""Versioned per-user settings; older settings remain valid after upgrades."""
import json
import os
from pathlib import Path

APP_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "CapsWriterLite"
SETTINGS_PATH = APP_HOME / "settings.json"
DEFAULTS = {
    "model_dir": "",
    "run_at_login": False,
    "shortcut": "caps lock",
    "hold_mode": True,
    "suppress": True,
    "restore_key": False,
    "threshold": 0.3,
    "paste": True,
    "restore_clipboard": True,
    "save_audio": False,
    "save_text": False,
    "record_dir": str(APP_HOME / "records"),
    "audio_name_len": 20,
    "format_num": True,
    "format_punc": True,
    "format_spell": True,
    "trailing_mode": "strip",
    "trash_punc": "，。,.",
    "ending_symbol": "。",
    "mic_seg_duration": 25,
    "mic_seg_overlap": 2,
    "file_seg_duration": 25,
    "file_seg_overlap": 2,
    "num_threads": 4,
    "hot_zh": True,
    "hot_en": True,
    "hot_rule": True,
    "hot_kwd": True,
    "heteronym": True,
    "tone": False,
    "legacy_dir": "",
    "client_addr": "127.0.0.1",
    "client_port": 6016,
    "server_addr": "0.0.0.0",
    "server_port": 6016,
    "quicker_enabled": False,
    "quicker_host": "127.0.0.1",
    "quicker_port": 6655,
    "quicker_password": "",
    "quicker_actions": {},
    "hotwords": {},
    "track_edits": True,
}


def load_settings():
    settings = {**DEFAULTS, "hotwords": {}, "quicker_actions": {}}
    try:
        loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            for key, default in DEFAULTS.items():
                val = loaded.get(key)
                if isinstance(val, type(default)) and (not isinstance(default, int) or not isinstance(val, bool)):
                    settings[key] = val
                elif isinstance(default, float) and isinstance(val, (float, int)) and not isinstance(val, bool):
                    settings[key] = float(val)
    except (OSError, ValueError, TypeError):
        pass
    settings["hotwords"] = {str(k): str(v) for k, v in settings["hotwords"].items()}
    settings["quicker_actions"] = {str(k): str(v) for k, v in settings["quicker_actions"].items()}
    return settings


def save_settings(settings):
    APP_HOME.mkdir(parents=True, exist_ok=True)
    target = SETTINGS_PATH.with_suffix(".json.tmp")
    target.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(target, SETTINGS_PATH)


def parse_mappings(content, delimiter="="):
    mapping = {}
    for lineno, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if delimiter not in line:
            raise ValueError(f"第 {lineno} 行缺少 {delimiter}")
        key, value = (part.strip() for part in line.split(delimiter, 1))
        if not key or not value:
            raise ValueError(f"第 {lineno} 行的关键词或替换内容为空")
        mapping[key] = value
    return mapping


def validate_settings(value):
    from key_bindings import parse_shortcut
    parse_shortcut(value["shortcut"])
    if value["trailing_mode"] not in ("strip", "keep", "append"):
        raise ValueError("句末标点模式无效")
    if not 0.05 <= value["threshold"] <= 5:
        raise ValueError("录音最小时长必须在 0.05 到 5 秒之间")
    if not 1 <= value["audio_name_len"] <= 100:
        raise ValueError("音频文件名长度必须在 1 到 100 之间")
    if not 1 <= value["num_threads"] <= 16:
        raise ValueError("模型线程数必须在 1 到 16 之间")
    for prefix in ("mic", "file"):
        length, overlap = value[prefix + "_seg_duration"], value[prefix + "_seg_overlap"]
        if not 5 <= length <= 120 or not 0 <= overlap <= min(10, length - 1):
            raise ValueError(prefix + " 分段长度/重叠时长超出支持范围")
    for key in ("client_port", "server_port"):
        if not 1 <= value[key] <= 65535:
            raise ValueError(key + " 端口必须在 1–65535")
    if not value["record_dir"].strip():
        raise ValueError("保存文件夹不能为空")
    if not value["ending_symbol"] or len(value["ending_symbol"]) > 3:
        raise ValueError("句末符号应为 1–3 个字符")
    if value["quicker_host"] not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Quicker 仅允许连接本机")
    if not 1 <= value["quicker_port"] <= 65535:
        raise ValueError("Quicker 端口必须在 1–65535")
    return value
