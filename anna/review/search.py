"""Read-only, explainable candidate retrieval. Ranking never changes reference resolution."""
from pathlib import PurePosixPath
import re
from anna.catalog.concepts import aliases, RULE_TYPES
from anna.review.store import read_effective_reference
from anna.source.html import key, compact

SEARCH_VERSION = 1

_LEADING_BONUS_RE = re.compile(r'^\+\d+\s*')
_TRAILING_MARK_RE = re.compile(r'[，。、；：,.;:!?！？*†‡]+$')


def _normalized_terms(text, kind):
    cleaned = _TRAILING_MARK_RE.sub('', _LEADING_BONUS_RE.sub('', compact(text))).strip()
    base = {key(n) for n in aliases({'name': cleaned, 'type': kind}) if key(n)} if cleaned else set()
    expanded = set(base)
    for term in base:
        if term.isascii() and len(term) >= 4:
            if len(term) >= 5 and term.endswith('ies'):
                expanded.add(term[:-3] + 'y')
            elif term.endswith('s') and not term.endswith(('ss', 'us', 'is')):
                expanded.add(term[:-1])
    return expanded


def candidate_matches(context, ref, query=''):
    if not isinstance(query, str) or len(query) > 200: raise ValueError('候选查询须为不超过 200 字的文本')
    query = compact(query)
    terms = {key(n) for n in aliases({'name': ref['text'], 'type': ref['kind']}) if key(n)}
    norm_terms = _normalized_terms(ref['text'], ref['kind']) - terms
    query_key = key(query)
    tokens = query.split()
    token_pairs = [(key(tok), tok.casefold()) for tok in tokens]
    token_keys = [tk for tk, _ in token_pairs]
    evidence = ref.get('evidence', {})
    destination = evidence.get('target_path', '')
    fragment = evidence.get('fragment', '')
    source_ids = set(context.page_sources[ref['path']])
    rulesets = {context.source_by_id[s].get('ruleset_hint') for s in source_ids if s in context.source_by_id} - {None, '', '待核'}
    automatic = set(ref['automatic']['candidates'])
    wanted = RULE_TYPES | {'class_feature'} if ref['kind'] == 'concept' else {ref['kind']}
    entry_names = getattr(context, '_search_name_keys', None)
    if entry_names is None:
        entry_names = {eid: {key(n) for n in aliases(entry) if key(n)} for eid, entry in context.entries.items()}
        context._search_name_keys = entry_names
    rows = []
    for entry in context.entries.values():
        names = entry_names[entry['id']]
        reasons = []; score = 0
        def add(code, value, weight):
            nonlocal score
            reasons.append({'code': code, 'value': value}); score += weight
        if entry['id'] in automatic: add('automatic_candidate', entry['id'], 100)
        if names & terms: add('exact_name', sorted(names & terms), 70)
        elif norm_terms and (names & norm_terms): add('normalized_name', sorted(names & norm_terms), 55)
        if fragment and any(key(a) == key(fragment) for a in entry.get('anchors', [])):
            add('anchor_match', fragment, 60)
        if destination:
            if entry['page_path'] == destination: add('original_path', destination, 65)
            elif PurePosixPath(entry['page_path']).name == PurePosixPath(destination).name:
                add('same_filename', PurePosixPath(destination).name, 50)
        if query_key:
            path_lower = entry['page_path'].casefold()
            if len(tokens) == 1:
                if any(query_key in name for name in names): add('query_name', query, 40)
                elif query.casefold() in path_lower: add('query_path', query, 25)
                else: continue  # An explicit query narrows the structural suggestions.
            else:
                if any(query_key in name for name in names): add('query_name', query, 40)
                elif all(tk and any(tk in name for name in names) for tk in token_keys):
                    add('query_name', query, 35)
                elif all((tk and any(tk in name for name in names)) or tl in path_lower for tk, tl in token_pairs):
                    add('query_path', query, 25)
                else: continue
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


def search_candidates(db, reference_id, *, query='', entry_type='', ruleset='', source_id='',
                      locatable_only=False, exclude_sections=False, limit=20, offset=0, context=None):
    from anna.review.context import Context
    if not 1 <= limit <= 100 or offset < 0: raise ValueError('候选查询范围无效')
    ref = read_effective_reference(db, reference_id)
    if ref is None: raise LookupError('引用不存在，请重新查询有效引用 ID')
    context = context or Context(db)
    matches = candidate_matches(context, ref, query)
    matches = [m for m in matches if (not entry_type or m['entry']['type'] == entry_type)
               and (not ruleset or m['entry']['ruleset'] == ruleset)
               and (not source_id or source_id in m['entry']['source_ids'])
               and (not locatable_only or context.locatable(m['entry']['id']))
               and (not exclude_sections or m['entry']['type'] != 'section')]
    rows = []
    for match in matches[offset:offset+limit]:
        entry = match['entry']; text = context.pages[entry['page_path']][entry['start']:entry['end']]
        rows.append({**{k: entry.get(k) for k in ('id','name','type','ruleset','source_ids','source_names','page_path','start','end','anchors')},
                     'score': match['score'], 'reasons': match['reasons'], 'warnings': match['warnings'],
                     'locatable': context.locatable(entry['id']), 'excerpt': text[:600], 'excerpt_truncated': len(text)>600})
    page = context.pages[ref['path']]
    return {'reference_id': reference_id, 'binding': context.binding(ref),
            'excerpt': page[max(0,ref['start']-160):ref['end']+160],
            'query': query, 'total': len(matches), 'limit': limit, 'offset': offset,
            'search_version': SEARCH_VERSION, 'automatic_approval': False, 'rows': rows}
