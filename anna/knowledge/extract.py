"""Pure structural extraction for rule knowledge: headers, hierarchy, tables and raw clauses.

No prose sentence regexes live here; semantic statements come from authored annotations
or explicit printed structure (headers, prerequisites, attunement, background feat lines,
subclass leveled children, and core definition roles).
"""
from __future__ import annotations

from collections import defaultdict
import re

from anna.knowledge.conditions import CLASSES, prerequisite_expression
from anna.knowledge.model import rule_value
from anna.knowledge.values import evidence, sentences
from anna.source.html import key
from anna.source.tables import table_grid

FEAT_HEADER = re.compile(
    r'^(?:(?:起源|通用|战斗风格|传奇恩惠|种族|血族)?专长)[（(]|^$'
)
FEAT_CATEGORY_LINE = re.compile(r'^(?:起源|通用|战斗风格|传奇恩惠|种族|血族)专长$')
ITEM_HEADER_LINE = re.compile(
    r'^(?:武器|护甲|奇物|戒指|魔杖|法杖|魔棒|药水|卷轴|弹药|盾牌|长剑|短剑|弯刀|匕首|巨剑|长棍|权杖|魔法船)'
    r'(?:[（(][^）)]*[）)])?[，,]\s*(?:普通|非普通|珍稀|极珍稀|传说|神器|稀有|罕见)'
    r'(?:\s*[（(]([^\n）)]*同调[^\n）)]*)[）)])?$'
)
BILINGUAL_LABEL = re.compile(r'[\u3400-\u9fff\d]+[A-Za-z][A-Za-z\d\-\'’]*[。.]')
RULE_VERB = re.compile(r'你|可以|能够|习得|始终|必须|若|如果|当|选择|恢复|造成|受到|陷入|变为|提升|增加|目标|法术')


def compact(text):
    return re.sub(r'\s+', '', text)


def class_requirement(entry, context):
    """Extract an explicit N级： heading level and verify its unique class owner."""
    text = context.pages[entry['page_path']]
    ev = evidence(entry['page_path'], text, entry['start'], entry['title_end'])
    match = re.match(r'\s*(\d+)级[：:]', ev['raw'])
    if not match:
        return None, None, ev
    level = int(match[1])
    if not 1 <= level <= 20:
        return None, None, ev
    owner = context.class_owner(entry)
    if not owner:
        return None, level, ev
    return {'kind': 'class_level', 'class': owner['name_zh'], 'owner_id': owner['id'], 'minimum': level}, level, ev


def _advance_line(text, pos, end):
    line_end = text.find('\n', pos, end)
    return min(end, line_end + 1) if line_end >= 0 else end


def _parse_prerequisite_header(entry, text, body_start):
    path, end = entry['page_path'], entry['end']
    pos = body_start
    while pos < end and text[pos].isspace():
        pos += 1
    if pos >= end:
        return {'status': 'not_stated'}, body_start, []
    line_end = text.find('\n', pos, end)
    if line_end < 0:
        line_end = end
    line = text[pos:line_end]
    match = re.search(r'(?:先决条件|先决|前提|前置条件)[：:]', line)
    if match:
        prefix = compact(line[:match.start()])
        if FEAT_HEADER.fullmatch(prefix):
            a = pos + match.start()
            begin = pos + match.end()
            b = begin
            depth = 0
            while b < line_end:
                char = text[b]
                if char == '。':
                    break
                if char in '（(':
                    depth += 1
                elif char in '）)':
                    if not depth:
                        break
                    depth -= 1
                b += 1
            suffix = text[b:line_end].strip()
            if suffix in {'', '）', ')'}:
                raw = text[begin:b].strip()
                expr = prerequisite_expression(raw) if not depth else None
                ev = evidence(path, text, a, b)
                gaps = [] if expr else [{'reason': 'unparsed_prerequisite', 'evidence': ev}]
                return {'status': 'parsed' if expr else 'unparsed', 'expression': expr, 'evidence': ev}, _advance_line(text, pos, end), gaps
    if entry['type'] == 'feat' and FEAT_CATEGORY_LINE.fullmatch(line.strip()):
        return {'status': 'not_stated'}, _advance_line(text, pos, end), []
    return {'status': 'not_stated'}, body_start, []


