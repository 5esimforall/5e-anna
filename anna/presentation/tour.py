"""The tour: where to start and what to read next, laid over the book's own organisation.

Curated topics (`tour/tour.json`, shared by every collection) name a few starting points: a chapter in each
collection's own contents, an Anna query, or a collection's own listing, and the topics a contents node leads on
to. The library tour (`library_tour.py`) keeps the first column; this module reads the curation and answers the
book's levels below it from the source: contents children, the entries a page holds by text range, and recorded
relations between entries. Leads and hints quote the source; anna writes none.
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
SCHEMA = 2
# A node is named by one URL segment: a contents node (`n924`), an entry version (`ver_…`) or a topic key.
CONTENTS_SEGMENT = re.compile(r'n([0-9]+|page_[0-9a-f]+)')
ENTRY_SEGMENT = re.compile(r'ver_[0-9a-f]+')
TOPIC_KEY = re.compile(r'[a-z][a-z0-9-]*')
COLLECTION_KEY = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
# Contents dividers such as “——核心规则——” name the group of siblings that follows; bare rules only divide.
DIVIDER = re.compile(r'[—–-]{2,}(.*?)[—–-]{2,}')
LANGUAGES = ('zh', 'en')
RULESETS = ('5e', '5r')
BROWSE_KINDS = ('subclasses',)
ORIGIN_RANK = {'官方': 0, '合作': 1, '待核': 2}
CLASS_PREFIX = re.compile(r'^([^\s：:·\-]+)[：:]')
LEAF_CLASS_PREFIX = re.compile(r'^([^\s：:·\-]+)[-·：:]')
TRAILING_NOTE = re.compile(r'[（(][^）)]*[）)]$')
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
    name_en: str
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
    """A curated starting point. Its chapter is named once per collection by the collection's own contents path;
    its query, facet and browse view are asked of the whole library; a dynamic list is one collection's own listing."""

    key: str
    label: dict
    ruleset: str | None
    contents: dict
    query: dict | None
    facet: str | None
    sequence: bool
    dynamic: dict
    browse: str | None = None

    def contents_in(self, collection):
        return self.contents.get(collection)

    def dynamic_in(self, collection):
        return self.dynamic.get(collection)


def curation_revision(path) -> tuple[str, int, int, int]:
    """The file identity shared by the collection tours and the library's curated views."""
    try:
        stat = Path(path).stat()
    except OSError as exc:
        raise TourError('curation_invalid', 500, f'导览精选无法读取：{exc}') from exc
    return str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns


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
    for collection, places in choices.items():
        if not COLLECTION_KEY.fullmatch(str(collection)) or not isinstance(places, dict):
            problems.append(f'choices {collection!r}')
            continue
        for where, keys in places.items():
            if not isinstance(keys, list) or any(k not in topics for k in keys):
                problems.append(f'choices {collection!r} {where!r}')
    if problems:
        raise TourError('curation_invalid', 500, '导览精选格式无效：' + '；'.join(problems))
    return {'sections': sections, 'topics': topics, 'choices': choices}


def label_ok(label):
    return isinstance(label, dict) and all(isinstance(label.get(lang), str) and label[lang] for lang in LANGUAGES)


def per_collection(raw, key, field, problems):
    """A field named once per collection, as {collection: text}."""
    value = raw.get(field)
    if value is None:
        return {}
    if not isinstance(value, dict) or not value or any(
            not COLLECTION_KEY.fullmatch(str(k)) or not isinstance(v, str) or not v for k, v in value.items()):
        problems.append(f'topic {key!r} {field}')
        return {}
    return dict(value)


