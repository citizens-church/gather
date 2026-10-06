"""Server-side ElevenLabs connection; credentials are never sent to listeners."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).parent
MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # George, a stock narration voice.

def api_key():
    value = os.getenv("ELEVENLABS_API_KEY", "")
    if not value:
        saved = ROOT / ".runtime/elevenlabs.env"
        path = saved if saved.exists() else Path(os.getenv("ELEVENLABS_ENV_FILE", str(Path.home() / "AI/sound-workbench/.env")))
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip().startswith("ELEVENLABS_API_KEY="):
                    value = line.split("=", 1)[1].strip().strip("\"'")
                    break
    return value

def configured():
    return api_key().startswith("sk_")

def voice_id(lang):
    return os.getenv(f"ELEVENLABS_VOICE_{lang.upper()}") or os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_VOICE)

def identity(lang):
    return f"elevenlabs:{MODEL}:{voice_id(lang)}:{lang}:1.0"

def request(path, body=None, key=None):
    key = key or api_key()
    if not key:
        raise RuntimeError("ElevenLabs is not configured.")
    headers = {"xi-api-key": key, "Accept": "audio/mpeg" if body else "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request("https://api.elevenlabs.io" + path, headers=headers, data=None if body is None else json.dumps(body).encode())
    try:
        return urllib.request.urlopen(req, timeout=25)
    except urllib.error.HTTPError as exc:
        # Retain actionable error codes without reflecting provider content or keys.
        try:
            code = json.loads(exc.read()).get("detail", {}).get("status", "request_failed")
        except Exception:
            code = "request_failed"
        raise RuntimeError(f"ElevenLabs HTTP {exc.code}: {code}") from None

def synthesize(text, lang, path, key=None):
    body = {"text": text, "model_id": MODEL, "language_code": lang, "voice_settings": {"stability": .55, "similarity_boost": .75, "speed": 1.0}}
    temp = path.with_suffix(".partial")
    try:
        with request(f"/v1/text-to-speech/{voice_id(lang)}/stream?output_format=mp3_44100_128", body, key=key) as response, temp.open("wb") as output:
            while chunk := response.read(16384):
                output.write(chunk)
        if temp.stat().st_size < 1000:
            raise RuntimeError("ElevenLabs returned an empty voice file.")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
