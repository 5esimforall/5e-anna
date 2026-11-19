"""The tour over the reader's library: where to start and what to read next, whichever collections hold it.

The first column is curated once for every collection (`tour/tour.json`): a topic names its chapter in each
collection by that collection's own contents path, and asks its query of the whole library. The shelf lists the
library's works as Stella's shelf lays them out. Below a topic or a shelf group each level is a collection's own
(its contents, books, packages and entries), named `collection:segment` and answered by that collection's tour.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict

from anna.presentation.library import Library
from anna.presentation.reader import ReaderError
from anna.presentation.shelf import SEPARATOR, Shelf, qualify, split
from anna.presentation.tour import CURATION, ENTRY_LIST, GROUP_LIMIT, TOPIC_KEY, TourError, curation_revision, divider, load

_curations: dict[tuple[str, int, int, int], dict] = {}
_views: OrderedDict = OrderedDict()
_lock = threading.Lock()
VIEW_CACHE = 256


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
    view['shape'] = 'detail' if lead and (lead['truncated'] or not view['groups']) else 'index'
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
        topic = self.topics.get(at)
        if topic is None:
            raise TourError('node_missing', 404) if TOPIC_KEY.fullmatch(at) else TourError('invalid_node', 400)
        place = self.place(topic)
        return self.by_key[place[0]].tour.layout('n' + place[1]) if place else {'status': 'empty'}

    # --- Topics ---

    def available(self, topic) -> bool:
        """A topic shows when the library can answer it: its query, or a chapter or listing of a collection in it."""
        return topic.query is not None or any(topic.contents_in(k) or topic.dynamic_in(k) for k in self.by_key)

    def place(self, topic):
        """Where a topic's chapter opens: in the collection whose text of the work the reader leads with, among
        those whose contents name the chapter exactly; otherwise in the first of them."""
        found = []
        for c in self.members:
            path = topic.contents_in(c.key)
            if path and (resolved := c.tour.resolve(path))[0] == 'ok':
                found.append((c.key, resolved[1]))
        if not found:
            return None
        text = self._text_of(qualify(*found[0]))
        lead = text[0]['collection'] if text else None
        return next((f for f in found if f[0] == lead), found[0])

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
            if topic.facet:
                view['groups'].append(self.facet_group(topic.query, topic.facet))
            elif not place:
                view['groups'].append(self.entry_list(topic.query))
        return view

    # --- Answers from the whole library, as Anna's table gives them ---

    def count(self, query):
        """How many versions a query finds, as the facet counts beside it count them."""
        return self.library.entries({**query, 'limit': ['1']})['versions_total']

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
