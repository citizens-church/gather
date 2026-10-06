"""Gather: single-booth, local church-translation prototype."""
import asyncio
import copy
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import difflib
import io
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
import time

import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import qrcode
import qrcode.image.svg
from engine import Engine, LANGUAGES
import voices
import neural_voice
import natural_voice
from audio_sources import Livestream, SourceError
from spanish import validate_translation, source_language, is_mixed
from speech_gate import PhraseBuffer, ThoughtBuffer, incomplete_english
from sermon_lab import SermonLab
from sermon_metrics import review_flags
from notes_service import NotesService
from vocabulary import Vocabulary, matches as vocabulary_matches, explain as vocabulary_explain, speech_text as vocabulary_speech_text
from live_notes import LiveNotes

ROOT = Path(__file__).parent
RUNTIME = Path(os.getenv("GATHER_RUNTIME_DIR", str(ROOT / ".runtime")))
RUNTIME.mkdir(exist_ok=True)
HOST_TOKEN = secrets.token_urlsafe(32)
ROOM_FILE = RUNTIME / "listener-room"
if not ROOM_FILE.exists():
    ROOM_FILE.write_text(secrets.token_urlsafe(18))
    ROOM_FILE.chmod(0o600)
ROOM = ROOM_FILE.read_text().strip()
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")
PREVIEW_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="caption-preview")
engine = None
load_error = None
subscribers = set()
jobs = asyncio.Queue(maxsize=24)
audio_tasks = set()
voice_slots = asyncio.Semaphore(2)
capture_active = False
livestream = None
livestream_task = None
source_connecting = False
capture_finished = asyncio.Event()
capture_finished.set()
CURRENT_FILE = RUNTIME / "current-session.json"
session = {"id": secrets.token_hex(8), "live": False, "title": "Citizens Church", "languages": ["es"], "review": True, "terms": ["Habakkuk", "Melchizedek", "Jehoshaphat", "Gethsemane"], "segments": [], "started": None, "voice_provider": "supertonic" if natural_voice.configured() else "kokoro" if neural_voice.configured() else "local", "source_language": "auto", "spanish_region": "latin", "spanish_voice": "alex", "korean_register": "spoken", "glossary": {}}
if CURRENT_FILE.exists():
    recovered = json.loads(CURRENT_FILE.read_text())
    session.update(recovered)
    # An intentional hot update can keep the same session; ordinary recovery stays paused.
    session["live"] = os.getenv("GATHER_RESUME_SESSION") == session["id"]
    for segment in session["segments"]:
        if segment["status"] in {"queued", "translating"}:
            segment["status"] = "review"
            segment["recovered"] = True

def require_host(request):
    if request.client.host not in {"127.0.0.1", "::1"} or not secrets.compare_digest(request.headers.get("x-host-token", ""), HOST_TOKEN):
        raise HTTPException(403, "Booth controls are available only on this Mac.")

def require_room(room):
    if not secrets.compare_digest(room or "", ROOM):
        raise HTTPException(404, "This listening link is not active.")

def public_segment(seg):
    result = {k: seg[k] for k in ("id", "source", "translations", "audio", "timing", "created", "status")}
    result.update({"audio_parts": seg.get("audio_parts", {}), "audio_complete": seg.get("audio_complete", {}), "vocabulary_explanations":seg.get('vocabulary_explanations', {})})
    return result

def snapshot(host=False):
    result = {k: session[k] for k in ("id", "live", "title", "languages", "started", "voice_provider")}
    result.update({"ready": engine is not None, "error": load_error, "listeners": sum(not item[1] for item in subscribers), "language_names": {k: v["name"] for k, v in LANGUAGES.items()}})
    result["segments"] = [dict(s) if host else public_segment(s) for s in session["segments"] if host or s["status"] == "ready" or bool(s["translations"])]
    result['vocabulary']=vocabulary.public()
    result['notes_live']=live_notes.status()
    if host:
        result.update({"review": session["review"], "terms": session["terms"], "room": ROOM, "previous_session": session.get("previous_session"), "voice_connected": voices.configured(), "neural_voice_ready": neural_voice.configured(), "natural_voice_ready": natural_voice.configured(), "translation_model": getattr(engine, "translation_model", None), "finishing_audio": capture_active and not session["live"], **{key: session[key] for key in ("source_language", "spanish_region", "spanish_voice", "korean_register", "glossary")}})
        result["audio_source"] = "livestream" if capture_active and livestream and not livestream.stopped else "board" if capture_active else None
    return result

def emit(kind, payload, host_only=False):
    data = json.dumps({"kind": kind, **payload}, ensure_ascii=False)
    for queue, is_host in list(subscribers):
        if host_only and not is_host:
            continue
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            # Close slow connections. Reconnect retrieves a consistent snapshot.
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(json.dumps({"kind": "resync"}))

