"""Compare anna's entries with 5etools-cn, an independently curated catalog of the same rules.

5etools-cn stores each rule object with its Chinese name, English name and source code, and much of
its Chinese text was taken from 不全书. Those identities, machine fields and reprint links are a second
opinion on anna's entries, never a replacement: a match is a candidate that names the rule which
found it, and a disagreement is reported for review instead of overwriting what 不全书 prints.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

from html_extract import compact, digest, key

CATALOG = '5etools-cn'
# The 5etools-cn record kinds that can describe the same object as an anna entry type.
KINDS = {
    'spell': ('spell',), 'creature': ('monster',), 'magic_item': ('item', 'magicvariant'),
    'equipment': ('baseitem', 'item'), 'poison': ('item',), 'animal_offer': ('baseitem', 'item'),
    'feat': ('feat',), 'class': ('class',), 'subclass': ('subclass',),
    'class_feature': ('classFeature', 'subclassFeature', 'optionalfeature'),
    'species': ('race', 'subrace'), 'background': ('background',),
    'rule': ('variantrule', 'action', 'condition', 'status', 'sense', 'skill'),
    'action': ('action',), 'condition': ('condition', 'status'), 'disease': ('disease',),
    'hazard': ('hazard', 'trap'), 'trap': ('trap', 'hazard'), 'subsystem': ('variantrule',),
    'facility': ('facility',), 'vehicle': ('vehicle', 'object'), 'reward': ('reward', 'charoption'),
    'lore': ('deity', 'table'),
}
RECORD_KINDS = frozenset(k for kinds in KINDS.values() for k in kinds)
# Generated indexes, Foundry exports and fluff restate records that the rule files already hold.
SKIPPED_DIRS = {'generated'}
SKIPPED_PREFIXES = ('fluff', 'foundry')
OUTCOMES = ('resolved', 'ambiguous', 'other_source', 'unaligned', 'unmatched')
# An English name found in exactly one 5etools-cn source votes for that source. A source is aligned
# by votes once enough of them agree; a source sharing only a few names is not.
MIN_VOTES = 3
MIN_SHARE = 0.2
TRANSLATED_FROM_BOOK = '不全书'

SCHOOLS = {'A': '防护', 'C': '咒法', 'D': '预言', 'E': '惑控', 'V': '塑能', 'I': '幻术', 'N': '死灵', 'T': '变化'}
SIZES = {'T': '微型', 'S': '小型', 'M': '中型', 'L': '大型', 'H': '巨型', 'G': '超巨型'}
RARITIES = {'common': '普通', 'uncommon': '非普通', 'rare': '珍稀', 'very rare': '极珍稀',
            'legendary': '传说', 'artifact': '神器', 'varies': '多种稀有度'}
# Older 不全书 pages print rare items as 稀有 or 罕见; matched records confirm both mean rare.
RARITY_WORDING = {'稀有': '珍稀', '罕见': '珍稀'}
NUMERALS = {c: i for i, c in enumerate('一二三四五六七八九', 1)}


def record_values(kind, item):
    """Comparable machine fields, in anna's vocabulary. A `_copy` record inherits fields it does not list."""
    if '_copy' in item:
        return {}
    values = {}
    if kind == 'spell':
        if isinstance(item.get('level'), int):
            values['spell_level'] = [item['level']]
        if item.get('school') in SCHOOLS:
            values['school'] = [SCHOOLS[item['school']]]
        values['ritual'] = [bool((item.get('meta') or {}).get('ritual'))]
        values['concentration'] = [any(isinstance(d, dict) and d.get('concentration') for d in item.get('duration') or [])]
    elif kind == 'monster':
        cr = item.get('cr')
        cr = cr.get('cr') if isinstance(cr, dict) else cr
        if isinstance(cr, str):
            values['cr'] = [cr]
        sizes = [SIZES[s] for s in item.get('size') or [] if s in SIZES]
        if sizes:
            values['size'] = sizes
    elif kind in {'item', 'magicvariant', 'baseitem'}:
        fields = item.get('inherits', item) if kind == 'magicvariant' else item
        if fields.get('rarity') in RARITIES:
            values['rarity'] = [RARITIES[fields['rarity']]]
            values['attunement'] = [bool(fields.get('reqAttune'))]
    return values


