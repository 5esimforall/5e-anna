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
from anna.presentation import contracts
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


def field_of(key):
    """The field a condition belongs to: `q0`, `not_q1` and `typing` are all the name field `q`."""
    key = key.removeprefix(NOT)
    return 'q' if key=='typing' else re.sub(r'^(q|body)\d+$',r'\1',key)


def conditions(params):
    """Every condition a request sets, as (key, SQL, values). Values within one condition are alternatives;
    an entry matches when it meets every condition. The keys name the URL parameter (one per search term,
    `q0`, `body1`, `not_q0`, …), so counts can say what each condition removes. An excluded condition holds
    for entries that meet none of its values; an entry whose value was not extracted is not excluded."""
    excluded = {k[len(NOT):]:v for k,v in params.items() if k.startswith(NOT)}
    for key in excluded:
        if key not in NEGATABLE and not key.startswith('f_'): raise ValueError('无法排除：'+key)
    return included(params)+[(NOT+key,f'NOT COALESCE(({sql}),0)',values) for key,sql,values in included(excluded)]


def included(params):
    out = []
    types = entry_types(params)
    if types: out.append(('type','e.type IN ('+','.join('?' for _ in types)+')',types))
    # Literal substring searches, not SQL LIKE patterns; each must match the name.
    terms = browse.search_terms(params)
    for i, term in enumerate(terms): out.append((f'q{i}','instr(lower(e.name),lower(?))>0',[term]))
    typing = browse.typing_term(params)
    if typing and typing not in terms: out.append(('typing','instr(lower(e.name),lower(?))>0',[typing]))
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
    field, value = first(params,'field'),first(params,'value')
    if value:
        if field not in FIELDS: raise ValueError('无效属性')
        out.append(('field','EXISTS(SELECT 1 FROM json_each(e.fields,?) f WHERE CAST(f.value AS TEXT)=?)',['$.'+field,{'true':'1','false':'0'}.get(value,value)]))
    out.extend(browse.facet_conditions(params))
    return out


def base_clause(params):
    """Untyped sections join the results only on request; this scopes every query and count."""
    return "e.type!='section'" if first(params,'sections')!='1' else '1'


def unclassified_matches(db, params):
    """Untyped sections that match every current condition, for an empty name search's follow-up.

    The same conditions apply, including any type filter: a section cannot stand in for a chosen
    type, so the offer appears only when adding sections would actually return rows.
    """
    conds = conditions(params)
    clause = "e.type='section'"+(' AND '+' AND '.join(sql for _,sql,_ in conds) if conds else '')
    return db.execute('SELECT count(*) FROM entries e WHERE '+clause,[v for _,_,vals in conds for v in vals]).fetchone()[0]


def filters(db, params):
    conds = conditions(params)
    clauses = [base_clause(params)]+[sql for _,sql,_ in conds]
    return ' WHERE '+' AND '.join(clauses), [v for _,_,vals in conds for v in vals]


def facets(db, params):
    """Each field's options with counts under every other condition: what picking the option would match.
    `without` counts the results if one condition were removed; `scope_total` counts the chosen types."""
    types = entry_types(params)
    config = browse.schema_for(types)
    conds = conditions(params)
    fields_of = [field_of(key) for key,_,_ in conds]
    type_bound = {i:f[2:] if f.startswith('f_') else f for i,f in enumerate(fields_of) if f=='cr' or f.startswith('f_')}
    answers = {t:set(browse.type_conditions(t)) for t in TYPES}
    columns = ''.join(f', ({sql}) AS c{i}' for i,(_,sql,_) in enumerate(conds))
    origin_of = dict(db.execute('SELECT id, origin FROM sources'))
    domain = [*config['facets'],*config['flags']]
    counted = ['type',*('f_'+f for f in domain),'ruleset','origin','source']
    counts = {k:Counter() for k in counted}
    universe = {k:set() for k in counted}
    unknown_cover = Counter()
    total = scope_total = 0
    without = Counter()
    rows = db.execute('SELECT e.type, e.fields, e.ruleset, e.legacy, e.classification_status, '
        "(SELECT group_concat(es.source_id, char(31)) FROM entry_sources es WHERE es.entry_id=e.id) AS sources"+columns+
        ' FROM entries e WHERE '+base_clause(params), [v for _,_,vals in conds for v in vals])
    for row in rows:
        failed = [i for i in range(len(conds)) if not row[f'c{i}']]
        in_scope = all(fields_of[i]!='type' for i in failed)
        # Choosing a type drops the type-specific conditions it cannot answer, so its count ignores those too.
        answerable = answers[row['type']]
        counts_type = all(fields_of[i]=='type' or (i in type_bound and type_bound[i] not in answerable) for i in failed)
        if not in_scope and not counts_type: continue
        fields = json.loads(row['fields']) if in_scope and domain else None
        source_ids = row['sources'].split('\x1f') if row['sources'] else []
        def values(key):
            if key=='type': return [row['type']]
            if key.startswith('f_'): return browse.normalized(fields,key[2:])
            if key=='source': return source_ids
            if key=='origin': return list(dict.fromkeys(origin_of.get(s) for s in source_ids if origin_of.get(s)))
            return [row[{'status':'classification_status'}.get(key,key)]]
        if in_scope:
            scope_total += 1
            for key in counted:
                if key!='type': universe[key].update(values(key))
            for f in domain: unknown_cover[f] += browse.normalized(fields,f)!=[browse.UNKNOWN]
        if not failed: total += 1
        elif len(failed)==1: without[conds[failed[0]][0]] += 1
        if counts_type: counts['type'][row['type']] += 1
        # A field's options count with every other field's conditions kept and its own, chosen or excluded, set aside.
        for key in counted[1:]:
            if all(fields_of[i]==key for i in failed):
                counts[key].update(values(key))
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
        for o in options: o['count'] = counts[key][o['value']]
        label = {'type':'类型','ruleset':'规则版本','origin':'来源性质','source':'来源','legacy':'旧版标记','status':'分类依据'}.get(key) or browse.LABELS[key[2:]]
        mode = 'flag' if key.startswith('f_') and key[2:] in config['flags'] else 'multi'
        return {'key':key,'label':label,'mode':mode,'options':options}
    domain_groups = [group('f_'+f) for f in domain]
    # Mixed types lead with the facets most of their entries can answer (rarity before weapon mastery).
    if len(config['kinds'])>1: domain_groups.sort(key=lambda g:-unknown_cover[g['key'][2:]])
    return {'schema':config,'groups':[group('type'),*domain_groups,*(group(k) for k in ('ruleset','origin','source'))],
            'total':total,'scope_total':scope_total,'without':dict(without)}


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


def query(db, params, export=False):
    where, values = filters(db,params)
    names = bool(browse.search_terms(params) or browse.typing_term(params))
    def section_hint(total):
        if export or first(params,'sections')=='1' or total or not names:return 0
        return unclassified_matches(db,params)
    latest = browse.latest_term(params)
    db.create_function('browse_query',0,lambda:latest)
    config = browse.schema_for(entry_types(params))
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
