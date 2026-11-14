#!/usr/bin/env python3
"""Serve anna, its reader and source resources on one local port."""
import argparse
from http.server import ThreadingHTTPServer
import mimetypes
import shutil
import json
from pathlib import Path
import signal
import sys
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


def bundle_signature(db, book):
    """The published files a running service must keep in step with the bundle it serves."""
    return signature(Path(book) / 'anna-manifest.json'), signature(db)


def validate_bundle(source, db, book):
    source, book = Path(source).resolve(), Path(book).resolve()
    manifest_path = book / 'anna-manifest.json'
    if not manifest_path.is_file():
        raise ValueError("Stella's linked book not found; run make build")
    m = json.loads(manifest_path.read_text())
    if m.get('reader_schema') != SCHEMA:
        raise ValueError("Stella's reader data needs updating; run make linked")
    reviewed = m.get('review_snapshot')
    if not reviewed or load_events(reviewed['path'])[1] != reviewed['sha256']:
        raise ValueError('Review log does not match the published snapshot; run make build')
    if m['source_root'] != str(source) or not Path(db).is_file() or fingerprint(db) != m['database_sha256']:
        raise ValueError("Anna's database and Stella's linked book do not match; run make build")
    actual = {p.relative_to(source).as_posix(): p for p in source.rglob('*')
              if p.is_file() and '.git' not in p.relative_to(source).parts and not p.is_symlink()}
    if set(actual) != set(m['inputs']) or any(fingerprint(actual[p]) != h for p, h in m['inputs'].items()):
        raise ValueError('Source snapshot changed; run make build')
    for path, expected in m['outputs'].items():
        file = book / path
        if not file.is_file() or fingerprint(file) != expected:
            raise ValueError("Stella's linked book file is missing or modified; run make build: " + path)
    return m


class Bundle:
    """One validated snapshot of the database, reader model, search index and source metadata."""

    def __init__(self, source, db, book, manifest, entries):
        self.source, self.db, self.book = Path(source).resolve(), Path(db).resolve(), Path(book).resolve()
        self.manifest = manifest
        self.reader = json.loads((self.book / 'anna-reader.json').read_text())
        self.entries = entries
        self.signature = bundle_signature(self.db, self.book)

    @property
    def snapshot(self):
        return self.reader['snapshot']


def load_bundle(source, db, book, quiet=True):
    """Validate the published files and load what requests read; called again to reload after a rebuild."""
    signature_before = bundle_signature(db, book)
    validate_bundle(source, db, book)
    search.WEB = Path(source).resolve()
    entries = search.build_index(quiet=quiet)
    # The second validation catches a rebuild that overlapped loading the index; the final
    # signature check catches one that overlapped the second validation itself.
    manifest = validate_bundle(source, db, book)
    bundle = Bundle(source, db, book, manifest, entries)
    if bundle.signature != signature_before:
        raise ValueError('Build files changed while loading; run make build and retry')
    return bundle


class AnnaServer(ThreadingHTTPServer):
    """Owns the bundle requests serve; in watch mode one thread swaps it after a rebuild."""

    def __init__(self, address, handler, bundle, watch=False):
        super().__init__(address, handler)
        self.bundle = bundle
        self.source = bundle.source
        self.watch = watch
        self.watch_stop = threading.Event()
        self.reload_lock = threading.Lock()
        self.reload_error = None
        self.reload_attempt = None
        self.watch_thread = None
        if watch:
            self.watch_thread = threading.Thread(target=self.watch_loop, daemon=True)
            self.watch_thread.start()

    def bundle_intact(self, bundle=None):
        """Whether the files on disk still match a loaded bundle, so a stale mix is never served."""
        bundle = bundle or self.bundle
        try:
            return bundle_signature(bundle.db, bundle.book) == bundle.signature
        except OSError:
            return False

    def reload(self):
        """Swap in a rebuilt bundle; a bundle that fails validation is never published."""
        if not self.reload_lock.acquire(blocking=False):
            return
        try:
            # While this attempt runs, requests report a reload rather than the previous failure.
            self.reload_error = None
            self.bundle = load_bundle(self.source, self.bundle.db, self.bundle.book)
        except (Exception, SystemExit) as exc:
            self.reload_error = str(exc) or type(exc).__name__
            print('[anna] bundle reload failed: ' + self.reload_error, file=sys.stderr, flush=True)
        finally:
            self.reload_lock.release()

    def watch_loop(self):
        # build.py replaces the database before the linked book. The manifest is published with the
        # book, so reload on it alone: waiting for both signatures would validate a half-built pair
        # and report an error during every normal build. Requests still check both files.
        seen = None
        manifest = Path(self.bundle.book) / 'anna-manifest.json'
        while not self.watch_stop.wait(.5):
            try:
                current = signature(manifest)
            except OSError:
                continue
            if current == self.bundle.signature[0]:
                seen = current
                continue
            if current != seen:
                seen = current
                continue
            if current == self.reload_attempt:
                continue
            self.reload_attempt = current
            self.reload()

    def server_close(self):
        self.watch_stop.set()
        if self.watch_thread and self.watch_thread is not threading.current_thread():
            self.watch_thread.join(timeout=30)
        super().server_close()


