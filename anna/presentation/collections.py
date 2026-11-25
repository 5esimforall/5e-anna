"""Read-only catalog providers. A collection is a library, not a book's source code."""
from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
import threading
from typing import Any, Callable, Protocol
import weakref

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
    def schema(self, types: list[str], widened: bool = False) -> dict: ...
    def capabilities(self) -> Capabilities: ...
    def signature(self) -> tuple: ...


class TourProvider(Protocol):
    """Optional read-only guided tour over one collection; the library tour composes them."""

    def view(self, at: str = '') -> dict: ...
    def layout(self, segment: str) -> dict: ...
    def resolve(self, label_path: str) -> tuple[str, str | None]: ...
    def below(self, node_id: str) -> int | None: ...
    def subclass_records(self) -> list[dict]: ...


@dataclass(frozen=True)
class SearchHits:
    """One collection's full-text matches, best first: whether every term matched, (score, key) pairs, and how
    to show the match a key names. Collections rank by the same rule, so a library can interleave their hits."""

    match: str
    hits: list[tuple[int, Any]]
    result: Callable[[Any, int], dict]


class ReaderProvider(Protocol):
    """Optional read-only Stella reader over one collection's pages: contents, pages, full-text search and the
    assets its pages load. Addresses here are the collection's own; the library shelf qualifies them."""

    @property
    def snapshot(self) -> str: ...
    def navigation(self) -> dict: ...
    def page_data(self, path: str | None = None, entry: str | None = None) -> dict: ...
    def search(self, keyword: str, *, title_only: bool = False) -> SearchHits: ...


_thread_local = threading.local()
_name_fold_lock = threading.Lock()
_name_fold_cache: OrderedDict[tuple, dict[tuple[str, str], tuple[str, ...]]] = OrderedDict()


class _ConnectionSlot:
    """Own one thread-local read-only connection and close it cleanly if its thread or cache dies."""

    def __init__(self, conn):
        self.conn = conn
        self._close = weakref.finalize(self, conn.close)

    def close(self):
        self._close()


def _cached_book_connection(db_path: Path, signature: tuple):
    cache = getattr(_thread_local, 'book_conns', None)
    if cache is None:
        cache = OrderedDict()
        _thread_local.book_conns = cache
    key = str(Path(db_path).resolve())
    entry = cache.get(key)
    if entry is not None:
        cached_sig, slot = entry
        if cached_sig == signature:
            cache.move_to_end(key)
            return slot.conn
        cache.pop(key, None)
        slot.close()
    conn = catalog.connect(db_path, check_same_thread=False)
    cache[key] = (signature, _ConnectionSlot(conn))
    while len(cache) > 8:
        _, (_, old) = cache.popitem(last=False)
        old.close()
    return conn


def _clear_book_connections():
    cache = getattr(_thread_local, 'book_conns', None)
    if cache:
        for _, slot in cache.values():
            slot.close()
        cache.clear()


