"""Unified page-entry discovery and container frames (headings, leads, lists, and tables)."""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import re

from bs4 import Tag

from anna.catalog.signatures import (
    CLASS_PATH,
    FIELD_TITLE,
    QUICKREF_ATTR_KINDS,
    STANDALONE_CREATURE_META_RE,
    STANDALONE_ITEM_META_RE,
    STANDALONE_POISON_RE,
    STANDALONE_SPELL_CONFIRM_RE,
    STANDALONE_SPELL_META_RE,
    STANDALONE_STATS_RE,
    class_structure,
    clean_entry_name,
    extract_entry_fields,
    matches_feat_descriptor,
    skill_notation,
)
from anna.catalog.reference_slots import heading_family, measurement_rule
from anna.catalog.types import RULE_TYPES, classify, mark_class_regions, refine_page_types
from anna.source.html import Document, compact, digest, key, split_name

STRUCTURAL_HEADING_METHODS = {'heading', 'word_heading', 'colored_heading'}

# Catalog and definition table vocabularies.
TABLE_HEADERS = {
    '价格': 'price', '费用': 'price', '市场价格': 'price', '重量': 'weight',
    '载重': 'carrying_capacity', '速度': 'speed', '伤害': 'damage',
    '属性': 'properties', '词条': 'properties', '精通': 'mastery',
    '护甲等级（AC）': 'armor_class', '护甲等级（ AC ）': 'armor_class', '护甲等级': 'armor_class',
    '力量': 'strength_requirement', '隐匿': 'stealth', '特殊': 'special',
    '份量': 'quantity', '数量': 'quantity', '容器': 'container', '存放容器': 'container',
    '船员': 'crew', '乘客': 'passengers', '货物（吨）': 'cargo_tons',
    'AC': 'ac', 'HP': 'hp', '伤害阈值': 'damage_threshold',
}
TABLE_NAME_HEADERS = {
    '物品', '项目', '名称', '商品', '船只类型', '护甲', '护甲名称', '法器', '徽章',
    '种类', '类型', '大小', '水晶大小', '死亡象征', '货物',
}
VEHICLE_NAMES_RE = re.compile(
    r'Carriage|Wagon|Cart|Chariot|Sled\b|Galley|Keelboat|Longship|Rowboat|Sailing Ship|Warship|Airship',
    re.I,
)
DEFINITION_TABLE_KINDS = {'技能': 'skill', '属性': 'ability'}
DEFINITION_TABLE_FIELDS = {'skill': {'属性': 'ability'}}
BASIC_FACILITIES_CAPTION_RE = re.compile(r'基础设施\s*Basic Facilities', re.I)
CORE_SPELL_CHAPTER_RE = re.compile(r'/(?:魔法|法术)/')


@dataclass
class Candidate:
    start: int
    title_end: int
    name: str
    rank: int
    method: str
    tag: Tag | None = None
    anchors: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    explicit_end: int | None = None
    end: int = 0
    parent: int | None = None
    local_legacy: bool = False
    fields: dict = field(default_factory=dict)


def entry_draft(
    name: str,
    start: int,
    title_end: int,
    end: int,
    body_text: str,
    *,
    kind: str,
    classification_rule: str,
    boundary_rule: str,
    level: int,
    is_primary: bool = False,
    parent_index: int | None = None,
    anchors: list[str] | None = None,
    printed_types: list[list[str]] | None = None,
    legacy_direct: bool = False,
    fields: dict | None = None,
    extra_facts: list | None = None,
    observation_ids: list[str] | None = None,
    table_span: list[int] | None = None,
) -> dict:
    """Construct the uniform page-entry record emitted by all heading, lead, list, and table frames."""
    draft = {
        'name': name,
        'start': start,
        'title_end': title_end,
        'end': end,
        'anchors': anchors if anchors is not None else [],
        'parent_index': parent_index,
        'type': kind,
        'classification_rule': classification_rule,
        'boundary_rule': boundary_rule,
        'legacy_direct': legacy_direct,
        'fields': fields if fields is not None else {},
        'observation_ids': observation_ids if observation_ids is not None else [],
        'body_hash': sha256(compact(body_text).encode()).hexdigest(),
        'level': level,
        'is_primary': is_primary,
    }
    if printed_types is not None:
        draft['printed_types'] = printed_types
    if extra_facts is not None:
        draft['extra_facts'] = extra_facts
    if table_span is not None:
        draft['table_span'] = table_span
    return draft


