const $=id=>document.getElementById(id);
const room=new URLSearchParams(location.search).get('room');
$('notes-link').href='/notes?room='+encodeURIComponent(room||'');
let session,segments=new Map(),playing=false,connected=false,lastHeard=0,currentAudio=null,currentId=null,preview=null;
const failed=new Set();
const heardParts=new Map();
function playbackRate(){if($('speed').value!=='auto')return Number($('speed').value);const backlog=[...segments.values()].filter(s=>s.id>lastHeard).length;return backlog>=3?1.15:1;}
function language(){return $('language').value;}
function status(message){$('player-status').textContent=message;}
function renderVocabulary(){
  const lang=language(),root=$('vocabulary-list');root.replaceChildren();
  for(const entry of session?.vocabulary?.entries||[]){const meaning=entry.explanations[lang];if(!meaning)continue;const row=document.createElement('div');row.className='history-item';const title=document.createElement('strong');title.textContent=entry.term;const text=document.createElement('p');text.textContent=meaning;row.append(title,text);root.append(row);}
  $('vocabulary-details').hidden=!root.childElementCount;$('vocabulary-summary').textContent=lang==='ko'?'이름과 표현':lang==='es'?'Nombres y expresiones':'Names & expressions';
}
function render(){
  renderVocabulary();
  if(currentAudio && $('speed').value==='auto')currentAudio.playbackRate=playbackRate();
  const lang=language(),ordered=[...segments.values()].sort((a,b)=>a.id-b.id),last=ordered.at(-1);
  if(last){$('current-caption').textContent=last.translations[lang]||'Preparing translation…';$('original').textContent=last.source;$('caption-label').textContent=`LIVE TRANSLATION / ${String(last.id).padStart(3,'0')}`;}else{$('current-caption').textContent='Waiting for the speaker…';$('original').textContent='';$('caption-label').textContent='LISTENING';}
  if(preview&&(!last||preview.line_id>last.id)){$('current-caption').textContent=preview.translations[lang]||'';$('original').textContent=preview.source;$('caption-label').textContent='IN PROGRESS / WORDING MAY CHANGE';}
  const root=$('history');root.replaceChildren();for(const seg of ordered.slice(-13,-1).reverse()){const row=document.createElement('div');row.className='history-item';const number=document.createElement('small');number.textContent=`LINE ${String(seg.id).padStart(3,'0')}`;const text=document.createElement('div');text.textContent=seg.translations[lang]||'';row.append(number,text);root.append(row);}$('history-count').textContent=Math.max(0,ordered.length-1);
  $('jump').hidden=!playing||ordered.filter(s=>s.id>lastHeard).length<3;
}
function pause(){playing=false;currentAudio?.pause();currentAudio=null;currentId=null;$('play').textContent='▶  Listen live';status('Audio paused. Captions are still live.');}
function reset(next){if(session?.id!==next.id){currentAudio?.pause();currentAudio=null;currentId=null;segments.clear();lastHeard=0;failed.clear();heardParts.clear();preview=null;$('current-caption').textContent='Waiting for the speaker…';$('original').textContent='';}if(next.segments){const ids=new Set(next.segments.map(s=>s.id));if(currentId!==null&&!ids.has(currentId)){currentAudio?.pause();currentAudio=null;currentId=null;}segments.clear();for(const key of failed)if(!ids.has(Number(key.split(':')[0])))failed.delete(key);lastHeard=Math.min(lastHeard,Math.max(0,...ids));for(const key of heardParts.keys())if(!ids.has(Number(key.split(':')[0])))heardParts.delete(key);preview=null;}session=next;$('service-name').textContent=next.title;const chosen=language();$('language').replaceChildren();for(const lang of next.languages)$('language').add(new Option(next.language_names?.[lang]||({es:'Español',ko:'한국어',fr:'Français',pt:'Português'}[lang]),lang));if(next.languages.includes(chosen))$('language').value=chosen;for(const seg of next.segments||[])segments.set(seg.id,seg);render();pump();}
function pump(){
  if(!playing||currentAudio||!connected)return;
  const next=[...segments.values()].sort((a,b)=>a.id-b.id).find(s=>s.id>lastHeard);
  if(!next){status(session?.live?'Listening for the next line…':'The session has ended.');return;}
  const lang=language();
  if(failed.has(`${next.id}:${lang}`)){lastHeard=next.id;status('Voice unavailable for one line. Read its caption above.');setTimeout(pump,1000);return;}
  const key=`${next.id}:${lang}`,parts=next.audio_parts?.[lang]||[],index=heardParts.get(key)||0;
  const url=parts.length?parts[index]:next.audio?.[lang];
  if(!url){if(parts.length&&next.audio_complete?.[lang]){lastHeard=next.id;heardParts.delete(key);pump();return;}status('Preparing the next voice sentence…');return;}
  const audio=new Audio(url);currentAudio=audio;currentId=next.id;audio.playbackRate=playbackRate();audio.preload='auto';
  audio.onplaying=()=>window.dispatchEvent(new CustomEvent('gather-playback',{detail:{session_id:session.id,line:next.id,language:lang,piece:index,time:Date.now(),playback_rate:audio.playbackRate}}));
  audio.onended=()=>{if(currentAudio!==audio)return;const latest=segments.get(next.id);if(parts.length){heardParts.set(key,index+1);if(latest?.audio_complete?.[lang]&&index+1>=(latest.audio_parts?.[lang]?.length||0)){lastHeard=next.id;heardParts.delete(key);}}else lastHeard=next.id;currentAudio=null;currentId=null;render();pump();};
  audio.onerror=()=>{if(currentAudio!==audio)return;failed.add(`${next.id}:${lang}`);lastHeard=next.id;currentAudio=null;currentId=null;status('Could not play this line. Captions remain available.');setTimeout(pump,1000);};
  audio.play().then(()=>status('Playing translation.')).catch(()=>{if(currentAudio!==audio)return;currentAudio=null;currentId=null;playing=false;$('play').textContent='▶  Listen live';status('Tap Listen live again to allow audio on this device.');});
}
$('play').onclick=()=>{if(playing){pause();return;}playing=true;heardParts.clear();$('play').textContent='Ⅱ  Pause audio';const ordered=[...segments.values()].sort((a,b)=>a.id-b.id);lastHeard=ordered.length?ordered.at(-1).id-1:0;pump();};
$('jump').onclick=()=>{currentAudio?.pause();currentAudio=null;currentId=null;lastHeard=Math.max(0,...segments.keys())-1;pump();render();};
$('language').onchange=()=>{currentAudio?.pause();currentAudio=null;currentId=null;heardParts.clear();lastHeard=Math.max(0,...segments.keys())-1;render();pump();};
$('speed').onchange=()=>{if(currentAudio)currentAudio.playbackRate=playbackRate();};
$('show-original').onchange=()=>{$('original').hidden=!$('show-original').checked;};
const events=new EventSource(`/api/events?room=${encodeURIComponent(room||'')}`);
events.onopen=()=>{connected=true;$('connection').textContent='Connected';$('listener-dot').classList.add('live');pump();};
events.onerror=()=>{connected=false;$('connection').textContent='Reconnecting';$('listener-dot').classList.remove('live');status('Connection interrupted. Trying to reconnect…');};
events.onmessage=event=>{const data=JSON.parse(event.data);if(data.kind==='snapshot'||data.kind==='reset')reset(data.session);if(data.session_id&&data.session_id!==session?.id)return;if(data.kind==='vocabulary'){session.vocabulary=data.vocabulary;renderVocabulary();}if(data.kind==='preview'){preview=data;render();}if(data.kind==='segment'||data.kind==='audio'){segments.set(data.segment.id,data.segment);if(preview&&data.segment.id>=preview.line_id)preview=null;render();pump();}if(data.kind==='audio_error'){failed.add(`${data.segment_id}:${data.language}`);pump();}if(data.kind==='status'&&data.live!==undefined){session.live=data.live;preview=null;render();pump();}if(data.kind==='resync'){events.close();location.reload();}};
