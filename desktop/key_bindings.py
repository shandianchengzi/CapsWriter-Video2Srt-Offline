"""Runtime rebindable key hook. The model never restarts when rebinding."""
import keyboard

MODIFIERS = {"ctrl", "alt", "shift", "windows"}
ALIASES = {"control": "ctrl", "win": "windows", "cmd": "windows",
           "capslock": "caps lock", "right control": "right ctrl",
           "left control": "left ctrl"}
UNSAFE = {"esc", "escape", "enter", "tab", "backspace", "delete", "windows",
          "left windows", "right windows", "ctrl", "shift", "alt"}


def parse_shortcut(value):
    parts = [ALIASES.get(x.strip().lower(), x.strip().lower()) for x in value.split("+")]
    if not 1 <= len(parts) <= 4 or any(not part for part in parts):
        raise ValueError("快捷键格式错误，例如 caps lock、f8 或 ctrl+alt+v")
    try:
        parts = [keyboard.normalize_name(part) for part in parts]
    except (ValueError, KeyError) as exc:
        raise ValueError("无法识别的键位：" + value) from exc
    if len(set(parts)) != len(parts):
        raise ValueError("快捷键不能包含重复按键")
    main = parts[-1]
    modifiers = parts[:-1]
    if main in UNSAFE or any(mod not in MODIFIERS for mod in modifiers):
        raise ValueError("请使用普通按键或 F 功能键作为主键，修饰键只能是 Ctrl/Alt/Shift/Windows")
    if len(parts) == 1 and len(main) == 1 and main.isalnum():
        raise ValueError("单个字母/数字会干扰正常输入，请搭配 Ctrl/Alt/Shift")
    if main in ("caps lock", "num lock", "scroll lock") and modifiers:
        raise ValueError("锁定键不能与组合键一起使用")
    return tuple(modifiers), main


class KeyBinding:
    def __init__(self, callback):
        self.callback = callback
        self.hook = None
        self.modifiers = ()
        self.main = None
        self.suppress = False

    def bind(self, value, suppress):
        modifiers, main = parse_shortcut(value)
        # Suppressing only the terminal key blocks normal use without the modifiers;
        # disallow for chord shortcuts.
        if modifiers and suppress:
            raise ValueError("组合键不能启用“屏蔽原按键”，请关闭该选项")
        # Register new hook before removing the old hook; failed updates preserve binding.
        handle = keyboard.hook_key(main, self._on_key, suppress=suppress)
        old = self.hook
        self.hook = handle
        self.modifiers = modifiers
        self.main = main
        self.suppress = suppress
        if old is not None:
            keyboard.unhook(old)

    def _on_key(self, event):
        if self.modifiers:
            if event.event_type == "down" and not all(keyboard.is_pressed(mod) for mod in self.modifiers):
                return
        self.callback(event)

    def close(self):
        if self.hook is not None:
            keyboard.unhook(self.hook)
            self.hook = None