def quickref_rows(doc: Document) -> list[dict]:
    """Extract D&D index table rows (item, monster, spell) from quick-reference pages."""
    rows = []
    for row in doc.tagged_rows(tuple(QUICKREF_ATTR_KINDS)):
        attr = row.pop('attr')
        row['kind'] = QUICKREF_ATTR_KINDS[attr]
        rows.append(row)
    return rows


def skill_leads(doc: Document, headings=()):
    """Leads under an ability-check heading that apply their own name in that check's notation."""
    headings = sorted(headings, key=lambda h: h.start)
    leads = doc.line_leads()
    bounds = sorted({h.start for h in headings} | doc.standalone_bilingual_lines())
    for i, (a, b, name) in enumerate(leads):
        zh, en = split_name(name)
        if not (zh and en and len(name) <= 60) or re.search(r'[。！？；，、]', name):
            continue
        owner = next((h for h in reversed(headings) if h.start < a), None)
        if owner is None:
            continue
        check_zh, check_en = split_name(owner.name)
        if not (len(check_zh) > 2 and check_zh.endswith('检定') and re.search(r'\bChecks?$', check_en, re.I)):
            continue
        end = leads[i + 1][0] if i + 1 < len(leads) else len(doc.text)
        end = min([end] + [s for s in bounds if s > a])
        if skill_notation(zh, [check_zh[:-2]]).search(doc.text, b, end):
            yield a, b, end, name, owner


def rule_children(doc: Document, start: int, end: int, parent_name: str):
    """Find independent rule subdefinitions (gloss series or parallel family leads) inside a rule."""
    parent_zh, parent_en = split_name(parent_name)

    def in_family(name: str) -> bool:
        zh, en = split_name(name)
        return any(p and key(p) != key(n) and key(p) in key(n) for p, n in ((parent_zh, zh), (parent_en, en)))

    for series in doc.underlined_gloss_series(start, end):
        yield from ((*item, 'rule_term_gloss_list') for item in series)

    for group in doc.parallel_bold_lines(start, end):
        yield from ((*item, 'rule_parallel_lead') for item in group if in_family(item[3]))


def collect_slot_definitions(doc, cs, entries, core_rule_source):
    """Use the named collection around a definition, not a global list of member names."""
    by_start = {e['start']:e for e in entries}
    for i, container in enumerate(cs):
        if container.method not in STRUCTURAL_HEADING_METHODS:
            continue
        family = heading_family(container.name)
        if not family:
            continue
        end = min(container.end, cs[i+1].start) if i+1<len(cs) else container.end
        weapon_family = family in {'properties','mastery'}
        if not core_rule_source and not weapon_family:
            continue
        children = []
        for j, child in enumerate(cs[i+1:], i+1):
            if child.parent == i:
                children.append(j)
            elif child.start < container.end:
                continue  # nested below a child, not a sibling definition
            elif (container.method == 'word_heading' and child.parent == container.parent
                  and child.rank == container.rank and child.method == 'heading'
                  and child.tag is not None and child.tag.name == 'h4'):
                children.append(j)
            else:
                break
        # "Properties" alone could be an unrelated list. The introduction or repeated
        # member descriptions must explicitly place these properties on weapons.
        if weapon_family and '武器' not in container.name and '武器' not in doc.text[container.title_end:end]:
            descriptions = [doc.text[cs[j].title_end:cs[j].end].strip().split('。',1)[0] for j in children]
            if sum('词条' in d and '武器' in d for d in descriptions) < 2:
                continue
        evidence = {'family':family, 'heading':container.name,
                    'heading_span':[container.start,container.title_end]}
        leads = [(a,b,n) for a,b,n in doc.line_leads() if container.title_end<=a<b<=end]

        def register(member, description):
            declaration = evidence.copy()
            if family=='duration' and (rule := measurement_rule(description)):
                declaration['measure'] = rule
            member['slot_definition'] = declaration

        # Earlier layouts describe measured durations in the parent introduction instead of a member.
        intro = doc.text[container.title_end:leads[0][0] if leads else end]
        if family=='duration' and entries[i]['type']=='rule' and '法术' in intro and measurement_rule(intro):
            register(entries[i], intro)

        for j in children:
            member = entries[j]
            if not all(split_name(member['name'])) or not compact(doc.text[member['title_end']:member['end']]):
                continue
            if weapon_family and member['type']=='section':
                member.update(type='rule',classification_rule='reference_family_member')
            if member['type']=='rule':
                register(member, doc.text[member['title_end']:member['end']])

        # A series of bilingual paragraph labels under the same family heading.
        if len(leads)<2 or not all(all(split_name(n)) for a,b,n in leads):
            continue
        for j,(a,b,name) in enumerate(leads):
            stop = leads[j+1][0] if j+1<len(leads) else end
            if not compact(doc.text[b:stop]):
                continue
            member = by_start.get(a)
            if member is None:
                member = entry_draft(name,a,b,stop,doc.text[a:stop],kind='rule',
                                     classification_rule='reference_family_member',boundary_rule='family_line_lead',
                                     level=container.rank+1,parent_index=i)
                entries.append(member)
                by_start[a] = member
            if member['type']=='rule':
                register(member, doc.text[b:stop])


