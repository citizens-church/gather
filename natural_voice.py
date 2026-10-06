"""Supertonic 3: local Spanish and Korean neural speech, with pinned assets."""
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import math

ROOT = Path(__file__).parent
_model = None
_lock = threading.Lock()

def configured():
    return all((ROOT / "models/supertonic/onnx" / name).exists() for name in ("duration_predictor.onnx", "text_encoder.onnx", "vector_estimator.onnx", "vocoder.onnx", "tts.json", "unicode_indexer.json"))

def load():
    global _model
    if _model is None:
        from supertonic import TTS
        _model = TTS(model="supertonic-3", model_dir=ROOT / "models/supertonic", auto_download=False, intra_op_num_threads=2, inter_op_num_threads=1)
    return _model

def identity(lang, style="dora", speed=1.05):
    return f"supertonic-3:{'M1' if style == 'alex' else 'F1'}:{lang}:8:{speed:.2f}:scripture-v1:sentences-v1:pacing-v2"

def select_speed(estimated_seconds, target_seconds, language):
    if language not in {"es","ko"} or not target_seconds or target_seconds <= 0 or not math.isfinite(target_seconds):
        return 1.05
    # Keep the normal voice unless its duration would build a playback queue.
    # Allow 8% interpretation overhead; cap the native duration multiplier.
    requested = estimated_seconds * 1.05 / (target_seconds * 1.08)
    return round(min(1.25 if language=='es' else 1.35, max(1.05, requested)), 2)

def pacing(text, lang, style="dora", target_seconds=None):
    if lang not in {"es","ko"} or not target_seconds or target_seconds <= 0:
        return {"speed": 1.05, "estimated_seconds": None}
    from supertonic.utils import chunk_text
    with _lock:
        tts = load()
        voice = tts.get_voice_style("M1" if style == "alex" else "F1")
        chunks = chunk_text(spoken_text(text, lang), 120)
        duration = .12 * max(0, len(chunks)-1)
        for chunk in chunks:
            ids, mask = tts.model.text_processor([chunk], lang)
            predicted, *_ = tts.model.dp_ort.run(None, {"text_ids": ids, "style_dp": voice.dp, "text_mask": mask})
            duration += float(predicted.reshape(-1)[0]) / 1.05
    speed = select_speed(duration, target_seconds, lang)
    return {"speed": speed, "estimated_seconds": round(duration, 3), "target_seconds": target_seconds, "limited": speed >= (1.25 if lang=='es' else 1.35)}

def spoken_text(text, lang):
    if lang == "ko":
        books = "창세기|출애굽기|레위기|민수기|신명기|여호수아|사사기|룻기|사무엘상|사무엘하|열왕기상|열왕기하|역대상|역대하|에스라|느헤미야|에스더|욥기|시편|잠언|전도서|아가|이사야|예레미야|예레미야애가|에스겔|다니엘|호세아|요엘|아모스|오바댜|요나|미가|나훔|하박국|스바냐|학개|스가랴|말라기|마태복음|마가복음|누가복음|요한복음|사도행전|로마서|고린도전서|고린도후서|갈라디아서|에베소서|빌립보서|골로새서|데살로니가전서|데살로니가후서|디모데전서|디모데후서|디도서|빌레몬서|히브리서|야고보서|베드로전서|베드로후서|요한일서|요한이서|요한삼서|유다서|요한계시록"
        def reference(match):
            book, chapter, verse, last = match.groups()
            return f"{book} {chapter}장 {verse}절" + (f"부터 {last}절" if last else "")
        return re.sub(r"(" + books + r")\s*(\d{1,3}):(\d{1,3})(?:[-–](\d{1,3}))?", reference, text)
    if lang == "es":
        def reference(match):
            book, chapter, verse, last = match.groups()
            return f"{book}, capítulo {chapter}, " + (f"versículos {verse} a {last}" if last else f"versículo {verse}")
        return re.sub(r"\b(Juan|Mateo|Marcos|Lucas|Romanos|Génesis|Éxodo|Salmos|Proverbios|Habacuc|Efesios|Apocalipsis)\s+(\d{1,3}):(\d{1,3})(?:[-–](\d{1,3}))?", reference, text)
    return text

def synthesize(text, lang, path, style="dora", speed=1.05):
    with _lock:
        model = load()
        voice = model.get_voice_style("M1" if style == "alex" else "F1")
        wav, duration = model.synthesize(spoken_text(text, lang), voice_style=voice, lang=lang, total_steps=8, speed=speed, silence_duration=.12, verbose=False)
        with tempfile.TemporaryDirectory(prefix="gather-natural-") as directory:
            raw = Path(directory) / "voice.wav"
            model.save_audio(wav, raw)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-codec:a", "libmp3lame", "-b:a", "96k", str(path)], check=True, capture_output=True, timeout=20)
        return {"speed": speed, "duration_seconds": round(float(duration[0]), 3)}