def _parse_item_activation(entry, text, body_start):
    if entry['type'] != 'magic_item':
        return None, body_start, []
    path, end = entry['page_path'], entry['end']
    pos = body_start
    while pos < end and text[pos].isspace():
        pos += 1
    if pos >= end:
        return None, body_start, []
    line_end = text.find('\n', pos, end)
    if line_end < 0:
        line_end = end
    line = text[pos:line_end]
    match = ITEM_HEADER_LINE.fullmatch(line.strip())
    if not match:
        return None, body_start, []
    activation = None
    gaps = []
    if match[1]:
        att = match[1].strip()
        att_match = re.search(r'[（(]' + re.escape(match[1]) + r'[）)]', line)
        att_ev = evidence(path, text, pos + att_match.start(), pos + att_match.end())
        if re.fullmatch(r'需(?:要)?同调', compact(att)):
            activation = {'expression': {'kind': 'item_state', 'state': 'attuned', 'subject': 'source_item'}, 'evidence': att_ev}
        elif not re.search(r'不需|无需', att):
            activation = {'status': 'unparsed', 'expression': None, 'evidence': att_ev}
            gaps.append({'reason': 'unparsed_activation', 'evidence': att_ev})
    return activation, _advance_line(text, pos, end), gaps


def _parse_spell_parameters(entry, text, body_start):
    if entry['type'] != 'spell':
        return {}, body_start, []
    path, end = entry['page_path'], entry['end']
    parameters = {}
    gaps = []
    last = body_start
    for match in re.finditer(r'(施法时间|施法距离|法术成分|持续时间)[：:]([^\n]+)', text[body_start:end]):
        if '。' in text[last:body_start + match.start()]:
            break
        field = {'施法时间': 'action', '施法距离': 'range', '法术成分': 'components', '持续时间': 'duration'}[match[1]]
        ev = evidence(path, text, body_start + match.start(), body_start + match.end())
        raw = compact(match[2])
        last = max(last, body_start + match.end())
        value = {'raw': match[2].strip()}
        if field == 'action':
            value.update(
                kind='bonus_action' if '附赠' in raw else 'reaction' if '反应' in raw else 'action' if '动作' in raw else 'timed',
                ritual='仪式' in raw,
            )
        elif field == 'range':
            measure = re.fullmatch(r'(\d+)(尺|里)', raw)
            value.update(kind='self' if raw == '自身' else 'touch' if raw == '触碰' else 'distance' if measure else 'unparsed')
            if measure:
                value.update(quantity=int(measure[1]), unit='foot' if measure[2] == '尺' else 'mile')
        elif field == 'components':
            value.update(required=[v for v in ('V', 'S', 'M') if v in raw])
        elif field == 'duration':
            value.update(concentration=raw.startswith('专注'), kind='instantaneous' if raw == '立即' else 'timed')
            timed = re.search(r'(\d+)(分钟|小时|轮|日)', raw)
            if timed:
                value.update(
                    quantity=int(timed[1]),
                    unit={'分钟': 'minute', '小时': 'hour', '轮': 'round', '日': 'day'}[timed[2]],
                    maximum='至多' in raw,
                )
            elif raw != '立即':
                value['kind'] = 'unparsed'
        if value.get('kind') == 'unparsed':
            gaps.append({'reason': 'unsupported_casting_field', 'evidence': ev})
        parameters[field] = {**value, 'evidence': ev}
    return parameters, last, gaps


