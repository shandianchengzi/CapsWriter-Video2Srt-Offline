"""Store mutable settings outside the installation directory."""
import json
import os
from pathlib import Path

APP_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "CapsWriterLite"
SETTINGS_PATH = APP_HOME / "settings.json"
DEFAULTS = {
    "model_dir": "",
    "run_at_login": False,
    "quicker_enabled": False,
    "quicker_host": "127.0.0.1",
    "quicker_port": 6655,
    "quicker_password": "",
    "quicker_actions": {},
    "hotwords": {},
    "track_edits": True,
    "restore_clipboard": True,
}


def load_settings():
    settings = dict(DEFAULTS)
    settings["hotwords"] = {}
    settings["quicker_actions"] = {}
    try:
        loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            for key in DEFAULTS:
                if key in loaded and isinstance(loaded[key], type(DEFAULTS[key])):
                    settings[key] = loaded[key]
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
