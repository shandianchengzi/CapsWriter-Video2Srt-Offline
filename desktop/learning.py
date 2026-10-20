"""Best-effort correction detection, restricted to the last dictated insertion."""
from difflib import SequenceMatcher
import re
import time


def replace_hotwords(text, mappings):
    for original, corrected in sorted(mappings.items(), key=lambda x: len(x[0]), reverse=True):
        if original and corrected and original != corrected:
            text = re.sub(re.escape(original), lambda _: corrected, text, flags=re.I)
    return text


def propose_correction(initial, current, start, end):
    if initial == current or start >= end:
        return None
    changes = [(tag, a, b, c, d) for tag, a, b, c, d in
               SequenceMatcher(None, initial, current, autojunk=False).get_opcodes()
               if tag != "equal" and a < end and b > start]
    if len(changes) != 1:
        return None
    tag, a, b, c, d = changes[0]
    if tag != "replace" or a < start or b > end:
        return None
    old, new = initial[a:b].strip(), current[c:d].strip()
    if not (1 <= len(old) <= 40 and 1 <= len(new) <= 40):
        return None
    if old.casefold() == new.casefold() or old.isspace() or new.isspace():
        return None
    return old, new


def focused_control():
    try:
        import uiautomation as auto
        control = auto.GetFocusedControl()
        if control and not getattr(control, "IsPassword", False):
            return control
    except Exception:
        return None
    return None


def control_text(control):
    if control is None or getattr(control, "IsPassword", False):
        return None
    try:
        pattern = control.GetValuePattern()
        if pattern:
            result = pattern.Value
        else:
            pattern = control.GetTextPattern()
            if not pattern:
                return None
            result = pattern.DocumentRange.GetText(-1)
        return result if isinstance(result, str) and len(result) <= 12000 else None
    except Exception:
        return None


class EditObserver:
    def __init__(self, root, on_correction):
        self.root = root
        self.on_correction = on_correction
        self.generation = 0

    def begin(self, control, original, spoken):
        self.generation += 1
        if control is not None and original is not None and spoken:
            self.root.after(700, lambda: self._arm(self.generation, control, original, spoken))

    def _arm(self, generation, control, original, spoken):
        if generation != self.generation:
            return
        initial = control_text(control)
        if initial is None:
            return
        changes = [(c, d) for tag, a, b, c, d in
                   SequenceMatcher(None, original, initial, autojunk=False).get_opcodes()
                   if tag in ("insert", "replace")]
        locations = [initial.find(spoken, c, d) for c, d in changes]
        locations = [pos for pos in locations if pos >= 0]
        if not locations:
            return
        start = locations[-1]
        self._poll(generation, control, initial, start, start + len(spoken),
                   time.monotonic() + 30, None, 0)

    def _poll(self, generation, control, initial, start, end, deadline, candidate, stable_since):
        if generation != self.generation or time.monotonic() > deadline:
            return
        focused = focused_control()
        if focused is None:
            return
        try:
            if focused.GetRuntimeId() != control.GetRuntimeId():
                return
        except Exception:
            return
        current = control_text(control)
        if current is None:
            return
        possible = propose_correction(initial, current, start, end)
        now = time.monotonic()
        if possible and possible == candidate and now - stable_since >= 1.4:
            self.generation += 1
            self.on_correction(*possible)
            return
        if possible != candidate:
            stable_since = now
        self.root.after(550, lambda: self._poll(
            generation, control, initial, start, end, deadline, possible, stable_since))
