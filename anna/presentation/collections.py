"""Read-only catalog providers. A collection is a library, not a book's source code."""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Protocol

import anna.catalog.views as browse
from anna.presentation import catalog
from anna.presentation.layout import entry_layout

DEFAULT = '5echm'
Params = dict[str, list[str]]


@dataclass(frozen=True)
class Restriction:
    """The part of one catalog a reader's library includes: rules generations and works (None: all of them)."""

    rulesets: frozenset[str] | None = None
    sources: frozenset[str] | None = None


@dataclass(frozen=True)
class Capabilities:
    """What a catalog can answer, so a library spanning several catalogs can tell a condition
    that holds for none of a catalog's entries from one it simply does not constrain."""

    conditions: frozenset[str]
    facets: frozenset[str]
    types: frozenset[str]
    sources: frozenset[str]
    packages: frozenset[str] = frozenset()
    values: dict[str, frozenset[str]] | None = None
    stackable: frozenset[str] = frozenset()
    # Whether the catalog's own version groups already gather the same-name entries of its different works.
    # When they do, the catalog read alone stacks its rows itself; otherwise the library stacks them.
    same_name_groups: bool = True


class CatalogProvider(Protocol):
    """The existing query, facet, detail and reading projections, regardless of storage.

    `restrict` bounds every answer to the reader's library scope. `works`, `ranked`, `rows`, `schema`,
    `capabilities` and `signature` let a library combine several catalogs into one table."""

    def meta(self) -> dict: ...
    def entries(self, params: Params, restrict: Restriction | None = None) -> dict: ...
    def facets(self, params: Params, restrict: Restriction | None = None) -> dict: ...
    def detail(self, entry: str) -> dict: ...
    def layout(self, entry: str) -> dict: ...  # HTML, if any, must be inert and sanitized.
    def export(self, params: Params, restrict: Restriction | None = None) -> bytes: ...
    def works(self) -> list[dict]: ...
    def ranked(self, params: Params, restrict: Restriction | None = None, sort_field: str | None = None) -> list[dict]: ...
    def rows(self, ids: list[str]) -> dict[str, dict]: ...
    def schema(self, types: list[str]) -> dict: ...
    def capabilities(self) -> Capabilities: ...
    def signature(self) -> tuple: ...


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

    def entries(self, params, restrict=None):
        return self._read(catalog.query, params, restrict=restrict)

    def facets(self, params, restrict=None):
        return self._read(catalog.facets, params, restrict)

    def detail(self, entry):
        return self._read(catalog.detail, entry)

    def layout(self, entry):
        return self._read(entry_layout, entry, self.source_root)

    def export(self, params, restrict=None):
        result = self._read(catalog.query, params, export=True, restrict=restrict)
        return catalog.csv_export(result['rows'], result['schema'])

    def works(self):
        return self._read(catalog.works)

    def ranked(self, params, restrict=None, sort_field=None):
        return self._read(catalog.ranked, params, restrict, sort_field)

    def rows(self, ids):
        return self._read(catalog.rows, ids)

    def schema(self, types):
        return browse.schema_for([t for t in types if t in catalog.TYPES])

    def capabilities(self):
        meta = self.meta()
        return Capabilities(
            conditions=frozenset({'type', 'q', 'typing', 'body', 'ruleset', 'legacy', 'status', 'source', 'origin',
                                  'conflict', 'field', 'value', 'cr_min', 'cr_max', 'sections'}),
            facets=frozenset(browse.LABELS), types=frozenset(meta['type_order']),
            sources=frozenset(s['id'] for s in meta['sources']),
            values={key: frozenset(allowed) for key, (_, allowed) in catalog.COMMON_CHOICES.items()} | {'origin': frozenset(catalog.ORIGINS)},
            stackable=frozenset(browse.STACKABLE_TYPES),
        )

    def signature(self):
        stat = Path(self.db_path).stat()
        return (str(self.db_path), stat.st_ino, stat.st_size, stat.st_mtime_ns)

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

    def describe(self, *, tour=None, reader=None, snapshot=None):
        """The collection's name and what it offers: a query table, a tour, a book reader, and the build they share."""
        ready = self.provider is not None
        return {'id': self.key, 'label': {'zh': self.zh, 'en': self.en},
                'status': 'ready' if ready else 'reserved',
                'tour': ready and (self.tour is not None if tour is None else tour),
                'reader': ready and ((self.reader is not None or self.reader_url is not None) if reader is None else reader),
                'snapshot': (self.snapshot if snapshot is None else snapshot) if ready else None}


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

    def describe(self, *, book_reader=False, book_snapshot=None):
        """Every collection in registration order; the default book offers its tour and reader through the unified server."""
        return [collection.describe(tour=book_reader, reader=book_reader, snapshot=book_snapshot) if key == DEFAULT else collection.describe()
                for key, collection in self._items.items()]

    def get(self, key):
        return self._items.get(key)

    def ready(self):
        return [key for key, collection in self._items.items() if collection.provider is not None]

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