def _valid_candidate_name(name: str) -> bool:
    return bool(name and len(name) <= 200 and not FIELD_TITLE.fullmatch(name))


def candidates(doc: Document, observations=()) -> list[Candidate]:
    """Assemble, deduplicate, and bound page entry candidates over Document's layout IR."""
    items: list[Candidate] = []

    def add(
        tag: Tag,
        name: str | None = None,
        rank: int = 4,
        method: str = 'heading',
        anchor: str = '',
        kind: str = '',
        ob: dict | None = None,
        end: int | None = None,
    ) -> None:
        if id(tag) not in doc.ranges:
            return
        a, b = doc.span(tag)
        label = compact(name or doc.text[a:b])
        if not _valid_candidate_name(label):
            return
        items.append(
            Candidate(
                a, b, label, rank, method, tag,
                [anchor] if anchor else [],
                [kind] if kind else [],
                [ob] if ob else [],
                end,
            )
        )

    # 1. Structural headings and feat-descriptor bold openings.
    for block in doc.element_headings():
        if block.method == 'opening_bold':
            if matches_feat_descriptor(block.tail):
                add(block.tag, block.text, rank=4, method='descriptor_heading')
        else:
            add(block.tag, block.text, rank=block.rank, method=block.method, anchor=block.anchor)

    # 2. Inline level-prefixed and subfeature headings in class/subclass prose.
    if CLASS_PATH.search(doc.path) or class_structure(doc.text, doc.title):
        for para in doc.paragraph_marks():
            level_tag = None
            for tag, a, b in para.marked_runs:
                if compact(doc.text[para.start:a]):
                    continue
                name = compact(doc.text[a:b])
                existing = next((c for c in items if c.start == a and c.method in STRUCTURAL_HEADING_METHODS), None)
                if re.match(r'^\d+\s*级\s*[:：]', name) and (split_name(name)[1] or existing) and not re.search(r'[。；！？]', name):
                    if not existing and not any(c.start == a and c.title_end == b for c in items):
                        add(tag, rank=4, method='level_inline_heading')
                    level_tag = tag
                    break
            if level_tag is None:
                continue
            level_end = doc.span(level_tag)[1]
            for tag, a, b in para.bold_runs:
                name = compact(doc.text[a:b]).rstrip('。:：')
                if a <= level_end or compact(doc.text[para.start:a].split('\n')[-1]):
                    continue
                zh, en = split_name(name)
                if zh and en and len(name) < 120 and not re.search(r'[。；！？]', name):
                    add(tag, name=name, rank=5, method='feature_inline_heading', end=para.end)

        children = [c for c in items if c.method == 'feature_inline_heading']
        for c in children:
            c.explicit_end = min([c.explicit_end] + [x.start for x in children if c.start < x.start < c.explicit_end])

    # 3. Skill leads under ability-check headings.
    headings = [c for c in items if c.method in STRUCTURAL_HEADING_METHODS]
    for a, b, end, name, owner in skill_leads(doc, headings):
        if not any(c.start <= a < c.title_end for c in items):
            items.append(Candidate(a, b, name, owner.rank + 1, 'skill_lead', None, [], [], [], end))

    # 4. Captioned or titled reference tables.
    for tag, table_span in doc.titled_tables(exclude_title=FIELD_TITLE.fullmatch):
        a, b = doc.span(tag)
        existing = next((c for c in items if c.start == a and c.title_end == b), None)
        if existing:
            existing.fields['table_span'] = list(table_span)
            continue
        preceding = [c for c in items if c.start < a and (c.explicit_end is None or c.explicit_end > a)]
        parent = max(preceding, key=lambda c: c.start) if preceding else None
        add(tag, rank=parent.rank + 1 if parent else 2, method='table_caption', end=table_span[1])
        if items and items[-1].tag is tag:
            items[-1].fields['table_span'] = list(table_span)

    # 5. Boxed stat-block cards and untagged standalone signatures.
    for title_tag, anchor, box_end in doc.boxed_headings():
        add(title_tag, rank=2, method='stat_block', kind='creature', anchor=anchor, end=box_end)

    synthetic: list[Candidate] = []
    for m in STANDALONE_CREATURE_META_RE.finditer(doc.text):
        if not STANDALONE_STATS_RE.search(doc.text[m.end():m.end() + 450]):
            continue
        previous = doc.preceding_title(m.start())
        if not previous:
            continue
        a, b, title = previous
        if any(c.explicit_end and c.start <= a < c.explicit_end for c in items):
            continue
        synthetic.append(Candidate(a, b, title, 2, 'stat_signature', None, [], ['creature']))
    for c in synthetic:
        following = [x.start for x in synthetic + items if 'creature' in x.kinds and x.start > c.start]
        c.explicit_end = min(following) if following else len(doc.text)
        items.append(c)

    for kind, pattern in (('magic_item', STANDALONE_ITEM_META_RE), ('spell', STANDALONE_SPELL_META_RE)):
        for m in pattern.finditer(doc.text):
            if kind == 'spell' and not STANDALONE_SPELL_CONFIRM_RE.search(doc.text[m.end():m.end() + 700]):
                continue
            previous = doc.preceding_title(m.start())
            if not previous:
                continue
            a, b, title = previous
            if any(c.start <= a < c.title_end and kind in c.kinds for c in items):
                continue
            items.append(Candidate(a, b, title, 4, kind + '_signature', None, [], [kind]))

    for m in STANDALONE_POISON_RE.finditer(doc.text):
        title = compact(m[1])
        if re.search('[A-Za-z]', title):
            items.append(Candidate(m.start(1), m.end(1), title, 4, 'poison_signature', None, [], ['poison']))

    # 6. Bind quick-reference index observations by anchor or unique exact heading name.
    def nearest_candidate_tag(pos: int) -> Tag | None:
        near = [c for c in items if pos <= c.start <= pos + 200]
        return min(near, key=lambda c: c.start).tag if near else None

    for ob in observations:
        anchor = ob.get('target_anchor', '')
        resolved_anchor = doc.anchor_target(anchor, nearest_candidate_tag)
        if resolved_anchor is not None:
            tag, container_end = resolved_anchor
            add(
                tag,
                name=ob['name'],
                rank=2 if ob['kind'] == 'creature' else 4,
                method='index_anchor',
                anchor=anchor,
                kind=ob['kind'],
                ob=ob,
                end=container_end,
            )
        else:
            _, en = split_name(ob['name'])
            exact = [c for c in items if key(c.name) == key(ob['name'])]
            matches = exact or [c for c in items if en and key(split_name(c.name)[1]) == key(en)]
            if len(matches) == 1:
                c = matches[0]
                c.observations.append(ob)
                c.kinds.append(ob['kind'])
                c.method += ':index_name'
            elif not anchor and not items:
                add(doc.body, name=ob['name'], rank=1, method='index_page', kind=ob['kind'], ob=ob, end=len(doc.text))

    # 7. Merge overlapping title wrappers and establish parent-child hierarchy & end spans.
    items.sort(key=lambda c: (c.start, 0 if c.observations else 1, 0 if c.method == 'stat_block' else 1, -c.title_end))
    merged: list[Candidate] = []
    for c in items:
        same = next(
            (
                x for x in reversed(merged[-6:])
                if (c.start <= x.title_end and x.start <= c.title_end)
                and (
                    key(c.name) == key(x.name)
                    or c.observations
                    or x.observations
                    or c.method in {'stat_block', 'stat_signature'}
                    or x.method in {'stat_block', 'stat_signature'}
                )
            ),
            None,
        )
        if same:
            same.anchors = list(dict.fromkeys(same.anchors + c.anchors))
            same.kinds = list(dict.fromkeys(same.kinds + c.kinds))
            same.observations += c.observations
            same.fields.update(c.fields)
            same.start = min(same.start, c.start)
            same.title_end = max(same.title_end, c.title_end)
            same.rank = min(same.rank, c.rank)
            if c.explicit_end:
                same.explicit_end = max(c.explicit_end, same.explicit_end or 0)
            if c.method in {'stat_block', 'stat_signature'}:
                same.method = c.method
        else:
            merged.append(c)

    if not merged:
        merged = [Candidate(0, 0, doc.title, 1, 'page_fallback', end=len(doc.text))]
    elif merged[0].start < 150 and merged[0].rank >= 2:
        first_zh, first_en = split_name(merged[0].name)
        title_zh, title_en = split_name(doc.title)
        if (first_zh and key(first_zh) == key(title_zh)) or (first_en and key(first_en) == key(title_en)):
            merged[0].rank = 1

    if compact(doc.text[:merged[0].start]) and merged[0].start > 100:
        merged.insert(0, Candidate(0, 0, doc.title, 0, 'page_intro', end=merged[0].start))

    stack: list[int] = []
    legacy_markers = doc.legacy_marker_spans()
    for i, c in enumerate(merged):
        while stack and (merged[stack[-1]].rank >= c.rank or merged[stack[-1]].end <= c.start):
            stack.pop()
        c.parent = stack[-1] if stack else None
        stack.append(i)
        c.end = c.explicit_end or next((x.start for x in merged[i + 1:] if x.rank <= c.rank), len(doc.text))
        if c.method == 'page_intro':
            c.end = merged[i + 1].start
        marker_limit = min(c.end, c.title_end + 40, merged[i + 1].start if i + 1 < len(merged) else c.end)
        c.local_legacy = doc.has_local_legacy(c.name, c.start, c.title_end, marker_limit, legacy_markers, c.tag)

    return merged


