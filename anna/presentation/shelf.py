"""One bookshelf over the reader's collections.

Stella reads every collection in the reader's library as one shelf. The first collection lays out the shelf:
its groups and order; each later collection adds the works the earlier ones lack, under its own groups. A work
that several collections hold (the same publication, as the library aligns works) stands once, where the
earliest collection places it, and opens in the text the reader leads with; its other texts stay on the shelf
beside it, out of the contents until the reader turns to one of them.

Every address on the shelf names its collection: `collection:node`, `collection:page path` and
`collection:entry`. Each collection keeps its own nodes, pages, entries and build snapshot.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import re
import threading
import unicodedata

from anna.presentation.library import KEY_RE, name_fold, publications_of, works_of
from anna.presentation.reader import SCHEMA, ReaderError
from anna.presentation.tour import divider

SEPARATOR = ':'
SEARCH_PAGE_LIMIT = 200
# Evidence for the place in another text: the entries a page defines, matched by type and English name.
COUNTERPART_ENTRIES = 40


def qualify(collection: str, local: str) -> str:
    return collection + SEPARATOR + local if local else ''


def split(address: str) -> tuple[str, str]:
    """The collection and its own address; an address without a collection names nothing."""
    key, sep, local = (address or '').partition(SEPARATOR)
    if not sep or not KEY_RE.fullmatch(key) or not local:
        raise ReaderError('invalid_location', 400)
    return key, local


@dataclass(frozen=True)
class Text:
    """One collection's text of a publication: the node its contents open at, and the work it belongs to."""

    collection: str
    root: str
    work: str


@dataclass
class Layout:
    """A shelf as laid out once for a set of collections and leading texts."""

    nodes: list[dict]
    by_id: dict[str, dict]
    texts: dict[str, list[Text]]
    root_text: dict[tuple[str, str], str]


_layouts: dict[tuple, Layout] = {}
_names: dict[tuple, dict] = {}
_lock = threading.Lock()


