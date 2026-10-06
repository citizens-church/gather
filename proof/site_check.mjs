import {chromium} from '@playwright/test';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';

const root=resolve('.');await mkdir('proof/site-check',{recursive:true});
let revision=1;
const source={id:'s1',line:1,text:'God has not forgotten you. His love does not depend on your performance.',translations:{es:'Dios no te ha olvidado. Su amor no depende de tu desempeño.',ko:'하나님은 여러분을 잊지 않으셨어요. 하나님의 사랑은 여러분의 노력에 달려 있지 않아요.'}};
const point={title:'God has not forgotten you',source_ids:['s1'],quote:source.text,subpoints:[{text:'His love does not depend on your performance.',source_ids:['s1'],quote:source.text}]};
function note(){return {id:'a'.repeat(32),title:'Citizens Church · Live message',quality:'AI draft',scope:'Live notes from the published sermon transcript.',created:1,updated:revision,live:revision<3,live_status:revision>=3?'complete':'updated',shared:true,points:[point],sources:[source],scripture:[],translations:{es:{points:[{...point,title:'Dios no te ha olvidado',subpoints:[{...point.subpoints[0],text:source.translations.es}]}]},ko:{points:[{...point,title:'하나님은 여러분을 잊지 않으셨어요',subpoints:[{...point.subpoints[0],text:source.translations.ko}]}]}}};}
const server=createServer(async(req,res)=>{try{const url=new URL(req.url,'http://localhost');if(url.pathname==='/api/notes'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({documents:[note()]}));return;}if(url.pathname.endsWith('/export')){res.setHeader('Content-Type','text/markdown');res.setHeader('Content-Disposition','attachment; filename="gather-sermon-notes.md"');res.end('# Citizens Church\n\nGod has not forgotten you.\n');return;}if(url.pathname.startsWith('/api/notes/')){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({status:'complete',document:note()}));return;}let path;if(url.pathname==='/notes')path='public/notes.html';else if(url.pathname.startsWith('/static/'))path='public/'+url.pathname.slice(8);else path='site/'+(url.pathname==='/'?'index.html':url.pathname.slice(1));if(path.includes('..'))throw Error('Invalid path');const types={html:'text/html',js:'text/javascript',css:'text/css',svg:'image/svg+xml'};res.setHeader('Content-Type',types[path.split('.').at(-1)]||'text/plain');res.end(await readFile(resolve(root,path)));}catch{res.statusCode=404;res.end('Missing');}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
const browser=await chromium.launch();const errors=[];
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(base);await page.getByRole('heading',{level:1}).waitFor();await page.screenshot({path:'proof/site-check/site-desktop.png',fullPage:true});
 await page.getByRole('button',{name:'I’m hosting a service',exact:false}).click();await page.locator('#host-panel').waitFor({state:'visible'});assert.equal(await page.locator('#choose-host').getAttribute('aria-expanded'),'true');assert.match(await page.locator('#download-host').getAttribute('href'),/citizens-church\/gather\/releases/);
 await page.screenshot({path:'proof/site-check/site-host-setup.png',fullPage:true});
 await page.getByRole('button',{name:'I’m here to listen',exact:false}).click();await page.locator('#join-url').fill('https://example.com/not-a-room');await page.getByRole('button',{name:'Open listening room'}).click();await page.getByRole('status').filter({hasText:'Use the church listening link'}).waitFor();assert.equal(page.url(),base+'/');
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'proof/site-check/site-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.goto(base+'/notes?room=fixture');await page.locator('#points h2').waitFor();assert.match(await page.locator('#quality').textContent(),/LIVE/);
 await page.locator('#note-language').selectOption('ko');assert.match(await page.locator('#points h2').textContent(),/하나님/);await page.screenshot({path:'proof/site-check/notes-live-korean.png',fullPage:true});
 await page.locator('#search').fill('performance');revision=2;await page.locator('#notes-update').waitFor({state:'visible',timeout:12000});assert.equal(await page.locator('#search').inputValue(),'performance');await page.locator('#notes-update').click();await page.locator('#search').fill('');
 const downloadPromise=page.waitForEvent('download');await page.locator('#download').click();const download=await downloadPromise;assert.equal(download.suggestedFilename(),'gather-sermon-notes.md');await download.saveAs('proof/site-check/notes-download.md');
 await page.locator('#search').fill('God');revision=3;await page.locator('#notes-update').waitFor({state:'visible',timeout:12000});await page.locator('#notes-update').click();assert.match(await page.locator('#quality').textContent(),/SERVICE ENDED/);await page.locator('#search').fill('');
 await page.setViewportSize({width:1440,height:1000});await page.locator('#note-language').selectOption('es');await page.screenshot({path:'proof/site-check/notes-after-service-spanish.png',fullPage:true});
 assert.deepEqual(errors,[]);console.log('Desktop/mobile setup, invalid room, live-note updates, reading preservation, Korean/Spanish views, and post-service download passed.');
}finally{await browser.close();await new Promise(r=>server.close(r));}
