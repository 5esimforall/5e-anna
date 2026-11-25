"""Run the site's read-only requests in the GitHub Pages worker."""
from contextlib import closing
from functools import cached_property, lru_cache
import gzip
import json
from pathlib import Path, PurePosixPath
import traceback
from urllib.parse import urlsplit

from anna.presentation import catalog
from anna.presentation.collections import BookCatalog, CollectionRegistry, DEFAULT
from anna.presentation.our5etools import load_our5etools_collection
from anna.presentation.reader import BookReader
from anna.presentation.tour import PublishedTour, Tour, expand_tour_nodes, load as load_curation
from anna.server.app import AnnaHandler, READER_ROUTES
from anna.server.pages_publications import PATH as PUBLICATIONS, load_publications
from anna.server.query import CapturedResponse
from anna.source import search

ROOT = Path('/anna-pages')


def add_cached_media_markers(collection):
    """Keep media availability in the worker while the site serves the files."""
    inventory = collection / 'cached-media.json'
    if not inventory.is_file():
        return
    for name in json.loads(inventory.read_text(encoding='utf-8')):
        rel = PurePosixPath(name)
        if len(rel.parts) < 2 or rel.parts[0] not in {'img', 'ext'} or '..' in rel.parts:
            raise ValueError(f'Invalid published media path: {name}')
        marker = collection / 'media' / rel
        marker.parent.mkdir(parents=True, exist_ok=True)
        if not marker.exists():
            marker.touch()


class PublishedReader(BookReader):
    def __init__(self, book, db, snapshot, meta=None):
        self._book = book
        self._snapshot = snapshot
        self._meta = meta or {}
        self.db = db
        self.entries = None

    @property
    def snapshot(self):
        return self._snapshot

    @cached_property
    def tour_index(self):
        index_file = self._book / 'tour-index.json.gz'
        if not index_file.is_file():
            return None
        return json.loads(gzip.decompress(index_file.read_bytes()).decode('utf-8'))

    @cached_property
    def nav_nodes(self):
        if self.tour_index is not None:
            return expand_tour_nodes(self.tour_index['nodes'])
        return self.reader['nodes']

    def navigation(self):
        if self.tour_index is not None and 'reader_schema' in self._meta and 'default_page' in self._meta:
            return {
                'schema': self._meta['reader_schema'],
                'snapshot': self._snapshot,
                'nodes': self.nav_nodes,
                'default_page': self._meta['default_page'],
                'source_release': self._meta.get('source_release'),
                'page_base': '/book/',
            }
        return super().navigation()

    @cached_property
    def reader(self):
        return json.loads((self._book / 'anna-reader.json').read_text(encoding='utf-8'))

    @cached_property
    def manifest(self):
        return json.loads((self._book / 'anna-manifest.json').read_text(encoding='utf-8'))

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
        meta_file = book / 'anna-meta.json'
        if meta_file.is_file():
            meta = json.loads(meta_file.read_text(encoding='utf-8'))
            snapshot = meta['snapshot']
        else:
            meta = {}
            snapshot = json.loads((book / 'anna-reader.json').read_text(encoding='utf-8'))['snapshot']
        self.reader = PublishedReader(book, self.db, snapshot, meta=meta)
        our5etools = root / 'data/our5etools'
        additional = ()
        relationships = None
        if (our5etools / 'manifest.json').is_file():
            add_cached_media_markers(our5etools)
            collection = load_our5etools_collection(our5etools, curation_path=self.curation, lazy_reader=True)
            if collection is None:
                raise ValueError('Published our5etools snapshot is incomplete')
            additional = (collection,)
            relationships = load_publications(root / PUBLICATIONS, {
                DEFAULT: self.reader.snapshot, collection.key: collection.snapshot,
            })
        self.registry = CollectionRegistry(BookCatalog(self.db, self.source), additional, book_reader=self.reader,
                                           published_publications=relationships,
                                           book_snapshot=self.reader.snapshot, book_tour=self)

    @cached_property
    def tour(self):
        if self.reader.tour_index is not None:
            return PublishedTour(
                self.db, self.reader.tour_index, load_curation(self.curation),
                self.source, nodes=self.reader.nav_nodes,
            )
        locatable = {entry for entry, target in self.reader.manifest['entries'].items() if target['locatable']}
        return Tour(self.db, self.reader.reader['nodes'], locatable, load_curation(self.curation), self.source)

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
        return WorkerRequest(self.registry).dispatch(path, curation_path=self.curation)


class WorkerRequest(CapturedResponse, AnnaHandler):
    """Use the same query and reader handlers as the HTTP server."""


@lru_cache(maxsize=1)
def published_site():
    return PublishedSite(ROOT)


def warm():
    published_site()


def handle(path):
    try:
        return published_site().handle(path)
    except Exception:
        traceback.print_exc()
        return 500, {'Content-Type': 'application/json; charset=utf-8'}, b'{"code":"server_error"}'