def entry_values(kind, fields):
    """anna's extracted values for the same fields; values anna cannot state plainly are left out."""
    def listed(field):
        value = fields.get(field)
        return [] if value is None else value if isinstance(value, list) else [value]
    values = {}
    if kind == 'spell':
        levels = []
        for v in listed('spell_level'):
            m = re.fullmatch(r'\s*(戏法|([一二三四五六七八九])环|(\d)环)\s*', str(v))
            if m:
                levels.append(0 if m[1] == '戏法' else NUMERALS[m[2]] if m[2] else int(m[3]))
        values['spell_level'] = levels
        values['school'] = [v for v in listed('school') if v in SCHOOLS.values()]
        values['ritual'] = [v for v in listed('ritual') if isinstance(v, bool)]
        values['concentration'] = [v for v in listed('concentration') if isinstance(v, bool)]
    elif kind == 'creature':
        values['cr'] = [str(v) for v in listed('cr')]
        values['size'] = [v for v in listed('size') if v in SIZES.values()]
    elif kind == 'magic_item':
        values['rarity'] = [RARITY_WORDING.get(v, v) for v in listed('rarity') if RARITY_WORDING.get(v, v) in RARITIES.values()]
        values['attunement'] = [v for v in listed('attunement') if isinstance(v, bool)]
    return {k: v for k, v in values.items() if v}


def git_commit(root):
    try:
        return subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return 'unversioned'


def load_records(root):
    """Every record whose kind can describe an anna entry, plus the snapshot they were read from."""
    root = Path(root)
    data = root / 'data'
    if not data.is_dir():
        raise FileNotFoundError(f'{CATALOG} 对照镜像缺少 data/：{root}')
    records, hashes, unreadable = [], {}, []
    for path in sorted(data.rglob('*.json')):
        inner = path.relative_to(data).parts
        if inner[0] in SKIPPED_DIRS or path.name.startswith(SKIPPED_PREFIXES):
            continue
        rel = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        hashes[rel] = sha256(raw).hexdigest()
        try:
            doc = json.loads(raw)
        except ValueError as e:
            unreadable.append({'path': rel, 'error': str(e)})
            continue
        if not isinstance(doc, dict):
            continue
        for kind, items in doc.items():
            if kind not in RECORD_KINDS or not isinstance(items, list):
                continue
            for i, item in enumerate(items):
                if not isinstance(item, dict) or not (item.get('ENG_name') or item.get('name')):
                    continue
                locator = f'{rel}#/{kind}/{i}'
                reprints = [x if isinstance(x, str) else x.get('uid', '') for x in item.get('reprintedAs') or [] if isinstance(x, (str, dict))]
                records.append({
                    'id': '5etools_' + digest(locator), 'kind': kind, 'locator': locator,
                    'name_zh': compact(item.get('name') or ''), 'name_en': compact(item.get('ENG_name') or ''),
                    'source': str(item.get('source') or ''), 'page': item.get('page'),
                    'translator': str(item.get('translator') or ''), 'edition': str(item.get('edition') or ''),
                    'copied': '_copy' in item, 'reprinted_as': [x for x in reprints if x],
                    'owner': {'class_zh': item.get('className'), 'class_source': item.get('classSource'),
                              'subclass_short': item.get('subclassShortName'), 'subclass_source': item.get('subclassSource'),
                              'short_name': item.get('shortName'), 'level': item.get('level')},
                    'values': record_values(kind, item)})
    snapshot = {'catalog': CATALOG, 'root': str(root), 'commit': git_commit(root), 'files': len(hashes),
                'input_sha256': sha256(''.join(f'{p}:{h}\n' for p, h in sorted(hashes.items())).encode()).hexdigest(),
                'unreadable_files': unreadable}
    return records, snapshot


def owner_key(name):
    """Owner names compared without articles: 不全书 prints both Path of Wild Heart and Path of the Wild Heart."""
    return key(re.sub(r'\b(?:the|of)\b', ' ', name or '', flags=re.I))


