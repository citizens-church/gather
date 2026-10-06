import asyncio
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vocabulary import initial, matches, protect, restore, explain, speech_text, Vocabulary

class VocabularyTests(unittest.TestCase):
    def test_name_is_preserved_without_changing_source(self):
        source='Our series is called Peopling. Peopling takes practice.'
        masked,bindings=protect(source,initial())
        self.assertNotIn('Peopling',masked)
        self.assertEqual(restore(masked,bindings),source)
        self.assertEqual(source,'Our series is called Peopling. Peopling takes practice.')

    def test_missing_or_duplicate_marker_stops_publication(self):
        masked,bindings=protect('We are learning Peopling.',initial())
        with self.assertRaises(ValueError):restore('We are learning relationships.',bindings)
        with self.assertRaises(ValueError):restore(masked+' '+bindings[0]['marker'],bindings)

    def test_series_context_avoids_medical_detox(self):
        settings=initial()
        self.assertEqual(matches('Medical detox can be difficult.',settings),[])
        self.assertEqual(len(matches('Our series was called DETOX.',settings)),1)
        self.assertEqual(len(matches('DETOX taught us a lot.',settings,'Our previous series was DETOX.')),1)
        self.assertEqual(matches('Peoplingish and people are different words.',settings),[])

    def test_explicit_alias_and_longest_match(self):
        settings=initial();settings['entries'][0]['aliases']=['peepling']
        self.assertEqual(matches('We are learning peepling.',settings)[0]['entry']['term'],'Peopling')
        self.assertEqual(matches('People matter.',settings),[])
        settings['entries'].append({'term':'Peopling together','kind':'slang','meaning':'','aliases':[],'explanations':{}})
        self.assertEqual(matches('Peopling together',settings)[0]['entry']['term'],'Peopling together')

    def test_explains_once_per_language_and_service(self):
        found=matches('Peopling and Peopling.',initial())
        spoken,annotations=explain('Peopling.',found,'es',set())
        self.assertEqual(len(annotations),1)
        self.assertIn('relacionarnos',spoken)
        repeated,next_annotations=explain('Peopling.',found,'es',{annotations[0]['id']})
        self.assertEqual(repeated,'Peopling.');self.assertEqual(next_annotations,[])
        self.assertEqual(len(explain('Peopling.',found,'ko',set())[1]),1)
        self.assertEqual(len(explain('Peopling.',found,'es',set())[1]),1)

    def test_no_guessed_definition_for_detox(self):
        found=matches('Our series was called DETOX.',initial())
        self.assertEqual(explain('DETOX.',found,'es',set()),('DETOX.',[]))

    def test_korean_name_particles_and_spoken_reading(self):
        _,bindings=protect('Peopling is our series.',initial())
        caption=restore('GATHERVOCABAA는 관계에 관한 거예요.',bindings,'ko')
        self.assertEqual(caption,'Peopling은 관계에 관한 거예요.')
        self.assertEqual(speech_text(caption,bindings,'ko'),'피플링은 관계에 관한 거예요.')

class VocabularyApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.busy=False;self.calls=[]
        def host(request):
            if request.headers.get('x-host-token')!='test':raise HTTPException(403)
        def room(value):
            if value!='room':raise HTTPException(404)
        class Engine:
            def translate(_,text,languages,options):return {l:l+': '+text for l in languages},1
        async def infer(method,*args):self.calls.append(args);return method(*args)
        self.v=Vocabulary(Path(self.tmp.name),host,room,infer,lambda:Engine(),lambda:self.busy,lambda *args:None)
        self.app=FastAPI();self.v.register(self.app);self.client=TestClient(self.app);self.headers={'x-host-token':'test'}
    def tearDown(self):self.tmp.cleanup()
    def test_read_access_and_listener_privacy(self):
        self.assertEqual(self.client.get('/api/vocabulary').status_code,403)
        self.assertEqual(self.client.get('/api/vocabulary?room=wrong').status_code,404)
        public=self.client.get('/api/vocabulary?room=room').json()
        self.assertNotIn('aliases',public['entries'][0]);self.assertNotIn('explain',public['entries'][0])
        self.assertEqual(self.v.path.stat().st_mode&0o777,0o600)
    def test_changed_meaning_requires_paused_audio_and_rebuilds_explanations(self):
        data=initial();data['entries'][0]['meaning']='Treat people with respect.'
        self.busy=True
        self.assertEqual(self.client.post('/api/vocabulary',json=data,headers=self.headers).status_code,409)
        self.assertEqual(self.calls,[])
        self.busy=False;response=self.client.post('/api/vocabulary',json=data,headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['entries'][0]['explanations']['ko'],'ko: Treat people with respect.')
        self.assertFalse(self.v.preparing)
    def test_locale_edit_reuses_known_meaning_without_inference(self):
        data=initial();data['entries'][0]['explanations']['es']='Aprender a tratar bien a la gente.'
        self.assertEqual(self.client.post('/api/vocabulary',json=data,headers=self.headers).status_code,200)
        self.assertEqual(self.calls,[])
    def test_clear_meaning_clears_existing_explanations(self):
        data=initial();data['entries'][0]['meaning']=''
        response=self.client.post('/api/vocabulary',json=data,headers=self.headers)
        self.assertEqual(response.json()['entries'][0]['explanations'],{})
    def test_preparing_blocks_second_save(self):
        self.v.preparing=True
        self.assertEqual(self.client.post('/api/vocabulary',json=initial(),headers=self.headers).status_code,409)
    def test_reserved_marker_name_is_rejected(self):
        data=initial();data['entries'][1]['term']='GATHERVOCABAA'
        self.assertEqual(self.client.post('/api/vocabulary',json=data,headers=self.headers).status_code,422)

if __name__=='__main__':unittest.main()