def suggestions(text, terms):
    # Suggest only; never silently rewrite a speaker's words.
    words = re.findall(r"[A-Za-z]+", text)
    output = []
    def phonetic(value):
        letters = re.sub(r"[^a-z]", "", value.lower()).translate(str.maketrans({"c": "k", "q": "k", "z": "s", "v": "f"}))
        consonants = re.sub(r"[aeiouy]", "", letters)
        return re.sub(r"(.)\1+", r"\1", consonants)
    for term in terms:
        if re.search(r"\b" + re.escape(term) + r"\b", text, re.I):
            continue
        for n in (1, 2, 3):
            for index in range(len(words) - n + 1):
                span = " ".join(words[index:index+n])
                score = difflib.SequenceMatcher(None, span.lower().replace(" ", ""), term.lower().replace(" ", "")).ratio()
                sounds_alike = len(phonetic(span)) >= 3 and phonetic(span) == phonetic(term)
                if (score >= .79 or sounds_alike) and len(span) >= 5:
                    output.append({"heard": span, "suggestion": term})
    return list({(s["heard"], s["suggestion"]): s for s in output}.values())[:6]

async def infer(method, *args):
    return await asyncio.get_running_loop().run_in_executor(POOL, method, *args)

async def load_models():
    global engine, load_error
    try:
        engine = await infer(Engine)
        if neural_voice.configured():
            await asyncio.to_thread(neural_voice.load)
        if natural_voice.configured():
            await asyncio.to_thread(natural_voice.load)
        emit("status", {"ready": True})
    except Exception as exc:
        load_error = f"Models could not start: {type(exc).__name__}: {exc}"
        emit("status", {"ready": False, "error": load_error})

def synthesize(text, lang, path):
    with tempfile.TemporaryDirectory(prefix="gather-voice-") as directory:
        raw = Path(directory) / "voice.aiff"
        subprocess.run(["say", "-v", LANGUAGES[lang]["voice"], "-r", "190", "-o", str(raw), "--", text], check=True, capture_output=True, timeout=30)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-codec:a", "libmp3lame", "-b:a", "64k", str(path)], check=True, capture_output=True, timeout=30)

async def make_audio(seg, lang, generation, provider, spanish_voice="dora"):
    begin = time.perf_counter()
    speech=seg.get('speech_text',{}).get(lang,seg['translations'][lang])
    path = RUNTIME / f'{generation}-{seg["id"]}-{lang}.mp3'
    try:
        async with voice_slots:
            pace = await asyncio.to_thread(natural_voice.pacing, speech, lang, spanish_voice, seg["timing"].get("audio_seconds")) if provider == "supertonic" else None
            if pace:
                seg["timing"].setdefault("voice_pacing", {})[lang] = pace
            voice_identity = voices.identity(lang) if provider == "elevenlabs" else natural_voice.identity(lang, spanish_voice, pace["speed"]) if provider == "supertonic" else neural_voice.identity(lang, spanish_voice) if provider == "kokoro" else f"local:{LANGUAGES[lang]['voice']}:190"
            cache_key = hashlib.sha256((voice_identity + "\0" + speech).encode()).hexdigest()
            cached = RUNTIME / f"voice-{cache_key}.mp3"
            seg["timing"].setdefault("voice_cached", {})[lang] = cached.exists()
            if not cached.exists():
                generate_voice = voices.synthesize if provider == "elevenlabs" else natural_voice.synthesize if provider == "supertonic" else neural_voice.synthesize if provider == "kokoro" else synthesize
                args = (spanish_voice, pace["speed"]) if provider == "supertonic" else (spanish_voice,) if provider == "kokoro" else ()
                # Publish each spoken sentence as it becomes ready, while later speech is generated.
                chunks = [text.strip() for text in re.split(r'(?<=[.!?。])\s+', speech) if text.strip()]
                files = []
                seg.setdefault("audio_parts", {})[lang] = []
                seg.setdefault("audio_complete", {})[lang] = False
                for index, text in enumerate(chunks):
                    piece_key = hashlib.sha256((voice_identity + "\0" + text).encode()).hexdigest()
                    piece = RUNTIME / f"voice-{piece_key}.mp3"
                    if not piece.exists():
                        await asyncio.to_thread(generate_voice, text, lang, piece, *args)
                    if session["id"] != generation:
                        return
                    target = RUNTIME / f'{generation}-{seg["id"]}-{lang}-p{index}.mp3'
                    target.write_bytes(piece.read_bytes())
                    files.append(piece)
                    seg["audio_parts"][lang].append(f'/api/audio/{generation}/{seg["id"]}/{lang}/{index}?room={ROOM}')
                    seg["timing"].setdefault("first_audio_ms", {}).setdefault(lang, round((time.perf_counter() - begin) * 1000))
                    save_session()
                    emit("audio", {"session_id": generation, "segment": public_segment(seg)})
                if len(files) == 1:
                    cached.write_bytes(files[0].read_bytes())
                else:
                    await asyncio.to_thread(join_audio, files, cached)
            # Cached speech is shared without regenerating it for each listener.
            path.write_bytes(cached.read_bytes())
        if session["id"] != generation:
            return
        seg["timing"].setdefault("first_audio_ms", {}).setdefault(lang, round((time.perf_counter()-begin)*1000))
        seg["audio"][lang] = f'/api/audio/{generation}/{seg["id"]}/{lang}?room={ROOM}'
        seg.setdefault("audio_complete", {})[lang] = True
        seg["timing"].setdefault("tts_ms", {})[lang] = round((time.perf_counter() - begin) * 1000)
        save_session()
        emit("audio", {"session_id": generation, "segment": public_segment(seg)})
    except Exception as exc:
        if session["id"] == generation:
            if provider == "elevenlabs":
                emit("voice_error", {"message": "ElevenLabs could not generate this line. Check the connection and credits. Captions remain available."}, host_only=True)
            emit("audio_error", {"session_id": generation, "segment_id": seg["id"], "language": lang, "message": "Voice is unavailable for this line. Captions remain available."})