def record_context(records, code_rulesets):
    """Owner class, subclass, level and owner rule generation of feature and subclass records.

    5etools-cn repeats a subclass under each edition of its class; the class source's generation,
    taken from the anna sources aligned with that code, tells the copies apart.
    """
    classes = {(r['name_zh'], r['source']): r['name_en'] for r in records if r['kind'] == 'class'}
    subclasses = {(r['owner']['short_name'], r['source'], r['owner']['class_zh']): r['name_en'] for r in records if r['kind'] == 'subclass'}
    for r in records:
        o, ctx = r['owner'], {}
        if r['kind'] in {'classFeature', 'subclassFeature', 'subclass'} and o['class_zh']:
            name = classes.get((o['class_zh'], o['class_source']))
            if name:
                ctx['class'] = owner_key(name)
            generation = code_rulesets.get(o['class_source'], set())
            if len(generation) == 1:
                ctx['ruleset'] = next(iter(generation))
        if r['kind'] == 'subclassFeature':
            name = subclasses.get((o['subclass_short'], o['subclass_source'], o['class_zh']))
            if name:
                ctx['subclass'] = owner_key(name)
        if r['kind'] in {'classFeature', 'subclassFeature'} and o['level'] is not None:
            ctx['level'] = str(o['level'])
        r['context'] = ctx


def load_entries(db):
    """Typed anna entries with their sources, owner class/subclass and level."""
    types, names = {}, {}
    for eid, typ, en in db.execute('SELECT id,type,name_en FROM entries'):
        types[eid], names[eid] = typ, en or ''
    sources = defaultdict(list)
    for eid, sid in db.execute('SELECT entry_id,source_id FROM entry_sources ORDER BY entry_id,source_id'):
        sources[eid].append(sid)
    owners = defaultdict(lambda: defaultdict(set))
    for subject, obj in db.execute("SELECT subject_id,object_id FROM relations WHERE predicate IN ('part_of','belongs_to_class') AND object_id IS NOT NULL"):
        if types.get(obj) in {'class', 'subclass'} and names[obj]:
            owners[subject][types[obj]].add(owner_key(names[obj]))
    # A feature owned by a subclass also belongs to that subclass's class.
    by_subclass_key = defaultdict(set)
    for eid, typ in types.items():
        if typ == 'subclass' and names[eid] and eid in owners:
            by_subclass_key[owner_key(names[eid])] |= owners[eid]['class']
    entries = []
    placeholders = ','.join('?' * len(KINDS))
    for eid, typ, name, zh, en, ruleset, fields in db.execute(
            f'SELECT id,type,name,name_zh,name_en,ruleset,fields FROM entries WHERE type IN ({placeholders}) ORDER BY id', list(KINDS)):
        fields = json.loads(fields or '{}')
        ctx = {}
        own = owners.get(eid, {})
        classes = set(own.get('class', set()))
        for sub in own.get('subclass', set()):
            classes |= by_subclass_key.get(sub, set())
        if classes:
            ctx['class'] = classes
        if own.get('subclass'):
            ctx['subclass'] = set(own['subclass'])
        levels = fields.get('level')
        levels = levels if isinstance(levels, list) else [] if levels is None else [levels]
        if levels and typ == 'class_feature':
            ctx['level'] = {str(v) for v in levels}
        if ruleset in {'5e', '5r'}:
            ctx['ruleset'] = {ruleset}
        entries.append({'id': eid, 'type': typ, 'name': name or '', 'name_zh': zh or '', 'name_en': en or '',
                        'source_ids': sources.get(eid, []), 'fields': fields, 'context': ctx})
    return entries


