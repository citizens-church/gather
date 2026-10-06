"""Local, repeatable sermon replay. Never publishes test lines to the congregation."""
import asyncio
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from urllib.request import Request as URLRequest, urlopen
import wave
import numpy as np
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from audio_sources import Livestream, SourceError, validate_url
from speech_gate import PhraseBuffer, ThoughtBuffer, incomplete_english
from sermon_metrics import alignment, character_similarity, review_flags
import natural_voice

def caption_excerpt(data, start, duration):
    """Word timestamps prevent taking a whole caption outside the requested clip."""
    end = start + duration
    selected = []
    for event in data.get("events", []):
        base = event.get("tStartMs", 0)/1000
        for segment in event.get("segs", []):
            stamp = base + segment.get("tOffsetMs", 0)/1000
            if start <= stamp < end:
                selected.append(segment.get("utf8", ""))
    text = re.sub(r"\[(?:music|applause|laughter)\]", " ", "".join(selected), flags=re.I)
    return re.sub(r"\s+", " ", text).strip()

def youtube_reference(url, start, duration):
    import shutil
    import yt_dlp
    from urllib.parse import urlsplit
    if urlsplit(url).hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        return None
    class Quiet:
        def debug(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg): pass
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "socket_timeout": 15, "retries": 1, "logger": Quiet(), "js_runtimes": {"node": {"path": shutil.which("node") or "node"}}}) as dl:
        info = dl.extract_info(url, download=False)
    for category, provenance in (("subtitles", "Uploader captions; not yet checked against audio"), ("automatic_captions", "YouTube automatic captions; not human-verified")):
        tracks = info.get(category, {})
        track = tracks.get("en-orig") or tracks.get("en") or []
        candidate = next((item for item in track if item.get("ext") == "json3"), None)
        if candidate:
            validate_url(candidate["url"])
            with urlopen(URLRequest(candidate["url"], headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as response:
                data = json.loads(response.read(4_000_001))
            return {"text": caption_excerpt(data, start, duration), "provenance": provenance, "quality": "provisional"}
    return None

def translate_pass(engine, lines, register="spoken"):
    outputs = {"es": [], "ko": []}
    timings = {"es": [], "ko": []}
    errors = {"es": [], "ko": []}
    approved = {"es": [], "ko": []}
    previous = {}
    context = ""
    for line_index, text in enumerate(lines):
        for lang in ("es", "ko"):
            if incomplete_english(text):
                errors[lang].append({"source": text, "message": "Unfinished English thought held for review; no voice generated for this phrase."})
                continue
            try:
                result, elapsed = engine.translate(text, [lang], {"source_language": "en", "context": context[-350:], "previous_translations": previous, "korean_register": register})
            except ValueError as exc:
                errors[lang].append({"source": text, "message": str(exc)})
                continue
            outputs[lang].append(result[lang])
            timings[lang].append(elapsed)
            flags = review_flags(text, result[lang], lang)
            errors[lang].extend({"source": text, "message": flag} for flag in flags)
            if not flags:
                approved[lang].append({"source": text, "translation": result[lang], "index": line_index})
            previous[lang] = (text, result[lang])
        context = text
    return {lang: " ".join(values) for lang, values in outputs.items()}, timings, errors, approved

def reference_lines(text):
    # Bound context while preserving complete sentences where possible.
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    lines, current = [], ""
    for sentence in sentences:
        if len((current+sentence).split()) > 60 and current:
            lines.append(current)
            current = ""
        if len(sentence.split()) > 80:
            if current:
                lines.append(current)
                current = ""
            tokens = sentence.split()
            lines.extend(" ".join(tokens[i:i+60]) for i in range(0, len(tokens), 60))
        else:
            current = (current + " " + sentence).strip()
    if current:
        lines.append(current)
    return lines

def evaluate_audio(engine, samples, config, directory, reference, progress=lambda message: None):
    phrases = PhraseBuffer()
    thoughts = ThoughtBuffer()
    carry = np.zeros(0, np.float32)
    recognized = []
    rows = []
    pending_seconds = 0.
    progress("Recognizing the sermon with the live speech boundaries")
    for offset in range(0, len(samples), 1600):
        item = phrases.push(samples[offset:offset+1600])
        if item:
            block, speech_end, final = item
            combined = np.concatenate((carry, block))
            text, elapsed, carry = engine.transcribe_window(combined, final)
            # Consumed audio excludes the unfinished tail retained for the next pass.
            # block alone can be shorter than the combined recognition window.
            consumed = (len(combined)-len(carry))/16000
            pending_seconds += consumed
            thought = thoughts.push(text)
            if thought:
                recognized.append(thought[0])
                rows.append({"source": thought[0], "speech_end_seconds": round(speech_end, 3), "commit_seconds": round(phrases.offset/16000, 3), "asr_ms": elapsed, "held_for_review": thought[1], "audio_seconds": round(pending_seconds, 3)})
                pending_seconds = 0.
    item = phrases.flush()
    tail = np.concatenate((carry, item[0])) if item else carry
    if len(tail):
        text, elapsed = engine.transcribe(tail)
        pending_seconds += len(tail)/16000
        thought = thoughts.push(text)
        if thought:
            recognized.append(thought[0])
            rows.append({"source": thought[0], "speech_end_seconds": round(phrases.speech_end/16000, 3), "commit_seconds": len(samples)/16000, "asr_ms": elapsed, "held_for_review": thought[1], "audio_seconds": round(pending_seconds, 3)})
            pending_seconds = 0.
    thought = thoughts.finish()
    if thought:
        recognized.append(thought[0])
        rows.append({"source": thought[0], "speech_end_seconds": round(phrases.speech_end/16000, 3), "commit_seconds": len(samples)/16000, "held_for_review": thought[1], "audio_seconds": round(pending_seconds, 3)})
    source = " ".join(recognized)
    progress("Checking speech-boundary omissions against a full-context recognition pass")
    full_context, full_asr_ms = engine.transcribe(samples)
    progress("Translating what was actually heard into Spanish and Korean")
    translations, timing, errors, approved = translate_pass(engine, recognized, config.korean_register)
    isolated = None
    if reference and reference.get("text"):
        progress("Translating the reference transcript to separate recognition errors")
        isolated, _, reference_errors, _ = translate_pass(engine, reference_lines(reference["text"]), config.korean_register)
    else:
        reference_errors = {"es": [], "ko": []}
    voices = {}
    for lang in ("es", "ko"):
        if approved[lang]:
            progress(f"Generating the {lang} voice for listening review")
            begin = time.perf_counter()
            files, plans = [], []
            for index, utterance in enumerate(approved[lang]):
                row = rows[utterance["index"]]
                pace = natural_voice.pacing(utterance["translation"], lang, target_seconds=row["audio_seconds"])
                path = directory / f"{lang}-p{index}.mp3"
                natural_voice.synthesize(utterance["translation"], lang, path, speed=pace["speed"])
                plans.append(pace)
                files.append(path)
            with tempfile.TemporaryDirectory(prefix="gather-lab-join-") as temporary:
                listing = Path(temporary) / "pieces.txt"
                listing.write_text("\n".join("file '"+str(path).replace("'", "'\\''")+"'" for path in files))
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(directory/f"{lang}.mp3")], check=True, capture_output=True, timeout=20)
            voices[lang] = {"filename": f"{lang}.mp3", "tts_ms": round((time.perf_counter()-begin)*1000), "excludes_held_phrases": bool(errors[lang]), "utterance_pacing": plans}
            duration = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(directory / f"{lang}.mp3")], timeout=10))
            voices[lang].update({"duration_seconds": round(duration, 3), "duration_ratio": round(duration/(len(samples)/16000), 3)})
    results = {}
    for lang in ("es", "ko"):
        expected = getattr(config, f"reference_{lang}")
        results[lang] = {"text": translations[lang], "from_reference_transcript": isolated[lang] if isolated else None,
            "reference_translation": expected or None, "reference_quality": config.translation_quality,
            "character_similarity": character_similarity(expected, translations[lang]) if expected else None,
            "checks": [entry["message"]+" Source: "+entry["source"] for entry in errors[lang]], "reference_checks": reference_errors[lang], "translation_ms": timing[lang], "voice": voices.get(lang),
            "human_review": {"meaning": None, "naturalness": None, "voice": None, "notes": "", "reviewer": ""}}
    return {"title": config.title, "source_url": config.url, "start_seconds": config.start_seconds, "audio_seconds": round(len(samples)/16000, 3),
        "recognized": source, "segments": rows, "reference": reference,
        "recognition_comparison": alignment(reference["text"], source) if reference and reference.get("text") else None,
        "full_context_transcript": full_context, "boundary_comparison": alignment(full_context, source), "full_context_asr_ms": full_asr_ms,
        "languages": results, "model": engine.translation_model, "speech_detector": "Silero v6.2 ONNX", "korean_register": config.korean_register,
        "korean_profile": "sermon-ko-v2",
        "limits": "Offline replay with the live speech boundaries. Inference timings exclude network, live queueing and listener playback. WER against provisional captions measures disagreement, not verified accuracy. Character similarity is wording overlap, not meaning or naturalness. Bilingual listening review is required.",
        "completed_at": time.time()}