def join_audio(files, output):
    with tempfile.TemporaryDirectory(prefix="gather-join-") as directory:
        listing = Path(directory) / "pieces.txt"
        listing.write_text("\n".join("file '" + str(path).replace("'", "'\\''") + "'" for path in files))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(output)], check=True, capture_output=True, timeout=20)

async def translator_worker():
    while True:
        generation, seg = await jobs.get()
        try:
            if session["id"] != generation:
                continue
            seg["status"] = "translating"
            emit("segment", {"segment": dict(seg)}, host_only=True)
            previous = [s["source"] for s in session["segments"] if s["id"] < seg["id"] and s["status"] == "ready"][-2:]
            options = {key: session[key] for key in ("source_language", "spanish_region", "korean_register", "glossary")}
            options["context"] = " ".join(previous)
            options['vocabulary_settings']=copy.deepcopy(vocabulary.settings)
            found_terms=vocabulary_matches(seg['source'], options['vocabulary_settings'], options['context'])
            options['stream_clause']=bool(seg.get('stream_clause'))
            previous_segments = [s for s in session["segments"] if s["id"] < seg["id"] and s["status"] == "ready"]
            options["previous_translations"] = {lang: (previous_segments[-1]["source"], previous_segments[-1].get('base_translations',previous_segments[-1]['translations'])[lang]) for lang in session["languages"] if previous_segments and lang in previous_segments[-1]["translations"]}
            for lang in list(session["languages"]):
                if lang in seg["audio"]:
                    continue
                translations, elapsed = await infer(engine.translate, seg["source"], [lang], options)
                if session["id"] != generation:
                    break
                checks = review_flags(seg["source"], translations[lang], lang)
                seg.setdefault("checks", {})[lang] = checks
                if checks:
                    seg["status"] = "error"
                    seg["error"] = f"Check {LANGUAGES[lang]['name']} before retrying: " + " ".join(checks)
                    emit("segment", {"session_id": generation, "segment": dict(seg)}, host_only=True)
                    emit("audio_error", {"session_id": generation, "segment_id": seg["id"], "language": lang, "message": "This translation needs an operator check."})
                    continue
                seg.setdefault('base_translations',{})[lang]=translations[lang]
                seen={item['id'] for prior in session['segments'] if prior['id']<seg['id'] for item in prior.get('vocabulary_explanations',{}).get(lang,[])}
                translations[lang],annotations=vocabulary_explain(translations[lang],found_terms,lang,seen)
                seg.setdefault('vocabulary_explanations',{})[lang]=annotations
                seg.setdefault('speech_text',{})[lang]=vocabulary_speech_text(translations[lang],found_terms,lang)
                seg["translations"].update(translations)
                seg["status"] = "ready"
                seg["timing"].setdefault("translation_ms_by_language", {})[lang] = elapsed
                seg["timing"]["translation_ms"] = sum(seg["timing"]["translation_ms_by_language"].values())
                caption_time = round((time.time() - seg["created"]) * 1000)
                seg["timing"].setdefault("caption_ms_by_language", {})[lang] = caption_time
                seg["timing"].setdefault("caption_ms", caption_time)
                save_session()
                live_notes.capture(session)
                emit("segment", {"session_id": generation, "segment": public_segment(seg)})
                task = asyncio.create_task(make_audio(seg, lang, generation, session["voice_provider"], session["spanish_voice"]))
                audio_tasks.add(task)
                task.add_done_callback(audio_tasks.discard)
        except Exception as exc:
            seg["status"] = "error"
            seg["error"] = f"Translation failed: {type(exc).__name__}"
            if session["id"] == generation:
                emit("segment", {"session_id": generation, "segment": dict(seg)}, host_only=True)
        finally:
            if session["id"] == generation:
                save_session()
            jobs.task_done()

