"""Persistent installer for the original full-size CapsWriter model bundle."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import urllib.error
import urllib.request
import zipfile

APP_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "CapsWriterLite"
MODEL_ROOT = APP_HOME / "models"
ARCHIVE = APP_HOME / "downloads" / "models.zip"
MODEL_URL = "https://github.com/shandianchengzi/CapsWriter-Video2Srt-Offline/releases/download/1/models.zip"
MODEL_SHA256 = "9d14d117978e2a6c3985fa5ba4f53d5071d0412cb7a52b261a320de2b9e54723"


def normalize_model_path(folder):
    """Accept either models/, its parent, or a model directory chosen manually."""
    folder = Path(folder).expanduser().resolve()
    if (folder / "paraformer-offline-zh").is_dir():
        return folder
    if (folder / "models" / "paraformer-offline-zh").is_dir():
        return folder / "models"
    return folder


def is_ready(folder):
    folder = normalize_model_path(folder)
    model = folder / "paraformer-offline-zh" / "model.int8.onnx"
    tokens = folder / "paraformer-offline-zh" / "tokens.txt"
    punc = folder / "punc_ct-transformer_cn-en"
    return (model.is_file() and model.stat().st_size > 10_000_000
            and tokens.is_file() and tokens.stat().st_size > 100
            and punc.is_dir() and any(punc.rglob("*.onnx")))


def locate_model(configured=""):
    if configured and is_ready(configured):
        return normalize_model_path(configured)
    if is_ready(MODEL_ROOT):
        return MODEL_ROOT
    # Existing installations should be reusable without another 1.18 GB download.
    for parent in (Path.cwd(), Path(__file__).resolve().parent.parent,
                   Path(os.sys.executable).resolve().parent):
        if is_ready(parent):
            return normalize_model_path(parent)
    return None


def _sha256(path, notify):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    notify("已验证下载包 SHA256")
    return digest.hexdigest()


def _download_archive(notify):
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    if ARCHIVE.exists() and _sha256(ARCHIVE, notify) == MODEL_SHA256:
        return
    ARCHIVE.unlink(missing_ok=True)
    part = ARCHIVE.with_suffix(".zip.part")
    for attempt in range(3):
        try:
            offset = part.stat().st_size if part.exists() else 0
            request = urllib.request.Request(MODEL_URL, headers={
                "User-Agent": "CapsWriterLite/0.1",
                **({"Range": f"bytes={offset}-"} if offset else {}),
            })
            with urllib.request.urlopen(request, timeout=90) as response:
                resume = offset > 0 and response.status == 206
                if not resume:
                    offset = 0
                size = int(response.headers.get("Content-Length") or 0) + offset
                done = offset
                with part.open("ab" if resume else "wb") as out:
                    while True:
                        data = response.read(1024 * 1024)
                        if not data:
                            break
                        out.write(data)
                        done += len(data)
                        if size:
                            notify(f"下载原版大模型：{done * 100 // size}%")
                if size and done != size:
                    raise IOError("下载中断，保留临时文件用于断点续传")
            if _sha256(part, notify) != MODEL_SHA256:
                part.unlink(missing_ok=True)
                raise ValueError("模型包校验失败，已丢弃损坏的下载文件")
            part.replace(ARCHIVE)
            return
        except (OSError, ValueError, urllib.error.URLError) as exc:
            notify(f"模型下载重试 {attempt + 1}/3：{exc}")
            if attempt == 2:
                raise RuntimeError(f"下载模型失败：{exc}") from exc


def install_original(notify=lambda text: None):
    """Download once; extract into a temporary folder and atomically install."""
    if is_ready(MODEL_ROOT):
        notify("已存在完整的大模型，直接复用")
        return MODEL_ROOT
    _download_archive(notify)
    staging = APP_HOME / "models.installing"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(ARCHIVE) as zf:
            items = zf.infolist()
            total_uncompressed = sum(x.file_size for x in items)
            if total_uncompressed > 12 * 1024 ** 3 or len(items) > 2000:
                raise ValueError("模型包内容超出安全限制")
            for i, entry in enumerate(items):
                name = entry.filename.replace("\\", "/")
                parts = name.split("/")
                if parts and parts[0] == "models":
                    parts = parts[1:]
                # Never extract absolute paths or path traversal.
                if not parts or any(part in ("", ".", "..") for part in parts):
                    continue
                if parts[0] not in ("paraformer-offline-zh", "punc_ct-transformer_cn-en"):
                    continue
                target = staging.joinpath(*parts)
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(entry) as source, target.open("wb") as dest:
                        shutil.copyfileobj(source, dest, length=1024 * 1024)
                if i % 10 == 0:
                    notify(f"解压原版模型：{i + 1}/{len(items)}")
        if not is_ready(staging):
            raise ValueError("压缩包解压后模型不完整，请检查模型文件布局")
        MODEL_ROOT.parent.mkdir(parents=True, exist_ok=True)
        if MODEL_ROOT.exists():
            shutil.rmtree(MODEL_ROOT)
        staging.replace(MODEL_ROOT)
        ARCHIVE.unlink(missing_ok=True)  # Keep models, not a redundant 1.18GB archive.
        notify("模型安装完成，下次启动不会重新下载")
        return MODEL_ROOT
    finally:
        if staging.exists():
            shutil.rmtree(staging)