@dataclass(frozen=True)
class BookCatalog:
    """Adapt the published 不全书 SQLite projection without changing its entries or evidence."""

    CONDITIONS = frozenset({'type', 'q', 'typing', 'body', 'ruleset', 'legacy', 'status', 'source', 'origin',
                            'conflict', 'field', 'value', 'cr_min', 'cr_max', 'sections'})
    same_name_groups = True

    db_path: Path
    source_root: Path

    def _read(self, view, *args, **kwargs):
        try:
            sig = self.signature()
        except OSError:
            with closing(catalog.connect(self.db_path)) as db:
                return view(db, *args, **kwargs)
        db = _cached_book_connection(self.db_path, sig)
        return view(db, *args, **kwargs)

    def has(self, entry):
        return self._read(catalog.has, entry)

    def known_types(self) -> frozenset[str]:
        return frozenset(self._read(catalog.held_types))

    def count(self, params, restrict=None) -> int:
        return self._read(catalog.count, params, restrict)

    def same_name(self, entry_type, folded, include_ids=()):
        if not folded or entry_type not in browse.STACKABLE_TYPES:
            return self._read(catalog.same_name, entry_type, '', include_ids)
        if self._read(catalog._has_table, 'name_fold_index'):
            return self._read(catalog.same_name, entry_type, folded, include_ids)
        # Local unprojected databases lack name_fold_index; cache the folded lookup per snapshot.
        sig = self.signature()
        with _name_fold_lock:
            index = _name_fold_cache.get(sig)
            if index is not None:
                _name_fold_cache.move_to_end(sig)
        if index is None:
            from anna.presentation.library import name_fold
            built: dict[tuple[str, str], list[str]] = {}
            kinds = ','.join("'" + k + "'" for k in browse.STACKABLE_TYPES)
            rows = self._read(lambda db: db.execute(
                f"SELECT id, type, name_en FROM {catalog._entry_table(db)} "
                f"WHERE type IN ({kinds}) AND name_en IS NOT NULL AND name_en!=''"
            ).fetchall())
            for eid, typ, name_en in rows:
                f = name_fold(name_en)
                if f:
                    built.setdefault((typ, f), []).append(eid)
            index = {k: tuple(v) for k, v in built.items()}
            with _name_fold_lock:
                _name_fold_cache.clear()
                _name_fold_cache[sig] = index
        matched = index.get((entry_type, folded), ())
        merged = tuple(dict.fromkeys((*matched, *include_ids)))
        return self._read(catalog.same_name, entry_type, '', merged)

    def meta(self):
        return self._read(catalog.meta)

    def entries(self, params, restrict=None):
        return self._read(catalog.query, params, restrict=restrict)

    def facets(self, params, restrict=None):
        return self._read(catalog.facets, params, restrict)

    def detail(self, entry):
        return self._read(catalog.detail, entry)

    def versions(self, entry):
        return self._read(catalog.versions, entry)

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

    def schema(self, types, widened=False):
        return browse.schema_for([t for t in types if t in catalog.TYPES], widened)

    def capabilities(self):
        sources, held = self._read(
            lambda db: (
                frozenset(r[0] for r in db.execute('SELECT id FROM sources')),
                frozenset(catalog.held_types(db)),
            )
        )
        return Capabilities(
            conditions=self.CONDITIONS,
            facets=frozenset(browse.LABELS),
            types=held,
            sources=sources,
            values={key: frozenset(allowed) for key, (_, allowed) in catalog.COMMON_CHOICES.items()} | {'origin': frozenset(catalog.ORIGINS)},
            stackable=frozenset(browse.STACKABLE_TYPES),
            same_name_groups=self.same_name_groups,
        )

    def signature(self):
        stat = Path(self.db_path).stat()
        return (str(self.db_path), stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def source_text(self, path):
        row = self._read(lambda db: db.execute('SELECT text,sha256 FROM pages WHERE path=?', (path,)).fetchone())
        if row is None:
            raise LookupError('页面不存在')
        return tuple(row)


@dataclass(frozen=True)
class Collection:
    key: str
    zh: str
    en: str
    provider: CatalogProvider | None = None
    snapshot: str | None = None
    tour: TourProvider | None = None
    reader: ReaderProvider | None = None
    book_base: str | None = None
    media_base: str | None = None

    def describe(self):
        """The collection's name and what it offers: a query table, a tour, a book reader, and the build they share."""
        ready = self.provider is not None
        return {'id': self.key, 'label': {'zh': self.zh, 'en': self.en},
                'status': 'ready' if ready else 'reserved',
                'tour': ready and self.tour is not None,
                'reader': ready and self.reader is not None,
                'snapshot': self.snapshot if ready else None}


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


class CollectionRegistry:
    """The collections the server knows. The default book's tour and reader come with its validated bundle, so a
    query-only server registers the book without them."""

    def __init__(self, book: BookCatalog, additional=(), *, book_tour=None, book_reader=None, book_snapshot=None,
                 published_publications=None):
        self.book = book
        self.published_publications = published_publications
        self._items = {DEFAULT: Collection(DEFAULT, '不全书', 'D&D 5e Incomplete Book', book, snapshot=book_snapshot,
                                           tour=book_tour, reader=book_reader, book_base='/book/')}
        for collection in additional:
            if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', collection.key):
                raise ValueError(f'Invalid collection key: {collection.key!r}')
            if collection.key in self._items:
                raise ValueError(f'Duplicate collection: {collection.key}')
            self._items[collection.key] = collection
        self._items.setdefault(OUR5ETOOLS.key, OUR5ETOOLS)
        self._items.setdefault(FIVETOOLS.key, FIVETOOLS)

    def describe(self):
        """Every collection in registration order, with what each offers."""
        return [collection.describe() for collection in self._items.values()]

    def get(self, key):
        return self._items.get(key)

    def ready(self):
        return [key for key, collection in self._items.items() if collection.provider is not None]
