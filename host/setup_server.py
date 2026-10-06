"""Browser-guided local setup. Standard library only; no system Python required."""
import argparse
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import signal
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser

def verified(path,item):
    if not path.is_file() or path.stat().st_size!=item['size']:return False
    with path.open('rb') as stream:
        digest=hashlib.file_digest(stream,'sha256').hexdigest()
    return digest==item['sha256']

def download(item,path,progress=lambda *_:None):
    """Resume partial downloads and verify the pinned checksum before exposing a file."""
    path.parent.mkdir(parents=True,exist_ok=True)
    if verified(path,item):
        progress(item['size']);return
    partial=path.with_suffix(path.suffix+'.partial')
    offset=partial.stat().st_size if partial.exists() else 0
    if offset>=item['size']:
        if verified(partial,item):partial.replace(path);progress(item['size']);return
        partial.unlink();offset=0
    req=urllib.request.Request(item['url'],headers={'Range':f'bytes={offset}-'} if offset else {})
    with urllib.request.urlopen(req,timeout=60) as response:
        if offset and response.status!=206:offset=0
        if offset and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
            raise ValueError('The download server returned an unexpected range. Retry setup.')
        with partial.open('ab' if offset else 'wb') as output:
            total=offset;progress(total)
            while block:=response.read(1024*1024):
                output.write(block);total+=len(block);progress(total)
    if not verified(partial,item):
        partial.unlink(missing_ok=True)
        raise ValueError('A model download failed its integrity check. Retry to download a verified copy.')
    partial.replace(path)

class Setup:
    def __init__(self,resources,data,uv,port=47680):
        self.resources,self.data,self.uv,self.port=resources,data,uv,port
        self.code=data/'app';self.environment=data/'environment';self.model_root=self.code/'models'
        self.token=secrets.token_urlsafe(32);self.lock=threading.Lock();self.process=None;self.tool_process=None
        self.models=json.loads((resources/'host/models.json').read_text())
        self.state={'stage':'idle','message':'Ready to set up this computer.','downloaded':0,'total':0,'busy':False,'error':None,'booth_url':f'http://127.0.0.1:{port}/'}
        self.data.mkdir(parents=True,exist_ok=True)

    def hardware(self):
        ram=0
        if platform.system()=='Darwin':
            ram=int(subprocess.check_output(['sysctl','-n','hw.memsize']).strip())
        return {'supported':platform.system()=='Darwin' and platform.machine()=='arm64','ram_gb':round(ram/1024**3,1),'free_gb':round(shutil.disk_usage(self.data).free/1024**3,1),'platform':platform.system(),'architecture':platform.machine()}

    def view(self):
        with self.lock:state=dict(self.state)
        state.update({'hardware':self.hardware(),'core_bytes':sum(f['size'] for m in self.models if not m.get('optional') for f in m['files']),'notes_bytes':sum(f['size'] for m in self.models if m.get('optional') for f in m['files'])})
        return state

    def status(self,**values):
        with self.lock:self.state.update(values)

    def start(self,notes):
        with self.lock:
            if self.state['busy']:return False
            self.state.update({'busy':True,'error':None,'stage':'preparing','message':'Preparing your local host…'})
        threading.Thread(target=self.install,args=(notes,),daemon=True).start();return True

    def command(self,args,log):
        with log.open('ab') as output:
            self.tool_process=subprocess.Popen(args,stdout=output,stderr=subprocess.STDOUT,env={**os.environ,'UV_PYTHON_INSTALL_DIR':str(self.data/'python'),'UV_CACHE_DIR':str(self.data/'cache')})
            result=self.tool_process.wait();self.tool_process=None
            if result:raise subprocess.CalledProcessError(result,args)

    def tools(self):
        tools=json.loads((self.resources/'host/tools.json').read_text());directory=self.data/'bin';directory.mkdir(exist_ok=True)
        notices=self.data/'notices';notices.mkdir(exist_ok=True)
        for name,item in tools.items():
            archive=self.data/'tool-downloads'/name
            download(item,archive)
            if name in {'ffmpeg','ffprobe'}:
                target=directory/name
                with gzip.open(archive,'rb') as stream,target.open('wb') as output:shutil.copyfileobj(stream,output)
                target.chmod(0o755)
            elif name=='node':
                with tarfile.open(archive) as tar:
                    prefix='node-'+item['version']+'-darwin-arm64/'
                    with tar.extractfile(prefix+'bin/node') as stream,(directory/'node').open('wb') as output:shutil.copyfileobj(stream,output)
                    with tar.extractfile(prefix+'LICENSE') as stream,(notices/'node-LICENSE').open('wb') as output:shutil.copyfileobj(stream,output)
                (directory/'node').chmod(0o755)
            else:shutil.copy2(archive,notices/name)

    def install(self,notes):
        try:
            hardware=self.hardware()
            if not hardware['supported']:raise ValueError('This host build needs an Apple Silicon Mac. Other computers can join an existing church host.')
            if hardware['ram_gb']<16:raise ValueError('Hosting this model needs at least 16 GB memory; 24 GB or more is recommended.')
            self.code.mkdir(parents=True,exist_ok=True)
            for source in self.resources.glob('*.py'):shutil.copy2(source,self.code/source.name)
            for folder in ('public','host'):shutil.copytree(self.resources/folder,self.code/folder,dirs_exist_ok=True)
            shutil.copy2(self.resources/'requirements.txt',self.code/'requirements.txt')
            selected=[m for m in self.models if notes or not m.get('optional')]
            remaining=sum(f['size'] for m in selected for f in m['files'] if not verified(self.model_root/(f['name'] if m.get('file') else m['id']+'/'+f['name']),f))
            if shutil.disk_usage(self.data).free<remaining+4*1024**3:raise ValueError('Free more disk space before setup. Allow about 25 GB for a new installation.')
            log=self.data/'setup.log'
            self.status(stage='tools',message='Downloading the audio and livestream tools…')
            self.tools()
            self.status(stage='dependencies',message='Installing the speech and translation tools. This can take a few minutes.')
            python=self.environment/'bin/python'
            if not python.exists():self.command([str(self.uv),'venv','--python','3.12','--managed-python',str(self.environment)],log)
            self.command([str(self.uv),'pip','install','--python',str(python),'-r',str(self.code/'requirements.txt')],log)
            total=sum(f['size'] for m in selected for f in m['files']);done=0
            self.status(stage='models',total=total,downloaded=0)
            labels={'parakeet':'English speech recognition','translategemma-12b':'Spanish and Korean translation','nllb':'Provisional captions','supertonic':'Natural male and female voices','sermon-notes':'Sermon notes','silero-v6.2.onnx':'Speech detection'}
            for model in selected:
                self.status(message='Downloading '+labels[model['id']]+'…')
                for item in model['files']:
                    target=self.model_root/(item['name'] if model.get('file') else model['id']+'/'+item['name'])
                    download(item,target,lambda count:self.status(downloaded=done+count))
                    done+=item['size'];self.status(downloaded=done)
            self.status(stage='warming',message='Starting Gather and warming up both languages…')
            # Avoid starting a second server on an occupied port, including another Gather host.
            try:
                with urllib.request.urlopen(self.state['booth_url']+'api/health',timeout=2) as response:existing=json.load(response)
            except (OSError,ValueError):existing=None
            if existing is not None:raise ValueError('Another service is already using this host port. Open the existing Gather booth or quit it before starting this host.')
            env={**os.environ,'PARAKEET_MODEL_PATH':str(self.model_root/'parakeet'),'GATHER_RUNTIME_DIR':str(self.data/'runtime'),'PATH':str(self.data/'bin')+os.pathsep+os.environ.get('PATH','')}
            self.backend_log=(self.data/'host.log').open('ab')
            self.process=subprocess.Popen([str(python),'-m','uvicorn','server:app','--host','0.0.0.0','--port',str(self.port),'--no-access-log','--timeout-graceful-shutdown','3'],cwd=self.code,env=env,stdout=self.backend_log,stderr=subprocess.STDOUT)
            deadline=time.time()+180
            while time.time()<deadline:
                if self.process.poll() is not None:raise ValueError('Gather could not start. Retry setup; diagnostic details are in the local host log.')
                try:
                    with urllib.request.urlopen(self.state['booth_url']+'api/health',timeout=2) as response:health=json.load(response)
                    if health.get('error'):raise ValueError(health['error'])
                    if health.get('ready'):
                        (self.data/'installed.json').write_text(json.dumps({'notes':notes}))
                        self.status(stage='ready',busy=False,message='Your host is ready. Open the booth, choose your audio feed, and share its listening link.');return
                except urllib.error.URLError:pass
                time.sleep(1)
            raise ValueError('The models took longer than expected to warm up. Check the host log, then retry.')
        except Exception as error:
            self.close();self.process=None
            self.status(stage='error',busy=False,error=str(error) if isinstance(error,ValueError) else 'Setup could not finish. Check your connection and retry; downloaded models are retained.',message='Setup paused. Your completed model downloads are retained.')

    def close(self):
        if self.tool_process and self.tool_process.poll() is None:self.tool_process.terminate()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:self.process.kill()

