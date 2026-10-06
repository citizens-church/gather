const $=id=>document.getElementById(id);
let token, bootstrap, state, ws, context, stream, node, connected=false, startingMic=false,streamConnected=false;
const lines=new Map();
function notice(message){$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path,body){const res=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'x-host-token':token,...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body)});if(res.status===403&&path==="/api/events"){location.reload();return new Promise(()=>{});}if(!res.ok){let error;try{error=await res.json();}catch{error={detail:'The booth could not complete this action.'};}throw new Error(typeof error.detail==='string'?error.detail:JSON.stringify(error.detail));}return res;}
function setState(next){state=next;$('live-notes').checked=next.notes_live?.enabled??true;if(next.review!==undefined)$("review").checked=next.review;if(next.terms)$("terms").value=next.terms.join("\n");for(const el of document.querySelectorAll("[name=lang]"))el.checked=next.languages.includes(el.value);lines.clear();for(const s of next.segments||[])lines.set(s.id,s);$('voice').value=!next.live&&next.natural_voice_ready?'supertonic':next.voice_provider||'supertonic';streamConnected=next.audio_source==='livestream';$('source-language').value=next.source_language||'en';$('spanish-region').value=next.spanish_region||'latin';$('spanish-voice').value=next.spanish_voice||'dora';$('korean-register').value=next.korean_register||'spoken';$('glossary').value=Object.entries(next.glossary||{}).map(([a,b])=>`${a} = ${b}`).join('\n');render();controls();const last=next.segments?.at(-1);if(last?.timing){$("asr-time").textContent=last.timing.asr_ms?`${last.timing.asr_ms} ms`:"Text input";$("mt-time").textContent=last.timing.translation_ms===undefined?"—":`${last.timing.translation_ms} ms`;const times=Object.values(last.timing.tts_ms||{});$("tts-time").textContent=times.length?`${Math.max(...times)} ms`:"—";}}
function controls(){const live=state?.live, ready=state?.ready; $('start').hidden=!!live;$('stop').hidden=!live;$('start').disabled=!ready||!connected||state?.finishing_audio;$('start').textContent=state?.finishing_audio?'Finishing last audio line…':ready?'Start session':state?.error?'Models unavailable':'Warming up models…';$('mic').disabled=!live||!ready||!connected;$('send').disabled=!live||!ready||!connected;for(const el of document.querySelectorAll('#church,#review,#terms,[name=lang]'))el.disabled=!!live;$('session-status').textContent=!connected?'Reconnecting…':live?'Session live':ready?'Ready for a service':'Loading local models';$('live-dot').classList.toggle('live',!!live&&connected);$('listener-count').textContent=state?.listeners||0;$('download-previous').hidden=!state?.previous_session;$('live-notes').disabled=!connected||!state?.notes_live?.available;voiceControls();}
function element(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;}
function voiceControls(){
  const option=$('voice').querySelector('[value=elevenlabs]');option.disabled=!state?.voice_connected;option.textContent=state?.voice_connected?'ElevenLabs · George':'ElevenLabs · connect first';
  $('voice').disabled=!!state?.live;$('connect-voice').disabled=!!state?.live;$('voice-key').disabled=!!state?.live;
  $('voice').querySelector('[value=kokoro]').disabled=!state?.neural_voice_ready;$('voice').querySelector('[value=supertonic]').disabled=!state?.natural_voice_ready;sourceControls();
  $('voice').querySelector('[value=supertonic]').textContent=($('spanish-voice').value==='alex'?'Male':'Female')+' neural voice · on this Mac';
  for(const id of ['source-language','spanish-region','korean-register','glossary'])$(id).disabled=!!state?.live;
  $('spanish-voice').disabled=!connected||!['supertonic','kokoro'].includes($('voice').value);
  $('processing-note').textContent=$('voice').value==='elevenlabs'?'Recognition and translation run here. ElevenLabs generates the voice.':'Speech and translation are processed on this Mac.';
}
async function downloadTranscript(path,name){const res=await api(path);const url=URL.createObjectURL(await res.blob());const a=element('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function sourceControls(){if(streamConnected)$('source-kind').value='livestream';const kind=$('source-kind').value;$('livestream-controls').hidden=kind!=='livestream';$('board-controls').hidden=kind!=='board';$('connect-stream').disabled=!state?.live||!connected;$('connect-stream').textContent=streamConnected?'Pause livestream':'Connect livestream';$('source-kind').disabled=!!ws||streamConnected;$('stream-url').disabled=streamConnected;if(!lines.size&&state?.live){const hint=$('transcript').querySelector('.hint');if(hint)hint.textContent=ws||streamConnected||state.audio_source?'Listening for the speaker…':'Connect your sound desk or livestream to begin.';}}
$('source-kind').onchange=sourceControls;
$('connect-stream').onclick=async()=>{const button=$('connect-stream');button.disabled=true;try{if(streamConnected){await api('/api/source/pause',{});streamConnected=false;$('stream-status').textContent='Livestream paused.';}else{$('stream-status').textContent='Connecting to the livestream…';const response=await (await api('/api/source/livestream',{url:$('stream-url').value})).json();streamConnected=true;$('stream-status').textContent=`Receiving: ${response.title}`;}notice('');}catch(e){notice(e.message);}finally{sourceControls();}};
$('live-notes').onchange=async()=>{if(!state?.live)return;try{const value=await (await api('/api/notes/live/control',{enabled:$('live-notes').checked})).json();state.notes_live=value;notice(value.enabled?'Live notes enabled. Points will update as the message continues.':'Live notes paused. Translation continues; existing notes remain available.');}catch(error){$('live-notes').checked=state.notes_live.enabled;notice(error.message);}};
$('voice').onchange=voiceControls;
$('spanish-voice').onchange=async()=>{const style=$('spanish-voice').value;try{await api('/api/voice/style',{style});state.spanish_voice=style;notice(`${style==='alex'?'Male':'Female'} voice selected. New lines will use this voice.`);}catch(e){$('spanish-voice').value=state.spanish_voice;notice(e.message);}};
$('connect-voice').onclick=async()=>{const button=$('connect-voice');button.disabled=true;$('voice-connection-status').textContent='Testing a short Spanish voice sample…';try{await api('/api/voice/connect',{key:$('voice-key').value});$('voice-key').value='';state.voice_connected=true;voiceControls();$('voice').value='elevenlabs';voiceControls();$('voice-connection-status').textContent='Connected. George will speak translations in the next session.';notice('');}catch(e){$('voice-connection-status').textContent=e.message;}finally{button.disabled=!!state?.live;}};
$('download-previous').onclick=async()=>{try{await downloadTranscript(`/api/export/${state.previous_session}`,'gather-previous-transcript.json');}catch(e){notice(e.message);}};
function render(){
  const root=$('transcript'), atBottom=root.scrollHeight-root.scrollTop-root.clientHeight<80;
  const editable=new Map([...root.querySelectorAll('[data-edit]')].map(el=>[Number(el.dataset.edit),el.value]));
  if(!lines.size){$('line-count').textContent='0';root.replaceChildren(element('div','hint',state?.live?'Listening for the speaker…':'Start a session to begin.'));return;}
  root.replaceChildren();
  for(const s of [...lines.values()].sort((a,b)=>a.id-b.id)){
    const row=element('article','transcript-line');row.dataset.segment=s.id;
    const sourceLabel=({en:'ENGLISH',es:'ESPAÑOL',ko:'한국어',mixed:'MIXED'})[s.source_language]||'SOURCE';
    const top=element('div','line-top');top.append(element('span','',`${String(s.id).padStart(3,'0')} / ${sourceLabel}`),element('span',`line-status ${s.status}`,s.status==='ready'?'Published':s.status));row.append(top);
    if(['review','error'].includes(s.status)){
      const edit=element('textarea','edit-line');edit.dataset.edit=s.id;edit.value=editable.get(s.id)??s.source;edit.setAttribute('aria-label',`Correct line ${s.id}`);edit.rows=2;row.append(edit);
      for(const hint of s.suggestions||[]){const button=element('button','suggestion',`Check “${hint.heard}” → ${hint.suggestion}`);button.type='button';button.onclick=()=>{edit.value=edit.value.replace(new RegExp(hint.heard.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'i'),hint.suggestion);edit.focus();};row.append(button);}
      if(s.error)row.append(element('p','hint',s.error));
      const actions=element('div','line-actions');
      const publish=element('button','primary',s.status==='error'?'Retry translation':'Publish line →');publish.type='button';publish.disabled=[...lines.values()].some(p=>p.id<s.id&&['review','error'].includes(p.status));
      const discard=element('button','text-button','Discard');discard.type='button';
      for(const [button,isDiscard] of [[publish,false],[discard,true]])button.onclick=async()=>{button.disabled=true;try{await api(`/api/review/${s.id}`,{text:edit.value||s.source,discard:isDiscard});notice('');}catch(e){notice(e.message);button.disabled=false;}};
      actions.append(publish,discard);row.append(actions);
    }else{row.append(element('p','source',s.source));}
    for(const [lang,text] of Object.entries(s.translations||{})){const t=element('div','translation');t.append(element('small','',state.language_names?.[lang]||lang),element('span','',text));row.append(t);}
    root.append(row);
  }
  $('line-count').textContent=lines.size;
  if(atBottom)root.scrollTop=root.scrollHeight;
}
function handle(event){
  if(event.kind==='snapshot'){connected=true;setState(event.session);}
  if(event.kind==='reset'){state={...state,...event.session};lines.clear();$('transcript').replaceChildren(element('div','hint','Listening for the speaker…'));render();controls();}
  if(event.kind==='segment'||event.kind==='audio'){
    const s=event.segment;if(event.session_id&&event.session_id!==state.id)return;lines.set(s.id,{...lines.get(s.id),...s});render();
    if(s.timing){$('asr-time').textContent=s.timing.asr_ms?`${s.timing.asr_ms} ms`:'Text input';$('mt-time').textContent=s.timing.translation_ms===undefined?'—':`${s.timing.translation_ms} ms`;const times=Object.values(s.timing.tts_ms||{});$('tts-time').textContent=times.length?`${Math.max(...times)} ms`:'—';}
  }
  if(event.kind==='partial')$('partial').textContent=event.text;
  if(event.kind==='meter'){const level=Math.max(0,Math.min(100,(20*Math.log10(Math.max(event.rms,0.00001))+60)/60*100));$('meter-fill').style.width=`${level}%`;$('audio-quality').textContent=event.peak>.98?'Clipping':event.rms<.009?'Quiet / silence':'Speech detected';}
  if(event.kind==='status'){Object.assign(state,event);controls();if(event.error)notice(event.error);}
  if(event.kind==='audience'){state.listeners=event.listeners;controls();}
  if(event.kind==='capture_error'){notice(event.message);stopMic();}
  if(event.kind==='audio_source'){streamConnected=event.source==='livestream';$('stream-status').textContent=`Receiving: ${event.title}`;sourceControls();}if(event.kind==='capture_stopped'){streamConnected=false;state.finishing_audio=false;stopMic();controls();}
  if(event.kind==='voice_error')notice(event.message);
  if(event.kind==='voice_connection'){state.voice_connected=event.connected;voiceControls();}
  if(event.kind==='notes_control'){state.notes_live=event;$('live-notes').checked=event.enabled;}
  if(event.kind==='voice_style'){state.spanish_voice=event.style;$('spanish-voice').value=event.style;voiceControls();}
}
async function events(){
  for(;;){
    try{const response=await api('/api/events');const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';for(;;){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let end;while((end=buffer.indexOf('\n\n'))>=0){const event=buffer.slice(0,end);buffer=buffer.slice(end+2);if(event.startsWith('data: '))handle(JSON.parse(event.slice(6)));}}}
    catch{}connected=false;controls();await new Promise(resolve=>setTimeout(resolve,1500));
  }
}
async function stopMic(){const socket=ws;ws=null;if(socket?.readyState===1)socket.send(JSON.stringify({type:'stop'}));node?.disconnect();node=null;stream?.getTracks().forEach(track=>track.stop());stream=null;if(context){await context.close().catch(()=>{});context=null;}$('mic').textContent='Start microphone';$('meter-fill').style.width='0';$('audio-quality').textContent='No input';sourceControls();}
async function startMic(){
  if(startingMic||ws)return;startingMic=true;
  try{
    stream=await navigator.mediaDevices.getUserMedia({audio:{deviceId:$('device').value?{exact:$('device').value}:undefined,echoCancellation:!$('desk-feed').checked,noiseSuppression:!$('desk-feed').checked,autoGainControl:false},video:false});
    context=new AudioContext();await context.resume();await context.audioWorklet.addModule('/static/audio-worklet.js');
    ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/capture`);const socket=ws;
    await new Promise((resolve,reject)=>{socket.onopen=()=>socket.send(JSON.stringify({token}));socket.onmessage=event=>{if(JSON.parse(event.data).ready)resolve();};socket.onerror=()=>reject(new Error('Could not connect the audio input.'));socket.onclose=()=>reject(new Error('Audio capture is unavailable.'));});
    node=new AudioWorkletNode(context,'gather-pcm');node.port.onmessage=event=>{if(socket.readyState===1){if(socket.bufferedAmount>64000){notice('The audio connection is falling behind. Reconnect the microphone.');stopMic();return;}socket.send(event.data);}};
    context.createMediaStreamSource(stream).connect(node);const mute=context.createGain();mute.gain.value=0;node.connect(mute).connect(context.destination);
    socket.onclose=()=>{if(ws===socket){stopMic();notice('Microphone disconnected. Restart it to continue.');}};
    $('mic').textContent='Pause microphone';sourceControls();notice('');
  }catch(e){await stopMic();notice(e.name==='NotAllowedError'?'Microphone permission is needed. Allow access in the browser, then try again.':e.message);}finally{startingMic=false;}
}
$('mic').onclick=()=>ws?stopMic():startMic();
$('devices').onclick=async()=>{try{const temporary=await navigator.mediaDevices.getUserMedia({audio:true});temporary.getTracks().forEach(t=>t.stop());const devices=await navigator.mediaDevices.enumerateDevices();$('device').replaceChildren(new Option('Default microphone / sound desk',''));for(const d of devices.filter(d=>d.kind==='audioinput'))$('device').add(new Option(d.label||'Audio input',d.deviceId));notice('');}catch(e){notice('Allow microphone access to see the available audio inputs.');}};
function readGlossary(){const values={};for(const line of $('glossary').value.split('\n').filter(s=>s.trim())){const index=line.indexOf('=');if(index<1||!line.slice(index+1).trim())throw new Error('Use one phrase = Spanish translation per glossary line.');values[line.slice(0,index).trim()]=line.slice(index+1).trim();}return values;}
$('start').onclick=async()=>{try{const languages=[...document.querySelectorAll('[name=lang]:checked')].map(el=>el.value);if(!languages.length)throw new Error('Choose at least one translation language.');const next=await (await api('/api/start',{title:$('church').value,languages,review:$('review').checked,live_notes_enabled:$('live-notes').checked,terms:$('terms').value.split('\n').map(t=>t.trim()).filter(Boolean),voice_provider:$('voice').value,source_language:$('source-language').value,spanish_region:$('spanish-region').value,spanish_voice:$('spanish-voice').value,korean_register:$('korean-register').value,glossary:readGlossary(),draft_edits:Object.fromEntries([...document.querySelectorAll('[data-edit]')].map(el=>[el.dataset.edit,el.value]))})).json();setState(next);notice(next.previous_session?'New session started. The previous transcript is saved; use Download previous transcript to retrieve it.':'');}catch(e){notice(e.message);}};
$('stop').onclick=async()=>{try{await stopMic();const ended=await (await api('/api/stop',{})).json();state.finishing_audio=ended.finishing_audio;state.live=false;controls();notice('Session ended. Starting another session saves this transcript, including unpublished lines.');}catch(e){notice(e.message);}};
$('text-form').onsubmit=async event=>{event.preventDefault();const text=$('text').value.trim();if(!text)return;try{await api('/api/text',{text});$('text').value='';notice('');}catch(e){notice(e.message);}};
$('sample').onclick=()=>{$('text').value='Welcome to Citizens Church. God has not abandoned you. Open your Bible to Habakkuk chapter two.';$('text').focus();};
$('copy-link').onclick=async()=>{try{await navigator.clipboard.writeText(bootstrap.listener_url);$('copy-link').textContent='Link copied';setTimeout(()=>$('copy-link').textContent='Copy phone link',1800);}catch{notice(`Phone link: ${bootstrap.listener_url}`);}};
$('download').onclick=async()=>{try{const res=await api('/api/export');const url=URL.createObjectURL(await res.blob());const a=element('a');a.href=url;a.download='gather-transcript.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){notice(e.message);}};
setInterval(()=>{if(!state?.started)return;const s=Math.max(0,Math.floor((Date.now()/1000)-state.started));if(state.live)$('clock').textContent=`${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;},1000);
try{const res=await fetch('/api/bootstrap');if(!res.ok)throw new Error('Open the booth on this Mac at localhost.');bootstrap=await res.json();token=bootstrap.token;state=bootstrap.state;$('church').value=state.title;$('listen-link').href=bootstrap.local_listener_url;$('overlay-link').href=bootstrap.overlay_url;const qr=await api('/api/qr');$('qr').src=URL.createObjectURL(await qr.blob());$('qr').hidden=false;$('qr-loading').hidden=true;setState(state);events();}catch(e){notice(e.message);}
