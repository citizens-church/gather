"""Stateful 16 kHz speech detection and the shared live/replay phrase boundaries.

Silero v6.2 ONNX (MIT), pinned locally. No downloads at runtime.
This detects speech, not an isolated stem or the identity of the preacher.
"""
from collections import deque
from pathlib import Path
import re
import numpy as np
import onnxruntime as ort

MODEL = Path(__file__).parent / "models/silero-v6.2.onnx"

def incomplete_english(text):
    """Conservative fragment check, not a grammar or semantic confidence model."""
    ending = re.sub(r"[^a-z' ]", " ", text.lower())
    ending = re.sub(r"\s+", " ", ending).strip()
    ending = re.sub(r"(?:\s+(?:uh|um))+$", "", ending)
    return bool(re.search(r"\b(?:and|but|because|although|if|you gotta|you might|going to|have to|has to|need to|want to)$", ending)) or (len(ending.split()) >= 3 and ending.endswith(" never"))

class ThoughtBuffer:
    def __init__(self):
        self.pending = ""

    def push(self, text):
        if not text.strip():
            return None
        text = (self.pending + " " + text).strip()
        if incomplete_english(text) and len(text.split()) < 100:
            self.pending = text
            return None
        self.pending = ""
        return text, incomplete_english(text)

    def finish(self):
        text, self.pending = self.pending, ""
        return (text, incomplete_english(text)) if text else None

class SpeechGate:
    def __init__(self, threshold=.5):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(MODEL), sess_options=opts, providers=["CPUExecutionProvider"])
        self.state = np.zeros((2, 1, 128), np.float32)
        self.context = np.zeros((1, 64), np.float32)
        self.pending = np.zeros(0, np.float32)
        self.threshold = threshold
        self.last_probability = 0.

    def probability(self, samples):
        self.pending = np.concatenate((self.pending, np.asarray(samples, np.float32)))
        probabilities = []
        while len(self.pending) >= 512:
            frame, self.pending = self.pending[:512], self.pending[512:]
            block = np.concatenate((self.context, frame[None, :]), axis=1)
            result, self.state = self.session.run(None, {"input": block, "state": self.state, "sr": np.array(16000, np.int64)})
            self.context = block[:, -64:]
            probabilities.append(float(result.reshape(-1)[0]))
        if probabilities:
            self.last_probability = max(probabilities)
        return self.last_probability

class PhraseBuffer:
    """100ms PCM in, utterances out; frequently check complete sentence boundaries."""
    def __init__(self, window_seconds=2):
        self.gate = SpeechGate()
        self.parts = []
        self.pre_roll = deque(maxlen=3)
        self.active = False
        self.count = self.quiet = 0
        self.offset = self.speech_end = 0
        self.probability = 0.
        self.window_samples = int(window_seconds * 16000)

    def push(self, samples):
        self.offset += len(samples)
        self.probability = self.gate.probability(samples)
        speaking = self.probability >= (.35 if self.active else .5)
        if speaking:
            self.speech_end = self.offset
        if not self.active and speaking:
            self.active = True
            self.parts = list(self.pre_roll)
            self.count = sum(len(p) for p in self.parts)
            self.pre_roll.clear()
        if not self.active:
            self.pre_roll.append(samples)
            return None
        self.parts.append(samples)
        self.count += len(samples)
        self.quiet = 0 if speaking else self.quiet + len(samples)
        if self.quiet >= 6400 or self.count >= self.window_samples:
            return self.flush(final=self.quiet >= 6400)
        return None

    def flush(self, final=True):
        result = (np.concatenate(self.parts), self.speech_end / 16000, final) if self.count >= 3200 else None
        self.parts, self.count, self.quiet, self.active = [], 0, 0, not final
        return result

    def preview(self):
        return np.concatenate(self.parts) if self.parts else np.zeros(0, np.float32)
