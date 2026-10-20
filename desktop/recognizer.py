"""Keep one full-size Paraformer + CT-Transformer session resident in memory."""
from __future__ import annotations
import re
import threading
from pathlib import Path

import numpy as np


class FullRecognizer:
    def __init__(self, directory, progress=lambda status: None):
        directory = Path(directory)
        self._lock = threading.Lock()
        progress("正在初始化 Paraformer 大模型…")
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
        progress("正在初始化 CT-Transformer 标点大模型…")
        from funasr_onnx import CT_Transformer
        self.punc = CT_Transformer(str(directory / "punc_ct-transformer_cn-en"), quantize=True)
        progress("原版大模型已载入内存，可以直接开始语音输入")

    def _decode(self, samples):
        stream = self.asr.create_stream()
        stream.accept_waveform(16000, np.ascontiguousarray(samples, dtype=np.float32))
        self.asr.decode_stream(stream)
        tokens = stream.result.tokens
        text = " ".join(tokens).replace("@@ ", "")
        return re.sub(r"([^a-zA-Z0-9]) (?![a-zA-Z0-9])", r"\1", text).strip()

    def transcribe(self, audio):
        """Inference only: do not re-create recognizers between recordings."""
        with self._lock:
            samples = np.asarray(audio, dtype=np.float32).reshape(-1)
            if samples.size < 4800:
                return ""
            # Avoid feeding arbitrarily long recordings to an offline short-utterance model.
            segment = 20 * 16000
            parts = [self._decode(samples[i:i + segment]) for i in range(0, samples.size, segment)]
            text = " ".join(part for part in parts if part).strip()
            if text:
                text = self.punc(text)[0]
            return text.strip()
