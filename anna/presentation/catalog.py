#!/usr/bin/env python3
"""Catalog response models, filtering and exports over the published SQLite snapshot."""
from __future__ import annotations
from collections import Counter, OrderedDict
import csv
from functools import lru_cache
import gzip
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import anna.catalog.views as browse
from anna.presentation import contracts, expression
from anna.presentation.expression import Query
import anna.paths as paths
from anna.presentation.layout import text_start

ROOT = paths.PROJECT
DB = ROOT / 'data/extracted/catalog.sqlite'
TYPES = dict(class_='职业', subclass='子职', class_feature='职业能力', species='种族物种', background='背景', feat='专长', spell='法术', equipment='普通装备', magic_item='魔法物品', creature='生物数据卡', reward='奖励', vehicle='载具', animal_offer='动物购置', facility='设施', ability='属性', skill='技能', condition='状态', action='动作', rule='规则', trap='陷阱', poison='毒药', disease='疾病', hazard='环境危害', subsystem='扩展机制', lore='资料索引', section='未分类段落')
TYPES['class'] = TYPES.pop('class_')
FIELDS = {'spell_level':'环阶', 'school':'学派', 'rarity':'稀有度', 'cr':'挑战等级', 'item_type':'物品类别', 'feat_category':'专长类别', 'concentration':'专注', 'attunement':'同调'}


def connect(path=DB, *, check_same_thread=True):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, check_same_thread=check_same_thread)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    browse.register(db)
    return db


def first(params, name, default=''):
    return params.get(name, [default])[0]


def entry_types(params):
    """The chosen entry types. They scope the table and pick its schema; untyped sections stay behind `sections`."""
    chosen = list(dict.fromkeys(params.get('type', [])))
    if any(t not in TYPES or t=='section' for t in chosen): raise ValueError('未知条目类型')
    return chosen


COMMON_CHOICES = {'ruleset':('ruleset',('5e','5r','待核')), 'legacy':('legacy',('entry_marked','source_archived','unmarked')),
                  'status':('classification_status',('原文明示','上下文推得','未分类段落'))}
ORIGINS = ('官方','合作','待核')
SOURCE_EXISTS = 'EXISTS(SELECT 1 FROM entry_sources es JOIN sources s ON s.id=es.source_id WHERE es.entry_id=e.id AND '


ENTRY_BODY = ("(SELECT substr(p.text,s.b+1,e.end-s.b) FROM pages p, (SELECT CASE WHEN json_extract(e.data,'$.title_end')>e.start "
              "AND json_extract(e.data,'$.title_end')<e.end THEN json_extract(e.data,'$.title_end') ELSE e.start END AS b) s WHERE p.path=e.page_path)")


# Any field of chosen values, and each name or body search, can be excluded: `not_<key>` holds the excluded
# values or terms. Ranges, the conflict switch and the term being typed are not negated.
NOT = 'not_'
NEGATABLE = ('type','q','body','ruleset','legacy','status','source','origin')


def card_condition(card):
    """One card's SQL. Its values are alternatives; an excluded card holds for entries that meet none of them, and an
    entry whose value was not extracted is not excluded. A card this catalog cannot read holds for every entry."""
    if card.negated and card.base not in NEGATABLE and not card.base.startswith('f_'): raise ValueError('无法排除：'+card.base)
    found = included({card.base:list(card.values)})
    if not found: return '1',[]
    _,sql,values = found[0]
    return (f'NOT COALESCE(({sql}),0)',values) if card.negated else (sql,values)


def included(params):
    out = []
    types = entry_types(params)
    if types: out.append(('type','e.type IN ('+','.join('?' for _ in types)+')',types))
    # Literal substring searches, not SQL LIKE patterns; each must match the name.
    terms = browse.search_terms(params)
    for i, term in enumerate(terms): out.append((f'q{i}','instr(lower(e.name),lower(?))>0',[term]))
    typing = browse.typing_term(params)
    if typing: out.append(('typing','instr(lower(e.name),lower(?))>0',[typing]))
    # Body searches read the entry's own text after its title line (entry_layout.text_start), literally.
    for i, term in enumerate(browse.body_terms(params)): out.append((f'body{i}','instr(lower('+ENTRY_BODY+'),lower(?))>0',[term]))
    for key,(column,allowed) in COMMON_CHOICES.items():
        chosen = list(dict.fromkeys(params.get(key,[])))
        if not chosen: continue
        if chosen == [browse.NONE]: out.append((key,'0',[])); continue
        if any(v not in allowed for v in chosen): raise ValueError('无效筛选：'+key)
        out.append((key,'e.'+column+' IN ('+','.join('?' for _ in chosen)+')',chosen))
    sources = list(dict.fromkeys(params.get('source', [])))
    if sources:
        if len(sources)>155: raise ValueError('来源过多')
        out.append(('source',SOURCE_EXISTS+'s.id IN ('+','.join('?' for _ in sources)+'))',sources))
    origin = list(dict.fromkeys(params.get('origin',[])))
    if origin:
        if origin == [browse.NONE]: out.append(('origin','0',[]))
        elif any(o not in ORIGINS for o in origin): raise ValueError('无效来源性质')
        else: out.append(('origin',SOURCE_EXISTS+'s.origin IN ('+','.join('?' for _ in origin)+'))',origin))
    if first(params,'conflict')=='1': out.append(('conflict',"json_array_length(json_extract(e.data,'$.field_conflicts'))>0",[]))
    out.extend(browse.facet_conditions(params))
    return out


def restriction_clause(restrict):
    """The reader's library scope: the rules generations and works this catalog may answer from.
    It bounds the universe every query and count reads; it is not a condition a count can set aside."""
    clauses, values = [], []
    if restrict is not None and restrict.rulesets is not None:
        clauses.append('e.ruleset IN ('+','.join('?' for _ in restrict.rulesets)+')'); values.extend(sorted(restrict.rulesets))
    if restrict is not None and restrict.sources is not None:
        clauses.append('EXISTS(SELECT 1 FROM entry_sources es WHERE es.entry_id=e.id AND es.source_id IN ('+','.join('?' for _ in restrict.sources)+'))' if restrict.sources else '0')
        values.extend(sorted(restrict.sources))
    return clauses, values


def attribute_clause(params):
    """A request's single attribute (`field` with `value`), the form category views linked by before facets: it
    narrows every query and count. The page turns such links into a card; the API still reads them."""
    field, value = first(params,'field'),first(params,'value')
    if not value: return [],[]
    if field not in FIELDS: raise ValueError('无效属性')
    return ['EXISTS(SELECT 1 FROM json_each(e.fields,?) f WHERE CAST(f.value AS TEXT)=?)'],['$.'+field,{'true':'1','false':'0'}.get(value,value)]


def base_clause(params, restrict=None, *, non_section_max=None):
    """Untyped sections join the results only on request; with the library scope this bounds every query and count."""
    clauses, values = restriction_clause(restrict)
    attribute, attributed = attribute_clause(params)
    if first(params, 'sections') != '1':
        if non_section_max is not None:
            head, head_vals = 'e.rowid<=?', [non_section_max]
        else:
            head, head_vals = "e.type!='section'", []
    else:
        head, head_vals = '1', []
    return ' AND '.join([head, *clauses, *attribute]), head_vals + values + attributed


