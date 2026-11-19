"""Loopback-only HTTP transport for the read-only presentation services."""
import html
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import anna.paths as paths
from anna.presentation import catalog
from anna.presentation.collections import BookCatalog, CollectionError, CollectionRegistry, DEFAULT

WEB = paths.PROJECT / 'web'


class Handler(BaseHTTPRequestHandler):
    db_path=catalog.DB
    reader_url=None
    # The build snapshot the query page shares with the reader; None when the query page runs alone.
    snapshot=None
    db_signature=None
    source_root=paths.WEB
    additional_collections=()

    def collection_registry(self):
        return CollectionRegistry(BookCatalog(self.db_path, Path(self.source_root)), self.additional_collections)

    def collection_conflict(self, key):
        if key == DEFAULT and self.db_signature is not None:
            stat = Path(self.db_path).stat()
            if (stat.st_ino, stat.st_size, stat.st_mtime_ns) != self.db_signature:
                return {'error':'数据库已重建，请重启 make up 以同步链接版。'}
        return None

    def log_message(self,*args): pass
    def send(self,body,kind='application/json; charset=utf-8',status=200,download=False):
        if not isinstance(body,bytes): body=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        has_reader = bool(self.reader_url) or any(
            getattr(collection, 'reader', None) is not None for collection in self.additional_collections
        )
        frames = "'self'" if urlsplit(self.path).path == '/read' and has_reader else "'none'"
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
            static.update({f'/{name}.js':(f'{name}.js','text/javascript; charset=utf-8') for name in ('theme','kit','menu','topbar','workspace','companions')})
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
            if url.path not in {'/api/meta','/api/facets','/api/entries','/api/entry','/api/entry-layout','/api/export','/source'}:
                raise LookupError('页面不存在')
            registry = self.collection_registry()
            requested = parse_qs(url.query, keep_blank_values=True).get('collection')
            collection = registry.select(requested, book_only=url.path=='/source')
            if issue := self.collection_conflict(collection.key):
                self.send(issue, status=409); return
            p = parse_qs(url.query)
            p.pop('collection', None)
            provider = collection.provider
            if url.path=='/api/meta':
                native = collection.key == DEFAULT
                self.send({
                    **provider.meta(),
                    'collection': collection.key,
                    'default_collection': DEFAULT,
                    'collections': registry.describe(),
                    'source_text': native,
                    'reader_url': self.reader_url if native else collection.reader_url,
                    'snapshot': self.snapshot if native else collection.snapshot,
                    'tour_url': '/tour' if native else (f'/tour?collection={collection.key}' if collection.tour is not None else None),
                })
            elif url.path=='/api/facets': self.send(provider.facets(p))
            elif url.path=='/api/entries': self.send(provider.entries(p))
            elif url.path=='/api/entry': self.send(provider.detail(catalog.first(p,'id')))
            elif url.path=='/api/entry-layout': self.send(provider.layout(catalog.first(p,'id')))
            elif url.path=='/api/export':
                filename = '5e-anna.csv' if collection.key==DEFAULT else f'5e-anna-{collection.key}.csv'
                self.send(provider.export(p),'text/csv; charset=utf-8',download=filename)
            else:
                self.serve_source(registry.book, p)
        except CollectionError as exc: self.send({'code':exc.code},status=exc.status)
        except ValueError as exc: self.send({'error':str(exc)},status=400)
        except LookupError as exc: self.send({'error':str(exc)},status=404)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception:
            self.send({'error':'读取失败；请检查数据库并重启服务。'},status=500)
            raise

    def serve_source(self, book, p):
        path = catalog.first(p,'path')
        text, checksum = book.source_text(path)
        if catalog.first(p,'sha256') and catalog.first(p,'sha256') != checksum:
            self.send({'error':'source_snapshot_mismatch','path':path},status=409); return
        start=max(0,min(len(text),int(catalog.first(p,'start','0')))); end=max(start,min(len(text),int(catalog.first(p,'end',str(start)))))
        body='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>页面文本 · 5e Anna</title><script src="/kit.js"></script><script src="/theme.js"></script><link rel="icon" href="/favicon.ico"><link rel="stylesheet" href="/style.css"><main class="source-page"><a href="/">← 条目查询</a><h1>页面文本</h1><p>提取后的文本；高亮为所选证据范围。</p><p>'+html.escape(path)+'</p><pre>'+html.escape(text[:start])+'<mark id="evidence">'+html.escape(text[start:end])+'</mark>'+html.escape(text[end:])+'</pre></main></html>'
        self.send(body.encode(),'text/html; charset=utf-8')