class AnnaHandler(serve.Handler):
    reader_url = '/read'

    # Requests pin one bundle in do_GET; a reload only affects later requests.
    @property
    def db_path(self):
        return self.current.db

    @property
    def snapshot(self):
        return self.current.snapshot

    @property
    def source_root(self):
        return self.current.source

    @property
    def search_entries(self):
        return self.current.entries

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def stale(self):
        if self.server.watch and self.server.reload_error:
            return {'code': 'reload_failed', 'error': self.server.reload_error}
        if self.server.watch:
            return {'code': 'reloading', 'error': '数据已更新，正在自动重载；完成后刷新页面。'}
        return {'code': 'bundle_changed'}

    def do_GET(self):
        self.current = self.server.bundle
        if self.headers.get('Host', '') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
            self.send({'code': 'invalid_host'}, status=403)
            return
        url = urlsplit(self.path)
        if url.path.startswith('/book/'):
            self.serve_book(url.path[len('/book'):])
            return
        # Keep the independent UI loadable so a refresh can explain stale data.
        if (url.path.startswith('/api/') or url.path == '/source') and not self.server.bundle_intact(self.current):
            self.send(self.stale(), status=409)
            return
        if url.path not in {'/api/reader', '/api/reader-page', '/api/book-search'}:
            super().do_GET()
            return
        params = parse_qs(url.query, keep_blank_values=True)
        try:
            if 'snapshot' in params and params['snapshot'] != [self.current.snapshot]:
                raise ReaderError('snapshot_changed', 409)
            if url.path == '/api/reader':
                reader = self.current.reader
                self.send({k: reader[k] for k in ('schema', 'snapshot', 'nodes', 'default_page', 'source_release')} | {'content_base': '/book/'})
            elif url.path == '/api/reader-page':
                if any(len(v) != 1 for v in params.values()):
                    raise ReaderError('invalid_location', 400)
                self.send(page_data(self.current.reader, self.current.manifest, path=serve.first(params, 'path'), entry=serve.first(params, 'entry')))
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

    def stale_reason(self):
        if self.server.watch:
            return 'Bundle rebuild did not validate' if self.server.reload_error else 'Bundle reloading; retry shortly'
        return 'Bundle rebuilt; restart make up'

    def serve_book(self, path):
        bundle = self.current
        if not self.server.bundle_intact(bundle):
            self.send_error(409, self.stale_reason())
            return
        path = unquote(path)
        if path.startswith('/definition/'):
            eid = path.rsplit('/', 1)[-1]
            entry = bundle.manifest['entries'].get(eid)
            if not entry:
                self.send_error(404, 'Unknown entry')
            elif not entry['locatable']:
                self.send_error(422, 'Entry has no source content range')
            else:
                self.redirect('/book/' + quote(entry['path'], safe='/') + '#' + quote(entry['fragment']))
            return
        # Exact manifest membership prevents traversal, directory listings, stale files and
        # accidental publication of the upstream shell or internal build metadata.
        relative = path.lstrip('/')
        if relative not in bundle.manifest['outputs'] or relative.startswith('anna-') and not relative.startswith('anna-assets/'):
            self.send_error(404)
            return
        resolved = (bundle.book / relative).resolve()
        if not resolved.is_relative_to(bundle.book):
            self.send_error(404)
            return
        # Open the already-validated literal path. SimpleHTTPRequestHandler's path
        # translator would decode '%' a second time and could select a different file.
        try:
            with resolved.open('rb') as file:
                etag = '"' + bundle.manifest['outputs'][relative] + '"'
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


def create_server(db, book, manifest, port, watch=False, bundle=None):
    """Build the one-port server; an already loaded bundle carries the search index."""
    bundle = bundle or Bundle(manifest['source_root'], db, book, manifest, [])
    return AnnaServer(('127.0.0.1', port), AnnaHandler, bundle, watch=watch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--db', type=Path, default=serve.DB)
    parser.add_argument('--book', type=Path, default=BOOK)
    parser.add_argument('--source', type=Path, default=WEB)
    parser.add_argument('--watch', action='store_true', help='swap in a rebuilt bundle without restarting')
    args = parser.parse_args()
    server, thread, stop = None, None, threading.Event()
    try:
        bundle = load_bundle(args.source, args.db, args.book)
        server = create_server(args.db, args.book, bundle.manifest, args.port, watch=args.watch, bundle=bundle)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f'Anna: http://127.0.0.1:{server.server_port}/\n'
              f'Stella: http://127.0.0.1:{server.server_port}/read\n'
              + ('Rebuilt data reloads automatically; Ctrl-C to stop.' if args.watch else 'Ctrl-C to stop.'), flush=True)
        while not stop.wait(.5):
            if not thread.is_alive():
                raise RuntimeError('Server exited unexpectedly')
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
