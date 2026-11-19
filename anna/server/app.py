#!/usr/bin/env python3
"""Serve anna, its reader and source resources on one local port."""
import argparse
import errno
from http.server import ThreadingHTTPServer
import mimetypes
import shutil
import json
from pathlib import Path
import signal
import sys
import threading
from urllib.parse import parse_qs, quote, unquote, urlsplit

from anna.server.linked_book import BOOK, fingerprint
from anna.paths import OUR5ETOOLS, OUR5ETOOLS_DATA, WEB
from anna.presentation.reader import SCHEMA, ReaderError, page_data
from anna.review.store import load_annotations
import anna.server.query as serve
from anna.presentation import catalog
from anna.presentation.collections import BookCatalog, CollectionError, CollectionRegistry, DEFAULT
from anna.presentation.tour import TourCache, TourError
import anna.source.search as search


def _try_load_our5etools(data_dir, source_dir):
    """Lazily load the our5etools collection when its published manifest exists."""
    if not data_dir:
        return None
    data_path = Path(data_dir).resolve()
    if not (data_path / 'manifest.json').is_file():
        return None
    from anna.presentation.our5etools import load_our5etools_collection
    source_path = Path(source_dir).resolve() if source_dir else None
    return load_our5etools_collection(data_dir=data_path, source_dir=source_path)


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
    annotated = m.get('annotation_snapshot')
    if not annotated or load_annotations(annotated['path'])[1] != annotated['sha256']:
        raise ValueError('Annotation log does not match the published snapshot; run make build')
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
        self.tours = TourCache()

    @property
    def snapshot(self):
        return self.reader['snapshot']

    def tour(self):
        """The tour over this snapshot, read on first use; a changed curation file is read again."""
        locatable = lambda: {eid for eid, e in self.manifest['entries'].items() if e['locatable']}
        return self.tours.get(self.db, self.reader['nodes'], locatable, self.snapshot)


def load_bundle(source, db, book, quiet=True):
    """Validate the published files and load what requests read; called again to reload after a rebuild."""
    signature_before = bundle_signature(db, book)
    validate_bundle(source, db, book)
    entries = search.load_index(db, source, quiet=quiet)
    # The second validation catches a rebuild that overlapped loading the index; the final
    # signature check catches one that overlapped the second validation itself.
    manifest = validate_bundle(source, db, book)
    bundle = Bundle(source, db, book, manifest, entries)
    if bundle.signature != signature_before:
        raise ValueError('Build files changed while loading; run make build and retry')
    return bundle