def entry_header(entry, text, context):
    """Extract only structural headers: level prefix, explicit prerequisites, item attunement, spell header fields."""
    body_start = entry['title_end']
    prerequisites = {'status': 'not_stated'}
    heading_level = None
    gaps = []
    if entry['type'] == 'class_feature':
        cond, heading_level, heading_ev = class_requirement(entry, context)
        if heading_level is not None:
            if cond is not None:
                prerequisites = {'status': 'parsed', 'expression': cond, 'evidence': heading_ev}
            else:
                gaps.append({'reason': 'unresolved_level_owner', 'evidence': heading_ev})
    explicit_pre, body_start, pre_gaps = _parse_prerequisite_header(entry, text, body_start)
    if explicit_pre.get('evidence'):
        prerequisites = explicit_pre
    gaps.extend(pre_gaps)
    activation, body_start, act_gaps = _parse_item_activation(entry, text, body_start)
    gaps.extend(act_gaps)
    parameters, body_start, param_gaps = _parse_spell_parameters(entry, text, body_start)
    gaps.extend(param_gaps)
    return {
        'prerequisites': prerequisites,
        'activation': activation,
        'parameters': parameters,
        'heading_level': heading_level,
        'body_start': body_start,
        'gaps': gaps,
    }


def table_exclusions(entry, body_start, context):
    path, end = entry['page_path'], entry['end']
    spans = []
    tables = [t for t in context.tables if t['page_path'] == path and body_start <= t['start'] < t['end'] <= end]
    captions_by_table = {}
    for e in context.entries.values():
        if e['page_path'] == path and body_start <= e['start'] < e['end'] <= end and e.get('table_span'):
            captions_by_table[tuple(e['table_span'])] = e['start']
    for t in tables:
        a = min(t['start'], captions_by_table.get((t['start'], t['end']), t['start']))
        spans.append((a, t['end']))
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def entry_clauses(entry, text, body_start, exclusions=()):
    """Yield non-table prose sentence spans inside [body_start, entry['end']]."""
    segments = []
    cursor = body_start
    end = entry['end']
    for a, b in exclusions:
        if cursor < a:
            segments.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        segments.append((cursor, end))
    clauses = []
    for seg_start, seg_end in segments:
        for a, b, comp in sentences(text, seg_start, seg_end):
            if not re.search(r'[\u3400-\u9fff]', comp):
                continue
            if comp in {'升环施法。', '戏法强化。'}:
                continue
            raw = text[a:b]
            if (not re.search(r'[，、；：（）()]', raw)
                    and BILINGUAL_LABEL.fullmatch(comp)
                    and not RULE_VERB.search(comp)):
                continue
            clauses.append({'span': [a, b], 'raw': raw, 'compact': comp})
    return clauses


def named_table(entry, context, label):
    captions = [
        e for e in context.entries.values()
        if e['page_path'] == entry['page_path']
        and entry['title_end'] <= e['start'] < e['end'] <= entry['end']
        and e.get('table_span')
        and e['name_zh'] == label
    ]
    tables = [
        (e, t) for e in captions for t in context.tables
        if t['page_path'] == entry['page_path'] and e['table_span'] == [t['start'], t['end']]
    ]
    if len(captions) != 1 or len(tables) != 1:
        raise ValueError('missing_or_competing_grant_table')
    caption, table = tables[0]
    try:
        grid = table_grid(table)
    except ValueError as exc:
        raise ValueError('invalid_grant_table_geometry') from exc
    return caption, table, grid


def resolve_lookup(entry, context, lookup_names, target_kind, ev, reason='annotated_lookup'):
    """Resolve one or more printed lookup names cleanly through the source-aware resolver."""
    if len(lookup_names) == 1:
        return context.resolve(entry, lookup_names[0], target_kind)
    lookups = [{'name': name, 'resolution': context.resolve(entry, name, target_kind)} for name in lookup_names]
    viable = [x['resolution'] for x in lookups if x['resolution']['status'] in {'resolved', 'ambiguous'}]
    targets = sorted({tid for r in (viable or [x['resolution'] for x in lookups]) for tid in r['targets']})
    status = ('resolved' if len(targets) == 1 else 'ambiguous') if viable else ('review' if targets else 'unresolved')
    return {'targets': targets, 'status': status, 'reason': reason, 'resolution_evidence': {'lookups': lookups}}


