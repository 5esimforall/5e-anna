from contextlib import closing, ExitStack
from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import signal
import sqlite3
import subprocess
import time
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import up
from up import validate_bundle,create_server
from linked_book import build_book
import test_linked_book as fixtures

class ServerTests(unittest.TestCase):
    def request(self,server,path,host=None):
        with closing(HTTPConnection('127.0.0.1',server.server_port)) as c:
            c.request('GET',path,headers={'Host':host} if host else {})
            r=c.getresponse();return r.status,r.getheader('Location'),r.read()
    def test_single_port_routes_and_no_wrong_target_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=fixtures.LinkedBuildTests().fixture(root);book=root/'book'
            (source/'topics/literal%2e.css').write_text('literal percent path')
            m,_=build_book(source,db,book)
            server=create_server(db,book,m,0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                self.assertEqual(self.request(server,'/book/definition/ver_test')[:2],(302,'/book/topics/test.htm#anna-ver_test'))
                self.assertEqual(self.request(server,'/read')[0],200)
                for old_path in ('/topics/test.htm', '/definition/ver_test', '/anna/entry/ver_test', '/book/assets/webhelp-shell.js'):
                    self.assertEqual(self.request(server,old_path)[0],404)
                reader=json.loads(self.request(server,'/api/reader')[2])
                self.assertEqual(reader['content_base'],'/book/')
                self.assertIsNone(reader['source_release'])
                # One bundle binds the query page's snapshot to the reader's model.
                handler=object.__new__(up.AnnaHandler);handler.current=server.bundle
                self.assertEqual((up.AnnaHandler.reader_url,handler.snapshot),('/read',reader['snapshot']))
                target=json.loads(self.request(server,'/api/reader-page?entry=ver_test')[2])
                self.assertEqual((target['path'],target['fragment']),('topics/test.htm','anna-ver_test'))
                self.assertEqual(self.request(server,'/api/reader-page?entry=ver_test&path=topics/test.htm')[0],400)
                self.assertEqual(self.request(server,'/api/reader-page?path=../style.css')[0],404)
                self.assertEqual(self.request(server,'/api/reader-page?entry=ver_test&snapshot=old')[0],409)
                self.assertEqual(self.request(server,'/book/anna/entry/ver_test')[0],404)
                self.assertEqual(self.request(server,'/book/api/reader-context')[0],404)
                self.assertEqual(self.request(server,'/__original/index.htm')[0],404)
                for p in ['/book/definition/missing','/book/anna/entry/missing','/book/../catalog.sqlite','/book/anna-manifest.json','/book/anna-reader.json',
                          '/book/','/book/index.htm','/book/index.html','/book/indexh.htm','/book/webhelpsearch.htm','/book/webhelpcontents.htm','/book/webhelpframe.htm']:
                    self.assertEqual(self.request(server,p)[0],404)
                self.assertEqual(self.request(server,'/book/topics/test.htm',host='attacker.example')[0],403)
                self.assertIn(b'anna-bindings',self.request(server,'/book/topics/test.htm')[2])
                self.assertEqual(self.request(server,'/book/topics/literal%252e.css')[2],b'literal percent path')
                with closing(HTTPConnection('127.0.0.1', server.server_port)) as c:
                    c.request('GET', '/book/topics/style.css')
                    response = c.getresponse(); etag = response.getheader('ETag'); response.read()
                    self.assertTrue(etag)
                    self.assertEqual(response.getheader('Cache-Control'), 'private, no-cache')
                    c.request('GET', '/book/topics/style.css', headers={'If-None-Match': etag})
                    response = c.getresponse()
                    self.assertEqual(response.status, 304); self.assertEqual(response.read(), b'')
                    c.request('GET', '/book/topics/style.css', headers={'If-None-Match': '"different"'})
                    response = c.getresponse()
                    self.assertEqual(response.status, 200); self.assertTrue(response.read())
                # A running reader cannot combine a rebuilt book with its old navigation/model.
                (book/'anna-manifest.json').write_text('{}')
                self.assertEqual(self.request(server,'/read')[0],200)
                status,_,body=self.request(server,'/api/reader')
                self.assertEqual((status,json.loads(body)),(409,{'code':'bundle_changed'}))
                self.assertEqual(self.request(server,'/book/topics/test.htm')[0],409)
                with closing(HTTPConnection('127.0.0.1', server.server_port)) as c:
                    c.request('GET', '/book/topics/style.css', headers={'If-None-Match': etag})
                    response = c.getresponse()
                    self.assertEqual(response.status, 409); response.read()
            finally:
                server.shutdown();server.server_close();thread.join()
    def test_bundle_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=fixtures.LinkedBuildTests().fixture(root);book=root/'book'
            m,_=build_book(source,db,book);self.assertEqual(validate_bundle(source,db,book),m)
            (book/'topics/style.css').write_text('changed')
            with self.assertRaisesRegex(ValueError,'modified'):validate_bundle(source,db,book)
    def test_busy_port_keeps_the_bind_error(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=fixtures.LinkedBuildTests().fixture(root);book=root/'book'
            m,_=build_book(source,db,book)
            server=create_server(db,book,m,0)
            try:
                # A failed bind must report the port, not a half-initialised server.
                with self.assertRaises(OSError) as caught:
                    create_server(db,book,m,server.server_port)
                self.assertIn('Address already in use',str(caught.exception))
            finally:
                server.server_close()

    def test_obsolete_port_arguments_are_rejected(self):
        for argument in ('--anna-port', '--book-port'):
            result=subprocess.run([sys.executable, 'scripts/up.py', argument, '0'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('unrecognized arguments',result.stderr)

    def test_sigint_and_sigterm_stops_single_server(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig), tempfile.TemporaryDirectory() as td:
                root=Path(td);source,db=fixtures.LinkedBuildTests().fixture(root);book=root/'book'
                build_book(source,db,book)
                code="import sys; from pathlib import Path; import up; up.search.CACHE=Path(sys.argv.pop(1)); up.main()"
                script=Path(__file__).resolve().parents[1]/'scripts'
                with (root/'log').open('w+') as log:
                    process=subprocess.Popen([sys.executable,'-c',code,str(root/'search.pkl'),'--source',str(source),'--db',str(db),'--book',str(book),'--port','0'],cwd=script,stdout=log,stderr=log)
                    try:
                        deadline=time.monotonic()+10
                        output=''
                        while time.monotonic()<deadline:
                            log.seek(0);output=log.read()
                            if 'Ctrl-C' in output or process.poll() is not None:break
                            time.sleep(.02)
                        self.assertIn('Ctrl-C',output,output)
                        self.assertIn('Anna:',output)
                        self.assertIn('Stella:',output)
                        ports=[int(line.split(':')[-1].rstrip('/')) for line in output.splitlines() if line.startswith(('Anna:',))]
                        self.assertEqual(len(ports),1)
                        for path in ('/', '/read', '/book/topics/test.htm'):
                            port=ports[0]
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
            server=create_server(db,book,m,0)
            t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            try:
                self.assertEqual(self.request(server,'/book/definition/ver_test')[0],422)
                self.assertEqual(self.request(server,'/book/anna/entry/ver_test')[0],404)
            finally:
                server.shutdown()
                server.server_close()
                t.join()

class WatchTests(unittest.TestCase):
    def request(self,server,path):
        with closing(HTTPConnection('127.0.0.1',server.server_port)) as c:
            c.request('GET',path);r=c.getresponse();return r.status,r.read()
    def snapshot(self,server):
        status,body=self.request(server,'/api/reader')
        return json.loads(body)['snapshot'] if status==200 else None
    def wait_for(self,predicate,timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate():return True
            time.sleep(.05)
        return False
    def rebuilt_database(self,db):
        # build.py publishes the database before the linked book; this leaves a database the old book cannot explain.
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("UPDATE metadata SET value=? WHERE key='summary'",(json.dumps({'full_scan':True,'rebuilt':True}),));conn.commit()
    def start(self,root,source,db,book,manifest):
        stack=ExitStack()
        stack.enter_context(patch.object(up.search,'CACHE',root/'search.pkl'))
        stack.enter_context(patch.object(up.search,'WEB',source))
        server=create_server(db,book,manifest,0,watch=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(stack.close);self.addCleanup(thread.join)
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return server
    def test_rebuilt_bundle_is_swapped_without_restart(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=fixtures.LinkedBuildTests().fixture(root);book=root/'book'
            manifest,_=build_book(source,db,book)
            server=self.start(root,source,db,book,manifest)
            before=self.snapshot(server);self.assertTrue(before)
            self.rebuilt_database(db)
            build_book(source,db,book)
            self.assertTrue(self.wait_for(lambda:self.snapshot(server) not in (None,before)),'service did not swap in the rebuilt bundle')
            self.assertEqual(self.snapshot(server),json.loads((book/'anna-reader.json').read_text())['snapshot'])
            self.assertIsNone(server.reload_error)
            self.assertEqual(server.bundle.snapshot,self.snapshot(server))
            handler=object.__new__(up.AnnaHandler);handler.current=server.bundle
            self.assertEqual((handler.snapshot,handler.search_entries),(server.bundle.snapshot,server.bundle.entries))
            self.assertEqual(self.request(server,'/book/topics/test.htm')[0],200)
            self.assertEqual(self.request(server,'/api/reader-page?entry=ver_test&snapshot=stale')[0],409)
    def test_half_published_rebuild_stays_409_without_a_false_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=fixtures.LinkedBuildTests().fixture(root);book=root/'book'
            manifest,_=build_book(source,db,book)
            server=self.start(root,source,db,book,manifest)
            before=self.snapshot(server);self.assertTrue(before)
            self.rebuilt_database(db)
            self.assertTrue(self.wait_for(lambda:self.request(server,'/api/reader')[0]==409),'service served a database the old book cannot explain')
            status,body=self.request(server,'/api/reader')
            self.assertEqual((status,json.loads(body)['code']),(409,'reloading'))
            self.assertIsNone(server.reload_error)
            self.assertEqual(self.request(server,'/book/topics/test.htm')[0],409)
            # Publishing the linked book completes the pair and swaps it in.
            build_book(source,db,book)
            self.assertTrue(self.wait_for(lambda:self.snapshot(server) not in (None,before)))
            self.assertIsNone(server.reload_error)
    def test_invalid_manifest_reports_reload_failed_and_recovers(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=fixtures.LinkedBuildTests().fixture(root);book=root/'book'
            manifest,_=build_book(source,db,book)
            server=self.start(root,source,db,book,manifest)
            before=self.snapshot(server);self.assertTrue(before)
            (book/'anna-manifest.json').write_text('{}')
            self.assertTrue(self.wait_for(lambda:self.request(server,'/api/reader')[0]==409 and server.reload_error),'invalid manifest was not reported')
            status,body=self.request(server,'/api/reader')
            self.assertEqual((status,json.loads(body)['code']),(409,'reload_failed'))
            self.assertTrue(json.loads(body)['error'])
            self.assertEqual(self.request(server,'/book/topics/test.htm')[0],409)
            # A complete rebuild publishes a valid manifest and recovers the same service.
            build_book(source,db,book)
            self.assertTrue(self.wait_for(lambda:self.request(server,'/api/reader')[0]==200))
            self.assertEqual(self.snapshot(server),before)
            self.assertIsNone(server.reload_error)