class Shelf:
    """The reader's collections that have a book reader, in the reader's priority order."""

    def __init__(self, registry, scope):
        self.registry, self.scope = registry, scope
        self.members = [c for c in (registry.get(k) for k in scope.libraries) if c is not None and c.reader is not None]
        if not self.members:
            raise ReaderError('unavailable', 404)
        self.by_key = {c.key: c for c in self.members}

    def member(self, key):
        """A collection on this shelf; one the reader's library leaves out is not on it."""
        if key in self.by_key:
            return self.by_key[key]
        known = self.registry.get(key)
        raise ReaderError('outside_library' if known is not None and known.reader is not None else 'invalid_location',
                          404 if known is not None else 400)

    @property
    def signature(self) -> str:
        """The builds this shelf reads: one value that changes whenever a collection on it is rebuilt."""
        raw = json.dumps([[c.key, c.reader.snapshot] for c in self.members])
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def check(self, key, snapshot):
        if snapshot and snapshot != self.member(key).reader.snapshot:
            raise ReaderError('snapshot_changed', 409)

    # --- Layout ---

    def _leads(self, texts: list[Text]) -> list[Text]:
        """A publication's texts, the one the reader leads with first, then in library order."""
        lead = next((t for t in texts if (t.collection, t.work) in self.scope.lead), texts[0])
        return [lead, *(t for t in texts if t is not lead)]

    def layout(self) -> Layout:
        key = (tuple((c.key, c.reader.snapshot) for c in self.members),
               tuple(sorted(w for w in self.scope.lead if w[0] in self.by_key)))
        with _lock:
            if key in _layouts:
                return _layouts[key]
        found = self._lay_out()
        with _lock:
            if len(_layouts) > 8:
                _layouts.clear()
            _layouts[key] = found
        return found

    def _lay_out(self) -> Layout:
        trees = {}
        for c in self.members:
            rows = c.reader.navigation()['nodes']
            children = defaultdict(list)
            for row in rows:
                children[row.get('parent_id') or ''].append(row['id'])
            trees[c.key] = ({row['id']: row for row in rows}, children)
        publications = publications_of(self.registry) if len(self.registry.ready()) > 1 else None
        texts: dict[str, list[Text]] = defaultdict(list)
        root_text: dict[tuple[str, str], str] = {}
        # Texts of one publication meet only where several collections share the shelf.
        for c in self.members if len(self.members) > 1 else ():
            nodes = trees[c.key][0]
            for work in works_of(c):
                root = work.get('reader_root')
                if not root or root not in nodes or (c.key, root) in root_text:
                    continue
                publication = publications.of(c.key, work['id']) if publications else c.key + SEPARATOR + work['id']
                if any(t.collection == c.key for t in texts[publication]):
                    continue
                texts[publication].append(Text(c.key, root, work['id']))
                root_text[(c.key, root)] = publication
        shared = {p: self._leads(t) for p, t in texts.items() if len(t) > 1}

        out: list[dict] = []
        placed: set[tuple[str, str]] = set()

        def emit(key, node_id, parent, extra=None):
            nodes, children = trees[key]
            node = nodes[node_id]
            address = qualify(key, node_id)
            # Empty paths and fragments are left out: the shelf holds every node of every collection read.
            record = {'id': address, 'parent_id': parent, 'label': node['label'], 'status': node['status']}
            if node.get('path'):
                record['path'] = qualify(key, node['path'])
            if node.get('fragment'):
                record['fragment'] = node['fragment']
            if extra:
                record.update(extra)
            out.append(record)
            for child in children.get(node_id, []):
                visit(key, child, address)

        def visit(key, node_id, parent):
            publication = root_text.get((key, node_id))
            if publication not in shared:
                emit(key, node_id, parent)
                return
            if (key, node_id) in placed:
                return
            # The publication stands where the earliest collection places it, each text beside the others.
            leads = shared[publication]
            names = [{'id': qualify(t.collection, t.root), 'collection': t.collection} for t in leads]
            for i, text in enumerate(leads):
                placed.add((text.collection, text.root))
                emit(text.collection, text.root, parent, {'texts': names, **({'alternate': True} if i else {})})

        for c in self.members:
            for node_id in trees[c.key][1].get('', []):
                visit(c.key, node_id, '')
        nodes = _pruned(out, {c.key for c in self.members[1:]})
        return Layout(nodes, {n['id']: n for n in nodes}, shared, root_text)

    # --- Navigation ---

    def navigation(self) -> dict:
        layout = self.layout()
        first = self.members[0].reader.navigation()
        collections = []
        for c in self.members:
            nav = c.reader.navigation()
            collections.append({
                'id': c.key, 'label': {'zh': c.zh, 'en': c.en}, 'snapshot': c.reader.snapshot,
                'page_base': nav.get('page_base') or nav.get('content_base') or c.book_base,
                'lazy_media': bool(nav.get('lazy_media')), 'cached_media': nav.get('cached_media') or [],
                'source_release': nav.get('source_release'),
            })
        return {'schema': SCHEMA, 'signature': self.signature, 'nodes': layout.nodes,
                'default_page': qualify(self.members[0].key, first.get('default_page') or ''),
                'collections': collections}

    # --- Pages ---

    def page(self, *, path: str = '', entry: str = '', snapshot: str = '') -> dict:
        if bool(path) == bool(entry):
            raise ReaderError('invalid_location', 400)
        key, local = split(path or entry)
        collection = self.member(key)
        self.check(key, snapshot)
        data = collection.reader.page_data(path=local if path else None, entry=local if entry else None)
        return {**data, 'collection': key, 'path': qualify(key, data['path'])}

    # --- Full-text search ---

    def search(self, keyword: str, *, title_only=False, page=1, page_size=30) -> dict:
        """Every collection's matches as one list, best first. Pages that match every term outrank the rest: when
        some collection has such pages, the others' partial matches do not show, as within one collection."""
        if len(keyword) > 2000:
            raise ReaderError('invalid_search', 400)
        found = [(rank, c, c.reader.search(keyword, title_only=title_only)) for rank, c in enumerate(self.members)]
        complete = any(hits.match == 'all' and hits.hits for _, _, hits in found)
        ranked = sorted(((score, rank, index, c, hits) for rank, c, hits in found if hits.match == 'all' or not complete
                         for score, index in hits.hits), key=lambda item: (-item[0], item[1], str(item[2])))
        page_size = min(SEARCH_PAGE_LIMIT, max(1, page_size))
        start = (max(1, page) - 1) * page_size
        results = []
        for score, _, index, c, hits in ranked[start:start + page_size]:
            result = hits.result(index, score)
            results.append({**result, 'path': qualify(c.key, result['path']), 'collection': c.key})
        total = len(ranked)
        return {'results': results, 'match': 'all' if complete or not ranked else 'any', 'total': total,
                'page': max(1, page), 'totalPages': -(-total // page_size) if total else 0, 'pageSize': page_size}

    # --- Another text of the same publication ---

    def counterpart(self, node: str, collection: str, scope: str = '') -> dict:
        """Where another collection's text of the publication takes up the place this node reads: the deepest
        heading on the way here whose title the other text also gives, chapter numbering aside; else the page that
        defines most of the entries this page defines (same type and English name); else the text's start."""
        layout = self.layout()
        if node not in layout.by_id:
            raise ReaderError('invalid_location', 400)
        self.member(collection)
        current = layout.by_id[node]
        while current and 'texts' not in current:
            current = layout.by_id.get(current['parent_id'])
        target = next((t for t in (current or {}).get('texts', []) if t['collection'] == collection), None)
        if target is None:
            raise ReaderError('no_counterpart', 404)
        found = _same_heading(layout, node, current['id'], target['id']) \
            or self._shared_page(split(node)[0], layout.by_id[node], current, collection, target['id'], layout) \
            or target['id']
        # A reading scope inside the text maps by its heading too, when the place found lies within it.
        root = target['id']
        if scope and scope != current['id'] and scope in layout.by_id:
            mapped = _same_heading(layout, scope, current['id'], target['id'])
            if mapped and any(n['id'] == found for n in _subtree(layout, mapped)):
                root = mapped
        return {'root': root, 'node': found}

    def _shared_page(self, key, node, text_root, collection, target_root, layout):
        page = node.get('path') or ''
        if not page:
            return None
        try:
            definitions = self.page(path=page)['definitions']
        except ReaderError:
            return None
        source = self.by_key[key]
        rows = source.provider.rows([d['id'] for d in definitions[:COUNTERPART_ENTRIES]])
        wanted = {(r['type'], name_fold(r.get('name_en'))) for r in rows.values() if name_fold(r.get('name_en'))}
        if not wanted:
            return None
        publication = layout.root_text.get(split(text_root['id']))
        work = next((t.work for t in layout.texts.get(publication, []) if t.collection == collection), None)
        names = self._names(collection).get(work, {})
        votes = Counter()
        target = self.by_key[collection]
        for name in wanted:
            for eid in names.get(name, [])[:3]:
                try:
                    votes[target.reader.page_data(entry=eid)['path']] += 1
                except ReaderError:
                    continue
        # One page must hold most of them: entries spread over many pages say only that both texts share them.
        (path, most), = votes.most_common(1) or [(None, 0)]
        if not path or most * 2 < sum(votes.values()):
            return None
        return next((n['id'] for n in _subtree(layout, target_root) if n.get('path') == qualify(collection, path)), None)

    def _names(self, key):
        """A collection's stackable entries by work, then by (type, folded English name)."""
        collection = self.by_key[key]
        cache_key = (key, collection.provider.signature())
        with _lock:
            if cache_key in _names:
                return _names[cache_key]
        out: dict = defaultdict(lambda: defaultdict(list))
        for r in collection.provider.ranked({}):
            if r.get('work') and (folded := name_fold(r.get('name_en'))):
                out[r['work']][(r['type'], folded)].append(r['id'])
        with _lock:
            _names[cache_key] = out
        return out


CHAPTER = re.compile(r'^(?:第\s*[0-9一二三四五六七八九十百零〇两]+\s*[章部篇节]|附录\s*[A-Za-z0-9]+|序章|前言|chapter\s*\d+|appendix\s*[a-z])[：:.\s]*', re.I)


def heading_key(label: str) -> str:
    """A heading as two translations may both number it: chapter and appendix numbering, spacing and punctuation dropped."""
    text = unicodedata.normalize('NFKC', label or '').strip()
    return re.sub(r'[\W_]+', '', CHAPTER.sub('', text)).casefold()


def _same_heading(layout, node, root, target_root):
    """The other text's node titled as the deepest heading from this node up to its text's root; of several so
    titled, the one nearest the same depth below its root, then the earliest."""
    depth = {target_root: 0}
    inside: dict[str, list[str]] = defaultdict(list)
    for n in _subtree(layout, target_root):
        if n['id'] != target_root:
            depth[n['id']] = depth.get(n['parent_id'], 0) + 1
            if key := heading_key(n['label']):
                inside[key].append(n['id'])
    lineage, current = [], layout.by_id.get(node)
    while current and current['id'] != root:
        lineage.append(current)
        current = layout.by_id.get(current['parent_id'])
    for level, heading in enumerate(lineage):
        hits = inside.get(heading_key(heading['label']), [])
        if hits:
            own = len(lineage) - level
            return min(hits, key=lambda h: abs(depth[h] - own))
    return None


def _subtree(layout, root):
    """The nodes under a root, in contents order."""
    inside, ids = [], {root}
    for n in layout.nodes:
        if n['id'] in ids or n['parent_id'] in ids:
            ids.add(n['id'])
            inside.append(n)
    return inside


def _pruned(nodes, later):
    """Drop what moving shared works left empty in later collections: a group with nothing under it, and a
    divider with no row before the next divider."""
    children = defaultdict(list)
    for n in nodes:
        children[n['parent_id']].append(n)
    keep: set[str] = set()

    def walk(parent):
        siblings = children.get(parent, [])
        kept = []
        for n in siblings:
            walk(n['id'])
            emptied = n['id'].partition(SEPARATOR)[0] in later and n['status'] != 'present' \
                and divider(n) is None and not any(c['id'] in keep for c in children.get(n['id'], []))
            had_children = bool(children.get(n['id']))
            if not (emptied and had_children):
                kept.append(n)
        # A divider heads the rows after it; one left heading none goes too.
        for i, n in enumerate(kept):
            if divider(n) is not None and n['id'].partition(SEPARATOR)[0] in later:
                following = kept[i + 1] if i + 1 < len(kept) else None
                if following is None or divider(following) is not None:
                    continue
            keep.add(n['id'])

    walk('')
    return [n for n in nodes if n['id'] in keep and (not n['parent_id'] or n['parent_id'] in keep)]
