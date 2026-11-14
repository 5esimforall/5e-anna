"""Evidence-only correspondence between two datasets; never changes rule identities.

Location correspondence and dependency equivalence are separate results. Exact text
can locate an observation but cannot establish that an old approval remains valid.
"""
from collections import Counter, defaultdict
import json

from review_store import digest


def records(db, table):
    return {v['id']: v for raw, in db.execute('SELECT data FROM ' + table) for v in [json.loads(raw)]}


class UnmappedEvidence(ValueError):
    pass


class Correspondence:
    def __init__(self, before, after):
        self.old_pages = self.pages(before)
        self.new_pages = self.pages(after)
        self.old_entries = records(before, 'entries')
        self.new_entries = records(after, 'entries')
        self.old_by_page = self.by_page(self.old_entries)
        self.new_by_page = self.by_page(self.new_entries)
        self.page_results = self.align_pages()
        self.page_map = {r['before']: r['after'] for r in self.page_results if r['after'] is not None}
        self.entry_results = self.align_entries()
        self.entry_map = {r['before']: r['after'] for r in self.entry_results if r['after'] is not None}
        self.ids = dict(self.entry_map)
        for old, new in self.entry_map.items():
            self.ids[self.old_entries[old]['entity_id']] = self.new_entries[new]['entity_id']
        self.span_cache = {}

    @staticmethod
    def pages(db):
        return {p: {'text': t, 'sha256': h} for p, t, h in db.execute('SELECT path,text,sha256 FROM pages')}

    @staticmethod
    def by_page(entries):
        result = defaultdict(list)
        for e in entries.values():
            result[e['page_path']].append(e)
        for rows in result.values():
            rows.sort(key=lambda e: (e['end'] - e['start'], e['start'], e['id']))
        return result

    def align_pages(self):
        by_hash = defaultdict(list)
        by_text = defaultdict(list)
        for path, page in self.new_pages.items():
            by_hash[page['sha256']].append(path)
            if page['text'].strip():
                by_text[digest(page['text'])].append(path)
        result = []
        for path, old in sorted(self.old_pages.items()):
            if path in self.new_pages:
                new = self.new_pages[path]
                status = 'unchanged' if old == new else 'format_changed' if old['text'] == new['text'] else 'content_changed'
                result.append({'before': path, 'after': path, 'status': status, 'evidence': 'same_path', 'candidates': [path]})
            else:
                candidates = by_hash.get(old['sha256'], [])
                rule = 'exact_bytes'
                if not candidates and old['text'].strip():
                    candidates = by_text.get(digest(old['text']), [])
                    rule = 'exact_page_text'
                result.append({'before': path, 'after': None, 'status': 'candidate', 'evidence': rule, 'candidates': sorted(candidates)})
        claims = Counter(c for r in result for c in r['candidates'])
        for row in result:
            if row['status'] != 'candidate':
                continue
            candidates = row['candidates']
            if len(candidates) == 1 and claims[candidates[0]] == 1:
                row.update(after=candidates[0], status='relocated')
            else:
                row['status'] = 'ambiguous' if candidates else 'missing'
        return result

    def body(self, entry, old=True):
        pages = self.old_pages if old else self.new_pages
        return pages[entry['page_path']]['text'][entry['start']:entry['end']]

    def align_entries(self):
        result = []
        # Indices are scoped to a corresponding page; no global name-based matching.
        anchors = defaultdict(list)
        bodies = defaultdict(list)
        positions = defaultdict(list)
        old_anchors = Counter((e['page_path'], a) for e in self.old_entries.values() for a in set(e.get('anchors', [])))
        for e in self.new_entries.values():
            for a in set(e.get('anchors', [])):
                anchors[(e['page_path'], a)].append(e['id'])
            if self.body(e, False).strip():
                bodies[(e['page_path'], digest(self.body(e, False)))].append(e['id'])
            positions[(e['page_path'], e['start'], e['end'])].append(e['id'])
        for eid, old in sorted(self.old_entries.items()):
            path = self.page_map.get(old['page_path'])
            candidates, rule = [], 'page_unmapped'
            if path:
                if self.old_pages[old['page_path']]['text'] == self.new_pages[path]['text']:
                    candidates = positions.get((path, old['start'], old['end']), [])
                    rule = 'same_range_in_equal_page'
                if not candidates:
                    candidates = sorted({i for a in old.get('anchors', []) if old_anchors[(old['page_path'], a)] == 1
                                         for i in anchors.get((path, a), []) if len(anchors[(path, a)]) == 1})
                    rule = 'unique_source_anchor'
                if not candidates and self.body(old).strip():
                    candidates = bodies.get((path, digest(self.body(old))), [])
                    rule = 'exact_entry_text'
            result.append({'before': eid, 'after': None, 'status': 'candidate', 'evidence': rule,
                           'before_location': self.location(old), 'candidates': sorted(candidates)})
        claims = Counter(c for r in result for c in r['candidates'])
        for row in result:
            found = row['candidates']
            if len(found) == 1 and claims[found[0]] == 1:
                old, new = self.old_entries[row['before']], self.new_entries[found[0]]
                same_text = self.body(old) == self.body(new, False)
                row.update(after=new['id'], after_location=self.location(new),
                           status=('unchanged' if old['id'] == new['id'] and self.location(old) == self.location(new)
                                   else 'relocated') if same_text else 'content_changed')
            else:
                row['status'] = 'ambiguous' if found else 'missing'
                # A familiar locator is useful to inspect, but is not identity evidence.
                if path := self.page_map.get(self.old_entries[row['before']]['page_path']):
                    old = self.old_entries[row['before']]
                    row['locator_candidates'] = [e['id'] for e in self.new_by_page[path]
                                                 if e.get('entity_id') == old.get('entity_id')]
        return result

    @staticmethod
    def location(entry):
        return {'path': entry['page_path'], 'span': [entry['start'], entry['end']]}

    def span(self, path, start, end):
        key = (path, start, end)
        if key in self.span_cache:
            return self.span_cache[key]
        result = {'before': {'path': path, 'span': [start, end]}, 'after': None, 'status': 'missing', 'candidates': []}
        target = self.page_map.get(path)
        old = self.old_pages.get(path, {}).get('text', '')
        if not target or not 0 <= start <= end <= len(old):
            self.span_cache[key] = result
            return result
        new = self.new_pages[target]['text']
        if old == new:
            result.update(after={'path': target, 'span': [start, end]}, status='unchanged' if path == target else 'relocated', evidence='equal_page_text')
        else:
            owners = [e for e in self.old_by_page[path] if e['start'] <= start <= end <= e['end']]
            # An unchanged enclosing source range supplies a deterministic offset even
            # when the quote repeats. A changed owner cannot supply that guarantee.
            for owner in owners:
                corresponding = self.new_entries.get(self.entry_map.get(owner['id']))
                if corresponding and self.body(owner) == self.body(corresponding, False):
                    offset = corresponding['start'] - owner['start']
                    result.update(after={'path': target, 'span': [start + offset, end + offset]},
                                  status='relocated', evidence='equal_owner_text', owner_id=owner['id'])
                    break
            if result['after'] is None and end > start:
                quote = old[start:end]
                # Changed scopes yield candidates only; never silently transport them.
                low, high = 0, len(new)
                if owners:
                    owner = self.new_entries.get(self.entry_map.get(owners[0]['id']))
                    if owner:
                        low, high = owner['start'], owner['end']
                pos = new.find(quote, low, high)
                while pos >= 0:
                    result['candidates'].append({'path': target, 'span': [pos, pos + len(quote)]})
                    pos = new.find(quote, pos + 1, high)
                result['status'] = 'ambiguous' if len(result['candidates']) > 1 else 'context_changed' if result['candidates'] else 'missing'
        self.span_cache[key] = result
        return result

    def transport(self, value, path=None):
        """Transport only proven coordinates/identifiers, preserving all dependency data.

        Unknown coordinate structures remain unchanged and therefore fail equivalence
        rather than being stripped. Content hashes, semantic values and review status
        are never discarded to make a match succeed.
        """
        if isinstance(value, list):
            return [self.transport(v, path) for v in value]
        if not isinstance(value, dict):
            return self.ids.get(value, value) if isinstance(value, str) else value
        old_path = value.get('page_path', value.get('path', path))
        result = {self.ids.get(k, k): self.transport(v, old_path) for k, v in value.items()}
        for field in ('path', 'page_path', 'target_path'):
            if isinstance(value.get(field), str) and value[field] in self.page_map:
                result[field] = self.page_map[value[field]]
        if isinstance(old_path, str) and old_path in self.old_pages:
            if all(type(value.get(k)) is int for k in ('start', 'end')):
                aligned = self.span(old_path, value['start'], value['end'])
                if not aligned['after']:
                    raise UnmappedEvidence('range_' + aligned['status'])
                result['start'], result['end'] = aligned['after']['span']
                if type(value.get('title_end')) is int:
                    title = self.span(old_path, value['start'], value['title_end'])
                    if not title['after']:
                        raise UnmappedEvidence('title_' + title['status'])
                    result['title_end'] = title['after']['span'][1]
            for field in ('span', 'scope', 'cue_span', 'cell_span', 'table_span'):
                span = value.get(field)
                if isinstance(span, list) and len(span) == 2 and all(type(n) is int for n in span):
                    aligned = self.span(old_path, *span)
                    if not aligned['after']:
                        raise UnmappedEvidence(field + '_' + aligned['status'])
                    result[field] = aligned['after']['span']
            if 'cell_spans' in value:
                result['cell_spans'] = []
                for span in value['cell_spans']:
                    aligned = self.span(old_path, *span)
                    if not aligned['after']:
                        raise UnmappedEvidence('cell_spans_' + aligned['status'])
                    result['cell_spans'].append(aligned['after']['span'])
        return result

    def context_dependencies(self, entry_id, old=True):
        """Conservative inherited context, separate from extractor-declared dependencies."""
        entries = self.old_entries if old else self.new_entries
        page_entries = self.old_by_page if old else self.new_by_page
        entry = entries[entry_id]
        # Retain whole ancestor text until finer semantic dependencies are represented.
        return {e['id']: {'body_sha256': digest(self.body(e, old)), 'parent_id': e.get('parent_id'),
                         'type': e['type'], 'source_ids': e.get('source_ids', []), 'ruleset': e.get('ruleset')}
                for e in page_entries[entry['page_path']] if e['start'] <= entry['start'] and entry['end'] <= e['end']}
