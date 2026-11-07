"""Read-only, explainable candidate retrieval. Ranking never changes reference resolution."""
from pathlib import PurePosixPath
from concepts import aliases, RULE_TYPES
from html_extract import key, compact

SEARCH_VERSION = 1


def candidate_matches(context, ref, query=''):
    if not isinstance(query, str) or len(query) > 200: raise ValueError('候选查询须为不超过 200 字的文本')
    query = compact(query)
    terms = {key(n) for n in aliases({'name': ref['text'], 'type': ref['kind']}) if key(n)}
    query_key = key(query)
    evidence = ref.get('evidence', {})
    destination = evidence.get('target_path', '')
    fragment = evidence.get('fragment', '')
    source_ids = set(context.pages[ref['path']]['source_ids'])
    rulesets = {context.sources[s].get('ruleset_hint') for s in source_ids if s in context.sources} - {None, '', '待核'}
    automatic = set(ref['automatic']['candidates'])
    wanted = RULE_TYPES | {'class_feature'} if ref['kind'] == 'concept' else {ref['kind']}
    rows = []
    for entry in context.entries.values():
        names = {key(n) for n in aliases(entry) if key(n)}
        reasons = []; score = 0
        def add(code, value, weight):
            nonlocal score
            reasons.append({'code': code, 'value': value}); score += weight
        if entry['id'] in automatic: add('automatic_candidate', entry['id'], 100)
        if names & terms: add('exact_name', sorted(names & terms), 70)
        if fragment and any(key(a) == key(fragment) for a in entry.get('anchors', [])):
            add('anchor_match', fragment, 60)
        if destination:
            if entry['page_path'] == destination: add('original_path', destination, 65)
            elif PurePosixPath(entry['page_path']).name == PurePosixPath(destination).name:
                add('same_filename', PurePosixPath(destination).name, 50)
        if query_key:
            if any(query_key in name for name in names): add('query_name', query, 40)
            elif query.casefold() in entry['page_path'].casefold(): add('query_path', query, 25)
            else: continue  # An explicit query narrows the structural suggestions.
        if not reasons: continue
        if destination and entry['page_path'].startswith(str(PurePosixPath(destination).parent)+'/'):
            add('destination_directory', str(PurePosixPath(destination).parent), 12)
        if source_ids & set(entry['source_ids']): add('same_source', sorted(source_ids & set(entry['source_ids'])), 8)
        if len(rulesets) == 1 and entry['ruleset'] in rulesets: add('same_ruleset', entry['ruleset'], 5)
        warnings = []
        if len(rulesets) == 1 and entry['ruleset'] in {'5e','5r'} and entry['ruleset'] not in rulesets:
            warnings.append('ruleset_mismatch')
        if ref['kind'] not in {'entry','ambiguous'} and entry['type'] not in wanted: warnings.append('type_mismatch')
        if entry['type'] == 'section': warnings.append('section_candidate')
        if not context.locatable(entry['id']): warnings.append('target_unlocatable')
        rows.append({'entry': entry, 'score': score, 'reasons': reasons, 'warnings': warnings})
    return sorted(rows, key=lambda r: (-r['score'], r['entry']['page_path'], r['entry']['start'], r['entry']['id']))


def search_candidates(db, reference_id, *, query='', entry_type='', ruleset='', source_id='', limit=20, offset=0, context=None):
    import json
    from references import ReviewContext
    if not 1 <= limit <= 100 or offset < 0: raise ValueError('候选查询范围无效')
    row = db.execute('SELECT data FROM effective_references WHERE id=?', (reference_id,)).fetchone()
    if not row: raise LookupError('引用不存在，请重新查询有效引用 ID')
    ref = json.loads(row[0]); context = context or ReviewContext(db)
    matches = candidate_matches(context, ref, query)
    matches = [m for m in matches if (not entry_type or m['entry']['type'] == entry_type)
               and (not ruleset or m['entry']['ruleset'] == ruleset)
               and (not source_id or source_id in m['entry']['source_ids'])]
    rows = []
    for match in matches[offset:offset+limit]:
        entry = match['entry']; text = context.pages[entry['page_path']]['text'][entry['start']:entry['end']]
        rows.append({**{k: entry.get(k) for k in ('id','name','type','ruleset','source_ids','source_names','page_path','start','end','anchors')},
                     'score': match['score'], 'reasons': match['reasons'], 'warnings': match['warnings'],
                     'locatable': context.locatable(entry['id']), 'excerpt': text[:600], 'excerpt_truncated': len(text)>600})
    page = context.pages[ref['path']]['text']
    return {'reference_id': reference_id, 'binding': context.binding(ref),
            'excerpt': page[max(0,ref['start']-160):ref['end']+160],
            'query': query, 'total': len(matches), 'limit': limit, 'offset': offset,
            'search_version': SEARCH_VERSION, 'automatic_approval': False, 'rows': rows}
