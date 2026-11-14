"""One effective reference model for queries and the source-preserving link layer."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from anna.catalog.concepts import aliases
from anna.source.html import Document, compact, key, resolve_link
from anna.serialization import canonical, digest
from anna.review.store import DEFAULT_DECISIONS, lifecycle, load_events, REVIEW_SEMANTICS_VERSION, KNOWLEDGE_OPERATIONS


class ReviewContext:
    def __init__(self, db):
        self.entries = {e['id']: e for (raw,) in db.execute('SELECT data FROM entries') for e in [json.loads(raw)]}
        self.pages = {p: {'text': t, 'sha256': h, 'source_ids': json.loads(s)}
                      for p, t, h, s in db.execute('SELECT path,text,sha256,source_ids FROM pages')}
        self.sources = {sid: {k: v for k, v in json.loads(raw).items()
                              if k not in {'paths', 'toc_node', 'evidence', 'reference_role_evidence', 'catalog_ids'}}
                        for sid, raw in db.execute('SELECT id,data FROM sources')}
        self.by_page = defaultdict(list)
        self.names = defaultdict(set)
        self.entry_dependencies = {}
        for e in self.entries.values():
            self.by_page[e['page_path']].append(e)
            self.entry_dependencies[e['id']] = {
                k: e.get(k) for k in ('name', 'name_zh', 'name_en', 'type', 'ruleset', 'ruleset_status',
                                     'source_ids', 'parent_id', 'page_path', 'start', 'end', 'title_end',
                                     'anchors', 'fields', 'field_conflicts', 'classification_status', 'legacy', 'identity_status')}

            for name in aliases(e): self.names[key(name)].add(e['id'])
        self.locals = defaultdict(list)
        for path, raw in db.execute('SELECT json_extract(data,\'$.path\'),data FROM local_definitions'):
            self.locals[path].append(json.loads(raw))
        coverage = json.loads(db.execute("SELECT value FROM metadata WHERE key='concept_coverage'").fetchone()[0])
        self.profiles = {p['path']: {'id': p['id'], 'supported': p['supported'],
            'rules': {k: {field: rule[field] for field in ('selectors', 'annotation')}
                      for k, rule in p.get('rules', {}).items()}}
            for p in coverage['style_profiles']}

    def locatable(self, target):
        e = self.entries.get(target)
        return bool(e and self.pages[e['page_path']]['text'][e['start']:max(e['end'], e['title_end'])].strip())

    def binding(self, ref):
        text = self.pages[ref['path']]['text']
        owners = sorted((e for e in self.by_page[ref['path']] if e['start'] <= ref['start'] < e['end']),
                        key=lambda e: (e['start'], -e['end'], e['id']))
        return {k: ref[k] for k in ('path', 'start', 'end', 'signal', 'kind', 'role')} | {
            'reference_id': ref['id'], 'text': text[ref['start']:ref['end']],
            'before': text[max(0, ref['start']-80):ref['start']], 'after': text[ref['end']:ref['end']+80],
            'title_chain': [{'id': e['id'], 'name': e['name'], 'type': e['type']} for e in owners]}

    def basis(self, ref, target=None, *, search_query=None):
        """Conservative dependencies, including rejected/name competitors, not just the winner."""
        page = self.pages[ref['path']]
        ids = set(ref['automatic']['candidates']) | self.names.get(key(ref['text']), set())
        destination = ref.get('evidence', {}).get('target_path')
        if destination: ids.update(e['id'] for e in self.by_page[destination])
        if target: ids.add(target)
        search = None
        if search_query is not None:
            from anna.review.search import candidate_matches, SEARCH_VERSION
            matches = candidate_matches(self, ref, search_query)
            candidate_ids = sorted(m['entry']['id'] for m in matches)
            ids.update(candidate_ids)
            search = {'query': search_query, 'candidate_ids': candidate_ids, 'strategy_version': SEARCH_VERSION}
        owners = [e for e in self.by_page[ref['path']] if e['start'] <= ref['start'] < e['end']]
        definition_ids = set(ids)
        ids.update(e['id'] for e in owners)
        source_ids = set(page['source_ids'])
        for eid in ids:
            source_ids.update(self.entries.get(eid, {}).get('source_ids', []))
        evidence = dict(ref.get('evidence', {}))
        stylesheets = evidence.pop('stylesheets', [])
        evidence.pop('stylesheet_sha256', None)
        # The applied grammar matters; unrelated CSS bytes and global TOC hashes do not.
        evidence['format_profiles'] = {p['path']: self.profiles.get(p['path'])
                                       for p in stylesheets if p.get('supported')}
        definitions = {eid: (self.entry_dependencies[eid] | {
            'content_sha256': digest(self.pages[self.entries[eid]['page_path']]['text'][
                self.entries[eid]['start']:self.entries[eid]['end']])})
            if eid in self.entries else None for eid in sorted(definition_ids)}
        # Enclosing identities determine scope; avoid hashing whole ancestor chapters.
        scopes = {e['id']: {k: v for k, v in self.entry_dependencies[e['id']].items()
                             if k not in {'start', 'end', 'title_end'}} for e in owners}
        smallest = min(owners, key=lambda e: e['end']-e['start']) if owners else None
        context_text = page['text'][smallest['start']:smallest['end']] if smallest else page['text'][max(0,ref['start']-160):ref['end']+160]
        return {
            'schema': 2, 'semantics_version': REVIEW_SEMANTICS_VERSION,
            'binding': self.binding(ref), 'context_sha256': digest(context_text),
            'automatic': ref['automatic'], 'evidence': evidence,
            'definitions': definitions, 'scopes': scopes,
            'sources': {sid: self.sources.get(sid) for sid in sorted(source_ids)},
            'local_definitions': [d for d in self.locals[ref['path']]
                                  if d['scope'][0] <= ref['start'] < d['scope'][1]],
            'target_id': target, 'target_locatable': self.locatable(target) if target else None,
            **({'candidate_search': search} if search is not None else {}),
        }


def basis_changes(before, after):
    """Machine-readable old/new dependencies; legacy approvals require explicit re-review."""
    if before.get('schema') != after.get('schema'):
        return [{'path': ['schema'], 'kind': 'changed', 'before': before.get('schema', 1),
                 'after': after.get('schema'), 'reason': 'dependency_schema_changed'}]
    changes = []
    def walk(old, new, path):
        if old == new: return
        if isinstance(old, dict) and isinstance(new, dict):
            for k in sorted(old.keys() | new.keys()):
                if k not in old or k not in new:
                    changes.append({'path': path+[k], 'kind': 'added' if k not in old else 'removed',
                                    'before': old.get(k), 'after': new.get(k)})
                else: walk(old[k], new[k], path+[k])
        else: changes.append({'path': path, 'kind': 'changed', 'before': old, 'after': new})
    walk(before, after, [])
    return changes



def reference(observation):
    ref = {k: observation[k] for k in ('id', 'path', 'start', 'end', 'text', 'signal', 'kind', 'role', 'evidence')}
    ref['automatic'] = {k: observation[k] for k in ('status', 'reason', 'target_id', 'candidates')}
    ref['observation_ids'] = [ref['id']] if ref['signal'] != 'explicit' else []
    return ref


def discover_originals(source, context):
    """Read original links once; the book exporter no longer has a separate resolver."""
    from anna.source.pages import read_page
    links = defaultdict(list)
    anchors = {}
    for path, page in sorted(context.pages.items()):
        if not page['text']: continue
        doc = Document(path, read_page(Path(source)/path)[0])
        if doc.text != page['text']: raise ValueError('原文文本与数据库不一致：'+path)
        anchors[path] = {name: [doc.span(tag)[0] for tag in tags] for name, tags in doc.anchors.items()}
        occurrences = Counter()
        for tag in doc.body.find_all('a', href=True):
            dest = resolve_link(path, tag['href'])
            if not dest or not dest[0].startswith('topics/'): continue
            a, b = doc.span(tag)
            occurrence = occurrences[(a,b)]; occurrences[(a,b)] += 1
            links[path].append({'id': 'reference_'+digest([path, a, b, 'explicit', occurrence])[:24],
                'path': path, 'start': a, 'end': b, 'text': compact(doc.text[a:b]),
                'signal': 'explicit', 'kind': 'entry', 'role': 'explicit_reference',
                'evidence': {'href': tag['href'], 'target_path': dest[0], 'fragment': dest[1]}})
        doc.close()
    for path, rows in links.items():
        for row in rows:
            destination = row['evidence']['target_path']; fragment = row['evidence']['fragment']
            entries = context.by_page[destination]
            direct = [e for e in entries if fragment and fragment in e.get('anchors', [])]
            if len(direct) == 1:
                candidates = direct
            elif fragment:
                positions = anchors.get(destination, {}).get(fragment, [])
                candidates = [e for e in entries if len(positions) == 1 and e['start'] <= positions[0] < e['end']]
                if candidates:
                    size = min(e['end']-e['start'] for e in candidates)
                    candidates = [e for e in candidates if e['end']-e['start'] == size]
            else:
                exact = [e for e in entries if key(row['text']) in {key(n) for n in (e['name'], e['name_zh'], e['name_en']) if n}]
                candidates = exact or [e for e in entries if e.get('is_primary') and e['type'] != 'section']
            ids = sorted({e['id'] for e in candidates})
            row.update(status='resolved' if len(ids) == 1 else 'ambiguous' if ids else 'unresolved',
                       reason='explicit_locator', target_id=ids[0] if len(ids) == 1 else None, candidates=ids)
    return links


def overlap(a, b):
    return a['start'] < b['end'] and b['start'] < a['end']


def replay_page(refs, events, context, results, states):
    by_id = {r['id']: r for r in refs}
    claims = defaultdict(list)
    for event in events:
        eid = event['id']
        if states[eid] != 'active': continue
        binding = event['binding']; ref = by_id.get(binding['reference_id'])
        affected = [ref] if ref else []
        changes = basis_changes(event['basis'], context.basis(ref, event.get('target_id'),
            search_query=event.get('search_query', '') if event['operation']=='missing_definition' else None)) if ref else [
            {'path': ['binding', 'reference_id'], 'kind': 'removed', 'before': binding['reference_id'], 'after': None}]
        valid = bool(ref and context.binding(ref) == binding
                     and not changes
                     and (event['operation'] != 'confirm' or context.locatable(event['target_id'])))
        if not ref:
            # Do not migrate approvals. Keep changed/split/shifted ranges under review.
            page = context.pages.get(binding['path'], {}).get('text', '')
            spans = [{'start': binding['start'], 'end': binding['end']}]
            pos = page.find(binding['text'])
            while pos >= 0:
                spans.append({'start': pos, 'end': pos+len(binding['text'])})
                pos = page.find(binding['text'], pos+1)
            affected = [r for r in refs if any(overlap(r, span) for span in spans)]
        result = results[eid]
        result.update(status='applied' if valid else 'stale' if affected else 'missing',
                      reason='basis_matches' if valid else 'basis_changed' if affected else 'reference_missing',
                      affected_reference_ids=[r['id'] for r in affected], changes=changes)
        for r in affected: claims[r['id']].append((event, valid))
    for ref in refs:
        ref.update(ref['automatic'])
        ref.update(review_status='unreviewed', decision_ids=[], can_link=False, presentation='not_resolved')
        decisions = claims[ref['id']]
        if len(decisions) > 1:
            ref.update(status='review', reason='decision_conflict', target_id=None, review_status='conflict')
            for event, _ in decisions:
                results[event['id']].update(status='conflict', reason='overlapping_active_decisions')
        elif decisions:
            event, valid = decisions[0]
            if not valid:
                ref.update(status='review', reason='decision_stale', target_id=None, review_status='stale')
            elif event['operation'] == 'confirm':
                ref.update(status='resolved', reason='reviewed_reference', target_id=event['target_id'], review_status='applied')
            elif event['operation'] == 'missing_definition':
                ref.update(status='unresolved', reason='reviewed_missing_definition', target_id=None, review_status='applied')
            else:
                ref.update(status='excluded', reason='reviewed_non_reference', target_id=None, review_status='applied')
        elif any(states[e['id']] == 'revoked' and e['binding']['reference_id'] == ref['id'] for e in events):
            ref['review_status'] = 'revoked'
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
            if overlap(previous, ref): conflicts.update((previous['id'], ref['id']))
        active.append(ref)
    for ref in refs:
        if ref['id'] in conflicts: ref.update(can_link=False, presentation='overlapping_references')


def build_references(db, source, out, decision_path=DEFAULT_DECISIONS):
    events, log_hash = load_events(decision_path)
    states = lifecycle(events)
    all_event_count = len(events)
    reference_ids = {e['id'] for e in events if e['operation'] not in KNOWLEDGE_OPERATIONS and e['operation'] != 'revoke'}
    events = [e for e in events if e['id'] in reference_ids or e['operation']=='revoke' and e['decision_id'] in reference_ids]
    context = ReviewContext(db)
    originals = discover_originals(source, context)
    by_page = defaultdict(list)
    results = {}
    for event in events:
        if event['operation'] == 'revoke':
            results[event['id']] = {'event': event, 'status': 'recorded', 'reason': 'revocation', 'affected_reference_ids': []}
        else:
            by_page[event['binding']['path']].append(event)
            results[event['id']] = {'event': event, 'status': states[event['id']] if states[event['id']] != 'active' else 'missing',
                                   'reason': 'reference_missing' if states[event['id']] == 'active' else states[event['id']], 'affected_reference_ids': [],
                                   'changes': [{'path': ['binding', 'path'], 'kind': 'removed',
                                                'before': event['binding']['path'], 'after': None}] if states[event['id']] == 'active' else []}
    db.executescript('''
        CREATE TABLE effective_references(id TEXT PRIMARY KEY,page_path TEXT REFERENCES pages(path),start INTEGER,end INTEGER,
            signal TEXT,status TEXT,target_id TEXT REFERENCES entries(id),review_status TEXT,can_link INTEGER,presentation TEXT,data TEXT NOT NULL);
        CREATE INDEX effective_reference_page_idx ON effective_references(page_path,start,end);
        CREATE INDEX effective_reference_review_idx ON effective_references(review_status,status);
        CREATE TABLE review_decisions(id TEXT PRIMARY KEY,reference_id TEXT,status TEXT,data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS concept_observation_page_idx ON concept_observations(page_path,start,end);
    ''')
    counts = Counter(); presentations = Counter(); total = 0
    for path in sorted(context.pages):
        refs = [reference(o) for o in originals.get(path, [])]
        grouped = defaultdict(list)
        for ref in refs: grouped[(ref['start'], ref['end'])].append(ref)
        exact = {span: group[0] for span, group in grouped.items() if len(group) == 1}
        for raw, in db.execute("SELECT data FROM concept_observations WHERE page_path=? AND signal IN ('index','format','plain','table') ORDER BY start,end,id", (path,)):
            o = json.loads(raw)
            original = exact.get((o['start'], o['end']))
            if original:
                original['observation_ids'].append(o['id'])
            elif o['role'] not in {'definition', 'explicit_reference'}:
                refs.append(reference(o))
        replay_page(refs, by_page[path], context, results, states)
        presentation(refs, context, path)
        for ref in refs:
            assert 0 <= ref['start'] <= ref['end'] <= len(context.pages[path]['text']), ref['id']
            db.execute('INSERT INTO effective_references VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (ref['id'], path, ref['start'], ref['end'], ref['signal'], ref['status'], ref['target_id'],
                        ref['review_status'], int(ref['can_link']), ref['presentation'], canonical(ref)))
            total += 1; counts[(ref['review_status'], ref['status'])] += 1; presentations[ref['presentation']] += 1
    for event in events:
        result = results[event['id']]
        rid = event.get('binding', {}).get('reference_id')
        db.execute('INSERT INTO review_decisions VALUES(?,?,?,?)', (event['id'], rid, result['status'], canonical(result)))
        if result['status'] in {'stale', 'missing', 'conflict'}:
            record = {'id': 'review_decision_'+event['id'], 'target': event['binding']['path'],
                      'reason': 'review_decision_'+result['status'], 'priority': 'high',
                      'detail': result, 'evidence': event['binding']}
            db.execute('INSERT INTO review_queue VALUES(?,?,?,?,?)',
                       (record['id'], record['target'], record['reason'], record['priority'], canonical(record)))
    missing_counts = build_missing_definitions(db, events, results, out)
    snapshot = {'path': str(Path(decision_path).resolve()), 'sha256': log_hash, 'events': all_event_count,
                'bytes': Path(decision_path).stat().st_size if Path(decision_path).exists() else 0}
    report = {'references': total, 'missing_definitions': missing_counts, 'outcomes': {f'{review}/{status}': n for (review, status), n in sorted(counts.items())},
              'presentation': dict(presentations), 'decisions': dict(Counter(r['status'] for r in results.values()))}
    db.execute('INSERT INTO metadata VALUES(?,?)', ('review_snapshot', canonical(snapshot)))
    db.execute('INSERT INTO metadata VALUES(?,?)', ('reference_coverage', canonical(report)))
    (Path(out)/'reference_coverage.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    (Path(out)/'review_decisions.jsonl').write_text(''.join(canonical(results[e['id']])+'\n' for e in events))
    return report


def query_references(db, *, path='', reference_id='', status='', signal='', review_status='', limit=50, offset=0):
    if not 1 <= limit <= 300 or offset < 0: raise ValueError('引用查询范围无效')
    clauses = []; values = []
    for field, value in [('page_path', path), ('id', reference_id), ('status', status), ('signal', signal), ('review_status', review_status)]:
        if value: clauses.append(field+'=?'); values.append(value)
    where = ' WHERE '+' AND '.join(clauses) if clauses else ''
    total = db.execute('SELECT count(*) FROM effective_references'+where, values).fetchone()[0]
    rows = [json.loads(row[0]) for row in db.execute('SELECT data FROM effective_references'+where+' ORDER BY page_path,start,id LIMIT ? OFFSET ?', values+[limit, offset])]
    return {'total': total, 'offset': offset, 'limit': limit, 'rows': rows}


def query_decisions(db, *, status='', limit=50, offset=0):
    if not 1 <= limit <= 300 or offset < 0: raise ValueError('审核查询范围无效')
    where = ' WHERE status=?' if status else ''; values = [status] if status else []
    total = db.execute('SELECT count(*) FROM review_decisions'+where, values).fetchone()[0]
    rows = [json.loads(row[0]) for row in db.execute('SELECT data FROM review_decisions'+where+' ORDER BY id LIMIT ? OFFSET ?', values+[limit, offset])]
    return {'total': total, 'offset': offset, 'limit': limit, 'rows': rows}


def build_missing_definitions(db, events, results, out):
    """Derive a durable issue queue from the same append-only review history."""
    db.executescript("""
        CREATE TABLE missing_definitions(id TEXT PRIMARY KEY,reference_id TEXT,page_path TEXT,status TEXT,data TEXT NOT NULL);
        CREATE INDEX missing_definition_status_idx ON missing_definitions(status,page_path);
    """)
    replacements = {e['supersedes']: e for e in events if e.get('supersedes')}
    records = []
    for event in events:
        if event['operation'] != 'missing_definition': continue
        terminal = event
        while terminal['id'] in replacements: terminal = replacements[terminal['id']]
        result = results[terminal['id']]; status = result['status']
        if terminal is event:
            status = 'open' if status == 'applied' else status
        elif status == 'applied':
            status = {'confirm':'resolved', 'non_reference':'dismissed', 'missing_definition':'superseded'}[terminal['operation']]
        else:
            status = 'needs_review'  # Withdrawing/failing a repair does not prove the definition exists.
        record = {'id': event['id'], 'reference_id': event['binding']['reference_id'],
                  'path': event['binding']['path'], 'text': event['binding']['text'], 'status': status,
                  'event': event, 'latest_decision_id': terminal['id'], 'latest_status': result['status'],
                  'target_id': terminal.get('target_id') if status == 'resolved' else None,
                  'changes': result.get('changes', [])}
        records.append(record)
        db.execute('INSERT INTO missing_definitions VALUES(?,?,?,?,?)',
                   (record['id'],record['reference_id'],record['path'],status,canonical(record)))
        if status in {'open','needs_review'}:
            review = {'id':'missing_definition_'+event['id'], 'target':record['path'], 'reason':'missing_definition',
                      'priority':'high', 'detail':record, 'evidence':event['binding']}
            db.execute('INSERT INTO review_queue VALUES(?,?,?,?,?)',
                       (review['id'],review['target'],review['reason'],review['priority'],canonical(review)))
    (Path(out)/'missing_definitions.jsonl').write_text(''.join(canonical(r)+'\n' for r in records))
    return dict(Counter(r['status'] for r in records))


def query_missing_definitions(db, *, status='', path='', limit=50, offset=0):
    if not 1 <= limit <= 300 or offset < 0: raise ValueError('缺失定义查询范围无效')
    clauses=[]; values=[]
    for field,value in [('status',status),('page_path',path)]:
        if value: clauses.append(field+'=?'); values.append(value)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    total=db.execute('SELECT count(*) FROM missing_definitions'+where,values).fetchone()[0]
    rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM missing_definitions'+where+' ORDER BY page_path,id LIMIT ? OFFSET ?',values+[limit,offset])]
    return {'total':total,'rows':rows,'limit':limit,'offset':offset}
