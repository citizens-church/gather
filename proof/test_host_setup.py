import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from setup_server import download, verified, Setup, serve

class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.body=b'verified model data'*1200
        body=self.body;self.ranges=[];ranges=self.ranges
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                offset=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0]);ranges.append(offset)
                self.send_response(206 if offset else 200)
                if offset:self.send_header('Content-Range',f'bytes {offset}-{len(body)-1}/{len(body)}')
                self.end_headers();self.wfile.write(body[offset:])
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.item={'url':f'http://127.0.0.1:{self.http.server_port}/model','size':len(body),'sha256':hashlib.sha256(body).hexdigest()}
        self.path=Path(self.tmp.name)/'model.bin'
    def tearDown(self):self.http.shutdown();self.http.server_close();self.tmp.cleanup()
    def test_resume_and_reuse_verified_download(self):
        self.path.with_suffix('.bin.partial').write_bytes(self.body[:7000])
        download(self.item,self.path)
        self.assertEqual(self.ranges,[7000]);self.assertTrue(verified(self.path,self.item))
        download(self.item,self.path);self.assertEqual(self.ranges,[7000])
    def test_bad_checksum_is_not_exposed_as_model(self):
        with self.assertRaises(ValueError):download({**self.item,'sha256':'0'*64},self.path)
        self.assertFalse(self.path.exists());self.assertFalse(self.path.with_suffix('.bin.partial').exists())
    def test_completed_partial_is_verified_without_redownload(self):
        self.path.with_suffix('.bin.partial').write_bytes(self.body)
        download(self.item,self.path);self.assertEqual(self.ranges,[]);self.assertTrue(self.path.exists())

class SetupApiTests(unittest.TestCase):
    def test_cross_origin_and_missing_token_cannot_start_install(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            setup=Setup(root,Path(directory),Path('/missing-uv'))
            http=serve(setup,0)
            # The handler validates the configured port, so construct the client Host explicitly.
            threading.Thread(target=http.serve_forever,daemon=True).start()
            base=f'http://127.0.0.1:{http.server_port}'
            try:
                for headers in ({'Host':'127.0.0.1:0'},{'Host':'127.0.0.1:0','x-setup-token':setup.token,'Origin':'https://unrelated.example'}):
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        urllib.request.urlopen(urllib.request.Request(base+'/api/install',data=b'{"notes":false}',headers=headers))
                    self.assertEqual(raised.exception.code,403)
                self.assertFalse(setup.state['busy'])
            finally:http.shutdown();http.server_close()

if __name__=='__main__':unittest.main()
