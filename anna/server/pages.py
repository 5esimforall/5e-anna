"""Run the site's read-only requests in the GitHub Pages worker."""
from contextlib import closing
from functools import cached_property, lru_cache
import json
from pathlib import Path
import traceback
from urllib.parse import urlsplit

from anna.presentation import catalog
from anna.presentation.collections import BookCatalog, CollectionRegistry
from anna.presentation.reader import BookReader
from anna.presentation.tour import Tour
from anna.server.app import AnnaHandler, READER_ROUTES
from anna.source import search

ROOT = Path('/anna-pages')


class PublishedReader(BookReader):
    def __init__(self, reader, manifest, db):
        super().__init__(reader, manifest, None)
        self.db = db

    def search(self, keyword, *, title_only=False):
        if self.entries is None:
            with closing(catalog.connect(self.db)) as db:
                self.entries = search.indexed_entries(db)
        return super().search(keyword, title_only=title_only)


class PublishedSite:
    def __init__(self, root):
        self.db = root / 'data/extracted/catalog.sqlite'
        self.source = root / 'source'
        self.curation = root / 'tour/tour.json'
        book = root / 'data/5echm-anna'
        reader = json.loads((book / 'anna-reader.json').read_text(encoding='utf-8'))
        manifest = json.loads((book / 'anna-manifest.json').read_text(encoding='utf-8'))
        self.reader = PublishedReader(reader, manifest, self.db)
        self.registry = CollectionRegistry(BookCatalog(self.db, self.source), book_reader=self.reader,
                                           book_snapshot=self.reader.snapshot, book_tour=self)

    @cached_property
    def tour(self):
        from anna.presentation.tour import load

        locatable = {entry for entry, target in self.reader.manifest['entries'].items() if target['locatable']}
        return Tour(self.db, self.reader.reader['nodes'], locatable, load(self.curation), self.source)

    def view(self, segment):
        return self.tour.view(segment)

    def layout(self, segment):
        return self.tour.layout(segment)

    def resolve(self, label_path):
        return self.tour.resolve(label_path)

    def below(self, node_id):
        return self.tour.below(node_id)

    def subclass_records(self):
        return self.tour.subclass_records()

    def handle(self, path):
        url = urlsplit(path)
        request = WorkerRequest(self.registry)
        if url.path in READER_ROUTES:
            request.serve_reader(url)
        else:
            request.serve_query(url)
        return request.response


class WorkerRequest(AnnaHandler):
    """Reuse the HTTP handler's request parameters and responses without a socket."""

    def __init__(self, registry):
        self.registry = registry

    def collection_registry(self):
        return self.registry

    def collection_conflict(self, key):
        return None  # Each worker reads a single published snapshot.

    def send(self, body, kind='application/json; charset=utf-8', status=200, download=False):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        headers = {'Content-Type': kind}
        if download:
            filename = download if isinstance(download, str) else '5e-anna.csv'
            headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        self.response = status, headers, body


@lru_cache(maxsize=1)
def published_site():
    return PublishedSite(ROOT)


def handle(path):
    try:
        return published_site().handle(path)
    except Exception:
        traceback.print_exc()
        return 500, {'Content-Type': 'application/json; charset=utf-8'}, b'{"code":"server_error"}'