def extract_feat_grant(entry, context):
    """Extract background `专长：...` field lines without prose heuristics."""
    if entry['type'] != 'background':
        return None
    text = context.pages[entry['page_path']]
    match = re.search(r'(?m)^[ \t]*专长[：:]([^\n]+)', text[entry['title_end']:entry['end']])
    if not match:
        return None
    label = match[1].strip()
    name = re.split(r'[（(]', label)[0].strip()
    a = entry['title_end'] + match.start()
    b = entry['title_end'] + match.end()
    qualifiers = re.findall(r'[（(]([^）)]+)[）)]', label)
    classes = [c for c in CLASSES if any(q == c for q in qualifiers)]
    unknown = [q for q in qualifiers if q not in classes and not re.fullmatch(r'见第[^）]+章', q)]
    ev = evidence(entry['page_path'], text, a, b)
    target = {'kind': 'entry', 'name': name, 'resolution': context.resolve(entry, name, 'feat'), 'evidence': ev}
    return rule_value(
        grants=[{
            'id': 'grant_1', 'entry_type': 'feat', 'mode': 'gain', 'target': target,
            'constraints': {'class_list': classes} if classes else {}, 'branches': [], 'evidence': [ev],
        }],
        unparsed=[{'reason': 'unparsed_grant_qualifier', 'evidence': ev}] if unknown else [],
    )


def extract_subclass_features(entry, context):
    """Extract structural leveled child class-feature ownership for subclasses."""
    if entry['type'] != 'subclass':
        return None
    children = [e for e in context.entries.values() if e.get('parent_id') == entry['id'] and e['type'] == 'class_feature']
    if not children:
        return None
    grants = []
    gaps = []
    for child in sorted(children, key=lambda e: e['start']):
        condition, _, ev = class_requirement(child, context)
        if condition:
            grants.append({
                'id': 'feature_' + child['id'], 'entry_type': 'class_feature', 'mode': 'gain',
                'target': {'kind': 'entry', 'name': child['name'], 'resolution': {'status': 'resolved', 'targets': [child['id']], 'reason': 'leveled_child'}, 'evidence': ev},
                'constraints': {}, 'when': condition, 'branches': [], 'evidence': [ev],
            })
        else:
            gaps.append({'reason': 'unresolved_feature_level', 'evidence': ev})
    return rule_value(grants=grants, unparsed=gaps)


def extract_definition_roles(context):
    """Content roles explain definitions; they never establish equivalence or priority."""
    groups = defaultdict(list)
    core = []
    for e in context.entries.values():
        if e['type'] not in {'rule', 'section', 'action', 'condition', 'ability', 'skill'} or not e.get('name_en'):
            continue
        if not any(context.source_by_id.get(s, {}).get('reference_role') == 'core_rules' for s in e['source_ids']):
            continue
        core.append(e)
        for sid in e['source_ids']:
            groups[(sid, key(e['name_en']))].append(e)
    for e in core:
        if '/术语汇编/' in e['page_path']:
            role = 'glossary'
        elif e['type'] == 'section' and not e.get('is_primary') and any(
            len({x['page_path'] for x in groups[(s, key(e['name_en']))] if x['type'] == 'section' and not x.get('is_primary')}) > 1
            for s in e['source_ids']
        ):
            role = 'template'
        elif e['type'] in {'rule', 'action', 'condition', 'ability', 'skill'}:
            role = 'explanation'
        else:
            continue
        yield e, {'role': role, 'scope_entry_id': e.get('parent_id'), 'unparsed': []}
