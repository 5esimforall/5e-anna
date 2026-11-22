#!/usr/bin/env python3
"""Catalog response models, filtering and exports over the published SQLite snapshot."""
from __future__ import annotations
from collections import Counter
import csv
import io
import json
import re
import sqlite3
from pathlib import Path
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


def connect(path=DB):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
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


def base_clause(params, restrict=None):
    """Untyped sections join the results only on request; with the library scope this bounds every query and count."""
    clauses, values = restriction_clause(restrict)
    attribute, attributed = attribute_clause(params)
    return ' AND '.join(["e.type!='section'" if first(params,'sections')!='1' else '1', *clauses, *attribute]), values+attributed


def unclassified_matches(db, params, restrict=None):
    """Untyped sections that match every current condition, for an empty name search's follow-up.

    The same conditions apply, including any type filter: a section cannot stand in for a chosen
    type, so the offer appears only when adding sections would actually return rows.
    """
    query = Query.of(params)
    scope, scoped = restriction_clause(restrict)
    attribute, attributed = attribute_clause(query)
    sql, values = expression.sql(query.where, card_condition)
    return db.execute('SELECT count(*) FROM entries e WHERE '+' AND '.join(["e.type='section'",*scope,*attribute,sql]),scoped+attributed+values).fetchone()[0]


def bounded(query):
    """A query within the search limits: each list of terms counts across all its cards."""
    browse.search_terms(query);browse.body_terms(query)
    return query


def filters(db, params, restrict=None):
    """The WHERE clause for the query's conditions as written, within the library scope."""
    query = bounded(Query.of(params))
    base, scoped = base_clause(query, restrict)
    sql, values = expression.sql(query.where, card_condition)
    return f' WHERE {base} AND {sql}', scoped+values


def facets(db, params, restrict=None):
    """Each field's options with counts. An option counts what the query matches with that field's own cards set
    aside and the option held; while the caret probes (`probe`), what the query matches with the option placed at
    the caret, so a suggestion says what choosing it there returns. `without` counts the matches each card's removal
    would add, naming the card by its place in the line; `scope_total` counts the chosen types."""
    query = bounded(Query.of(params))
    types = entry_types(query)
    config = browse.schema_for(types, expression.open_types(query.where))
    line = query.cards()
    columns = [expression.sql(card,card_condition) for card in line]
    selected = ''.join(f', ({sql}) AS c{card.index}' for card,(sql,_) in zip(line,columns))
    origin_of = dict(db.execute('SELECT id, origin FROM sources'))
    domain = [*config['facets'],*config['flags']]
    counted = ['type',*('f_'+f for f in domain),'ruleset','origin','source']
    counts = {k:Counter() for k in counted}
    universe = {k:set() for k in counted}
    unknown_cover = Counter()
    total = scope_total = always = 0
    without = Counter()
    # One test per distinct tree; a field without cards of its own counts under the query itself.
    tests = {}
    meets = lambda card: (lambda row,name=f'c{card.index}': bool(row[name]))
    def check(node):
        tests.setdefault(node, expression.test(node, meets))
        return node
    where = query.where
    matches = check(where)
    gains = [(str(card.index),check(expression.without(where,card.index))) for card in line]
    in_scope = check(expression.relax(where,lambda c: c.field!='type'))
    if query.probing:
        placed = check(expression.substitute(query.tree,lambda c: expression.TRUE if c is expression.PROBE else c))
        unplaced = check(expression.substitute(query.tree,lambda c: expression.FALSE if c is expression.PROBE else c))
    else:
        aside = {key:check(expression.relax(where,lambda c,key=key: c.field==key)) for key in counted}
    base, scoped = base_clause(query, restrict)
    rows = db.execute('SELECT e.type, e.fields, e.ruleset, e.legacy, e.classification_status, '
        "(SELECT group_concat(es.source_id, char(31)) FROM entry_sources es WHERE es.entry_id=e.id) AS sources"+selected+
        ' FROM entries e WHERE '+base, [v for _,values in columns for v in values]+scoped)
    for row in rows:
        result = {node:t(row) for node,t in tests.items()}
        if not any(result.values()): continue
        parsed = None
        source_ids = row['sources'].split('\x1f') if row['sources'] else []
        def values(key):
            nonlocal parsed
            if key=='type': return [row['type']]
            if key.startswith('f_'):
                parsed = parsed or json.loads(row['fields'])
                return browse.normalized(parsed,key[2:])
            if key=='source': return source_ids
            if key=='origin': return list(dict.fromkeys(origin_of.get(s) for s in source_ids if origin_of.get(s)))
            return [row[{'status':'classification_status'}.get(key,key)]]
        if result[in_scope]:
            scope_total += 1
            for key in counted[1:]: universe[key].update(values(key))
            for f in domain: unknown_cover[f] += values('f_'+f)!=[browse.UNKNOWN]
        if result[matches]: total += 1
        else:
            for name,t in gains:
                if result[t]: without[name] += 1
        if query.probing:
            if result[unplaced]: always += 1
            elif result[placed]:
                for key in counted: counts[key].update(values(key))
        else:
            for key in counted:
                if result[aside[key]]: counts[key].update(values(key))
    for key in counted:
        # A chosen value stays listed even when nothing in scope holds it, so it can be unchosen.
        universe[key].update(v for v in [*params.get(key,[]),*params.get(NOT+key,[])] if v!=browse.NONE)
    held = [r[0] for r in db.execute("SELECT DISTINCT type FROM entries WHERE type!='section'")]
    universe['type'] = set(held)
    sources = {r['id']:json.loads(r['data']) for r in db.execute('SELECT id, data FROM sources')}
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