class AnnaServer(ThreadingHTTPServer):
    """Owns the bundle requests serve; in watch mode one thread swaps it after a rebuild."""

    def __init__(self, address, handler, bundle, watch=False, *, additional_collections=(),
                 our5etools_data=None, our5etools_source=None):
        # A failed bind closes the socket through server_close(), so the reload state must exist
        # before the socket is created; otherwise the bind error is masked by an AttributeError.
        self.bundle = bundle
        self.source = bundle.source
        self.additional_collections = tuple(additional_collections)
        self._base_additional_collections = tuple(c for c in self.additional_collections if c.key != '5etools')
        self.our5etools_data = Path(our5etools_data).resolve() if our5etools_data else None
        self.our5etools_source = Path(our5etools_source).resolve() if our5etools_source else None
        self._our5etools_signature = self._our5etools_manifest_signature()
        # Reject invalid registrations before opening a listener.
        CollectionRegistry(BookCatalog(bundle.db, bundle.source), self.additional_collections)
        self.watch = watch
        self.watch_stop = threading.Event()
        self.reload_lock = threading.Lock()
        self.reload_error = None
        self.reload_attempt = None
        self.watch_thread = None
        super().__init__(address, handler)
        if watch:
            self.watch_thread = threading.Thread(target=self.watch_loop, daemon=True)
            self.watch_thread.start()

    def _our5etools_manifest_signature(self):
        if not self.our5etools_data:
            return None
        manifest = self.our5etools_data / 'manifest.json'
        try:
            return signature(manifest)
        except OSError:
            return None

    def reload_our5etools(self):
        if not self.our5etools_data:
            return
        try:
            loaded = _try_load_our5etools(self.our5etools_data, self.our5etools_source)
            self.additional_collections = (
                (*self._base_additional_collections, loaded)
                if loaded is not None
                else self._base_additional_collections
            )
            self._our5etools_signature = self._our5etools_manifest_signature()
        except Exception as exc:
            print('[anna] our5etools reload failed: ' + (str(exc) or type(exc).__name__), file=sys.stderr, flush=True)

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
        seen_o5e = self._our5etools_signature
        attempted_o5e = self._our5etools_signature
        manifest = Path(self.bundle.book) / 'anna-manifest.json'
        while not self.watch_stop.wait(.5):
            if self.our5etools_data is not None:
                cur_o5e = self._our5etools_manifest_signature()
                if cur_o5e == self._our5etools_signature:
                    seen_o5e = cur_o5e
                elif cur_o5e != seen_o5e:
                    seen_o5e = cur_o5e
                elif cur_o5e != attempted_o5e:
                    attempted_o5e = cur_o5e
                    self.reload_our5etools()
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
    def additional_collections(self):
        return self.server.additional_collections

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

    def collection_conflict(self, key):
        if key == DEFAULT and not self.server.bundle_intact(self.current):
            return self.stale()
        return None

    def _our5etools_reader(self):
        for col in self.additional_collections:
            if col.key == '5etools' and col.reader is not None:
                return col.reader
        for col in self.additional_collections:
            if col.reader is not None:
                return col.reader
        return None

    def _send_file(self, resolved, content_type, *, etag=None, cache_control='private, no-cache', support_range=False):
        try:
            stat = resolved.stat()
        except OSError:
            self.send_error(404)
            return
        size = stat.st_size
        if etag is None:
            etag = f'"{stat.st_size:x}-{stat.st_mtime_ns:x}"'
        cached = self.headers.get('If-None-Match', '').split(',')
        if not support_range or not self.headers.get('Range'):
            if any(tag.strip() in {etag, 'W/' + etag, '*'} for tag in cached):
                self.send_response(304)
                self.send_header('ETag', etag)
                self.resource_response = True
                self.send_header('Cache-Control', cache_control)
                self.end_headers()
                return
        range_header = self.headers.get('Range', '').strip() if support_range else ''
        start, end = 0, size - 1
        partial = False
        if range_header.startswith('bytes='):
            spec = range_header[len('bytes='):].split(',', 1)[0].strip()
            if '-' in spec:
                lo_s, hi_s = spec.split('-', 1)
                try:
                    if lo_s == '' and hi_s:
                        length = int(hi_s)
                        if length <= 0:
                            raise ValueError
                        start = max(0, size - length)
                        end = size - 1
                    else:
                        start = int(lo_s)
                        end = int(hi_s) if hi_s else size - 1
                    if start < 0 or end < start or start >= size:
                        self.send_response(416)
                        self.send_header('Content-Range', f'bytes */{size}')
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                    end = min(end, size - 1)
                    partial = True
                except ValueError:
                    partial = False
        try:
            with resolved.open('rb') as file:
                self.resource_response = True
                if partial:
                    length = end - start + 1
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('ETag', etag)
                    self.send_header('Cache-Control', cache_control)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(length))
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()
                    file.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = file.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                else:
                    self.send_response(200)
                    if support_range:
                        self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('ETag', etag)
                    self.send_header('Cache-Control', cache_control)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(size))
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()
                    shutil.copyfileobj(file, self.wfile)
        except FileNotFoundError:
            self.send_error(404)

    def serve_our5etools_book(self, path):
        reader = self._our5etools_reader()
        if reader is None:
            self.send_error(404)
            return
        decoded = unquote(path)
        if decoded.startswith('/definition/'):
            eid = decoded.rsplit('/', 1)[-1]
            try:
                target = reader.page_data(entry=eid)
            except ReaderError as exc:
                self.send_error(exc.status, exc.code)
                return
            frag = target.get('fragment') or ''
            location = '/our5etools-book/' + quote(target['path'], safe='/') + ('#' + quote(frag) if frag else '')
            self.redirect(location)
            return
        relative = decoded.lstrip('/')
        found = reader.resolve_book_asset(relative)
        if not found:
            self.send_error(404)
            return
        resolved, content_type = found
        self._send_file(Path(resolved), content_type or mimetypes.guess_type(str(resolved))[0] or 'application/octet-stream')

    def serve_our5etools_media(self, path):
        reader = self._our5etools_reader()
        if reader is None:
            self.send_error(404)
            return
        relative = unquote(path).lstrip('/')
        found = reader.resolve_media_asset(relative)
        if not found:
            self.send_error(404)
            return
        resolved, content_type = found
        self._send_file(
            Path(resolved),
            content_type or mimetypes.guess_type(str(resolved))[0] or 'application/octet-stream',
            cache_control='private, max-age=3600',
            support_range=True,
        )

    def do_GET(self):
        self.current = self.server.bundle
        if self.headers.get('Host', '') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
            self.send({'code': 'invalid_host'}, status=403)
            return
        url = urlsplit(self.path)
        if url.path.startswith('/book/'):
            self.serve_book(url.path[len('/book'):])
            return
        if url.path.startswith('/our5etools-book/'):
            self.serve_our5etools_book(url.path[len('/our5etools-book'):])
            return
        if url.path.startswith('/our5etools-media/'):
            self.serve_our5etools_media(url.path[len('/our5etools-media'):])
            return
        if url.path not in {'/api/reader', '/api/reader-page', '/api/book-search', '/api/tour', '/api/tour-layout'}:
            super().do_GET()
            return
        params = parse_qs(url.query, keep_blank_values=True)
        try:
            collection = self.collection_registry().select(params.pop('collection', None), book_only=False)
            if issue := self.collection_conflict(collection.key):
                self.send(issue, status=409)
                return
            if collection.key == DEFAULT:
                if 'snapshot' in params and params['snapshot'] != [self.current.snapshot]:
                    raise ReaderError('snapshot_changed', 409)
                if url.path == '/api/reader':
                    reader = self.current.reader
                    self.send({k: reader[k] for k in ('schema', 'snapshot', 'nodes', 'default_page', 'source_release')} | {'content_base': '/book/'})
                elif url.path == '/api/tour':
                    if any(len(v) != 1 for v in params.values()):
                        raise TourError('invalid_node', 400)
                    self.send(self.current.tour().view(catalog.first(params, 'node')) | {'snapshot': self.current.snapshot})
                elif url.path == '/api/tour-layout':
                    if any(len(v) != 1 for v in params.values()):
                        raise TourError('invalid_node', 400)
                    self.send(self.current.tour().layout(catalog.first(params, 'node'), self.current.source))
                elif url.path == '/api/reader-page':
                    if any(len(v) != 1 for v in params.values()):
                        raise ReaderError('invalid_location', 400)
                    self.send(page_data(self.current.reader, self.current.manifest, path=catalog.first(params, 'path'), entry=catalog.first(params, 'entry')))
                else:
                    if len(catalog.first(params, 'keyword')) > 2000:
                        raise ReaderError('invalid_search', 400)
                    self.send(search.search_entries(self.search_entries, params))
                return

            if 'snapshot' in params and collection.snapshot and params['snapshot'] != [collection.snapshot]:
                raise ReaderError('snapshot_changed', 409)
            if url.path == '/api/tour':
                if collection.tour is None:
                    raise CollectionError('collection_unsupported', 400)
                if any(len(v) != 1 for v in params.values()):
                    raise TourError('invalid_node', 400)
                at = catalog.first(params, 'at') or catalog.first(params, 'node') or None
                scope_source = catalog.first(params, 'scope_source') or None
                scope_package = catalog.first(params, 'scope_package') or None
                self.send(collection.tour.view(at=at, scope_source=scope_source, scope_package=scope_package))
            elif url.path == '/api/tour-layout':
                if collection.tour is None:
                    raise CollectionError('collection_unsupported', 400)
                if any(len(v) != 1 for v in params.values()):
                    raise TourError('invalid_node', 400)
                target_id = catalog.first(params, 'id') or catalog.first(params, 'node') or catalog.first(params, 'at')
                self.send(collection.tour.layout(target_id))
            elif url.path == '/api/reader':
                if collection.reader is None:
                    raise CollectionError('collection_unsupported', 400)
                nav = collection.reader.navigation()
                base = nav.get('page_base') or nav.get('content_base') or '/our5etools-book/'
                self.send({**nav, 'collection': collection.key, 'page_base': base, 'content_base': base})
            elif url.path == '/api/reader-page':
                if collection.reader is None:
                    raise CollectionError('collection_unsupported', 400)
                if any(len(v) != 1 for v in params.values()):
                    raise ReaderError('invalid_location', 400)
                self.send(collection.reader.page_data(
                    path=catalog.first(params, 'path') or None,
                    entry=catalog.first(params, 'entry') or None,
                ))
            else:
                if collection.reader is None:
                    raise CollectionError('collection_unsupported', 400)
                query = catalog.first(params, 'keyword') or catalog.first(params, 'q')
                if len(query) > 2000:
                    raise ReaderError('invalid_search', 400)
                root = catalog.first(params, 'root') or None
                try:
                    self.send(collection.reader.book_search(query, root=root, params=params))
                except TypeError:
                    self.send(collection.reader.book_search(query, root=root))
        except (ReaderError, TourError, CollectionError) as exc:
            self.send({'code': exc.code}, status=exc.status)
        except ValueError as exc:
            self.send({'error': str(exc)}, status=400)
        except LookupError as exc:
            self.send({'error': str(exc)}, status=404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def end_headers(self):
        req_path = urlsplit(self.path).path
        if req_path.startswith('/book/') or req_path.startswith('/our5etools-book/'):
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


def create_server(db, book, manifest, port, watch=False, bundle=None, *, additional_collections=(),
                  our5etools_data=None, our5etools_source=None):
    """Build the one-port server; an already loaded bundle carries the search index."""
    bundle = bundle or Bundle(manifest['source_root'], db, book, manifest, [])
    return AnnaServer(('127.0.0.1', port), AnnaHandler, bundle, watch=watch,
                      additional_collections=additional_collections,
                      our5etools_data=our5etools_data, our5etools_source=our5etools_source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--db', type=Path, default=catalog.DB)
    parser.add_argument('--book', type=Path, default=BOOK)
    parser.add_argument('--source', type=Path, default=WEB)
    parser.add_argument('--our5etools-data', type=Path, default=OUR5ETOOLS_DATA)
    parser.add_argument('--our5etools-source', type=Path, default=OUR5ETOOLS)
    parser.add_argument('--watch', action='store_true', help='swap in a rebuilt bundle without restarting')
    args = parser.parse_args()
    server, thread, stop = None, None, threading.Event()
    try:
        bundle = load_bundle(args.source, args.db, args.book)
        additional = ()
        our5etools_col = _try_load_our5etools(args.our5etools_data, args.our5etools_source)
        if our5etools_col is not None:
            additional = (our5etools_col,)
        server = create_server(
            args.db, args.book, bundle.manifest, args.port, watch=args.watch, bundle=bundle,
            additional_collections=additional,
            our5etools_data=args.our5etools_data, our5etools_source=args.our5etools_source,
        )
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
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            parser.exit(1, f'Port {args.port} is already in use; stop the service using it or set ANNA_PORT.\n')
        parser.exit(1, str(exc) + '\n')
    except (ValueError, RuntimeError) as exc:
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