# ---------------------------------------------------------------------------------------------------
# List and Table Container Frames.

def facility_list_entries(doc: Document) -> list[dict]:
    """Extract list-encoded facility catalogs (e.g. DMG 基础设施 Basic Facilities)."""
    entries: list[dict] = []
    for tlist in doc.titled_lists():
        if not BASIC_FACILITIES_CAPTION_RE.fullmatch(tlist.caption):
            continue
        caption_evidence = {'path': doc.path, 'span': list(tlist.caption_span)}
        for name, (a, b) in tlist.items:
            if not name or len(name) > 30:
                continue
            entries.append(
                entry_draft(
                    name, a, b, b, doc.text[a:b],
                    kind='facility',
                    classification_rule='facility_list_signature',
                    boundary_rule='list_item',
                    level=9,
                    extra_facts=[('facility_kind', '基础', caption_evidence, 'facility_list', '原文明示')],
                )
            )
    return entries


def _header_field(header: str) -> str | None:
    cleaned = re.sub(r'\s+', '', header)
    if cleaned.startswith('护甲等级'):
        return 'armor_class'
    return TABLE_HEADERS.get(cleaned)


def weapon_field(header):
    return 'price' if header.strip()=='价值' else _header_field(header)


def weapon_header(headers):
    """The column signature identifies weapons even in a price-less or period-labelled inventory."""
    roles = [_header_field(h) for h in headers]
    return (bool(headers) and {'damage','properties'} <= set(roles)
            and (headers[0] in TABLE_NAME_HEADERS or headers[0].endswith('物品'))
            and all(roles.count(role)==1 for role in ('damage','properties')))