# Results load in batches as the list scrolls; one request may restore up to RESTORE_LIMIT rows at once.
BATCH, RESTORE_LIMIT = 150, 1500


def result_window(params):
    try:offset,limit = int(first(params,'offset','0')),int(first(params,'limit',str(BATCH)))
    except ValueError:raise ValueError('offset 与 limit 必须是整数') from None
    if offset<0:raise ValueError('offset 不能为负数')
    if not 1<=limit<=RESTORE_LIMIT:raise ValueError(f'每次读取 1 至 {RESTORE_LIMIT} 行')
    return offset,limit


def query(db, params, export=False, restrict=None):
    params = Query.of(params)
    where, values = filters(db,params,restrict)
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
    # Export always lists every matching version as its own row.
    if export or versions=='each':
        total = db.execute('SELECT count(*) FROM entries e'+where,values).fetchone()[0]
        sql = 'SELECT e.data FROM entries e'+where+' ORDER BY '+order
        if not export: sql+=' LIMIT ? OFFSET ?'; values=values+[limit,offset]
        rows = [browse.enrich(contracts.entry(json.loads(r[0]))) for r in db.execute(sql,values)]
        return {'total':total,'versions_total':total,'versions':versions,'prefer':prefer,'offset':0 if export else offset,'rows':rows,'schema':config,
                'section_matches':section_hint(total)}
    # Stacking happens inside the matched set: filters decide which versions exist, then each
    # candidate group shows its preferred-ruleset version at that version's sorted position.
    first_choice, second_choice = browse.RULESET_PREFERENCES[prefer]
    matched = db.execute('WITH '+stack_members()+', matched AS (SELECT e.id, COALESCE(m.stack, e.id) AS stack, m.status, '
        'ROW_NUMBER() OVER (ORDER BY '+order+') AS pos, CASE e.ruleset WHEN ? THEN 0 WHEN ? THEN 1 ELSE 2 END AS preference '
        'FROM entries e LEFT JOIN m ON m.entry_id=e.id'+where+') '
        'SELECT id, stack, status, pos, ROW_NUMBER() OVER (PARTITION BY stack ORDER BY preference, pos) AS rank FROM matched',
        [first_choice,second_choice]+values).fetchall()
    heads = sorted((r for r in matched if r['rank']==1), key=lambda r:r['pos'])
    total = len(heads)
    shown = heads[offset:offset+limit]
    stacks = {r['stack']:r for r in shown}
    members = {}
    for r in sorted((r for r in matched if r['stack'] in stacks), key=lambda r:(r['stack'],r['rank'])):
        members.setdefault(r['stack'],[]).append(r['id'])
    ids = [i for ids in members.values() for i in ids]
    data = {}
    for start in range(0,len(ids),400):
        batch = ids[start:start+400]
        data.update((r[0],browse.enrich(contracts.entry(json.loads(r[1])))) for r in db.execute('SELECT id, data FROM entries WHERE id IN ('+','.join('?' for _ in batch)+')',batch))
    rows = []
    for head in shown:
        row = data[head['id']]
        stacked = [data[i] for i in members[head['stack']]]
        row['versions'] = [{k:v.get(k) for k in VERSION_FIELDS} for v in stacked]
        row['stack'] = {'id':head['stack'],'status':head['status']} if head['status'] else None
        row['version_diffs'] = browse.version_diffs(config,row,stacked[1:])
        rows.append(row)
    return {'total':total,'versions_total':len(matched),'versions':versions,'prefer':prefer,'offset':offset,'rows':rows,'schema':config,
            'section_matches':section_hint(total)}


