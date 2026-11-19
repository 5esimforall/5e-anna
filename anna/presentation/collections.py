"""Read-only catalog providers. A collection is a library, not a book's source code."""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Protocol

from anna.presentation import catalog
from anna.presentation.layout import entry_layout

DEFAULT = '5echm'
Params = dict[str, list[str]]


class CatalogProvider(Protocol):
    """The existing query, facet, detail and reading projections, regardless of storage."""

    def meta(self) -> dict: ...
    def entries(self, params: Params) -> dict: ...
    def facets(self, params: Params) -> dict: ...
    def detail(self, entry: str) -> dict: ...
    def layout(self, entry: str) -> dict: ...  # HTML, if any, must be inert and sanitized.
    def export(self, params: Params) -> bytes: ...


class TourProvider(Protocol):
    """Optional read-only guided tour interface for an external collection."""

    def view(self, at: str = '', *, scope_source: str | None = None, scope_package: str | None = None) -> dict: ...
    def layout(self, entry_id: str, source_root: Path | None = None) -> dict: ...


class ReaderProvider(Protocol):
    """Optional read-only Stella chapter reader and media asset interface for an external collection."""

    def navigation(self) -> dict: ...
    def page_data(self, path: str | None = None, entry: str | None = None) -> dict: ...
    def book_search(self, query: str = '', root: str | None = None, *, params: Params | None = None) -> dict: ...
    def resolve_book_asset(self, relpath: str) -> tuple[Path, str] | None: ...
    def resolve_media_asset(self, relpath: str) -> tuple[Path, str] | None: ...


@dataclass(frozen=True)
class BookCatalog:
    """Adapt the published 不全书 SQLite projection without changing its entries or evidence."""

    db_path: Path
    source_root: Path

    def _read(self, view, *args, **kwargs):
        with closing(catalog.connect(self.db_path)) as db:
            return view(db, *args, **kwargs)

    def meta(self):
        return self._read(catalog.meta)

    def entries(self, params):
        return self._read(catalog.query, params)

    def facets(self, params):
        return self._read(catalog.facets, params)

    def detail(self, entry):
        return self._read(catalog.detail, entry)

    def layout(self, entry):
        return self._read(entry_layout, entry, self.source_root)

    def export(self, params):
        result = self._read(catalog.query, params, export=True)
        return catalog.csv_export(result['rows'], result['schema'])

    def source_text(self, path):
        with closing(catalog.connect(self.db_path)) as db:
            row = db.execute('SELECT text,sha256 FROM pages WHERE path=?', (path,)).fetchone()
            if row is None:
                raise LookupError('页面不存在')
            return tuple(row)


@dataclass(frozen=True)
class Collection:
    key: str
    zh: str
    en: str
    provider: CatalogProvider | None = None
    reader_url: str | None = None
    snapshot: str | None = None
    tour: TourProvider | object | None = None
    reader: ReaderProvider | object | None = None
    book_base: str | None = None
    media_base: str | None = None

    def describe(self):
        return {'id': self.key, 'label': {'zh': self.zh, 'en': self.en},
                'status': 'ready' if self.provider is not None else 'reserved'}


OUR5ETOOLS = Collection(
    'our5etools',
    'our5etools',
    'our5etools',
    book_base='/our5etools-book/',
    media_base='/our5etools-media/',
)
FIVETOOLS = Collection(
    '5etools',
    '5etools',
    '5etools',
    book_base='/5etools-book/',
    media_base='/5etools-media/',
)


class CollectionError(ValueError):
    def __init__(self, code, status):
        super().__init__(code)
        self.code, self.status = code, status


class CollectionRegistry:
    def __init__(self, book: BookCatalog, additional=()):
        self.book = book
        self._items = {DEFAULT: Collection(DEFAULT, '不全书', 'D&D 5e Incomplete Book', book)}
        for collection in additional:
            if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', collection.key):
                raise ValueError(f'Invalid collection key: {collection.key!r}')
            if collection.key in self._items:
                raise ValueError(f'Duplicate collection: {collection.key}')
            self._items[collection.key] = collection
        self._items.setdefault(OUR5ETOOLS.key, OUR5ETOOLS)
        self._items.setdefault(FIVETOOLS.key, FIVETOOLS)

    def describe(self):
        return [collection.describe() for collection in self._items.values()]

    def select(self, requested, *, book_only=False):
        if requested is None:
            return self._items[DEFAULT]
        if len(requested) != 1 or not requested[0]:
            raise CollectionError('invalid_collection', 400)
        collection = self._items.get(requested[0])
        if collection is None:
            raise CollectionError('unknown_collection', 404)
        if collection.provider is None:
            raise CollectionError('collection_unavailable', 409)
        if book_only and collection.key != DEFAULT:
            raise CollectionError('collection_unsupported', 400)
        return collection