def serve(setup,port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def safe(self):
            host=self.headers.get('Host','')
            origin=self.headers.get('Origin')
            return host in {f'127.0.0.1:{port}',f'localhost:{port}'} and (not origin or origin in {f'http://127.0.0.1:{port}',f'http://localhost:{port}'})
        def send(self,status,body,kind='application/json'):
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'");self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if not self.safe():return self.send(403,b'{}')
            if self.path=='/api/setup':return self.send(200,json.dumps({**setup.view(),'token':setup.token}).encode())
            pages={'/':('setup.html','text/html; charset=utf-8'),'/setup.js':('setup.js','text/javascript'),'/setup.css':('setup.css','text/css')}
            if self.path not in pages:return self.send(404,b'{}')
            name,kind=pages[self.path];self.send(200,(setup.resources/'host'/name).read_bytes(),kind)
        def do_POST(self):
            if not self.safe() or not secrets.compare_digest(self.headers.get('x-setup-token',''),setup.token):return self.send(403,b'{}')
            if self.path!='/api/install':return self.send(404,b'{}')
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<1024:raise ValueError()
                data=json.loads(self.rfile.read(length))
                if not isinstance(data.get('notes'),bool):raise ValueError()
            except (ValueError,TypeError):return self.send(422,b'{}')
            started=setup.start(data['notes']);self.send(202 if started else 409,json.dumps({'started':started}).encode())
    http=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    return http

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--resources',type=Path,required=True);parser.add_argument('--uv',type=Path,required=True);parser.add_argument('--data',type=Path,default=Path.home()/'Library/Application Support/Gather');parser.add_argument('--setup-port',type=int,default=47681);parser.add_argument('--host-port',type=int,default=47680);parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    setup=Setup(args.resources,args.data,args.uv,args.host_port);http=serve(setup,args.setup_port)
    if (args.data/'installed.json').exists():setup.start(json.loads((args.data/'installed.json').read_text()).get('notes',False))
    def stop_signal(*_):
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop_signal)
    if not args.no_browser:webbrowser.open(f'http://127.0.0.1:{args.setup_port}/')
    try:http.serve_forever()
    except KeyboardInterrupt:pass
    finally:setup.close();http.server_close()

if __name__=='__main__':main()
