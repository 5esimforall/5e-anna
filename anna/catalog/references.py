"""One effective reference model for queries and the source-preserving link layer."""
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re

from anna.catalog.concepts import aliases
from anna.review.store import (
    DEFAULT_ANNOTATIONS,
    annotation_lifecycle,
    append_annotation_events,
    input_snapshot,
    load_annotations,
    locked,
    partition_annotations,
    require_annotations_appendable,
)
from anna.serialization import canonical, digest
from anna.source.html import Document, compact, key, resolve_link


def entry_selector(entry):
    """Portable entry identity without snapshot byte offsets or ver_ IDs."""
    return {
        'type': entry['type'],
        'ruleset': entry['ruleset'],
        'name_zh': entry['name_zh'],
        'name_en': entry.get('name_en', ''),
        'page_path': entry['page_path'],
    }


def _selector_key(selector):
    return (
        selector.get('page_path', ''),
        selector.get('type', ''),
        selector.get('ruleset', ''),
        selector.get('name_zh', ''),
        selector.get('name_en', ''),
    )


def _suggest_within(text, a, b):
    """Build the tightest unique surrounding sentence/line snippet for portable span anchors."""
    la = text.rfind('\n', 0, a) + 1
    lb = text.find('\n', b)
    if lb == -1:
        lb = len(text)
    # Try sentence delimiters inside the line first for prose paragraphs.
    sa = la
    for m in re.finditer(r'[。！？；]', text[la:a]):
        sa = la + m.end()
    while sa < a and text[sa].isspace():
        sa += 1
    sb_match = re.search(r'[。！？；]', text[b:lb])
    sb = b + sb_match.end() if sb_match else lb
    snippet = text[sa:sb].strip()
    if snippet and text.count(snippet) == 1:
        return snippet
    line = text[la:lb].strip()
    if line and text.count(line) == 1:
        return line
    pad = 40
    while pad <= 240:
        wa = max(0, a - pad)
        wb = min(len(text), b + pad)
        chunk = text[wa:wb].strip()
        if chunk and text.count(chunk) == 1:
            return chunk
        pad += 40
    return text[max(0, a - 160):min(len(text), b + 160)]