def weapon_cells(path, text, tables):
    """Map each cell to the currently governing header, including repeated headers within a table."""
    cells = {}
    for table in tables:
        headers = table['headers']
        if not weapon_header(headers):
            continue
        by_row = {}
        for row,col,a,b in table['cells']:
            by_row.setdefault(row, []).append((col,a,b))
        for row in sorted(set(by_row) | set(table.get('repeated_headers',{}))):
            spans = by_row.get(row, [])
            printed = table.get('repeated_headers',{}).get(row) or [compact(text[a:b]) for _,a,b in spans]
            if weapon_header(printed):
                headers = printed
                continue
            if len(spans) != len(headers):
                continue
            for col,a,b in spans:
                cells[a,b] = (_header_field(headers[col]), {'id':'tbl_'+digest(path,table['index']),
                             'row':row, 'column':col, 'span':table['span'], 'cell_span':[a,b],
                             'headers':headers, 'cell_text':compact(text[a:b])})
    return cells


def _definition_table_entries(doc: Document, table: dict, family_heading=None) -> list[dict]:
    """Entries for tables whose first header names a definition kind and whose rows all carry bilingual names."""
    rows = table['rows']
    headers = [re.sub(r'\s+', '', h) for h in rows[0]['cells']]
    kind = DEFINITION_TABLE_KINDS.get(headers[0]) if headers else None
    damage_table = family_heading is not None and headers == ['类型','例子']
    if damage_table:
        kind = 'rule'
    body = rows[1:]
    if not kind or len(body) < 2:
        return []
    if any(
        len(r['cells']) != len(headers)
        or any(s != '1' for s in r['rowspan'] + r['colspan'])
        or not all(split_name(r['cells'][0])) or (damage_table and not r['cells'][1])
        for r in body
    ):
        return []
    columns = {
        j: field_name
        for j, h in enumerate(headers)
        for label, field_name in DEFINITION_TABLE_FIELDS.get(kind, {}).items()
        if h == label
    }
    entries: list[dict] = []
    for row in body:
        left, right = row['cell_spans'][0][0], row['cell_spans'][-1][1]
        fields = {
            field_name: [(row['cells'][j], row['cell_spans'][j][0] - left, row['cell_spans'][j][1] - left)]
            for j, field_name in columns.items()
            if row['cells'][j]
        }
        entries.append(
            entry_draft(
                row['cells'][0], left, row['cell_spans'][0][1], right, doc.text[left:right],
                kind=kind,
                classification_rule='reference_family_member' if damage_table else 'definition_table',
                boundary_rule='table_row',
                level=9,
                fields=fields,
                extra_facts=[],
            )
        )
        if damage_table:
            entries[-1]['slot_definition'] = {'family':'damage','heading':family_heading.name,
                                               'heading_span':[family_heading.start,family_heading.title_end]}
    return entries