async def add_segment(text, asr_ms=0, audio_seconds=0, capture_end=None, force_review=False, boundary_kind=None):
    text = text.strip()
    if not text:
        return None
    if len(session["segments"]) >= 2000:
        raise HTTPException(409, "This session has reached its line limit. Download the transcript and start another session.")
    if not session["review"] and jobs.full() and capture_end is None:
        raise HTTPException(429, "Translation is catching up. Pause briefly and retry this line.")
    seg = {"id": len(session["segments"]) + 1, "raw": text, "source": text, "status": "review" if session["review"] else "queued", "translations": {}, "audio": {}, "created": time.time(), "timing": {"asr_ms": asr_ms, "audio_seconds": round(audio_seconds, 2)}, "suggestions": suggestions(text, session["terms"]), "scripture": re.findall(r"\b(?:[1-3]\s)?[A-Z][a-z]+\s\d{1,3}:\d{1,3}(?:[-–]\d{1,3})?", text)}
    seg["source_language"] = "mixed" if is_mixed(text) else source_language(text, session["source_language"])
    seg['stream_clause']=boundary_kind=='clause'
    if boundary_kind:
        seg['timing']['speech_boundary']=boundary_kind
    if force_review:
        seg["status"] = "review"
        seg["checks"] = {"source": ["The audio ended before this English thought was complete. Review it before publishing."]}
    if capture_end:
        seg["timing"]["capture_end_to_source_ms"] = round((time.time() - capture_end) * 1000)
    session["segments"].append(seg)
    save_session()
    emit("partial", {"text": ""}, host_only=True)
    emit("segment", {"segment": dict(seg)}, host_only=True)
    if not session["review"] and not force_review:
        # Save captured words before waiting for a slot; pressure must not drop speech.
        await jobs.put((session["id"], seg))
    return seg

@asynccontextmanager
async def lifespan(app):
    loader = asyncio.create_task(load_models())
    worker = asyncio.create_task(translator_worker())
    if live_notes.enabled:live_notes.task=asyncio.create_task(live_notes.run())
    yield
    if livestream:
        await livestream.close()
    save_session()
    await live_notes.close()
    loader.cancel()
    worker.cancel()
    for task in list(audio_tasks):
        task.cancel()
    POOL.shutdown(wait=False, cancel_futures=True)
    PREVIEW_POOL.shutdown(wait=False, cancel_futures=True)

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
lab = SermonLab(ROOT, RUNTIME, require_host, infer, lambda: engine,
                lambda: capture_active or source_connecting or not jobs.empty() or bool(audio_tasks)
                or notes.busy or live_notes.processing or vocabulary.preparing or any(s["status"] == "translating" for s in session["segments"]))
lab.register(app)
notes = NotesService(ROOT, RUNTIME, require_host, require_room, infer, lambda:engine,
                     lambda:session, lambda:lab.busy or live_notes.processing or vocabulary.preparing or capture_active or source_connecting
                     or not jobs.empty() or bool(audio_tasks)
                     or any(s['status']=='translating' for s in session['segments']))
notes.register(app)
vocabulary=Vocabulary(RUNTIME,require_host,require_room,infer,lambda:engine,
                      lambda:capture_active or source_connecting or lab.busy or notes.busy
                      or not jobs.empty() or bool(audio_tasks) or any(s['status']=='translating' for s in session['segments']),emit)
vocabulary.register(app)
live_notes=LiveNotes(ROOT,notes,lambda:session,
                     lambda:capture_active or source_connecting or not jobs.empty() or bool(audio_tasks) or any(s['status']=='translating' for s in session['segments']),emit)

class LiveNotesControl(BaseModel):
    enabled:bool

@app.post('/api/notes/live/control')
async def control_live_notes(config:LiveNotesControl,request:Request):
    require_host(request)
    if config.enabled and not live_notes.enabled:raise HTTPException(409,'Install the notes model through Gather Host setup first.')
    session['live_notes_enabled']=config.enabled
    if not config.enabled:
        await live_notes.close()
        for document in live_notes.documents.values():
            if document['source_id']==session['id']:
                document.update({'live':False,'live_status':'paused','updated':time.time()});notes.save(document)
                emit('notes_update',{'document_id':document['id'],'updated':document['updated'],'live':False})
    elif live_notes.task is None:live_notes.task=asyncio.create_task(live_notes.run())
    save_session();emit('notes_control',live_notes.status(),host_only=True)
    return live_notes.status()

@app.get('/vocabulary')
async def vocabulary_page(request:Request):
    if request.client.host not in {'127.0.0.1','::1'}:raise HTTPException(403)
    return FileResponse(ROOT/'public/vocabulary.html')