class ReviewContext:
    def __init__(self, db):
        self.entries = {e['id']: e for (raw,) in db.execute('SELECT data FROM entries') for e in [json.loads(raw)]}
        self.pages = {p: {'text': t, 'sha256': h, 'source_ids': json.loads(s)}
                      for p, t, h, s in db.execute('SELECT path,text,sha256,source_ids FROM pages')}
        self.sources = {
            sid: {
                k: v for k, v in json.loads(raw).items()
                if k in {'id', 'name', 'ruleset_hint', 'reference_role', 'archived', 'origin', 'release_status'}
            }
            for sid, raw in db.execute('SELECT id,data FROM sources')
        }
        self.by_page = defaultdict(list)
        self.names = defaultdict(set)
        for e in self.entries.values():
            self.by_page[e['page_path']].append(e)
            for name in aliases(e):
                self.names[key(name)].add(e['id'])
        self.locals = defaultdict(list)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='local_definitions'").fetchone():
            for path, raw in db.execute("SELECT json_extract(data,'$.path'),data FROM local_definitions"):
                self.locals[path].append(json.loads(raw))
        coverage_row = db.execute("SELECT value FROM metadata WHERE key='concept_coverage'").fetchone()
        coverage = json.loads(coverage_row[0]) if coverage_row else {'style_profiles': []}
        self.profiles = {
            p['path']: {
                'id': p['id'],
                'supported': p['supported'],
                'rules': {
                    k: {field: rule[field] for field in ('selectors', 'annotation')}
                    for k, rule in p.get('rules', {}).items()
                },
            }
            for p in coverage['style_profiles']
        }

    def locatable(self, target):
        e = self.entries.get(target)
        return bool(e and self.pages[e['page_path']]['text'][e['start']:max(e['end'], e['title_end'])].strip())

    def resolve_entry_selector(self, selector):
        """Resolve a portable target entry selector; unique cross-page moves transfer."""
        matches = [
            e for e in self.entries.values()
            if e['type'] == selector['type']
            and e['ruleset'] == selector['ruleset']
            and e['name_zh'] == selector['name_zh']
            and e.get('name_en', '') == selector.get('name_en', '')
        ]
        if not matches:
            return 'missing', None
        exact = [e for e in matches if e['page_path'] == selector['page_path']]
        if len(exact) == 1:
            return 'exact', exact[0]
        if len(exact) > 1:
            return 'ambiguous', None
        if len(matches) == 1:
            return 'transferred', matches[0]
        return 'ambiguous', None

    def enclosing_entry(self, path, start, end):
        typed = [
            e for e in self.by_page[path]
            if e['type'] != 'section' and e['start'] <= start and end <= e['end']
        ]
        pool = typed or [
            e for e in self.by_page[path]
            if e['start'] <= start and end <= e['end']
        ]
        return min(pool, key=lambda e: (e['end'] - e['start'], e['id'])) if pool else None

    def binding(self, ref):
        text = self.pages[ref['path']]['text']
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

    def portable_evidence(self, ref):
        """Retain semantic evidence without snapshot byte offsets or ephemeral IDs."""
        ev = deepcopy(ref.get('evidence', {}))
        ev.pop('format_mark_id', None)
        ev.pop('format_mark_span', None)
        ev.pop('stylesheet_sha256', None)
        ev.pop('resolution', None)
        stylesheets = ev.pop('stylesheets', [])
        if stylesheets or ref['signal'] == 'format':
            ev['format_profiles'] = {
                p['path']: self.profiles.get(p['path'])
                for p in stylesheets
                if p.get('supported')
            }
        if 'syntax' in ev and isinstance(ev['syntax'], dict):
            ev['syntax'] = {k: v for k, v in ev['syntax'].items() if k != 'cue_span'}
        if 'scope' in ev and isinstance(ev['scope'], dict):
            ev['scope'] = {
                'ruleset': ev['scope'].get('ruleset'),
                'source_count': len(ev['scope'].get('source_ids', [])),
            }
        if 'table' in ev and isinstance(ev['table'], dict):
            ev['table'] = {
                k: ev['table'][k]
                for k in ('headers', 'cell_text', 'column')
                if k in ev['table']
            }
        return ev

    def reference_basis(self, ref, anchor_id, statements):
        """Compute separate content and governing-context hashes without volatile offsets/IDs."""
        from anna.review.search import SEARCH_VERSION, candidate_matches

        page = self.pages[ref['path']]
        content = [
            {
                'id': anchor_id,
                'kind': 'span',
                'quote': ref['text'],
                'signal': ref['signal'],
                'role': ref['role'],
                'ref_kind': ref['kind'],
            },
            {
                'rule_kind': 'reference',
                'statements': deepcopy(statements),
            },
        ]
        owner = self.enclosing_entry(ref['path'], ref['start'], ref['end'])
        enclosing = {
            'type': owner['type'],
            'ruleset': owner['ruleset'],
            'name_zh': owner['name_zh'],
            'name_en': owner.get('name_en', ''),
            'classification_status': owner.get('classification_status', ''),
        } if owner else None

        ctx = {
            'enclosing': enclosing,
            'page_sources': [
                {
                    'ruleset_hint': self.sources[sid].get('ruleset_hint'),
                    'reference_role': self.sources[sid].get('reference_role'),
                    'archived': self.sources[sid].get('archived'),
                }
                for sid in sorted(page['source_ids'])
                if sid in self.sources
            ],
            'automatic': {
                'status': ref['automatic']['status'],
                'reason': ref['automatic']['reason'],
                'candidates': sorted(
                    [entry_selector(self.entries[cid]) for cid in ref['automatic']['candidates'] if cid in self.entries],
                    key=_selector_key,
                ),
            },
            'evidence': self.portable_evidence(ref),
            'name_competitors': sorted(
                [entry_selector(self.entries[eid]) for eid in self.names.get(key(ref['text']), ()) if eid in self.entries],
                key=_selector_key,
            ),
            'local_definitions': sorted(
                [
                    {
                        'name': d['name'],
                        'kind': d['kind'],
                        'definition': entry_selector(self.entries[d['definition_id']]),
                    }
                    for d in self.locals[ref['path']]
                    if d['scope'][0] <= ref['start'] < d['scope'][1] and d['definition_id'] in self.entries
                ],
                key=lambda d: (d['name'], d['kind'], _selector_key(d['definition'])),
            ),
        }
        ruling = statements['ruling']
        if ruling == 'confirm':
            tmatch, tentry = self.resolve_entry_selector(statements['target'])
            ctx['target'] = {
                'resolved': tentry is not None,
                'ambiguous': tmatch == 'ambiguous',
                'selector': {k: v for k, v in statements['target'].items() if k != 'page_path'},
                'locatable': bool(tentry and self.locatable(tentry['id'])),
            }
        elif ruling == 'missing_definition':
            query = statements.get('search_query', '')
            matches = candidate_matches(self, ref, query)
            ctx['candidate_search'] = {
                'query': query,
                'strategy_version': SEARCH_VERSION,
                'candidates': sorted(
                    [entry_selector(m['entry']) for m in matches],
                    key=_selector_key,
                ),
            }
        return {
            'schema': 1,
            'content': content,
            'content_sha256': digest(content),
            'context': ctx,
            'context_sha256': digest(ctx),
        }


