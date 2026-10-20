"""Resident original Paraformer + CT-Transformer, preserving legacy text formatting."""
from __future__ import annotations
import re
import threading
from pathlib import Path

import numpy as np

from util.chinese_itn import chinese_to_num
from util.format_tools import adjust_space


class FullRecognizer:
    def __init__(self, directory, progress=lambda status: None):
        directory = Path(directory)
        self._lock = threading.Lock()
        progress("正在初始化原版 Paraformer 大模型…")
        import sherpa_onnx
        self.asr = sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=str(directory / "paraformer-offline-zh" / "model.int8.onnx"),
            tokens=str(directory / "paraformer-offline-zh" / "tokens.txt"),
            num_threads=4,
            sample_rate=16000,
            feature_dim=80,
            decoding_method="greedy_search",
            debug=False,
        )
        progress("正在初始化原版 CT-Transformer 标点大模型…")
        from funasr_onnx import CT_Transformer
        self.punc = CT_Transformer(str(directory / "punc_ct-transformer_cn-en"), quantize=True)
        progress("原版大模型已载入内存；此进程内不会再次初始化")

    def _decode(self, samples):
        stream = self.asr.create_stream()
        stream.accept_waveform(16000, np.ascontiguousarray(samples, dtype=np.float32))
        self.asr.decode_stream(stream)
        return list(stream.result.tokens), list(stream.result.timestamps)

    def transcribe(self, audio):
        """Keep recognizers resident; split long dictation with overlap and timestamp trim."""
        with self._lock:
            samples = np.asarray(audio, dtype=np.float32).reshape(-1)
            if samples.size < 4800:
                return ""
            sample_rate = 16000
            segment_samples = 25 * sample_rate
            overlap_samples = 2 * sample_rate
            stride = segment_samples - overlap_samples
            merged = []
            for start in range(0, len(samples), stride):
                end = min(start + segment_samples, len(samples))
                tokens, timestamps = self._decode(samples[start:end])
                duration = (end - start) / sample_rate
                if len(timestamps) == len(tokens):
                    chosen = [
                        token for token, timestamp in zip(tokens, timestamps)
                        if (start == 0 or timestamp >= 1)
                        and (end == len(samples) or timestamp < duration - 1)
                    ]
                else:
                    chosen = tokens  # Some sherpa-onnx versions may omit timestamps.
                # Match the legacy CapsWriter edge de-duplication behavior.
                if len(merged) >= 2 and merged[-2:] == chosen[:2]:
                    chosen = chosen[2:]
                elif merged and merged[-1:] == chosen[:1]:
                    chosen = chosen[1:]
                merged.extend(chosen)
                if end == len(samples):
                    break
            text = " ".join(merged).replace("@@ ", "")
            text = re.sub(r"([^a-zA-Z0-9]) (?![a-zA-Z0-9])", r"\1", text).strip()
            if not text:
                return ""
            # Keep the existing original server's format pipeline.
            text = adjust_space(text)
            text = self.punc(text)[0]
            text = chinese_to_num(text)
            text = adjust_space(text)
            return text.strip()