@app.middleware("http")
async def protect(request, call_next):
    # Prevent DNS-rebinding and cross-origin requests into the booth.
    host = request.headers.get("host", "").split(":")[0]
    if host not in {"localhost", "127.0.0.1", "[", LAN_IP}:
        return Response("Unknown host", status_code=403)
    origin = request.headers.get("origin")
    if origin and origin != f'{request.url.scheme}://{request.headers.get("host")}':
        return Response("Cross-origin request blocked", status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'"
    return response

def local_ip():
    try:
        # No packets sent: selects the interface that would serve the local network.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"

LAN_IP = local_ip()

@app.get("/")
async def host_page(request: Request):
    if request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Open the listening link from the booth instead.")
    return FileResponse(ROOT / "public/index.html")

@app.get("/listen")
async def listener_page(room: str):
    require_room(room)
    return FileResponse(ROOT / "public/listen.html")

@app.get("/overlay")
async def overlay(room: str):
    require_room(room)
    return FileResponse(ROOT / "public/overlay.html")

@app.get("/api/bootstrap")
async def bootstrap(request: Request):
    if request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403)
    port = request.url.port or 80
    return {"token": HOST_TOKEN, "listener_url": f"http://{LAN_IP}:{port}/listen?room={ROOM}", "local_listener_url": f"http://127.0.0.1:{port}/listen?room={ROOM}", "overlay_url": f"http://127.0.0.1:{port}/overlay?room={ROOM}&lang=es", "state": snapshot(True)}

@app.get("/api/health")
async def health():
    return {"ready": engine is not None, "error": load_error, "speech": "Parakeet v3 / local", "translation": getattr(engine, "translation_model", "Warming up"), "voice": session["voice_provider"], "voice_connected": voices.configured(), "neural_voice_ready": neural_voice.configured()}

class Start(BaseModel):
    title: str = Field(default="Citizens Church", min_length=1, max_length=80)
    languages: list[str] = Field(default=["es", "ko"], min_length=1, max_length=4)
    review: bool = True
    terms: list[str] = Field(default=[], max_length=50)
    voice_provider: str = "supertonic"
    source_language: str = "en"
    spanish_region: str = "latin"
    spanish_voice: str = "alex"
    korean_register: str = Field(default="spoken", pattern="^(spoken|formal)$")
    glossary: dict[str, str] = Field(default={}, max_length=40)
    draft_edits: dict[int, str] = Field(default={}, max_length=2000)
    live_notes_enabled:bool=True

@app.post("/api/start")
async def start(config: Start, request: Request):
    require_host(request)
    if notes.busy or vocabulary.preparing:
        raise HTTPException(409, 'Let notes or church vocabulary finish before starting audio.')
    if engine is None:
        raise HTTPException(503, load_error or "Models are warming up.")
    if session["live"]:
        raise HTTPException(409, "End the current session before starting another.")
    if capture_active:
        try:
            await asyncio.wait_for(capture_finished.wait(), timeout=15)
        except asyncio.TimeoutError:
            raise HTTPException(409, "The last audio line is still finishing. Try again shortly.")
    if any(lang not in LANGUAGES for lang in config.languages):
        raise HTTPException(422, "Choose Spanish, Korean, French, or Portuguese.")
    if any(len(term) > 80 for term in config.terms):
        raise HTTPException(422, "Each vocabulary term must be at most 80 characters.")
    if config.source_language not in {"en", "es", "auto"} or config.spanish_region not in {"latin", "mx"} or config.spanish_voice not in {"dora", "alex"}:
        raise HTTPException(422, "Choose a supported speech language, Spanish style, and voice.")
    if any(not key.strip() or not value.strip() or len(key) > 80 or len(value) > 120 for key, value in config.glossary.items()):
        raise HTTPException(422, "Use short, nonempty glossary entries: original phrase = Spanish equivalent.")
    if config.voice_provider not in {"local", "elevenlabs", "kokoro", "supertonic"}:
        raise HTTPException(422, "Choose a supported voice.")
    if config.voice_provider == "elevenlabs" and not voices.configured():
        raise HTTPException(409, "Connect ElevenLabs with a valid secret key first.")
    if config.voice_provider == "kokoro" and not neural_voice.configured():
        raise HTTPException(409, "The local neural voice model is not installed.")
    if config.voice_provider == "kokoro" and "ko" in config.languages:
        raise HTTPException(422, "Choose Supertonic or Mac voices for Korean speech.")
    if config.voice_provider == "supertonic" and not natural_voice.configured():
        raise HTTPException(409, "The multilingual neural voice model is not installed.")
    if any(not value.strip() or len(value) > 1200 for value in config.draft_edits.values()):
        raise HTTPException(422, "Each edited line must contain 1–1200 characters.")
    for segment in session["segments"]:
        if segment["status"] in {"review", "error"} and segment["id"] in config.draft_edits:
            segment["source"] = config.draft_edits[segment["id"]].strip()
    live_notes.capture(session)
    previous = archive_session()
    session.update({"id": secrets.token_hex(8), "live": True, "title": config.title.strip(), "languages": list(dict.fromkeys(config.languages)), "review": config.review, "terms": config.terms, "segments": [], "started": time.time(), "voice_provider": config.voice_provider, "previous_session": previous})
    session.update({key: getattr(config, key) for key in ("source_language", "spanish_region", "spanish_voice", "korean_register", "glossary")})
    session['live_notes_enabled']=config.live_notes_enabled
    if config.live_notes_enabled and live_notes.enabled and live_notes.task is None:live_notes.task=asyncio.create_task(live_notes.run())
    save_session()
    emit("reset", {"session": snapshot(False)})
    return snapshot(True)

