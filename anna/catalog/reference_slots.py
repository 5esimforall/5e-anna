"""Typed reference slots. A field names the family; the source definitions supply its members."""
from __future__ import annotations

from collections import defaultdict
import re

from anna.catalog.signatures import LABEL_RE, LABEL_TO_FIELD
from anna.source.html import key, split_name

# These are headings of definition collections, never names of individual members.
FAMILY_HEADINGS = {
    ('法术成分', 'components'): 'components',
    ('持续时间', 'duration'): 'duration',
    ('词条', 'properties'): 'properties',
    ('武器词条', 'weaponproperties'): 'properties',
    ('精通词条', 'masteryproperties'): 'mastery',
    ('魔法学派', 'schoolsofmagic'): 'school',
    ('伤害类型', 'damagetypes'): 'damage',
}


def heading_family(name):
    zh, en = split_name(name)
    if re.search(r'弹药(?:种类|类型)?$',zh) and re.search(r'\bammunition(?: types?)?$',en,re.I):
        return 'ammunition'
    return FAMILY_HEADINGS.get((re.sub(r'^边栏[：:]\s*','',zh), key(en)))


SYMBOL = re.compile(r'[（(]([A-Z])[）)]$')
NAME = r'[\u3400-\u9fff]+|[A-Za-z][A-Za-z\-]*(?: [A-Za-z][A-Za-z\-]*)*'
LIST_SEPARATOR = re.compile(r'\s*[、，,；;/／]\s*')
MEMBER_NAME = re.compile(NAME)
QUALIFIED_NAME = re.compile(r'(?:' + NAME + r')\d*')
DAMAGE_AMOUNT = re.compile(r'\s*\d+(?:[dD]\d+)?(?:\s*[+＋-]\s*\d+)?\s*(?:点\s*)?')
TIME_UNIT = r'(?:分钟|小时|星期|轮|日|天|周|月|年)'
TIME_AMOUNT = re.compile(r'(?:[0-9]+|[一二两三四五六七八九十百千]+)\s*(?:个\s*)?' + TIME_UNIT)
TIME_BOUND = re.compile(r'\s*(?:(?:至多|最多|最长)\s*)?')
MEASUREMENT = re.compile(r'(?:单位(?:是|为)[^。；\n]{1,48}|可(?:以)?用[^。；\n]{1,48}?度量)')


def measurement_rule(text):
    """A duration explanation must actually describe measurement in time units."""
    for statement in MEASUREMENT.finditer(text):
        if len(set(re.findall(TIME_UNIT, statement[0]))) >= 2:
            return statement[0]
    return None


def list_tokens(text, start, pattern):
    spans = []
    i = start
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        member = pattern.match(text, i)
        if not member:
            return []
        spans.append(member.span())
        i = member.end()
        while i < len(text) and text[i].isspace():
            i += 1
        qualified = i < len(text) and text[i] in '(（'
        if qualified:
            # Full- and half-width parentheses delimit the same printed qualifier.
            depth = 0
            while i < len(text):
                c = text[i]
                i += 1
                depth += int(c in '(（') - int(c in ')）')
                if not depth:
                    break
            if depth:
                return []
        if text[i:].strip() in ('', '。', '.'):
            break
        sep = LIST_SEPARATOR.match(text, i)
        if sep and sep.end() < len(text):
            i = sep.end()
        elif not (qualified and pattern.match(text, i)):
            return []
    return spans


def duration_tokens(text):
    """Named modes and measured bounds may coexist, including alternative durations."""
    spans = []
    i = 0
    while i < len(text):
        i = TIME_BOUND.match(text, i).end()
        amount = TIME_AMOUNT.match(text, i)
        if amount:
            span = amount.span()
        else:
            name = re.match(r'(' + NAME + r')(?=\s*(?:[，,；;（(。.]|$))', text[i:])
            alternative = re.match(r'([\u3400-\u9fff]+?)或(?=\s*(?:[0-9一二两三四五六七八九十百千]|至多|最多|最长))',text[i:])
            if alternative:
                name = alternative
            if not name:
                break
            span = (i, i + name.end(1))
        spans.append(span)
        separator = re.match(r'\s*(?:[，,；;]|或)', text[span[1]:])
        if not separator:
            break
        i = span[1] + separator.end()
    return spans


