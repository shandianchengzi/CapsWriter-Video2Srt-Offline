"""Opt-in local voice/text journal. Never persist audio/text when disabled."""
import re
import wave
from datetime import datetime
from pathlib import Path
import numpy as np


def _safe_title(text, length):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return (text[:length].strip(" .") or "audio")


def save_record(settings, audio, text, started_at, keywords=()):
    if not settings["save_audio"] and not settings["save_text"]:
        return ()
    dest = Path(settings["record_dir"]).expanduser()
    time = datetime.fromtimestamp(started_at)
    folder = dest / time.strftime("%Y") / time.strftime("%m")
    folder.mkdir(parents=True, exist_ok=True)
    stem = time.strftime("%Y%m%d-%H%M%S-%f")
    audio_file = None
    if settings["save_audio"] and audio is not None and len(audio):
        audio_dir = folder / "assets"
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_file = audio_dir / (stem + "-" + _safe_title(text, settings["audio_name_len"]) + ".wav")
        samples = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype("<i2")
        with wave.open(str(audio_file), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes(samples.tobytes())
    files = []
    if settings["save_text"]:
        targets = [folder / (time.strftime("%d") + ".md")]
        if settings["hot_kwd"]:
            targets += [folder / (kwd + "-" + time.strftime("%d") + ".md")
                        for kwd in keywords if text.startswith(kwd)]
        for target in targets:
            entry = text
            if target != targets[0]:
                entry = text[len(target.stem.rsplit("-", 1)[0]):].lstrip("，。,.: ")
            prefix = f"[{time.strftime('%H:%M:%S')}]"
            if audio_file:
                relative = audio_file.relative_to(folder).as_posix().replace(" ", "%20")
                prefix = f"{prefix}({relative})"
            with target.open("a", encoding="utf-8") as output:
                output.write(f"{prefix} {entry}\n\n")
            files.append(target)
    return tuple(([audio_file] if audio_file else []) + files)
