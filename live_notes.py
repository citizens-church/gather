"""Incremental, source-linked live notes. Never reads unpublished booth drafts."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from sermon_notes import validate_outline, scripture_references

def document_id(session_id):return hashlib.sha256(('live-notes:'+session_id).encode()).hexdigest()[:32]

def published_sources(session):
    return [{'id':f"s{s['id']}",'line':s['id'],'text':s['source'],'translations':copy.deepcopy(s.get('base_translations',s.get('translations',{})))} for s in session['segments'] if s.get('translations') and s['status']!='discarded']

def apply_patch(document,patch,sources):
    ids=patch['source_ids'];lookup={s['id']:s['text']for s in sources}
    if not ids or any(i not in lookup for i in ids):raise ValueError('Live note cites an unavailable source.')
    passage=' '.join(lookup[i]for i in ids);quote=passage[:220].rsplit(' ',1)[0]if len(passage)>220 else passage
    point={'title':patch['title']['en'],'source_ids':ids,'quote':quote,'subpoints':[{'text':patch['text']['en'],'source_ids':ids,'quote':quote}]}
    point=validate_outline({'points':[point]},sources)['points'][0]
    index=patch['point']
    if not isinstance(index,int)or not 0<=index<=min(5,len(document['points'])):raise ValueError('Invalid live outline group.')
    for language in ('es','ko'):
        title,text=patch['title'].get(language,''),patch['text'].get(language,'')
        if not title.strip()or not text.strip()or len(title)>160 or len(text)>160:raise ValueError('A translated live note is empty or too long.')
        # Validate source IDs, numbers and supported quotation independently for each language.
        validate_outline({'points':[{'title':title,'source_ids':ids,'quote':quote,'subpoints':[{'text':text,'source_ids':ids,'quote':quote}]}]},sources)
    if index==len(document['points']):
        document['points'].append(point)
        for language in ('es','ko'):
            document['translations'][language]['points'].append({**copy.deepcopy(point),'title':patch['title'][language],'subpoints':[{**copy.deepcopy(point['subpoints'][0]),'text':patch['text'][language]}]})
    else:
        current=document['points'][index]
        current['source_ids']=list(dict.fromkeys(current['source_ids']+ids));current['subpoints'].append(point['subpoints'][0])
        for language in ('es','ko'):
            translated=document['translations'][language]['points'][index]
            translated['source_ids']=list(current['source_ids']);translated['subpoints'].append({**copy.deepcopy(point['subpoints'][0]),'text':patch['text'][language]})
    document['processed_line']=max(s['line']for s in sources)
    document['generation_ms']=patch.get('generation_ms',0)
    document['updated']=time.time();document['live_status']='updated';document.pop('live_error',None)
    document.pop('retry_after',None)

class LiveNotes:
    def __init__(self,root,notes,current,waiting,emit):
        self.root,self.notes,self.current,self.waiting,self.emit=root,notes,current,waiting,emit
        self.enabled=(root/'models/sermon-notes/model.safetensors').exists() and os.getenv('GATHER_DISABLE_LIVE_NOTES')!='1'
        self.documents={};self.process=None;self.processing=False;self.last_run=0;self.task=None

    def capture(self,session):
        if not self.enabled or not session.get('live_notes_enabled',True):return None
        sources=published_sources(session)
        if not sources:return None
        id=document_id(session['id'])
        if id not in self.documents:
            path=self.notes.directory/(id+'.json')
            self.documents[id]=json.loads(path.read_text())if path.exists()else {'id':id,'title':session['title'],'scope':'Live notes from the published sermon transcript. Points may be incomplete while the preacher continues.','quality':'AI draft; check against the sermon','source_kind':'session','source_id':session['id'],'created':session.get('started')or time.time(),'shared':True,'points':[],'sources':[],'translations':{'es':{'points':[]},'ko':{'points':[]}},'scripture':[],'processed_line':0,'live_status':'collecting','updated':0}
        document=self.documents[id]
        changed=document['sources']!=sources or document.get('live')!=session['live']
        document['sources']=sources;document['live']=session['live'];document['scripture']=scripture_references(sources)
        if changed:
            document['updated']=time.time();self.notes.save(document);self.emit('notes_update',{'document_id':id,'updated':document['updated'],'live':document['live']})
        return document

    def status(self):
        id=document_id(self.current()['id']);doc=self.documents.get(id)
        return {'available':self.enabled,'enabled':self.enabled and self.current().get('live_notes_enabled',True),'document_id':id if doc else None,'processing':self.processing,'status':doc.get('live_status','collecting')if doc else 'waiting'}

    async def infer(self,body):
        if self.process is None or self.process.returncode is not None:
            log=self.notes.runtime/'live-notes.log';log.touch(mode=0o600,exist_ok=True)
            with log.open('ab')as stderr:
                self.process=await asyncio.create_subprocess_exec(sys.executable,str(self.root/'live_notes_worker.py'),cwd=self.root,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=stderr)
        self.process.stdin.write((json.dumps(body,ensure_ascii=False)+'\n').encode());await self.process.stdin.drain()
        raw=await asyncio.wait_for(self.process.stdout.readline(),timeout=90)
        if not raw:raise ValueError('Live notes worker disconnected.')
        result=json.loads(raw)
        if 'error'in result:raise ValueError(result['error'])
        return result['patch']

    async def run(self):
        while True:
            try:
                self.capture(self.current())
                for document in list(self.documents.values()):
                    pending=[s for s in document['sources']if s['line']>document['processed_line']]
                    if not pending:
                        if not document['live'] and not self.waiting() and document.get('live_status')!='complete':
                            document.update({'live_status':'complete','updated':time.time(),'scope':'Notes from the published sermon transcript. The service has ended; check AI wording against its source passages.'});self.notes.save(document);self.emit('notes_update',{'document_id':document['id'],'updated':document['updated'],'live':False})
                        continue
                    if document.get('retry_after',0)>time.time():continue
                    if document['live']and time.time()-self.last_run<25:continue
                    batch=[];size=0
                    for source in pending:
                        if batch and(size+len(source['text'])>1000 or len(batch)>=8):break
                        batch.append(source);size+=len(source['text'])
                    self.processing=True;self.last_run=time.time();document['live_status']='organizing';self.notes.save(document)
                    try:
                        patch=await self.infer({'sources':batch,'headings':[p['title']for p in document['points']]})
                        apply_patch(document,patch,batch)
                    except Exception as error:
                        document.update({'live_status':'retrying','live_error':'Live notes need a source check. The transcript is retained.','retry_after':time.time()+30})
                        if isinstance(error,asyncio.TimeoutError)and self.process:self.process.kill();await self.process.wait();self.process=None
                    finally:self.processing=False
                    self.notes.save(document);self.emit('notes_update',{'document_id':document['id'],'updated':document['updated'],'live':document['live']})
                await asyncio.sleep(2)
            except asyncio.CancelledError:raise
            except Exception:await asyncio.sleep(3)

    async def close(self):
        if self.task:
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
            self.task=None
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:await asyncio.wait_for(self.process.wait(),timeout=5)
            except asyncio.TimeoutError:self.process.kill();await self.process.wait()
        self.process=None;self.processing=False
