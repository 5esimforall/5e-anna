"""The tour over the reader's library: where to start and what to read next, whichever collections hold it.

The first column is curated once for every collection (`tour/tour.json`): a topic names its chapter in each
collection by that collection's own contents path, and asks its query of the whole library. The shelf lists the
library's works as Stella's shelf lays them out. Below a topic or a shelf group each level is a collection's own
(its contents, books, packages and entries), named `collection:segment` and answered by that collection's tour.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict, defaultdict

import anna.catalog.views as browse
from anna.presentation.alignment import UnionFind
from anna.presentation.library import CONTENT_RANK, Library, PREFERENCES, capabilities_of, content_of, name_fold
from anna.presentation.reader import ReaderError
from anna.presentation.shelf import SEPARATOR, Shelf, qualify, split
from anna.presentation.tour import CURATION, ENTRY_LIST, GROUP_LIMIT, TOPIC_KEY, TourError, curation_revision, divider, load

_curations: dict[tuple[str, int, int, int], dict] = {}
_views: OrderedDict = OrderedDict()
_subclass_indexes: OrderedDict = OrderedDict()
_lock = threading.Lock()
VIEW_CACHE = 256
INDEX_CACHE = 32

SUBCLASS_SEGMENT = re.compile(r'subclasses\.([a-z0-9_-]+)')
CORE_CLASSES = (
    ('野蛮人', 'barbarian'), ('吟游诗人', 'bard'), ('牧师', 'cleric'), ('德鲁伊', 'druid'),
    ('战士', 'fighter'), ('武僧', 'monk'), ('圣武士', 'paladin'), ('游侠', 'ranger'),
    ('游荡者', 'rogue'), ('术士', 'sorcerer'), ('魔契师', 'warlock'), ('法师', 'wizard'),
    ('奇械师', 'artificer'),
)
CORE_SLUGS = dict(CORE_CLASSES)
CORE_ORDER = {name: i for i, (name, _) in enumerate(CORE_CLASSES)}
UNASSIGNED_SLUG = 'unassigned'
UNASSIGNED_LABEL = {'zh': '未归属职业', 'en': 'Unassigned'}
ORIGIN_ORDER = ('官方', '合作', '本站精选', '社区', '待核')
ORIGIN_LABELS = {
    '官方': {'zh': '官方', 'en': 'Official'},
    '合作': {'zh': '合作内容', 'en': 'Partnered'},
    '本站精选': {'zh': '本站精选', 'en': 'Site curated'},
    '社区': {'zh': '社区扩展', 'en': 'Community'},
    '待核': {'zh': '待核来源', 'en': 'Unverified'},
}


def _subclass_slug(owner_class: str | None) -> str:
    if not owner_class:
        return UNASSIGNED_SLUG
    if owner_class in CORE_SLUGS:
        return CORE_SLUGS[owner_class]
    return 'c_' + hashlib.sha256(owner_class.encode('utf-8')).hexdigest()[:12]


def curation(path=CURATION):
    """A revision and its curated topics, read again when the file changes."""
    revision = curation_revision(path)
    with _lock:
        if revision in _curations:
            return revision, _curations[revision]
    found = load(path)
    with _lock:
        _curations.clear()
        _curations[revision] = found
    return revision, found


def _group(key, items, *, label=None, ordered=False):
    items = list(items)
    out = {'key': key, 'label': label, 'ordered': ordered, 'items': items[:GROUP_LIMIT]}
    if len(items) > GROUP_LIMIT:
        out['more'] = len(items) - GROUP_LIMIT
    return out


def _shaped(view):
    lead = view.get('lead')
    has_children = any(g.get('key') != 'relations' for g in view.get('groups') or ())
    view['shape'] = 'detail' if lead and (lead['truncated'] or not has_children) else 'index'
    return view


class LibraryTour:
    """One reader's tour: the collections of their library that have a tour, in their priority order."""

    def __init__(self, registry, scope, curation_path=None):
        self.registry, self.scope = registry, scope
        self.library = Library(registry, scope)
        self.members = [c for c in self.library.members if c.tour is not None]
        if not self.members:
            raise TourError('unavailable', 404)
        self.by_key = {c.key: c for c in self.members}
        self.curation_revision, self.curation = curation(curation_path or CURATION)
        self.topics = self.curation['topics']
        self._places: dict[str, tuple[str, str] | None] = {}
        try:
            self.shelf = Shelf(registry, scope)
        except ReaderError:
            self.shelf = None

    @property
    def signature(self) -> str:
        """The builds this tour reads: one value that changes whenever a collection in it is rebuilt."""
        raw = json.dumps([[c.key, c.snapshot] for c in self.members])
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def check(self, snapshot):
        if snapshot and snapshot != self.signature:
            raise TourError('snapshot_changed', 409)

    def member(self, key):
        if key not in self.by_key:
            raise TourError('node_missing', 404)
        return self.by_key[key]

    # --- Views ---

    def view(self, at: str = '') -> dict:
        key = (self.signature, self.curation_revision, self.library.scope, at)
        with _lock:
            if key in _views:
                _views.move_to_end(key)
                return _views[key]
        # Each collection's build too, for what the reader's workspace keeps of the levels shown here.
        found = _shaped(self._level(at)) | {'snapshot': self.signature, 'snapshots': {c.key: c.snapshot for c in self.members}}
        with _lock:
            _views[key] = found
            while len(_views) > VIEW_CACHE:
                _views.popitem(last=False)
        return found

    def _level(self, at):
        if not at:
            return self.root_view()
        if SEPARATOR in at:
            try:
                key, local = split(at)
            except ReaderError:
                raise TourError('invalid_node', 400) from None
            collection = self.member(key)
            shelved = self._shelf_divider(at)
            if shelved is not None:
                return self.divider_view(at, shelved)
            return self.qualified(collection.tour.view(local), key)
        if m := SUBCLASS_SEGMENT.fullmatch(at):
            return self.subclass_class_view(m.group(1))
        if TOPIC_KEY.fullmatch(at):
            if at not in self.topics or not self.available(self.topics[at]):
                raise TourError('node_missing', 404)
            return self.topic_view(self.topics[at])
        raise TourError('invalid_node', 400)

    def layout(self, at: str) -> dict:
        """A level's own words as its collection sets them."""
        if SEPARATOR in at:
            try:
                key, local = split(at)
            except ReaderError:
                raise TourError('invalid_node', 400) from None
            return self.member(key).tour.layout(local)
        if not at:
            return {'status': 'empty'}
        if m := SUBCLASS_SEGMENT.fullmatch(at):
            if m.group(1) not in self._subclasses_index():
                raise TourError('node_missing', 404)
            return {'status': 'empty'}
        topic = self.topics.get(at)
        if topic is None:
            raise TourError('node_missing', 404) if TOPIC_KEY.fullmatch(at) else TourError('invalid_node', 400)
        place = self.place(topic)
        return self.by_key[place[0]].tour.layout('n' + place[1]) if place else {'status': 'empty'}

    # --- Topics ---

    def available(self, topic) -> bool:
        """A topic shows when the library can answer it: its query, browse view, or a chapter or listing of a collection."""
        return topic.query is not None or topic.browse is not None or any(topic.contents_in(k) or topic.dynamic_in(k) for k in self.by_key)

    def place(self, topic):
        """Where a topic's chapter opens: in the collection whose text of the work the reader leads with, among
        those whose contents name the chapter exactly; otherwise in the first of them."""
        if topic.key in self._places:
            return self._places[topic.key]
        found = []
        for c in self.members:
            path = topic.contents_in(c.key)
            if path and (resolved := c.tour.resolve(path))[0] == 'ok':
                found.append((c.key, resolved[1]))
        if not found:
            self._places[topic.key] = None
            return None
        text = self._text_of(qualify(*found[0]))
        lead = text[0]['collection'] if text else None
        chosen = next((f for f in found if f[0] == lead), found[0])
        self._places[topic.key] = chosen
        return chosen

    def _text_of(self, node):
        """The texts of the work a shelf node belongs to, the leading one first."""
        if self.shelf is None:
            return None
        by_id = self.shelf.layout().by_id
        current = by_id.get(node)
        while current and 'texts' not in current:
            current = by_id.get(current['parent_id'])
        return current['texts'] if current else None

    def status(self, topic):
        named = [c for c in self.members if topic.contents_in(c.key)]
        if not named or self.place(topic):
            return 'ok'
        return named[0].tour.resolve(topic.contents_in(named[0].key))[0]

    def topic_item(self, key):
        topic = self.topics[key]
        item = {'id': key, 'kind': 'topic', 'label': topic.label, 'status': self.status(topic), 'ruleset': topic.ruleset, 'count': None}
        owner = self._listing_owner(topic)
        if topic.query:
            item['count'] = self.count(topic.query)
        elif topic.browse == 'subclasses':
            item['count'] = sum(len(stacks) for _, stacks in self._subclasses_index().values())
        elif owner:
            item['count'] = owner.tour.listing_count(topic)
        elif place := self.place(topic):
            item['count'] = self.by_key[place[0]].tour.below(place[1])
        return item

    def _listing_owner(self, topic):
        return next((c for c in self.members if topic.dynamic_in(c.key)), None)

    def topic_view(self, topic):
        owner = self._listing_owner(topic)
        if owner is not None:
            return self.qualified(owner.tour.view(topic.key), owner.key)
        view = {'id': topic.key, 'kind': 'topic', 'trail': [], 'label': topic.label, 'ruleset': topic.ruleset,
                'sources': [], 'status': self.status(topic), 'lead': None, 'read': None, 'anna': None, 'groups': []}
        place = self.place(topic)
        if place:
            key, node = place
            shown = self.qualified(self.by_key[key].tour.view('n' + node), key)
            view.update({k: shown.get(k) for k in ('sources', 'lead', 'read', 'anna', 'groups')})
            view['trail'] = [*shown.get('trail', []), shown['title']]
            view['collection'] = key
            if topic.sequence:
                for group in view['groups']:
                    group['ordered'] = group['key'] == 'contents'
        if topic.query:
            view['anna'] = {'query': topic.query, 'count': self.count(topic.query)}
        if topic.browse == 'subclasses':
            groups = self.subclasses_root_groups()
            if groups:
                view['groups'].extend(groups)
            elif topic.query:
                view['groups'].append(self.entry_list(topic.query))
            return view
        if topic.query:
            if topic.facet:
                view['groups'].append(self.facet_group(topic.query, topic.facet))
            elif not place:
                view['groups'].append(self.entry_list(topic.query))
        return view

    # --- Subclasses grouped by owning class across the reader's library ---

    def _subclasses_index(self) -> dict[str, tuple[str | None, list[list[dict]]]]:
        """Scoped, stacked subclasses across every member collection, grouped by owning class."""
        cache_key = (tuple((c.key, c.snapshot, c.provider.signature()) for c in self.members), self.library.scope)
        with _lock:
            if cache_key in _subclass_indexes:
                _subclass_indexes.move_to_end(cache_key)
                return _subclass_indexes[cache_key]

        pubs = self.library.publications
        preference = PREFERENCES[self.scope.prefer]
        raw_by_slug: dict[str, list[dict]] = defaultdict(list)
        owner_by_slug: dict[str, str | None] = {}

        for c in self.members:
            getter = getattr(c.tour, 'subclass_records', None)
            if getter is None:
                continue
            restrict = self.library.restrict(c)
            lib_rank = self.library.rank.get(c.key, len(self.library.rank))
            for r in getter():
                if restrict is not None:
                    if restrict.rulesets is not None and r['ruleset'] not in restrict.rulesets:
                        continue
                    if restrict.sources is not None:
                        sids = r.get('source_ids') or ()
                        if not sids or not any(sid in restrict.sources for sid in sids):
                            continue
                owner = r.get('owner_class') or None
                slug = _subclass_slug(owner)
                owner_by_slug.setdefault(slug, owner)
                sids = r.get('source_ids') or ()
                origin = r.get('origin') or '待核'
                content_rank = min((pubs.rank(c.key, sid, origin) for sid in sids),
                                   default=CONTENT_RANK[content_of(origin)])
                lead_rank = 0 if any((c.key, sid) in self.scope.lead for sid in sids) else 1
                ruleset_rank = preference.index(r['ruleset']) if r['ruleset'] in preference else 2
                raw_by_slug[slug].append({
                    **r,
                    'collection': c.key,
                    'lib': lib_rank,
                    'origin': origin,
                    'content_rank': content_rank,
                    'lead': lead_rank,
                    'ruleset_rank': ruleset_rank,
                })

        index: dict[str, tuple[str | None, list[list[dict]]]] = {}
        for slug, records in raw_by_slug.items():
            stacks = self._stack_subclasses(records)
            index[slug] = (owner_by_slug[slug], stacks)

        with _lock:
            _subclass_indexes[cache_key] = index
            while len(_subclass_indexes) > INDEX_CACHE:
                _subclass_indexes.popitem(last=False)
        return index

    def _stack_subclasses(self, records: list[dict]) -> list[list[dict]]:
        def member_key(r):
            return (r['content_rank'], r['ruleset_rank'], r['lead'], r['lib'],
                    1 if r.get('is_copy') else 0, -(r.get('parts_count') or 0),
                    browse.name_key(r['title']) or '', r['id'])

        if self.scope.versions == 'each':
            stacks = [[r] for r in records]
        else:
            uf = UnionFind()

            for r in records:
                node = ('row', r['collection'], r['id'])
                uf.find(node)
                if r.get('stack'):
                    uf.union(node, ('local', r['collection'], r['stack']))
                if folded := name_fold(r.get('name_en') or ''):
                    uf.union(node, ('name', folded))

            grouped: dict = {}
            for r in records:
                grouped.setdefault(uf.find(('row', r['collection'], r['id'])), []).append(r)
            stacks = [sorted(members, key=member_key) for members in grouped.values()]

        stacks.sort(key=lambda s: (s[0]['ruleset_rank'], browse.name_key(s[0]['title']) or '', s[0]['lib'], s[0]['id']))
        return stacks

    def subclasses_root_groups(self) -> list[dict]:
        index = self._subclasses_index()
        if not index:
            return []
        core = [(slug, owner, stacks) for slug, (owner, stacks) in index.items() if owner in CORE_ORDER]
        core.sort(key=lambda x: CORE_ORDER[x[1]])
        other = [(slug, owner, stacks) for slug, (owner, stacks) in index.items() if owner is not None and owner not in CORE_ORDER]
        other.sort(key=lambda x: (min(s[0]['content_rank'] for s in x[2]), -len(x[2]), browse.name_key(x[1]) or ''))
        unassigned = index.get(UNASSIGNED_SLUG)

        def class_item(slug, owner, stacks):
            if owner is None:
                return {'id': 'subclasses.' + slug, 'kind': 'class', 'label': UNASSIGNED_LABEL, 'count': len(stacks)}
            return {'id': 'subclasses.' + slug, 'kind': 'class', 'title': owner, 'count': len(stacks)}

        groups = []
        has_tail = bool(other or unassigned)
        if core:
            label = {'zh': '核心职业', 'en': 'Core classes'} if has_tail else {'zh': '按所属职业', 'en': 'By class'}
            groups.append(_group('classes', [class_item(*x) for x in core], label=label))
        if has_tail:
            tail = [class_item(*x) for x in other]
            if unassigned:
                tail.append(class_item(UNASSIGNED_SLUG, None, unassigned[1]))
            label = {'zh': '其他职业', 'en': 'Other classes'} if core else {'zh': '按所属职业', 'en': 'By class'}
            groups.append(_group('classes', tail, label=label))
        return groups

    def subclass_class_view(self, slug: str) -> dict:
        index = self._subclasses_index()
        if slug not in index:
            raise TourError('node_missing', 404)
        owner, stacks = index[slug]
        by_origin: dict[str, list[list[dict]]] = defaultdict(list)
        for stack in stacks:
            origin = stack[0]['origin']
            by_origin[origin if origin in ORIGIN_LABELS else '待核'].append(stack)

        multi = len(self.members) > 1
        primary_key = self.members[0].key
        groups = []
        for origin in ORIGIN_ORDER:
            origin_stacks = by_origin.get(origin)
            if not origin_stacks:
                continue
            items = []
            for stack in origin_stacks:
                head, others = stack[0], stack[1:]
                hint = head.get('source_name') or head.get('source_short') or ''
                if multi and head['collection'] != primary_key:
                    col_name = self.by_key[head['collection']].zh
                    hint = f'{hint} · {col_name}' if hint else col_name
                versions = []
                for v in others:
                    v_item = {
                        'id': qualify(v['collection'], v['id']),
                        'ruleset': v['ruleset'],
                        'source': v.get('source_short') or v.get('source_name') or '',
                    }
                    if multi and v['collection'] != head['collection']:
                        v_item['collection'] = v['collection']
                    versions.append(v_item)
                item = {
                    'id': qualify(head['collection'], head['id']),
                    'kind': 'entry',
                    'title': head['title'],
                    'type': 'subclass',
                    'type_label': '子职',
                    'ruleset': head['ruleset'],
                    'hint': hint or None,
                    'count': head.get('parts_count') or None,
                    'versions': versions,
                }
                if head.get('name_en'):
                    item['search'] = head['name_en']
                items.append(item)
            groups.append(_group('subclasses', items, label=ORIGIN_LABELS[origin]))

        anna = None
        if owner and any('owner_class' in capabilities_of(c).facets for c in self.members):
            query = {'type': ['subclass'], 'f_owner_class': [owner]}
            qcount = self.count(query)
            if qcount > 0:
                anna = {'query': query, 'count': qcount}

        topic = self.topics.get('subclasses')
        trail = [topic.label if topic else {'zh': '子职', 'en': 'Subclasses'}]
        view = {
            'id': 'subclasses.' + slug,
            'kind': 'class',
            'trail': trail,
            'ruleset': None,
            'sources': [],
            'status': 'ok',
            'lead': None,
            'read': None,
            'anna': anna,
            'groups': groups,
        }
        if owner is None:
            view['label'] = UNASSIGNED_LABEL
        else:
            view['title'] = owner
        return view

    # --- Answers from the whole library, as Anna's table gives them ---

    def count(self, query):
        """How many versions a query finds, as the facet counts beside it count them."""
        return self.library.count(query)

    def facet_group(self, query, facet):
        key = facet if facet == 'type' else 'f_' + facet
        found = next((g for g in self.library.facets(dict(query))['groups'] if g['key'] == key), None)
        options = [o for o in (found or {}).get('options', []) if o.get('count') and not str(o['value']).startswith('__')]
        if facet == 'type':
            chosen = set(query.get('type', []))
            options = [o for o in options if not chosen or o['value'] in chosen]
        items = [{'id': None, 'kind': 'query', 'title': o['label'], 'count': o['count'], 'query': {**query, key: [o['value']]}}
                 for o in options]
        return _group('facet', items, label=found['label'] if found else None)

    def entry_list(self, query):
        result = self.library.entries({**query, 'limit': [str(ENTRY_LIST)]})
        types = self.library.meta().get('types', {})
        items = []
        for row in result['rows']:
            key = row.get('collection') or self.library.primary.key
            label = types.get(row['type'])
            items.append({'id': qualify(key, row['id']), 'kind': 'entry', 'title': row.get('name_zh') or row['name'],
                          'type': row['type'], 'type_label': label.get('label') if isinstance(label, dict) else label,
                          'ruleset': row.get('ruleset'),
                          'versions': [{'id': qualify(v.get('collection') or key, v['id']), 'ruleset': v.get('ruleset')}
                                       for v in row.get('versions', []) if v['id'] != row['id']]})
        group = _group('entries', items)
        if result['total'] > len(items):
            group['more'] = result['total'] - len(items)
        return group

    # --- The shelf ---

    def _top(self):
        if self.shelf is None:
            return []
        return [n for n in self.shelf.layout().nodes if not n['parent_id'] and not n.get('alternate')
                and split(n['id'])[0] in self.by_key]

    def _segment(self, node):
        key, local = split(node['id'])
        return qualify(key, 'n' + local)

    def _shelf_divider(self, at):
        """The rows a named divider at the top of the shelf heads, or None for any other level."""
        top = self._top()
        for i, node in enumerate(top):
            if self._segment(node) == at and divider(node):
                rows = []
                for other in top[i + 1:]:
                    if divider(other):
                        break
                    rows.append(other)
                return rows
        return None

    def shelf_item(self, node):
        key, local = split(node['id'])
        item = {'id': self._segment(node), 'kind': 'contents', 'title': node['label'], 'status': node['status'],
                'count': self.by_key[key].tour.below(local)}
        if key != self.members[0].key:
            item['collection'] = key
        return item

    def shelf_items(self):
        """The top of the shelf: loose books first, then each named divider as one group."""
        items, top = [], self._top()
        for i, node in enumerate(top):
            name = divider(node)
            if name is None and not any(item['kind'] == 'shelf' for item in items):
                items.append(self.shelf_item(node))
            elif name:
                rows = self._shelf_divider(self._segment(node)) or []
                item = {'id': self._segment(node), 'kind': 'shelf', 'title': name, 'status': 'ok',
                        'count': sum(divider(n) is None for n in rows)}
                key = split(node['id'])[0]
                if key != self.members[0].key:
                    item['collection'] = key
                items.append(item)
        return items

    def divider_view(self, at, rows):
        key, local = split(at)
        shown = self.by_key[key].tour.view(local)
        groups, label, current = [], None, []
        for node in rows:
            name = divider(node)
            if name is None:
                current.append(self.shelf_item(node))
                continue
            if current:
                groups.append(_group('contents', current, label=label))
            label, current = name or None, []
        if current:
            groups.append(_group('contents', current, label=label))
        return {'id': at, 'kind': 'contents', 'trail': [], 'title': shown.get('title'), 'sources': shown.get('sources', []),
                'status': shown.get('status', 'ok'), 'lead': shown.get('lead'), 'read': None, 'anna': None,
                'groups': groups, 'collection': key}

    def root_view(self):
        groups = []
        for section in self.curation['sections']:
            items = [self.topic_item(k) for k in section['topics'] if self.available(self.topics[k])]
            if items:
                groups.append({'key': 'section', 'section': section['key'], 'label': section['label'], 'ordered': False, 'items': items})
        shelf = self.shelf_items()
        if shelf:
            groups.append(_group('shelf', shelf))
        return {'id': '', 'kind': 'root', 'groups': groups}

    # --- A collection's level, named for the library ---

    def qualified(self, view, key):
        """A collection's level with every node it names qualified by the collection, and the topics it names
        answered for the whole library."""
        name = lambda local: qualify(key, local) if local else local
        view = dict(view)
        groups = []
        for group in view.get('groups', []):
            items = []
            for item in group['items']:
                item = dict(item)
                if item.get('kind') == 'topic':
                    topic = self.topics.get(item['id'])
                    if topic is not None and self.available(topic):
                        items.append(self.topic_item(item['id']))
                    continue
                if item.get('id') and item.get('kind') in {'contents', 'entry', 'book', 'package'}:
                    item['id'] = name(item['id'])
                if item.get('versions'):
                    item['versions'] = [{**v, 'id': name(v['id'])} for v in item['versions']]
                items.append(item)
            if items:
                groups.append({**group, 'items': items})
        view['groups'] = groups
        if view.get('sequence'):
            view['sequence'] = {side: ({**item, 'id': name(item['id'])} if item else None) for side, item in view['sequence'].items()}
        if view.get('read'):
            view['read'] = {k: name(v) if k in {'root', 'page', 'entry'} else v for k, v in view['read'].items() if v}
        if view.get('kind') not in {'topic', 'root'}:
            view['id'] = name(view['id'])
        view['collection'] = key
        view.pop('shape', None)
        return view
