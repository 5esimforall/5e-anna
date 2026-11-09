"""Dedicated, read-only browser-test stack. Never reuses a user's development server."""
from contextlib import closing
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
from urllib.parse import urlsplit

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT/'scripts'))
from html_extract import Document
from linked_book import BOOK,inject,text_slice
from paths import WEB
import up
import book_search as search

PORT=int(os.environ.get('ANNA_E2E_PORT','18380'))
DB=Path(os.environ.get('ANNA_E2E_DB',up.serve.DB)).resolve()
BOOK=Path(os.environ.get('ANNA_E2E_BOOK',BOOK)).resolve()


def fixture():
    raw=Path(__file__).with_name('fixture.html').read_bytes()
    doc=Document('fixture.html',raw.decode())
    definitions=[]
    for eid,tag in [('alpha',doc.soup.select_one('#Alpha')),('cell',doc.soup.select_one('#cell')),('beta',doc.soup.select_one('#Beta'))]:
        spec=text_slice(doc,*doc.span(tag))
        definitions.append({'id':eid,'name':tag.get_text(),'body':spec,'title':spec})
    ref=text_slice(doc,*doc.span(doc.soup.select_one('a')))
    linked=inject(raw,{'definitions':definitions,'references':[{'id':'alpha','range':ref}]})
    doc.close();return raw,linked


def cases(manifest):
    with closing(sqlite3.connect(DB)) as db:
        spell=json.loads(db.execute("SELECT data FROM entries WHERE name_en='Shield' AND ruleset='5r' AND page_path='topics/玩家手册2024/法术详述/1环.htm'").fetchone()[0])
        row=db.execute("SELECT m.data,e.id FROM concept_observations m JOIN entries e ON e.page_path=m.page_path AND m.start>=e.start AND m.end<=e.end WHERE m.status='resolved' AND m.signal='format' AND e.type='spell' AND m.page_path='topics/玩家手册2024/法术详述/1环.htm' ORDER BY m.start LIMIT 1").fetchone()
        mention=json.loads(row[0]);mention['entry_id']=row[1]
        mention['target']=manifest['entries'][mention['target_id']]
        core=[]
        for name,signal in [('Sharpshooter','plain'),('Spell Sniper','format')]:
            feat=db.execute("SELECT id,page_path,start,end FROM entries WHERE name_en=? AND type='feat' AND ruleset='5r' AND page_path='topics/玩家手册2024/专长/通用专长.htm'",(name,)).fetchone()
            refs=[json.loads(r[0]) for r in db.execute(
                "SELECT data FROM effective_references WHERE page_path=? AND signal=? AND start>=? AND end<=? AND json_extract(data,'$.text') IN ('半身掩护','四分之三掩护') ORDER BY start",
                (feat[1],signal,feat[2],feat[3]))]
            core.append({'entry_id':feat[0],'path':feat[1],'references':[
                {**ref,'target':manifest['entries'].get(ref['target_id'])} for ref in refs]})
        old_path='topics/玩家手册/战斗/掩护.htm'
        old_cover=json.loads(db.execute(
            "SELECT data FROM effective_references WHERE page_path=? AND signal='plain' AND json_extract(data,'$.text')='半身掩护' ORDER BY start LIMIT 1",
            (old_path,)).fetchone()[0])
        old_cover['target']=manifest['entries'].get(old_cover['target_id'])
    path='topics/速查/合作万器大全.htm'
    raw=(BOOK/path).read_text()
    data=json.loads(raw.split('id="anna-bindings">',1)[1].split('</script>',1)[0])
    reference=data['references'][0]
    return {'spell':{'id':spell['id'],'path':spell['page_path'],'anchor':'Shield','name':spell['name']},
            'reference':{'path':path,'id':reference['id'],'text':reference['range']['text'],**manifest['entries'][reference['id']]},
            'reference_source':path,'mention':mention,'core_rules':core,'classic_cover':old_cover}


def main():
    manifest=up.validate_bundle(WEB,DB,BOOK)
    data=json.dumps(cases(manifest),ensure_ascii=False).encode()
    raw,linked=fixture()
    class OriginalHandler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(WEB),**kwargs)
        def log_message(self,*args):pass
        def do_GET(self):
            if self.headers.get('Host','') not in {f'127.0.0.1:{PORT}',f'localhost:{PORT}'}:self.send_error(403);return
            path=urlsplit(self.path).path
            special={'/__ready':(b'OK','text/plain'),'/__cases':(data,'application/json'),
                     '/__fixture__/source':(raw,'text/html; charset=utf-8'),'/__fixture__/linked':(linked,'text/html; charset=utf-8'),
                     '/book/anna-assets/links.js':((PROJECT/'book/links.js').read_bytes(),'text/javascript'),
                     '/book/anna-assets/link-labels.js':((PROJECT/'web/link-labels.js').read_bytes(),'text/javascript')}
            if path in special:
                body,kind=special[path];self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
            if any(x.startswith('.') for x in path.split('/') if x):self.send_error(404);return
            super().do_GET()
        def list_directory(self,path):self.send_error(404);return None
    servers=[];threads=[];stop=threading.Event()
    try:
        servers=[up.create_server(DB,BOOK,manifest,PORT+1)]
        servers.append(ThreadingHTTPServer(('127.0.0.1',PORT),OriginalHandler))
        search.WEB=WEB;search.CACHE=PROJECT/'.cache/browser-search.pkl'
        servers[0].RequestHandlerClass.search_entries=search.build_index(quiet=True)
        for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
        for server in servers:
            t=threading.Thread(target=server.serve_forever,daemon=True);t.start();threads.append(t)
        print('Browser test stack ready',flush=True);stop.wait()
    finally:
        for s,t in zip(servers,threads):
            if t.is_alive():s.shutdown()
        for s in servers:s.server_close()
        for t in threads:t.join()

if __name__=='__main__':main()