class LabConfig(BaseModel):
    title: str = Field(default="Sermon listening test", max_length=120)
    url: str = Field(min_length=8, max_length=2000)
    start_seconds: float = Field(default=2100, ge=0, le=86400)
    duration_seconds: int = Field(default=30, ge=5, le=120)
    transcript: str = Field(default="", max_length=12000)
    transcript_quality: str = Field(default="draft", pattern="^(draft|reviewed)$")
    reference_es: str = Field(default="", max_length=12000)
    reference_ko: str = Field(default="", max_length=12000)
    translation_quality: str = Field(default="draft", pattern="^(draft|reviewed)$")
    use_captions: bool = True
    korean_register: str = Field(default="spoken", pattern="^(spoken|formal)$")
    reuse_audio: str | None = Field(default=None, pattern="^[0-9a-f]{32}$")

class Review(BaseModel):
    language: str = Field(pattern="^(es|ko)$")
    meaning: int = Field(ge=1, le=5)
    naturalness: int = Field(ge=1, le=5)
    voice: int = Field(ge=1, le=5)
    notes: str = Field(default="", max_length=4000)
    reviewer: str = Field(min_length=1, max_length=120)

class SermonLab:
    def __init__(self, root, runtime, require_host, infer, get_engine, unavailable):
        self.root, self.directory = root, runtime / "sermon-lab"
        self.directory.mkdir(exist_ok=True, mode=0o700)
        self.require_host, self.infer, self.get_engine, self.unavailable = require_host, infer, get_engine, unavailable
        self.busy = False
        self.tasks = set()
        self.states = {}

    def save(self, path, data):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(path)

    async def run(self, job_id, config):
        directory = self.directory / job_id
        directory.mkdir(mode=0o700)
        def progress(message):
            self.states[job_id] = {"status": "running", "message": message}
        source = None
        try:
            if config.reuse_audio:
                progress("Replaying the saved sermon audio")
                parent = self.path(config.reuse_audio)
                previous = json.loads((parent / "report.json").read_text())
                config.title, config.url, config.start_seconds = previous["title"], previous["source_url"], previous["start_seconds"]
                shutil.copyfile(parent / "source.wav", directory / "source.wav")
                with wave.open(str(directory / "source.wav"), "rb") as audio:
                    data = bytearray(audio.readframes(audio.getnframes()))
                config.duration_seconds = max(5, min(120, round(len(data)/32000)))
            else:
                progress("Connecting to the public sermon audio")
                source = await asyncio.wait_for(Livestream.open(config.url, config.start_seconds, realtime=False), 45)
                config.title = source.title
                config.start_seconds = source.start_seconds
                live_source = source.live
                data = bytearray()
                while len(data) < config.duration_seconds*32000:
                    message = await asyncio.wait_for(source.receive(), 20)
                    if message["type"] == "websocket.disconnect":
                        break
                    data.extend(message.get("bytes", b""))
                await source.close()
            samples = np.frombuffer(data[:config.duration_seconds*32000], "<i2").astype(np.float32)/32768
            if len(samples) < 16000:
                raise ValueError("The source did not provide a full second of audio.")
            with wave.open(str(directory / "source.wav"), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(bytes(data[:len(samples)*2]))
            reference = {"text": config.transcript, "quality": config.transcript_quality, "provenance": "Operator-supplied transcript"} if config.transcript.strip() else None
            if config.reuse_audio and reference and config.transcript_quality == "draft" and previous.get("reference") and previous["reference"]["text"] == config.transcript:
                reference = previous["reference"]
            caption_error = None
            if not reference and config.use_captions and not (source and live_source):
                progress("Fetching the English caption excerpt as a provisional reference")
                try:
                    reference = await asyncio.to_thread(youtube_reference, config.url, config.start_seconds, len(samples)/16000)
                except Exception:
                    caption_error = "English captions were unavailable. Paste a transcript and rerun this saved audio."
            elif not reference and source and live_source:
                caption_error = "Live-edge audio has no matching timestamped caption reference. Listen to this saved clip, paste its transcript, and rerun."
            report = await self.infer(evaluate_audio, self.get_engine(), samples, config, directory, reference, progress)
            report.update({"id": job_id, "caption_error": caption_error, "config": config.model_dump()})
            self.save(directory / "report.json", report)
            self.states[job_id] = {"status": "complete", "message": "Ready for bilingual listening review"}
        except asyncio.CancelledError:
            self.states[job_id] = {"status": "error", "message": "Test interrupted; recorded audio was kept."}
            raise
        except Exception as exc:
            self.states[job_id] = {"status": "error", "message": str(exc) if isinstance(exc, (ValueError, SourceError)) else f"Sermon test failed ({type(exc).__name__}). Check the source and try again."}
        finally:
            if source:
                await source.close()
            self.busy = False

    def path(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise HTTPException(404)
        return self.directory / job_id

    def register(self, app):
        @app.get("/lab")
        async def page(request: Request):
            if request.client.host not in {"127.0.0.1", "::1"}:
                raise HTTPException(403)
            return FileResponse(self.root / "public/lab.html")

        @app.post("/api/lab")
        async def start(config: LabConfig, request: Request):
            self.require_host(request)
            if self.busy or self.unavailable():
                raise HTTPException(409, "Pause the live audio and let pending lines finish before running a sermon test.")
            if self.get_engine() is None:
                raise HTTPException(503, "The local models are still loading.")
            if config.reuse_audio and not (self.path(config.reuse_audio) / "report.json").exists():
                raise HTTPException(404, "The saved sermon test could not be found.")
            try:
                await asyncio.to_thread(validate_url, config.url)
            except Exception as exc:
                raise HTTPException(400, str(exc)) from None
            # Recheck after the asynchronous URL validation.
            if self.busy or self.unavailable():
                raise HTTPException(409, "The booth is busy. Pause live audio before testing.")
            self.busy = True
            job_id = secrets.token_hex(16)
            self.states[job_id] = {"status": "running", "message": "Starting sermon replay"}
            task = asyncio.create_task(self.run(job_id, config))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return {"id": job_id}

        @app.get("/api/lab")
        async def history(request: Request):
            self.require_host(request)
            reports = []
            for path in sorted(self.directory.glob("*/report.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
                report = json.loads(path.read_text())
                reports.append({k: report[k] for k in ("id", "title", "audio_seconds", "completed_at")})
            return {"reports": reports, "busy": self.busy}

        @app.get("/api/lab/{job_id}")
        async def result(job_id: str, request: Request):
            self.require_host(request)
            path = self.path(job_id) / "report.json"
            if path.exists():
                return {"status": "complete", "report": json.loads(path.read_text())}
            if job_id not in self.states:
                raise HTTPException(404)
            return self.states[job_id]

        @app.get("/api/lab/{job_id}/audio/{language}")
        async def audio(job_id: str, language: str, request: Request):
            self.require_host(request)
            if language not in {"source", "es", "ko"}:
                raise HTTPException(404)
            path = self.path(job_id) / ("source.wav" if language == "source" else f"{language}.mp3")
            if not path.exists():
                raise HTTPException(404)
            return FileResponse(path)

        @app.post("/api/lab/{job_id}/review")
        async def review(job_id: str, review: Review, request: Request):
            self.require_host(request)
            path = self.path(job_id) / "report.json"
            if not path.exists():
                raise HTTPException(404)
            report = json.loads(path.read_text())
            value = review.model_dump()
            value.pop("language")
            value["reviewed_at"] = time.time()
            report["languages"][review.language]["human_review"] = value
            self.save(path, report)
            return {"saved": True}
