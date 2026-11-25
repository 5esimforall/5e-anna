"""Read-only source context for reference resolution."""
from collections import defaultdict
import json
from anna.catalog.concepts import Resolver


class Context:
    def __init__(self, db):
        self.entries = {e['id']: e for raw, in db.execute('SELECT data FROM entries') for e in [json.loads(raw)]}
        self.pages = dict(db.execute('SELECT path,text FROM pages'))
        page_cols = {r[1] for r in db.execute('PRAGMA table_info(pages)')}
        self.page_sources = (
            {p: json.loads(s or '[]') for p, s in db.execute('SELECT path,source_ids FROM pages')}
            if 'source_ids' in page_cols else {p: [] for p in self.pages}
        )
        present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.sources = [json.loads(raw) for raw, in db.execute('SELECT data FROM sources')]
        self.source_by_id = {s['id']: s for s in self.sources}
        self.resolver = Resolver(list(self.entries.values()), self.sources)
        self.by_page = self.resolver.pages
        self.names = self.resolver.names
        self.locals = defaultdict(list)
        if 'local_definitions' in present:
            for path, raw in db.execute("SELECT json_extract(data,'$.path'),data FROM local_definitions"):
                self.locals[path].append(json.loads(raw))
        cov_row = db.execute("SELECT value FROM metadata WHERE key='concept_coverage'").fetchone() if 'metadata' in present else None
        coverage = json.loads(cov_row[0]) if cov_row else {'style_profiles': []}
        self.profiles = {
            p['path']: {
                'id': p['id'],
                'supported': p['supported'],
                'rules': {k: {f: rule[f] for f in ('selectors', 'annotation')} for k, rule in p.get('rules', {}).items()},
            }
            for p in coverage.get('style_profiles', [])
        }

    def locatable(self, target):
        e = self.entries.get(target)
        return bool(e and self.pages[e['page_path']][e['start']:max(e['end'], e['title_end'])].strip())

    def enclosing_entry(self, path, start, end):
        typed = [e for e in self.by_page[path] if e['type'] != 'section' and e['start'] <= start and end <= e['end']]
        pool = typed or [e for e in self.by_page[path] if e['start'] <= start and end <= e['end']]
        return min(pool, key=lambda e: (e['end'] - e['start'], e['id'])) if pool else None

    def binding(self, ref):
        text = self.pages[ref['path']]
        owners = sorted(
            (e for e in self.by_page[ref['path']] if e['start'] <= ref['start'] < e['end']),
            key=lambda e: (e['start'], -e['end'], e['id']),
        )
        return {k: ref[k] for k in ('path', 'start', 'end', 'signal', 'kind', 'role')} | {
            'reference_id': ref['id'],
            'text': text[ref['start']:ref['end']],
            'before': text[max(0, ref['start'] - 80):ref['start']],
            'after': text[ref['end']:ref['end'] + 80],
            'title_chain': [{'id': e['id'], 'name': e['name'], 'type': e['type']} for e in owners],
        }