def value_tokens(field, text):
    """Offsets in a printed field value, never free-text matches in material or range descriptions."""
    if field == 'damage':
        amount = DAMAGE_AMOUNT.match(text)
        return list_tokens(text, amount.end(), MEMBER_NAME) if amount else []
    if field == 'duration':
        return duration_tokens(text)
    return list_tokens(text, 0, QUALIFIED_NAME if field == 'properties' else MEMBER_NAME)


CREATURE_FIELDS = {'skills', 'condition_immunities', 'resistances', 'immunities'}
CREATURE_FIELD_LABEL_RE = re.compile(
    r'\s*(?:技能(?:熟练项?|加值)?|状态免疫|伤害抗性|伤害免疫|抗性|免疫)(?:\s*[A-Za-z][A-Za-z ]{0,35})?\s*[:：]?\s*'
)
CREATURE_SKILL_RE = re.compile(
    r'(?<![^\s，、；;,：:。或和与及\d])((?![或和与及])[\u3400-\u9fff]{2,4})(?:\s*[A-Za-z][A-Za-z ]{1,20})?\s*[+＋\-−]\s*\d+'
)
CREATURE_FIELD_TARGETS = {
    'skills': ('skills', 'skill'),
    'condition_immunities': ('condition_immunities', 'condition'),
    'resistances': ('damage', 'rule'),
    'immunities': ('damage', 'rule'),
}


