"""Regression checks in an isolated runtime; never touches the user's session or provider account."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

temporary = tempfile.TemporaryDirectory(prefix="gather-session-tests-")
os.environ["GATHER_RUNTIME_DIR"] = temporary.name
sys.path.insert(0, str(Path(__file__).parent.parent))
import server
from fastapi.testclient import TestClient

class SessionTests(unittest.TestCase):
    def setUp(self):
        server.RUNTIME = Path(temporary.name) / self._testMethodName
        server.RUNTIME.mkdir(exist_ok=True)
        server.CURRENT_FILE = server.RUNTIME / "current-session.json"
        server.engine = object()
        server.capture_active = False
        server.capture_finished = asyncio.Event()
        server.capture_finished.set()
        server.jobs = asyncio.Queue(maxsize=24)
        server.session.update({"id": "a" * 16, "live": False, "title": "Test church", "languages": ["es"], "review": True, "terms": [], "segments": [], "started": None, "voice_provider": "local", "previous_session": None})
        self.client = TestClient(server.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
        self.headers = {"x-host-token": server.HOST_TOKEN}

    def post(self, path, data):
        return self.client.post(path, json=data, headers=self.headers)

    def test_pending_draft_is_archived_without_publishing(self):
        self.assertEqual(self.post("/api/start", {}).status_code, 200)
        prior = server.session["id"]
        self.post("/api/text", {"text": "An unreviewed line."})
        self.post("/api/stop", {})
        response = self.post("/api/start", {"review": False, "draft_edits": {"1": "An edited, unreviewed line."}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["previous_session"], prior)
        self.assertEqual(response.json()["segments"], [])
        archived = self.client.get("/api/export/" + prior, headers=self.headers).json()
        self.assertEqual(archived["segments"][0]["source"], "An edited, unreviewed line.")
        self.assertEqual(archived["segments"][0]["raw"], "An unreviewed line.")
        self.assertEqual(archived["segments"][0]["status"], "unpublished")
        self.assertEqual(archived["segments"][0]["translations"], {})
        self.assertTrue(server.jobs.empty())
        self.assertEqual(self.client.get("/api/export/" + prior).status_code, 403)

    def test_failed_or_queued_lines_do_not_block_new_session(self):
        self.post("/api/start", {"review": False})
        prior = server.session["id"]
        self.post("/api/text", {"text": "Queued translation."})
        server.session["segments"][0]["status"] = "error"
        self.post("/api/stop", {})
        self.assertEqual(self.post("/api/start", {}).status_code, 200)
        archived = self.client.get("/api/export/" + prior, headers=self.headers).json()
        self.assertEqual(archived["segments"][0]["status"], "unpublished")

    def test_live_session_still_requires_end(self):
        self.post("/api/start", {})
        prior = server.session["id"]
        response = self.post("/api/start", {})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(server.session["id"], prior)

    def test_received_line_is_durable_before_translation_finishes(self):
        self.post("/api/start", {"review":False})
        self.post("/api/text", {"text":"God has not abandoned you."})
        saved=json.loads(server.CURRENT_FILE.read_text())
        self.assertEqual(saved["segments"][0]["source"],"God has not abandoned you.")
        self.assertEqual(saved["segments"][0]["status"],"queued")

    def test_captured_words_survive_a_full_translation_queue(self):
        async def scenario():
            server.session.update({"review":False})
            server.jobs=asyncio.Queue(maxsize=1)
            server.jobs.put_nowait((server.session["id"],{}))
            task=asyncio.create_task(server.add_segment("God has not abandoned you.",capture_end=1))
            await asyncio.sleep(0)
            saved=json.loads(server.CURRENT_FILE.read_text())
            self.assertEqual(saved["segments"][0]["source"],"God has not abandoned you.")
            self.assertFalse(task.done())
            server.jobs.get_nowait()
            self.assertEqual((await task)["source"],"God has not abandoned you.")
        asyncio.run(scenario())

    def test_start_waits_for_finishing_audio(self):
        async def scenario():
            server.capture_active = True
            server.capture_finished.clear()
            async def finish():
                await asyncio.sleep(.03)
                server.capture_active = False
                server.capture_finished.set()
            task = asyncio.create_task(finish())
            request = type("Request", (), {"client": type("Client", (), {"host": "127.0.0.1"})(), "headers": self.headers})()
            result = await server.start(server.Start(), request)
            await task
            self.assertTrue(result["live"])
        asyncio.run(scenario())

    def test_voice_key_id_is_rejected(self):
        result = self.post("/api/voice/connect", {"key": "not-a-secret-key-id"})
        self.assertEqual(result.status_code, 422)
        self.assertFalse((server.RUNTIME / "elevenlabs.env").exists())

    def test_live_voice_change_keeps_session_and_transcript_and_persists(self):
        server.session.update({"live": True, "voice_provider": "supertonic", "languages": ["es", "ko"], "spanish_voice": "dora"})
        prior_id = server.session["id"]
        segment = {"id": 1, "source": "Original speech", "translations": {"es": "Original", "ko": "원문"}, "status": "ready"}
        server.session["segments"].append(segment)
        result = self.post("/api/voice/style", {"style": "alex"})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(server.session["live"])
        self.assertEqual(server.session["id"], prior_id)
        self.assertEqual(server.session["segments"], [segment])
        self.assertEqual(json.loads(server.CURRENT_FILE.read_text())["spanish_voice"], "alex")
        self.assertEqual(self.client.post("/api/voice/style", json={"style": "dora"}).status_code, 403)
        self.assertEqual(self.post("/api/voice/style", {"style": "unknown"}).status_code, 422)

    def test_verified_secret_is_saved_privately_and_never_returned(self):
        secret = "sk_synthetic_test_only_not_a_real_key"
        def synthesize(text, lang, path, key=None):
            self.assertEqual(key, secret)
            path.write_bytes(b"synthetic audio")
        with patch.object(server.voices, "synthesize", synthesize):
            result = self.post("/api/voice/connect", {"key": secret})
        self.assertEqual(result.status_code, 200)
        self.assertNotIn(secret, result.text)
        target = server.RUNTIME / "elevenlabs.env"
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertIn(secret, target.read_text())

    def test_a_partly_published_source_cannot_be_rewritten(self):
        self.post("/api/start", {})
        self.post("/api/text", {"text": "God has not abandoned you."})
        segment = server.session["segments"][0]
        segment["status"] = "error"
        segment["translations"] = {"es": "Dios no te ha abandonado."}
        response = self.post("/api/review/1", {"text": "God has abandoned you."})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(segment["source"], "God has not abandoned you.")

    def test_livestream_requires_a_live_session_and_host(self):
        self.assertEqual(self.post("/api/source/livestream", {"url": "https://example.com/audio.m3u8"}).status_code, 409)
        self.assertEqual(self.client.post("/api/source/livestream", json={"url": "https://example.com/audio.m3u8"}).status_code, 403)

if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        temporary.cleanup()