def extract_tables(doc: Document, page_candidates: list[Candidate], core_rule_source=False) -> tuple[list[dict], list[dict], list[dict]]:
    """Extract raw table records alongside definition-table and catalog-table row entries."""
    entries: list[dict] = []
    tables: list[dict] = []
    issues: list[dict] = []

    family_tables = {tuple(c.fields['table_span']): c for c in page_candidates
                     if core_rule_source and c.fields.get('table_span') and heading_family(c.name)=='damage'}
    for ti, t in enumerate(doc.tables()):
        rows = t['rows']
        a, b = t['span']
        tables.append({
            'id': 'tbl_' + digest(doc.path, ti),
            'page_path': doc.path,
            'start': a,
            'end': b,
            'headers': rows[0]['cells'],
            'rows': [{k: r[k] for k in ('cells', 'cell_spans', 'span', 'rowspan', 'colspan')} for r in rows],
            'layout_warning': any(any(x != '1' for x in r['rowspan'] + r['colspan']) for r in rows),
        })

        defined = _definition_table_entries(doc, t, family_tables.get((a,b)))
        if defined:
            entries.extend(defined)
            continue

        hi = next(
            (
                i for i, r in enumerate(rows[:4])
                if weapon_header(r['cells']) or (any(_header_field(h) == 'price' for h in r['cells'])
                and any(h in TABLE_NAME_HEADERS for h in r['cells']))
            ),
            None,
        )
        if hi is None:
            continue

        headers = rows[hi]['cells']
        weapons = weapon_header(headers)
        column_field = weapon_field if weapons else _header_field
        namecols = [0] if weapons else [i for i, h in enumerate(headers) if h in TABLE_NAME_HEADERS]
        before = [c for c in page_candidates if c.start < a]
        title = max(before, key=lambda c: c.start).name if before else doc.title
        caption_text = t.get('preceding_text', '')
        if caption_text and len(caption_text) <= 100:
            title = caption_text

        groups = []
        for i, nc in enumerate(namecols):
            lo = 0 if len(namecols) == 1 else (nc if nc else 0)
            end = namecols[i + 1] if i + 1 < len(namecols) else len(headers)
            cols = list(range(lo, end))
            if not weapons and not any(column_field(headers[j]) == 'price' for j in cols):
                continue
            mapped = {column_field(headers[j]) for j in cols}
            animal = 'carrying_capacity' in mapped and bool(re.search(r'坐骑.*动物', title))
            vehicle = (
                ('speed' in mapped and bool(re.search('载具|船', doc.path + title)))
                or ('carrying_capacity' in mapped and not animal and '载具' in doc.path)
            )
            equipment = weapons or 'weight' in mapped or ('price' in mapped and title.startswith('机巧奇具'))
            goods = len(headers) == 2 and headers[nc] in {'货物', '商品'} and bool(re.search('商业货物|其他开支', doc.path))
            if not (animal or vehicle or equipment or goods):
                issues.append({'reason': 'catalog_table_unhandled', 'detail': {'headers': headers, 'title': title}, 'span': [a, b]})
                continue
            groups.append((nc, cols, 'animal_offer' if animal else 'vehicle' if vehicle else 'equipment'))

        group_label = ''
        for row in rows[hi + 1:]:
            vals = row['cells']
            if weapons and weapon_header(vals):
                headers = vals
                groups = [(0, list(range(len(headers))), 'equipment')]
                group_label = ''
                continue
            if len(vals) == 1 and row['colspan'][0] != '1':
                group_label = vals[0]
                continue
            if len(vals) != len(headers) or any(s != '1' for s in row['rowspan'] + row['colspan']):
                if groups:
                    issues.append({'reason': 'catalog_row_layout_unresolved', 'detail': vals, 'span': list(row['span'])})
                continue
            for nc, cols, kind in groups:
                name = vals[nc]
                if not name or len(name) > 100:
                    continue
                price = next((vals[j] for j in cols if column_field(headers[j]) == 'price'), '')
                if not re.search(r'\d|×|多[种类]|不等|—|－', price):
                    # Without a printed price, require an actual damage expression, not a section divider.
                    if not weapons or not re.match(r'\s*\d', vals[next(j for j in cols if column_field(headers[j])=='damage')]):
                        continue
                actual = 'vehicle' if kind == 'equipment' and '坐骑' in doc.path and VEHICLE_NAMES_RE.search(name) else kind
                left = min(row['cell_spans'][j][0] for j in cols)
                right = max(row['cell_spans'][j][1] for j in cols)
                fields = {
                    field_name: [(vals[j], row['cell_spans'][j][0] - left, row['cell_spans'][j][1] - left)]
                    for j in cols
                    if (field_name := column_field(headers[j])) and vals[j]
                }
                extra = []
                if actual == 'animal_offer':
                    if doc.path.startswith(('topics/玩家手册/', 'topics/玩家手册2024/')):
                        extra.append(('role', '坐骑', {'path': doc.path, 'span': [a, b], 'table_title': title}, 'mount_catalog_context', '上下文推得'))
                    zh, _ = split_name(name)
                    matching = [c for c in page_candidates if key(split_name(re.sub(r'[（(].*', '', c.name))[0]) == key(zh)]
                    if len(matching) == 1:
                        c = matching[0]
                        body = doc.text[c.start:c.end]
                        role = '宠物' if re.search('宠物|送信', body) else '役畜' if re.search('拉动.*雪橇|拉一辆雪橇', body) else None
                        if role:
                            extra.append(('role', role, {'path': doc.path, 'span': [c.start, c.end]}, 'animal_description', '原文明示'))
                        m = re.search(r'使用\s*(.*?)\s*数据卡', body, re.S)
                        if m:
                            extra.append(('statblock_name', compact(m[1]), {'path': doc.path, 'span': [c.start + m.start(), c.start + m.end()]}, 'statblock_reference', '原文明示'))
                if group_label:
                    extra.append(('table_group', group_label, {'path': doc.path, 'span': [a, b]}, 'table_group', '原文明示'))

                entries.append(
                    entry_draft(
                        name, left, row['cell_spans'][nc][1], right, doc.text[left:right],
                        kind=actual,
                        classification_rule=actual + '_table',
                        boundary_rule='table_row',
                        level=9,
                        fields=fields,
                        extra_facts=extra,
                    )
                )
    return entries, tables, issues