def align_sources(sources, entries, records):
    """Pair each anna source with the 5etools-cn source codes that print the same book.

    Two independent signals: the source's own catalog abbreviation equals a code, or English names
    that occur in a single code concentrate on it. An abbreviation alone can collide (不全书's CM is a
    third-party book, 5etools' CM is Candlekeep Mysteries), so a code no name supports stays 待核.
    Abbreviated codes that the names support only weakly while the names point elsewhere leave the
    source in conflict. Only 上下文推得 pairs take part in matching.
    """
    codes = {r['source'] for r in records if r['source']}
    folded = defaultdict(set)
    for c in codes:
        folded[c.casefold()].add(c)
    by_name = defaultdict(set)
    for r in records:
        if r['name_en']:
            by_name[(r['kind'], key(r['name_en']))].add(r['source'])
    votes = defaultdict(Counter)
    for e in entries:
        if not e['name_en'] or not e['source_ids']:
            continue
        found = set().union(*(by_name.get((k, key(e['name_en'])), set()) for k in KINDS[e['type']]))
        if len(found) == 1:
            for sid in e['source_ids']:
                votes[sid][next(iter(found))] += 1
    pairs = {}
    for sid, s in sources.items():
        for abbreviation in s.get('abbreviations') or []:
            for code in folded.get(abbreviation.casefold(), ()):
                pair = pairs.setdefault((sid, code), {'source_id': sid, 'code': code, 'rules': [], 'evidence': {}})
                pair['rules'].append('catalog_code')
                pair['evidence']['abbreviation'] = abbreviation
    for sid, counts in votes.items():
        total = sum(counts.values())
        for code, n in counts.items():
            if n >= MIN_VOTES and n >= MIN_SHARE * total:
                pair = pairs.setdefault((sid, code), {'source_id': sid, 'code': code, 'rules': [], 'evidence': {}})
                pair['rules'].append('unique_name_votes')
    for (sid, code), pair in pairs.items():
        pair['evidence']['votes'] = votes[sid][code]
        pair['evidence']['source_votes'] = sum(votes[sid].values())
    for sid in {sid for sid, _ in pairs}:
        mine = [p for (s, _), p in pairs.items() if s == sid]
        coded = {p['code'] for p in mine if 'catalog_code' in p['rules'] and p['evidence']['votes']}
        voted = {p['code'] for p in mine if 'unique_name_votes' in p['rules']}
        conflict = bool(coded and voted and not coded & voted)
        for p in mine:
            supported = 'unique_name_votes' in p['rules'] or p['evidence']['votes'] > 0
            p['status'] = '冲突' if conflict else '上下文推得' if supported else '待核'
    return sorted(pairs.values(), key=lambda p: (p['source_id'], p['code']))


def code_rulesets(alignment, sources):
    """The rule generations of the anna sources aligned with each 5etools-cn code."""
    out = defaultdict(set)
    for p in alignment:
        hint = sources[p['source_id']].get('ruleset_hint')
        if p['status'] == '上下文推得' and hint in {'5e', '5r'}:
            out[p['code']].add(hint)
    return out


# A different owner class rules a record out. Subclass, level and the owner's rule generation only
# choose among several same-named records: 不全书 files some features under group headings such as
# Divine Domain, and a feature can move levels between printings.
OWNER_KEYS = ('class',)
NARROWING_KEYS = ('subclass', 'level', 'ruleset')


def agrees(entry_ctx, record_ctx, keys):
    return all(record_ctx[k] in entry_ctx[k] for k in keys if entry_ctx.get(k) and record_ctx.get(k))


def match_entry(e, aligned, by_en, by_zh):
    """Resolve an entry against records of compatible kinds: English name first, then Chinese name,
    within the 5etools-cn sources aligned with its own; the owner class rules records out, and
    subclass, level and rule generation choose among the rest.

    Returns (outcome, rule, candidates, context_conflict). Candidates that all belong to another
    class stay ambiguous rather than resolved.
    """
    kinds = KINDS[e['type']]
    codes = set().union(*(aligned.get(s, set()) for s in e['source_ids'])) if e['source_ids'] else set()
    tried = []
    for rule, index, name in (('english_name', by_en, e['name_en']), ('chinese_name', by_zh, e['name_zh'])):
        if not key(name):
            continue
        found = [r for k in kinds for r in index.get((k, key(name)), [])]
        tried.append((rule, found))
        here = [r for r in found if r['source'] in codes]
        if not here:
            continue
        owned = [r for r in here if agrees(e['context'], r['context'], OWNER_KEYS)]
        if not owned:
            return 'ambiguous', rule, here, True
        if len(owned) == 1:
            return 'resolved', rule if len(here) == 1 else rule + '+owner_context', owned, False
        narrowed = [r for r in owned if agrees(e['context'], r['context'], NARROWING_KEYS)]
        if len(narrowed) == 1:
            return 'resolved', rule + '+owner_context', narrowed, False
        return 'ambiguous', rule, narrowed or owned, False
    rule, found = next(((r, f) for r, f in tried if f), ('', []))
    if not found:
        return 'unmatched', '', [], False
    return ('other_source' if codes else 'unaligned'), rule, found, False


