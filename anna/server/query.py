"""Loopback-only HTTP transport for the read-only presentation services."""
import html
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlsplit
import anna.paths as paths
from anna.presentation import catalog
from anna.presentation.collections import BookCatalog, CollectionError, CollectionRegistry, DEFAULT
from anna.presentation.expression import Query
from anna.presentation.library import Library, describe_works, parse_scope

WEB = paths.PROJECT / 'web'


class Handler(BaseHTTPRequestHandler):
    db_path=catalog.DB
    db_signature=None
    source_root=paths.WEB
    additional_collections=()

    def collection_registry(self):
        """The collections this server answers for; the query-only server has no linked book to read or tour."""
        return CollectionRegistry(BookCatalog(self.db_path, Path(self.source_root)), self.additional_collections)

    def collection_conflict(self, key):
        if key == DEFAULT and self.db_signature is not None:
            stat = Path(self.db_path).stat()
            if (stat.st_ino, stat.st_size, stat.st_mtime_ns) != self.db_signature:
                return {'error':'数据库已重建，请重启 make up 以同步链接版。'}
        return None

    def log_message(self,*args): pass
    def reads_books(self):
        return any(getattr(collection, 'reader', None) is not None for collection in self.additional_collections)
    def send(self,body,kind='application/json; charset=utf-8',status=200,download=False):
        if not isinstance(body,bytes): body=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        frames = "'self'" if urlsplit(self.path).path == '/read' and self.reads_books() else "'none'"
        self.send_header(
            'Content-Security-Policy',
            f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; frame-src {frames}",
        )
        if download: self.send_header('Content-Disposition','attachment; filename="'+(download if isinstance(download,str) else '5e-anna.csv')+'"')
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        # Reject DNS-rebinding hosts; never expose local data to a remote origin.
        if self.headers.get('Host','') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
            self.send({'error':'仅限本机访问'},status=403); return
        url=urlsplit(self.path)
        try:
            static={'/link-labels.js':('link-labels.js','text/javascript; charset=utf-8'),'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/detail.js':('detail.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
            static['/favicon.ico']=('favicon.ico','image/x-icon')
            static['/anna-logo.png']=('anna-logo.png','image/png')
            static.update({f'/{name}.js':(f'{name}.js','text/javascript; charset=utf-8') for name in ('theme','kit','motion','menu','topbar','workspace','companions','library','differences')})
            static['/read']=('reader.html','text/html; charset=utf-8')
            static['/tour']=('tour.html','text/html; charset=utf-8')
            static.update({f'/tour.{ext}':(f'tour.{ext}',kind) for ext,kind in [('js','text/javascript; charset=utf-8'),('css','text/css; charset=utf-8')]})
            static['/stella-logo.png']=('stella-logo.png','image/png')
            static['/stella-favicon.ico']=('stella-favicon.ico','image/x-icon')
            static.update({f'/reader.{ext}':(f'reader.{ext}',kind) for ext,kind in [('js','text/javascript; charset=utf-8'),('css','text/css; charset=utf-8')]})
            static.update({f'/reader-{name}.js':(f'reader-{name}.js','text/javascript; charset=utf-8') for name in ('labels','model','flow')})
            static.update({f'/favicon-{size}.png':(f'favicon-{size}.png','image/png') for size in (16,32,48)})
            if url.path in static:
                file,kind=static[url.path]; self.send((WEB/file).read_bytes(),kind); return
            if url.path not in {'/api/meta','/api/facets','/api/entries','/api/entry','/api/entry-layout','/api/export','/api/library','/source'}:
                raise LookupError('页面不存在')
            registry = self.collection_registry()
            p = parse_qs(url.query, keep_blank_values=True)
            # Blank values set nothing, except where the scope itself must reject them (an empty `collection`).
            filled = lambda q:{k:[x for x in v if x] for k,v in q.items() if any(v)}
            if url.path=='/source':
                if issue := self.collection_conflict(DEFAULT):
                    self.send(issue, status=409); return
                self.serve_source(registry.book, filled(p)); return
            if url.path=='/api/library':
                self.send(self.describe_library(registry, works=catalog.first(p,'works')=='1')); return
            scope = parse_scope(p, registry)
            for key in scope.libraries:
                if issue := self.collection_conflict(key):
                    self.send(issue, status=409); return
            # The query's conditions are a line: their order and the markers between them carry its meaning.
            # Markers carry blank values, so the line reads the request before blanks are dropped.
            own = set(p)
            query = lambda: Query.parse((k, v) for k, v in parse_qsl(url.query, keep_blank_values=True) if k in own)
            p = filled(p)
            library = Library(registry, scope)
            primary = library.primary
            if url.path=='/api/meta':
                members = [registry.get(k) for k in scope.libraries]
                self.send({
                    **library.meta(),
                    'collection': primary.key,
                    'default_collection': DEFAULT,
                    'collections': registry.describe(),
                    'library': scope.describe(),
                    # The default book's extracted page text (/source) serves its entries whenever the book is read.
                    'source_text': DEFAULT in scope.libraries,
                    # Stella and the tour read the whole library; they are there when a collection in it offers them.
                    'reader_url': '/read' if any(c.reader is not None for c in members) else None,
                    'tour_url': '/tour' if any(c.tour is not None for c in members) else None,
                })
            elif url.path=='/api/facets': self.send(library.facets(query()))
            elif url.path=='/api/entries': self.send(library.entries(query()))
            elif url.path=='/api/entry': self.send(library.detail(catalog.first(p,'id')))
            elif url.path=='/api/entry-layout': self.send(library.layout(catalog.first(p,'id')))
            else:
                body, key = library.export(query())
                filename = '5e-anna.csv' if key==DEFAULT else f'5e-anna-{key}.csv'
                self.send(body,'text/csv; charset=utf-8',download=filename)
        except CollectionError as exc: self.send({'code':exc.code},status=exc.status)
        except ValueError as exc: self.send({'error':str(exc)},status=400)
        except LookupError as exc: self.send({'error':str(exc)},status=404)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception:
            self.send({'error':'读取失败；请检查数据库并重启服务。'},status=500)
            raise

    def describe_library(self, registry, works=False):
        """Every collection the server knows and what it offers; with `works=1`, each ready one's works too."""
        collections = registry.describe()
        works = {w['id']: w for w in describe_works(registry, registry.ready())} if works else {}
        return {'default_collection': DEFAULT,
                'collections': [c | ({'works': works[c['id']]['works'], 'total': works[c['id']]['total']} if c['id'] in works else {})
                                for c in collections]}

    def serve_source(self, book, p):
        path = catalog.first(p,'path')
        text, checksum = book.source_text(path)
        if catalog.first(p,'sha256') and catalog.first(p,'sha256') != checksum:
            self.send({'error':'source_snapshot_mismatch','path':path},status=409); return
        start=max(0,min(len(text),int(catalog.first(p,'start','0')))); end=max(start,min(len(text),int(catalog.first(p,'end',str(start)))))
        body='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>页面文本 · 5e Anna</title><script src="/kit.js"></script><script src="/theme.js"></script><link rel="icon" href="/favicon.ico"><link rel="stylesheet" href="/style.css"><main class="source-page"><a href="/">← 条目查询</a><h1>页面文本</h1><p>提取后的文本；高亮为所选证据范围。</p><p>'+html.escape(path)+'</p><pre>'+html.escape(text[:start])+'<mark id="evidence">'+html.escape(text[start:end])+'</mark>'+html.escape(text[end:])+'</pre></main></html>'
        self.send(body.encode(),'text/html; charset=utf-8')