# ---------------------------------------------------------------------------------------------------
# Whole-page extraction pipeline over Document's domain-free layout IR.

def extract_page_entries(
    doc: Document,
    observations: list[dict],
    context: str,
    toc_place: dict,
    core_rule_source: bool,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Run candidate discovery, signature typing, field extraction, and container frames for one page."""
    path = doc.path
    cs = candidates(doc, observations)
    mark_class_regions(cs, doc.text)

    entries: list[dict] = []
    subrules: list[dict] = []
    is_core_magic_chapter = bool(core_rule_source and CORE_SPELL_CHAPTER_RE.search(path) and '/法术详述/' not in path)

    for i, c in enumerate(cs):
        full = doc.text[c.start:c.end]
        c.fields['core_rule_chapter'] = is_core_magic_chapter
        next_start = cs[i + 1].start if i + 1 < len(cs) else c.end
        own = doc.text[c.start:min(c.end, next_start)]
        primary = i == 0 or (i == 1 and cs[0].method == 'page_intro')

        typing = classify(c, own, full, context + ' | ' + path, primary)
        kind, rule = typing.type, typing.rule
        full_for_fields = full if (kind == 'class' or c.explicit_end) else own
        fields = extract_entry_fields(full_for_fields, kind, max(0, c.title_end - c.start))

        entries.append(
            entry_draft(
                clean_entry_name(c.name, kind),
                c.start, c.title_end, c.end, full,
                kind=kind,
                classification_rule=rule,
                boundary_rule=c.method,
                level=c.rank,
                is_primary=primary,
                parent_index=c.parent,
                anchors=c.anchors,
                printed_types=[list(t) for t in typing.printed_types],
                legacy_direct=c.local_legacy,
                fields=fields,
                observation_ids=[o['id'] for o in c.observations],
                table_span=c.fields.get('table_span'),
            )
        )
        if core_rule_source and kind in RULE_TYPES:
            for a, b, end, name, method in rule_children(doc, c.title_end, min(c.end, next_start), c.name):
                subrules.append(
                    entry_draft(
                        name, a, b, end, doc.text[a:end],
                        kind='rule',
                        classification_rule='rule_subdefinition',
                        boundary_rule=method,
                        level=c.rank + 1,
                        parent_index=i,
                        legacy_direct=c.local_legacy,
                    )
                )

    entries.extend(subrules)
    refine_page_types(entries, doc.text, toc_place)
    collect_slot_definitions(doc, cs, entries, core_rule_source)
    entries.extend(facility_list_entries(doc))
    table_entries, tables, table_issues = extract_tables(doc, cs, core_rule_source)
    entries.extend(table_entries)
    return entries, tables, table_issues