class SlotVocabulary:
    """Family-local spellings with source-backed target identities, kept out of global aliases."""
    def __init__(self, resolver, pages):
        self.members = defaultdict(lambda: defaultdict(list))
        self.collections = defaultdict(dict)
        self.legends = defaultdict(list)
        self.parameter_roles = {}
        self.measured = defaultdict(list)
        self.parameterized = defaultdict(lambda: defaultdict(list))
        self.family_names = defaultdict(set)
        for entry in resolver.entries.values():
            etype = entry.get('type')
            if etype == 'skill':
                for n in (entry.get('name_zh', ''), entry.get('name_en', '')):
                    if n:
                        self.members['skills', key(n)][entry['id']].append({
                            'member_id': entry['id'], 'member_name': entry['name'], 'heading': '技能', 'via': 'member',
                        })
                if entry.get('name_zh'):
                    self.family_names['skills'].add(entry['name_zh'])
            elif etype == 'condition':
                for n in (entry.get('name_zh', ''), entry.get('name_en', '')):
                    if n:
                        self.members['condition_immunities', key(n)][entry['id']].append({
                            'member_id': entry['id'], 'member_name': entry['name'], 'heading': '状态', 'via': 'member',
                        })
                if entry.get('name_zh'):
                    self.family_names['condition_immunities'].add(entry['name_zh'])
            if collection := entry.get('slot_collection'):
                self.collections[collection][entry['id']] = [{'member_id':entry['id'],'member_name':entry['name'],
                                                              'heading':entry['name'],'via':'collection'}]
            definition = entry.get('slot_definition')
            if not definition:
                continue
            family = definition['family']
            if family == 'legend':
                self.legends[entry['page_path'],definition['column']].append(entry)
                continue
            if entry.get('name_zh'):
                self.family_names[family].add(entry['name_zh'])
            names = {entry['name_zh'], entry.get('name_en','')}
            if family == 'components' and (m := SYMBOL.search(entry['name'])):
                names.add(m[1])
            ids = {entry['id']}
            body = pages[entry['page_path']]['text'][entry['title_end']:entry['end']]
            if family=='properties':
                declared = re.findall(r'(?:^|[。；\n])\s*(?:其)?(?:所需的?)?([\u3400-\u9fff]+?)(?:的种类|类型)'
                                      r'(?:会|应|须)?(?:与武器的射程(?:写|列)在一起|在(?:该)?武器的射程旁(?:边)?注明)',body)
                if set(declared)=={'弹药'}:
                    self.parameter_roles[entry['id']] = 'ammunition'
            # Some families print a named parameter form, e.g. 标识为“名称X”. A number alone
            # is never reason to throw away part of the printed name.
            parameter = (re.search(r'(?:标识为|记作|写作)[“「](' + re.escape(entry['name_zh']) + r'X)[”」]',body)
                         if family=='properties' and entry['name_zh'] else None)
            # A local duration synopsis may direct readers to the glossary for the actual rule.
            if family == 'duration':
                if re.search(r'(?:详见|参见)术语汇编', body):
                    glossary = [resolver.entries[i] for i in resolver.names.get(key(entry['name_zh']), ())]
                    targets = {g['id'] for g in glossary if '/术语汇编/' in g['page_path']
                               and g['type']=='rule' and g['ruleset']==entry['ruleset']
                               and set(g['source_ids']) == set(entry['source_ids'])
                               and g['name_zh']==entry['name_zh'] and key(g['name_en'])==key(entry['name_en'])}
                    if targets:
                        ids = targets
            if rule := definition.get('measure'):
                self.measured[entry['id']].append({'member_id':entry['id'], 'member_name':entry['name'],
                    'heading':definition['heading'], 'via':'member', 'measure':rule})
            for spelling in {key(n) for n in names if n}:
                for target in ids:
                    declaration = {'member_id':entry['id'], 'member_name':entry['name'],
                                   'heading':definition['heading'], 'via':'member' if target==entry['id'] else 'glossary_referral'}
                    self.members[family, spelling][target].append(declaration)
                    if parameter and spelling == key(entry['name_zh']):
                        self.parameterized[family, spelling][target].append({**declaration, 'notation':parameter[1]})

    def names_for(self, family):
        return sorted(self.family_names.get(family, ()), key=lambda n: (-len(n), n))

    def candidates(self, field, name):
        if field == 'duration' and TIME_AMOUNT.fullmatch(name):
            return self.measured
        exact = self.members.get((field, key(name)), {})
        qualified = re.fullmatch(r'(.+?)([0-9]+)',name)
        if not qualified:
            return exact
        matches = {i:list(ds) for i,ds in exact.items()}
        for i,ds in self.parameterized.get((field,key(qualified[1])), {}).items():
            matches.setdefault(i,[]).extend(ds)
        return matches


def field_occurrences(facts, entries, text, cells, vocabulary=None):
    """Bind only printed field evidence. Weapon cells must agree with the column and equipment row."""
    for fact in facts:
        field = fact['field']
        owner = entries[fact['entry_id']]
        a,b = fact['evidence']['span']
        evidence = {'field':field, 'field_span':[a,b], 'field_text':text[a:b]}
        if not owner['title_end'] <= a < b <= owner['end']:
            continue
        if owner['type']=='spell' and field in {'components','duration'}:
            label = LABEL_RE.match(text,a,b)
            if not label or LABEL_TO_FIELD[label[1]] != field:
                continue
            yield owner, *label.span(1), 'field', {**evidence,'slot_role':'collection'}
            start = label.end()
            signal = 'field'
        elif owner['type']=='equipment' and field in {'damage','properties','mastery'}:
            cell = cells.get((a,b))
            if not cell or cell[0]!=field:
                continue
            evidence['table'] = cell[1]
            start = a
            signal = 'table'
        elif owner['type']=='creature' and field in CREATURE_FIELDS:
            label = CREATURE_FIELD_LABEL_RE.match(text,a,b)
            start = label.end() if label else a
            body = text[start:b]
            if field == 'skills':
                masked = re.sub(r'\([^()\n]{0,40}\)|（[^（）\n]{0,40}）', lambda m: ' ' * len(m[0]), body)
                for m in CREATURE_SKILL_RE.finditer(masked):
                    yield owner, start+m.start(1), start+m.end(1), 'field', evidence
            elif field == 'condition_immunities':
                tokens = value_tokens(field, body)
                if tokens:
                    for lo,hi in tokens:
                        yield owner, start+lo, start+hi, 'field', evidence
                elif vocabulary and (names := vocabulary.names_for('condition_immunities')):
                    pat = re.compile('|'.join(map(re.escape, names)))
                    for m in pat.finditer(body):
                        yield owner, start+m.start(), start+m.end(), 'field', evidence
            elif vocabulary and (names := vocabulary.names_for('damage')):
                pat = re.compile('|'.join(map(re.escape, names)))
                for m in pat.finditer(body):
                    yield owner, start+m.start(), start+m.end(), 'field', evidence
            continue
        else:
            continue
        for lo,hi in value_tokens(field,text[start:b]):
            yield owner, start+lo, start+hi, signal, evidence


