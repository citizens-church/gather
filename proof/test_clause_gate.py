import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clause_gate import clause_cut
from engine import Engine
import numpy as np

def tokens(text):
    return [SimpleNamespace(text=(' ' if i else '')+word,start=i*.4,end=(i+1)*.4)for i,word in enumerate(text.split())]

class ClauseTests(unittest.TestCase):
    def test_runon_commits_before_sentence_end(self):
        words=tokens('God loves you and he has not forgotten you even when life gets hard')
        self.assertAlmostEqual(clause_cut(words,6.5),3.6)

    def test_does_not_split_objects_negation_or_conditions(self):
        for text in ('God has not','We need to','God will never','What God says','God will forgive you if you','God has promised that he will'):
            self.assertEqual(clause_cut(tokens(text),6),0,text)

    def test_object_and_preposition_stay_together(self):
        words=tokens('God has spoken to you and he wants you to hear')
        self.assertAlmostEqual(clause_cut(words,5.5),2.0)

    def test_relative_noun_phrase_is_not_a_complete_predicate(self):
        self.assertEqual(clause_cut(tokens('The God who loves you and who knows you'),5),0)

    def test_imperative_can_stream_with_its_object_and_reference(self):
        self.assertAlmostEqual(clause_cut(tokens('Open your Bible to Habakkuk and listen to his words'),5),2.0)

    def test_retained_audio_includes_next_clause_without_rewriting_words(self):
        words=tokens('God loves you and he has not forgotten you even when life gets hard')
        engine=Engine.__new__(Engine)
        engine.mx=SimpleNamespace(array=np.asarray)
        engine.get_logmel=lambda audio,config:audio
        result=SimpleNamespace(text=''.join(t.text for t in words),tokens=words,sentences=[])
        engine.asr=SimpleNamespace(preprocessor_config=None,generate=lambda audio:[result])
        text,_,carry,kind=engine.transcribe_window(np.zeros(104000,np.float32),False,True)
        self.assertEqual(text,'God loves you and he has not forgotten you')
        self.assertEqual(kind,'clause')
        self.assertEqual(len(carry),46400)

if __name__=='__main__':unittest.main()
