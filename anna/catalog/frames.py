"""Page-level candidate assembly and sub-entry container frames over the domain-free layout IR."""
from __future__ import annotations

from dataclasses import dataclass, field
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
    matches_feat_descriptor,
    skill_notation,
)
from anna.source.html import Document, compact, key, split_name

STRUCTURAL_HEADING_METHODS = {'heading', 'word_heading', 'colored_heading'}


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