def basis_changes(before, after):
    """Machine-readable changes between dependency evidence."""
    changes = []

    def walk(old, new, path):
        if old == new:
            return
        if isinstance(old, dict) and isinstance(new, dict):
            for k in sorted(old.keys() | new.keys()):
                if k not in old or k not in new:
                    changes.append({
                        'path': path + [k],
                        'kind': 'added' if k not in old else 'removed',
                        'before': old.get(k),
                        'after': new.get(k),
                    })
                else:
                    walk(old[k], new[k], path + [k])
        else:
            changes.append({'path': path, 'kind': 'changed', 'before': old, 'after': new})

    walk(before, after, [])
    return changes


def reference(observation):
    ref = {k: observation[k] for k in ('id', 'path', 'start', 'end', 'text', 'signal', 'kind', 'role', 'evidence')}
    ref['automatic'] = {k: observation[k] for k in ('status', 'reason', 'target_id', 'candidates')}
    ref['observation_ids'] = [ref['id']] if ref['signal'] != 'explicit' else []
    return ref


def discover_originals(source, context, dom_by_path=None):
    """Read original links once; the book exporter no longer has a separate resolver."""
    from anna.source.pages import read_page
    links = defaultdict(list)
    anchors = {}
    for path, page in sorted(context.pages.items()):
        if not page['text']:
            continue
        if dom_by_path and path in dom_by_path:
            dom = dom_by_path[path]
            text = page['text']
        else:
            doc = Document(path, read_page(Path(source) / path)[0])
            try:
                if doc.text != page['text']:
                    raise ValueError('原文文本与数据库不一致：' + path)
                dom = doc.dom_snapshot()
                text = doc.text
            finally:
                doc.close()
        anchors[path] = {name: [s[0] for s in spans] for name, spans in dom['anchors'].items()}
        occurrences = Counter()
        for link in dom['body_links']:
            dest = resolve_link(path, link['href'])
            if not dest or not dest[0].startswith('topics/'):
                continue
            a, b = link['span']
            occurrence = occurrences[(a, b)]
            occurrences[(a, b)] += 1
            links[path].append({
                'id': 'reference_' + digest([path, a, b, 'explicit', occurrence])[:24],
                'path': path,
                'start': a,
                'end': b,
                'text': compact(text[a:b]),
                'signal': 'explicit',
                'kind': 'entry',
                'role': 'explicit_reference',
                'evidence': {'href': link['href'], 'target_path': dest[0], 'fragment': dest[1]},
            })
    for path, rows in links.items():
        for row in rows:
            destination = row['evidence']['target_path']
            fragment = row['evidence']['fragment']
            entries = context.by_page[destination]
            direct = [e for e in entries if fragment and fragment in e.get('anchors', [])]
            if len(direct) == 1:
                candidates = direct
            elif fragment:
                positions = anchors.get(destination, {}).get(fragment, [])
                candidates = [e for e in entries if len(positions) == 1 and e['start'] <= positions[0] < e['end']]
                if candidates:
                    size = min(e['end'] - e['start'] for e in candidates)
                    candidates = [e for e in candidates if e['end'] - e['start'] == size]
            else:
                exact = [e for e in entries if key(row['text']) in {key(n) for n in (e['name'], e['name_zh'], e['name_en']) if n}]
                candidates = exact or [e for e in entries if e.get('is_primary') and e['type'] != 'section']
            ids = sorted({e['id'] for e in candidates})
            row.update(
                status='resolved' if len(ids) == 1 else 'ambiguous' if ids else 'unresolved',
                reason='explicit_locator',
                target_id=ids[0] if len(ids) == 1 else None,
                candidates=ids,
            )
    return links


def overlap(a, b):
    return a['start'] < b['end'] and b['start'] < a['end']