def works(db):
    """Every work (book or other source) of this catalog with its origin, rules generation and entry count:
    what the reader's library can include or leave out. `codes`, `abbreviations` and `titles` are the names
    another collection may know the same work by: 5etools source codes this build aligned by its own votes,
    the book's abbreviations, and its titles and aliases."""
    codes = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='counterpart_sources'").fetchone():
        for source_id, code in db.execute("SELECT source_id, code FROM counterpart_sources WHERE status='上下文推得' AND code!=''"):
            codes.setdefault(source_id, []).append(code)
    out = []
    for r in db.execute("SELECT s.id, s.name, s.origin, s.ruleset_hint, s.data, count(e.id) AS n FROM sources s LEFT JOIN entry_sources es ON es.source_id=s.id "
                        "LEFT JOIN entries e ON e.id=es.entry_id AND e.type!='section' GROUP BY s.id ORDER BY s.name"):
        data = json.loads(r['data'])
        titles = [r['name'], r['name'].split(' / ')[-1], *(data.get('aliases') or [])]
        out.append({'id':r['id'],'name':r['name'],'label':data.get('label') or r['name'],'origin':r['origin'] or '待核',
                    'ruleset':r['ruleset_hint'] if r['ruleset_hint'] in {'5e','5r'} else '待核','group':data.get('group') or '','count':r['n'],
                    'codes':sorted(codes.get(r['id'], [])),'abbreviations':list(data.get('abbreviations') or []),
                    'titles':list(dict.fromkeys(t for t in titles if t)),
                    # The Stella contents node that opens this work's book.
                    'reader_root':data.get('toc_node') or None})
    return out


# One cheap row per matching version, in this catalog's own order: what a library spanning several
# catalogs needs to sort, stack and page the versions before reading the shown rows in full.
def ranked(db, params, restrict=None, sort_field=None):
    params = Query.of(params)
    where, values = filters(db, params, restrict)
    latest = browse.latest_term(params)
    db.create_function('browse_query',0,lambda:latest)
    value = f"browse_rank(e.fields,'{sort_field}')" if sort_field else 'NULL'
    sql = ('WITH '+stack_members()+f', ranked AS (SELECT e.id, e.type, e.name, e.name_zh, e.name_en, e.ruleset, m.stack, {value} AS value, '
           "e.page_path AS path, e.start AS start, (SELECT s.origin FROM entry_sources es JOIN sources s ON s.id=es.source_id WHERE es.entry_id=e.id LIMIT 1) AS origin, "
           '(SELECT es.source_id FROM entry_sources es WHERE es.entry_id=e.id LIMIT 1) AS work '
           'FROM entries e LEFT JOIN m ON m.entry_id=e.id'+where+') SELECT * FROM ranked')
    # The book records no entry as a copy of another, so no version here names a base.
    return [{'id':r['id'],'type':r['type'],'name':r['name'],'name_zh':r['name_zh'],'name_en':r['name_en'],'ruleset':r['ruleset'],
             'stack':r['stack'],'value':r['value'],'source_key':(r['path'] or '',r['start'] or 0),'origin':r['origin'] or '待核',
             'work':r['work'],'base':None,'base_work':None,'base_name_en':None} for r in db.execute(sql, values)]


def rows(db, ids):
    """Full table rows for the given version IDs, keyed by ID."""
    out = {}
    ids = list(dict.fromkeys(ids))
    for start in range(0,len(ids),400):
        batch = ids[start:start+400]
        out.update((r[0],browse.enrich(contracts.entry(json.loads(r[1])))) for r in db.execute('SELECT id, data FROM entries WHERE id IN ('+','.join('?' for _ in batch)+')',batch))
    return out


