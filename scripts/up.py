#!/usr/bin/env python3
"""Serve anna, its reader and source resources on one local port."""
import argparse
from http.server import ThreadingHTTPServer
import mimetypes
import shutil
import json
from pathlib import Path
import signal
import threading
from urllib.parse import parse_qs, quote, unquote, urlsplit

from linked_book import BOOK, fingerprint
from paths import WEB
from reader import SCHEMA, ReaderError, page_data
from review_store import load_events
import serve
import book_search as search


def signature(path):
    stat = Path(path).stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def validate_bundle(source, db, book):
    source, book = Path(source).resolve(), Path(book).resolve()
    manifest_path = book / 'anna-manifest.json'
    if not manifest_path.is_file():
        raise ValueError('链接版不存在，请运行 make build')
    m = json.loads(manifest_path.read_text())
    if m.get('reader_schema') != SCHEMA:
        raise ValueError('阅读器数据需要更新，请运行 make linked')
    reviewed = m.get('review_snapshot')
    if not reviewed or load_events(reviewed['path'])[1] != reviewed['sha256']:
        raise ValueError('审核日志与发布快照不匹配，请运行 make build')
    if m['source_root'] != str(source) or not Path(db).is_file() or fingerprint(db) != m['database_sha256']:
        raise ValueError('数据库与链接版不匹配，请运行 make build')
    actual = {p.relative_to(source).as_posix(): p for p in source.rglob('*')
              if p.is_file() and '.git' not in p.relative_to(source).parts and not p.is_symlink()}
    if set(actual) != set(m['inputs']) or any(fingerprint(actual[p]) != h for p, h in m['inputs'].items()):
        raise ValueError('原文快照已变化，请运行 make build')
    for path, expected in m['outputs'].items():
        file = book / path
        if not file.is_file() or fingerprint(file) != expected:
            raise ValueError('链接版文件缺失或被修改，请运行 make build：' + path)
    return m