def unclassified_matches(db, params, restrict=None):
    """Untyped sections that match every current condition, for an empty name search's follow-up.

    The same conditions apply, including any type filter: a section cannot stand in for a chosen
    type, so the offer appears only when adding sections would actually return rows.
    """
    query = Query.of(params)
    scope, scoped = restriction_clause(restrict)
    attribute, attributed = attribute_clause(query)
    sql, values = expression.sql(query.where, card_condition)
    non_section_max = (
        _meta_int(db, 'pages_non_section_max_rowid')
        if 'e.data' not in sql and not attributed and _has_table(db, 'entry_core')
        else None
    )
    sec_clause = 'e.rowid>?' if non_section_max is not None else "e.type='section'"
    sec_vals = [non_section_max] if non_section_max is not None else []
    clauses_sql = ' AND '.join([sec_clause, *scope, *attribute, sql])
    from_sql, (clauses_sql,) = _from_and_bind(db, clauses_sql)
    return db.execute(
        f'SELECT count(*) FROM {from_sql} WHERE {clauses_sql}',
        sec_vals + scoped + attributed + values,
    ).fetchone()[0]


def bounded(query):
    """A query within the search limits: each list of terms counts across all its cards."""
    browse.search_terms(query);browse.body_terms(query)
    return query


def filters(db, params, restrict=None, *, extra_sql=''):
    """The WHERE clause for the query's conditions as written, within the library scope."""
    query = bounded(Query.of(params))
    sql, values = expression.sql(query.where, card_condition)
    attr_field = first(query, 'field') if first(query, 'value') else ''
    uses_data = 'e.data' in sql or 'e.data' in extra_sql
    non_section_max = None
    if not uses_data and not attr_field and _has_table(db, 'entry_core'):
        non_section_max = _meta_int(db, 'pages_non_section_max_rowid')
    base, scoped = base_clause(query, restrict, non_section_max=non_section_max)
    return f' WHERE {base} AND {sql}', scoped + values


_ASCII_LOWER = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')


def _ascii_lower(text: str) -> str:
    return text.translate(_ASCII_LOWER)


def simple_rank_filter(query, allowed_types, allowed_rulesets, *, restrict=None, max_sources=155):
    """Validate and extract simple AND-joined filters for in-memory rank-index execution, or return None.

    Handles unrestricted TRUE / FALSE and AllOf of non-negated ``type``, ``ruleset``, ``source``,
    ``q`` and ``typing`` cards when there is no attribute filter (``field``/``value``) and
    ``sections != '1'``.
    """
    if first(query, 'sections') == '1' or first(query, 'value') or query.probing:
        return None
    node = expression.settle(query.where)
    if node == expression.FALSE:
        return set(), set(), set(), []
    types = None
    rulesets = set(restrict.rulesets) if restrict is not None and restrict.rulesets is not None else None
    sources = set(restrict.sources) if restrict is not None and restrict.sources is not None else None
    terms: list[str] = []
    if node != expression.TRUE:
        parts = node.parts if isinstance(node, expression.AllOf) else (node,)
        for card in parts:
            if not isinstance(card, expression.Card) or card.negated or card.base not in {'type', 'ruleset', 'source', 'q', 'typing'}:
                return None
            if card.base == 'type':
                vals = list(dict.fromkeys(card.values))
                if any(v not in allowed_types for v in vals):
                    return None
                val_set = set(vals)
                types = val_set if types is None else (types & val_set)
            elif card.base == 'ruleset':
                vals = list(dict.fromkeys(card.values))
                if any(v not in allowed_rulesets for v in vals):
                    return None
                val_set = set(vals)
                rulesets = val_set if rulesets is None else (rulesets & val_set)
            elif card.base == 'source':
                vals = list(dict.fromkeys(card.values))
                if len(vals) > max_sources:
                    return None
                val_set = set(vals)
                sources = val_set if sources is None else (sources & val_set)
            elif card.base in {'q', 'typing'}:
                for val in card.values:
                    stripped = val.strip()
                    if stripped:
                        terms.append(_ascii_lower(stripped))
    return types, rulesets, sources, terms


def simple_type_ruleset_filter(query, allowed_types, allowed_rulesets, restrict=None):
    """Return (types_or_None, rulesets_or_None) when ``query`` and ``restrict`` only constrain
    non-negated ``type`` and ``ruleset`` cards, or ``None`` if full SQL evaluation is needed."""
    simple = simple_rank_filter(query, allowed_types, allowed_rulesets, restrict=restrict)
    if simple is None:
        return None
    types, rulesets, sources, terms = simple
    if sources is not None or terms:
        return None
    return types, rulesets


_TYPE_RULESET_COUNTS_LOCK = threading.Lock()
_TYPE_RULESET_COUNTS_CACHE: OrderedDict[tuple[str, int, int, int], list[list]] = OrderedDict()
_TYPE_RULESET_COUNTS_LIMIT = 8


def _type_ruleset_counts(db):
    counts_rows = _meta_json(db, 'pages_type_ruleset_counts')
    if counts_rows is not None:
        return counts_rows
    sig = _db_signature(db)
    if sig is None:
        return None
    with _TYPE_RULESET_COUNTS_LOCK:
        cached = _TYPE_RULESET_COUNTS_CACHE.get(sig)
        if cached is not None:
            _TYPE_RULESET_COUNTS_CACHE.move_to_end(sig)
            return cached
    table = _entry_table(db)
    rows = [
        [r[0], r[1], int(r[2])]
        for r in db.execute(
            f"SELECT type, COALESCE(ruleset, ''), count(*) FROM {table} "
            "GROUP BY type, COALESCE(ruleset, '') ORDER BY type, COALESCE(ruleset, '')"
        )
    ]
    with _TYPE_RULESET_COUNTS_LOCK:
        _TYPE_RULESET_COUNTS_CACHE[sig] = rows
        _TYPE_RULESET_COUNTS_CACHE.move_to_end(sig)
        while len(_TYPE_RULESET_COUNTS_CACHE) > _TYPE_RULESET_COUNTS_LIMIT:
            _TYPE_RULESET_COUNTS_CACHE.popitem(last=False)
    return rows


def count(db, params, restrict=None):
    """Count matching versions without reading full entry payloads or stacking."""
    query = bounded(Query.of(params))
    if first(query, 'sections') != '1' and not first(query, 'value') and not query.probing:
        allowed_types = set(TYPES) - {'section'}
        allowed_rulesets = set(COMMON_CHOICES['ruleset'][1])
        simple = simple_type_ruleset_filter(query, allowed_types, allowed_rulesets, restrict)
        if simple is not None and (counts_rows := _type_ruleset_counts(db)) is not None:
            types, rulesets = simple
            return sum(
                cnt for t, r, cnt in counts_rows
                if t != 'section' and (types is None or t in types) and (rulesets is None or r in rulesets)
            )
    where, values = filters(db, query, restrict)
    from_sql, (where,) = _from_and_bind(db, where)
    return int(db.execute(f'SELECT count(*) FROM {from_sql}{where}', values).fetchone()[0])