def save_session():
    write_private_json(CURRENT_FILE, session)
    if session["segments"]:
        write_private_json(RUNTIME / f'{session["id"]}.json', session)

def write_private_json(path, data):
    temp = path.with_suffix(".tmp")
    with temp.open("w") as output:
        temp.chmod(0o600)
        json.dump(data, output, ensure_ascii=False, indent=2)
    temp.replace(path)

def archive_session():
    if not session["segments"]:
        return session.get("previous_session")
    archived = copy.deepcopy(session)
    archived["live"] = False
    archived["archived_at"] = time.time()
    for segment in archived["segments"]:
        if segment["status"] not in {"ready", "discarded"}:
            segment["previous_status"] = segment["status"]
            segment["status"] = "unpublished"
    write_private_json(RUNTIME / f'{session["id"]}.json', archived)
    return session["id"]

@app.post("/api/stop")
async def stop(request: Request):
    require_host(request)
    session["live"] = False
    if livestream:
        await livestream.close()
    emit("status", {"live": False, "finishing_audio": capture_active})
    if capture_active:
        try:
            await asyncio.wait_for(capture_finished.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
    save_session()
    live_notes.capture(session)
    return {"ok": True, "finishing_audio": capture_active}

class VoiceConnection(BaseModel):
    key: str = Field(min_length=10, max_length=512)

class VoiceStyle(BaseModel):
    style: str = Field(pattern="^(alex|dora)$")

@app.post("/api/voice/style")
async def change_voice_style(config: VoiceStyle, request: Request):
    require_host(request)
    if session["voice_provider"] not in {"supertonic", "kokoro"}:
        raise HTTPException(409, "Choose a local neural voice to use this voice style.")
    session["spanish_voice"] = config.style
    save_session()
    emit("voice_style", {"style": config.style}, host_only=True)
    return {"style": config.style, "applies_to": "future lines"}

@app.post("/api/voice/connect")
async def connect_voice(connection: VoiceConnection, request: Request):
    require_host(request)
    if session["live"] or capture_active:
        raise HTTPException(409, "End the session before changing the voice connection.")
    key = connection.key.strip()
    if not key.startswith("sk_"):
        raise HTTPException(422, "Paste the ElevenLabs secret key beginning with sk_, not its key ID.")
    try:
        # Verify permission to synthesize, not just permission to list voices.
        await asyncio.to_thread(voices.synthesize, "Bienvenidos a nuestra iglesia.", "es", RUNTIME / "voice-connection-preview.mp3", key)
    except Exception as exc:
        raise HTTPException(502, str(exc) if isinstance(exc, RuntimeError) else "ElevenLabs could not be reached. Check your connection and try again.")
    target = RUNTIME / "elevenlabs.env"
    with target.open("w") as output:
        target.chmod(0o600)
        output.write("ELEVENLABS_API_KEY=" + key + "\n")
    emit("voice_connection", {"connected": True}, host_only=True)
    return {"connected": True, "voice": "George", "model": voices.MODEL}

class TextLine(BaseModel):
    text: str = Field(min_length=1, max_length=1200)

@app.post("/api/text")
async def text_line(line: TextLine, request: Request):
    require_host(request)
    if notes.busy or vocabulary.preparing:
        raise HTTPException(409, 'Let notes or church vocabulary finish before adding new lines.')
    if not session["live"]:
        raise HTTPException(409, "Start a session first.")
    return await add_segment(line.text)

class Review(BaseModel):
    text: str = Field(min_length=1, max_length=1200)
    discard: bool = False

@app.post("/api/review/{segment_id}")
async def review(segment_id: int, edit: Review, request: Request):
    require_host(request)
    if (notes.busy or vocabulary.preparing) and not edit.discard:
        raise HTTPException(409, 'Let sermon notes finish before publishing more lines.')
    seg = next((s for s in session["segments"] if s["id"] == segment_id), None)
    if not seg or seg["status"] not in {"review", "error"}:
        raise HTTPException(409, "This line has already been published or is unavailable.")
    if seg["translations"] and (edit.discard or edit.text.strip() != seg["source"]):
        raise HTTPException(409, "Part of this line is already published. Retry the same source, or send a new correction line.")
    if not edit.discard and any(s["id"] < segment_id and s["status"] in {"review", "error"} for s in session["segments"]):
        raise HTTPException(409, "Publish or discard earlier lines first so listeners hear the right order.")
    if not edit.discard and jobs.full():
        raise HTTPException(429, "Translation is catching up. Retry in a moment.")
    seg["source"] = edit.text.strip()
    if not seg["source"]:
        raise HTTPException(422, "Enter the speaker's words, or discard this line.")
    seg["status"] = "discarded" if edit.discard else "queued"
    if not edit.discard:
        jobs.put_nowait((session["id"], seg))
    emit("segment", {"segment": dict(seg)}, host_only=True)
    return {"ok": True}

@app.get("/api/events")
async def events(request: Request, room: str = ""):
    is_host = bool(request.headers.get("x-host-token"))
    if is_host:
        require_host(request)
    else:
        require_room(room)
    queue = asyncio.Queue(maxsize=200)
    subscriber = (queue, is_host)
    async def stream():
        subscribers.add(subscriber)
        emit("audience", {"listeners": sum(not s[1] for s in subscribers)}, host_only=True)
        try:
            yield "data: " + json.dumps({"kind": "snapshot", "session": snapshot(is_host)}, ensure_ascii=False) + "\n\n"
            while not await request.is_disconnected():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {item}\n\n"
                    if json.loads(item).get("kind") == "resync":
                        return
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            subscribers.discard(subscriber)
            emit("audience", {"listeners": sum(not s[1] for s in subscribers)}, host_only=True)
    return StreamingResponse(stream(), media_type="text/event-stream")

@app.get("/api/qr")
async def qr(request: Request):
    require_host(request)
    url = f"http://{LAN_IP}:{request.url.port or 80}/listen?room={ROOM}"
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    out = io.BytesIO()
    image.save(out)
    return Response(out.getvalue(), media_type="image/svg+xml")

@app.get("/api/audio/{generation}/{segment_id}/{lang}")
async def audio(generation: str, segment_id: int, lang: str, room: str):
    require_room(room)
    if generation != session["id"] or lang not in LANGUAGES:
        raise HTTPException(404)
    path = RUNTIME / f"{generation}-{segment_id}-{lang}.mp3"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/mpeg")

@app.get("/api/audio/{generation}/{segment_id}/{lang}/{piece}")
async def audio_piece(generation: str, segment_id: int, lang: str, piece: int, room: str):
    require_room(room)
    if generation != session["id"] or lang not in LANGUAGES or not 0 <= piece <= 100:
        raise HTTPException(404)
    path = RUNTIME / f"{generation}-{segment_id}-{lang}-p{piece}.mp3"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/mpeg")

@app.get("/api/export")
async def export(request: Request):
    require_host(request)
    save_session()
    return Response(json.dumps(session, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="gather-transcript.json"'})

@app.get("/api/export/{session_id}")
async def export_archive(session_id: str, request: Request):
    require_host(request)
    if not re.fullmatch(r"[a-f0-9]{16}", session_id):
        raise HTTPException(404)
    path = RUNTIME / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/json", filename="gather-previous-transcript.json")

@app.websocket("/api/capture")
async def capture(ws: WebSocket):
    origin = ws.headers.get("origin")
    allowed_origin = f'http://{ws.headers.get("host", "")}'
    if ws.client.host not in {"127.0.0.1", "::1"} or (origin and origin != allowed_origin):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=5)
        if not secrets.compare_digest(str(hello.get("token", "")), HOST_TOKEN) or not session["live"] or engine is None or capture_active or source_connecting or lab.busy or notes.busy or vocabulary.preparing:
            await ws.close(code=1008, reason="The booth is unavailable or already capturing.")
            return
    except Exception:
        await ws.close(code=1008)
        return
    await capture_audio(ws)

async def capture_audio(ws):
    global capture_active
    phrases = PhraseBuffer()
    capture_active = True
    capture_finished.clear()
    generation = session["id"]
    last_preview = time.monotonic()
    last_audio_time = time.time()
    last_speech_time = last_audio_time
    epoch = 0
    preview_task = None
    recognition_queue = asyncio.Queue(maxsize=12)
    async def recognize_lines():
        carry = np.zeros(0, dtype=np.float32)
        thoughts = ThoughtBuffer()
        pending_seconds = 0.
        carry_end = last_speech_time
        while True:
            item = await recognition_queue.get()
            try:
                if item is None:
                    if len(carry) and session["id"] == generation:
                        text, elapsed = await infer(engine.transcribe, carry)
                        pending_seconds += len(carry)/16000
                        item = thoughts.push(text)
                        if item:
                            await add_segment(item[0], elapsed, pending_seconds, carry_end, force_review=item[1])
                            pending_seconds = 0.
                    item = thoughts.finish()
                    if item and session["id"] == generation:
                        await add_segment(item[0], audio_seconds=pending_seconds, capture_end=carry_end, force_review=item[1])
                    return
                samples, speech_end, final = item
                if session["id"] != generation:
                    continue
                samples = np.concatenate((carry, samples)) if len(carry) else samples
                text, elapsed, carry, boundary_kind = await infer(engine.transcribe_window, samples, final, True)
                carry_end = speech_end
                pending_seconds += (len(samples)-len(carry))/16000
                item = thoughts.push(text)
                if item:
                    await add_segment(item[0], elapsed, pending_seconds, speech_end, force_review=item[1],boundary_kind=boundary_kind)
                    pending_seconds = 0.
            except Exception as exc:
                emit("capture_error", {"message": f"A speech line could not be processed: {type(exc).__name__}. Check the audio input."}, host_only=True)
            finally:
                recognition_queue.task_done()
    recognition_worker = asyncio.create_task(recognize_lines())
    async def preview_line(samples, expected_epoch):
        try:
            text, _ = await infer(engine.transcribe, samples)
            if epoch != expected_epoch or session["id"] != generation or not session["live"]:
                return
            emit("partial", {"text": text}, host_only=True)
            if text and not session["review"]:
                options = {"source_language": session["source_language"], "fast_preview": True}
                translations, _ = await asyncio.get_running_loop().run_in_executor(PREVIEW_POOL, engine.translate_fast, text, list(session["languages"]), options)
                if epoch == expected_epoch and session["id"] == generation and session["live"]:
                    emit("preview", {"session_id": generation, "line_id": len(session["segments"]) + recognition_queue.qsize() + 1, "source": text, "translations": translations})
        except Exception:
            # A preview is optional. Final recognition still runs and reports failures.
            pass
    async def flush(final=True):
        nonlocal epoch
        epoch += 1
        item = phrases.flush(final)
        if item and session["id"] == generation:
            await recognition_queue.put((item[0], last_speech_time, item[2]))
    try:
        await ws.send_json({"ready": True})
        while session["live"] and session["id"] == generation:
            try:
                message = await asyncio.wait_for(ws.receive(), timeout=.5)
            except asyncio.TimeoutError:
                if time.time() - last_audio_time > 20:
                    break
                continue
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text"):
                if json.loads(message["text"]).get("type") == "stop":
                    break
                continue
            raw = message.get("bytes", b"")
            if not raw or len(raw) % 2 or len(raw) > 6400:
                raise ValueError("Invalid PCM chunk")
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
            last_audio_time = time.time()
            rms = float(np.sqrt(np.mean(samples ** 2)))
            peak = float(np.max(np.abs(samples)))
            emit("meter", {"rms": round(rms, 4), "peak": round(peak, 4)}, host_only=True)
            item = phrases.push(samples)
            speaking = phrases.probability >= .35
            if speaking:
                last_speech_time = last_audio_time
            if item:
                epoch += 1
                await recognition_queue.put((item[0], last_speech_time, item[2]))
            elif phrases.count >= 16000 and time.monotonic() - last_preview >= 1.0 and recognition_queue.empty() and jobs.empty() and (preview_task is None or preview_task.done()):
                preview_task = asyncio.create_task(preview_line(phrases.preview(), epoch))
                last_preview = time.monotonic()
        await flush()
    except (WebSocketDisconnect, asyncio.TimeoutError):
        await flush()
    except Exception as exc:
        emit("capture_error", {"message": str(exc) if isinstance(exc, SourceError) else f"Audio capture stopped: {type(exc).__name__}. You can restart the audio input."}, host_only=True)
    finally:
        epoch += 1
        if preview_task:
            await preview_task
        await recognition_queue.put(None)
        await recognition_worker
        capture_active = False
        capture_finished.set()
        emit("status", {"finishing_audio": False}, host_only=True)
        emit("capture_stopped", {}, host_only=True)
        try:
            await ws.close()
        except (RuntimeError,WebSocketDisconnect):
            pass

class StreamConnection(BaseModel):
    url: str = Field(min_length=10, max_length=2000)

@app.post("/api/source/livestream")
async def connect_livestream(config: StreamConnection, request: Request):
    global livestream, livestream_task, source_connecting, capture_active
    require_host(request)
    if not session["live"] or engine is None:
        raise HTTPException(409, "Start a session before connecting the livestream.")
    if capture_active or source_connecting or lab.busy or notes.busy or vocabulary.preparing:
        raise HTTPException(409, "Pause the current audio feed before connecting another.")
    source_connecting = True
    generation = session["id"]
    try:
        source = await asyncio.wait_for(Livestream.open(config.url.strip()), timeout=35)
        if not session["live"] or session["id"] != generation:
            await source.close()
            raise HTTPException(409, "The session ended while connecting the feed.")
        livestream = source
        livestream_task = asyncio.create_task(capture_audio(source))
        # Reserve capture immediately; the microphone cannot race the scheduled task.
        capture_active = True
        capture_finished.clear()
        emit("audio_source", {"source": "livestream", "title": source.title}, host_only=True)
        return {"connected": True, "title": source.title}
    except (SourceError, asyncio.TimeoutError) as exc:
        raise HTTPException(502, str(exc) if isinstance(exc, SourceError) else "The livestream took too long to connect. Try again or use the sound desk.")
    finally:
        source_connecting = False

@app.post("/api/source/pause")
async def pause_livestream(request: Request):
    require_host(request)
    if livestream:
        await livestream.close()
    if livestream_task:
        await livestream_task
    return {"ok": True}

app.mount("/static", StaticFiles(directory=ROOT / "public"), name="static")