def create_server(db, book, manifest, anna_port):
    book = Path(book).resolve()
    reader = json.loads((book / 'anna-reader.json').read_text())
    book_signature, db_signature = signature(book / 'anna-manifest.json'), signature(db)

    def current_bundle():
        try:
            return signature(book / 'anna-manifest.json') == book_signature and signature(db) == db_signature
        except FileNotFoundError:
            return False

    class AnnaHandler(serve.Handler):
        db_path = Path(db)
        source_root = Path(manifest['source_root'])
        reader_url = '/read'
        search_entries = []

        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.headers.get('Host', '') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
                self.send({'code': 'invalid_host'}, status=403)
                return
            url = urlsplit(self.path)
            if url.path.startswith('/book/'):
                self.serve_book(url.path[len('/book'):])
                return
            # Keep the independent UI loadable so a refresh can explain stale data.
            if (url.path.startswith('/api/') or url.path == '/source') and not current_bundle():
                self.send({'code': 'bundle_changed'}, status=409)
                return
            if url.path not in {'/api/reader', '/api/reader-page', '/api/book-search'}:
                super().do_GET()
                return
            params = parse_qs(url.query, keep_blank_values=True)
            try:
                if 'snapshot' in params and params['snapshot'] != [reader['snapshot']]:
                    raise ReaderError('snapshot_changed', 409)
                if url.path == '/api/reader':
                    self.send({k: reader[k] for k in ('schema', 'snapshot', 'nodes', 'default_page')} | {'content_base': '/book/'})
                elif url.path == '/api/reader-page':
                    if any(len(v) != 1 for v in params.values()):
                        raise ReaderError('invalid_location', 400)
                    self.send(page_data(reader, manifest, path=serve.first(params, 'path'), entry=serve.first(params, 'entry')))
                else:
                    if len(serve.first(params, 'keyword')) > 2000:
                        raise ReaderError('invalid_search', 400)
                    self.send(search.search_entries(self.search_entries, params))
            except ReaderError as exc:
                self.send({'code': exc.code}, status=exc.status)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def end_headers(self):
            if urlsplit(self.path).path.startswith('/book/'):
                self.send_header('Content-Security-Policy', "frame-ancestors 'self'")
                self.send_header('Referrer-Policy', 'same-origin')
                if not getattr(self, 'resource_response', False):
                    self.send_header('Cache-Control', 'no-store')
            super().end_headers()

        def redirect(self, url):
            self.send_response(302)
            self.send_header('Location', url)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def serve_book(self, path):
            if not current_bundle():
                self.send_error(409, 'Bundle rebuilt; restart make up')
                return
            path = unquote(path)
            if path == '/api/reader-context':
                body = json.dumps({'app_origins': [f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'],
                                   'snapshot': reader['snapshot'],
                                   'presentation_resources': reader['presentation_resources']}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith('/definition/') or path.startswith('/anna/entry/'):
                eid = path.rsplit('/', 1)[-1]
                entry = manifest['entries'].get(eid)
                if not entry:
                    self.send_error(404, 'Unknown entry')
                elif path.startswith('/anna/'):
                    self.redirect('/?entry=' + quote(eid))
                elif not entry['locatable']:
                    self.send_error(422, 'Entry has no source content range')
                else:
                    self.redirect('/book/' + quote(entry['path'], safe='/') + '#' + quote(entry['fragment']))
                return
            # Exact manifest membership prevents traversal, directory listings, stale files and
            # accidental publication of the upstream shell or internal build metadata.
            relative = path.lstrip('/')
            if relative not in manifest['outputs'] or relative.startswith('anna-') and not relative.startswith('anna-assets/'):
                self.send_error(404)
                return
            resolved = (book / relative).resolve()
            if not resolved.is_relative_to(book):
                self.send_error(404)
                return
            # Open the already-validated literal path. SimpleHTTPRequestHandler's path
            # translator would decode '%' a second time and could select a different file.
            try:
                with resolved.open('rb') as file:
                    etag = '"' + manifest['outputs'][relative] + '"'
                    cached = self.headers.get('If-None-Match', '').split(',')
                    if any(tag.strip() in {etag, 'W/' + etag, '*'} for tag in cached):
                        self.send_response(304)
                        self.send_header('ETag', etag)
                        self.resource_response = True
                        self.send_header('Cache-Control', 'private, no-cache')
                        self.end_headers()
                        return
                    self.resource_response = True
                    self.send_response(200)
                    self.send_header('ETag', etag)
                    self.send_header('Cache-Control', 'private, no-cache')
                    self.send_header('Content-Type', mimetypes.guess_type(str(resolved))[0] or 'application/octet-stream')
                    self.send_header('Content-Length', str(resolved.stat().st_size))
                    self.end_headers()
                    shutil.copyfileobj(file, self.wfile)
            except FileNotFoundError:
                self.send_error(404)

        def do_HEAD(self):
            self.send_error(405)

    return ThreadingHTTPServer(('127.0.0.1', anna_port), AnnaHandler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--anna-port', type=int, default=8000)
    parser.add_argument('--db', type=Path, default=serve.DB)
    parser.add_argument('--book', type=Path, default=BOOK)
    parser.add_argument('--source', type=Path, default=WEB)
    args = parser.parse_args()
    server, thread, stop = None, None, threading.Event()
    try:
        manifest = validate_bundle(args.source, args.db, args.book)
        server = create_server(args.db, args.book, manifest, args.anna_port)
        search.WEB = args.source.resolve()
        server.RequestHandlerClass.search_entries = search.build_index(quiet=True)
        # Updating the mirror concurrently with startup must not publish a new index
        # beside old definitions/content. Once started, both are immutable in memory/on disk.
        validate_bundle(args.source, args.db, args.book)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f'anna: http://127.0.0.1:{server.server_port}/\n'
              f'reader: http://127.0.0.1:{server.server_port}/read\n'
              'Ctrl-C 停止服务。', flush=True)
        while not stop.wait(.5):
            if not thread.is_alive():
                raise RuntimeError('站点服务意外退出')
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, str(exc) + '\n')
    finally:
        if thread and thread.is_alive():
            server.shutdown()
        if server:
            server.server_close()
        if thread:
            thread.join()


if __name__ == '__main__':
    main()