def facets(db, params, restrict=None):
    """Each field's options with counts. An option counts what the query matches with that field's own cards set
    aside and the option held; while the caret probes (`probe`), what the query matches with the option placed at
    the caret, so a suggestion says what choosing it there returns. `without` counts the matches each card's removal
    would add, naming the card by its place in the line; `scope_total` counts the chosen types."""
    query = bounded(Query.of(params))
    types = entry_types(query)
    config = browse.schema_for(types, expression.open_types(query.where))
    if (
        query.where == expression.TRUE
        and not query.probing
        and restrict is None
        and first(query, 'sections') != '1'
        and not first(query, 'value')
        and not any(params.get(k) or params.get(NOT + k) for k in ('type', 'ruleset', 'origin', 'source'))
        and (pre := _meta_json(db, 'pages_unfiltered_facets')) is not None
    ):
        sources = {r['id']: json.loads(r['data']) for r in db.execute('SELECT id, data FROM sources')}
        type_group = {
            'key': 'type', 'label': '类型', 'mode': 'multi',
            'options': [{'value': t, 'label': TYPES[t], 'count': pre['type'][t]} for t in browse.TYPE_ORDER if t in pre['type']],
        }
        rs_group = {
            'key': 'ruleset', 'label': '规则版本', 'mode': 'multi',
            'options': [{'value': v, 'label': v, 'count': pre['ruleset'].get(v, 0)} for v in COMMON_CHOICES['ruleset'][1]],
        }
        or_group = {
            'key': 'origin', 'label': '来源性质', 'mode': 'multi',
            'options': [{'value': v, 'label': v, 'count': pre['origin'].get(v, 0)} for v in ORIGINS],
        }
        src_options = sorted(
            (
                {
                    'value': v, 'label': sources[v]['label'], 'title': sources[v]['name'],
                    'search': ' '.join([sources[v]['name'], *sources[v].get('aliases', [])]),
                    'count': cnt,
                }
                for v, cnt in pre['source'].items() if cnt > 0 and v in sources
            ),
            key=lambda o: o['label'],
        )
        src_group = {'key': 'source', 'label': '来源', 'mode': 'multi', 'options': src_options}
        return {'schema': config, 'groups': [type_group, rs_group, or_group, src_group],
                'total': pre['total'], 'scope_total': pre['total'], 'without': {}}
    line = query.cards()
    single_type_card = (
        len(line) == 1
        and line[0].base == 'type'
        and not line[0].negated
        and not query.probing
        and restrict is None
        and first(query, 'sections') != '1'
        and not first(query, 'value')
        and bool(types)
        and (pre := _meta_json(db, 'pages_unfiltered_facets')) is not None
    )
    if not single_type_card:
        pre = None
    columns = [] if single_type_card else [expression.sql(card, card_condition) for card in line]
    selected = '' if single_type_card else ''.join(f', ({sql}) AS c{card.index}' for card, (sql, _) in zip(line, columns))
    origin_of = dict(db.execute('SELECT id, origin FROM sources'))
    domain = [*config['facets'], *config['flags']]
    counted = ['type', *('f_' + f for f in domain), 'ruleset', 'origin', 'source']
    counts = {k: Counter() for k in counted}
    universe = {k: set() for k in counted}
    unknown_cover = Counter()
    total = scope_total = always = 0
    without = Counter()
    where = query.where
    if not single_type_card:
        tests = {}
        meets = lambda card: (lambda row, name=f'c{card.index}': bool(row[name]))
        def check(node):
            tests.setdefault(node, expression.test(node, meets))
            return node
        matches = check(where)
        gains = [(str(card.index), check(expression.without(where, card.index))) for card in line]
        in_scope = check(expression.relax(where, lambda c: c.field != 'type'))
        if query.probing:
            placed = check(expression.substitute(query.tree, lambda c: expression.TRUE if c is expression.PROBE else c))
            unplaced = check(expression.substitute(query.tree, lambda c: expression.FALSE if c is expression.PROBE else c))
        else:
            aside = {key: check(expression.relax(where, lambda c, key=key: c.field == key)) for key in counted}
    has_core = _has_table(db, 'entry_core')
    has_split_fields = has_core and _has_table(db, 'entry_fields')
    use_single_src = _meta_int(db, 'pages_single_source') == 1 and _has_column(db, 'entry_core', 'primary_source_id')
    source_expr = ('e.primary_source_id AS sources' if use_single_src else
                   "(SELECT group_concat(es.source_id, char(31)) FROM entry_sources es WHERE es.entry_id=e.id) AS sources")
    extra_clause = f' AND e.type IN ({",".join("?" for _ in types)})' if single_type_card else ''
    extra_vals = list(types) if single_type_card else []
    non_section_max = (
        _meta_int(db, 'pages_non_section_max_rowid')
        if has_core and 'e.data' not in selected and not first(query, 'value')
        else None
    )
    base, scoped = base_clause(query, restrict, non_section_max=non_section_max)
    from_sql, (selected_sql, base_sql) = _from_and_bind(db, selected, base)
    if has_split_fields and 'entry_fields' not in from_sql:
        if not domain:
            fields_expr = 'NULL AS fields'
            case_vals = []
        elif types and not query.probing and not expression.open_types(where):
            ph = ','.join('?' for _ in types)
            fields_expr = f'CASE WHEN e.type IN ({ph}) THEN (SELECT ef2.fields FROM entry_fields ef2 WHERE ef2.entry_rowid=e.rowid) END AS fields'
            # Place CASE bindings before WHERE bindings.
            case_vals = list(types)
        else:
            from_sql += ' LEFT JOIN entry_fields ef ON ef.entry_rowid=e.rowid'
            fields_expr = 'ef.fields AS fields'
            case_vals = []
    else:
        fields_expr = ('ef.fields' if 'entry_fields ef' in from_sql else 'e.fields') + ' AS fields'
        case_vals = []

    rows_cur = db.execute(
        f'SELECT e.type, {fields_expr}, e.ruleset, e.legacy, e.classification_status, {source_expr}{selected_sql} '
        f'FROM {from_sql} WHERE {base_sql}{extra_clause}',
        case_vals + [v for _, values in columns for v in values] + scoped + extra_vals,
    )
    for row in rows_cur:
        parsed = None
        raw_src = row['sources']
        source_ids = (raw_src.split('\x1f') if '\x1f' in raw_src else [raw_src]) if raw_src else []

        def values(key):
            nonlocal parsed
            if key == 'type':
                return [row['type']]
            if key.startswith('f_'):
                parsed = parsed or json.loads(row['fields'])
                return browse.normalized(parsed, key[2:])
            if key == 'source':
                return source_ids
            if key == 'origin':
                return list(dict.fromkeys(origin_of.get(s) for s in source_ids if origin_of.get(s)))
            return [row[{'status': 'classification_status'}.get(key, key)]]

        if single_type_card:
            scope_total += 1
            total += 1
            for key in counted[1:]:
                vals = values(key)
                universe[key].update(vals)
                counts[key].update(vals)
            for f in domain:
                unknown_cover[f] += values('f_' + f) != [browse.UNKNOWN]
            continue
        result = {node: t(row) for node, t in tests.items()}
        if not any(result.values()):
            continue
        if result[in_scope]:
            scope_total += 1
            for key in counted[1:]:
                universe[key].update(values(key))
            for f in domain:
                unknown_cover[f] += values('f_' + f) != [browse.UNKNOWN]
        if result[matches]:
            total += 1
        else:
            for name, t in gains:
                if result[t]:
                    without[name] += 1
        if query.probing:
            if result[unplaced]:
                always += 1
            elif result[placed]:
                for key in counted:
                    counts[key].update(values(key))
        else:
            for key in counted:
                if result[aside[key]]:
                    counts[key].update(values(key))
    if single_type_card:
        counts['type'].update(pre['type'])
        diff = pre['total'] - total
        if diff > 0:
            without[str(line[0].index)] = diff
    for key in counted:
        # A chosen value stays listed even when nothing in scope holds it, so it can be unchosen.
        universe[key].update(v for v in [*params.get(key, []), *params.get(NOT + key, [])] if v != browse.NONE)
    universe['type'] = held_types(db)
    sources = {r['id']: json.loads(r['data']) for r in db.execute('SELECT id, data FROM sources')}
    def group(key):
        if key=='type':
            options = [{'value':t,'label':TYPES[t]} for t in browse.TYPE_ORDER if t in universe['type']]
        elif key.startswith('f_'):
            field = key[2:]
            options = [{'value':v,'label':browse.option_label(field,v)} for v in sorted(universe[key],key=lambda v:browse.option_order(field,v))]
        elif key=='source':
            options = sorted(({'value':v,'label':sources[v]['label'],'title':sources[v]['name'],
                               'search':' '.join([sources[v]['name'],*sources[v].get('aliases',[])])} for v in universe[key] if v in sources),key=lambda o:o['label'])
        elif key=='origin': options = [{'value':v,'label':v} for v in ORIGINS]
        else: options = [{'value':v,'label':v} for v in COMMON_CHOICES[key][1]]
        for o in options: o['count'] = counts[key][o['value']]+always
        label = {'type':'类型','ruleset':'规则版本','origin':'来源性质','source':'来源','legacy':'旧版标记','status':'分类依据'}.get(key) or browse.LABELS[key[2:]]
        mode = 'flag' if key.startswith('f_') and key[2:] in config['flags'] else 'multi'
        return {'key':key,'label':label,'mode':mode,'options':options}
    domain_groups = [group('f_'+f) for f in domain]
    # Mixed types lead with the facets most of their entries can answer (rarity before weapon mastery).
    if len(config['kinds'])>1: domain_groups.sort(key=lambda g:-unknown_cover[g['key'][2:]])
    return {'schema':config,'groups':[group('type'),*domain_groups,*(group(k) for k in ('ruleset','origin','source'))],
            'total':total,'scope_total':scope_total,'without':dict(without),**({'probe_base':always} if query.probing else {})}