def bind_reference_anchor(context, page_path, anchor, page_refs):
    """Bind a portable reference anchor against the current page text and discovered references."""
    if page_path not in context.pages:
        return 'missing', None, [], 'Subject page missing'
    text = context.pages[page_path]['text']
    quote = anchor['quote']
    within = anchor.get('within')
    signal = anchor.get('signal')
    all_q_spans = [(m.start(), m.end()) for m in re.finditer(re.escape(quote), text)]

    if within:
        wmatches = list(re.finditer(re.escape(within), text))
        if not wmatches:
            affected = [r for r in page_refs if any(overlap(r, {'start': s, 'end': e}) for s, e in all_q_spans)]
            return ('stale' if affected else 'missing'), None, affected, 'Enclosing context snippet not found'
        if len(wmatches) > 1:
            affected = [
                r for r in page_refs
                if any(overlap(r, {'start': m.start(), 'end': m.end()}) for m in wmatches)
            ]
            return 'ambiguous', None, affected, 'Enclosing context snippet matches multiple locations'
        wa, wb = wmatches[0].start(), wmatches[0].end()
    else:
        wa, wb = 0, len(text)

    qmatches = list(re.finditer(re.escape(quote), text[wa:wb]))
    if not qmatches:
        return 'missing', None, [], 'Reference quote not found'
    if len(qmatches) > 1:
        affected = [
            r for r in page_refs
            if any(overlap(r, {'start': wa + m.start(), 'end': wa + m.end()}) for m in qmatches)
        ]
        return 'ambiguous', None, affected, 'Reference quote matches multiple occurrences'

    sa, sb = wa + qmatches[0].start(), wa + qmatches[0].end()
    exact_refs = [
        r for r in page_refs
        if r['start'] == sa and r['end'] == sb and (not signal or r['signal'] == signal)
    ]
    if len(exact_refs) == 1:
        return 'bound', exact_refs[0], [exact_refs[0]], 'Bound uniquely'
    if len(exact_refs) > 1:
        return 'ambiguous', None, exact_refs, 'Multiple references share this span'
    affected = [r for r in page_refs if overlap(r, {'start': sa, 'end': sb})]
    return ('stale' if affected else 'missing'), None, affected, 'No matching reference at bound span'


def _synthetic_binding(page_path, anchor, page_text=''):
    return {
        'reference_id': '',
        'path': page_path,
        'start': 0,
        'end': 0,
        'signal': anchor.get('signal') or '',
        'kind': '',
        'role': '',
        'text': anchor['quote'],
        'before': '',
        'after': '',
        'title_chain': [],
    }


def replay_annotations(refs_by_page, events, context, results, states):
    claims = defaultdict(list)
    revoked_anchors = defaultdict(list)

    for event in events:
        if event['operation'] != 'annotate':
            continue
        eid = event['id']
        lstate = states[eid]
        if lstate == 'revoked':
            revoked_anchors[event['subject']['page_path']].append(event['anchors'][0])
        if lstate in {'revoked', 'superseded'}:
            continue

        page_path = event['subject']['page_path']
        anchor = event['anchors'][0]
        statements = event['statements']
        ruling = statements['ruling']
        page_refs = refs_by_page.get(page_path, ())

        bind_status, bound_ref, affected, bind_msg = bind_reference_anchor(context, page_path, anchor, page_refs)
        subject_entry = (
            context.enclosing_entry(page_path, bound_ref['start'], bound_ref['end'])
            if bound_ref else None
        )
        subject_id = subject_entry['id'] if (subject_entry and subject_entry['type'] != 'section') else None
        binding_view = context.binding(bound_ref) if bound_ref else _synthetic_binding(page_path, anchor)
        row = results[eid]
        row.update(
            subject_match=bind_status,
            subject_id=subject_id,
            affected_reference_ids=[r['id'] for r in affected],
            binding=binding_view,
        )

        if lstate == 'conflict':
            row.update(status='conflict', detail='overlapping_active_decisions', reason='overlapping_active_decisions')
            for r in affected:
                claims[r['id']].append((event, row))
            continue

        if bind_status != 'bound':
            status = 'missing' if bind_status == 'missing' else 'stale' if bind_status == 'stale' else 'ambiguous'
            row.update(
                status=status,
                reason=bind_msg,
                detail=bind_msg,
                changes=[{'path': ['anchors', anchor['id']], 'kind': 'changed', 'before': anchor, 'after': bind_msg}],
            )
            for r in affected:
                claims[r['id']].append((event, row))
            continue

        resolved_target_id = None
        target_ok = True
        tmatch = None
        if ruling == 'confirm':
            tmatch, tentry = context.resolve_entry_selector(statements['target'])
            resolved_target_id = tentry['id'] if tentry else None
            target_ok = resolved_target_id is not None and context.locatable(resolved_target_id)

        current_basis = context.reference_basis(bound_ref, anchor['id'], statements)
        saved_basis = event['basis']
        content_ok = current_basis['content_sha256'] == saved_basis['content_sha256']
        context_ok = current_basis['context_sha256'] == saved_basis['context_sha256']
        changes = basis_changes(saved_basis['context'], current_basis['context'])
        if not content_ok:
            changes = [{'path': ['content'], 'kind': 'changed', 'before': saved_basis['content'], 'after': current_basis['content']}] + changes

        valid = content_ok and context_ok and target_ok
        row.update(
            status='applied' if valid else 'stale',
            reason='basis_matches' if valid else 'basis_changed',
            detail='basis_matches' if valid else ('Target ambiguous or unlocatable' if not target_ok else 'basis_changed'),
            current_basis=current_basis,
            resolved_target_id=resolved_target_id,
            changes=changes,
        )
        claims[bound_ref['id']].append((event, row))

    for path in sorted(context.pages):
        refs = refs_by_page[path]
        revoked_here = revoked_anchors.get(path, [])
        for ref in refs:
            ref.update(ref['automatic'])
            ref.update(review_status='unreviewed', decision_ids=[], can_link=False, presentation='not_resolved')
            decisions = claims[ref['id']]
            if len(decisions) > 1:
                ref.update(status='review', reason='decision_conflict', target_id=None, review_status='conflict')
                for _, row in decisions:
                    row.update(status='conflict', reason='overlapping_active_decisions', detail='overlapping_active_decisions')
            elif decisions:
                event, row = decisions[0]
                if row['status'] != 'applied':
                    ref.update(status='review', reason='decision_stale', target_id=None, review_status='stale')
                else:
                    ruling = event['statements']['ruling']
                    if ruling == 'confirm':
                        ref.update(status='resolved', reason='reviewed_reference', target_id=row['resolved_target_id'], review_status='applied')
                    elif ruling == 'missing_definition':
                        ref.update(status='unresolved', reason='reviewed_missing_definition', target_id=None, review_status='applied')
                    else:
                        ref.update(status='excluded', reason='reviewed_non_reference', target_id=None, review_status='applied')
            elif revoked_here:
                for rev_anchor in revoked_here:
                    st, bound_ref, _, _ = bind_reference_anchor(context, path, rev_anchor, [ref])
                    if st == 'bound' and bound_ref['id'] == ref['id']:
                        ref['review_status'] = 'revoked'
                        break
            ref['decision_ids'] = [e['id'] for e, _ in decisions]


