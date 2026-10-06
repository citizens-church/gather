"""Persistent Kokoro neural voices; no account, network, or automatic downloads."""
from pathlib import Path
import subprocess
import tempfile
import threading
import numpy as np

ROOT = Path(__file__).parent
VOICES = {"es": ("ef_dora", "es"), "fr": ("ff_siwis", "fr-fr"), "pt": ("pf_dora", "pt-br"), "en": ("af_heart", "en-us")}
_model = None
_lock = threading.Lock()

def configured():
    return all((ROOT / "models/kokoro" / name).exists() for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"))

def identity(lang, spanish_voice="dora"):
    voice = "em_alex" if lang == "es" and spanish_voice == "alex" else VOICES[lang][0]
    return f"kokoro-v1.0:{voice}:{VOICES[lang][1]}:1.08"

def load():
    global _model
    if _model is None:
        import onnxruntime as rt
        from kokoro_onnx import Kokoro
        class CompatibleKokoro(Kokoro):
            def _create_audio(self, phonemes, voice, speed):
                # Exports differ in speed dtype; use the ONNX graph's declared type.
                tokens = self.tokenizer.tokenize(phonemes)
                if len(tokens) > 510:
                    raise ValueError("Voice phrase is too long; shorten it rather than truncate speech.")
                style = np.asarray(voice[len(tokens)], dtype=np.float32)
                specs = {item.name: item.type for item in self.sess.get_inputs()}
                token_key = "input_ids" if "input_ids" in specs else "tokens"
                dtype = np.float32 if specs["speed"] == "tensor(float)" else np.int32
                result = self.sess.run(None, {token_key: np.array([[0, *tokens, 0]], dtype=np.int64), "style": style, "speed": np.array([speed], dtype=dtype)})[0]
                return result.reshape(-1), 24000
        options = rt.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        path = ROOT / "models/kokoro"
        runtime = rt.InferenceSession(str(path / "kokoro-v1.0.onnx"), sess_options=options, providers=["CPUExecutionProvider"])
        _model = CompatibleKokoro.from_session(runtime, str(path / "voices-v1.0.bin"))
    return _model

def synthesize(text, lang, path, spanish_voice="dora"):
    import soundfile as sf
    voice = "em_alex" if lang == "es" and spanish_voice == "alex" else VOICES[lang][0]
    with _lock:
        model = load()
        samples, rate = model.create(text, voice=voice, lang=VOICES[lang][1], speed=1.08)
    with tempfile.TemporaryDirectory(prefix="gather-kokoro-") as directory:
        wav = Path(directory) / "voice.wav"
        sf.write(str(wav), samples, rate)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-codec:a", "libmp3lame", "-b:a", "96k", str(path)], check=True, capture_output=True, timeout=20)