VERSION_FIELDS = ('id','name','name_zh','name_en','ruleset','source_ids','source_names','fields','field_conflicts','quickref','legacy')


def stack_members(alias='m'):
    """Membership of same-name candidate groups whose names identify one published object."""
    kinds=','.join("'"+k+"'" for k in browse.STACKABLE_TYPES)
    return (f"{alias}(stack, entry_id, status) AS (SELECT g.group_id, j.value, g.status FROM identity_candidates g, "
            f"json_each(g.data,'$.entry_ids') j WHERE g.type IN ({kinds}))")


_RANK_LOCK = threading.Lock()
_STACKED_RANKINGS: OrderedDict = OrderedDict()
_TYPE_ORDER_RANK = {t: i for i, t in enumerate(browse.TYPE_ORDER)}


def _db_signature(db):
    try:
        rows_list = db.execute('PRAGMA database_list').fetchall()
    except sqlite3.Error:
        return None
    file_path = next((r[2] for r in rows_list if r[1] == 'main'), '')
    if not file_path:
        return None
    try:
        st = os.stat(file_path)
    except OSError:
        return None
    return (file_path, st.st_ino, st.st_size, st.st_mtime_ns)


@lru_cache(maxsize=4)
def _load_rank_index_cached(rank_path: str, rank_sig: tuple, db_sig: tuple | None):
    raw = json.loads(gzip.decompress(Path(rank_path).read_bytes()).decode('utf-8'))
    types_lut = raw['types']
    paths_lut = raw['paths']
    sources_lut = raw['sources']
    statuses_lut = raw['statuses']
    origins = raw['origins']
    parsed = []
    for row in raw['rows']:
        eid, ti, name, nz, name_en, rs, gid, sti, pi, start, src_val = row
        name_zh = name if nz is None else nz
        if isinstance(src_val, list):
            sids = tuple(sources_lut[i] for i in src_val)
            work = sids[0] if sids else None
        elif src_val >= 0:
            sid = sources_lut[src_val]
            sids = (sid,)
            work = sid
        else:
            sids = ()
            work = None
        parsed.append({
            'id': eid,
            'type': types_lut[ti],
            'name': name,
            'name_zh': name_zh,
            'name_en': name_en,
            'ruleset': rs,
            'stack': gid,
            'status': statuses_lut[sti] if sti >= 0 else None,
            'path': paths_lut[pi] if pi >= 0 else '',
            'start': start or 0,
            'work': work,
            'sources': frozenset(sids),
            'origin': origins.get(work) or '待核',
            'lower_name': _ascii_lower(name),
        })
    return parsed


def load_rank_index(db):
    rows_list = db.execute('PRAGMA database_list').fetchall()
    file_path = rows_list[0]['file'] if rows_list and rows_list[0]['file'] else ''
    if not file_path:
        return None
    rank_file = Path(file_path).with_name('rank-index.json.gz')
    try:
        r_st = rank_file.stat()
    except OSError:
        return None
    return _load_rank_index_cached(str(rank_file), (r_st.st_ino, r_st.st_size, r_st.st_mtime_ns), _db_signature(db))


def _filter_rank_index(index_rows, types, rulesets, sources, terms):
    if types == set() or rulesets == set() or sources == set():
        return []
    out = []
    for r in index_rows:
        if types is not None and r['type'] not in types:
            continue
        if rulesets is not None and r['ruleset'] not in rulesets:
            continue
        if sources is not None and not (r['sources'] & sources):
            continue
        if terms:
            lname = r['lower_name']
            if any(term not in lname for term in terms):
                continue
        out.append(r)
    return out


