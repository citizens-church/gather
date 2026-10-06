const $=id=>document.getElementById(id);
function choose(role){const listening=role==='listen';$('listen-panel').hidden=!listening;$('host-panel').hidden=listening;for(const [id,selected]of [['choose-listen',listening],['choose-host',!listening]]){$(id).classList.toggle('selected',selected);$(id).setAttribute('aria-expanded',String(selected));}}
$('choose-listen').onclick=()=>choose('listen');$('choose-host').onclick=()=>choose('host');
function listeningURL(value){let url;try{url=new URL(value);}catch{throw Error('Paste the complete listening link from the church booth.');}if(!['http:','https:'].includes(url.protocol)||url.username||url.password||!url.pathname.endsWith('/listen')||!url.searchParams.get('room'))throw Error('Use the church listening link, including its listening room.');return url.href;}
$('join-form').onsubmit=event=>{event.preventDefault();try{location.assign(listeningURL($('join-url').value.trim()));}catch(error){$('join-status').textContent=error.message;}};
$('open-installed').onclick=()=>{$('launch-status').textContent='Allow your browser to open Gather Host. If it is not installed, download the Mac host app above first.';};
const platform=navigator.userAgentData?.platform||navigator.platform||'';
if(!/mac/i.test(platform))$('platform-note').textContent='This device can listen. The downloadable AI host currently needs an Apple Silicon Mac; Windows and Linux native hosts are not available yet.';
const incoming=new URLSearchParams(location.hash.slice(1)).get('join');if(incoming){try{$('join-url').value=listeningURL(incoming);$('join-status').textContent='Your listening link is ready. Open the room to choose your language.';}catch(error){$('join-status').textContent=error.message;}}