def presentation(refs, context, path):
    titles = [{'start': e['start'], 'end': e['title_end']} for e in context.by_page[path] if e['title_end'] > e['start']]
    originals = [r for r in refs if r['signal'] == 'explicit']
    for ref in refs:
        if ref['status'] != 'resolved':
            ref['presentation'] = 'original_retained' if ref['signal'] == 'explicit' else 'not_resolved'
        elif not ref['text'].strip() or ref['end'] <= ref['start']:
            ref['presentation'] = 'empty_range'
        elif any(overlap(ref, title) for title in titles):
            ref['presentation'] = 'definition_title'
        elif not context.locatable(ref['target_id']):
            ref['presentation'] = 'target_unlocatable'
        elif ref['signal'] != 'explicit' and any(overlap(ref, original) for original in originals):
            ref['presentation'] = 'covered_by_original'
        else:
            ref.update(can_link=True, presentation='link')
    # Do not let DOM order choose between overlapping independent click targets.
    eligible = sorted((r for r in refs if r['can_link']), key=lambda r: (r['start'], r['end'], r['id']))
    active = []
    conflicts = set()
    for ref in eligible:
        active = [r for r in active if r['end'] > ref['start']]
        for previous in active:
            if overlap(previous, ref):
                conflicts.update((previous['id'], ref['id']))
        active.append(ref)
    for ref in refs:
        if ref['id'] in conflicts:
            ref.update(can_link=False, presentation='overlapping_references')


def _ui_event_view(event, binding):
    ruling = event['statements']['ruling'] if event['operation'] == 'annotate' else 'revoke'
    return {
        **event,
        'operation': ruling,
        'binding': binding,
        **(
            {'candidate_search': {'query': event['statements'].get('search_query', '')}}
            if ruling == 'missing_definition'
            else {}
        ),
    }