def meta(db):
    summary = json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    sources = []
    for r in db.execute('SELECT s.data,count(e.id) AS n FROM sources s LEFT JOIN entry_sources es ON es.source_id=s.id LEFT JOIN entries e ON e.id=es.entry_id AND e.type!=\'section\' GROUP BY s.id ORDER BY s.name'):
        s=json.loads(r['data']); sources.append({k:s[k] for k in ['id','name','aliases','coverage']}|{'short_name':s['label'],'count':r['n']})
    held = {r[0] for r in db.execute('SELECT DISTINCT type FROM entries')}
    return {'summary':summary,'types':TYPES,'fields':{**FIELDS,**browse.COLUMN_LABELS},'sources':sources,
            # Only types the data holds are offered; the picker lists them in reading order.
            'type_order':[t for t in browse.TYPE_ORDER if t!='section' and t in held],'presets':list(browse.PRESETS),
            'type_conditions':{t:browse.type_conditions(t) for t in TYPES if t!='section'}}


def version_compare(db, entry):
    """All versions in the entry's candidate group, independent of the current filters, field by field."""
    group=db.execute('WITH '+stack_members()+' SELECT stack, status FROM m WHERE entry_id=?',(entry['id'],)).fetchone()
    if not group:return None
    rows=[contracts.entry(json.loads(r[0])) for r in db.execute('WITH '+stack_members()+' SELECT e.data FROM m JOIN entries e ON e.id=m.entry_id WHERE m.stack=? '
        "ORDER BY CASE e.ruleset WHEN '5r' THEN 0 WHEN '5e' THEN 1 ELSE 2 END, e.page_path, e.start, e.id",(group['stack'],))]
    order=list(dict.fromkeys(k for r in rows for k in browse.field_order(r) if k not in browse.METADATA_FIELDS))
    fields=[]
    for k in order:
        known={browse.comparable(r['fields'],k) for r in rows}-{None}
        fields.append({'field':k,'values':[r['fields'].get(k) for r in rows],'differs':len(known)>1})
    return {'group_id':group['stack'],'status':group['status'],
            'members':[{k:r.get(k) for k in ('id','name','name_zh','name_en','ruleset','source_ids','source_names','legacy','fields','quickref')} for r in map(browse.enrich,rows)],
            'fields':fields}



def detail(db, eid):
    row=db.execute('SELECT data FROM entries WHERE id=?',(eid,)).fetchone()
    if not row: raise LookupError('条目不存在')
    e=browse.enrich(contracts.entry(json.loads(row[0])))
    e['field_order']=browse.field_order(e)
    start=text_start(e)
    e['body']=db.execute('SELECT substr(text,?,?) FROM pages WHERE path=?',(start+1,e['end']-start,e['page_path'])).fetchone()[0].strip()
    e['relations']=[dict(r) for r in db.execute('SELECT predicate,object_id,target_label,status FROM relations WHERE subject_id=? ORDER BY predicate,target_label',(eid,))]
    e['versions']=version_compare(db,e)
    return e

def csv_export(rows, config=None):
    out=io.StringIO(newline=''); writer=csv.writer(out)
    extras=[f for f in (config or browse.configure(browse.GENERIC))['export_columns'] if f not in browse.IDENTITY_COLUMNS]
    writer.writerow(['版本ID','中文名','英文名','类型','版本','来源','分类依据','旧版标记','字段冲突','正文路径']+[browse.COLUMN_LABELS.get(f,f) for f in extras])
    for r in rows:
        cells=[r['id'],r['name_zh'],r['name_en'],TYPES.get(r['type'],r['type']),r['ruleset'],'；'.join(r['source_names']),r['classification_status'],r['legacy'],'；'.join(r['field_conflicts']),r['page_path']]
        cells.extend(json.dumps(r['fields'].get(f),ensure_ascii=False) if isinstance(r['fields'].get(f),(list,dict)) else str(r['fields'].get(f,'')) for f in extras)
        writer.writerow(["'"+s if s.lstrip().startswith(('=','+','-','@')) else s for s in map(str,cells)])
    return ('\ufeff'+out.getvalue()).encode('utf-8')
