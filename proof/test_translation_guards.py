"""Language routing and concrete preservation checks, without GPU or user data."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spanish import source_language, is_mixed, translation_notes, validate_translation
from natural_voice import spoken_text

class TranslationGuards(unittest.TestCase):
    def test_spanish_slang_is_spanish(self):
        text = "No manches, estaba bien agüitado, pero Dios no me dejó solo."
        self.assertEqual(source_language(text), "es")
        self.assertFalse(is_mixed(text))

    def test_spanish_dominant_spanglish_is_not_passthrough(self):
        self.assertTrue(is_mixed("Dios no te ha abandonado, so don't give up."))
        self.assertTrue(is_mixed("We're in this juntos."))

    def test_idiom_does_not_force_literal_grace(self):
        notes, glossary = translation_notes("Give yourself some grace.")
        self.assertTrue(notes)
        self.assertNotIn("grace", glossary)

    def test_missing_reference_is_flagged_but_korean_format_is_valid(self):
        self.assertTrue(validate_translation("Open John 3:16.", "요한복음 3장 15절을 펴십시오."))
        self.assertFalse(validate_translation("Open John 3:16.", "요한복음 3장 16절을 펴십시오."))

    def test_scripture_speech_does_not_change_clocks(self):
        self.assertEqual(spoken_text("요한복음 3:16. 7:30에 만나요.", "ko"), "요한복음 3장 16절. 7:30에 만나요.")
        self.assertEqual(spoken_text("Juan 3:16. Nos vemos a las 7:30.", "es"), "Juan, capítulo 3, versículo 16. Nos vemos a las 7:30.")

if __name__ == "__main__":
    unittest.main(verbosity=2)