def build_references(db, source, out, annotations_path=DEFAULT_ANNOTATIONS, dom_by_path=None):
    ann_file = Path(annotations_path).resolve()
    raw_bytes = ann_file.read_bytes() if ann_file.exists() else b''
    all_events, log_hash = load_annotations(ann_file)
    _, events = partition_annotations(all_events)
    states, _ = annotation_lifecycle(events)

    context = ReviewContext(db)
    originals = discover_originals(source, context, dom_by_path=dom_by_path)
    results = {}
    for event in events:
        if event['operation'] != 'annotate':
            continue
        eid = event['id']
        lstate = states[eid]
        anchor = event['anchors'][0]
        page_path = event['subject']['page_path']
        results[eid] = {
            'id': eid,
            'annotation_id': event['annotation_id'],
            'kind': 'reference',
            'status': lstate,
            'lifecycle_state': lstate,
            'subject_match': None,
            'subject_id': None,
            'subject': event['subject'],
            'detail': lstate,
            'reason': lstate,
            'event': event,
            'current_basis': None,
            'affected_reference_ids': [],
            'resolved_target_id': None,
            'changes': [],
            'binding': _synthetic_binding(page_path, anchor),
        }

    db.executescript('''
        CREATE TABLE effective_references(id TEXT PRIMARY KEY,page_path TEXT REFERENCES pages(path),start INTEGER,end INTEGER,
            signal TEXT,status TEXT,target_id TEXT REFERENCES entries(id),review_status TEXT,can_link INTEGER,presentation TEXT,data TEXT NOT NULL);
        CREATE INDEX effective_reference_page_idx ON effective_references(page_path,start,end);
        CREATE INDEX effective_reference_review_idx ON effective_references(review_status,status);
        CREATE TABLE IF NOT EXISTS knowledge_annotations(id TEXT PRIMARY KEY,annotation_id TEXT,subject_id TEXT REFERENCES entries(id),kind TEXT,status TEXT,data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS knowledge_annotation_subject ON knowledge_annotations(subject_id,annotation_id,status);
        DELETE FROM knowledge_annotations WHERE kind='reference';
        CREATE INDEX IF NOT EXISTS concept_observation_page_idx ON concept_observations(page_path,start,end);
    ''')

    refs_by_page = {}
    for path in sorted(context.pages):
        refs = [reference(o) for o in originals.get(path, [])]
        grouped = defaultdict(list)
        for ref in refs:
            grouped[(ref['start'], ref['end'])].append(ref)
        exact = {span: group[0] for span, group in grouped.items() if len(group) == 1}
        for raw, in db.execute(
            "SELECT data FROM concept_observations WHERE page_path=? AND signal IN ('index','format','plain','table') ORDER BY start,end,id",
            (path,),
        ):
            o = json.loads(raw)
            original = exact.get((o['start'], o['end']))
            if original:
                original['observation_ids'].append(o['id'])
            elif o['role'] not in {'definition', 'explicit_reference'}:
                refs.append(reference(o))
        refs_by_page[path] = refs

    replay_annotations(refs_by_page, events, context, results, states)

    counts = Counter()
    presentations = Counter()
    total = 0
    for path in sorted(context.pages):
        refs = refs_by_page[path]
        presentation(refs, context, path)
        for ref in refs:
            assert 0 <= ref['start'] <= ref['end'] <= len(context.pages[path]['text']), ref['id']
            db.execute(
                'INSERT INTO effective_references VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (
                    ref['id'], path, ref['start'], ref['end'], ref['signal'], ref['status'], ref['target_id'],
                    ref['review_status'], int(ref['can_link']), ref['presentation'], canonical(ref),
                ),
            )
            total += 1
            counts[(ref['review_status'], ref['status'])] += 1
            presentations[ref['presentation']] += 1

    for row in results.values():
        stored = {k: v for k, v in row.items() if k != 'binding'}
        stored['event'] = _ui_event_view(row['event'], row['binding'])
        db.execute(
            'INSERT INTO knowledge_annotations VALUES(?,?,?,?,?,?)',
            (row['id'], row['annotation_id'], row['subject_id'], 'reference', row['status'], canonical(stored)),
        )
        if row['status'] in {'stale', 'missing', 'ambiguous', 'conflict'}:
            ui_row = {**stored}
            record = {
                'id': 'review_decision_' + row['id'],
                'target': row['subject']['page_path'],
                'reason': 'review_decision_' + ('stale' if row['status'] == 'ambiguous' else row['status']),
                'priority': 'high',
                'detail': ui_row,
                'evidence': row['binding'],
            }
            db.execute(
                'INSERT INTO review_queue VALUES(?,?,?,?,?)',
                (record['id'], record['target'], record['reason'], record['priority'], canonical(record)),
            )

    missing_counts = build_missing_definitions(db, events, results, out)
    ann_snapshot = {
        'path': str(ann_file),
        'sha256': log_hash,
        'bytes': len(raw_bytes),
        'events': len(all_events),
    }
    report = {
        'references': total,
        'missing_definitions': missing_counts,
        'outcomes': {f'{review}/{status}': n for (review, status), n in sorted(counts.items())},
        'presentation': dict(presentations),
        'decisions': dict(Counter(r['status'] for r in results.values())),
    }
    db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('annotation_snapshot', canonical(ann_snapshot)))
    db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('reference_coverage', canonical(report)))
    (Path(out) / 'reference_coverage.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def build_missing_definitions(db, events, results, out):
    """Derive a durable issue queue from unified reference annotations."""
    db.executescript("""
        CREATE TABLE missing_definitions(id TEXT PRIMARY KEY,reference_id TEXT,page_path TEXT,status TEXT,data TEXT NOT NULL);
        CREATE INDEX missing_definition_status_idx ON missing_definitions(status,page_path);
    """)
    replacements = {e['supersedes']: e for e in events if e.get('supersedes')}
    records = []
    for event in events:
        if event['operation'] != 'annotate' or event['statements']['ruling'] != 'missing_definition':
            continue
        terminal = event
        while terminal['id'] in replacements:
            terminal = replacements[terminal['id']]
        result = results[terminal['id']]
        status = result['status']
        if terminal is event:
            status = 'open' if status == 'applied' else status
        elif status == 'applied':
            status = {
                'confirm': 'resolved',
                'non_reference': 'dismissed',
                'missing_definition': 'superseded',
            }[terminal['statements']['ruling']]
        else:
            status = 'needs_review'  # Withdrawing/failing a repair does not prove the definition exists.
        ev_res = results[event['id']]
        binding = ev_res['binding']
        ui_event = _ui_event_view(event, binding)
        record = {
            'id': event['id'],
            'reference_id': binding['reference_id'],
            'path': event['subject']['page_path'],
            'text': event['anchors'][0]['quote'],
            'status': status,
            'event': ui_event,
            'latest_decision_id': terminal['id'],
            'latest_status': result['status'],
            'target_id': result.get('resolved_target_id') if status == 'resolved' else None,
            'changes': result.get('changes', []),
        }
        records.append(record)
        db.execute(
            'INSERT INTO missing_definitions VALUES(?,?,?,?,?)',
            (record['id'], record['reference_id'], record['path'], status, canonical(record)),
        )
        if status in {'open', 'needs_review'}:
            review = {
                'id': 'missing_definition_' + event['id'],
                'target': record['path'],
                'reason': 'missing_definition',
                'priority': 'high',
                'detail': record,
                'evidence': binding,
            }
            db.execute(
                'INSERT INTO review_queue VALUES(?,?,?,?,?)',
                (review['id'], review['target'], review['reason'], review['priority'], canonical(review)),
            )
    (Path(out) / 'missing_definitions.jsonl').write_text(''.join(canonical(r) + '\n' for r in records))
    return dict(Counter(r['status'] for r in records))


def read_reference(db, reference_id):
    row = db.execute('SELECT data FROM effective_references WHERE id=?', (reference_id,)).fetchone()
    if not row:
        raise ValueError('Reference not found; look up a valid reference ID')
    return json.loads(row[0])


def inspect_reference(db, reference_id, target=None, *, context=None, path=DEFAULT_ANNOTATIONS, missing_definition=False, query=''):
    from anna.review.search import search_candidates

    ref = read_reference(db, reference_id)
    context = context or ReviewContext(db)
    if target and target not in context.entries:
        raise ValueError('Target entry not found')
    if missing_definition and target:
        raise ValueError('Missing-definition checks cannot specify a target')
    ruling = 'missing_definition' if missing_definition else 'confirm' if target else 'non_reference'
    selected = context.entries.get(target)
    if ruling == 'confirm':
        statements = {'ruling': 'confirm', 'target': entry_selector(selected)}
    elif ruling == 'missing_definition':
        statements = {'ruling': 'missing_definition', 'search_query': query}
    else:
        statements = {'ruling': 'non_reference'}
    page_text = context.pages[ref['path']]['text']
    anchor_id = 'r1'
    anchor = {
        'id': anchor_id,
        'kind': 'span',
        'quote': ref['text'],
        'within': _suggest_within(page_text, ref['start'], ref['end']),
        'signal': ref['signal'],
    }
    subject = {'kind': 'page', 'page_path': ref['path']}
    owner = context.enclosing_entry(ref['path'], ref['start'], ref['end'])
    if owner and owner['type'] != 'section':
        subject['entry'] = {
            'type': owner['type'],
            'ruleset': owner['ruleset'],
            'name_zh': owner['name_zh'],
            'name_en': owner.get('name_en', ''),
        }
    basis = context.reference_basis(ref, anchor_id, statements)
    candidates = [context.entries[i] for i in ref['automatic']['candidates'] if i in context.entries]
    target_text = context.pages[selected['page_path']]['text'][selected['start']:selected['end']] if selected else None
    return {
        'reference': ref,
        'excerpt': page_text[max(0, ref['start'] - 160):ref['end'] + 160],
        'subject': subject,
        'anchors': [anchor],
        'statements': statements,
        'target': selected,
        'target_text': target_text,
        'candidates': candidates,
        **({'candidate_search': search_candidates(db, reference_id, query=query, context=context)} if missing_definition else {}),
        'basis': basis,
        'content_sha256': basis['content_sha256'],
        'context_sha256': basis['context_sha256'],
        'snapshot_sha256': input_snapshot(db),
        'log_sha256': load_annotations(path)[1],
    }


def save_reference_batch(db, path, items, *, expected_snapshot=None, expected_log=None):
    if not isinstance(items, list) or not items:
        raise ValueError('Batch must be a non-empty list of reference annotations')
    with locked(path):
        require_annotations_appendable(db, path)
        if expected_snapshot is not None and expected_snapshot != input_snapshot(db):
            raise ValueError('Build snapshot changed; inspect the whole batch again')
        all_events, log_hash = load_annotations(path)
        if expected_log is not None and expected_log != log_hash:
            raise ValueError('Annotation log changed; re-check the batch before saving')
        _, ref_events = partition_annotations(all_events)
        states, _ = annotation_lifecycle(ref_events)
        context = ReviewContext(db)
        by_id = {e['id']: e for e in ref_events}
        pending = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('Each reference annotation must be an object')
            ruling = item.get('ruling')
            reference_id = item.get('reference_id')
            target = item.get('target')
            reviewer = item.get('reviewer')
            reason = item.get('reason')
            supersedes = item.get('supersedes')
            if ruling not in {'confirm', 'non_reference', 'missing_definition'}:
                raise ValueError('Unsupported reference ruling')
            if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reason, str) or not reason.strip():
                raise ValueError('Reviewer and reason cannot be empty')
            if not isinstance(reference_id, str) or reference_id in seen:
                raise ValueError('Reference ID is missing or duplicated in batch')
            if target is not None and not isinstance(target, str):
                raise ValueError('Target ID must be a string')
            seen.add(reference_id)

            view = inspect_reference(
                db, reference_id, target, context=context, path=path,
                missing_definition=ruling == 'missing_definition', query=item.get('search_query', ''),
            )
            if item.get('expected_content_sha256') and item['expected_content_sha256'] != view['content_sha256']:
                raise ValueError('Reference content basis changed; inspect again: ' + reference_id)
            if item.get('expected_context_sha256') and item['expected_context_sha256'] != view['context_sha256']:
                raise ValueError('Reference context basis changed; inspect again: ' + reference_id)

            ref = view['reference']
            if not ref['text'].strip() or ref['presentation'] == 'definition_title':
                raise ValueError('This is an empty range or a definition entry; it cannot be reviewed as a body reference')
            if ruling == 'confirm' and (not target or not context.locatable(target)):
                raise ValueError('Confirming a reference requires a locatable definition entry')
            if ruling != 'confirm' and target is not None:
                raise ValueError('Non-reference or missing-definition rulings cannot have a target')

            anchor = view['anchors'][0]
            aid = item.get('annotation_id') or (by_id[supersedes]['annotation_id'] if supersedes in by_id else None)
            if aid is None:
                aid = 'ann_ref_' + digest([ref['path'], anchor.get('within', ''), anchor['quote'], ref['signal']])[:20]

            active = [
                e['id'] for e in ref_events
                if e['operation'] == 'annotate'
                and states.get(e['id']) in {'active', 'conflict'}
                and (
                    e['annotation_id'] == aid
                    or (
                        e['subject']['page_path'] == ref['path']
                        and bind_reference_anchor(context, ref['path'], e['anchors'][0], [ref])[0] == 'bound'
                    )
                )
            ]
            if sorted(active) != ([supersedes] if supersedes else []):
                raise ValueError('This reference already has an active annotation; supersede it or revoke competing revisions')

            event = {
                'operation': 'annotate',
                'annotation_id': aid,
                'kind': 'reference',
                'subject': view['subject'],
                'anchors': view['anchors'],
                'statements': view['statements'],
                'basis': view['basis'],
                'reviewer': reviewer.strip(),
                'reason': reason.strip(),
            }
            if supersedes:
                event['supersedes'] = supersedes
            pending.append(event)
        return append_annotation_events(path, pending, log_hash)


def save_reference_annotation(db, path, *, ruling, reference_id, target=None, expected_content_sha256=None,
                              expected_context_sha256=None, reviewer, reason, supersedes=None, search_query='', annotation_id=None):
    return save_reference_batch(
        db,
        path,
        [{
            'ruling': ruling,
            'reference_id': reference_id,
            'target': target,
            'expected_content_sha256': expected_content_sha256,
            'expected_context_sha256': expected_context_sha256,
            'reviewer': reviewer,
            'reason': reason,
            'supersedes': supersedes,
            'search_query': search_query,
            'annotation_id': annotation_id,
        }],
    )[0]
