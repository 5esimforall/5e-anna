"""Read-only, evidence-bearing comparison probes for batch-one acquisition chains.

This adapter describes 5etools machine fields, not executable anna rules. In particular
innate/daily and attachedSpells/will do not imply preparation, free casting or long rests.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from anna.compare.catalog import ABILITIES, load_records
from anna.source.html import key
from anna.paths import FIVETOOLS, PROJECT, WEB
from anna.serialization import canonical, digest

SCHEMA = 1
OUTCOMES = {
    'agree': '一致（限本维度）', 'conflict': '结构化值分歧',
    'anna_missing': 'anna 尚未表达', 'external_insufficient': '外部字段不足',
    'external_unsupported': '外部结构暂不支持', 'identity_unresolved': '对应范围待核',
}
# These are report probes, not identity aliases or extraction rules. A changed/duplicate
# heading invalidates the probe instead of silently selecting another same-name section.
ELF_HEADINGS = {
    'High': '高等精灵High Elf', 'Wood': '木精灵Wood Elf',
    'Drow': '黑暗精灵Dark Elf（卓尔Drow）',
}
ITEMS = {'Wand of Magic Missiles', 'Wand of Fireballs', 'Staff of Healing'}
BOOKS = {'PHB': ('玩家手册2014', '5e'), 'XPHB': ('玩家手册2024', '5r'),
         'DMG': ('城主指南2014', '5e'), 'XDMG': ('城主指南2024', '5r')}


def level_gate(expressions):
    """Combine conjunctive thresholds only; never flatten an OR or a negation."""
    levels = []
    def collect(value):
        if not value: return True
        if 'all' in value: return all(collect(v) for v in value['all'])
        if 'any' in value or 'not' in value: return False
        if value.get('kind') in {'class_level', 'character_level'}: levels.append(value)
        return True
    if not all(collect(v) for v in expressions) or not levels: return None
    if len({(x['kind'], x.get('class'), x.get('owner_id')) for x in levels}) != 1: return None
    gate = {k: levels[0][k] for k in ('kind', 'class') if k in levels[0]}
    gate['minimum'] = max(x['minimum'] for x in levels)
    return gate


def selected_family(record):
    kind, source, name = record['kind'], record['source'], record['name_en']
    if kind == 'subrace' and source == 'PHB' and name in ELF_HEADINGS:
        return 'elf_2014'
    if kind == 'subclass' and source in {'PHB', 'XPHB'} and record['owner']['class_zh'] == '牧师':
        return 'domain_spells'
    if kind == 'feat' and source in {'PHB', 'XPHB'} and name == 'Magic Initiate':
        return 'magic_initiate'
    if kind == 'background' and source == 'XPHB' and name in {'Acolyte', 'Sage', 'Guide'}:
        return 'magic_initiate'
    if kind == 'item' and source in {'DMG', 'XDMG'} and name in ITEMS:
        return 'charged_items'
    return None


def pointer_get(doc, pointer):
    for part in pointer.strip('/').split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        doc = doc[int(part)] if isinstance(doc, list) else doc[part]
    return doc


def escape_pointer(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def external_evidence(record, suffix, value):
    return {'locator': record['locator'] + suffix, 'raw': value}


def normalize_external(record, item, spell_index):
    """Normalize only observed, bounded grammar; retain every unsupported input as a gap."""
    grants, fields, gaps = [], {}, []
    def gap(reason, suffix, raw):
        gaps.append({'reason': reason, 'evidence': external_evidence(record, suffix, raw)})
    def ev(suffix, raw):
        return external_evidence(record, suffix, raw)
    if '_copy' in item or '_mod' in item:
        for field in ('_copy', '_mod'):
            if field in item: gap('inheritance_unresolved', '/' + field, item[field])
        return {'grants': [], 'fields': {}, 'gaps': gaps, 'blocked': True}
    if '_versions' in item:
        gap('versions_not_expanded', '/_versions', item['_versions'])

    def target(raw):
        if isinstance(raw, str):
            uid, sep, marker = raw.partition('#')
            if sep and marker != 'c':
                return None
            parts = uid.split('|')
            if len(parts) > 2 or not parts[0]: return None
            # 5etools unqualified spell UIDs refer to PHB, not the owner's book.
            source = (parts[1] if len(parts) == 2 and parts[1] else 'PHB').upper()
            candidates = spell_index.get((key(parts[0]), source), [])
            return {'kind': 'fixed', 'uid': raw, 'source': source,
                    'record_ids': sorted(set(candidates)), 'marker': marker or None}
        if isinstance(raw, dict) and set(raw) <= {'choose', 'count'} and isinstance(raw.get('choose'), str):
            clauses = raw['choose'].split('|')
            pairs = [c.split('=', 1) for c in clauses]
            if any(len(p) != 2 for p in pairs): return None
            filters = dict(pairs)
            if len(filters) != len(pairs) or set(filters) != {'level', 'class'}: return None
            if not re.fullmatch(r'[0-9]', filters['level']) or not filters['class']: return None
            count = raw.get('count', 1)
            if type(count) is not int or count < 1: return None
            return {'kind': 'choice', 'levels': [int(filters['level'])],
                    'class_list': [filters['class']], 'count': count}
        return None

    def ability(raw):
        if isinstance(raw, str) and raw in ABILITIES: return [ABILITIES[raw]]
        if isinstance(raw, dict) and set(raw) == {'choose'} and isinstance(raw['choose'], list):
            if raw['choose'] and all(isinstance(x, str) and x in ABILITIES for x in raw['choose']):
                return sorted({ABILITIES[x] for x in raw['choose']})
        return None

    def members(values, suffix, context):
        if not isinstance(values, list):
            gap('unsupported_spell_members', suffix, values); return
        for i, raw in enumerate(values):
            p = suffix + '/' + str(i)
            t = target(raw)
            if t is None:
                gap('unsupported_spell_target', p, raw); continue
            grants.append({**context, 'target': t, 'evidence': [ev(p, raw)], 'locator': record['locator'] + p})

    groups = item.get('additionalSpells', [])
    if not isinstance(groups, list):
        gap('unsupported_additional_spells', '/additionalSpells', groups); groups = []
    for i, group in enumerate(groups):
        base = '/additionalSpells/' + str(i)
        if not isinstance(group, dict):
            gap('unsupported_spell_group', base, group); continue
        a = ability(group.get('ability'))
        if 'ability' in group and a is None: gap('unsupported_ability', base + '/ability', group['ability'])
        for field in sorted(set(group) - {'ENG_name', 'name', 'ability', 'known', 'prepared', 'innate', 'expanded'}):
            gap('unsupported_group_field', base + '/' + escape_pointer(field), group[field])
        for mode in ('known', 'prepared', 'innate', 'expanded'):
            if mode not in group: continue
            levels = group[mode]
            if not isinstance(levels, dict):
                gap('unsupported_level_map', base + '/' + mode, levels); continue
            for level, body in levels.items():
                p = base + '/' + mode + '/' + escape_pointer(level)
                if level != '_' and not re.fullmatch(r'[1-9][0-9]*', level):
                    gap('unsupported_level_key', p, body); continue
                gate = None if level == '_' else {'kind': 'class_level' if record['kind'] == 'subclass' else 'character_level', 'minimum': int(level)}
                if gate and gate['kind'] == 'class_level': gate['class'] = record['owner']['class_zh']
                ctx = {'group': base, 'mode': mode, 'level': gate, 'ability': a,
                       'uses': None, 'resource': None, 'recovery': None,
                       'context_evidence': [ev(base + '/ability', group['ability'])] if 'ability' in group else []}
                ctx['context_evidence'].append(ev(p, body))
                if isinstance(body, list):
                    members(body, p, ctx); continue
                if not isinstance(body, dict):
                    gap('unsupported_usage_map', p, body); continue
                for usage, value in body.items():
                    q = p + '/' + escape_pointer(usage)
                    if usage == '_': members(value, q, ctx)
                    elif usage == 'daily' and isinstance(value, dict):
                        for count, spells in value.items():
                            r = q + '/' + escape_pointer(count)
                            if not re.fullmatch(r'[1-9][0-9]*', count):
                                gap('unsupported_daily_scope', r, spells); continue
                            members(spells, r, {**ctx, 'uses': int(count), 'recovery': {'cadence': 'daily'}})
                    else: gap('unsupported_usage', q, value)

    if 'attachedSpells' in item:
        attached = item['attachedSpells']
        ctx = {'group': '/attachedSpells', 'mode': 'attached', 'level': None, 'ability': None,
               'uses': None, 'resource': None, 'recovery': None, 'context_evidence': []}
        if isinstance(attached, list): members(attached, '/attachedSpells', ctx)
        elif isinstance(attached, dict):
            for field, value in attached.items():
                p = '/attachedSpells/' + escape_pointer(field)
                if field == 'charges' and isinstance(value, dict):
                    for amount, spells in value.items():
                        q = p + '/' + escape_pointer(amount)
                        if not re.fullmatch(r'[1-9][0-9]*', amount): gap('unsupported_charge_cost', q, spells)
                        else: members(spells, q, {**ctx, 'resource': {'kind': 'charges', 'base_cost': int(amount)}})
                elif field == 'will':
                    members(value, p, ctx)
                    gap('will_does_not_verify_cost', p, value)
                else: gap('unsupported_attached_spells', p, value)
        else: gap('unsupported_attached_spells', '/attachedSpells', attached)
    for field in ('charges', 'recharge', 'rechargeAmount', 'feats'):
        if field not in item: continue
        raw = item[field]
        valid = (type(raw) is int and raw >= 0 if field == 'charges' else
                 raw in ('dawn', 'dusk', 'midnight') if field == 'recharge' else
                 bool(isinstance(raw, str) and re.fullmatch(r'(?:\{@dice )?\s*\d+d\d+(?:\s*[+-]\s*\d+)?\s*\}?', raw)) if field == 'rechargeAmount' else
                 isinstance(raw, list))
        if valid: fields[field] = {'value': raw, 'evidence': ev('/' + field, raw)}
        else: gap('unsupported_' + field, '/' + field, raw)
    missing = [f for f in ('charges', 'recharge', 'rechargeAmount') if f not in item] if record['kind'] == 'item' else []
    return {'grants': grants, 'fields': fields, 'missing_fields': missing, 'gaps': gaps, 'blocked': False}


class ReportContext:
    def __init__(self, db, source, records):
        self.db, self.source = db, Path(source)
        self.records = {r['id']: r for r in records}
        self.entries = {r['id']: r for r in (json.loads(row[0]) for row in db.execute('SELECT data FROM entries'))}
        self.matches = {r['entry_id']: r for r in (json.loads(row[0]) for row in db.execute('SELECT data FROM counterparts'))}
        self.by_record = defaultdict(list)
        for eid, match in self.matches.items():
            if match['outcome'] == 'resolved': self.by_record[match['record_id']].append(eid)
        self.knowledge = {r['subject_id']: r for r in (json.loads(row[0]) for row in db.execute("SELECT data FROM effective_knowledge WHERE kind='grant'"))}
        self.source_ids = {code: {row[0] for row in db.execute(
            "SELECT s.id FROM sources s JOIN counterpart_sources c ON c.source_id=s.id WHERE s.name=? AND c.code=? AND c.status='上下文推得'",
            (name, code))} for code, (name, _) in BOOKS.items()}
        self.pages, self.checked_files = {}, {}

    def page(self, path):
        if path not in self.pages:
            row = self.db.execute('SELECT text,sha256 FROM pages WHERE path=?', (path,)).fetchone()
            if row is None: raise ValueError('Missing source page: ' + path)
            file = (self.source / path).resolve()
            if not file.is_relative_to(self.source.resolve()): raise ValueError('Source path outside mirror')
            if sha256(file.read_bytes()).hexdigest() != row[1]: raise ValueError('Source snapshot changed: ' + path)
            self.pages[path] = row[0]
            self.checked_files[path] = row[1]
        return self.pages[path]

    def evidence(self, path, span):
        text = self.page(path)
        if (not isinstance(span, (list, tuple)) or len(span) != 2 or
                any(type(n) is not int for n in span) or not 0 <= span[0] < span[1] <= len(text)):
            raise ValueError('Invalid source evidence range')
        return {'path': path, 'span': list(span), 'raw': text[span[0]:span[1]]}

    def verify_evidence(self, value):
        if isinstance(value, dict):
            if {'path', 'span', 'raw'} <= value.keys():
                if self.evidence(value['path'], value['span'])['raw'] != value['raw']:
                    raise ValueError('Source evidence no longer matches its range')
            for v in value.values(): self.verify_evidence(v)
        elif isinstance(value, list):
            for v in value: self.verify_evidence(v)

    def locate(self, record, family):
        matched = sorted(self.by_record.get(record['id'], []))
        ids = [i for i in matched if self.source_ids[record['source']].intersection(self.entries[i].get('source_ids', []))
               and self.entries[i]['ruleset'] == BOOKS[record['source']][1]]
        excluded = [{'entry_id': i, 'reason': 'outside_core_book_probe'} for i in matched if i not in ids]
        if len(ids) == 1:
            e = self.entries[ids[0]]
            return {'status': 'counterpart', 'entry_ids': ids, 'root_id': ids[0],
                    'excluded_matches': excluded,
                    'matching_rule': self.matches[ids[0]]['rule'],
                    'evidence': [self.evidence(e['page_path'], [e['start'], e['end']])]}
        if len(ids) > 1:
            return {'status': 'ambiguous', 'entry_ids': ids, 'root_id': None,
                    'evidence': [self.evidence(self.entries[i]['page_path'], [self.entries[i]['start'], self.entries[i]['end']]) for i in ids]}
        if family == 'elf_2014':
            parents = [r for r in self.records.values() if r['kind'] == 'race' and r['source'] == 'PHB' and r['name_en'] == 'Elf']
            parent_ids = [i for p in parents for i in self.by_record.get(p['id'], [])
                          if self.source_ids['PHB'].intersection(self.entries[i].get('source_ids', []))
                          and self.entries[i]['ruleset'] == '5e']
            if len(parent_ids) == 1:
                e = self.entries[parent_ids[0]]; text = self.page(e['page_path'])
                # All three printed headings must be unique inside the matched parent.
                positions = {name: [m.start() for m in re.finditer(re.escape(heading), text[e['start']:e['end']])]
                             for name, heading in ELF_HEADINGS.items()}
                if all(len(p) == 1 for p in positions.values()):
                    starts = {n: p[0] + e['start'] for n, p in positions.items()}
                    start = starts[record['name_en']]
                    later = [p for p in starts.values() if p > start]
                    later.extend(child['start'] for child in self.entries.values() if child.get('parent_id') == e['id']
                                 and child['page_path'] == e['page_path'] and child['start'] > start)
                    end = min(later, default=e['end'])
                    return {'status': 'source_slice', 'entry_ids': [e['id']], 'root_id': e['id'],
                            'matching_rule': 'matched_parent_and_unique_printed_subrace_heading',
                            'identity_confirmed': False,
                            'evidence': [self.evidence(e['page_path'], [start, end])]}
        # Same-source named sections are evidence candidates only. No semantic equality
        # is asserted across a missing kind/owner mapping, even if the name is unique.
        aligned = self.source_ids[record['source']]
        candidates = [e for e in self.entries.values() if key(e['name_en']) == key(record['name_en'])
                      and aligned.intersection(e.get('source_ids', []))]
        evidence = []
        for e in candidates:
            parent = self.entries.get(e.get('parent_id'), e)
            evidence.append(self.evidence(parent['page_path'], [parent['start'], parent['end']]))
        return {'status': 'unresolved', 'entry_ids': sorted(e['id'] for e in candidates), 'root_id': None, 'evidence': evidence}

    def anna_grants(self, mapping, domain_feature=None):
        found, records, gaps = [], {}, []
        if not mapping['root_id']: return found, records, gaps
        probe = mapping['evidence'][0] if mapping['status'] == 'source_slice' else None
        def walk(eid, path, gates):
            if eid in [s['subject_id'] for s in path] or len(path) >= 8:
                gaps.append({'reason': 'acquisition_cycle_or_depth', 'subject_id': eid}); return
            r = self.knowledge.get(eid)
            if not r: return
            if r.get('schema') != 2:
                raise ValueError('Unsupported anna knowledge schema; run make build')
            records[r['id']] = {k: r[k] for k in ('id', 'subject_id', 'status', 'review_status', 'decision_ids')}
            records[r['id']]['unparsed'] = r['value'].get('unparsed', [])
            if r['status'] == 'rejected':
                gaps.append({'reason': 'rejected_knowledge', 'record_id': r['id']}); return
            pre = r['value'].get('prerequisites', {})
            conditions = gates + ([pre['expression']] if pre.get('expression') else [])
            for g in r['value'].get('grants', []):
                ev = g.get('evidence', [])
                if probe and not any(x['path'] == probe['path'] and probe['span'][0] <= x['span'][0] < x['span'][1] <= probe['span'][1] for x in ev):
                    continue
                steps = path + [{'subject_id': eid, 'record_id': r['id'], 'grant_id': g['id'],
                                 'parse_status': r['status'], 'review_status': r['review_status'], 'evidence': ev}]
                terms = conditions + ([g['when']] if g.get('when') else [])
                if g['entry_type'] == 'class_feature':
                    res = g['target'].get('resolution', {})
                    if r['status'] == 'resolved' and res.get('status') == 'resolved' and len(res.get('targets', [])) == 1:
                        if not path and domain_feature and self.entries[res['targets'][0]]['name_en'] != domain_feature:
                            continue
                        walk(res['targets'][0], steps, terms)
                    else: gaps.append({'reason': 'unresolved_acquisition', 'path': steps})
                    continue
                if g['entry_type'] != 'spell': continue
                target = g['target']; classes = [None]
                if target['kind'] == 'choice':
                    constraints = target.get('constraints', {})
                    c = constraints.get('class_list', {})
                    if isinstance(c, dict): classes = c.get('values') or [None]
                    elif isinstance(c, list): classes = c or [None]
                    t = {'kind': 'choice', 'levels': constraints.get('levels'), 'count': target.get('count')}
                else:
                    res = target.get('resolution', {}); ts = res.get('targets', [])
                    mapped = [self.matches.get(i, {}) for i in ts]
                    rids = [m['record_id'] for m in mapped if m.get('outcome') == 'resolved']
                    t = {'kind': 'fixed', 'entry_ids': ts, 'record_ids': sorted(set(rids)) if res.get('status') == 'resolved' and len(ts) == 1 else []}
                for cls in classes:
                    a = g.get('ability', {}); ability = None
                    if a.get('kind') == 'fixed': ability = [a['value']]
                    elif a.get('kind') == 'choose_one': ability = sorted(a['values'])
                    elif a.get('kind') == 'by_class' and cls in a['values']: ability = [a['values'][cls]]
                    gate = level_gate(terms)
                    found.append({'target': {**t, **({'class_list': [cls] if cls else None} if t['kind'] == 'choice' else {})},
                                  'mode': g['mode'], 'level': gate, 'conditions': terms, 'ability': ability,
                                  'branches': g['branches'], 'evidence': ev + ([a['evidence']] if a.get('evidence') else []),
                                  'path': steps, 'record_id': r['id'], 'grant_id': g['id'],
                                  'choice_binding': (r['id'] + ':class_list' if target['kind'] == 'choice'
                                      and target.get('constraints', {}).get('class_list') == r['value'].get('contexts', {}).get('class_list')
                                      and r['value'].get('contexts', {}).get('class_list') else None),
                                  'parse_status': r['status'], 'review_status': r['review_status']})
        walk(mapping['root_id'], [], [])
        self.verify_evidence(found); self.verify_evidence(records)
        return found, records, gaps


def target_key(target):
    if target['kind'] == 'fixed':
        ids = target['record_ids']
        return ('fixed', ids[0]) if len(ids) == 1 else None
    if target.get('levels') and target.get('class_list'):
        return ('choice', tuple(target['levels']), tuple(target['class_list']))
    return None


def compare_case(external, anna, mapping, records):
    rows = []
    def row(dimension, outcome, reason, a, b, ae=None, be=None, **scope):
        rows.append({'dimension': dimension, 'outcome': outcome, 'reason': reason,
                     'anna': {'value': a, 'evidence': ae or []},
                     'external': {'value': b, 'evidence': be or []}, **scope})
    if external['blocked']:
        row('structure', 'external_unsupported', 'inheritance_unresolved', None, external['gaps'], mapping['evidence'])
        return rows
    if mapping['status'] not in {'counterpart', 'source_slice'}:
        row('identity', 'identity_unresolved', 'no_unique_scoped_counterpart', mapping['entry_ids'], None, mapping['evidence'])
        return rows
    by_key = defaultdict(list)
    for a in anna: by_key[target_key(a['target'])].append(a)
    external_keys = Counter(target_key(b['target']) for b in external['grants'])
    used, groups = set(), defaultdict(list)
    for b in external['grants']:
        k = target_key(b['target']); matches = by_key.get(k, []) if k else []
        # A unique choice in an evidenced slice can expose a missing list constraint.
        # Multiple alternative classes never share a constraint-less anna choice.
        if not matches and b['target']['kind'] == 'choice':
            levels = b['target']['levels']
            peers = [g for g in external['grants'] if g['target']['kind'] == 'choice' and g['target']['levels'] == levels]
            if len(peers) == 1:
                matches = [a for a in anna if a['target']['kind'] == 'choice' and a['target'].get('levels') == levels and not a['target'].get('class_list')]
        be = b['evidence'] + b['context_evidence']
        scope = {'external_group': b['group'], 'external_locator': b['locator'], 'target': b['target']}
        if k is None:
            row('spell', 'identity_unresolved', 'external_spell_identity_or_filter_unresolved', None, b['target'], mapping['evidence'], be, **scope); continue
        if external_keys[k] > 1:
            row('spell', 'identity_unresolved', 'multiple_external_grants_in_scope', None, b['target'], mapping['evidence'], be, **scope); continue
        if not matches:
            row('spell', 'anna_missing', 'grant_not_represented', None, b['target'], mapping['evidence'], be, **scope); continue
        if len(matches) != 1:
            row('spell', 'identity_unresolved', 'multiple_anna_grants_in_scope', [a['target'] for a in matches], b['target'],
                [e for a in matches for e in a['evidence']], be, **scope); continue
        a = matches[0]; ae = a['evidence']; scope.update(anna_path=a['path'], anna_status=a['parse_status'], review_status=a['review_status'])
        used.add(id(a))
        if b['target']['kind'] == 'choice': groups[b['group']].append((a, b))
        complete_target = a['target']['kind'] == 'fixed' or a['target'].get('class_list') == b['target'].get('class_list')
        row('spell', 'agree' if complete_target else 'anna_missing', 'same_scoped_target' if complete_target else 'choice_domain_not_represented', a['target'], b['target'], ae, be, **scope)
        for dimension in ('level', 'ability', 'mode', 'selection'):
            av = a['target'].get('count') if dimension == 'selection' else a[dimension]
            bv = b['target'].get('count') if dimension == 'selection' else b[dimension]
            if dimension == 'selection' and b['target']['kind'] != 'choice': continue
            if dimension == 'mode' and bv in {'innate', 'attached'}:
                outcome, reason = 'external_insufficient', 'external_category_does_not_state_acquisition_mode'
            elif bv is None:
                outcome, reason = 'external_insufficient', 'field_not_stated'
            elif av is None:
                outcome, reason = 'anna_missing', 'field_not_represented'
            else:
                outcome, reason = ('agree', 'same_value') if av == bv else ('conflict', 'different_values')
            row(dimension, outcome, reason, av, bv, ae, be, **scope)
        branches = a['branches']
        uses = sorted({x['uses']['quantity']['value'] for x in branches if x.get('uses', {}).get('quantity', {}).get('kind') == 'constant'})
        row('uses', 'external_insufficient' if b['uses'] is None else 'anna_missing' if not uses else 'agree' if uses == [b['uses']] else 'conflict',
            'daily_count_only_not_recovery' if b['uses'] is not None else 'field_not_stated', uses or None, b['uses'], ae, be, **scope)
        # Cost/recovery are intentionally not unified across daily, free/slot and
        # variable-charge encodings. Report both structures rather than fabricate equality.
        resources = [x['resource'] for x in branches if x['resource']['kind'] != 'not_stated']
        recoveries = [x['recovery'] for x in branches if x.get('recovery')]
        row('resource', 'anna_missing' if b['resource'] and not resources else 'external_insufficient',
            'base_cost_does_not_state_scaling_or_cap', resources or None, b['resource'], ae, be, **scope)
        row('recovery', 'external_insufficient', 'daily_is_not_a_long_rest_event', recoveries or None, b['recovery'], ae, be, **scope)
    for group, pairs in groups.items():
        if len(pairs) < 2: continue
        bindings = {a['choice_binding'] for a, _ in pairs}
        row('choice_dependency', 'agree' if len(bindings) == 1 and None not in bindings else 'anna_missing',
            'shared_class_choice_binding', sorted(x for x in bindings if x), group,
            [e for a, _ in pairs for e in a['evidence']], [e for _, b in pairs for e in b['context_evidence']])
    for a in anna:
        k = target_key(a['target'])
        if id(a) in used or (k is not None and k in external_keys): continue
        outcome = 'identity_unresolved' if k is None else 'external_insufficient' if external['gaps'] or not external['grants'] else 'conflict'
        row('spell', outcome, 'anna_target_not_in_external_machine_set', a['target'], None, a['evidence'],
            [e for g in external['grants'] for e in g['evidence']], anna_path=a['path'], anna_status=a['parse_status'])
    for name, field in external['fields'].items():
        if name == 'feats': continue
        row(name, 'anna_missing', 'item_pool_not_represented', None, field['value'], mapping['evidence'], [field['evidence']])
    for name in external.get('missing_fields', []):
        row(name, 'external_insufficient', 'field_not_stated', None, None, mapping['evidence'])
    if 'feats' in external['fields']:
        field = external['fields']['feats']
        # Keep variant UIDs opaque: `_versions` and `_mod` are not identity edges.
        row('feat_dependency', 'external_unsupported', 'variant_uid_not_expanded',
            list(records.values()), field['value'], mapping['evidence'], [field['evidence']])
    for gap in external['gaps']:
        row('structure', 'external_unsupported', gap['reason'], None, gap['evidence']['raw'], mapping['evidence'], [gap['evidence']])
    if not rows:
        row('coverage', 'external_insufficient', 'no_machine_spells_does_not_prove_no_grant', None, None, mapping['evidence'])
    return rows


def background_comparisons(ctx, case, variants, spell_index):
    """Compare declared variant selectors without applying `_mod` or creating identities."""
    field = case['external']['fields'].get('feats')
    if not field or case['mapping']['status'] != 'counterpart': return []
    bg = ctx.knowledge.get(case['mapping']['root_id'])
    if not bg or bg['status'] == 'rejected': return []
    rows = []
    for group in field['value']:
        if not isinstance(group, dict) or len(group) != 1: continue
        uid, enabled = next(iter(group.items()))
        if enabled is not True or len(uid.split('|')) != 2: continue
        name, source = uid.split('|')
        candidates = variants.get((key(name), source.upper()), [])
        if len(candidates) != 1: continue
        parent, index, variant = candidates[0]
        if '_copy' in variant: continue
        pm = ctx.locate(parent, 'magic_initiate')
        if pm['status'] != 'counterpart': continue
        grants = [g for g in bg['value']['grants'] if g['entry_type'] == 'feat' and
                  g['target'].get('resolution', {}).get('status') == 'resolved' and
                  g['target']['resolution'].get('targets') == [pm['root_id']]]
        if len(grants) != 1: continue
        g = grants[0]
        # Only directly declared machine fields are inspected; prose modifications remain opaque.
        direct = normalize_external(parent, {'additionalSpells': variant.get('additionalSpells')}, spell_index)
        choices = [x['target'].get('class_list') for x in direct['grants']]
        if direct['gaps'] or not choices or any(not x or x != choices[0] for x in choices): continue
        expected = choices[0]
        be = [field['evidence'], external_evidence(parent, f'/_versions/{index}', variant)]
        observed = g.get('constraints', {}).get('class_list')
        rows.append({'dimension': 'variant_class_selector',
                     'outcome': 'anna_missing' if not observed else 'agree' if observed == expected else 'conflict',
                     'reason': 'declared_variant_selector_only',
                     'anna': {'value': observed, 'evidence': g['evidence']},
                     'external': {'value': expected, 'evidence': be},
                     'anna_status': bg['status'], 'review_status': bg['review_status']})
        options = [json.loads(row[0]) for row in ctx.db.execute('SELECT data FROM casting_options WHERE root_id=? ORDER BY id', (bg['subject_id'],))]
        # Verify each projected option separately: no union that could hide an unconstrained path.
        paths = [{'option_id': o['id'], 'class_list': o['constraints'].get('class_list'),
                  'acquisition_steps': o['acquisition_steps']} for o in options]
        matches = bool(paths) and all(p['class_list'] == expected and p['acquisition_steps'] and
                                      p['acquisition_steps'][0]['subject_id'] == bg['subject_id'] and
                                      p['acquisition_steps'][0]['target_id'] == pm['root_id'] for p in paths)
        ctx.verify_evidence(paths)
        rows.append({'dimension': 'projected_class_selector', 'outcome': 'agree' if matches else 'conflict' if paths else 'anna_missing',
                     'reason': 'each_projected_path_retains_selector',
                     'anna': {'value': paths, 'evidence': g['evidence']},
                     'external': {'value': expected, 'evidence': be},
                     'anna_status': bg['status'], 'review_status': bg['review_status']})
    return rows


def build_report(db, external_root, source):
    """Use an already built catalog; never rebuild, update reviews or mutate database state."""
    metadata = dict(db.execute('SELECT key,value FROM metadata'))
    if 'counterpart_coverage' not in metadata: raise ValueError('Catalog has no counterpart snapshot; run make build')
    published = json.loads(metadata['counterpart_coverage'])
    records, snapshot = load_records(external_root)
    if snapshot['input_sha256'] != published.get('input_sha256'):
        raise ValueError('5etools-cn snapshot differs from catalog; run make build')
    ctx = ReportContext(db, source, records)
    spell_index = defaultdict(list)
    for r in records:
        if r['kind'] == 'spell':
            for name in {r['name_zh'], r['name_en']}:
                if name: spell_index[(key(name), r['source'].upper())].append(r['id'])
    documents, file_hashes, cases, variants = {}, {}, [], defaultdict(list)
    for r in records:
        family = selected_family(r)
        if not family: continue
        file, pointer = r['locator'].split('#', 1)
        if file not in documents:
            raw = (Path(external_root) / file).read_bytes()
            documents[file] = json.loads(raw); file_hashes[file] = sha256(raw).hexdigest()
        item = pointer_get(documents[file], pointer)
        if r['kind'] == 'feat':
            for index, variant in enumerate(item.get('_versions', [])):
                if isinstance(variant, dict) and variant.get('name') and variant.get('source'):
                    variants[(key(variant['name']), variant['source'].upper())].append((r, index, variant))
        # Subraces named High or Wood belonging to another race are outside the probe.
        if family == 'elf_2014' and (item.get('raceName'), item.get('raceSource')) != ('精灵', 'PHB'): continue
        external = normalize_external(r, item, spell_index)
        mapping = ctx.locate(r, family)
        domain_feature = r['name_en'] + ' Spells' if family == 'domain_spells' and r['source'] == 'XPHB' else None
        anna, kr, gaps = ctx.anna_grants(mapping, domain_feature)
        if domain_feature: mapping['semantic_scope'] = {'feature_name_en': domain_feature, 'rule': 'domain_spell_feature_only'}
        # Preserve background acquisition evidence without traversing variant dependencies.
        if r['kind'] == 'background' and mapping['root_id'] in ctx.knowledge:
            bg = ctx.knowledge[mapping['root_id']]
            kr[bg['id']]['grants'] = bg['value']['grants']
            ctx.verify_evidence(kr)
        comparisons = compare_case(external, anna, mapping, kr)
        cases.append({'id': 'semantic_' + digest(r['locator'])[:24], 'family': family, 'external_record': r,
                      'mapping': mapping, 'anna': {'grants': anna, 'records': list(kr.values()), 'gaps': gaps},
                      'external': {**external, 'text_evidence': external_evidence(r, '/entries', item['entries']) if 'entries' in item else None},
                      'comparisons': comparisons})
    for case in cases:
        if case['external_record']['kind'] == 'background':
            case['comparisons'].extend(background_comparisons(ctx, case, variants, spell_index))
    # Reject a mirror changing during a report, including uncommitted edits.
    _, final = load_records(external_root)
    if final['input_sha256'] != snapshot['input_sha256']:
        raise ValueError('5etools-cn changed during comparison')
    for file, expected in file_hashes.items():
        if sha256((Path(external_root) / file).read_bytes()).hexdigest() != expected:
            raise ValueError('5etools-cn evidence changed during comparison')
    for file, expected in ctx.checked_files.items():
        if sha256((Path(source) / file).read_bytes()).hexdigest() != expected:
            raise ValueError('Source evidence changed during comparison')
    summary = json.loads(metadata['summary'])
    counts = Counter(row['outcome'] for case in cases for row in case['comparisons'])
    return {'schema': SCHEMA, 'scope': 'batch1_acquisition_probes',
            'adapter_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
            'policy': {'writes_effective_rules': False, 'approves_reviews': False,
                       'agreement_is_dimension_local': True, 'shared_translation_is_not_independent_evidence': True,
                       'source_slice_is_identity': False, 'unqualified_spell_uid_source': 'PHB'},
            'snapshots': {'anna': {k: summary.get(k) for k in ('snapshot_commit', 'input_manifest_sha256', 'extraction_sha256', 'pipeline_sha256')},
                          'review': json.loads(metadata.get('review_snapshot', '{}')),
                          'external': snapshot, 'catalog_external': published,
                          'external_evidence_files': file_hashes, 'source_evidence_files': ctx.checked_files},
            'summary': {'cases': len(cases), 'families': dict(Counter(c['family'] for c in cases)),
                        'mappings': dict(Counter(c['mapping']['status'] for c in cases)), 'outcomes': dict(counts)},
            'cases': cases}


def render_markdown(report):
    lines = ['# 第一批规则链：5etools-cn 语义对照', '',
             '本报告只提供核对线索，不修改有效规则或批准审核。一致仅指某个维度；部分解析的局部值不等于可用规则链。',
             '双方中文可能共享不全书译文，不计作独立原文证据。`source_slice` 是匹配父条目下的报告取证范围，不确认亚种身份。', '',
             f"- 不全书快照：`{report['snapshots']['anna']['snapshot_commit']}`",
             f"- 5etools-cn 快照：`{report['snapshots']['external']['commit']}`",
             f"- 外部输入指纹：`{report['snapshots']['external']['input_sha256']}`",
             f"- 样例：{report['summary']['cases']}；逐维度计数：" + '；'.join(f'{OUTCOMES[k]} {v}' for k,v in report['summary']['outcomes'].items()), '',
             '完整值、原文切片、JSON Pointer、获取路径与审核状态保存在同名 JSON。下列范围均为页面提取文本 Unicode 码点，右端不含；并非 HTML 字节偏移。', '']
    def cell(value):
        text = canonical(value) if value is not None else '—'
        return text.replace('|', '&#124;').replace('\n', ' ').replace('`', '&#96;')
    for case in report['cases']:
        r = case['external_record']; m = case['mapping']
        lines.extend([f"## {r['name_zh']} / {r['name_en']} · {r['source']} · {r['owner'].get('class_source') or r['kind']}", '',
                      f"- 外部：`{r['locator']}`；对应：`{m['status']}`；条目：" + ', '.join(f'`{i}`' for i in m['entry_ids'])])
        for e in m['evidence']:
            lines.append(f"- 不全书：`{e['path']}` [{e['span'][0]}, {e['span'][1]})")
        for k in case['anna']['records']:
            lines.append(f"- anna 记录：`{k['id']}`；解析 `{k['status']}`；审核 `{k['review_status']}`")
        if m.get('semantic_scope'):
            lines.append('- 比较范围：领域法术能力；其他能力的同名施法不并入本表。')
        if m['status'] not in {'counterpart', 'source_slice'} and case['external']['grants']:
            lines += ['', '外部机器字段预期（身份待核，尚未与 anna 断言相等）：', '']
            for g in case['external']['grants']:
                lines.append(f"- `{g['locator']}`：{cell(g['target'])}；等级 {cell(g['level'])}；类别 `{g['mode']}`")
        lines += ['', '| 维度／外部目标 | 结果 | anna | 5etools-cn | 原因 |', '|---|---|---|---|---|']
        for row in case['comparisons']:
            a, b = row['anna']['value'], row['external']['value']
            if row['dimension'] in {'structure', 'feat_dependency', 'projected_class_selector'}:
                a = '见 JSON' if a is not None else None; b = '见 JSON'
            label = row['dimension'] + (' / ' + row['external_locator'].split('#')[-1] if row.get('external_locator') else '')
            lines.append(f"| {cell(label)} | {OUTCOMES[row['outcome']]} | {cell(a)} | {cell(b)} | `{row['reason']}` |")
        lines.append('')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=PROJECT/'data/extracted/catalog.sqlite')
    parser.add_argument('--5etools', dest='external', type=Path, default=FIVETOOLS)
    parser.add_argument('--source', type=Path, default=WEB)
    parser.add_argument('--output', type=Path, default=PROJECT/'data/extracted/counterpart_semantics', help='Output stem for .json and .md')
    args = parser.parse_args()
    try:
        for output in (args.output.with_suffix('.json'), args.output.with_suffix('.md')):
            if output.resolve() == args.db.resolve() or any(output.resolve().is_relative_to(root.resolve()) for root in (args.source, args.external)):
                raise ValueError('Report output must not overwrite an input mirror or database')
        with closing(sqlite3.connect(args.db.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            report = build_report(db, args.external, args.source)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        args.output.with_suffix('.md').write_text(render_markdown(report))
        print(json.dumps(report['summary'], ensure_ascii=False))
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
