"""The reader's library: which collections answer a query, in what priority, and which of their works and rules.

A server offers collections (不全书, our5etools, …). Each reader chooses, in their own browser, which of them to
read and in what order, whether same-name versions share one row, and which rules generations and kinds of
content to include. The query page sends that choice with every request; this module turns it into one table.

A single collection answers through its own provider, as before. Several collections answer together: each
provider ranks its matching versions cheaply, the library orders them in one sequence, stacks same-name
versions across collections and reads only the shown rows in full. Collections keep their own entry IDs,
evidence and identities; a stack is a presentation of same-name candidates, never a merged identity.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
import csv
import io
import json
import re
import threading
import unicodedata
from dataclasses import dataclass, replace
from typing import Any

import anna.catalog.views as browse
from anna.presentation.collections import DEFAULT, Restriction

# Origins as each collection records them, read as one vocabulary of kinds of content.
CONTENT = ('official', 'partnered', 'unverified', 'curated', 'community')
CONTENT_OF_ORIGIN = {'官方': 'official', '合作': 'partnered', '待核': 'unverified', '本站精选': 'curated', '社区': 'community'}
# A published version heads its stack before homebrew of the same name; unverified origins sit between.
CONTENT_RANK = {'official': 0, 'partnered': 0, 'unverified': 1, 'curated': 2, 'community': 2}
RULESETS = ('5e', '5r', '待核')
VERSIONS = ('stacked', 'each')
PREFERENCES = {'5r': ('5r', '5e'), '5e': ('5e', '5r')}
KEY_RE = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
MAX_HIDDEN = 4000
BATCH, RESTORE_LIMIT = 150, 1500
NONE = browse.NONE
VERSION_FIELDS = ('id', 'collection', 'name', 'name_zh', 'name_en', 'ruleset', 'source_ids', 'source_names',
                  'fields', 'field_conflicts', 'quickref', 'legacy', 'package_id')


def _first(params, name, default=''):
    return params.get(name, [default])[0]


def content_of(origin: str | None) -> str:
    return CONTENT_OF_ORIGIN.get(origin or '待核', 'unverified')


def merge_order(lists):
    """One order containing every item: the first list as given, each later item right after its predecessor."""
    out: list = []
    for items in lists:
        previous = None
        for item in items:
            if item not in out:
                out.insert(out.index(previous) + 1 if previous is not None else len(out) if out else 0, item)
            previous = item
    return out


def comparable(row, field):
    """A field's value as versions from different collections compare it: each catalog's own normalized reading
    (its quick-reference tokens) where it has one, so 'M' and True for a material component read alike.
    Unknown, conflicting or inapplicable values never differ."""
    tokens = (row.get('quickref') or {}).get(field)
    if tokens is None:
        return browse.comparable(row.get('fields') or {}, field)
    if any(t.startswith('__') for t in tokens):
        return None
    return tuple(sorted(unicodedata.normalize('NFKC', re.sub(r'\s+', '', t)).casefold() for t in tokens))


def version_diffs(config, head, others):
    """Table columns whose values differ between the shown version and each other version, across collections."""
    out = {}
    for column in config['columns']:
        if column in browse.IDENTITY_COLUMNS:
            continue
        ids = [o['id'] for o in others if any(
            (a := comparable(head, f)) is not None and (b := comparable(o, f)) is not None and a != b
            for f in browse.column_fields(config, column))]
        if ids:
            out[column] = ids
    return out


def name_fold(text: str) -> str:
    """The English name as same-name stacking compares it (case, width and punctuation ignored)."""
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', text or '').casefold())


# --- The reader's scope ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Scope:
    """Which collections answer (in priority order), how versions show, and which rules and works count."""

    libraries: tuple[str, ...]
    pinned: bool = False
    rulesets: frozenset[str] | None = None
    content: frozenset[str] | None = None
    hidden: frozenset[tuple[str, str]] = frozenset()
    versions: str = 'stacked'
    prefer: str = '5r'
    unavailable: tuple[str, ...] = ()
    fallback: bool = False

    @property
    def narrowed(self) -> bool:
        return self.rulesets is not None or self.content is not None or bool(self.hidden)

    def restriction(self, key: str, works: list[dict]) -> Restriction | None:
        sources = None
        if self.content is not None or any(c == key for c, _ in self.hidden):
            sources = frozenset(w['id'] for w in works
                                if (self.content is None or content_of(w['origin']) in self.content) and (key, w['id']) not in self.hidden)
            if len(sources) == len(works):
                sources = None
        if self.rulesets is None and sources is None:
            return None
        return Restriction(self.rulesets, sources)

    def describe(self) -> dict:
        return {'libraries': list(self.libraries), 'pinned': self.pinned, 'versions': self.versions, 'prefer': self.prefer,
                'rulesets': sorted(self.rulesets) if self.rulesets is not None else list(RULESETS),
                'content': [c for c in CONTENT if self.content is None or c in self.content],
                'hidden': len(self.hidden), 'narrowed': self.narrowed,
                'unavailable': list(self.unavailable), 'fallback': self.fallback}


def _one(params, key):
    values = params.pop(key, None)
    if values is None:
        return None
    if len(values) != 1:
        raise ValueError(f'{key} 只能出现一次')
    return values[0]


def _choices(raw, allowed, label):
    if raw is None:
        return None
    chosen = frozenset(v for v in raw.split(',') if v)
    if not chosen or any(v not in allowed for v in chosen):
        raise ValueError('无效' + label)
    return None if chosen == frozenset(allowed) else chosen


def parse_scope(params: dict[str, list[str]], registry) -> Scope:
    """Take the library scope out of request parameters; what remains are the query's own conditions.

    `collection` pins one collection (a link from a collection's tour or book); otherwise `libraries` lists
    the reader's collections in priority order, of which those the server can open now answer."""
    pin = params.pop('collection', None)
    raw_libraries = _one(params, 'libraries')
    rulesets = _choices(_one(params, 'rulesets'), RULESETS, '规则版本范围')
    content = _choices(_one(params, 'content'), CONTENT, '内容范围')
    hidden = set()
    raw_hidden = params.pop('hide', [])
    if len(raw_hidden) > MAX_HIDDEN:
        raise ValueError('隐藏的作品过多')
    for item in raw_hidden:
        key, sep, work = item.partition(':')
        if not sep or not KEY_RE.fullmatch(key) or not work or len(work) > 300:
            raise ValueError('无效的隐藏作品')
        hidden.add((key, work))
    versions = _one(params, 'versions') or 'stacked'
    if versions not in VERSIONS:
        raise ValueError('无效版本显示方式')
    prefer = _one(params, 'prefer') or '5r'
    if prefer not in PREFERENCES:
        raise ValueError('无效规则版本偏好')
    common = dict(rulesets=rulesets, content=content, hidden=frozenset(hidden), versions=versions, prefer=prefer)
    if pin is not None:
        return Scope((registry.select(pin).key,), pinned=True, **common)
    requested = [DEFAULT] if raw_libraries is None else list(dict.fromkeys(k for k in raw_libraries.split(',') if k))
    if any(not KEY_RE.fullmatch(k) for k in requested):
        raise ValueError('无效资料库')
    ready = set(registry.ready())
    libraries = tuple(k for k in requested if k in ready)
    unavailable = tuple(k for k in requested if k not in ready)
    if not libraries:
        return Scope((DEFAULT,), unavailable=unavailable, fallback=True, **common)
    return Scope(libraries, unavailable=unavailable, **common)


# --- Per-collection facts, cached by each catalog's snapshot ------------------------------------------------------

_cache_lock = threading.Lock()
_facts: dict[tuple, Any] = {}
_rankings: OrderedDict = OrderedDict()


def _fact(collection, name, compute):
    provider = collection.provider
    key = (collection.key, name, provider.signature())
    with _cache_lock:
        if key in _facts:
            return _facts[key]
    value = compute()
    with _cache_lock:
        if len(_facts) > 64:
            _facts.clear()
        _facts[key] = value
    return value


def works_of(collection):
    return _fact(collection, 'works', collection.provider.works)


def capabilities_of(collection):
    return _fact(collection, 'capabilities', collection.provider.capabilities)


def meta_of(collection):
    return _fact(collection, 'meta', collection.provider.meta)


def describe_works(registry, collection_keys):
    """The works of each ready collection, with content kinds, for the reader's library settings."""
    out = []
    for key in collection_keys:
        collection = registry.get(key)
        if collection is None or collection.provider is None:
            continue
        works = [w | {'content': content_of(w['origin'])} for w in works_of(collection)]
        out.append({'id': key, 'works': works, 'total': sum(w['count'] for w in works)})
    return out


# --- Conditions as each collection can answer them ----------------------------------------------------------------

def _condition_key(key: str, conditions) -> bool:
    base = key.removeprefix('not_')
    return base.startswith('f_') or base in conditions


def localize(params, caps, every_condition, known_types):
    """The request's conditions as one collection can answer them.

    Returns the collection's own parameters and the fields of included conditions it cannot hold at all
    (another collection's type, work or field). Excluded values it cannot hold exclude nothing and drop."""
    local, unanswerable = {}, {}
    for key, values in params.items():
        if not _condition_key(key, every_condition):
            local[key] = values
            continue
        base = key.removeprefix('not_')
        negated = base != key
        if base == 'type':
            if any(v not in known_types for v in values):
                raise ValueError('未知条目类型')
            allowed = caps.types
        elif base == 'source':
            allowed = caps.sources | {NONE}
        elif base == 'package':
            allowed = caps.packages | {NONE}
        elif caps.values and base in caps.values:
            allowed = caps.values[base] | {NONE}
        else:
            allowed = None
        answerable = base in caps.conditions or (base.startswith('f_') and base[2:] in caps.facets)
        kept = [v for v in values if allowed is None or v in allowed] if answerable else []
        if kept:
            local[key] = kept
        elif not negated and any(values):
            unanswerable[base] = key
    return local, unanswerable


# --- The library -------------------------------------------------------------------------------------------------

class Library:
    """One table over the reader's collections."""

    def __init__(self, registry, scope: Scope):
        self.registry, self.scope = registry, scope
        self.members = [registry.get(k) for k in scope.libraries]
        self.rank = {c.key: i for i, c in enumerate(self.members)}

    # What any ready collection can answer: a condition none of them knows is passed on for its catalog to judge.
    @property
    def every_condition(self):
        return frozenset().union(*(capabilities_of(self.registry.get(k)).conditions for k in self.registry.ready()))

    @property
    def known_types(self):
        return frozenset().union(*(capabilities_of(self.registry.get(k)).types for k in self.registry.ready()))

    @property
    def primary(self):
        return self.members[0]

    @property
    def federated(self) -> bool:
        return len(self.members) > 1

    def restrict(self, collection):
        if not self.scope.narrowed:
            return None
        return self.scope.restriction(collection.key, works_of(collection))

    def _local(self, collection, params):
        if self.scope.pinned:
            return dict(params), {}
        return localize(params, capabilities_of(collection), self.every_condition, self.known_types)

    def _presentation(self, params):
        """A collection's own parameters for how versions show; defaults stay implicit, as a link would leave them."""
        out = dict(params)
        if self.scope.versions != 'stacked':
            out['versions'] = [self.scope.versions]
        if self.scope.prefer != '5r':
            out['prefer'] = [self.scope.prefer]
        return out

    def _answer(self, method, collection, params):
        """One collection's answer within the library scope; a collection read whole is asked as before."""
        restrict = self.restrict(collection)
        return method(params) if restrict is None else method(params, restrict)

    # Meta: one vocabulary over the collections, with each work marked by its collection.
    def meta(self) -> dict:
        if not self.federated:
            return self.primary.provider.meta()
        metas = [(c, meta_of(c)) for c in self.members]
        out = dict(metas[0][1])
        out['sources'] = [s | {'collection': c.key} for c, m in metas for s in m.get('sources', [])]
        out['packages'] = [p | {'collection': c.key} for c, m in metas for p in m.get('packages', [])]
        out['types'] = {k: v for _, m in reversed(metas) for k, v in m['types'].items()}
        out['fields'] = {k: v for _, m in reversed(metas) for k, v in m['fields'].items()}
        out['type_order'] = merge_order(m['type_order'] for _, m in metas)
        out['presets'] = list({p['name']: p for _, m in reversed(metas) for p in m.get('presets', [])}.values())
        conditions: dict[str, list[str]] = {}
        for _, m in metas:
            for t, keys in m.get('type_conditions', {}).items():
                conditions[t] = list(dict.fromkeys([*conditions.get(t, []), *keys]))
        out['type_conditions'] = conditions
        return out

    def schema(self, types):
        types = list(dict.fromkeys(types))
        schemas = [c.provider.schema(types) for c in self.members
                   if not types or any(t in capabilities_of(c).types for t in types)] or [self.primary.provider.schema([])]
        if len(schemas) == 1:
            return schemas[0]
        out = dict(schemas[0])
        for key in ('kinds', 'facets', 'flags', 'columns', 'export_columns', 'sorts'):
            out[key] = merge_order(s.get(key, []) for s in schemas)
        out['composites'] = {k: v for s in reversed(schemas) for k, v in (s.get('composites') or {}).items()}
        out['range'] = next((s['range'] for s in schemas if s.get('range')), None)
        return out

    # --- Entries ---

    def entries(self, params) -> dict:
        offset, limit = _window(params)
        if not self.federated:
            collection = self.primary
            local, unanswerable = self._local(collection, params)
            if unanswerable:
                return self._empty(params, offset)
            return self._answer(collection.provider.entries, collection, self._presentation(local))
        ranking = self._ranking(params)
        heads = ranking['stacks']
        shown = heads[offset:offset + limit]
        wanted: dict[str, list[str]] = {}
        for stack in shown:
            for key, eid in stack:
                wanted.setdefault(key, []).append(eid)
        full = {}
        for key, ids in wanted.items():
            rows = self.registry.get(key).provider.rows(ids)
            _mark(rows.values(), key)
            full.update(((key, eid), row) for eid, row in rows.items())
        schema = ranking['schema']
        rows = []
        for stack in shown:
            members = [full[m] for m in stack if m in full]
            if not members:
                continue
            row = members[0]
            if self.scope.versions == 'stacked':
                row['versions'] = [{k: v.get(k) for k in VERSION_FIELDS} for v in members]
                spans = len({m['collection'] for m in members}) > 1
                row['stack'] = {'id': 'library:' + row['collection'] + ':' + row['id'],
                                'status': 'library_candidate' if spans else 'same_name_candidate'} if len(members) > 1 else None
                row['version_diffs'] = version_diffs(schema, row, members[1:])
            rows.append(row)
        total = len(heads)
        return {'total': total, 'versions_total': ranking['versions_total'], 'versions': self.scope.versions,
                'prefer': self.scope.prefer, 'offset': offset, 'rows': rows, 'schema': schema,
                'section_matches': self._section_matches(params) if not total else 0}

    def _empty(self, params, offset):
        return {'total': 0, 'versions_total': 0, 'versions': self.scope.versions, 'prefer': self.scope.prefer,
                'offset': offset, 'rows': [], 'schema': self.schema(params.get('type', [])), 'section_matches': 0}

    def _section_matches(self, params):
        if not (params.get('q') or params.get('typing')) or _first(params, 'sections') == '1':
            return 0
        total = 0
        for collection in self.members:
            local, unanswerable = self._local(collection, params)
            if unanswerable or 'sections' not in capabilities_of(collection).conditions:
                continue
            result = self._answer(collection.provider.entries, collection, {**self._presentation(local), 'limit': ['1'], 'offset': ['0']})
            total += result.get('section_matches', 0)
        return total

    def _sort(self, params, schema):
        default = schema['default_sort']
        raw = _first(params, 'sort')
        desc = _first(params, 'dir', 'asc') == 'desc'
        if raw.startswith('-'):
            raw, desc = raw[1:], True
        if _first(params, 'dir', 'asc') not in {'asc', 'desc'}:
            raise ValueError('无效排序方向')
        searching = bool(browse.latest_term(params))
        sort = raw or (browse.MATCH if searching else default)
        if sort == browse.MATCH and not searching:
            sort = default
        if sort != browse.MATCH and sort not in schema['sorts']:
            raise ValueError('无效排序')
        return sort, desc, default

    def _ranking(self, params):
        """Every matching version in one order, stacked by the reader's choice; cached per query."""
        types = list(dict.fromkeys(params.get('type', [])))
        schema = self.schema(types)
        sort, desc, default = self._sort(params, schema)
        query = tuple(sorted((k, tuple(v)) for k, v in params.items() if k not in {'offset', 'limit', 'entry', 'tab'}))
        signature = tuple((c.key, c.provider.signature()) for c in self.members)
        cache_key = (query, self.scope, signature)
        with _cache_lock:
            if cache_key in _rankings:
                _rankings.move_to_end(cache_key)
                return _rankings[cache_key]
        direct = {browse.MATCH, 'name', 'name_en', 'type', 'ruleset', 'source'}
        # The provider reads a field's sort value only when the order needs it: the chosen field, or the default under match.
        field = sort if sort not in direct else default if sort == browse.MATCH and default not in direct else None
        rows = []
        for collection in self.members:
            local, unanswerable = self._local(collection, params)
            if unanswerable:
                continue
            stackable, lib = capabilities_of(collection).stackable, self.rank[collection.key]
            for r in collection.provider.ranked(local, self.restrict(collection), field):
                r.update(collection=collection.key, lib=lib, stackable=r['type'] in stackable)
                rows.append(r)
        ordered = self._ordered(rows, sort, desc, default, browse.latest_term(params))
        stacks = self._stacks(ordered)
        ranking = {'stacks': stacks, 'versions_total': len(ordered), 'schema': schema}
        with _cache_lock:
            _rankings[cache_key] = ranking
            while len(_rankings) > 12:
                _rankings.popitem(last=False)
        return ranking

    def _ordered(self, rows, sort, desc, default, latest):
        type_order = merge_order(meta_of(c)['type_order'] for c in self.members)
        type_rank = {t: i for i, t in enumerate(type_order)}

        def value(r, s):
            if s == 'name':
                return browse.name_key(r['name_zh'] or r['name'])
            if s == 'name_en':
                return browse.name_key(r['name_en'] or None)
            if s == 'type':
                return type_rank.get(r['type'], len(type_rank))
            if s == 'ruleset':
                return r['ruleset']
            if s == 'source':
                return (r['lib'], r['source_key'])
            v = r['value']
            return None if v is None else (0, v) if isinstance(v, (int, float)) else (1, str(v))

        def by(items, s, reverse):
            present = [r for r in items if value(r, s) is not None]
            present.sort(key=lambda r: value(r, s), reverse=reverse)
            return present + [r for r in items if value(r, s) is None]

        rows.sort(key=lambda r: (browse.name_key(r['name_zh'] or r['name']) or '', r['lib'], r['id']))
        if sort == browse.MATCH:
            rows = by(rows, default, False)
            rows.sort(key=lambda r: len(r['name_zh'] or r['name'] or ''))
            rows.sort(key=lambda r: browse.match_rank(r['name_zh'], r['name_en'], r['name'], latest), reverse=desc)
        else:
            rows = by(rows, sort, desc)
        for i, r in enumerate(rows):
            r['pos'] = i
        return rows

    def _stacks(self, rows):
        """Same-name published versions across collections share a row; each collection's own stacks are kept.
        The row shows a published version before homebrew, then the preferred rules, then the higher collection."""
        if self.scope.versions == 'each':
            return [[(r['collection'], r['id'])] for r in rows]
        parent: dict = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            parent[find(a)] = find(b)

        for r in rows:
            node = ('row', r['collection'], r['id'])
            find(node)
            if r['stack']:
                union(node, ('local', r['collection'], r['stack']))
            # Published versions (official, partnered or of unverified origin) stack by English name, as the book's
            # same-name candidates do; homebrew of the same name is another work's object and keeps its own stack.
            if r['stackable'] and r['name_en'] and CONTENT_RANK[content_of(r['origin'])] < 2:
                folded = name_fold(r['name_en'])
                if folded:
                    union(node, ('name', r['type'], folded))
        groups: dict = {}
        for r in rows:
            groups.setdefault(find(('row', r['collection'], r['id'])), []).append(r)
        preference = PREFERENCES[self.scope.prefer]

        def head_key(r):
            ruleset = preference.index(r['ruleset']) if r['ruleset'] in preference else 2
            return (CONTENT_RANK[content_of(r['origin'])], ruleset, r['lib'], r['pos'])

        stacks = []
        for members in groups.values():
            members.sort(key=head_key)
            stacks.append(members)
        stacks.sort(key=lambda m: m[0]['pos'])
        return [[(r['collection'], r['id']) for r in members] for members in stacks]

    # --- Facets ---

    def facets(self, params) -> dict:
        if not self.federated:
            collection = self.primary
            local, unanswerable = self._local(collection, params)
            if not unanswerable:
                return self._answer(collection.provider.facets, collection, local)
        types = list(dict.fromkeys(params.get('type', [])))
        merged: dict[str, dict] = {}
        order: list[str] = []
        total = scope_total = 0
        without: Counter = Counter()
        for collection in self.members:
            local, unanswerable = self._local(collection, params)
            answered = {k: v for k, v in local.items() if k.removeprefix('not_') not in unanswerable}
            result = self._answer(collection.provider.facets, collection, answered)
            counted = None if not unanswerable else set(unanswerable) if len(unanswerable) == 1 else set()
            if counted is None:
                total += result['total']
                without.update(result.get('without', {}))
            elif len(unanswerable) == 1:
                ((field, key),) = unanswerable.items()
                without[key] += result['total']
            if 'type' not in unanswerable:
                scope_total += result['scope_total']
            for group in result['groups']:
                key = group['key']
                if key not in merged:
                    merged[key] = {**group, 'options': []}
                    order.append(key)
                into = {o['value']: o for o in merged[key]['options']}
                for option in group['options']:
                    count = option.get('count', 0) if counted is None or key in counted else 0
                    if option['value'] in into:
                        into[option['value']]['count'] = into[option['value']].get('count', 0) + count
                    else:
                        fresh = {**option, 'count': count}
                        if key in {'source', 'package'}:
                            fresh['collection'] = collection.key
                        merged[key]['options'].append(fresh)
                        into[option['value']] = fresh
        if 'type' in merged:
            type_order = merge_order(meta_of(c)['type_order'] for c in self.members)
            rank = {t: i for i, t in enumerate(type_order)}
            merged['type']['options'].sort(key=lambda o: rank.get(o['value'], len(rank)))
        if self.federated and 'source' in merged:
            labels = Counter(o['label'] for o in merged['source']['options'])
            names = {c.key: c.zh for c in self.members}
            for o in merged['source']['options']:
                if labels[o['label']] > 1:
                    o['label'] = f"{o['label']}（{names.get(o.get('collection'), o.get('collection'))}）"
            merged['source']['options'].sort(key=lambda o: o['label'].lower())
        return {'schema': self.schema(types), 'groups': [merged[k] for k in order], 'total': total,
                'scope_total': scope_total, 'without': dict(without)}

    # --- Detail, layout and export ---

    def owner(self, entry_id: str):
        """The collection holding a version: a pinned page's own, else the reader's libraries first, then any other ready one."""
        if self.scope.pinned or len(self.registry.ready()) == 1:
            return self.primary
        for key in [*self.scope.libraries, *(k for k in self.registry.ready() if k not in self.rank)]:
            collection = self.registry.get(key)
            if entry_id in collection.provider.rows([entry_id]):
                return collection
        raise LookupError('条目不存在')

    def detail(self, entry_id: str) -> dict:
        collection = self.owner(entry_id)
        entry = collection.provider.detail(entry_id)
        # A reader spanning collections learns whose entry this is; a single collection answers as it always has.
        if collection.key != self.primary.key or self.federated:
            entry['collection'] = collection.key
        if self.federated and self.scope.versions == 'stacked':
            self._cross_versions(entry, collection)
        return entry

    def layout(self, entry_id: str) -> dict:
        return self.owner(entry_id).provider.layout(entry_id)

    def _cross_versions(self, entry, collection):
        """The reader's version comparison widened to same-name versions in the reader's other collections."""
        if not entry.get('name_en') or entry['type'] not in capabilities_of(collection).stackable:
            return
        folded = name_fold(entry['name_en'])
        foreign = []
        for other in self.members:
            if other.key == collection.key or entry['type'] not in capabilities_of(other).stackable:
                continue
            local, unanswerable = self._local(other, {'type': [entry['type']]})
            if unanswerable:
                continue
            ids = [r['id'] for r in other.provider.ranked(local, self.restrict(other)) if name_fold(r['name_en']) == folded]
            rows = other.provider.rows(ids)
            _mark(rows.values(), other.key)
            foreign.extend(rows.values())
        if not foreign:
            return
        own = entry.get('versions') or {'group_id': None, 'status': 'same_name_candidate',
                                         'members': [{k: entry.get(k) for k in VERSION_FIELDS}]}
        for m in own['members']:
            m.setdefault('collection', collection.key)
        members = [*own['members'], *({k: r.get(k) for k in VERSION_FIELDS} for r in foreign)]
        order = list(dict.fromkeys(k for m in members for k in browse.field_order({'type': entry['type'], 'fields': m['fields']})
                                   if k not in browse.METADATA_FIELDS))
        fields = []
        for k in order:
            known = {comparable(m, k) for m in members} - {None}
            fields.append({'field': k, 'values': [m['fields'].get(k) for m in members], 'differs': len(known) > 1})
        entry['versions'] = {'group_id': own.get('group_id'), 'status': 'library_candidate', 'members': members, 'fields': fields}

    def export(self, params) -> tuple[bytes, str]:
        if not self.federated:
            collection = self.primary
            local, unanswerable = self._local(collection, params)
            if not unanswerable:
                return self._answer(collection.provider.export, collection, local), collection.key
        each = Library(self.registry, replace(self.scope, versions='each'))
        ranking = each._ranking(params)
        rows = []
        ids = [m for stack in ranking['stacks'] for m in stack]
        by_collection: dict[str, list[str]] = {}
        for key, eid in ids:
            by_collection.setdefault(key, []).append(eid)
        full = {}
        for key, eids in by_collection.items():
            collection = self.registry.get(key)
            origins = {w['id']: w['origin'] for w in works_of(collection)}
            for eid, row in collection.provider.rows(eids).items():
                # Every row names its work's origin, whichever catalog records it on the row itself.
                row.setdefault('origin', next((origins[s] for s in row.get('source_ids') or [] if s in origins), ''))
                full[(key, eid)] = row
        for m in ids:
            if m in full:
                full[m]['collection'] = m[0]
                rows.append(full[m])
        return csv_export(rows, ranking['schema'], meta_of(self.primary)['types']), 'library'


def _mark(rows, key):
    for row in rows:
        row['collection'] = key
        for version in row.get('versions') or []:
            version.setdefault('collection', key)


def _window(params):
    try:
        offset, limit = int(_first(params, 'offset', '0')), int(_first(params, 'limit', str(BATCH)))
    except ValueError:
        raise ValueError('offset 与 limit 必须是整数') from None
    if offset < 0:
        raise ValueError('offset 不能为负数')
    if not 1 <= limit <= RESTORE_LIMIT:
        raise ValueError(f'每次读取 1 至 {RESTORE_LIMIT} 行')
    return offset, limit


def csv_export(rows, config, types):
    """Every matching version across the reader's collections, one per row, with its collection named."""
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    extras = [f for f in config['export_columns'] if f not in {'name', 'sources', 'ruleset', 'type', 'classification_status', 'legacy'}]
    labels = browse.COLUMN_LABELS
    writer.writerow(['资料库', '版本ID', '中文名', '英文名', '类型', '版本', '来源', '来源性质', '旧版标记', '字段冲突']
                    + [labels.get(f, f) for f in extras])
    for r in rows:
        cells = [r['collection'], r['id'], r.get('name_zh') or r.get('name'), r.get('name_en', ''), types.get(r['type'], r['type']),
                 r.get('ruleset', ''), '；'.join(r.get('source_names') or []), r.get('origin', ''), r.get('legacy', ''),
                 '；'.join(r.get('field_conflicts') or [])]
        for f in extras:
            value = r['fields'].get(f)
            cells.append(json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else '' if value is None else str(value))
        writer.writerow(["'" + s if s.lstrip().startswith(('=', '+', '-', '@')) else s for s in map(str, cells)])
    return ('﻿' + out.getvalue()).encode('utf-8')
