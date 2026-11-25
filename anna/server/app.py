#!/usr/bin/env python3
"""Serve anna, its reader and source resources on one local port."""
import argparse
import errno
from http.server import ThreadingHTTPServer
import mimetypes
import os
import shutil
import json
from pathlib import Path
import signal
import sys
import threading
from urllib.parse import parse_qs, quote, unquote, urlsplit

from anna.server import home, tour_cache
from anna.paths import BOOK, FIVETOOLS_DATA, OUR5ETOOLS_DATA, WEB
from anna.presentation.reader import SCHEMA, BookReader, ReaderError, fingerprint
from anna.review.store import load_annotations
import anna.server.query as serve
from anna.presentation import catalog
from anna.presentation.collections import BookCatalog, CollectionRegistry, DEFAULT
from anna.presentation.library import parse_scope
from anna.presentation.library_tour import LibraryTour
from anna.presentation.shelf import Shelf
from anna.presentation.tour import TourCache, TourError
import anna.source.search as search

# Stella and the tour read the reader's whole library; their requests carry its scope as Anna's queries do.
READER_ROUTES = {'/api/reader', '/api/reader-page', '/api/reader-counterpart', '/api/book-search', '/api/tour', '/api/tour-layout'}


def _try_load_our5etools(data_dir, *, lazy_media=False):
    """Lazily load the our5etools collection when its published manifest exists."""
    if not data_dir:
        return None
    data_path = Path(data_dir).resolve()
    if not (data_path / 'manifest.json').is_file():
        return None
    from anna.presentation.our5etools import load_our5etools_collection
    return load_our5etools_collection(data_dir=data_path, lazy_media=bool(lazy_media))


def _try_load_fivetools(data_dir, *, lazy_media=False):
    """Lazily load the reserved standalone 5etools collection when built separately."""
    if not data_dir:
        return None
    data_path = Path(data_dir).resolve()
    if not (data_path / 'manifest.json').is_file():
        return None
    from anna.presentation.our5etools import load_fivetools_collection
    return load_fivetools_collection(data_dir=data_path, lazy_media=bool(lazy_media))


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
        self.book_reader = BookReader(self.reader, manifest, entries)
        self.book_tour = BookTour(self)

    @property
    def snapshot(self):
        return self.reader['snapshot']

    def registry(self, additional):
        return CollectionRegistry(BookCatalog(self.db, self.source), additional, book_tour=self.book_tour,
                                  book_reader=self.book_reader, book_snapshot=self.snapshot)

    def tour(self):
        """The tour over this snapshot, read on first use; a changed curation file is read again."""
        locatable = lambda: {eid for eid, e in self.manifest['entries'].items() if e['locatable']}
        return self.tours.get(self.db, self.reader['nodes'], locatable, self.snapshot, self.source)