def resolve_field_slots(occurrences, resolver, vocabulary, path, text):
    """One reading per printed occurrence, independent of fact insertion order."""
    groups = defaultdict(list)
    for owner,a,b,signal,evidence in occurrences:
        groups[a,b].append((owner,signal,evidence))
    slots = {}
    for (a,b), readings in sorted(groups.items()):
        owner,signal,ev = readings[0]
        owners = sorted({e['id'] for e,_,_ in readings})
        scope = {'source_ids':owner['source_ids'], 'ruleset':owner['ruleset'], 'owner_ids':owners}
        meanings = {(s, f['field'], f.get('slot_role','value'), tuple(f['field_span']), tuple(sorted(e['source_ids'])), e['ruleset'])
                    for e,s,f in readings}
        if len(meanings)!=1:
            # No competing owner, field or generation wins merely because it was loaded first.
            signals = {m[0] for m in meanings}
            slots[a,b] = (signal if len(signals)==1 else 'field', {'owner_count':len(owners), 'competing_fields':sorted({m[1] for m in meanings})},
                          {'status':'review','reason':'slot_conflict','targets':[]})
            continue
        label = text[a:b]
        vocab_family, target_kind = CREATURE_FIELD_TARGETS.get(ev['field'], (ev['field'], 'rule'))
        members = (vocabulary.collections[ev['field']] if ev.get('slot_role')=='collection'
                   else vocabulary.candidates(vocab_family,label))
        result, proof = resolve_members(resolver,path,a,label,scope,members,kind=target_kind)
        slots[a,b] = signal,{**ev,**proof},result
        if (ev['field']=='properties' and result['status']=='resolved'
                and (role := vocabulary.parameter_roles.get(result['targets'][0]))):
            # Only a parameter expressly declared by the resolved property may contain another reference.
            suffix = re.match(r'\s*[（(]\s*射程\s*\d+\s*/\s*\d+\s*[；;]\s*('+NAME+r')\s*[）)]',text[b:ev['field_span'][1]])
            if suffix:
                lo,hi = b+suffix.start(1), b+suffix.end(1)
                sub, proof = resolve_members(resolver,path,lo,suffix[1],scope,vocabulary.candidates(role,suffix[1]),'equipment')
                slots[lo,hi] = signal,{**ev,**proof,'slot_role':'parameter','family':role,'property':label},sub
    return slots


def resolve_members(resolver,path,position,label,scope,members,kind='rule'):
    """Resolve a structurally restricted set using the same source/edition policy as other slots."""
    if (not scope or len(scope['source_ids'])!=1 or scope['ruleset'] not in {'5e','5r'}
            or resolver.sources.get(scope['source_ids'][0],{}).get('ruleset_hint')!=scope['ruleset']):
        result = {'status':'review','reason':'slot_scope_unconfirmed','targets':sorted(members)}
    else:
        result = resolver.resolve(label,'concept',path,position,scope=scope,types={kind},member_ids=members)
    declarations = [d for i in result['targets'] for d in members.get(i,())]
    return result, {'scope':scope or {},'declarations':declarations}