def _sort_rank_rows(matched_rows, params, default_sort):
    sort = browse.sort_key(params, default_sort)
    direction = first(params, 'dir', 'asc')
    if direction not in {'asc', 'desc'}:
        raise ValueError('无效排序方向')
    reverse = direction == 'desc'
    if sort == browse.MATCH:
        latest = browse.latest_term(params)
        base_sorted = _sort_rank_rows(matched_rows, {'sort': [default_sort]}, default_sort)
        if base_sorted is None:
            return None
        # In browse.order_sql, MATCH compares match_rank, then length, then default_sort ASC.
        return sorted(
            base_sorted,
            key=lambda r: (
                browse.match_rank(r['name_zh'], r['name_en'], r['name'], latest) * (-1 if reverse else 1),
                len(r['name_zh'] or r['name']),
            ),
        )
    if sort == 'type':
        key_fn = lambda r: _TYPE_ORDER_RANK.get(r['type'], len(browse.TYPE_ORDER))
        tie_fn = lambda r: (r['name'], r['id'])
    elif sort == 'name':
        key_fn = lambda r: browse.name_key(r['name_zh'] or r['name'])
        tie_fn = lambda r: (r['name'], r['id'])
    elif sort == 'name_en':
        key_fn = lambda r: browse.name_key(r['name_en'] or None)
        tie_fn = lambda r: (r['name'], r['id'])
    elif sort == 'ruleset':
        key_fn = lambda r: r['ruleset']
        tie_fn = lambda r: (r['name'], r['id'])
    elif sort == 'source':
        key_fn = lambda r: r['path']
        tie_fn = lambda r: (r['start'], r['name'], r['id'])
    else:
        return None
    present = []
    missing = []
    for r in matched_rows:
        k = key_fn(r)
        if k is None:
            missing.append(r)
        else:
            present.append((k, r))
    # Sort tie-breaker forward first, then apply stable primary key sort in requested direction.
    present.sort(key=lambda item: tie_fn(item[1]))
    present.sort(key=lambda item: item[0], reverse=reverse)
    missing.sort(key=tie_fn)
    return [item[1] for item in present] + missing


# Results load in batches as the list scrolls; one request may restore up to RESTORE_LIMIT rows at once.
BATCH, RESTORE_LIMIT = 150, 1500


def result_window(params):
    try:offset,limit = int(first(params,'offset','0')),int(first(params,'limit',str(BATCH)))
    except ValueError:raise ValueError('offset 与 limit 必须是整数') from None
    if offset<0:raise ValueError('offset 不能为负数')
    if not 1<=limit<=RESTORE_LIMIT:raise ValueError(f'每次读取 1 至 {RESTORE_LIMIT} 行')
    return offset,limit


def _query_cache_key(db, params, versions, prefer, restrict):
    db_sig = _db_signature(db)
    if db_sig is None:
        return None
    q_clean = Query(
        params.tree,
        {k: v for k, v in params.options.items() if k not in {'offset', 'limit', 'entry', 'tab'}},
    ).key()
    return (db_sig, q_clean, versions, prefer, restrict)


_DIRECT_SORTS = frozenset({'type', 'name', 'name_en', 'ruleset', 'source'})


def _try_rank_order(db, params, config, restrict):
    sort_now = browse.sort_key(params, config['default_sort'])
    if sort_now not in _DIRECT_SORTS and not (sort_now == browse.MATCH and config['default_sort'] in _DIRECT_SORTS):
        return None
    allowed_types = set(TYPES) - {'section'}
    allowed_rulesets = set(COMMON_CHOICES['ruleset'][1])
    simple = simple_rank_filter(params, allowed_types, allowed_rulesets, restrict=restrict)
    if simple is None:
        return None
    rank_rows = load_rank_index(db)
    if rank_rows is None:
        return None
    return _sort_rank_rows(_filter_rank_index(rank_rows, *simple), params, config['default_sort'])


