import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from notes_service import NotesService
from sermon_notes import validate_outline,markdown,prepare_sources,scripture_references

class NotesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source={'id':'s1','line':1,'text':'God has not forgotten you. Read John 3:16.'}
        self.point={'title':'God has not forgotten you','source_ids':['s1'],'quote':'God has not forgotten you.','subpoints':[{'text':'Read John 3:16.','source_ids':['s1'],'quote':'Read John 3:16.'}]}
        def host(request):
            if request.headers.get('x-host-token')!='host':raise HTTPException(403)
        def room(value):
            if value!='room':raise HTTPException(404)
        self.service=NotesService(self.root,self.root,host,room,None,lambda:None,lambda:{},lambda:False)
        app=FastAPI();self.service.register(app);self.client=TestClient(app)
        self.doc={'id':'a'*32,'title':'God remembers','scope':'Excerpt only','quality':'Draft','created':1,'shared':False,'points':[self.point],'sources':[self.source],'translations':{}}
        self.service.save(self.doc)
    def tearDown(self):self.temp.cleanup()
    def test_source_quotes_and_ids_must_exist(self):
        self.assertTrue(validate_outline({'points':[self.point]},[self.source]))
        bad=json.loads(json.dumps(self.point));bad['quote']='God will make everyone wealthy.'
        with self.assertRaises(ValueError):validate_outline({'points':[bad]},[self.source])
        bad['source_ids']=['../../secret']
        with self.assertRaises(ValueError):validate_outline({'points':[bad]},[self.source])
    def test_paraphrase_cannot_add_numbers(self):
        bad=json.loads(json.dumps(self.point));bad['subpoints'][0]['text']='Read John 3:17.'
        with self.assertRaises(ValueError):validate_outline({'points':[bad]},[self.source])
    def test_drafts_stay_private(self):
        id=self.doc['id']
        self.assertEqual(self.client.get('/api/notes').status_code,403)
        self.assertEqual(self.client.get('/api/notes?room=room').json()['documents'],[])
        self.assertEqual(self.client.get('/api/notes/'+id+'?room=room').status_code,404)
        self.assertEqual(self.client.get('/api/notes/'+id+'/export?room=room').status_code,404)
        self.assertEqual(self.client.post('/api/notes/'+id+'/share').status_code,403)
    def test_only_host_can_share_and_valid_room_can_read(self):
        id=self.doc['id']
        self.assertEqual(self.client.post('/api/notes/'+id+'/share',headers={'x-host-token':'host'}).status_code,200)
        self.assertEqual(len(self.client.get('/api/notes?room=room').json()['documents']),1)
        self.assertEqual(self.client.get('/api/notes/'+id+'?room=wrong').status_code,404)
        self.assertEqual(self.client.get('/api/notes/'+id+'/export?room=room').status_code,200)
    def test_source_has_no_unpublished_lines(self):
        current={'id':'b'*16,'title':'Service','segments':[{'id':1,'source':'Private draft','translations':{}},{'id':2,'source':self.source['text'],'translations':{'es':'Dios no te ha olvidado.'}}]}
        self.service.session=lambda:current
        _,_,sources=self.service.sources('session',current['id'])
        self.assertEqual([s['id']for s in sources],['s2'])
    def test_generation_rejects_busy_audio(self):
        self.service.unavailable=lambda:True
        response=self.client.post('/api/notes',headers={'x-host-token':'host'},json={'kind':'session','source_id':'b'*16})
        self.assertEqual(response.status_code,409)
    def test_markdown_retains_source_and_scope(self):
        text=markdown(self.doc)
        self.assertIn('Excerpt only',text);self.assertIn('[s1]',text);self.assertIn(self.source['text'],text)
    def test_short_continuations_keep_citation_context(self):
        sources=prepare_sources([{'id':'s1','line':1,'text':'What God asks you to do is'},{'id':'s2','line':2,'text':'rest.'}])
        self.assertEqual(sources[0]['text'],'What God asks you to do is rest.')
        self.assertEqual(sources[0]['lines'],[1,2])
    def test_scripture_links_only_reference_what_was_said(self):
        refs=scripture_references([{'id':'s1','text':'Read First John 4:10 and Habakkuk chapter 2.'},{'id':'s2','text':'God remembers you.'}])
        self.assertEqual([r['text']for r in refs],['First John 4:10','Habakkuk chapter 2'])
    def test_request_to_rest_does_not_make_grace_conditional(self):
        source={'id':'s1','text':'God’s grace keeps washing over you. God asks you to rest.'}
        point={'title':'God’s grace requires rest','source_ids':['s1'],'quote':'God asks you to rest.','subpoints':[{'text':'God asks you to rest.','source_ids':['s1'],'quote':'God asks you to rest.'}]}
        self.assertEqual(validate_outline({'points':[point]},[source])['points'][0]['title'],'God asks you to rest.')
        point['title']='God asks you to rest.'
        self.assertTrue(validate_outline({'points':[point]},[source]))

if __name__=='__main__':unittest.main()
