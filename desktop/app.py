"""Windows tray voice input using the ORIGINAL full-size CapsWriter models."""
from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import keyboard
import numpy as np
import pyperclip
import pystray
from PIL import Image, ImageDraw
import sounddevice as sd

from learning import EditObserver, control_text, focused_control, replace_hotwords
from legacy_hotwords import HotwordEngine, FILES as HOT_FILES, read_file, write_files, import_legacy
from key_bindings import KeyBinding, parse_shortcut
from record_store import save_record
from model_store import APP_HOME, MODEL_ROOT, install_original, is_ready, locate_model
from preferences import load_settings, parse_mappings, save_settings, validate_settings
from quicker_bridge import ACTION_ID, connect_quicker
from recognizer import FullRecognizer

APP_TITLE = "CapsWriter Lite"
APP_VERSION = "0.3.2"


def register_single_instance():
    """Block duplicate model loads (the Windows mutex is released on exit)."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, "Local\\CapsWriterLite-VoiceInput")
    if not handle:
        raise OSError("无法创建单实例锁")
    if ctypes.get_last_error() == 183:
        return None
    return handle


def set_run_at_login(enabled):
    import winreg
    keyname = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, keyname, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            # In source mode run the .pyw script through pythonw, not python.exe.
            executable = Path(os.environ.get("CAPSWRITER_LAUNCHER_EXE") or sys.executable)
            if getattr(sys, "frozen", False):
                command = f'"{executable}" --background'
            else:
                pythonw = executable.with_name("pythonw.exe")
                if pythonw.is_file():
                    executable = pythonw
                command = f'"{executable}" "{Path(__file__).resolve()}" --background'
            winreg.SetValueEx(key, "CapsWriterLite", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "CapsWriterLite")
            except FileNotFoundError:
                pass


def make_icon():
    img = Image.new("RGBA", (64, 64), "#19344d")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((25, 9, 39, 38), radius=7, fill="white")
    draw.arc((16, 18, 48, 49), 0, 180, fill="#7de2c1", width=4)
    draw.line((32, 47, 32, 55), fill="white", width=4)
    draw.line((23, 56, 41, 56), fill="white", width=4)
    return img


class VoiceApp:
    def __init__(self):
        APP_HOME.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=APP_HOME / "capswriter.log", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s")
        self.settings = load_settings()
        self.hotwords = HotwordEngine()
        self.hotwords.refresh(self.settings)
        self.model = None
        self.loading = False
        self.downloading = False
        self.enabled = True
        self.running = True
        self.status = "正在启动…"
        self.events = queue.Queue()
        self.jobs = queue.Queue()
        self.model_lock = threading.Lock()
        self.sound_lock = threading.Lock()
        self.key_pressed = False
        self.recording = False
        self.record_started = 0.0
        self.record_epoch = 0.0
        self.audio_chunks = []
        self._synthetic = False
        self.focus_hwnd = 0
        self.audio_stream = None
        self.last_raw = ""
        self.last_text = ""

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_settings)
        self.window = None
        self.observer = EditObserver(self.root, self._offer_correction)
        self.tray = pystray.Icon("CapsWriterLite", make_icon(), APP_TITLE, pystray.Menu(
            pystray.MenuItem("打开设置", lambda icon, item: self.events.put(("settings", None)),
                             default=True),
            pystray.MenuItem("暂停 / 恢复语音输入",
                             lambda icon, item: self.events.put(("toggle", None))),
            pystray.MenuItem("纠正上次识别",
                             lambda icon, item: self.events.put(("manual", None))),
            pystray.MenuItem("检查软件更新",
                             lambda icon, item: self.events.put(("update", None))),
            pystray.MenuItem("打开日志",
                             lambda icon, item: self.events.put(("logs", None))),
            pystray.MenuItem("完全退出",
                             lambda icon, item: self.events.put(("quit", None))),
        ))
        threading.Thread(target=self.tray.run, daemon=True, name="tray").start()
        self.root.after(120, self._pump)
        self.key_binding = KeyBinding(self._on_key)
        try:
            self.key_binding.bind(self.settings["shortcut"], self.settings["suppress"])
        except Exception as exc:
            self._emit("error", f"无法注册语音快捷键：{exc}")
        existing = locate_model(self.settings.get("model_dir", ""))
        if existing:
            self.settings["model_dir"] = str(existing)
            save_settings(self.settings)
            self._begin_load(existing)
        else:
            self._emit("status", "尚未安装模型，请在设置中选择或下载原版大模型")
            self.root.after(450, self.show_settings)

    def _emit(self, kind, payload):
        self.events.put((kind, payload))

    def _set_status(self, msg):
        self._emit("status", msg)
        logging.info(msg)

    def _begin_load(self, directory):
        if self.loading or self.model is not None:
            return
        if not is_ready(directory):
            self._emit("error", "缺少 Paraformer 或 CT-Transformer 文件，模型目录不完整")
            return
        self.loading = True
        threading.Thread(target=self._load_worker, args=(directory,),
                         daemon=True, name="model-load").start()

    def _load_worker(self, directory):
        try:
            model = FullRecognizer(directory, self._set_status,
                                   num_threads=self.settings["num_threads"])
            with self.model_lock:
                self.model = model
                self.loaded_threads = self.settings["num_threads"]
            self._emit("ready", None)
        except Exception as exc:
            logging.exception("Failed to initialize models")
            self._emit("error", f"加载原版模型失败：{exc}")
        finally:
            self.loading = False

    def _open_microphone(self):
        if self.audio_stream:
            return
        try:
            self.audio_stream = sd.InputStream(
                samplerate=16000, channels=1, dtype="float32",
                blocksize=800, callback=self._audio_callback)
            self.audio_stream.start()
            self._set_status(f"已就绪：使用 {self.settings['shortcut']} 录音")
        except Exception as exc:
            self.audio_stream = None
            self._emit("error", f"麦克风初始化失败：{exc}")

    def _audio_callback(self, indata, frames, timing, status):
        with self.sound_lock:
            if self.recording:
                self.audio_chunks.append(indata[:, 0].copy())

    def _start_record(self):
        if not self.enabled or not self.model or not self.audio_stream:
            return
        with self.sound_lock:
            self.audio_chunks = []
            self.recording = True
            self.record_started = time.monotonic()
            self.record_epoch = time.time()
        self.focus_hwnd = ctypes.windll.user32.GetForegroundWindow()
        self._emit("status", f"正在录音…再次按键或松开 {self.settings['shortcut']} 结束")

    def _finish_record(self):
        with self.sound_lock:
            self.recording = False
            chunks = self.audio_chunks
            self.audio_chunks = []
            duration = time.monotonic() - self.record_started if self.record_started else 0
            self.record_started = 0
        if chunks and duration >= self.settings["threshold"]:
            self.jobs.put((np.concatenate(chunks), self.focus_hwnd, self.record_epoch))
            self._emit("status", "正在识别…")
            self._start_inference_if_needed()
        elif chunks:
            self._emit("status", "录音短于设定的最小时长，已取消")

    def _restore_key_if_needed(self):
        if self.settings["restore_key"] and not self.settings["suppress"]:
            if self.key_binding.main in ("caps lock", "num lock", "scroll lock"):
                self._synthetic = True
                try:
                    keyboard.send(self.key_binding.main)
                finally:
                    self._synthetic = False

    def _on_key(self, event):
        # Called from the keyboard hook thread, not the Tk UI thread.
        if not self.running or self._synthetic:
            return
        if event.event_type == "down":
            if self.key_pressed:
                return  # Ignore auto-repeat.
            self.key_pressed = True
            if self.settings["hold_mode"]:
                self._start_record()
            else:
                if self.recording:
                    self._finish_record()
                else:
                    self._start_record()
        elif event.event_type == "up":
            if not self.key_pressed:
                return
            self.key_pressed = False
            if self.settings["hold_mode"] and self.recording:
                self._finish_record()
            self._restore_key_if_needed()

    def _start_inference_if_needed(self):
        # One long-lived worker for all utterances; one long-lived model instance.
        if getattr(self, "_inference_running", False):
            return
        self._inference_running = True
        threading.Thread(target=self._inference_loop, daemon=True,
                         name="asr-inference").start()

    def _inference_loop(self):
        while self.running:
            try:
                audio, hwnd, recorded_at = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                text = self.model.transcribe(audio, dict(self.settings))
                self._emit("recognized", (text, hwnd, audio, recorded_at))
            except Exception as exc:
                logging.exception("Failed to recognize audio")
                self._emit("error", f"语音识别失败：{exc}")

    def _recognized(self, raw, hwnd, audio, recorded_at):
        self.last_raw = raw
        if not raw:
            self._set_status("未识别到有效语音")
            return
        processed = self.hotwords.apply(raw, self.settings, replace_hotwords)
        mode = self.settings["trailing_mode"]
        if mode == "strip":
            processed = processed.rstrip(self.settings["trash_punc"]).rstrip()
        elif mode == "append" and processed:
            if processed[-1] not in "。！？.!?":
                processed = processed.rstrip("，,") + self.settings["ending_symbol"]
        self.last_text = processed
        if not processed:
            return
        if self.settings["save_audio"] or self.settings["save_text"]:
            snapshot = dict(self.settings)
            keywords = self.hotwords.keywords_for(snapshot)
            def persist():
                try:
                    save_record(snapshot, audio, processed, recorded_at, keywords)
                except Exception:
                    logging.exception("Failed writing local dictation records")
                    self._emit("status", "本地录音/文字保存失败，详情请查看日志")
            threading.Thread(target=persist, daemon=True, name="record-save").start()

        command = processed.strip(" ,.!?。，！？")
        action = self.hotwords.quicker_actions(self.settings).get(command)
        if self.settings["quicker_enabled"] and action:
            threading.Thread(target=self._run_quicker, args=(action,),
                             daemon=True, name="quicker").start()
            return

        # Never paste into an unrelated app after focus changes.
        if hwnd != ctypes.windll.user32.GetForegroundWindow():
            pyperclip.copy(processed)
            self._set_status("窗口焦点发生变化，结果已复制到剪贴板")
            return
        control = focused_control() if self.settings["track_edits"] else None
        previous = control_text(control) if control else None
        try:
            if self.settings["paste"]:
                try:
                    old_clipboard = pyperclip.paste() if self.settings["restore_clipboard"] else None
                except Exception:
                    old_clipboard = None
                pyperclip.copy(processed)
                keyboard.send("ctrl+v")
                if old_clipboard is not None:
                    def restore():
                        try:
                            if pyperclip.paste() == processed:
                                pyperclip.copy(old_clipboard)
                        except Exception:
                            logging.exception("Could not restore text clipboard")
                    self.root.after(650, restore)
            else:
                keyboard.write(processed)
        except Exception as exc:
            self._emit("error", f"输入文本失败：{exc}")
            return
        if control and previous is not None:
            self.observer.begin(control, previous, processed)
        # Do not leak recognized private text to default logs when persistence is off.
        self._set_status("识别完成，文字已输入")

    def _run_quicker(self, action):
        try:
            connect_quicker(dict(self.settings), action)
            self._set_status("Quicker 动作已发送")
        except Exception as exc:
            self._emit("error", f"Quicker 执行失败：{exc}")

    def _offer_correction(self, old, new):
        if messagebox.askyesno("记住语音纠错？",
                               f"检测到你修改了上次语音输入：\n\n{old}  →  {new}"
                               "\n\n将这个替换关系保存为热词吗？",
                               parent=self.window or self.root):
            self.settings["hotwords"][old] = new
            save_settings(self.settings)
            if hasattr(self, "hotword_text") and self.hotword_text.winfo_exists():
                self.hotword_text.delete("1.0", "end")
                self.hotword_text.insert("1.0", "\\n".join(f"{k} = {v}" for k,v in self.settings["hotwords"].items()))
            self._set_status(f"已记住热词：{old} → {new}")

    def _check_updates(self):
        launcher = os.environ.get("CAPSWRITER_LAUNCHER_EXE", "")
        if not launcher or not Path(launcher).is_file():
            messagebox.showinfo(APP_TITLE, "当前为便携或源码运行模式。请先安装 Windows Setup.exe 版本，才能使用差分自动更新。", parent=self.window or self.root)
            return
        try:
            subprocess.Popen([launcher, "--check-for-updates"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             close_fds=True)
            self._set_status("正在检查更新…")
        except OSError as exc:
            self._emit("error", f"启动更新检查失败：{exc}")

    def _manual_correction(self):
        if not self.last_raw:
            messagebox.showinfo(APP_TITLE, "还没有语音识别结果", parent=self.window or self.root)
            return
        old = simpledialog.askstring("纠正上次识别", "识别错误的词：", initialvalue=self.last_raw,
                                     parent=self.window or self.root)
        if old is None or not old.strip():
            return
        new = simpledialog.askstring("纠正上次识别", "应当识别为：",
                                     parent=self.window or self.root)
        if new and new.strip() and old != new:
            self._offer_correction(old.strip(), new.strip())

    def _download_model(self):
        if self.downloading or self.loading or self.model is not None:
            return
        if not messagebox.askyesno("下载原版模型",
                                   "将从本仓库 Release 下载原版约 1.18 GB 的模型包。"
                                   "\n下载完成后解压并保存在本机，只需要下载一次。"
                                   "\n\n确定开始吗？", parent=self.window or self.root):
            return
        self.downloading = True

        def worker():
            try:
                directory = install_original(self._set_status)
                self._emit("installed", str(directory))
            except Exception as exc:
                logging.exception("Failed to install original models")
                self._emit("error", f"模型安装失败：{exc}")
            finally:
                self.downloading = False

        threading.Thread(target=worker, daemon=True, name="model-download").start()

    def _choose_model(self):
        directory = filedialog.askdirectory(parent=self.window, title="选择已有 models 文件夹")
        if not directory:
            return
        if not is_ready(directory):
            messagebox.showerror(APP_TITLE, "所选目录未包含完整的原版语音与标点模型",
                                 parent=self.window)
            return
        from model_store import normalize_model_path
        directory = str(normalize_model_path(directory))
        self.settings["model_dir"] = directory
        save_settings(self.settings)
        # A legacy installation stores hot-*.txt beside its models/ folder.
        legacy = Path(directory).parent
        if any((legacy / name).is_file() for name in HOT_FILES):
            try:
                counts = import_legacy(legacy)
                self.hotwords.refresh(self.settings)
                if hasattr(self, "dict_texts"):
                    for name, widget in self.dict_texts.items():
                        widget.delete("1.0", "end")
                        widget.insert("1.0", read_file(name))
                self._set_status("已自动合并旧版热词：" + str(counts))
            except Exception:
                logging.exception("Automatic legacy hotword import failed")
        self._begin_load(directory)

    def _import_legacy_ui(self):
        directory = filedialog.askdirectory(parent=self.window, title="选择旧版 CapsWriter 程序根目录（有 hot-zh.txt 等）")
        if not directory:
            return
        if not messagebox.askyesno("导入旧热词", "从所选目录合并旧词库，不覆盖已存在的规则。\\n\\n编辑框中未保存的改动将被刷新，是否继续？", parent=self.window):
            return
        try:
            counts = import_legacy(directory)
            self.settings["legacy_dir"] = str(directory)
            save_settings(self.settings)
            self.hotwords.refresh(self.settings)
            for name, widget in self.dict_texts.items():
                widget.delete("1.0", "end")
                widget.insert("1.0", read_file(name))
            self._set_status("旧版热词已合并：" + "、".join(f"{name} +{n}" for name, n in counts.items()))
            messagebox.showinfo(APP_TITLE, "旧词库合并成功；原文件未被修改。", parent=self.window)
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror(APP_TITLE, f"导入失败：{exc}", parent=self.window)

    def _pick_records_folder(self):
        folder = filedialog.askdirectory(parent=self.window, title="选择文字和录音的保存目录")
        if folder:
            self.record_dir_var.set(folder)

    def _apply_settings(self):
        try:
            new = dict(self.settings)
            new["hotwords"] = parse_mappings(self.hotword_text.get("1.0", "end"))
            new["quicker_actions"] = parse_mappings(self.quicker_text.get("1.0", "end"))
            if any(not ACTION_ID.fullmatch(action) for action in new["quicker_actions"].values()):
                raise ValueError("Quicker 动作 ID 需要是 UUID 格式")
            new["quicker_enabled"] = self.quicker_enabled_var.get()
            new["track_edits"] = self.edit_enabled_var.get()
            new["restore_clipboard"] = self.clipboard_enabled_var.get()
            new["quicker_host"] = self.quicker_host_var.get().strip()
            new["quicker_port"] = int(self.quicker_port_var.get())
            new["quicker_password"] = self.quicker_password_var.get()
            new["run_at_login"] = self.autostart_var.get()

            for key in ("shortcut", "record_dir", "trailing_mode", "trash_punc",
                        "ending_symbol", "client_addr", "server_addr"):
                new[key] = self.option_vars[key].get().strip()
            for key in ("hold_mode", "suppress", "restore_key", "paste", "save_audio",
                        "save_text", "format_num", "format_punc", "format_spell",
                        "hot_zh", "hot_en", "hot_rule", "hot_kwd", "heteronym", "tone"):
                new[key] = self.option_vars[key].get()
            for key in ("audio_name_len", "mic_seg_duration", "mic_seg_overlap",
                        "file_seg_duration", "file_seg_overlap", "num_threads",
                        "client_port", "server_port"):
                new[key] = int(self.option_vars[key].get())
            new["threshold"] = float(self.option_vars["threshold"].get())
            validate_settings(new)
            if any("\\n" in new[key] or "\\r" in new[key] for key in ("record_dir", "shortcut")):
                raise ValueError("路径和快捷键不可包含换行符")
            for name, widget in self.dict_texts.items():
                if len(widget.get("1.0", "end")) > 1_000_000:
                    raise ValueError(f"{name} 文件太大")
            edited = {name: widget.get("1.0", "end") for name, widget in self.dict_texts.items()}
            # Hotword regex are validated before any file writes.
            from legacy_hotwords import validate_contents
            validate_contents(edited)
            previous_shortcut = (self.settings["shortcut"], self.settings["suppress"])
            if (new["shortcut"], new["suppress"]) != previous_shortcut:
                self.key_binding.bind(new["shortcut"], new["suppress"])
                self.key_pressed = False
                if self.recording:
                    self._finish_record()
            write_files(edited)
            self.settings = new
            self.hotwords.refresh(new)
            save_settings(new)
            set_run_at_login(new["run_at_login"])
            needs_restart = new["num_threads"] != getattr(self, "loaded_threads", self.settings["num_threads"])
            info = "所有输入设置与热词已保存并生效。"
            if needs_restart:
                info += "\\n模型推理线程数将在下一次启动软件后生效，其余设置立即生效。"
            self._set_status("设置和词库已保存")
            messagebox.showinfo(APP_TITLE, info, parent=self.window)
        except (ValueError, OSError, re.error, RuntimeError) as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self.window)

    def _test_quicker(self):
        # Test only establishes the handshake; it cannot trigger arbitrary actions.
        settings = dict(self.settings)
        settings.update({
            "quicker_host": self.quicker_host_var.get().strip(),
            "quicker_port": int(self.quicker_port_var.get().strip() or 0),
            "quicker_password": self.quicker_password_var.get(),
        })
        def run():
            try:
                connect_quicker(settings)
                self._emit("info", "Quicker WebSocket 连接／认证成功")
            except Exception as exc:
                self._emit("error", f"Quicker 连接测试失败：{exc}")
        threading.Thread(target=run, daemon=True).start()

    def show_settings(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return
        win = tk.Toplevel(self.root)
        self.window = win
        win.title(f"{APP_TITLE} {APP_VERSION} - 设置")
        win.geometry("785x690")
        win.minsize(675, 570)
        win.protocol("WM_DELETE_WINDOW", self.hide_settings)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)
        book = ttk.Notebook(win)
        book.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.option_vars = {}
        for key, value in self.settings.items():
            if isinstance(value, bool):
                self.option_vars[key] = tk.BooleanVar(value=value)
            elif isinstance(value, (str, float, int)):
                self.option_vars[key] = tk.StringVar(value=str(value))

        def page(title):
            container = ttk.Frame(book, padding=12)
            container.columnconfigure(0, weight=1)
            book.add(container, text=title)
            return container

        def flag(parent, title, key):
            ttk.Checkbutton(parent, text=title,
                            variable=self.option_vars[key]).pack(anchor="w", pady=4)

        def edit(parent, title, key, width=16):
            line = ttk.Frame(parent)
            line.pack(fill="x", pady=4)
            ttk.Label(line, text=title, width=23).pack(side="left")
            field = ttk.Entry(line, textvariable=self.option_vars[key], width=width)
            field.pack(side="left", fill="x", expand=True)
            return field

        basic = page("模型与启动")
        ttk.Label(basic, text="保持原版 Paraformer + CT-Transformer 大模型；后台加载后常驻内存，"
                  "关闭设置不会卸载模型。", wraplength=690).pack(anchor="w", pady=(3, 13))
        self.status_var = tk.StringVar(value=self.status)
        ttk.Label(basic, textvariable=self.status_var, wraplength=690).pack(anchor="w", pady=(0, 14))
        ttk.Label(basic, text="现有大模型目录：").pack(anchor="w")
        self.model_path_var = tk.StringVar(value=self.settings.get("model_dir") or "未选择")
        ttk.Entry(basic, textvariable=self.model_path_var, state="readonly").pack(fill="x", pady=6)
        tool_row = ttk.Frame(basic)
        tool_row.pack(fill="x", pady=(3, 12))
        ttk.Button(tool_row, text="选择现有模型", command=self._choose_model).pack(side="left", padx=(0, 9))
        ttk.Button(tool_row, text="下载原版大模型", command=self._download_model).pack(side="left")
        self.autostart_var = tk.BooleanVar(value=self.settings["run_at_login"])
        ttk.Checkbutton(basic, text="Windows 登录时自动启动并在后台预加载模型",
                        variable=self.autostart_var).pack(anchor="w", pady=6)
        self.edit_enabled_var = tk.BooleanVar(value=self.settings["track_edits"])
        ttk.Checkbutton(basic, text="修改上次语音输入时询问是否记为热词（需要编辑器支持）",
                        variable=self.edit_enabled_var).pack(anchor="w", pady=6)
        ttk.Button(basic, text="检查更新（安装版支持差分）",
                   command=self._check_updates).pack(anchor="w", pady=(15, 5))
        ttk.Label(basic, text="完全退出请从右下角托盘菜单选择“完全退出”；软件更新不会删除模型与词库。",
                  foreground="#555", wraplength=690).pack(anchor="w", pady=(12, 3))

        keys = page("快捷键与输入")
        ttk.Label(keys, text="单键可选 CapsLock、F8、右 Alt 等；组合键如 Ctrl+Alt+V。"
                  "组合键不能同时启用“屏蔽原按键”，避免误屏蔽常用输入。", wraplength=690).pack(anchor="w", pady=(2, 14))
        line = ttk.Frame(keys)
        line.pack(fill="x", pady=8)
        ttk.Label(line, text="语音录入快捷键", width=23).pack(side="left")
        ttk.Combobox(line, textvariable=self.option_vars["shortcut"],
                     values=("caps lock", "f8", "f9", "f10", "right alt", "right ctrl", "ctrl+alt+v",
                             "ctrl+shift+space", "alt+shift+z"),
                     width=30).pack(side="left")
        flag(keys, "长按录音、松开识别（关闭后：单击开始、再次单击结束）", "hold_mode")
        flag(keys, "屏蔽原快捷键事件（仅适用于单键）", "suppress")
        flag(keys, "松键时再发送一次锁定键以恢复状态（仅当不屏蔽时）", "restore_key")
        edit(keys, "最短录音时长（秒）", "threshold")
        flag(keys, "粘贴输入（Ctrl+V，通常兼容性更好）", "paste")
        flag(keys, "粘贴后恢复原纯文本剪贴板", "restore_clipboard")
        ttk.Label(keys, text="关闭“粘贴输入”后改为模拟逐字键盘输入。"
                  "对于系统安全桌面、某些管理员窗口或输入法组合键，可能无法正常触发。",
                  foreground="#555", wraplength=690).pack(anchor="w", pady=(13, 4))

        record = page("保存与日记")
        ttk.Label(record, text="隐私默认：既不保存录音，也不保存识别文本。"
                  "你可以分别开启两项；音频以 WAV 保存、文字按年/月/日写入 Markdown 日记。",
                  wraplength=690).pack(anchor="w", pady=(2, 14))
        flag(record, "在本地保存每次语音录音（WAV）", "save_audio")
        flag(record, "在本地保存识别后的文字（Markdown）", "save_text")
        flag(record, "将匹配日记关键词的文字另存到专题日记", "hot_kwd")
        edit(record, "音频文件名文字截取长度", "audio_name_len")
        line = ttk.Frame(record)
        line.pack(fill="x", pady=12)
        ttk.Label(line, text="本地记录保存文件夹", width=23).pack(side="left")
        self.record_dir_var = self.option_vars["record_dir"]
        ttk.Entry(line, textvariable=self.record_dir_var).pack(side="left", fill="x", expand=True)
        ttk.Button(line, text="浏览…", command=self._pick_records_folder).pack(side="left", padx=(9, 0))
        ttk.Button(record, text="打开保存目录", command=lambda: os.startfile(str(
            Path(self.record_dir_var.get()).expanduser()))).pack(anchor="w", pady=7)
        ttk.Label(record, text="仅保存明确勾选的类型。即使取消两种保存，模型和词库仍存在本地。"
                  "\\n本地日志不再写入具体识别句子；Quicker 凭证仅存入用户配置文件。",
                  foreground="#555", wraplength=690).pack(anchor="w", pady=12)

        formatting = page("文本格式")
        flag(formatting, "将识别出的中文数字转为阿拉伯数字", "format_num")
        flag(formatting, "启用原版 CT-Transformer 自动标点（模型仍会预加载）", "format_punc")
        flag(formatting, "自动整理中文与英文之间的空格", "format_spell")
        line = ttk.Frame(formatting)
        line.pack(fill="x", pady=(18, 5))
        ttk.Label(line, text="句末标点处理", width=23).pack(side="left")
        ttk.Combobox(line, state="readonly",
                     values=("strip", "keep", "append"),
                     textvariable=self.option_vars["trailing_mode"], width=20).pack(side="left")
        ttk.Label(formatting, text="strip = 移除指定尾部标点；keep = 保留原识别结果；"
                  "append = 缺少句末标点时自动补全。", wraplength=690).pack(anchor="w", pady=5)
        edit(formatting, "strip 移除的标点字符", "trash_punc")
        edit(formatting, "append 自动添加的标点", "ending_symbol")
        ttk.Label(formatting, text="原版默认会移除末尾的“，。,.”；若你希望完整保留自动标点，可选择 keep。",
                  foreground="#555").pack(anchor="w", pady=(15, 0))

        hotwords = page("热词词库")
        ttk.Label(hotwords, text="首次安装直接加载仓库已有热词：中文按拼音匹配，英文按拼写匹配，"
                  "正则规则替换，日记关键词另建分类。手动纠错另存为自定义替换。",
                  wraplength=690).pack(anchor="w", pady=(2, 5))
        opts = ttk.Frame(hotwords)
        opts.pack(fill="x", pady=6)
        for key, title in (("hot_zh", "中文"), ("hot_en", "英文"),
                           ("hot_rule", "规则"), ("heteronym", "多音字"), ("tone", "声调")):
            ttk.Checkbutton(opts, text=title, variable=self.option_vars[key]).pack(
                side="left", padx=(0, 12))
        top = ttk.Frame(hotwords)
        top.pack(fill="x", pady=(2, 9))
        ttk.Button(top, text="从旧版目录导入并合并热词", command=self._import_legacy_ui).pack(side="left")
        ttk.Label(top, text="已有词库只合并不覆盖", foreground="#555").pack(side="left", padx=12)
        pane = ttk.Notebook(hotwords)
        pane.pack(fill="both", expand=True)
        self.dict_texts = {}
        for name, title in (("hot-zh.txt", "中文热词"), ("hot-en.txt", "英文热词"),
                             ("hot-rule.txt", "正则/Quicker 规则"), ("keywords.txt", "日记关键词")):
            tab = ttk.Frame(pane)
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(0, weight=1)
            pane.add(tab, text=title)
            area = tk.Text(tab, undo=True, wrap="none", height=14)
            scrollbar = ttk.Scrollbar(tab, orient="vertical", command=area.yview)
            area.configure(yscrollcommand=scrollbar.set)
            area.grid(row=0, column=0, sticky="nsew")
            scrollbar.grid(row=0, column=1, sticky="ns")
            area.insert("1.0", read_file(name))
            self.dict_texts[name] = area
        tab = ttk.Frame(pane)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        pane.add(tab, text="纠错记忆")
        ttk.Label(tab, text="自动确认的纠错会存入此处；手动格式：错误词 = 正确词").grid(
            row=0, column=0, sticky="w", pady=5)
        self.hotword_text = tk.Text(tab, undo=True, wrap="none", height=14)
        self.hotword_text.grid(row=1, column=0, sticky="nsew")
        self.hotword_text.insert("1.0", "\\n".join(
            f"{k} = {v}" for k, v in self.settings["hotwords"].items()))
        ttk.Label(hotwords, text="编辑完成后点击窗口右下角“保存设置”，无需重新加载大模型。",
                  foreground="#555").pack(anchor="w", pady=5)

        quicker = page("Quicker")
        quicker.columnconfigure(1, weight=1)
        quicker.rowconfigure(6, weight=1)
        self.quicker_enabled_var = tk.BooleanVar(value=self.settings["quicker_enabled"])
        ttk.Checkbutton(quicker, text="启用 Quicker 语音命令",
                        variable=self.quicker_enabled_var).grid(row=0, column=0,
                                                               columnspan=2, sticky="w", pady=10)
        self.quicker_host_var = tk.StringVar(value=self.settings["quicker_host"])
        self.quicker_port_var = tk.StringVar(value=str(self.settings["quicker_port"]))
        self.quicker_password_var = tk.StringVar(value=self.settings["quicker_password"])
        for row, title, var, password in (
            (1, "本机地址", self.quicker_host_var, ""),
            (2, "WebSocket 端口", self.quicker_port_var, ""),
            (3, "WebSocket 密码", self.quicker_password_var, "*"),
        ):
            ttk.Label(quicker, text=title).grid(row=row, column=0, sticky="w", pady=6)
            ttk.Entry(quicker, textvariable=var, show=password).grid(
                row=row, column=1, sticky="ew", pady=6, padx=(12, 0))
        ttk.Button(quicker, text="测试 Quicker 连接", command=self._test_quicker).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(12, 5))
        ttk.Label(quicker, text="语音命令 = 动作 UUID（精确匹配；旧 hot-rule.txt 中的 Quicker 命令也会生效）").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(12, 3))
        self.quicker_text = tk.Text(quicker, undo=True, wrap="none", height=12)
        self.quicker_text.grid(row=6, column=0, columnspan=2, sticky="nsew")
        self.quicker_text.insert("1.0", "\\n".join(
            f"{k} = {v}" for k, v in self.settings["quicker_actions"].items()))

        advanced = page("高级/旧版")
        ttk.Label(advanced, text="这些参数延续旧版 config.py。听写分段对当前程序即时生效；"
                  "旧版客户端/服务端的网络地址与文件字幕转录参数由兼容配置读取，"
                  "旧程序需重新启动才能更新。", wraplength=690).pack(anchor="w", pady=(3, 12))
        edit(advanced, "模型计算线程数（重启生效）", "num_threads")
        edit(advanced, "麦克风分段长度（秒）", "mic_seg_duration")
        edit(advanced, "麦克风分段重叠（秒）", "mic_seg_overlap")
        edit(advanced, "字幕文件分段长度（秒）", "file_seg_duration")
        edit(advanced, "字幕文件重叠（秒）", "file_seg_overlap")
        edit(advanced, "旧版客户端服务器地址", "client_addr")
        edit(advanced, "旧版客户端服务器端口", "client_port")
        edit(advanced, "旧版服务监听地址", "server_addr")
        edit(advanced, "旧版服务监听端口", "server_port")
        ttk.Label(advanced, text="新托盘版直接调用本地大模型，无需部署 WebSocket 服务；"
                  "“旧版”网络选项不会改变当前托盘程序的监听地址。",
                  foreground="#555", wraplength=690).pack(anchor="w", pady=(14, 4))

        footer = ttk.Frame(win)
        footer.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        ttk.Button(footer, text="保存全部设置", command=self._apply_settings).pack(side="right")
        ttk.Button(footer, text="隐藏到托盘", command=self.hide_settings).pack(
            side="right", padx=(0, 9))

    def hide_settings(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.withdraw()

    def _pump(self):
        for _ in range(35):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status = value
                try:
                    self.tray.title = f"{APP_TITLE}: {value[:75]}"
                except Exception:
                    pass
                if hasattr(self, "status_var"):
                    self.status_var.set(value)
            elif kind == "ready":
                self._open_microphone()
            elif kind == "installed":
                self.settings["model_dir"] = value
                save_settings(self.settings)
                if hasattr(self, "model_path_var"):
                    self.model_path_var.set(value)
                self._begin_load(value)
            elif kind == "recognized":
                self._recognized(*value)
            elif kind == "settings":
                self.show_settings()
            elif kind == "manual":
                self._manual_correction()
            elif kind == "update":
                self._check_updates()
            elif kind == "toggle":
                self.enabled = not self.enabled
                self._set_status("语音输入已启用" if self.enabled else "语音输入已暂停")
            elif kind == "logs":
                os.startfile(str(APP_HOME))
            elif kind == "info":
                messagebox.showinfo(APP_TITLE, value, parent=self.window or self.root)
            elif kind == "error":
                self._set_status(value)
                logging.error(value)
                messagebox.showerror(APP_TITLE, value, parent=self.window or self.root)
            elif kind == "quit":
                self.quit()
                return
        self.root.after(120, self._pump)

    def quit(self):
        self.running = False
        try:
            self.key_binding.close()
        except Exception:
            pass
        try:
            if self.audio_stream:
                self.audio_stream.stop()
                self.audio_stream.close()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        self.root.quit()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    if sys.platform != "win32":
        raise SystemExit("仅支持 Windows 10/11")
    mutex = register_single_instance()
    if mutex is None:
        ctypes.windll.user32.MessageBoxW(None, "程序已在后台运行，请从右下角托盘打开设置。",
                                         APP_TITLE, 0x40)
        return
    app = VoiceApp()
    app.run()


if __name__ == "__main__":
    main()