def comparisons(e, r):
    """Disagreements between a resolved pair; agreement and missing values are silent."""
    # Names compare as lookup keys: case, spacing and punctuation never change what a search finds.
    out = []
    if not key(e['name_en']):
        out.append(('name_en_missing', {'entry': e['name_en'], 'record': r['name_en']}))
    elif key(e['name_en']) != key(r['name_en']):
        out.append(('name_en_differs', {'entry': e['name_en'], 'record': r['name_en']}))
    if r['translator'] == TRANSLATED_FROM_BOOK and key(e['name_zh']) != key(r['name_zh']):
        out.append(('name_zh_differs', {'entry': e['name_zh'], 'record': r['name_zh']}))
    mine = entry_values(e['type'], e['fields'])
    for field, theirs in r['values'].items():
        if field in mine and not set(mine[field]) & set(theirs):
            out.append(('field_differs', {'field': field, 'entry': mine[field], 'record': theirs}))
    return out


def compare(db, root):
    """All counterpart outcomes, findings and source alignments for an exported catalog database."""
    records, snapshot = load_records(root)
    sources = {sid: json.loads(raw) for sid, raw in db.execute('SELECT id,data FROM sources')}
    entries = load_entries(db)
    alignment = align_sources(sources, entries, records)
    record_context(records, code_rulesets(alignment, sources))
    aligned = defaultdict(set)
    for p in alignment:
        if p['status'] == '上下文推得':
            aligned[p['source_id']].add(p['code'])
    by_en, by_zh = defaultdict(list), defaultdict(list)
    for r in records:
        if key(r['name_en']):
            by_en[(r['kind'], key(r['name_en']))].append(r)
        if key(r['name_zh']):
            by_zh[(r['kind'], key(r['name_zh']))].append(r)
    outcomes, findings = [], []
    resolved_by = defaultdict(list)
    candidates_seen = set()
    for e in entries:
        outcome, rule, found, context_conflict = match_entry(e, aligned, by_en, by_zh)
        record = found[0] if outcome == 'resolved' else None
        outcomes.append({'entry_id': e['id'], 'type': e['type'], 'name': e['name'], 'outcome': outcome, 'rule': rule,
                         'record_id': record['id'] if record else None, 'candidates': [r['id'] for r in found],
                         'context_conflict': context_conflict,
                         'candidate_sources': sorted({r['source'] for r in found}),
                         'aligned_codes': sorted(set().union(*(aligned.get(s, set()) for s in e['source_ids']))) if e['source_ids'] else []})
        candidates_seen.update(r['id'] for r in found)
        if record:
            resolved_by[record['id']].append(e['id'])
            for kind, detail in comparisons(e, record):
                findings.append({'kind': kind, 'entry_id': e['id'], 'record_id': record['id'], 'detail': detail})
    by_uid = defaultdict(list)
    for r in records:
        by_uid[(r['kind'], key(r['name_zh']), r['source'].casefold())].append(r)
    for r in records:
        for uid in r['reprinted_as']:
            name, _, source = uid.rpartition('|')
            targets = by_uid.get((r['kind'], key(name), source.casefold()), [])
            if len(targets) != 1:
                continue
            for older in resolved_by.get(r['id'], []):
                for newer in resolved_by.get(targets[0]['id'], []):
                    findings.append({'kind': 'reprinted_as', 'entry_id': older, 'record_id': r['id'],
                                     'detail': {'uid': uid, 'other_entry_id': newer, 'other_record_id': targets[0]['id']}})
    covered = {code for codes in aligned.values() for code in codes}
    for r in records:
        if (r['translator'] == TRANSLATED_FROM_BOOK and r['source'] in covered
                and r['id'] not in resolved_by and r['id'] not in candidates_seen):
            findings.append({'kind': 'unclaimed_record', 'entry_id': None, 'record_id': r['id'],
                             'detail': {'name': r['name_zh'], 'name_en': r['name_en'], 'source': r['source'], 'kind': r['kind']}})
    for f in findings:
        f['id'] = 'counterpart_' + digest(f['kind'], f['entry_id'] or '', f['record_id'], json.dumps(f['detail'], ensure_ascii=False, sort_keys=True))
    findings = sorted({f['id']: f for f in findings}.values(), key=lambda f: (f['kind'], f['entry_id'] or '', f['record_id']))
    return records, snapshot, alignment, outcomes, findings, resolved_by