def query(db, params, export=False, restrict=None):
    params = Query.of(params)
    where, values = filters(db, params, restrict)
    names = bool(browse.search_terms(params) or browse.typing_term(params))
    def section_hint(total):
        if export or first(params,'sections')=='1' or total or not names:return 0
        return unclassified_matches(db,params,restrict)
    latest = browse.latest_term(params)
    db.create_function('browse_query',0,lambda:latest)
    config = browse.schema_for(entry_types(params), expression.open_types(params.where))
    order = browse.order_sql(params,config['default_sort'])
    offset, limit = result_window(params)
    versions = first(params,'versions','stacked')
    if versions not in {'stacked','each'}:raise ValueError('无效版本显示方式')
    prefer = first(params,'prefer','5r')
    if prefer not in browse.RULESET_PREFERENCES:raise ValueError('无效规则版本偏好')
    from_sql, (where_sql, order_sql) = _from_and_bind(db, where, order)
    # Export always lists every matching version as its own row.
    if export or versions=='each':
        if not export:
            ordered = _try_rank_order(db, params, config, restrict)
            if ordered is not None:
                total = len(ordered)
                ids = [r['id'] for r in ordered[offset:offset + limit]]
                fetched = rows(db, ids) if ids else {}
                result_rows = [fetched[i] for i in ids if i in fetched]
                return {'total':total,'versions_total':total,'versions':versions,'prefer':prefer,'offset':offset,'rows':result_rows,'schema':config,
                        'section_matches':section_hint(total)}
        total = db.execute(f'SELECT count(*) FROM {from_sql}{where_sql}',values).fetchone()[0]
        if not export:
            ids = [r[0] for r in db.execute(f'SELECT e.id FROM {from_sql}{where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?', values+[limit,offset])]
            fetched = rows(db, ids) if ids else {}
            result_rows = [fetched[i] for i in ids if i in fetched]
            return {'total':total,'versions_total':total,'versions':versions,'prefer':prefer,'offset':offset,'rows':result_rows,'schema':config,
                    'section_matches':section_hint(total)}
        use_split = _has_table(db, 'entry_core') and 'e.data' not in where
        if use_split:
            need_export_fields = _has_table(db, 'entry_fields') and ('e.fields' in where or 'e.fields' in order)
            export_from = 'entry_core e LEFT JOIN entry_data d ON d.entry_rowid=e.rowid' + (
                ' LEFT JOIN entry_fields ef ON ef.entry_rowid=e.rowid' if need_export_fields else ''
            )
            export_col = 'd.data'
            export_where = where.replace('e.fields', 'ef.fields') if need_export_fields else where
            export_order = order.replace('e.fields', 'ef.fields') if need_export_fields else order
        else:
            export_from, export_col, export_where, export_order = 'entries e', 'e.data', where, order
        sql = f'SELECT {export_col} FROM {export_from}{export_where} ORDER BY {export_order}'
        export_rows = [browse.enrich(contracts.entry(json.loads(r[0]))) for r in db.execute(sql,values)]
        return {'total':total,'versions_total':total,'versions':versions,'prefer':prefer,'offset':0,'rows':export_rows,'schema':config,
                'section_matches':0}

    # Stacking happens inside the matched set: check ranking cache and compact rank-index first.
    cache_key = _query_cache_key(db, params, versions, prefer, restrict)
    cached_ranking = None
    if cache_key is not None:
        with _RANK_LOCK:
            cached_ranking = _STACKED_RANKINGS.get(cache_key)
            if cached_ranking is not None:
                _STACKED_RANKINGS.move_to_end(cache_key)

    if cached_ranking is None:
        first_choice, second_choice = browse.RULESET_PREFERENCES[prefer]
        ordered = _try_rank_order(db, params, config, restrict)
        if ordered is not None:
            pref_map = {first_choice: 0, second_choice: 1}
            groups = {}
            for pos, r in enumerate(ordered):
                skey = r['stack'] or r['id']
                groups.setdefault(skey, []).append((pref_map.get(r['ruleset'], 2), pos, r['id'], r['status']))
            heads_list = []
            members_by_stack = {}
            for skey, items in groups.items():
                items.sort(key=lambda item: (item[0], item[1]))
                heads_list.append((items[0][1], items[0][2], skey, items[0][3]))
                members_by_stack[skey] = [item[2] for item in items]
            heads_list.sort(key=lambda item: item[0])
            cached_ranking = {
                'heads': [(eid, skey, status) for _, eid, skey, status in heads_list],
                'members': members_by_stack,
                'versions_total': len(ordered),
            }

        if cached_ranking is None:
            if _has_table(db, 'identity_candidate_members'):
                matched = db.execute(
                    'WITH matched AS (SELECT e.id, COALESCE(m.group_id, e.id) AS stack, m.status, '
                    'ROW_NUMBER() OVER (ORDER BY '+order_sql+') AS pos, CASE e.ruleset WHEN ? THEN 0 WHEN ? THEN 1 ELSE 2 END AS preference '
                    f'FROM {from_sql} LEFT JOIN identity_candidate_members m ON m.entry_id=e.id{where_sql}) '
                    'SELECT id, stack, status, pos, ROW_NUMBER() OVER (PARTITION BY stack ORDER BY preference, pos) AS rank FROM matched',
                    [first_choice,second_choice]+values).fetchall()
            else:
                matched = db.execute('WITH '+stack_members()+', matched AS (SELECT e.id, COALESCE(m.stack, e.id) AS stack, m.status, '
                    'ROW_NUMBER() OVER (ORDER BY '+order_sql+') AS pos, CASE e.ruleset WHEN ? THEN 0 WHEN ? THEN 1 ELSE 2 END AS preference '
                    f'FROM {from_sql} LEFT JOIN m ON m.entry_id=e.id{where_sql}) '
                    'SELECT id, stack, status, pos, ROW_NUMBER() OVER (PARTITION BY stack ORDER BY preference, pos) AS rank FROM matched',
                    [first_choice,second_choice]+values).fetchall()
            heads_raw = sorted((r for r in matched if r['rank']==1), key=lambda r:r['pos'])
            members_map = {}
            for r in sorted(matched, key=lambda r:(r['stack'], r['rank'])):
                members_map.setdefault(r['stack'], []).append(r['id'])
            cached_ranking = {
                'heads': [(r['id'], r['stack'], r['status']) for r in heads_raw],
                'members': members_map,
                'versions_total': len(matched),
            }

        if cache_key is not None:
            with _RANK_LOCK:
                _STACKED_RANKINGS[cache_key] = cached_ranking
                while len(_STACKED_RANKINGS) > 12:
                    _STACKED_RANKINGS.popitem(last=False)

    heads = cached_ranking['heads']
    members = cached_ranking['members']
    total = len(heads)
    shown = heads[offset:offset+limit]
    ids = [i for _, skey, _ in shown for i in members[skey]]
    data = rows(db, ids) if ids else {}
    out_rows = []
    for head_id, skey, status in shown:
        row = data[head_id]
        stacked = [data[i] for i in members[skey]]
        row['versions'] = [{k:v.get(k) for k in VERSION_FIELDS} for v in stacked]
        row['stack'] = {'id':skey,'status':status} if status else None
        row['version_diffs'] = browse.version_diffs(config,row,stacked[1:])
        out_rows.append(row)
    return {'total':total,'versions_total':cached_ranking['versions_total'],'versions':versions,'prefer':prefer,'offset':offset,'rows':out_rows,'schema':config,
            'section_matches':section_hint(total)}


def _has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _has_column(db, table, column):
    return any(r[1] == column for r in db.execute(f'PRAGMA table_info("{table}")'))


def _entry_table(db):
    return 'entry_core' if _has_table(db, 'entry_core') else 'entries'


def _query_from(db, *fragments):
    if _has_table(db, 'entry_core') and not any('e.data' in fragment for fragment in fragments):
        if _has_table(db, 'entry_fields') and any('e.fields' in fragment for fragment in fragments):
            return 'entry_core e LEFT JOIN entry_fields ef ON ef.entry_rowid=e.rowid'
        return 'entry_core e'
    return 'entries e'


def _from_and_bind(db, *fragments):
    from_sql = _query_from(db, *fragments)
    if 'entry_fields ef' in from_sql:
        return from_sql, tuple(f.replace('e.fields', 'ef.fields') for f in fragments)
    return from_sql, fragments


def _meta_json(db, key):
    if not _has_table(db, 'metadata'):
        return None
    row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row is not None and row[0] else None


def _meta_int(db, key):
    if not _has_table(db, 'metadata'):
        return None
    row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
    return int(row[0]) if row is not None and row[0] else None


def held_types(db):
    rows_meta = _meta_json(db, 'pages_type_ruleset_counts')
    if rows_meta is not None:
        return {t for t, _, c in rows_meta if t != 'section' and c > 0}
    return {r[0] for r in db.execute(f"SELECT DISTINCT type FROM {_entry_table(db)} WHERE type!='section'")}


def has(db, eid):
    if not eid:
        return False
    table = _entry_table(db)
    return db.execute(f'SELECT 1 FROM {table} WHERE id=? LIMIT 1', (eid,)).fetchone() is not None


def _source_counts(db):
    cached = _meta_json(db, 'pages_source_counts')
    if cached is not None:
        return cached
    table = _entry_table(db)
    return dict(db.execute(
        f"SELECT es.source_id, count(*) FROM entry_sources es JOIN {table} e ON e.id=es.entry_id "
        "WHERE e.type!='section' GROUP BY es.source_id"
    ))


def works(db):
    """Every work (book or other source) of this catalog with its origin, rules generation and entry count:
    what the reader's library can include or leave out. `codes`, `abbreviations` and `titles` are the names
    another collection may know the same work by: 5etools source codes this build aligned by its own votes,
    the book's abbreviations, and its titles and aliases."""
    codes = {}
    if _has_table(db, 'counterpart_sources'):
        for source_id, code in db.execute("SELECT source_id, code FROM counterpart_sources WHERE status='上下文推得' AND code!=''"):
            codes.setdefault(source_id, []).append(code)
    counts = _source_counts(db)
    out = []
    for r in db.execute('SELECT id, name, origin, ruleset_hint, data FROM sources ORDER BY name'):
        data = json.loads(r['data'])
        titles = [r['name'], r['name'].split(' / ')[-1], *(data.get('aliases') or [])]
        out.append({'id':r['id'],'name':r['name'],'label':data.get('label') or r['name'],'origin':r['origin'] or '待核',
                    'ruleset':r['ruleset_hint'] if r['ruleset_hint'] in {'5e','5r'} else '待核','group':data.get('group') or '','count':counts.get(r['id'], 0),
                    'codes':sorted(codes.get(r['id'], [])),'abbreviations':list(data.get('abbreviations') or []),
                    'titles':list(dict.fromkeys(t for t in titles if t)),
                    # The Stella contents node that opens this work's book.
                    'reader_root':data.get('toc_node') or None})
    return out


