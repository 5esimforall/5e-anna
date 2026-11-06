#!/usr/bin/env python3
"""Run anna and its linked book as one localhost-only lifecycle."""
import argparse
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import signal
import threading
from urllib.parse import parse_qs,quote,unquote,urlsplit
from linked_book import BOOK,fingerprint
from paths import PROJECT,WEB
import serve
import serve_5echm as search


def validate_bundle(source,db,book):
    source=Path(source).resolve();book=Path(book).resolve()
    manifest_path=book/'anna-manifest.json'
    if not manifest_path.is_file():raise ValueError('链接版不存在，请运行 make build')
    m=json.loads(manifest_path.read_text())
    if m['source_root']!=str(source) or not Path(db).is_file() or fingerprint(db)!=m['database_sha256']:
        raise ValueError('数据库与链接版不匹配，请运行 make build')
    actual={p.relative_to(source).as_posix():p for p in source.rglob('*') if p.is_file() and '.git' not in p.relative_to(source).parts and not p.is_symlink()}
    if set(actual)!=set(m['inputs']) or any(fingerprint(actual[p])!=h for p,h in m['inputs'].items()):
        raise ValueError('原文快照已变化，请运行 make build')
    for p,h in m['outputs'].items():
        f=book/p
        if not f.is_file() or fingerprint(f)!=h:raise ValueError('链接版文件缺失或被修改，请运行 make build：'+p)
    return m


def make_book_handler(book,manifest,anna_port):
    class BookHandler(search.Handler):
        def __init__(self,*args,**kwargs):
            SimpleHTTPRequestHandler.__init__(self,*args,directory=str(book),**kwargs)
        def redirect(self,url):
            self.send_response(302);self.send_header('Location',url);self.send_header('Content-Length','0');self.end_headers()
        def list_directory(self,path):
            self.send_error(404);return None
        def do_GET(self):
            if self.headers.get('Host','') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
                self.send_error(403);return
            u=urlsplit(self.path);path=unquote(u.path)
            if path.startswith('/definition/') or path.startswith('/anna/entry/'):
                eid=path.rsplit('/',1)[-1]
                entry=manifest['entries'].get(eid)
                if not entry:self.send_error(404,'Unknown entry');return
                if path.startswith('/anna/'):
                    self.redirect(f'http://127.0.0.1:{anna_port}/?entry='+quote(eid));return
                target=quote(entry['path'],safe='/')+'#'+quote(entry['fragment'])
                if parse_qs(u.query).get('reader')==['1']:
                    target='index.htm?page='+quote(target,safe='')
                self.redirect('/'+target);return
            if any(p.startswith('.') or p=='..' for p in path.split('/') if p) or path.startswith('/anna-manifest'):
                self.send_error(404);return
            resolved=(Path(book)/path.lstrip('/')).resolve()
            if not resolved.is_relative_to(Path(book).resolve()):self.send_error(404);return
            super().do_GET()
        def do_HEAD(self):
            # Apply the same origin/path checks as GET (no privileged alternate method).
            self.send_error(405)
    return BookHandler


def create_servers(db,book,manifest,anna_port,book_port):
    if anna_port==book_port and anna_port!=0:raise ValueError('两个站点必须使用不同端口')
    class AnnaHandler(serve.Handler):pass
    AnnaHandler.db_path=Path(db)
    first=ThreadingHTTPServer(('127.0.0.1',anna_port),AnnaHandler)
    try:
        BookHandler=make_book_handler(book,manifest,first.server_port)
        second=ThreadingHTTPServer(('127.0.0.1',book_port),BookHandler)
    except BaseException:
        first.server_close();raise
    AnnaHandler.book_url=f'http://127.0.0.1:{second.server_port}'
    return first,second


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--anna-port',type=int,default=8001);p.add_argument('--book-port',type=int,default=8002)
    p.add_argument('--db',type=Path,default=serve.DB);p.add_argument('--book',type=Path,default=BOOK);p.add_argument('--source',type=Path,default=WEB)
    a=p.parse_args();servers=[];threads=[];stop=threading.Event()
    try:
        manifest=validate_bundle(a.source,a.db,a.book)
        servers=list(create_servers(a.db,a.book,manifest,a.anna_port,a.book_port))
        # All ports are reserved before starting either endpoint.
        search.WEB=a.source.resolve()
        servers[1].RequestHandlerClass.entries=search.build_index(quiet=True)
        servers[1].RequestHandlerClass.patched_search_page=search.build_patched_search_page()
        for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
        for server in servers:
            t=threading.Thread(target=server.serve_forever,daemon=True);t.start();threads.append(t)
        print(f'anna: http://127.0.0.1:{servers[0].server_port}/\n5echm-anna: http://127.0.0.1:{servers[1].server_port}/\nCtrl-C 同时停止两个站点。',flush=True)
        while not stop.wait(.5):
            if not all(t.is_alive() for t in threads):raise RuntimeError('站点服务意外退出')
    except (OSError,ValueError,RuntimeError) as exc:p.exit(1,str(exc)+'\n')
    finally:
        for server,t in zip(servers,threads):
            if t.is_alive():server.shutdown()
        for server in servers:server.server_close()
        for t in threads:t.join()

if __name__=='__main__':main()
