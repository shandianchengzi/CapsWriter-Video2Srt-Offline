"""Backwards-compatible dictionaries from hot-zh/en/rule/keywords.txt.

Install seeds from the repository; subsequent imports *merge*, never clear existing
local dictionaries. Imported user files remain editable in the Settings UI.
"""
from pathlib import Path
import os
import re
import shutil
import sys
import threading

from preferences import APP_HOME
from util import hot_sub_zh, hot_sub_en, hot_sub_rule

HOT_HOME = APP_HOME / "hotwords"
FILES = ("hot-zh.txt", "hot-en.txt", "hot-rule.txt", "keywords.txt")
ACTION_ID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def initial_defaults():
    packaged = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    candidates = (packaged / "legacy", packaged)
    for d in candidates:
        if (d / FILES[0]).is_file():
            return d
    return None


def initialize_files():
    HOT_HOME.mkdir(parents=True, exist_ok=True)
    defaults = initial_defaults()
    for name in FILES:
        dest = HOT_HOME / name
        if dest.exists():
            continue
        if defaults and (defaults / name).is_file():
            shutil.copy2(defaults / name, dest)
        else:
            dest.write_text("# 每行一项；可从旧版 CapsWriter 导入现有词库\n", encoding="utf-8")


def read_file(name):
    if name not in FILES:
        raise ValueError("不支持的词库文件")
    initialize_files()
    return (HOT_HOME / name).read_text(encoding="utf-8-sig")


def validate_contents(contents):
    """Validate rules and limits before changing files or applying settings."""
    if not set(contents).issubset(FILES):
        raise ValueError("未知词库文件")
    for name, content in contents.items():
        if len(content) > 1_000_000:
            raise ValueError("词库文件不能超过 1MB")
        if name == "hot-rule.txt":
            for line in content.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                if " = " not in line:
                    raise ValueError("规则必须用空格等号空格分隔：左侧 = 右侧")
                left, right = line.split(" = ", 1)
                if not left.strip() or not right.strip():
                    raise ValueError("规则搜索/替换内容不能为空")
                re.compile(left.strip())
def write_files(contents):
    initialize_files()
    validate_contents(contents)
    for name, content in contents.items():
        target = HOT_HOME / name
        tmp = target.with_suffix(".new")
        tmp.write_text(content.rstrip("\n") + "\n", encoding="utf-8")
        os.replace(tmp, target)


def _items(content):
    return [line.strip() for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def _merge_file(name, old, new):
    """Existing content wins on conflicting mappings; append only missing entries."""
    old_lines, new_lines = old.splitlines(), new.splitlines()
    if name == "hot-rule.txt":
        def key(line):
            return line.split(" = ", 1)[0].strip() if " = " in line else None
        known = {key(line) for line in old_lines if key(line) is not None}
        appended = [line for line in new_lines if key(line) is not None and key(line) not in known]
    else:
        known = set(_items(old))
        appended = [line for line in _items(new) if line not in known]
    if not appended:
        return old, 0
    return old.rstrip("\n") + "\n\n# 从旧版词库导入\n" + "\n".join(appended) + "\n", len(appended)


def import_legacy(directory):
    """Merge old installed files; do not erase user's local edits or settings."""
    initialize_files()
    folder = Path(directory).expanduser()
    if not folder.is_dir():
        raise ValueError("旧版配置目录不存在")
    found, counts = {}, {}
    for name in FILES:
        source = folder / name
        if not source.is_file():
            continue
        old = read_file(name)
        updated, count = _merge_file(name, old, source.read_text(encoding="utf-8-sig"))
        if count:
            found[name] = updated
        counts[name] = count
    if not counts:
        raise ValueError("所选目录找不到 hot-zh.txt、hot-en.txt、hot-rule.txt 或 keywords.txt")
    if found:
        write_files(found)
    return counts


class HotwordEngine:
    """Serialized legacy pinyin/regex dictionaries, with explicit toggles."""
    def __init__(self):
        self.lock = threading.RLock()
        initialize_files()
        self.refresh({"heteronym": True, "tone": False})

    def refresh(self, settings):
        with self.lock:
            hot_sub_zh.多音字 = settings.get("heteronym", True)
            hot_sub_zh.声调 = settings.get("tone", False)
            hot_sub_zh.风格 = 1 if hot_sub_zh.声调 else 0
            hot_sub_zh.更新热词词典(read_file("hot-zh.txt"))
            hot_sub_en.更新热词词典(read_file("hot-en.txt"))
            hot_sub_rule.更新热词词典(read_file("hot-rule.txt"))
            self.keywords = _items(read_file("keywords.txt"))
            self.actions = dict(hot_sub_rule.Quicker词典)

    def apply(self, text, settings, replacements):
        with self.lock:
            if settings["hot_zh"]:
                text = hot_sub_zh.热词替换(text)
            if settings["hot_en"]:
                text = hot_sub_en.热词替换(text)
            if settings["hot_rule"]:
                text = hot_sub_rule.热词替换(text)
            return replacements(text, settings["hotwords"])

    def quicker_actions(self, settings):
        with self.lock:
            result = dict(self.actions) if settings["hot_rule"] else {}
        result.update(settings["quicker_actions"])
        return result

    def keywords_for(self, settings):
        with self.lock:
            return tuple(self.keywords) if settings["hot_kwd"] else ()
