"""Church-authored names and meanings; keep source words and explain once."""
import copy
import hashlib
import json
import re
from pathlib import Path
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

class Term(BaseModel):
    term: str = Field(min_length=1,max_length=60)
    kind: str = Field(default='series',pattern='^(series|slang)$')
    meaning: str = Field(default='',max_length=200)
    aliases: list[str] = Field(default=[],max_length=6)
    explanations: dict[str,str] = Field(default={},max_length=2)
    pronunciations: dict[str,str] = Field(default={},max_length=2)
    explain: bool = True

class Settings(BaseModel):
    active_series: str = Field(default='',max_length=60)
    entries: list[Term] = Field(default=[],max_length=30)

def entry_id(entry):
    return hashlib.sha256(entry['term'].casefold().encode()).hexdigest()[:16]

def initial():
    return {'active_series':'Peopling','entries':[
        {'term':'Peopling','kind':'series','meaning':'Learning how to relate to people, treat them well, and navigate relationships.','aliases':[],'explanations':{'es':'Aprender a relacionarnos con los demás, tratarlos bien y cuidar nuestras relaciones.','ko':'사람들과 관계를 맺고, 잘 대하며, 관계를 이어 가는 법을 배우는 것이에요.'},'pronunciations':{'ko':'피플링'},'explain':True},
        {'term':'DETOX','kind':'series','meaning':'','aliases':[],'explanations':{},'pronunciations':{'ko':'디톡스'},'explain':True},
    ]}

def matches(text,settings,context=''):
    found=[]
    for entry in settings.get('entries',[]):
        names=[entry['term'],*entry.get('aliases',[])]
        current=entry['term'].casefold()==settings.get('active_series','').casefold()
        series_cue=r'\b(?:series|titled|called|named)\b'
        if entry.get('kind')=='series' and not current:
            prior=any(re.search(r'(?<!\w)'+re.escape(n)+r'(?!\w)',context,re.I)for n in names)
            if not re.search(series_cue,text,re.I) and not (prior and re.search(series_cue,context,re.I)):
                continue
        pattern=r'(?<!\w)(?:'+'|'.join(re.escape(n)for n in sorted(names,key=len,reverse=True)if n.strip())+r')(?!\w)'
        for m in re.finditer(pattern,text,re.I):
            found.append({'start':m.start(),'end':m.end(),'heard':m.group(),'entry':entry,'id':entry_id(entry)})
    selected=[]
    for match in sorted(found,key=lambda m:(m['start'],-(m['end']-m['start']))):
        if not selected or match['start']>=selected[-1]['end']:
            selected.append(match)
    return selected

