from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import signal
import subprocess
import time
import sys
import tempfile
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from up import validate_bundle,create_servers
from linked_book import build_book
import test_linked_book as fixtures

class StackTests(unittest.TestCase):
    def request(self,server,path,host=None):
        with closing(HTTPConnection('127.0.0.1',server.server_port)) as c:
            c.request('GET',path,headers={'Host':host} if host else {})
            r=c.getresponse();return r.status,r.getheader('Location'),r.read()
    def test_routes_ports_and_no_wrong_target_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=fixtures.LinkedBuildTests().fixture(root);book=root/'book'
            m,_=build_book(source,db,book)
            servers=create_servers(db,book,m,0,0);ts=[]
            try:
                for s in servers:
                    t=threading.Thread(target=s.serve_forever,daemon=True);t.start();ts.append(t)
                self.assertNotEqual(servers[0].server_port,servers[1].server_port)
                self.assertEqual(self.request(servers[1],'/definition/ver_test')[:2],(302,'/topics/test.htm#anna-ver_test'))
                self.assertIn('index.htm?page=',self.request(servers[1],'/definition/ver_test?reader=1')[1])
                self.assertEqual(self.request(servers[1],'/anna/entry/ver_test')[1],f'http://127.0.0.1:{servers[0].server_port}/?entry=ver_test')
                for p in ['/definition/missing','/anna/entry/missing','/../catalog.sqlite','/anna-manifest.json']:
                    self.assertEqual(self.request(servers[1],p)[0],404)
                self.assertEqual(self.request(servers[1],'/topics/test.htm',host='attacker.example')[0],403)
                self.assertIn(b'anna-bindings',self.request(servers[1],'/topics/test.htm')[2])
            finally:
                for s in servers:s.shutdown();s.server_close()
                for t in ts:t.join()
    def test_bundle_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=fixtures.LinkedBuildTests().fixture(root);book=root/'book'
            m,_=build_book(source,db,book);self.assertEqual(validate_bundle(source,db,book),m)
            (book/'style.css').write_text('changed')
            with self.assertRaisesRegex(ValueError,'修改'):validate_bundle(source,db,book)
    def test_second_port_failure_releases_first(self):
        with closing(socket.socket()) as occupied:
            occupied.bind(('127.0.0.1',0));occupied.listen()
            with closing(socket.socket()) as available:
                available.bind(('127.0.0.1',0));port=available.getsockname()[1]
            with self.assertRaises(OSError):create_servers('/none','/none',{'entries':{}},port,occupied.getsockname()[1])
            with closing(socket.socket()) as probe:probe.bind(('127.0.0.1',port))
    def test_duplicate_ports_rejected(self):
        with self.assertRaises(ValueError):create_servers('/none','/none',{},8123,8123)

    def test_sigint_and_sigterm_stop_both_endpoints(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig), tempfile.TemporaryDirectory() as td:
                root=Path(td);source,db=fixtures.LinkedBuildTests().fixture(root);book=root/'book'
                (source/'webhelpsearch.htm').write_text('<script>var API_BASE = "https://5echmsearch.kagangtuya.top";</script>')
                build_book(source,db,book)
                code="import sys; from pathlib import Path; import up; up.search.CACHE=Path(sys.argv.pop(1)); up.main()"
                script=Path(__file__).resolve().parents[1]/'scripts'
                with (root/'log').open('w+') as log:
                    process=subprocess.Popen([sys.executable,'-c',code,str(root/'search.pkl'),'--source',str(source),'--db',str(db),'--book',str(book),'--anna-port','0','--book-port','0'],cwd=script,stdout=log,stderr=log)
                    try:
                        deadline=time.monotonic()+10
                        output=''
                        while time.monotonic()<deadline:
                            log.seek(0);output=log.read()
                            if 'Ctrl-C' in output or process.poll() is not None:break
                            time.sleep(.02)
                        self.assertIn('Ctrl-C',output,output)
                        ports=[int(line.split(':')[-1].rstrip('/')) for line in output.splitlines() if line.startswith(('anna:','5echm-anna:'))]
                        self.assertEqual(len(ports),2)
                        for port,path in zip(ports,('/', '/topics/test.htm')):
                            with closing(HTTPConnection('127.0.0.1',port,timeout=2)) as c:
                                c.request('GET',path);r=c.getresponse();self.assertEqual(r.status,200);r.read()
                        process.send_signal(sig);self.assertEqual(process.wait(timeout=5),0)
                        for port in ports:
                            with closing(socket.socket()) as probe:self.assertNotEqual(probe.connect_ex(('127.0.0.1',port)),0)
                    finally:
                        if process.poll() is None:process.kill();process.wait()

    def test_missing_content_is_explicit_not_wrong_page_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=fixtures.LinkedBuildTests().fixture(root);book=root/'book'
            m,_=build_book(source,db,book);m['entries']['ver_test']['locatable']=False
            servers=create_servers(db,book,m,0,0)
            t=threading.Thread(target=servers[1].serve_forever,daemon=True);t.start()
            try:
                self.assertEqual(self.request(servers[1],'/definition/ver_test')[0],422)
                self.assertEqual(self.request(servers[1],'/anna/entry/ver_test')[0],302)
            finally:
                servers[1].shutdown()
                for s in servers:s.server_close()
                t.join()