class BookTour:
    """The book's tour levels, read only once a tour request needs them."""

    def __init__(self, bundle):
        self.bundle = bundle

    def view(self, segment):
        return self.bundle.tour().view(segment)

    def layout(self, segment):
        return self.bundle.tour().layout(segment)

    def resolve(self, label_path):
        return self.bundle.tour().resolve(label_path)

    def below(self, node_id):
        return self.bundle.tour().below(node_id)

    def subclass_records(self):
        return self.bundle.tour().subclass_records()


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
                 our5etools_data=None, our5etools_lazy_media=False,
                 fivetools_data=None, fivetools_lazy_media=None):
        # A failed bind closes the socket through server_close(), so the reload state must exist
        # before the socket is created; otherwise the bind error is masked by an AttributeError.
        self.bundle = bundle
        self.source = bundle.source
        self.additional_collections = tuple(additional_collections)
        self.our5etools_data = Path(our5etools_data).resolve() if our5etools_data else None
        self.our5etools_lazy_media = bool(our5etools_lazy_media)
        self.fivetools_data = Path(fivetools_data).resolve() if fivetools_data else None
        self.fivetools_lazy_media = bool(our5etools_lazy_media if fivetools_lazy_media is None else fivetools_lazy_media)
        self._managed_specs = {
            'our5etools': (self.our5etools_data, self.our5etools_lazy_media, _try_load_our5etools),
            '5etools': (self.fivetools_data, self.fivetools_lazy_media, _try_load_fivetools),
        }
        managed_keys = {key for key, (data_dir, _, _) in self._managed_specs.items() if data_dir is not None}
        self._base_additional_collections = tuple(
            c for c in self.additional_collections if c.key not in managed_keys
        )
        self._managed_collections = {
            key: next((c for c in self.additional_collections if c.key == key), None)
            for key in self._managed_specs
        }
        self._managed_signatures = {
            key: self._manifest_signature(data_dir)
            for key, (data_dir, _, _) in self._managed_specs.items()
        }
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

    @staticmethod
    def _manifest_signature(data_dir):
        if not data_dir:
            return None
        manifest = data_dir / 'manifest.json'
        try:
            return signature(manifest)
        except OSError:
            return None

    def _rebuild_additional_collections(self):
        extra = [self._managed_collections[k] for k in ('our5etools', '5etools') if self._managed_collections.get(k) is not None]
        self.additional_collections = (*self._base_additional_collections, *extra)

    def reload_managed(self, key):
        data_dir, lazy_media, loader = self._managed_specs[key]
        if not data_dir:
            return
        try:
            self._managed_collections[key] = loader(data_dir, lazy_media=lazy_media)
            self._rebuild_additional_collections()
            self._managed_signatures[key] = self._manifest_signature(data_dir)
        except Exception as exc:
            print(f'[anna] {key} reload failed: ' + (str(exc) or type(exc).__name__), file=sys.stderr, flush=True)

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
        seen_managed = dict(self._managed_signatures)
        attempted_managed = dict(self._managed_signatures)
        manifest = Path(self.bundle.book) / 'anna-manifest.json'
        while not self.watch_stop.wait(.5):
            for key, (data_dir, _, _) in self._managed_specs.items():
                if data_dir is None:
                    continue
                cur = self._manifest_signature(data_dir)
                if cur == self._managed_signatures[key]:
                    seen_managed[key] = cur
                elif cur != seen_managed[key]:
                    seen_managed[key] = cur
                elif cur != attempted_managed[key]:
                    attempted_managed[key] = cur
                    self.reload_managed(key)
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
    # Requests pin one bundle in do_GET; a reload only affects later requests.
    @property
    def db_path(self):
        return self.current.db

    def collection_registry(self):
        """Every collection, the book with the reader and tour of the bundle this request reads."""
        return self.current.registry(self.additional_collections)

    def reads_books(self):
        return True

    @property
    def source_root(self):
        return self.current.source

    @property
    def additional_collections(self):
        return self.server.additional_collections

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

    @staticmethod
    def _collection_book_base(col):
        raw = getattr(col, 'book_base', None) or f'/{col.key}-book/'
        return raw if raw.endswith('/') else (raw + '/')

    @staticmethod
    def _collection_media_base(col):
        raw = getattr(col, 'media_base', None) or f'/{col.key}-media/'
        return raw if raw.endswith('/') else (raw + '/')

    def _match_collection_route(self, req_path, kind):
        base_getter = self._collection_book_base if kind == 'book' else self._collection_media_base
        reserved_bases = ('/our5etools-book/', '/5etools-book/') if kind == 'book' else ('/our5etools-media/', '/5etools-media/')
        for col in self.additional_collections:
            if col.reader is not None:
                base = base_getter(col)
                if req_path.startswith(base):
                    return col.reader, base, '/' + req_path[len(base):]
        for reserved_base in reserved_bases:
            if req_path.startswith(reserved_base):
                return None, reserved_base, '/' + req_path[len(reserved_base):]
        return None

    def _is_external_book_path(self, req_path):
        if req_path.startswith(('/our5etools-book/', '/5etools-book/')):
            return True
        return any(
            col.reader is not None and req_path.startswith(self._collection_book_base(col))
            for col in self.additional_collections
        )

    def _try_serve_static_cache(self, req_path, page_path, cache_prefix, cache_mod, html_name, mark_main, mark_scopes):
        if req_path != page_path and not (req_path.startswith(cache_prefix) and req_path.endswith('.json')):
            return False
        if self.server.bundle_intact(self.current):
            registry = self.collection_registry()
            cache = cache_mod.local_cache(registry)
            scope_names, scope_payloads = cache_mod.local_scope_caches(registry)
        else:
            cache = None
            scope_names, scope_payloads = {}, {}
        if req_path != page_path:
            if cache and req_path == '/' + cache[0]:
                self.send(cache[1])
            elif (scoped := scope_payloads.get(req_path[1:])) is not None:
                self.send(scoped)
            else:
                self.send_error(404)
            return True
        if cache:
            body = mark_main((serve.WEB / html_name).read_bytes(), cache[0])
            if scope_names:
                body = mark_scopes(body, scope_names)
            self.send(body, 'text/html; charset=utf-8')
            return True
        return False

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

    def serve_our5etools_book(self, path, reader, book_base='/our5etools-book/'):
        base = book_base if book_base.endswith('/') else (book_base + '/')
        decoded = unquote(path)
        if decoded.startswith('/definition/'):
            eid = decoded.rsplit('/', 1)[-1]
            try:
                target = reader.page_data(entry=eid)
            except ReaderError as exc:
                self.send_error(exc.status, exc.code)
                return
            frag = target.get('fragment') or ''
            location = base + quote(target['path'], safe='/') + ('#' + quote(frag) if frag else '')
            self.redirect(location)
            return
        relative = decoded.lstrip('/')
        found = reader.resolve_book_asset(relative)
        if not found:
            self.send_error(404)
            return
        resolved, content_type = found
        self._send_file(Path(resolved), content_type or mimetypes.guess_type(str(resolved))[0] or 'application/octet-stream')

    def serve_our5etools_media(self, path, reader):
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
        if not self.host_allowed():
            self.send({'code': 'invalid_host'}, status=403)
            return
        url = urlsplit(self.path)
        for page_path, cache_prefix, cache_mod, html_name, mark_main, mark_scopes in (
            ('/', '/home.', home, 'index.html', home.mark_home, home.mark_home_scopes),
            ('/tour', '/tour.', tour_cache, 'tour.html', tour_cache.mark_tour, tour_cache.mark_tour_scopes),
        ):
            if self._try_serve_static_cache(url.path, page_path, cache_prefix, cache_mod, html_name, mark_main, mark_scopes):
                return
        if url.path.startswith('/book/'):
            self.serve_book(url.path[len('/book'):])
            return
        if matched_book := self._match_collection_route(url.path, 'book'):
            reader, book_base, rel = matched_book
            if reader is None:
                self.send_error(404)
            else:
                self.serve_our5etools_book(rel, reader=reader, book_base=book_base)
            return
        if matched_media := self._match_collection_route(url.path, 'media'):
            reader, _, rel = matched_media
            if reader is None:
                self.send_error(404)
            else:
                self.serve_our5etools_media(rel, reader=reader)
            return
        if url.path not in READER_ROUTES:
            super().do_GET()
            return
        self.serve_reader(url)

    def dispatch(self, path, *, curation_path=None):
        """Invoke reader or query handlers synchronously when wrapped by CapturedResponse."""
        url = urlsplit(path)
        if url.path in READER_ROUTES:
            self.serve_reader(url, curation_path=curation_path)
        else:
            self.serve_query(url)
        return self.response

    def serve_reader(self, url, *, curation_path=None):
        params = parse_qs(url.query, keep_blank_values=True)
        try:
            registry = self.collection_registry()
            scope = parse_scope(params, registry)
            for key in scope.libraries:
                if issue := self.collection_conflict(key):
                    self.send(issue, status=409)
                    return
            touring = url.path in {'/api/tour', '/api/tour-layout'}
            if any(len(v) != 1 for v in params.values()):
                raise TourError('invalid_node', 400) if touring else ReaderError('invalid_location', 400)
            one = lambda name: catalog.first(params, name)
            if touring:
                tour = LibraryTour(registry, scope, curation_path)
                tour.check(one('snapshot'))
                self.send(tour.view(one('at')) if url.path == '/api/tour' else tour.layout(one('at')))
                return
            shelf = Shelf(registry, scope)
            if url.path == '/api/reader':
                self.send(shelf.navigation())
            elif url.path == '/api/reader-page':
                self.send(shelf.page(path=one('path'), entry=one('entry'), snapshot=one('snapshot')))
            elif url.path == '/api/reader-counterpart':
                self.send(shelf.counterpart(one('node'), one('collection'), one('scope')))
            else:
                try:
                    page, size = int(one('page') or 1), int(one('pageSize') or 30)
                except ValueError:
                    raise ReaderError('invalid_search', 400) from None
                self.send(shelf.search(one('keyword'), title_only=one('titleOnly').lower() in {'true', '1'}, page=page, page_size=size))
        except (ReaderError, TourError) as exc:
            self.send({'code': exc.code}, status=exc.status)
        except ValueError as exc:
            self.send({'error': str(exc)}, status=400)
        except LookupError as exc:
            self.send({'error': str(exc)}, status=404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def end_headers(self):
        req_path = urlsplit(self.path).path
        if req_path.startswith('/book/') or self._is_external_book_path(req_path):
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
        content_type = mimetypes.guess_type(str(resolved))[0] or 'application/octet-stream'
        self._send_file(resolved, content_type, etag='"' + bundle.manifest['outputs'][relative] + '"')

    def do_HEAD(self):
        self.send_error(405)


def create_server(db, book, manifest, port, watch=False, bundle=None, *, additional_collections=(),
                  our5etools_data=None, our5etools_lazy_media=False,
                  fivetools_data=None, fivetools_lazy_media=None):
    """Build the one-port server; an already loaded bundle carries the search index."""
    bundle = bundle or Bundle(manifest['source_root'], db, book, manifest, [])
    return AnnaServer(('127.0.0.1', port), AnnaHandler, bundle, watch=watch,
                      additional_collections=additional_collections,
                      our5etools_data=our5etools_data,
                      our5etools_lazy_media=our5etools_lazy_media,
                      fivetools_data=fivetools_data, fivetools_lazy_media=fivetools_lazy_media)


def main():
    lazy_default = os.environ.get('ANNA_LAZY_MEDIA', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--db', type=Path, default=catalog.DB)
    parser.add_argument('--book', type=Path, default=BOOK)
    parser.add_argument('--web', type=Path, default=WEB)
    parser.add_argument('--our5etools-data', type=Path, default=OUR5ETOOLS_DATA)
    parser.add_argument('--5etools-data', dest='fivetools_data', type=Path, default=FIVETOOLS_DATA,
                        help='reserved standalone 5etools snapshot directory (default: data/5etools)')
    parser.add_argument('--lazy-media', action='store_true', default=lazy_default,
                        help='lazily fetch and locally cache unbundled remote images on demand')
    parser.add_argument('--watch', action='store_true', help='swap in a rebuilt bundle without restarting')
    args = parser.parse_args()
    server, thread, stop = None, None, threading.Event()
    try:
        bundle = load_bundle(args.web, args.db, args.book)
        cols = []
        our5etools_col = _try_load_our5etools(args.our5etools_data, lazy_media=args.lazy_media)
        if our5etools_col is not None:
            cols.append(our5etools_col)
        fivetools_col = _try_load_fivetools(args.fivetools_data, lazy_media=args.lazy_media)
        if fivetools_col is not None:
            cols.append(fivetools_col)
        additional = tuple(cols)
        server = create_server(
            args.db, args.book, bundle.manifest, args.port, watch=args.watch, bundle=bundle,
            additional_collections=additional,
            our5etools_data=args.our5etools_data,
            our5etools_lazy_media=args.lazy_media,
            fivetools_data=args.fivetools_data, fivetools_lazy_media=args.lazy_media,
        )
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        lazy_note = '\nLazy media caching: ON (uncached images save into data/our5etools/media/)' if args.lazy_media else ''
        print(f'Anna: http://127.0.0.1:{server.server_port}/\n'
              f'Stella: http://127.0.0.1:{server.server_port}/read'
              + lazy_note + '\n'
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