def build_counterparts(db, root, out):
    """Store counterpart tables and exports beside the catalog; returns the coverage report."""
    from build import csv_write, dumps
    records, snapshot, alignment, outcomes, findings, resolved_by = compare(db, root)
    db.executescript(f'''
    CREATE TABLE counterpart_records(id TEXT PRIMARY KEY,kind TEXT NOT NULL,name_zh TEXT,name_en TEXT,source TEXT,data TEXT NOT NULL);
    CREATE TABLE counterpart_sources(source_id TEXT REFERENCES sources(id),code TEXT NOT NULL,status TEXT CHECK(status IN ('上下文推得','待核','冲突')),data TEXT NOT NULL,PRIMARY KEY(source_id,code));
    CREATE TABLE counterparts(entry_id TEXT PRIMARY KEY REFERENCES entries(id),outcome TEXT CHECK(outcome IN ({','.join(repr(o) for o in OUTCOMES)})),rule TEXT,record_id TEXT REFERENCES counterpart_records(id),data TEXT NOT NULL);
    CREATE TABLE counterpart_findings(id TEXT PRIMARY KEY,kind TEXT NOT NULL,entry_id TEXT REFERENCES entries(id),record_id TEXT REFERENCES counterpart_records(id),data TEXT NOT NULL);
    CREATE INDEX counterpart_record_idx ON counterparts(record_id);
    CREATE INDEX counterpart_finding_entry_idx ON counterpart_findings(entry_id,kind);
    ''')
    db.executemany('INSERT INTO counterpart_records VALUES(?,?,?,?,?,?)',
                   [(r['id'], r['kind'], r['name_zh'], r['name_en'], r['source'], dumps(r)) for r in records])
    db.executemany('INSERT INTO counterpart_sources VALUES(?,?,?,?)', [(p['source_id'], p['code'], p['status'], dumps(p)) for p in alignment])
    db.executemany('INSERT INTO counterparts VALUES(?,?,?,?,?)', [(o['entry_id'], o['outcome'], o['rule'], o['record_id'], dumps(o)) for o in outcomes])
    db.executemany('INSERT INTO counterpart_findings VALUES(?,?,?,?,?)', [(f['id'], f['kind'], f['entry_id'], f['record_id'], dumps(f)) for f in findings])
    by_type = defaultdict(Counter)
    for o in outcomes:
        by_type[o['type']][o['outcome']] += 1
    report = {**snapshot, 'records': len(records), 'record_kinds': dict(sorted(Counter(r['kind'] for r in records).items())),
              'aligned_sources': len({p['source_id'] for p in alignment if p['status'] == '上下文推得'}),
              'alignment_status': dict(Counter(p['status'] for p in alignment)),
              'conflicting_sources': sorted({p['source_id'] for p in alignment if p['status'] == '冲突'}),
              'entries': len(outcomes), 'outcomes': dict(Counter(o['outcome'] for o in outcomes)),
              'outcomes_by_type': {t: dict(c) for t, c in sorted(by_type.items())},
              'resolution_rules': dict(Counter(o['rule'] for o in outcomes if o['outcome'] == 'resolved')),
              'shared_records': sum(len(ids) > 1 for ids in resolved_by.values()),
              'findings': dict(Counter(f['kind'] for f in findings)),
              'unclaimed_by_source': dict(Counter(f['detail']['source'] for f in findings if f['kind'] == 'unclaimed_record').most_common()),
              'policy': 'counterparts are candidates with their matching rule; 不全书 text and extracted values are never rewritten'}
    db.execute('INSERT INTO metadata VALUES(?,?)', ('counterpart_coverage', dumps(report)))
    records_by_id = {r['id']: r for r in records}
    rows = []
    for o in outcomes:
        r = records_by_id.get(o['record_id'])
        rows.append({**o, 'record_name': r['name_zh'] if r else '', 'record_name_en': r['name_en'] if r else '',
                     'record_source': r['source'] if r else '', 'record_locator': r['locator'] if r else ''})
    csv_write(out / 'counterparts.csv', rows, ['entry_id', 'type', 'name', 'outcome', 'rule', 'record_id', 'record_name', 'record_name_en',
                                              'record_source', 'record_locator', 'aligned_codes', 'candidate_sources', 'candidates'])
    csv_write(out / 'counterpart_findings.csv', [{**f, 'record_locator': records_by_id[f['record_id']]['locator']} for f in findings],
              ['id', 'kind', 'entry_id', 'record_id', 'record_locator', 'detail'])
    names = dict(db.execute('SELECT id,name FROM sources'))
    csv_write(out / 'counterpart_sources.csv', [{**p, 'source_name': names.get(p['source_id'], '')} for p in alignment],
              ['source_id', 'source_name', 'code', 'status', 'rules', 'evidence'])
    (out / 'counterpart_coverage.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report