# One cheap row per matching version, in this catalog's own order: what a library spanning several
# catalogs needs to sort, stack and page the versions before reading the shown rows in full.
def ranked(db, params, restrict=None, sort_field=None):
    params = bounded(Query.of(params))
    if sort_field is None:
        allowed_types = set(TYPES) - {'section'}
        allowed_rulesets = set(COMMON_CHOICES['ruleset'][1])
        simple = simple_rank_filter(params, allowed_types, allowed_rulesets, restrict=restrict)
        if simple is not None and (rank_rows := load_rank_index(db)) is not None:
            return [
                {
                    'id': r['id'], 'type': r['type'], 'name': r['name'], 'name_zh': r['name_zh'],
                    'name_en': r['name_en'], 'ruleset': r['ruleset'], 'stack': r['stack'], 'value': None,
                    'source_key': (r['path'], r['start']), 'origin': r['origin'], 'work': r['work'],
                    'base': None, 'base_work': None, 'base_name_en': None,
                }
                for r in _filter_rank_index(rank_rows, *simple)
            ]
    where, values = filters(db, params, restrict)
    latest = browse.latest_term(params)
    db.create_function('browse_query', 0, lambda: latest)
    origin_of = dict(db.execute('SELECT id, origin FROM sources'))
    value = f"browse_rank(e.fields,'{sort_field}')" if sort_field else 'NULL'
    from_sql, (where, value) = _from_and_bind(db, where, value)
    work_col = (
        'e.primary_source_id AS work'
        if _has_table(db, 'entry_core') and _has_column(db, 'entry_core', 'primary_source_id')
        else '(SELECT es.source_id FROM entry_sources es WHERE es.entry_id=e.id LIMIT 1) AS work'
    )
    if _has_table(db, 'identity_candidate_members'):
        sql = (f'SELECT e.id, e.type, e.name, e.name_zh, e.name_en, e.ruleset, m.group_id AS stack, {value} AS value, '
               f'e.page_path AS path, e.start AS start, {work_col} '
               f'FROM {from_sql} LEFT JOIN identity_candidate_members m ON m.entry_id=e.id{where}')
    else:
        sql = ('WITH '+stack_members()+f', ranked AS (SELECT e.id, e.type, e.name, e.name_zh, e.name_en, e.ruleset, m.stack, {value} AS value, '
               f'e.page_path AS path, e.start AS start, {work_col} '
               f'FROM {from_sql} LEFT JOIN m ON m.entry_id=e.id{where}) SELECT * FROM ranked')
    # The book records no entry as a copy of another, so no version here names a base.
    return [{'id':r['id'],'type':r['type'],'name':r['name'],'name_zh':r['name_zh'],'name_en':r['name_en'],'ruleset':r['ruleset'],
             'stack':r['stack'],'value':r['value'],'source_key':(r['path'] or '',r['start'] or 0),
             'origin':origin_of.get(r['work']) or '待核',
             'work':r['work'],'base':None,'base_work':None,'base_name_en':None} for r in db.execute(sql, values)]


def same_name(db, entry_type, folded, include_ids=()):
    """Cheap ranked rows for entries matching a normalized English name (plus explicit IDs)."""
    table = _entry_table(db)
    ids = set(include_ids)
    if folded and entry_type in browse.STACKABLE_TYPES:
        if _has_table(db, 'name_fold_index'):
            ids.update(r[0] for r in db.execute(
                'SELECT entry_id FROM name_fold_index WHERE type=? AND folded=?',
                (entry_type, folded),
            ))
        else:
            from anna.presentation.library import name_fold
            for eid, name_en in db.execute(
                f"SELECT id, name_en FROM {table} WHERE type=? AND name_en IS NOT NULL AND name_en!=''",
                (entry_type,),
            ):
                if name_fold(name_en) == folded:
                    ids.add(eid)
    if not ids:
        return []
    ordered_ids = list(dict.fromkeys(ids))
    has_members = _has_table(db, 'identity_candidate_members')
    has_primary_src = table == 'entry_core' and _has_column(db, 'entry_core', 'primary_source_id')
    origin_of = dict(db.execute('SELECT id, origin FROM sources')) if has_primary_src else None
    out = []
    for start in range(0, len(ordered_ids), 400):
        batch = ordered_ids[start:start + 400]
        ph = ','.join('?' for _ in batch)
        src_cols = (
            'e.primary_source_id AS work'
            if has_primary_src else
            '(SELECT s.origin FROM entry_sources es JOIN sources s ON s.id=es.source_id WHERE es.entry_id=e.id LIMIT 1) AS origin, '
            '(SELECT es.source_id FROM entry_sources es WHERE es.entry_id=e.id LIMIT 1) AS work'
        )
        if has_members:
            sql = (
                'SELECT e.id, e.type, e.name, e.name_zh, e.name_en, e.ruleset, m.group_id AS stack, '
                f'e.page_path AS path, e.start AS start, {src_cols} '
                f'FROM {table} e LEFT JOIN identity_candidate_members m ON m.entry_id=e.id '
                f'WHERE e.id IN ({ph})'
            )
        else:
            sql = (
                'WITH ' + stack_members() + ' '
                'SELECT e.id, e.type, e.name, e.name_zh, e.name_en, e.ruleset, m.stack AS stack, '
                f'e.page_path AS path, e.start AS start, {src_cols} '
                f'FROM {table} e LEFT JOIN m ON m.entry_id=e.id '
                f'WHERE e.id IN ({ph})'
            )
        for r in db.execute(sql, batch):
            work = r['work']
            origin = (origin_of.get(work) or '待核') if has_primary_src else (r['origin'] or '待核')
            out.append({
                'id': r['id'], 'type': r['type'], 'name': r['name'], 'name_zh': r['name_zh'],
                'name_en': r['name_en'], 'ruleset': r['ruleset'], 'stack': r['stack'], 'value': None,
                'source_key': (r['path'] or '', r['start'] or 0), 'origin': origin,
                'work': work, 'base': None, 'base_work': None, 'base_name_en': None,
            })
    return out


