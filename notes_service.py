"""Private draft generation and explicit listener sharing of sermon notes."""
import asyncio
import copy
import json
from pathlib import Path
import re
import secrets
import time
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sermon_notes import NotesEngine, markdown, prepare_sources, scripture_references

class NotesRequest(BaseModel):
    kind: str = Field(pattern='^(session|lab)$')
    source_id: str = Field(pattern='^[a-f0-9]{16,32}$')

class NotesService:
    def __init__(self, root, runtime, require_host, require_room, infer, engine, session, unavailable):
        self.root, self.runtime = root, runtime
        self.require_host, self.require_room, self.infer = require_host, require_room, infer
        self.engine, self.session, self.unavailable = engine, session, unavailable
        self.directory = runtime / 'notes'
        self.directory.mkdir(exist_ok=True)
        self.jobs, self.model, self.busy = {}, None, False

    def read(self, id):
        if not re.fullmatch('[a-f0-9]{32}', id):
            raise HTTPException(404)
        path = self.directory / (id+'.json')
        if not path.exists():
            raise HTTPException(404)
        return json.loads(path.read_text())

    def save(self, document):
        path = self.directory / (document['id']+'.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(document, ensure_ascii=False, indent=2))
        temp.chmod(0o600); temp.replace(path)

    def sources(self, kind, id):
        if kind == 'session':
            current = self.session()
            if id == current['id']:
                value = copy.deepcopy(current)
            else:
                path = self.runtime / (id+'.json')
                if not re.fullmatch('[a-f0-9]{16}', id) or not path.exists():
                    raise HTTPException(404)
                value = json.loads(path.read_text())
            sources = [{'id':f"s{s['id']}", 'text':s['source'], 'translations':s.get('translations',{}), 'line':s['id']} for s in value['segments'] if s.get('translations')]
            title, scope = value['title'], 'Notes from the published transcript. Check recognition and meaning against the sermon.'
        else:
            path = self.runtime / 'sermon-lab' / id / 'report.json'
            if not re.fullmatch('[a-f0-9]{32}', id) or not path.exists():
                raise HTTPException(404)
            value = json.loads(path.read_text())
            sources = [{'id':f's{i+1}', 'text':s['source'], 'line':i+1, 'seconds':value['start_seconds']+s.get('speech_end_seconds',0)} for i,s in enumerate(value['segments']) if not s.get('held_for_review')]
            title, scope = value['title'], f"Sermon excerpt: {value['audio_seconds']:g} seconds, starting at {value['start_seconds']:g} seconds. This is not a complete-sermon outline."
        if not sources:
            raise HTTPException(409, 'No published transcript is available for notes yet.')
        if sum(len(s['text']) for s in sources) > 180000:
            raise HTTPException(409, 'This transcript is too long for one outline. Use shorter excerpts.')
        return title, scope, prepare_sources(sources)

    async def generate(self, id, config, title, scope, sources):
        try:
            self.jobs[id]['message'] = 'Finding the main points and their supporting passages'
            if self.model is None:
                self.model = await self.infer(NotesEngine)
            content = await self.infer(self.model.generate, sources)
            doc = {'id':id, 'title':title, 'scope':scope, 'source_kind':config.kind, 'source_id':config.source_id, 'sources':sources, 'scripture':scripture_references(sources),'created':time.time(), 'shared':False, 'translations':{}, **content}
            # Translation is a separate, off-air step. Original citations remain English.
            for language in ('es','ko'):
                self.jobs[id]['message'] = 'Preparing '+('Spanish' if language=='es' else 'Korean')+' notes'
                translated = copy.deepcopy(doc['points'])
                for point in translated:
                    for node,key in [(point,'title')]+[(s,'text') for s in point['subpoints']]:
                        value,_ = await self.infer(self.engine().translate,node[key],[language],{'source_language':'en','korean_register':'spoken'})
                        node[key] = value[language]
                doc['translations'][language] = {'points':translated}
            self.save(doc)
            self.jobs[id] = {'status':'complete','document':doc}
        except Exception as exc:
            self.jobs[id] = {'status':'error','message':str(exc) if isinstance(exc,ValueError) else 'Notes could not finish. The transcript is preserved.'}
        finally:
            self.busy = False

    def register(self, app):
        @app.get('/notes')
        async def page(request:Request, room:str=''):
            if request.client.host not in {'127.0.0.1','::1'}:
                self.require_room(room)
            return FileResponse(self.root/'public/notes.html')

        @app.get('/api/notes')
        async def listing(request:Request, room:str=''):
            host = not room
            self.require_host(request) if host else self.require_room(room)
            docs = [json.loads(p.read_text()) for p in self.directory.glob('*.json')]
            if not host:
                docs = [d for d in docs if d.get('shared')]
            return {'documents':[{'id':d['id'],'title':d['title'],'created':d['created'],'shared':d['shared'],'scope':d['scope'],'live':d.get('live',False),'live_status':d.get('live_status'),'updated':d.get('updated',d['created'])} for d in sorted(docs,key=lambda d:d['created'],reverse=True)]}

        @app.get('/api/notes/sources')
        async def available(request:Request):
            self.require_host(request)
            current = self.session()
            items = []
            for id in [current['id'],current.get('previous_session')]:
                if id:
                    try:
                        title,scope,sources = self.sources('session',id)
                        label='Current service' if id==current['id'] else 'Previous transcript'
                        items.append({'kind':'session','id':id,'title':title,'label':label+': '+title+' · '+str(len(sources))+' lines','created':time.time()+1 if id==current['id'] else 0})
                    except HTTPException: pass
            for path in (self.runtime/'sermon-lab').glob('*/report.json'):
                r = json.loads(path.read_text())
                items.append({'kind':'lab','id':r['id'],'title':r['title'],'label':r['title']+f" · {r['audio_seconds']:g}s at {r['start_seconds']:g}s",'created':r['completed_at']})
            return {'sources':sorted(items,key=lambda i:i.get('created',float('inf')),reverse=True)}

        @app.post('/api/notes')
        async def create(config:NotesRequest,request:Request):
            self.require_host(request)
            if self.busy or self.unavailable():
                raise HTTPException(409,'Pause audio and let translations finish before creating notes.')
            if self.engine() is None:
                raise HTTPException(503,'The models are warming up.')
            title,scope,sources = self.sources(config.kind,config.source_id)
            id = secrets.token_hex(16)
            self.busy = True
            self.jobs[id] = {'status':'running','message':'Preparing the source transcript'}
            asyncio.create_task(self.generate(id,config,title,scope,sources))
            return {'id':id,'status':'running'}

        @app.get('/api/notes/{id}')
        async def document(id:str,request:Request,room:str=''):
            if not room:
                self.require_host(request)
                if id in self.jobs and self.jobs[id]['status']!='complete':
                    return self.jobs[id]
            else:
                self.require_room(room)
            doc = self.read(id)
            if room and not doc.get('shared'):
                raise HTTPException(404)
            return {'status':'complete','document':doc}

        @app.post('/api/notes/{id}/share')
        async def share(id:str,request:Request):
            self.require_host(request)
            doc = self.read(id); doc['shared']=True; self.save(doc)
            return {'shared':True}

        @app.get('/api/notes/{id}/export')
        async def export(id:str,request:Request,language:str='en',room:str=''):
            self.require_host(request) if not room else self.require_room(room)
            doc = self.read(id)
            if room and not doc.get('shared'):
                raise HTTPException(404)
            if language not in {'en','es','ko'}:
                raise HTTPException(400)
            return Response(markdown(doc,language),media_type='text/markdown',headers={'Content-Disposition':'attachment; filename="gather-sermon-notes.md"'})
