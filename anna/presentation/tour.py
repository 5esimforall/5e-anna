"""The tour: where to start and what to read next, laid over the book's own organisation.

Curated topics name a few starting points (a contents node, an Anna query, or both) and the topics a contents
node leads on to. Everything below them comes from the source: contents children, the entries a page holds
by text range, and recorded relations between entries. Leads and hints quote the source; anna writes none.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import re
import threading

import anna.catalog.views as browse
import anna.paths as paths
from anna.presentation import catalog
from anna.presentation.layout import span_layout, text_start
from anna.source.registry import QUICK_REFERENCE

CURATION = paths.PROJECT / 'tour' / 'tour.json'
SCHEMA = 1
# A node is named by one URL segment: a contents node (`n924`), an entry version (`ver_…`) or a topic key.
CONTENTS_SEGMENT = re.compile(r'n([0-9]+|page_[0-9a-f]+)')
ENTRY_SEGMENT = re.compile(r'ver_[0-9a-f]+')
TOPIC_KEY = re.compile(r'[a-z][a-z0-9-]*')
# Contents dividers such as “——核心规则——” name the group of siblings that follows; bare rules only divide.
DIVIDER = re.compile(r'[—–-]{2,}(.*?)[—–-]{2,}')
LANGUAGES = ('zh', 'en')
RULESETS = ('5e', '5r')
FACETS = ('type', *browse.LABELS)
LEAD_LIMIT = 220
HINT_LIMIT = 60
# Groups this short carry a hint per item; longer ones list names only.
HINTED_GROUP = 40
ENTRY_LIST = 60
GROUP_LIMIT = 300
SENTENCE_END = '。！？；!?'


class TourError(ValueError):
    def __init__(self, code, status, detail=''):
        super().__init__(detail or code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    name_zh: str
    type: str
    ruleset: str
    path: str
    start: int
    end: int
    title_end: int | None
    parent: str
    primary: bool
    table: bool


@dataclass(frozen=True)
class Topic:
    key: str
    label: dict
    ruleset: str | None
    contents: str | None
    query: dict | None
    facet: str | None
    sequence: bool


def load(path=CURATION):
    """Read and check the curated topics; a malformed file stops the tour instead of half-loading it."""
    try:
        data = json.loads(Path(path).read_text('utf-8'))
    except (OSError, ValueError) as exc:
        raise TourError('curation_invalid', 500, f'导览精选无法读取：{exc}') from exc
    problems = []
    if data.get('schema') != SCHEMA:
        problems.append('schema')
    sections, topics, seen = [], {}, set()
    for section in data.get('sections') or []:
        key = section.get('key')
        if not isinstance(key, str) or not TOPIC_KEY.fullmatch(key) or key in seen:
            problems.append(f'section {key!r}')
        seen.add(key)
        if not label_ok(section.get('label')):
            problems.append(f'section {key!r} label')
        keys = []
        for raw in section.get('topics') or []:
            topic = topic_of(raw, problems)
            if topic is None:
                continue
            if topic.key in topics:
                problems.append(f'topic {topic.key!r} repeated')
            topics[topic.key] = topic
            keys.append(topic.key)
        sections.append({'key': key, 'label': section.get('label'), 'topics': keys})
    choices = data.get('choices') or {}
    if not isinstance(choices, dict):
        problems.append('choices')
        choices = {}
    for where, keys in choices.items():
        if not isinstance(keys, list) or any(k not in topics for k in keys):
            problems.append(f'choices {where!r}')
    if problems:
        raise TourError('curation_invalid', 500, '导览精选格式无效：' + '；'.join(problems))
    return {'sections': sections, 'topics': topics, 'choices': choices}


def label_ok(label):
    return isinstance(label, dict) and all(isinstance(label.get(lang), str) and label[lang] for lang in LANGUAGES)


def topic_of(raw, problems):
    key = raw.get('key') if isinstance(raw, dict) else None
    # Topic keys share the URL with contents nodes and entries, so they may not look like either.
    if not isinstance(key, str) or not TOPIC_KEY.fullmatch(key) or CONTENTS_SEGMENT.fullmatch(key) or key.startswith('ver_'):
        problems.append(f'topic {key!r}')
        return None
    query, facet = raw.get('query'), raw.get('facet')
    if query is not None and (not isinstance(query, dict) or not all(
            isinstance(v, list) and v and all(isinstance(x, str) for x in v) for v in query.values())):
        problems.append(f'topic {key!r} query')
        query = None
    if facet is not None and (facet not in FACETS or query is None):
        problems.append(f'topic {key!r} facet')
    if raw.get('ruleset') not in (None, *RULESETS):
        problems.append(f'topic {key!r} ruleset')
    if not label_ok(raw.get('label')):
        problems.append(f'topic {key!r} label')
    if raw.get('contents') is None and query is None:
        problems.append(f'topic {key!r} leads nowhere')
    return Topic(key, raw.get('label'), raw.get('ruleset'), raw.get('contents'), query, facet, raw.get('sequence') is True)


def divider(node):
    """The group name a contents divider announces: '' for a bare rule, None for an ordinary node."""
    match = DIVIDER.fullmatch(node['label'].strip())
    return match.group(1).strip() if match else None


def clip(text, limit):
    """Shorten quoted source text at a sentence end where one falls late enough, else mid-sentence."""
    if len(text) <= limit:
        return text, False
    cut = max(text.rfind(mark, 0, limit) for mark in SENTENCE_END)
    if cut >= limit // 2:
        return text[:cut + 1], True
    return text[:limit].rstrip() + '…', True


def first_sentence(text):
    """The opening sentence when it is short enough to read as a hint; run-on text gives none."""
    ends = [i for i in (text.find(mark) for mark in SENTENCE_END) if 0 < i < HINT_LIMIT]
    return text[:min(ends) + 1] if ends else ''


class Tour:
    """One snapshot's tour: contents, page entries and relations read once, curated topics resolved against them."""

    def __init__(self, db_path, nodes, locatable, curation):
        self.db_path = Path(db_path)
        self.locatable = locatable
        self.nodes = {n['id']: n for n in nodes}
        self.children = defaultdict(list)
        for n in nodes:
            self.children[n['parent_id']].append(n['id'])
        self.label_path = {}
        self.by_label_path = defaultdict(list)
        self.page_nodes = defaultdict(list)
        for n in nodes:
            parent = self.label_path.get(n['parent_id'])
            self.label_path[n['id']] = f"{parent} / {n['label']}" if parent else n['label']
            self.by_label_path[self.label_path[n['id']]].append(n['id'])
            if n['path'] and divider(n) is None:
                self.page_nodes[n['path']].append(n['id'])
        self.entries, self.on_page = {}, defaultdict(list)
        self.parts, self.roots = defaultdict(list), {}
        with closing(catalog.connect(self.db_path)) as db:
            for row in db.execute("SELECT id, name, COALESCE(name_zh, ''), type, ruleset, page_path, start, end, "
                                  "json_extract(data, '$.title_end'), COALESCE(json_extract(data, '$.parent_id'), ''), "
                                  "COALESCE(json_extract(data, '$.is_primary'), 0), json_extract(data, '$.table_span') IS NOT NULL "
                                  "FROM entries"):
                entry = Entry(*row[:9], row[9], bool(row[10]), bool(row[11]))
                self.entries[entry.id] = entry
                self.on_page[entry.path].append(entry)
            self.parent_class, self.subclasses, self.feats = {}, defaultdict(list), defaultdict(list)
            for subject, predicate, target in db.execute(
                    "SELECT subject_id, predicate, object_id FROM relations "
                    "WHERE predicate IN ('belongs_to_class', 'grants_feat') AND object_id IS NOT NULL ORDER BY id"):
                if subject not in self.entries or target not in self.entries:
                    continue
                if predicate == 'grants_feat':
                    self.feats[subject].append(target)
                    continue
                self.parent_class[subject] = target
                if self.entries[subject].type == 'subclass':
                    self.subclasses[target].append(subject)
            self.source_names = {sid: name for sid, name in db.execute('SELECT id, name FROM sources')}
            self.entry_sources = defaultdict(list)
            for eid, sid in db.execute('SELECT entry_id, source_id FROM entry_sources ORDER BY source_id'):
                self.entry_sources[eid].append(sid)
            self.page_sources = {path: json.loads(ids or '[]') for path, ids in db.execute('SELECT path, source_ids FROM pages')}
        for path, entries in self.on_page.items():
            entries.sort(key=lambda e: (e.start, -e.end, e.id))
            self.roots[path] = self.nest(entries)
        for key in self.subclasses:
            self.subclasses[key].sort(key=self.reading_order)
        # Page texts and query counts are read on demand and shared by every request on this snapshot.
        self.texts, self.counts, self.cache_lock = {}, {}, threading.Lock()
        self.curation = curation
        self.topics = curation['topics']
        self.topic_nodes = {key: self.resolve(t.contents) for key, t in self.topics.items() if t.contents}
        self.choices = defaultdict(list)
        # Curated paths that name no single contents node; topics show their status, choices drop out.
        self.unresolved = {('topic', key): status for key, (status, _) in self.topic_nodes.items() if status != 'ok'}
        for where, keys in curation['choices'].items():
            status, node = self.resolve(where)
            if status == 'ok':
                self.choices[node].extend(keys)
            else:
                self.unresolved[('choices', where)] = status

    def owning_node(self, path):
        """Where the page sits in its own book: the quick-reference index lists pages but never owns them."""
        nodes = self.page_nodes.get(path, [])
        owned = [n for n in nodes if not self.label_path[n].startswith(QUICK_REFERENCE)]
        return (owned or nodes or [None])[0]

    def reading_order(self, eid):
        e = self.entries[eid]
        return (e.path, e.start, e.id)

    def nest(self, entries):
        """Entries nest as the build parsed the page's headings; an entry without a parent here is a page root."""
        roots = []
        for e in entries:
            parent = self.entries.get(e.parent)
            (self.parts[parent.id] if parent and parent.path == e.path else roots).append(e.id)
        return roots

    def resolve(self, label_path):
        """A curated contents path names exactly one node, or reports why it does not."""
        found = self.by_label_path.get(label_path, [])
        return ('ok', found[0]) if len(found) == 1 else ('missing' if not found else 'ambiguous', None)

    # Source text

    def page_text(self, path):
        with self.cache_lock:
            if path in self.texts:
                return self.texts[path]
        with closing(catalog.connect(self.db_path)) as db:
            row = db.execute('SELECT text FROM pages WHERE path=?', (path,)).fetchone()
        with self.cache_lock:
            if len(self.texts) > 512:
                self.texts.clear()
            self.texts[path] = row[0] if row else None
            return self.texts[path]

    # A level's own words: what it says before it divides into the rows below. The lead quotes the start of this
    # range; a level whose own words run past the lead reads as a page.

    def entry_span(self, e):
        """The entry's own words: after its title line and before its first part."""
        start = text_start({'start': e.start, 'end': e.end, 'title_end': e.title_end})
        parts = self.parts.get(e.id)
        stop = self.entries[parts[0]].start if parts else e.end
        return e.path, start, max(start, stop), e.id

    def page_entry(self, path):
        """The entry a page is about: its one primary root, as the build recognised it from the page title."""
        primary = [r for r in self.roots.get(path, []) if self.entries[r].primary]
        return self.entries[primary[0]] if len(primary) == 1 else None

    def page_span(self, path):
        entry = self.page_entry(path)
        if entry:
            return self.entry_span(entry)
        # Without a page entry the page's own words lie between its title line and its first entry.
        text = self.page_text(path) or ''
        title = re.match(r'\s*[^\n]*', text)
        start = title.end() if title else 0
        roots = self.roots.get(path, [])
        stop = self.entries[roots[0]].start if roots else len(text)
        return path, start, max(start, stop), None

    def span_text(self, span):
        path, start, stop, _ = span
        text = self.page_text(path)
        return ' '.join(text[start:stop].split()) if text else ''

    def entry_lead(self, e):
        return self.span_text(self.entry_span(e))

    def page_lead(self, path):
        return self.span_text(self.page_span(path))

    def own_span(self, segment):
        """The range a level's lead quotes, or None for a level that only leads on."""
        if not segment:
            return None
        if CONTENTS_SEGMENT.fullmatch(segment):
            node = self.nodes.get(segment[1:])
            if node is None:
                raise TourError('node_missing', 404)
            return self.page_span(node['path']) if node['status'] == 'present' else None
        if ENTRY_SEGMENT.fullmatch(segment):
            if segment not in self.entries:
                raise TourError('node_missing', 404)
            return self.entry_span(self.entries[segment])
        if TOPIC_KEY.fullmatch(segment):
            if segment not in self.topics:
                raise TourError('node_missing', 404)
            node = self.topic_nodes.get(segment, (None, None))[1]
            return self.own_span('n' + node) if node else None
        raise TourError('invalid_node', 400)

    def layout(self, segment, source_root):
        """A level's own words as the book sets them: what an expanded detail shows in place of its lead."""
        span = self.own_span(segment)
        if span is None:
            return {'status': 'empty'}
        path, start, stop, owner = span
        with closing(catalog.connect(self.db_path)) as db:
            return span_layout(db, path, start, stop, Path(source_root), owner)

    def lead(self, text):
        if not text:
            return None
        shown, truncated = clip(text, LEAD_LIMIT)
        return {'text': shown, 'truncated': truncated}

    @staticmethod
    def shaped(view):
        """A level with more of its own words than its lead shows, or nothing below them, reads as a detail;
        one whose lead already says all it has before the rows below reads as an index."""
        lead = view.get('lead')
        view['shape'] = 'detail' if lead and (lead['truncated'] or not view['groups']) else 'index'
        return view

    # Items: one row in a column, enough to name the node and say what lies below it

    def contents_count(self, node_id):
        return sum(divider(self.nodes[c]) is None for c in self.children.get(node_id, []))

    def below(self, node_id):
        """The rows a contents node opens onto: the entries on its page and its contents children."""
        return len(self.outline_ids(node_id)) + self.contents_count(node_id) or None

    def contents_item(self, node_id, hint=False):
        node = self.nodes[node_id]
        item = {'id': 'n' + node_id, 'kind': 'contents', 'title': node['label'], 'status': node['status']}
        if hint and node['status'] == 'present':
            item['hint'] = first_sentence(self.page_lead(node['path'])) or None
        item['count'] = self.below(node_id)
        return item

    def entry_item(self, eid, hint=False):
        e = self.entries[eid]
        item = {'id': eid, 'kind': 'entry', 'title': e.name}
        # A table's text is its cells; only prose has a first sentence worth quoting.
        if hint and not e.table:
            item['hint'] = first_sentence(self.entry_lead(e)) or None
        item.update({'type': e.type, 'type_label': catalog.TYPES.get(e.type, e.type), 'ruleset': e.ruleset, 'table': e.table,
                     'count': len(self.parts.get(eid, [])) or None})
        return item

    def topic_item(self, key):
        topic = self.topics[key]
        item = {'id': key, 'kind': 'topic', 'label': topic.label, 'status': 'ok', 'ruleset': topic.ruleset, 'count': None}
        if topic.contents:
            status, node = self.topic_nodes[key]
            item['status'] = status
        # A topic that names a collection counts its entries; a chapter counts the rows it opens onto.
        if topic.query:
            item['count'] = self.query_count(topic.query)
        elif item['status'] == 'ok':
            item['count'] = self.below(self.topic_nodes[key][1])
        return item

    def query_count(self, params):
        """How many entries a topic's query finds, counted once per snapshot."""
        key = json.dumps(params, sort_keys=True, ensure_ascii=False)
        with self.cache_lock:
            if key in self.counts:
                return self.counts[key]
        with closing(catalog.connect(self.db_path)) as db:
            count = self.count(db, params)
        with self.cache_lock:
            self.counts[key] = count
        return count

    def group(self, key, items, *, label=None):
        items = list(items)
        out = {'key': key, 'label': label, 'ordered': False, 'items': items[:GROUP_LIMIT]}
        if len(items) > GROUP_LIMIT:
            out['more'] = len(items) - GROUP_LIMIT
        return out

    # Views: one column

    def view(self, segment):
        return self.shaped(self.level(segment))

    def level(self, segment):
        if not segment:
            return self.root_view()
        if CONTENTS_SEGMENT.fullmatch(segment):
            node = segment[1:]
            if node not in self.nodes:
                raise TourError('node_missing', 404)
            return self.contents_view(node)
        if ENTRY_SEGMENT.fullmatch(segment):
            if segment not in self.entries:
                raise TourError('node_missing', 404)
            return self.entry_view(self.entries[segment])
        if TOPIC_KEY.fullmatch(segment):
            if segment not in self.topics:
                raise TourError('node_missing', 404)
            return self.topic_view(self.topics[segment])
        raise TourError('invalid_node', 400)

    def root_view(self):
        groups = [{'key': 'section', 'section': s['key'], 'label': s['label'], 'ordered': False,
                   'items': [self.topic_item(k) for k in s['topics']]} for s in self.curation['sections']]
        groups.append(self.group('shelf', self.shelf()))
        return {'id': '', 'kind': 'root', 'groups': groups}

    def shelf(self):
        """Top-level contents as the book shelf: loose books first, then each named divider as one group."""
        items = []
        for node_id in self.children.get('', []):
            name = divider(self.nodes[node_id])
            if name is None and not any(i['kind'] == 'shelf' for i in items):
                items.append(self.contents_item(node_id))
            elif name:
                items.append({'id': 'n' + node_id, 'kind': 'shelf', 'title': name, 'status': 'ok',
                              'count': sum(len(g) for _, g in self.divided(self.shelved(node_id)))})
        return items

    def shelved(self, divider_id):
        """The siblings a named divider heads: up to the next named divider."""
        siblings = self.children[self.nodes[divider_id]['parent_id']]
        out = []
        for node_id in siblings[siblings.index(divider_id) + 1:]:
            if divider(self.nodes[node_id]):
                break
            out.append(node_id)
        return out

    def divided(self, node_ids):
        """Split siblings at dividers, keeping each divider's name as its group's label."""
        groups, label, current = [], None, []
        for node_id in node_ids:
            name = divider(self.nodes[node_id])
            if name is None:
                current.append(node_id)
                continue
            if current:
                groups.append((label, current))
            label, current = name or None, []
        if current:
            groups.append((label, current))
        return groups

    def outline_ids(self, node_id):
        node = self.nodes[node_id]
        if node['status'] != 'present' or divider(node) is not None:
            return []
        entry, roots = self.page_entry(node['path']), self.roots.get(node['path'], [])
        if not entry:
            return roots
        # The page entry's own parts come first; other roots on the page follow in reading order.
        return self.parts.get(entry.id, []) + [r for r in roots if r != entry.id]

    def sources_of(self, source_ids):
        return [self.source_names[s] for s in source_ids if s in self.source_names]

    def contents_view(self, node_id):
        node, name = self.nodes[node_id], divider(self.nodes[node_id])
        ancestors, parent = [], node['parent_id']
        while parent:
            ancestors.append(self.nodes[parent]['label'])
            parent = self.nodes[parent]['parent_id']
        view = {'id': 'n' + node_id, 'kind': 'contents', 'trail': ancestors[::-1], 'title': name or node['label'],
                'sources': [], 'status': node['status'], 'lead': None, 'read': None, 'anna': None, 'groups': []}
        if node['status'] == 'present':
            view['sources'] = self.sources_of(self.page_sources.get(node['path'], []))
            view['lead'] = self.lead(self.page_lead(node['path']))
        if name is not None:
            # A divider heads the books after it; its own page only explains the group.
            hinted = len(self.shelved(node_id)) <= HINTED_GROUP
            view['groups'] = [self.group('contents', [self.contents_item(c, hinted) for c in ids], label=label)
                              for label, ids in self.divided(self.shelved(node_id))]
            return view
        view['read'] = {'root': node_id}
        entry = self.page_entry(node['path']) if node['status'] == 'present' else None
        if entry and entry.type != 'section':
            view['anna'] = {'entry': entry.id, 'type': entry.type}
        # The page's own headings and tables carry on from its lead, as the book reads before its subpages;
        # then the option sets this step leads to, then the contents below it.
        outline = self.outline_ids(node_id)
        if outline:
            view['groups'].append(self.group('outline', [self.entry_item(e, len(outline) <= HINTED_GROUP) for e in outline]))
        if self.choices.get(node_id):
            view['groups'].append(self.group('choices', [self.topic_item(k) for k in self.choices[node_id]]))
        children = self.children.get(node_id, [])
        hinted = self.contents_count(node_id) <= HINTED_GROUP
        view['groups'] += [self.group('contents', [self.contents_item(c, hinted) for c in ids], label=label)
                           for label, ids in self.divided(children)]
        view['sequence'] = self.sequence(node_id)
        return view

    def sequence(self, node_id):
        """The previous and next sibling, so a reader can walk the steps of a chapter in order."""
        siblings = [c for c in self.children[self.nodes[node_id]['parent_id']] if divider(self.nodes[c]) is None]
        i = siblings.index(node_id)
        return {'previous': self.contents_item(siblings[i - 1]) if i > 0 else None,
                'next': self.contents_item(siblings[i + 1]) if i + 1 < len(siblings) else None}

    def entry_view(self, e):
        node = self.owning_node(e.path)
        view = {'id': e.id, 'kind': 'entry', 'trail': self.label_path[node].split(' / ') if node else [], 'title': e.name,
                'ruleset': e.ruleset, 'type': e.type, 'type_label': catalog.TYPES.get(e.type, e.type),
                'sources': self.sources_of(self.entry_sources.get(e.id, [])), 'status': 'ok', 'lead': self.lead(self.entry_lead(e)),
                'read': {'entry': e.id} if e.id in self.locatable else None,
                'anna': {'entry': e.id, 'type': e.type} if e.type != 'section' else None, 'groups': []}
        parts = self.parts.get(e.id, [])
        if parts:
            view['groups'].append(self.group('parts', [self.entry_item(p, len(parts) <= HINTED_GROUP) for p in parts]))
        if e.id in self.parent_class and e.type == 'subclass':
            view['groups'].append(self.group('class', [self.entry_item(self.parent_class[e.id])]))
        if self.subclasses.get(e.id):
            view['groups'].append(self.group('subclasses', [self.entry_item(s) for s in self.subclasses[e.id]]))
        if self.feats.get(e.id):
            view['groups'].append(self.group('feats', [self.entry_item(f) for f in self.feats[e.id]]))
        if e.type == 'class' and e.name_zh:
            query = {'type': ['spell'], 'f_spell_classes': [e.name_zh], **({'ruleset': [e.ruleset]} if e.ruleset in RULESETS else {})}
            count = self.query_count(query)
            if count:
                view['groups'].append(self.group('spells', [{'id': None, 'kind': 'query', 'query': query, 'count': count,
                                                             'title': None, 'role': 'spells'}]))
        return view

    def topic_view(self, topic):
        view = {'id': topic.key, 'kind': 'topic', 'trail': [], 'label': topic.label, 'ruleset': topic.ruleset,
                'sources': [], 'status': 'ok', 'lead': None, 'read': None, 'anna': None, 'groups': []}
        if topic.contents:
            status, node = self.topic_nodes[topic.key]
            view['status'] = status
            if node:
                shown = self.contents_view(node)
                view.update({k: shown[k] for k in ('trail', 'sources', 'lead', 'read', 'anna', 'groups')})
                view['trail'] = view['trail'] + [shown['title']]
                if topic.sequence:
                    for group in view['groups']:
                        group['ordered'] = group['key'] == 'contents'
        if topic.query:
            view['anna'] = {'query': topic.query, 'count': self.query_count(topic.query)}
            with closing(catalog.connect(self.db_path)) as db:
                if topic.facet:
                    view['groups'].append(self.facet_group(db, topic.query, topic.facet))
                elif not topic.contents:
                    view['groups'].append(self.entry_list(db, topic.query))
        return view

    # Collections answered by Anna's own query, so counts and order read the same as in the table

    def count(self, db, params):
        conditions = catalog.conditions(params)
        sql = 'SELECT count(*) FROM entries e WHERE ' + catalog.base_clause(params) + ''.join(f' AND ({s})' for _, s, _ in conditions)
        return db.execute(sql, [v for _, _, values in conditions for v in values]).fetchone()[0]

    def facet_group(self, db, params, facet):
        key = facet if facet == 'type' else 'f_' + facet
        found = next((g for g in catalog.facets(db, params)['groups'] if g['key'] == key), None)
        options = [o for o in (found or {}).get('options', []) if o['count'] and not o['value'].startswith('__')]
        if facet == 'type':
            chosen = set(params.get('type', []))
            options = [o for o in options if not chosen or o['value'] in chosen]
        items = [{'id': None, 'kind': 'query', 'title': o['label'], 'count': o['count'],
                  'query': {**params, key: [o['value']]}} for o in options]
        return self.group('facet', items, label=found['label'] if found else None)

    def entry_list(self, db, params):
        result = catalog.query(db, {**params, 'limit': [str(ENTRY_LIST)]})
        items = []
        for row in result['rows']:
            item = self.entry_item(row['id']) if row['id'] in self.entries else {'id': row['id'], 'kind': 'entry', 'title': row['name']}
            item['versions'] = [{'id': v['id'], 'ruleset': v.get('ruleset')} for v in row.get('versions', []) if v['id'] != row['id']]
            items.append(item)
        group = self.group('entries', items)
        if result['total'] > len(items):
            group['more'] = result['total'] - len(items)
        return group


class TourCache:
    """One tour per published snapshot and curation file; rebuilt when either changes."""

    def __init__(self):
        self.lock, self.key, self.tour = threading.Lock(), None, None

    def get(self, db_path, nodes, locatable, snapshot, curation_path=CURATION):
        try:
            stat = Path(curation_path).stat()
        except OSError as exc:
            raise TourError('curation_invalid', 500, f'导览精选无法读取：{exc}') from exc
        key = (snapshot, str(curation_path), stat.st_mtime_ns, stat.st_size)
        with self.lock:
            if self.key != key:
                self.tour, self.key = Tour(db_path, nodes, locatable(), load(curation_path)), key
            return self.tour
