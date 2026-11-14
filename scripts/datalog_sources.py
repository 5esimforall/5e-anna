"""Bind authored rule evidence to anna's canonical source coordinates, read-only."""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import sqlite3
from urllib.parse import urlencode

from build import read_page
from html_extract import Document
from review_store import digest
from rule_values import evidence


class DatalogError(ValueError):
    def __init__(self, code, detail='', **context):
        super().__init__(detail or code)
        self.code, self.detail, self.context = code, detail, context

    def as_dict(self):
        return {'code': self.code, 'detail': self.detail, **self.context}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DatalogError('duplicate_key', key)
        result[key] = value
    return result


def read_json(path):
    try:
        return json.loads(Path(path).read_text('utf-8'), object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(DatalogError('invalid_number', value)))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatalogError('invalid_json', str(path), reason=str(exc)) from exc


def within(root, relative):
    """Manifest paths are relative POSIX paths, never filesystem escape hatches."""
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise DatalogError('invalid_path', str(relative))
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {'', '.', '..'} for part in relative.split('/')):
        raise DatalogError('invalid_path', relative)
    target = (Path(root) / relative).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        raise DatalogError('invalid_path', relative)
    return target


def occurrences(text, needle):
    start = 0
    while needle and (start := text.find(needle, start)) >= 0:
        yield start
        start += 1


def locate(text, selector):
    """Exact quote within an exact context; ambiguous matches never use load order."""
    quote, context = selector.get('quote'), selector.get('context')
    if not isinstance(quote, str) or not quote.strip() or not isinstance(context, str) or quote not in context:
        raise DatalogError('invalid_selector', 'quote must occur in its recorded context')
    contexts = list(occurrences(text, context))
    matches = sorted({(a + b, a + b + len(quote)) for a in contexts for b in occurrences(context, quote)})
    if len(matches) != 1:
        raise DatalogError('source_ambiguous' if matches else 'source_changed',
                           selector.get('path', ''), candidates=[list(x) for x in matches])
    start, end = matches[0]
    return start, end, 'same_range' if selector.get('span') == [start, end] else 'relocated'


def source_links(bound, base_url):
    params = {'path': bound['path'], 'start': bound['span'][0], 'end': bound['span'][1],
              'sha256': bound['page_sha256']}
    return {'evidence': base_url.rstrip('/') + '/source?' + urlencode(params) + '#evidence',
            'reader': base_url.rstrip('/') + '/read?' + urlencode({'page': bound['path']})}


def bind_sources(selectors, db_path, source_root, base_url):
    if not isinstance(selectors, dict) or not selectors:
        raise DatalogError('invalid_sources', 'a nonempty evidence registry is required')
    pages, result = {}, {}
    uri = Path(db_path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        for sid, selector in sorted(selectors.items()):
            if not isinstance(selector, dict) or selector.get('edition') not in {'5e', '5r'}:
                raise DatalogError('invalid_source', sid)
            path = selector.get('path')
            file = within(source_root, path)
            if path not in pages:
                row = db.execute('SELECT text,sha256 FROM pages WHERE path=?', (path,)).fetchone()
                if row is None:
                    raise DatalogError('source_missing', path)
                try:
                    raw, _, file_hash = read_page(file)
                except OSError as exc:
                    raise DatalogError('source_missing', path) from exc
                if file_hash != row[1]:
                    raise DatalogError('catalog_source_mismatch', path)
                doc = Document(path, raw)
                try:
                    if doc.text != row[0]:
                        raise DatalogError('source_adapter_mismatch', path)
                finally:
                    doc.close()
                entries = [json.loads(r[0]) for r in db.execute('SELECT data FROM entries WHERE page_path=?', (path,))]
                pages[path] = {'text': row[0], 'sha256': file_hash, 'entries': entries}
            page = pages[path]
            try:
                start, end, status = locate(page['text'], selector)
            except DatalogError as exc:
                exc.context['source_id'] = sid
                raise
            containers = [e for e in page['entries'] if e['start'] <= start and end <= e['end']]
            # These are location correspondences, not claims of rule identity.
            if any(e.get('ruleset') not in {selector['edition'], '待核'} for e in containers):
                raise DatalogError('source_edition_mismatch', sid)
            entry_ids = sorted(e['id'] for e in containers if e.get('ruleset') == selector['edition'])
            commits = sorted({e['snapshot'] for e in containers if e.get('snapshot')})
            bound = {'id': sid, 'edition': selector['edition'], **evidence(path, page['text'], start, end),
                     'quote': selector['quote'], 'context': selector['context'], 'context_sha256': digest(selector['context']),
                     'original_span': selector.get('span'), 'location_status': status,
                     'page_sha256': page['sha256'], 'page_text_sha256': digest(page['text']),
                     'entry_ids': entry_ids, 'source_commits': commits}
            bound['links'] = source_links(bound, base_url)
            result[sid] = bound
        # The build is a snapshot, not a mixture of files edited during extraction.
        verify_pages(pages, source_root)
    return result, {p: {k: v[k] for k in ('sha256',)} for p, v in sorted(pages.items())}


def verify_pages(pages, source_root):
    for path, page in pages.items():
        file = within(source_root, path)
        if not file.is_file() or sha256(file.read_bytes()).hexdigest() != page['sha256']:
            raise DatalogError('source_changed_during_build', path)


def validate_origins(origins, sources, editions, owner):
    """Every enabled edition has its own evidence, including shared clauses."""
    if not isinstance(origins, dict) or set(origins) != set(editions):
        raise DatalogError('invalid_origins', owner)
    for edition, refs in origins.items():
        if not isinstance(refs, list) or not refs or len(refs) != len(set(refs)):
            raise DatalogError('invalid_origins', owner)
        for ref in refs:
            if ref not in sources or sources[ref]['edition'] != edition:
                raise DatalogError('invalid_source_reference', owner, source_id=ref, edition=edition)