def rows(db, ids):
    """Full table rows for the given version IDs, keyed by ID."""
    out = {}
    ids = list(dict.fromkeys(ids))
    has_core = _has_table(db, 'entry_core')
    for start in range(0,len(ids),400):
        batch = ids[start:start+400]
        ph = ','.join('?' for _ in batch)
        sql = (
            f'SELECT e.id, d.data FROM entry_core e LEFT JOIN entry_data d ON d.entry_rowid=e.rowid WHERE e.id IN ({ph})'
            if has_core else
            f'SELECT id, data FROM entries WHERE id IN ({ph})'
        )
        out.update((r[0],browse.enrich(contracts.entry(json.loads(r[1])))) for r in db.execute(sql,batch))
    return out


def meta(db):
    summary = json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    counts = _source_counts(db)
    sources = []
    for r in db.execute('SELECT id, data FROM sources ORDER BY name'):
        s=json.loads(r['data']); sources.append({k:s[k] for k in ['id','name','aliases','coverage']}|{'short_name':s['label'],'count':counts.get(r['id'], 0)})
    held = held_types(db)
    return {'summary':summary,'types':TYPES,'fields':{**FIELDS,**browse.COLUMN_LABELS},'sources':sources,
            # Only types the data holds are offered; the picker lists them in reading order.
            'type_order':[t for t in browse.TYPE_ORDER if t!='section' and t in held],'presets':list(browse.PRESETS),
            'type_conditions':{t:browse.type_conditions(t) for t in TYPES if t!='section'}}


def _build_version_compare(group_id, status, members_rows):
    order = list(dict.fromkeys(k for r in members_rows for k in browse.field_order(r) if k not in browse.METADATA_FIELDS))
    fields = []
    for k in order:
        known = {browse.comparable(r['fields'], k) for r in members_rows} - {None}
        fields.append({'field': k, 'values': [r['fields'].get(k) for r in members_rows], 'differs': len(known) > 1})
    return {'group_id': group_id, 'status': status,
            'members': [{k: r.get(k) for k in ('id', 'name', 'name_zh', 'name_en', 'ruleset', 'source_ids', 'source_names', 'legacy', 'fields', 'quickref')}
                        for r in map(browse.enrich, members_rows)],
            'fields': fields}


def version_compare(db, entry):
    """All versions in the entry's candidate group, independent of the current filters, field by field."""
    has_core = _has_table(db, 'entry_core')
    if _has_table(db, 'identity_candidate_members'):
        group = db.execute('SELECT group_id AS stack, status FROM identity_candidate_members WHERE entry_id=?', (entry['id'],)).fetchone()
        if not group:
            return None
        if has_core:
            sql = (
                'SELECT d.data FROM identity_candidate_members m '
                'JOIN entry_core e ON e.id=m.entry_id LEFT JOIN entry_data d ON d.entry_rowid=e.rowid '
                'WHERE m.group_id=? '
                "ORDER BY CASE e.ruleset WHEN '5r' THEN 0 WHEN '5e' THEN 1 ELSE 2 END, e.page_path, e.start, e.id"
            )
        else:
            sql = (
                'SELECT e.data FROM identity_candidate_members m JOIN entries e ON e.id=m.entry_id WHERE m.group_id=? '
                "ORDER BY CASE e.ruleset WHEN '5r' THEN 0 WHEN '5e' THEN 1 ELSE 2 END, e.page_path, e.start, e.id"
            )
        members_rows = [contracts.entry(json.loads(r[0])) for r in db.execute(sql, (group['stack'],))]
    else:
        group=db.execute('WITH '+stack_members()+' SELECT stack, status FROM m WHERE entry_id=?',(entry['id'],)).fetchone()
        if not group:return None
        data_col = 'd.data' if has_core else 'e.data'
        from_join = 'm JOIN entry_core e ON e.id=m.entry_id LEFT JOIN entry_data d ON d.entry_rowid=e.rowid' if has_core else 'm JOIN entries e ON e.id=m.entry_id'
        members_rows=[contracts.entry(json.loads(r[0])) for r in db.execute(
            'WITH '+stack_members()+f' SELECT {data_col} FROM {from_join} WHERE m.stack=? '
            "ORDER BY CASE e.ruleset WHEN '5r' THEN 0 WHEN '5e' THEN 1 ELSE 2 END, e.page_path, e.start, e.id",(group['stack'],))]
    return _build_version_compare(group['stack'], group['status'], members_rows)



def detail(db, eid):
    if _has_table(db, 'entry_core'):
        row = db.execute('SELECT d.data FROM entry_core e LEFT JOIN entry_data d ON d.entry_rowid=e.rowid WHERE e.id=?', (eid,)).fetchone()
    else:
        row = db.execute('SELECT data FROM entries WHERE id=?',(eid,)).fetchone()
    if not row: raise LookupError('条目不存在')
    e=browse.enrich(contracts.entry(json.loads(row[0])))
    e['field_order']=browse.field_order(e)
    start=text_start(e)
    e['body']=db.execute('SELECT substr(text,?,?) FROM pages WHERE path=?',(start+1,e['end']-start,e['page_path'])).fetchone()[0].strip()
    e['relations']=[dict(r) for r in db.execute('SELECT predicate,object_id,target_label,status FROM relations WHERE subject_id=? ORDER BY predicate,target_label',(eid,))]
    e['versions']=version_compare(db,e)
    return e


def versions(db, eid):
    """Lightweight version-comparison slice for an entry without reading body text, relations or full JSON."""
    if not eid:
        raise LookupError('条目不存在')
    table = _entry_table(db)
    row = db.execute(f'SELECT id, type, name_en FROM {table} WHERE id=?', (eid,)).fetchone()
    if not row:
        raise LookupError('条目不存在')
    entry = {'id': row['id'], 'type': row['type'], 'name_en': row['name_en'] or ''}
    entry['versions'] = version_compare(db, entry)
    return entry

def format_csv_bytes(headers, row_iter):
    """Write CSV headers and row iterables with formula-injection guarding and a UTF-8 BOM."""
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(headers)
    for cells in row_iter:
        writer.writerow(["'" + s if s.lstrip().startswith(('=', '+', '-', '@')) else s for s in map(str, cells)])
    return ('\ufeff' + out.getvalue()).encode('utf-8')


def csv_export(rows, config=None):
    extras = [f for f in (config or browse.configure(browse.GENERIC))['export_columns'] if f not in browse.IDENTITY_COLUMNS]
    headers = ['版本ID', '中文名', '英文名', '类型', '版本', '来源', '分类依据', '旧版标记', '字段冲突', '正文路径'] + [
        browse.COLUMN_LABELS.get(f, f) for f in extras
    ]

    def iter_rows():
        for r in rows:
            cells = [
                r['id'], r['name_zh'], r['name_en'], TYPES.get(r['type'], r['type']), r['ruleset'],
                '；'.join(r['source_names']), r['classification_status'], r['legacy'],
                '；'.join(r['field_conflicts']), r['page_path'],
            ]
            cells.extend(
                json.dumps(r['fields'].get(f), ensure_ascii=False)
                if isinstance(r['fields'].get(f), (list, dict))
                else str(r['fields'].get(f, ''))
                for f in extras
            )
            yield cells

    return format_csv_bytes(headers, iter_rows())
