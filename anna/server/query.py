"""Loopback-only HTTP transport for the read-only presentation services."""
from contextlib import closing
import html
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import anna.paths as paths
import anna.catalog.views as browse
from anna.presentation import catalog, knowledge
from anna.presentation.layout import entry_layout
from anna.presentation.references import query_missing_definitions
from anna.review.search import search_candidates

WEB = paths.PROJECT / 'web'


class Handler(BaseHTTPRequestHandler):
    db_path=catalog.DB
    reader_url=None
    # The build snapshot the query page shares with the reader; None when the query page runs alone.
    snapshot=None
    db_signature=None
    source_root=paths.WEB
    def log_message(self,*args): pass
    def send(self,body,kind='application/json; charset=utf-8',status=200,download=False):
        if not isinstance(body,bytes): body=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        frames="'self'" if urlsplit(self.path).path=='/read' and self.reader_url else "'none'"
        self.send_header('Content-Security-Policy',f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; frame-src {frames}")
        if download: self.send_header('Content-Disposition','attachment; filename="'+(download if isinstance(download,str) else '5e-anna.csv')+'"')
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        # Reject DNS-rebinding hosts; never expose local data to a remote origin.
        if self.headers.get('Host','') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
            self.send({'error':'仅限本机访问'},status=403); return
        url=urlsplit(self.path); p=parse_qs(url.query)
        try:
            if self.db_signature is not None:
                stat=Path(self.db_path).stat()
                if (stat.st_ino,stat.st_size,stat.st_mtime_ns)!=self.db_signature:
                    self.send({'error':'数据库已重建，请重启 make up 以同步链接版。'},status=409);return
            static={'/link-labels.js':('link-labels.js','text/javascript; charset=utf-8'),'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/detail.js':('detail.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
            static['/review']=('review.html','text/html; charset=utf-8')
            static['/review-ui.js']=('review-ui.js','text/javascript; charset=utf-8')
            static['/favicon.ico']=('favicon.ico','image/x-icon')
            static['/anna-logo.png']=('anna-logo.png','image/png')
            static.update({f'/{name}.js':(f'{name}.js','text/javascript; charset=utf-8') for name in ('theme','kit','menu','topbar','workspace','companions','knowledge')})
            static['/read']=('reader.html','text/html; charset=utf-8')
            static['/stella-logo.png']=('stella-logo.png','image/png')
            static['/stella-favicon.ico']=('stella-favicon.ico','image/x-icon')
            static.update({f'/reader.{ext}':(f'reader.{ext}',kind) for ext,kind in [('js','text/javascript; charset=utf-8'),('css','text/css; charset=utf-8')]})
            static.update({f'/reader-{name}.js':(f'reader-{name}.js','text/javascript; charset=utf-8') for name in ('labels','model','flow')})
            static.update({f'/favicon-{size}.png':(f'favicon-{size}.png','image/png') for size in (16,32,48)})
            if url.path in static:
                file,kind=static[url.path]; self.send((WEB/file).read_bytes(),kind); return
            with closing(catalog.connect(self.db_path)) as db:
                if url.path=='/api/meta': self.send({**catalog.meta(db),'reader_url':self.reader_url,'snapshot':self.snapshot})
                elif url.path=='/api/facets': self.send(catalog.facets(db,p))
                elif url.path=='/api/entries': self.send(catalog.query(db,p))
                elif url.path=='/api/entry': self.send(catalog.detail(db,catalog.first(p,'id'),catalog.first(p,'chain_spell') or None,catalog.first(p,'chain_path') or None,catalog.first(p,'chain_effect') or None))
                elif url.path=='/api/knowledge-chain': self.send(knowledge.chain(db,catalog.first(p,'id'),catalog.first(p,'spell') or None,catalog.first(p,'path') or None,catalog.first(p,'effect') or None))
                elif url.path=='/api/knowledge-export': self.send(knowledge.export(db,catalog.first(p,'id'),catalog.first(p,'spell') or None,catalog.first(p,'path') or None,catalog.first(p,'effect') or None),download='anna-rule-chain.json')
                elif url.path=='/api/casting-facets': self.send(catalog.casting_facets(db,p))
                elif url.path=='/api/entry-layout': self.send(entry_layout(db,catalog.first(p,'id'),Path(self.source_root)))
                elif url.path=='/api/reference-candidates': self.send(search_candidates(db,catalog.first(p,'id'),query=catalog.first(p,'q'),
                    entry_type=catalog.first(p,'type'),ruleset=catalog.first(p,'ruleset'),source_id=catalog.first(p,'source'),
                    limit=int(catalog.first(p,'limit','20')),offset=int(catalog.first(p,'offset','0'))))
                elif url.path=='/api/missing-definitions': self.send(query_missing_definitions(db,status=catalog.first(p,'status'),path=catalog.first(p,'path'),
                    limit=int(catalog.first(p,'limit','50')),offset=int(catalog.first(p,'offset','0'))))
                elif url.path=='/api/export': self.send(catalog.csv_export(catalog.query(db,p,export=True)['rows'],browse.schema_for(catalog.entry_types(p))),'text/csv; charset=utf-8',download=True)
                elif url.path=='/source':
                    row=db.execute('SELECT text,sha256 FROM pages WHERE path=?',(catalog.first(p,'path'),)).fetchone()
                    if not row: raise LookupError('页面不存在')
                    if catalog.first(p,'sha256') and catalog.first(p,'sha256') != row[1]:
                        self.send({'error':'source_snapshot_mismatch','path':catalog.first(p,'path')},status=409); return
                    text=row[0]; start=max(0,min(len(text),int(catalog.first(p,'start','0')))); end=max(start,min(len(text),int(catalog.first(p,'end',str(start)))))
                    body='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>页面文本 · 5e Anna</title><script src="/kit.js"></script><script src="/theme.js"></script><link rel="icon" href="/favicon.ico"><link rel="stylesheet" href="/style.css"><main class="source-page"><a href="/">← 条目查询</a><h1>页面文本</h1><p>提取后的文本；高亮为所选证据范围。</p><p>'+html.escape(catalog.first(p,'path'))+'</p><pre>'+html.escape(text[:start])+'<mark id="evidence">'+html.escape(text[start:end])+'</mark>'+html.escape(text[end:])+'</pre></main></html>'
                    self.send(body.encode(),'text/html; charset=utf-8')
                else: raise LookupError('页面不存在')
        except ValueError as exc: self.send({'error':str(exc)},status=400)
        except LookupError as exc: self.send({'error':str(exc)},status=404)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception:
            self.send({'error':'读取失败；请检查数据库并重启服务。'},status=500)
            raise
