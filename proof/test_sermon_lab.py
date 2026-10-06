import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sermon_lab import SermonLab, caption_excerpt
from sermon_metrics import alignment, character_similarity, review_flags
from speech_gate import PhraseBuffer, ThoughtBuffer
from engine import Engine

class MetricsTests(unittest.TestCase):
    def fake_engine(self, result):
        engine = Engine.__new__(Engine)
        engine.mx = SimpleNamespace(array=np.asarray)
        engine.get_logmel = lambda audio, config: audio
        engine.asr = SimpleNamespace(preprocessor_config=None, generate=lambda audio: [result])
        return engine

    def test_continuous_clause_is_not_cut_at_six_seconds(self):
        engine = self.fake_engine(SimpleNamespace(text="What God says", sentences=[], tokens=[]))
        samples = np.ones(96000, np.float32)
        text, _, carry = engine.transcribe_window(samples, final=False)
        self.assertEqual(text, "")
        self.assertEqual(len(carry), len(samples))
        text, _, carry = engine.transcribe_window(samples, final=True)
        self.assertEqual(text, "What God says")
        self.assertEqual(len(carry), 0)

    def test_complete_sentence_retains_next_word_audio(self):
        tokens = [SimpleNamespace(text="God", start=1, end=2), SimpleNamespace(text=" is good.", start=2, end=4), SimpleNamespace(text=" Next", start=5, end=5.8)]
        engine = self.fake_engine(SimpleNamespace(text="God is good. Next", sentences=[SimpleNamespace(text="God is good.", end=4)], tokens=tokens))
        text, _, carry = engine.transcribe_window(np.zeros(96000, np.float32), final=False)
        self.assertEqual(text, "God is good.")
        self.assertEqual(len(carry), 24000)

    def test_sentence_can_commit_at_three_seconds_without_losing_tail(self):
        tokens = [SimpleNamespace(text="God",start=.2,end=.5),SimpleNamespace(text=" is good.",start=.6,end=1.8),SimpleNamespace(text=" He",start=2.2,end=2.5)]
        engine = self.fake_engine(SimpleNamespace(text="God is good. He",sentences=[SimpleNamespace(text="God is good.",end=1.8)],tokens=tokens))
        text,_,carry = engine.transcribe_window(np.zeros(48000,np.float32),False)
        self.assertEqual(text,"God is good.")
        self.assertEqual(len(carry),16000)

    def test_frequent_windows_keep_all_continuous_audio(self):
        phrases = PhraseBuffer(window_seconds=3)
        phrases.gate.probability = lambda samples: 1.0
        samples = np.arange(1600,dtype=np.float32)
        outputs = [r for _ in range(90) if (r:=phrases.push(samples))]
        self.assertEqual(len(outputs),3)
        self.assertEqual(sum(len(r[0]) for r in outputs),144000)
        self.assertTrue(all(not r[2] for r in outputs))

    def test_known_word_errors_and_empty_baseline(self):
        self.assertEqual(alignment("God has not forgotten you", "God has forgotten you")["deletions"], 1)
        self.assertEqual(alignment("God has not forgotten you", "")["wer"], 1)
        self.assertEqual(alignment("God has not forgotten you", "God has NOT forgotten you!")["wer"], 0)
        self.assertIsNone(alignment("", "test")["wer"])

    def test_similarity_has_unrelated_baseline(self):
        for text, wrong in (("Dios no te ha olvidado.", "Hoy compramos tres bicicletas."), ("하나님은 여러분을 잊지 않으셨어요.", "저는 자전거를 타고 학교에 가요.")):
            self.assertEqual(character_similarity(text, text), 100)
            self.assertLess(character_similarity(text, wrong), 50)
            self.assertEqual(character_similarity(text, ""), 0)

    def test_negation_loss_flagged_in_both_languages(self):
        self.assertTrue(review_flags("God has not forgotten you", "Dios te ha olvidado", "es"))
        self.assertTrue(review_flags("God has not forgotten you", "하나님은 여러분을 잊으셨어요", "ko"))
        self.assertFalse(review_flags("God has not forgotten you", "Dios no te ha olvidado", "es"))
        self.assertFalse(review_flags("God has not forgotten you", "하나님은 여러분을 잊지 않으셨어요", "ko"))

    def test_added_paragraph_is_flagged(self):
        flags = review_flags("You might make bad decisions.", "여러분은 잘못된 선택을 할 수도 있어요. " * 15, "ko")
        self.assertTrue(any("expanded" in flag for flag in flags))

    def test_unfinished_thought_waits_for_next_words(self):
        thoughts = ThoughtBuffer()
        self.assertIsNone(thoughts.push("You are never"))
        self.assertEqual(thoughts.push("too far gone."), ("You are never too far gone.", False))
        self.assertIsNone(thoughts.finish())
        self.assertIsNone(thoughts.push("And you have to"))
        self.assertEqual(thoughts.finish(), ("And you have to", True))

    def test_word_timestamp_clip_boundaries(self):
        data = {"events": [{"tStartMs": 1000, "segs": [{"utf8": "before ", "tOffsetMs": 0}, {"utf8": "inside ", "tOffsetMs": 1200}, {"utf8": "after", "tOffsetMs": 2500}]}]}
        self.assertEqual(caption_excerpt(data, 2, 1), "inside")

    def test_loud_non_speech_not_amplitude_trigger(self):
        phrases = PhraseBuffer()
        rng = np.random.default_rng(9)
        outputs = []
        for _ in range(30):
            result = phrases.push(rng.normal(0, .1, 1600).astype(np.float32))
            if result:
                outputs.append(result)
        self.assertFalse(outputs)
        self.assertIsNone(phrases.flush())

class LabAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = FastAPI()
        def auth(request):
            if request.headers.get("x-host-token") != "test-host":
                raise HTTPException(403)
        self.lab = SermonLab(Path(__file__).parents[1], Path(self.temp.name), auth, None, lambda: object(), lambda: True)
        self.lab.register(self.app)
        self.client = TestClient(self.app)
        self.headers = {"x-host-token": "test-host"}

    def tearDown(self):
        self.temp.cleanup()

    def test_reports_and_audio_require_booth_auth(self):
        self.assertEqual(self.client.get("/api/lab").status_code, 403)
        self.assertEqual(self.client.get("/api/lab/"+"a"*32+"/audio/source").status_code, 403)
        self.assertEqual(self.client.get("/api/lab/not-a-job", headers=self.headers).status_code, 404)

    def test_active_live_audio_prevents_test(self):
        r = self.client.post("/api/lab", headers=self.headers, json={"url": "https://example.com/audio"})
        self.assertEqual(r.status_code, 409)
        self.assertFalse(self.lab.busy)

    def test_bilingual_review_is_persisted_without_certification(self):
        job = "b"*32
        directory = self.lab.directory / job
        directory.mkdir()
        self.lab.save(directory / "report.json", {"languages": {"es": {}, "ko": {}}})
        r = self.client.post(f"/api/lab/{job}/review", headers=self.headers, json={"language": "ko", "meaning": 4, "naturalness": 3, "voice": 4, "reviewer": "Test reviewer", "notes": "Meaning retained; slightly stiff."})
        self.assertEqual(r.status_code, 200)
        report = json.loads((directory / "report.json").read_text())
        self.assertEqual(report["languages"]["ko"]["human_review"]["naturalness"], 3)
        self.assertNotIn("accuracy", report)

if __name__ == "__main__":
    unittest.main()