def topic_of(raw, problems):
    key = raw.get('key') if isinstance(raw, dict) else None
    # Topic keys share the URL with qualified nodes, so they may not look like a contents node or an entry.
    if not isinstance(key, str) or not TOPIC_KEY.fullmatch(key) or CONTENTS_SEGMENT.fullmatch(key) or key.startswith('ver_'):
        problems.append(f'topic {key!r}')
        return None
    query, facet, browse_kind = raw.get('query'), raw.get('facet'), raw.get('browse')
    if query is not None and (not isinstance(query, dict) or not all(
            isinstance(v, list) and v and all(isinstance(x, str) for x in v) for v in query.values())):
        problems.append(f'topic {key!r} query')
        query = None
    if facet is not None and (not isinstance(facet, str) or not re.fullmatch(r'[a-z_]+', facet) or query is None):
        problems.append(f'topic {key!r} facet')
    if browse_kind is not None and (browse_kind not in BROWSE_KINDS or facet is not None):
        problems.append(f'topic {key!r} browse')
        browse_kind = None
    if raw.get('ruleset') not in (None, *RULESETS):
        problems.append(f'topic {key!r} ruleset')
    if not label_ok(raw.get('label')):
        problems.append(f'topic {key!r} label')
    contents = per_collection(raw, key, 'contents', problems)
    dynamic = per_collection(raw, key, 'dynamic', problems)
    if not contents and query is None and not dynamic and browse_kind is None:
        problems.append(f'topic {key!r} leads nowhere')
    return Topic(key, raw.get('label'), raw.get('ruleset'), contents, query, facet, raw.get('sequence') is True, dynamic, browse_kind)


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
    """The book's levels of the tour in one snapshot: contents, page entries and relations read once.

    The library tour composes collections' tours: it keeps the curated first column, the shelf and the topics, and
    asks this one for the levels below them, named by the book's own segments (`n924`, `ver_…`)."""

    collection = '5echm'

    def __init__(self, db_path, nodes, locatable, curation, source_root):
        self.db_path = Path(db_path)
        self.source_root = Path(source_root)
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
            for row in db.execute("SELECT id, name, COALESCE(name_zh, ''), COALESCE(name_en, ''), type, ruleset, page_path, start, end, "
                                  "json_extract(data, '$.title_end'), COALESCE(json_extract(data, '$.parent_id'), ''), "
                                  "COALESCE(json_extract(data, '$.is_primary'), 0), json_extract(data, '$.table_span') IS NOT NULL "
                                  "FROM entries"):
                entry = Entry(*row[:10], row[10], bool(row[11]), bool(row[12]))
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
            self.source_names, self.source_meta = {}, {}
            for sid, name, origin, label in db.execute(
                    "SELECT id, name, COALESCE(origin, '待核'), COALESCE(json_extract(data, '$.label'), name) FROM sources"):
                self.source_names[sid] = name
                self.source_meta[sid] = {'name': name, 'origin': origin or '待核', 'label': label or name}
            self.entry_sources = defaultdict(list)
            for eid, sid in db.execute('SELECT entry_id, source_id FROM entry_sources ORDER BY source_id'):
                self.entry_sources[eid].append(sid)
            self.entry_stacks = {}
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_candidates'").fetchone():
                for gid, raw_data in db.execute("SELECT group_id, data FROM identity_candidates WHERE type='subclass'"):
                    for eid in json.loads(raw_data or '{}').get('entry_ids') or ():
                        self.entry_stacks[eid] = gid
            self.page_sources = {path: json.loads(ids or '[]') for path, ids in db.execute('SELECT path, source_ids FROM pages')}
        for path, entries in self.on_page.items():
            entries.sort(key=lambda e: (e.start, -e.end, e.id))
            self.roots[path] = self.nest(entries)
        for key in self.subclasses:
            self.subclasses[key].sort(key=self.reading_order)
        # Several PHB 2014 subclass pages share a category heading ("奥术传承", "神圣领域"); use the TOC label to tell them apart.
        self._generic_subclass_titles = self._find_generic_subclass_titles()
        self._subclass_records = self._build_subclass_records()
        # Page texts and query counts are read on demand and shared by every request on this snapshot.
        self.texts, self.counts, self.cache_lock = {}, {}, threading.Lock()
        self.choices = defaultdict(list)
        # Curated paths that name no single contents node in this book; topics show their status, choices drop out.
        self.unresolved = {}
        for key, topic in curation['topics'].items():
            if topic.contents_in(self.collection) and (status := self.resolve(topic.contents_in(self.collection))[0]) != 'ok':
                self.unresolved[('topic', key)] = status
        for where, keys in curation['choices'].get(self.collection, {}).items():
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

    def _chain_nodes(self, nid):
        chain = []
        while nid and nid in self.nodes:
            chain.append(self.nodes[nid])
            nid = self.nodes[nid]['parent_id']
        return chain[::-1]

    def _find_generic_subclass_titles(self):
        grouped = defaultdict(list)
        for e in self.entries.values():
            if e.type == 'subclass' and e.name_zh:
                grouped[(tuple(self.entry_sources.get(e.id, [])), e.name_zh)].append(e)
        titles = {}
        for (_, name_zh), items in grouped.items():
            if len(items) < 2:
                continue
            for e in items:
                nid = self.owning_node(e.path)
                label = self.nodes[nid]['label'].strip() if nid else ''
                if label and label != name_zh:
                    titles[e.id] = label
        return titles

    def entry_title(self, e):
        return self._generic_subclass_titles.get(e.id) or e.name

    def _build_subclass_records(self):
        """Subclasses in this snapshot with traceable owning-class evidence, for the library tour's subclass index."""
        class_names = {e.name_zh for e in self.entries.values() if e.type == 'class' and e.name_zh}
        class_pages = defaultdict(set)
        for e in self.entries.values():
            if e.type == 'class' and e.name_zh:
                class_pages[e.path].add(e.name_zh)
        class_by_path = {path: next(iter(names)) for path, names in class_pages.items() if len(names) == 1}

        def norm_class(label):
            cleaned = TRAILING_NOTE.sub('', label or '').strip()
            if cleaned == '邪术师' and '魔契师' in class_names:
                cleaned = '魔契师'
            return cleaned if cleaned in class_names else None

        def owner_of(e):
            if e.id in self.parent_class:
                parent = self.entries[self.parent_class[e.id]]
                if parent.name_zh:
                    return parent.name_zh
            nodes = self.page_nodes.get(e.path, [])
            for nid in nodes:
                chain = self._chain_nodes(nid)
                if len(chain) >= 3 and chain[0]['label'] == QUICK_REFERENCE and chain[1]['label'] == '职业速查':
                    found = class_by_path.get(chain[2]['path']) or norm_class(chain[2]['label'])
                    if found:
                        return found
            for nid in nodes:
                for anc in reversed(self._chain_nodes(nid)[:-1]):
                    if found := norm_class(anc['label']):
                        return found
            if m := CLASS_PREFIX.match(e.name):
                if found := norm_class(m.group(1)):
                    return found
            for nid in nodes:
                if m := LEAF_CLASS_PREFIX.match(self.nodes[nid]['label']):
                    if found := norm_class(m.group(1)):
                        return found
            nid = self.owning_node(e.path)
            if nid and (parent_id := self.nodes[nid]['parent_id']):
                sibs = {class_by_path[self.nodes[s]['path']] for s in self.children.get(parent_id, [])
                        if self.nodes[s]['path'] in class_by_path}
                if len(sibs) == 1:
                    return next(iter(sibs))
            if not nodes and e.path:
                stem = Path(e.path).stem
                if m := LEAF_CLASS_PREFIX.match(stem):
                    if found := norm_class(m.group(1)):
                        return found
            return None

        records = []
        for e in sorted((x for x in self.entries.values() if x.type == 'subclass'), key=lambda e: (e.path, e.start, e.id)):
            generic = e.id in self._generic_subclass_titles
            sids = list(self.entry_sources.get(e.id, []))
            metas = [self.source_meta[s] for s in sids if s in self.source_meta]
            primary = metas[0] if metas else None
            origin = min((m['origin'] for m in metas), key=lambda o: ORIGIN_RANK.get(o, 9), default='待核')
            records.append({
                'id': e.id,
                'title': self._generic_subclass_titles[e.id] if generic else (e.name_zh or e.name),
                'name_en': '' if generic else e.name_en,
                'owner_class': owner_of(e),
                'ruleset': e.ruleset,
                'source_ids': sids,
                'source_name': primary['name'] if primary else '',
                'source_short': primary['label'] if primary else '',
                'origin': origin,
                'parts_count': len(self.parts.get(e.id, [])),
                'stack': None if generic else self.entry_stacks.get(e.id),
                'is_copy': False,
            })
        return records

    def subclass_records(self):
        return self._subclass_records

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
        raise TourError('invalid_node', 400)

    def layout(self, segment):
        """A level's own words as the book sets them: what an expanded detail shows in place of its lead."""
        span = self.own_span(segment)
        if span is None:
            return {'status': 'empty'}
        path, start, stop, owner = span
        with closing(catalog.connect(self.db_path)) as db:
            return span_layout(db, path, start, stop, self.source_root, owner)

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
        item = {'id': eid, 'kind': 'entry', 'title': self.entry_title(e)}
        # A table's text is its cells; only prose has a first sentence worth quoting.
        if hint and not e.table:
            item['hint'] = first_sentence(self.entry_lead(e)) or None
        item.update({'type': e.type, 'type_label': catalog.TYPES.get(e.type, e.type), 'ruleset': e.ruleset, 'table': e.table,
                     'count': len(self.parts.get(eid, [])) or None})
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
        if CONTENTS_SEGMENT.fullmatch(segment):
            node = segment[1:]
            if node not in self.nodes:
                raise TourError('node_missing', 404)
            return self.contents_view(node)
        if ENTRY_SEGMENT.fullmatch(segment):
            if segment not in self.entries:
                raise TourError('node_missing', 404)
            return self.entry_view(self.entries[segment])
        raise TourError('invalid_node', 400)

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
            # The library tour answers each topic for the whole library; here it is only named.
            view['groups'].append(self.group('choices', [{'id': k, 'kind': 'topic'} for k in self.choices[node_id]]))
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
        view = {'id': e.id, 'kind': 'entry', 'trail': self.label_path[node].split(' / ') if node else [], 'title': self.entry_title(e),
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

    # Collections answered by Anna's own query, so counts and order read the same as in the table

    def count(self, db, params):
        where, values = catalog.filters(db, params)
        return db.execute('SELECT count(*) FROM entries e' + where, values).fetchone()[0]


class TourCache:
    """One tour per published snapshot and curation file; rebuilt when either changes."""

    def __init__(self):
        self.lock, self.key, self.tour = threading.Lock(), None, None

    def get(self, db_path, nodes, locatable, snapshot, source_root, curation_path=CURATION):
        key = (snapshot, curation_revision(curation_path))
        with self.lock:
            if self.key != key:
                self.tour, self.key = Tour(db_path, nodes, locatable(), load(curation_path), source_root), key
            return self.tour
