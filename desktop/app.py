"""Windows tray voice input using the ORIGINAL full-size CapsWriter models."""
from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
import queue
import re
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
from model_store import APP_HOME, MODEL_ROOT, install_original, is_ready, locate_model
from preferences import load_settings, parse_mappings, save_settings
from quicker_bridge import ACTION_ID, connect_quicker
from recognizer import FullRecognizer

APP_TITLE = "CapsWriter Lite"
APP_VERSION = "0.2.0"


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
            executable = Path(sys.executable)
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
        self.audio_chunks = []
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
            pystray.MenuItem("打开日志",
                             lambda icon, item: self.events.put(("logs", None))),
            pystray.MenuItem("完全退出",
                             lambda icon, item: self.events.put(("quit", None))),
        ))
        threading.Thread(target=self.tray.run, daemon=True, name="tray").start()
        self.root.after(120, self._pump)
        try:
            keyboard.hook_key("caps lock", self._on_caps, suppress=True)
        except Exception as exc:
            self._emit("error", f"无法注册 CapsLock：{exc}")
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
            model = FullRecognizer(directory, self._set_status)
            with self.model_lock:
                self.model = model
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
            self._set_status("已就绪：按住 CapsLock 说话，松开后输入")
        except Exception as exc:
            self.audio_stream = None
            self._emit("error", f"麦克风初始化失败：{exc}")

    def _audio_callback(self, indata, frames, timing, status):
        with self.sound_lock:
            if self.recording:
                self.audio_chunks.append(indata[:, 0].copy())

    def _on_caps(self, event):
        # Runs on keyboard's hook thread; never touch Tk widgets here.
        if not self.running:
            return
        if event.event_type == "down" and not self.key_pressed:
            self.key_pressed = True
            if not self.enabled or not self.model or not self.audio_stream:
                return
            with self.sound_lock:
                self.audio_chunks = []
                self.recording = True
                self.record_started = time.monotonic()
            self.focus_hwnd = ctypes.windll.user32.GetForegroundWindow()
            self._emit("status", "正在录音…松开 CapsLock 后识别")
        elif event.event_type == "up" and self.key_pressed:
            self.key_pressed = False
            with self.sound_lock:
                self.recording = False
                chunks = self.audio_chunks
                self.audio_chunks = []
                duration = time.monotonic() - self.record_started if self.record_started else 0
                self.record_started = 0
            if chunks and duration >= 0.3:
                self.jobs.put((np.concatenate(chunks), self.focus_hwnd))
                self._emit("status", "正在识别…")
                self._start_inference_if_needed()

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
                audio, hwnd = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                text = self.model.transcribe(audio)
                self._emit("recognized", (text, hwnd))
            except Exception as exc:
                logging.exception("Failed to recognize audio")
                self._emit("error", f"语音识别失败：{exc}")

    def _recognized(self, raw, hwnd):
        self.last_raw = raw
        self.last_text = replace_hotwords(raw, self.settings["hotwords"])
        if not self.last_text:
            self._set_status("未识别到有效语音")
            return
        command = self.last_text.strip(" ,.!?。，！？")
        action = self.settings["quicker_actions"].get(command)
        if self.settings["quicker_enabled"] and action:
            threading.Thread(target=self._run_quicker, args=(action,),
                             daemon=True, name="quicker").start()
            return

        # Do not type into an unrelated app after a focus switch.
        current_hwnd = ctypes.windll.user32.GetForegroundWindow()
        if hwnd != current_hwnd:
            pyperclip.copy(self.last_text)
            self._set_status("窗口焦点发生变化，文字已复制到剪贴板")
            return
        control = focused_control() if self.settings["track_edits"] else None
        previous = control_text(control) if control else None
        try:
            old_clipboard = pyperclip.paste() if self.settings["restore_clipboard"] else None
        except Exception:
            old_clipboard = None
        try:
            pyperclip.copy(self.last_text)
            keyboard.send("ctrl+v")
        except Exception as exc:
            self._emit("error", f"输入文本失败：{exc}")
            return
        if old_clipboard is not None:
            def restore():
                try:
                    if pyperclip.paste() == self.last_text:
                        pyperclip.copy(old_clipboard)
                except Exception:
                    logging.exception("Could not restore text clipboard")
            self.root.after(650, restore)
        if control and previous is not None:
            self.observer.begin(control, previous, self.last_text)
        self._set_status(f"已输入：{self.last_text[:55]}")

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
            self._set_status(f"已记住热词：{old} → {new}")

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
        self._begin_load(directory)

    def _apply_settings(self):
        try:
            new = dict(self.settings)
            new["hotwords"] = parse_mappings(self.hotword_text.get("1.0", "end"))
            new["quicker_actions"] = parse_mappings(self.quicker_text.get("1.0", "end"))
            if any(not ACTION_ID.fullmatch(action) for action in new["quicker_actions"].values()):
                raise ValueError("Quicker 动作 ID 需要是 UUID 格式")
            new["quicker_enabled"] = bool(self.quicker_enabled_var.get())
            new["track_edits"] = bool(self.edit_enabled_var.get())
            new["restore_clipboard"] = bool(self.clipboard_enabled_var.get())
            new["quicker_host"] = self.quicker_host_var.get().strip()
            new["quicker_port"] = int(self.quicker_port_var.get().strip())
            new["quicker_password"] = self.quicker_password_var.get()
            new["run_at_login"] = bool(self.autostart_var.get())
            if new["quicker_host"] not in ("127.0.0.1", "localhost", "::1"):
                raise ValueError("Quicker 只允许使用本机地址")
            if not 1 <= new["quicker_port"] <= 65535:
                raise ValueError("Quicker 端口范围必须是 1-65535")
            set_run_at_login(new["run_at_login"])
            self.settings = new
            save_settings(new)
            self._set_status("设置已保存，不需要重启模型")
            messagebox.showinfo(APP_TITLE, "保存成功，配置已经生效", parent=self.window)
        except (ValueError, OSError) as exc:
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
        win.geometry("640x595")
        win.minsize(550, 500)
        win.protocol("WM_DELETE_WINDOW", self.hide_settings)
        win.columnconfigure(0, weight=1)
        book = ttk.Notebook(win)
        book.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        win.rowconfigure(0, weight=1)

        basic = ttk.Frame(book, padding=14)
        basic.columnconfigure(0, weight=1)
        book.add(basic, text="语音与模型")
        ttk.Label(basic, text="按住 CapsLock 说话，松开后自动输入到当前窗口。"
                  "\n模型加载成功后常驻后台；关闭本窗口不会退出模型。", justify="left").pack(anchor="w", pady=(4, 16))
        self.status_var = tk.StringVar(value=self.status)
        ttk.Label(basic, textvariable=self.status_var, wraplength=530).pack(anchor="w", pady=(0, 12))
        ttk.Label(basic, text="原版模型目录：").pack(anchor="w")
        self.model_path_var = tk.StringVar(value=self.settings.get("model_dir") or "未选择")
        ttk.Entry(basic, textvariable=self.model_path_var, state="readonly").pack(fill="x", pady=5)
        controls = ttk.Frame(basic)
        controls.pack(fill="x", pady=(4, 12))
        ttk.Button(controls, text="选择现有模型", command=self._choose_model).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="下载原版大模型（1.18 GB）",
                   command=self._download_model).pack(side="left")
        self.autostart_var = tk.BooleanVar(value=self.settings["run_at_login"])
        ttk.Checkbutton(basic, text="随 Windows 登录启动，并在后台预加载模型",
                        variable=self.autostart_var).pack(anchor="w", pady=6)
        self.edit_enabled_var = tk.BooleanVar(value=self.settings["track_edits"])
        ttk.Checkbutton(basic, text="检测上次输入的纠错，并询问是否记为热词（支持部分编辑器）",
                        variable=self.edit_enabled_var).pack(anchor="w", pady=6)
        self.clipboard_enabled_var = tk.BooleanVar(value=self.settings["restore_clipboard"])
        ttk.Checkbutton(basic, text="输入后恢复原纯文本剪贴板",
                        variable=self.clipboard_enabled_var).pack(anchor="w", pady=6)
        ttk.Label(basic, text="备注：后台常驻占用内存，关闭窗口不释放模型。"
                  "\n仅点击右下角托盘的“完全退出”才退出。", justify="left",
                  foreground="#555").pack(anchor="w", pady=(18, 0))

        hotwords = ttk.Frame(book, padding=14)
        hotwords.rowconfigure(1, weight=1)
        hotwords.columnconfigure(0, weight=1)
        book.add(hotwords, text="热词与记忆")
        ttk.Label(hotwords, text="每行一个替换关系：识别错误词 = 正确词\n"
                  "例如：安格 = angr。修改后点击“保存设置”即时生效。").grid(row=0, column=0, sticky="w")
        self.hotword_text = tk.Text(hotwords, undo=True, wrap="none", height=15)
        self.hotword_text.grid(row=1, column=0, sticky="nsew", pady=10)
        self.hotword_text.insert("1.0", "\n".join(
            f"{k} = {v}" for k, v in self.settings["hotwords"].items()))

        quicker = ttk.Frame(book, padding=14)
        quicker.columnconfigure(1, weight=1)
        quicker.rowconfigure(6, weight=1)
        book.add(quicker, text="Quicker")
        self.quicker_enabled_var = tk.BooleanVar(value=self.settings["quicker_enabled"])
        ttk.Checkbutton(quicker, text="启用 Quicker 语音命令（默认关闭）",
                        variable=self.quicker_enabled_var).grid(row=0, column=0, columnspan=2,
                                                              sticky="w", pady=(0, 12))
        self.quicker_host_var = tk.StringVar(value=self.settings["quicker_host"])
        self.quicker_port_var = tk.StringVar(value=str(self.settings["quicker_port"]))
        self.quicker_password_var = tk.StringVar(value=self.settings["quicker_password"])
        for row, name, var, show in (
            (1, "本机地址", self.quicker_host_var, ""),
            (2, "WebSocket 端口", self.quicker_port_var, ""),
            (3, "Quicker 密码（如有）", self.quicker_password_var, "*"),
        ):
            ttk.Label(quicker, text=name).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(quicker, textvariable=var, show=show).grid(
                row=row, column=1, sticky="ew", pady=5, padx=(12, 0))
        ttk.Button(quicker, text="测试 Quicker 连接", command=self._test_quicker).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 4))
        ttk.Label(quicker, text="语音指令 = Quicker 动作 UUID（必须完整匹配，避免误触发）").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.quicker_text = tk.Text(quicker, undo=True, wrap="none", height=10)
        self.quicker_text.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=8)
        self.quicker_text.insert("1.0", "\n".join(
            f"{k} = {v}" for k, v in self.settings["quicker_actions"].items()))

        footer = ttk.Frame(win)
        footer.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        ttk.Button(footer, text="保存设置", command=self._apply_settings).pack(side="right")
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
            keyboard.unhook_all()
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
