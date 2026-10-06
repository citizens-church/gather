import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from live_notes import LiveNotes,document_id,published_sources,apply_patch
from sermon_notes import markdown

def fixture_patch(line='s1'):
    return {'point':0,'title':{'en':'God has not forgotten you','es':'Dios no te ha olvidado','ko':'하나님은 여러분을 잊지 않으셨어요'},'text':{'en':'God walks with you.','es':'Dios camina contigo.','ko':'하나님은 여러분과 함께 걸으세요.'},'source_ids':[line],'generation_ms':10}

class LiveNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.events=[]
        path=self.path
        class Notes:
            directory=path;runtime=path
            def save(_,document):(path/(document['id']+'.json')).write_text(json.dumps(document))
        self.current={'id':'a'*16,'live':True,'live_notes_enabled':True,'title':'Live sermon','started':1,'segments':[{'id':1,'source':'God has not forgotten you. God walks with you.','status':'ready','translations':{'es':'Dios no te ha olvidado.'},'base_translations':{'es':'Dios no te ha olvidado.'}},{'id':2,'source':'Unpublished draft.','status':'review','translations':{}}]}
        self.service=LiveNotes(self.path,Notes(),lambda:self.current,lambda:False,lambda *a:self.events.append(a));self.service.enabled=True
    def tearDown(self):self.temp.cleanup()
    def test_published_sources_exclude_private_drafts(self):
        self.assertEqual([s['id']for s in published_sources(self.current)],['s1'])
        self.current['segments'][0]['translations']['es']+=' Peopling — church explanation.'
        self.assertNotIn('church explanation',published_sources(self.current)[0]['translations']['es'])
    def test_live_document_is_shared_searchable_and_persisted(self):
        doc=self.service.capture(self.current)
        self.assertTrue(doc['shared']);self.assertTrue(doc['live']);self.assertEqual(doc['source_id'],self.current['id'])
        self.assertEqual(doc['sources'][0]['id'],'s1');self.assertEqual(doc['points'],[])
        self.assertTrue((self.path/(document_id(self.current['id'])+'.json')).exists())
    def test_patch_groups_support_and_keeps_source_quotes(self):
        doc=self.service.capture(self.current);sources=published_sources(self.current)
        apply_patch(doc,fixture_patch(),sources);apply_patch(doc,fixture_patch(),sources)
        self.assertEqual(len(doc['points']),1);self.assertEqual(len(doc['points'][0]['subpoints']),2)
        self.assertEqual(doc['points'][0]['quote'],sources[0]['text'])
        for lang in ('en','es','ko'):
            exported=markdown(doc,lang);self.assertIn('Live sermon',exported);self.assertIn('God has not forgotten you.',exported)
    def test_unknown_source_and_added_number_are_rejected_atomically(self):
        doc=self.service.capture(self.current);before=copy.deepcopy(doc)
        with self.assertRaises(ValueError):apply_patch(doc,fixture_patch('private'),doc['sources'])
        bad=fixture_patch();bad['text']['ko']='요한복음 3장 17절을 읽으세요.'
        with self.assertRaises(ValueError):apply_patch(doc,bad,doc['sources'])
        self.assertEqual(doc,before)
    def test_saved_notes_resume_and_finalize_after_service(self):
        async def scenario():
            async def fake_infer(body):return fixture_patch(body['sources'][0]['id'])
            self.service.infer=fake_infer
            doc=self.service.capture(self.current)
            task=asyncio.create_task(self.service.run())
            await asyncio.sleep(.04)
            self.assertEqual(doc['processed_line'],1);self.assertTrue(doc['live']);self.assertTrue(doc['points'])
            self.current['live']=False
            await asyncio.sleep(2.1)
            self.assertEqual(doc['live_status'],'complete');self.assertFalse(doc['live'])
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
            restored=LiveNotes(self.path,self.service.notes,lambda:self.current,lambda:False,lambda *_:None);restored.enabled=True
            self.assertEqual(restored.capture(self.current)['processed_line'],1)
        asyncio.run(scenario())
    def test_disabled_live_notes_does_not_read_or_publish(self):
        self.current['live_notes_enabled']=False
        self.assertIsNone(self.service.capture(self.current));self.assertEqual(self.events,[])

if __name__=='__main__':unittest.main()