def protect(text,settings,context=''):
    found=matches(text,settings,context)
    parts=[];bindings=[];position=0
    for index,m in enumerate(found):
        marker='GATHERVOCAB'+chr(65+index//26)+chr(65+index%26)
        parts.extend([text[position:m['start']],marker]);position=m['end']
        bindings.append({**m,'marker':marker})
    parts.append(text[position:])
    return ''.join(parts),bindings

def restore(text,bindings,language=None):
    for binding in bindings:
        if text.count(binding['marker'])!=1:
            raise ValueError('A church title or slang term was changed or omitted. Review this line.')
        text=text.replace(binding['marker'],binding['entry']['term'])
    if 'GATHERVOCAB' in text:
        raise ValueError('A vocabulary placeholder was changed. Review this line.')
    if language=='ko':
        for binding in bindings:
            term=binding['entry']['term'];reading=binding['entry'].get('pronunciations',{}).get('ko','').rstrip()
            if not reading or not ('가'<=reading[-1]<='힣'):continue
            final=(ord(reading[-1])-0xac00)%28
            for consonant,vowel in [('은','는'),('이','가'),('을','를'),('과','와'),('으로','로')]:
                correct=consonant if final and not(consonant=='으로' and final==8)else vowel
                text=re.sub(re.escape(term)+r'(?:'+consonant+'|'+vowel+r')(?=\s|[.,!?]|$)',lambda _:term+correct,text)
    return text

def speech_text(text,found,language):
    for match in found:
        entry=match['entry'];reading=entry.get('pronunciations',{}).get(language,'').strip()
        if reading:text=re.sub(r'(?<!\w)'+re.escape(entry['term'])+r'(?![A-Za-z0-9_])',lambda _:reading,text)
    return text

def explain(text,found,language,seen):
    annotations=[];added=set()
    for match in found:
        entry=match['entry'];meaning=entry.get('explanations',{}).get(language,'').strip()
        if not meaning or not entry.get('explain',True) or match['id'] in seen or match['id'] in added:
            continue
        # Clearly distinguish the church's definition from words the preacher said.
        explanation=entry['term']+' — '+meaning.rstrip('.。')+'.'
        text+=' '+explanation
        added.add(match['id']);annotations.append({'id':match['id'],'term':entry['term'],'meaning':meaning})
    return text,annotations

class Vocabulary:
    def __init__(self,runtime,require_host,require_room,infer,get_engine,busy,emit):
        self.path=runtime/'church-vocabulary.json'
        self.require_host,self.require_room=require_host,require_room
        self.infer,self.get_engine,self.busy,self.emit=infer,get_engine,busy,emit
        self.preparing=False
        self.settings=json.loads(self.path.read_text())if self.path.exists()else initial()
        if not self.path.exists():self.save(self.settings)

    def save(self,value):
        tmp=self.path.with_suffix('.tmp')
        with tmp.open('w')as f:
            tmp.chmod(0o600);json.dump(value,f,ensure_ascii=False,indent=2)
        tmp.replace(self.path);self.settings=copy.deepcopy(value)

    def public(self):
        return {'active_series':self.settings['active_series'],'entries':[{'term':e['term'],'meaning':e['meaning'],'explanations':e['explanations']}for e in self.settings['entries']]}

    def register(self,app):
        @app.get('/api/vocabulary')
        async def get(request:Request,room:str|None=None):
            if room is not None:
                self.require_room(room);return self.public()
            self.require_host(request);return self.settings

        @app.post('/api/vocabulary')
        async def update(config:Settings,request:Request):
            self.require_host(request)
            if self.preparing:raise HTTPException(409,'Church vocabulary is already being prepared.')
            value=config.model_dump()
            seen=set();old={entry_id(e):e for e in self.settings['entries']}
            to_prepare=[]
            for entry in value['entries']:
                entry['term']=entry['term'].strip();entry['meaning']=entry['meaning'].strip()
                if not entry['term'] or entry['term'].casefold()in seen or 'gathervocab' in entry['term'].casefold():
                    raise HTTPException(422,'Use distinct, nonempty terms.')
                seen.add(entry['term'].casefold())
                if any(not a.strip() or len(a)>60 or 'gathervocab' in a.casefold() for a in entry['aliases']):
                    raise HTTPException(422,'Keep aliases short and nonempty.')
                if any(k not in {'es','ko'} or len(v)>300 for k,v in entry['explanations'].items()):
                    raise HTTPException(422,'Use short Spanish and Korean explanations.')
                if any(k not in {'es','ko'} or len(v)>60 for k,v in entry['pronunciations'].items()):
                    raise HTTPException(422,'Use short Spanish and Korean pronunciations.')
                previous=old.get(entry_id(entry))
                if entry['meaning'] and (not previous or previous['meaning']!=entry['meaning'] or not all(entry['explanations'].get(l)for l in ('es','ko'))):
                    to_prepare.append(entry)
                if not entry['meaning']:entry['explanations']={}
            if value['active_series'] and not any(e['term']==value['active_series'] and e['kind']=='series'for e in value['entries']):
                raise HTTPException(422,'Choose an existing series title.')
            if to_prepare:
                if self.busy():raise HTTPException(409,'Pause audio and let translation finish before preparing new explanations.')
                engine=self.get_engine()
                if engine is None:raise HTTPException(503,'Translation models are warming up.')
                self.preparing=True
                try:
                    for entry in to_prepare:
                        translated,_=await self.infer(engine.translate,entry['meaning'],['es','ko'],{'source_language':'en','korean_register':'spoken'})
                        entry['explanations']=translated
                finally:self.preparing=False
            self.save(value);self.emit('vocabulary',{'vocabulary':self.public()})
            return self.settings
