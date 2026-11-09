#!/usr/bin/env python3
"""Loopback-only, read-only browser for the extracted SQLite catalog."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import html
import io
import json
import re
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, quote
import browse
import paths
from entry_layout import entry_layout, text_start
from references import query_references, query_decisions, query_missing_definitions
from review_store import load_events
from review_search import search_candidates

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data/extracted/catalog.sqlite'
WEB = ROOT / 'web'
TYPES = dict(class_='职业', subclass='子职', class_feature='职业能力', species='种族物种', background='背景', feat='专长', spell='法术', equipment='普通装备', magic_item='魔法物品', creature='生物数据卡', reward='奖励', vehicle='载具', animal_offer='动物购置', facility='设施', condition='状态', action='动作', rule='规则', trap='陷阱', poison='毒药', disease='疾病', hazard='环境危害', subsystem='扩展机制', lore='资料索引', section='未分类段落')
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


def conditions(params):
    """Every condition a request sets, as (key, SQL, values). Values within one condition are alternatives;
    an entry matches when it meets every condition. The keys name the URL parameter (one per name search,
    `q0`, `q1`, …), so counts can say what each condition removes."""
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
    index = {key:i for i,(key,_,_) in enumerate(conds)}
    type_bound = {i:key[2:] if key.startswith('f_') else key for i,(key,_,_) in enumerate(conds) if key=='cr' or key.startswith('f_')}
    answers = {t:set(browse.type_conditions(t)) for t in TYPES}
    columns = ''.join(f', ({sql}) AS c{i}' for i,(_,sql,_) in enumerate(conds))
    origin_of = dict(db.execute('SELECT id, origin FROM sources'))
    domain = [*config['facets'],*config['flags']]
    counted = ['type',*('f_'+f for f in domain),'ruleset','origin','source','legacy','status']
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
        in_scope = index.get('type') not in failed
        # Choosing a type drops the type-specific conditions it cannot answer, so its count ignores those too.
        answerable = answers[row['type']]
        counts_type = all(conds[i][0]=='type' or (i in type_bound and type_bound[i] not in answerable) for i in failed)
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
        for key in counted[1:]:
            if not failed or (len(failed)==1 and index.get(key)==failed[0]):
                counts[key].update(values(key))
    for key in counted:
        # A chosen value stays listed even when nothing in scope holds it, so it can be unchosen.
        universe[key].update(v for v in params.get(key,[]) if v!=browse.NONE)
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
    return {'schema':config,'groups':[group('type'),*domain_groups,*(group(k) for k in ('ruleset','origin','source','legacy','status'))],
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
        rows = [browse.enrich(json.loads(r[0])) for r in db.execute(sql,values)]
        return {'total':total,'versions_total':total,'versions':versions,'prefer':prefer,'offset':0 if export else offset,'rows':rows,'schema':config}
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
        data.update((r[0],browse.enrich(json.loads(r[1]))) for r in db.execute('SELECT id, data FROM entries WHERE id IN ('+','.join('?' for _ in batch)+')',batch))
    rows = []
    for head in shown:
        row = data[head['id']]
        stacked = [data[i] for i in members[head['stack']]]
        row['versions'] = [{k:v.get(k) for k in VERSION_FIELDS} for v in stacked]
        row['stack'] = {'id':head['stack'],'status':head['status']} if head['status'] else None
        row['version_diffs'] = browse.version_diffs(config,row,stacked[1:])
        rows.append(row)
    return {'total':total,'versions_total':len(matched),'versions':versions,'prefer':prefer,'offset':offset,'rows':rows,'schema':config}


def meta(db):
    summary = json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    sources = []
    for r in db.execute('SELECT s.data,count(e.id) AS n FROM sources s LEFT JOIN entry_sources es ON es.source_id=s.id LEFT JOIN entries e ON e.id=es.entry_id AND e.type!=\'section\' GROUP BY s.id ORDER BY s.name'):
        s=json.loads(r['data']); sources.append({k:s[k] for k in ['id','name','aliases','coverage']}|{'short_name':s['label'],'count':r['n']})
    held = {r[0] for r in db.execute('SELECT DISTINCT type FROM entries')}
    review = db.execute("SELECT value FROM metadata WHERE key='review_snapshot'").fetchone()
    review = json.loads(review[0]) if review else None
    if review: review['pending_rebuild'] = load_events(review['path'])[1] != review['sha256']
    return {'summary':summary,'review_snapshot':review,'types':TYPES,'fields':{**FIELDS,**browse.COLUMN_LABELS},'sources':sources,
            # Only types the data holds are offered; the picker lists them in reading order.
            'type_order':[t for t in browse.TYPE_ORDER if t!='section' and t in held],'presets':list(browse.PRESETS),
            'type_conditions':{t:browse.type_conditions(t) for t in TYPES if t!='section'}}


def version_compare(db, entry):
    """All versions in the entry's candidate group, independent of the current filters, field by field."""
    group=db.execute('WITH '+stack_members()+' SELECT stack, status FROM m WHERE entry_id=?',(entry['id'],)).fetchone()
    if not group:return None
    rows=[json.loads(r[0]) for r in db.execute('WITH '+stack_members()+' SELECT e.data FROM m JOIN entries e ON e.id=m.entry_id WHERE m.stack=? '
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
    e=browse.enrich(json.loads(row[0]))
    e['field_order']=browse.field_order(e)
    start=text_start(e)
    e['body']=db.execute('SELECT substr(text,?,?) FROM pages WHERE path=?',(start+1,e['end']-start,e['page_path'])).fetchone()[0].strip()
    e['facts']=[]
    for r in db.execute('SELECT field,value,evidence,rule,scope,status FROM facts WHERE entry_id=? ORDER BY field,id',(eid,)):
        f=dict(r); f['value']=json.loads(f['value']); f['evidence']=json.loads(f['evidence'])
        ev=f['evidence']
        if ev.get('path') and ev.get('span'):
            a,b=ev['span']; snippet=db.execute('SELECT substr(text,?,?) FROM pages WHERE path=?',(a+1,min(b-a,1200),ev['path'])).fetchone()
            if snippet: f['excerpt']=snippet[0]
        e['facts'].append(f)
    e['relations']=[dict(r) for r in db.execute('SELECT predicate,object_id,target_label,status FROM relations WHERE subject_id=? ORDER BY predicate,target_label',(eid,))]
    e['versions']=version_compare(db,e)
    e['concept_observations']=[]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='concept_observations'").fetchone():
        e['concept_observations']=[json.loads(r[0]) for r in db.execute("SELECT data FROM concept_observations WHERE page_path=? AND start>=? AND end<=? ORDER BY start,signal",(e['page_path'],e['start'],e['end']))]
        ids=sorted({cid for o in e['concept_observations'] if not o['target_id'] for cid in o['candidates']})
        candidates={}
        for start in range(0,len(ids),400):
            batch=ids[start:start+400]
            candidates.update((r['id'],dict(r)) for r in db.execute('SELECT id,name,ruleset FROM entries WHERE id IN ('+','.join('?' for _ in batch)+')',batch))
        for o in e['concept_observations']:
            o['candidate_entries']=[candidates[cid] for cid in o['candidates'] if cid in candidates] if not o['target_id'] else []
    e['missing_definitions']=query_missing_definitions(db,path=e['page_path'],limit=300)['rows'] if db.execute("SELECT 1 FROM sqlite_master WHERE name='missing_definitions'").fetchone() else []
    e['reviews']=[json.loads(r[0]) for r in db.execute('SELECT data FROM review_queue WHERE target IN (?,?) ORDER BY priority,reason',(eid,e['page_path']))]
    e['effective_references']=[]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='effective_references'").fetchone():
        e['effective_references']=[json.loads(r[0]) for r in db.execute(
            'SELECT data FROM effective_references WHERE page_path=? AND start>=? AND end<=? ORDER BY start,id',
            (e['page_path'],e['start'],e['end']))]
        ids=sorted({cid for r in e['effective_references'] if not r['target_id'] for cid in r['candidates']})
        candidates={}
        for start in range(0,len(ids),400):
            batch=ids[start:start+400]
            candidates.update((r['id'],dict(r)) for r in db.execute('SELECT id,name,ruleset FROM entries WHERE id IN ('+','.join('?' for _ in batch)+')',batch))
        for ref in e['effective_references']:
            ref['candidate_entries']=[candidates[cid] for cid in ref['candidates'] if cid in candidates]
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


class Handler(BaseHTTPRequestHandler):
    db_path=DB
    book_url=None
    db_signature=None
    source_root=paths.WEB
    def log_message(self,*args): pass
    def send(self,body,kind='application/json; charset=utf-8',status=200,download=False):
        if not isinstance(body,bytes): body=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if download: self.send_header('Content-Disposition','attachment; filename="5e-anna.csv"')
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        # Reject DNS-rebinding hosts; never expose local data to a remote origin.
        if self.headers.get('Host','') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
            self.send({'error':'仅限本机访问'},status=403); return
        url=urlsplit(self.path); p=parse_qs(url.query)
        try:
            if self.db_signature is not None:
                stat=Path(self.db_path).stat()
                if (stat.st_ino,stat.st_size,stat.st_mtime_ns)!=self.db_signature:
                    self.send({'error':'数据库已重建，请重启 make up 以同步链接版。'},status=409);return
            static={'/link-labels.js':('link-labels.js','text/javascript; charset=utf-8'),'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/detail.js':('detail.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
            static['/review']=('review.html','text/html; charset=utf-8')
            static['/review-ui.js']=('review-ui.js','text/javascript; charset=utf-8')
            static['/favicon.ico']=('favicon.ico','image/x-icon')
            static['/anna-logo.png']=('anna-logo.png','image/png')
            static['/theme.js']=('theme.js','text/javascript; charset=utf-8')
            static.update({f'/favicon-{size}.png':(f'favicon-{size}.png','image/png') for size in (16,32,48)})
            if url.path in static:
                file,kind=static[url.path]; self.send((WEB/file).read_bytes(),kind); return
            with closing(connect(self.db_path)) as db:
                if url.path=='/api/meta': self.send({**meta(db),'book_url':self.book_url})
                elif url.path=='/api/facets': self.send(facets(db,p))
                elif url.path=='/api/entries': self.send(query(db,p))
                elif url.path=='/api/entry': self.send(detail(db,first(p,'id')))
                elif url.path=='/api/entry-layout': self.send(entry_layout(db,first(p,'id'),Path(self.source_root)))
                elif url.path=='/api/references': self.send(query_references(db,path=first(p,'path'),reference_id=first(p,'id'),
                    status=first(p,'status'),signal=first(p,'signal'),review_status=first(p,'review_status'),
                    limit=int(first(p,'limit','50')),offset=int(first(p,'offset','0'))))
                elif url.path=='/api/reference-candidates': self.send(search_candidates(db,first(p,'id'),query=first(p,'q'),
                    entry_type=first(p,'type'),ruleset=first(p,'ruleset'),source_id=first(p,'source'),
                    limit=int(first(p,'limit','20')),offset=int(first(p,'offset','0'))))
                elif url.path=='/api/missing-definitions': self.send(query_missing_definitions(db,status=first(p,'status'),path=first(p,'path'),
                    limit=int(first(p,'limit','50')),offset=int(first(p,'offset','0'))))
                elif url.path=='/api/review-decisions': self.send(query_decisions(db,status=first(p,'status'),
                    limit=int(first(p,'limit','50')),offset=int(first(p,'offset','0'))))
                elif url.path=='/api/export': self.send(csv_export(query(db,p,export=True)['rows'],browse.schema_for(entry_types(p))),'text/csv; charset=utf-8',download=True)
                elif url.path=='/source':
                    row=db.execute('SELECT text FROM pages WHERE path=?',(first(p,'path'),)).fetchone()
                    if not row: raise LookupError('页面不存在')
                    text=row[0]; start=max(0,min(len(text),int(first(p,'start','0')))); end=max(start,min(len(text),int(first(p,'end',str(start)))))
                    body='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>页面文本 · 5e anna</title><script src="/theme.js"></script><link rel="icon" href="/favicon.ico"><link rel="stylesheet" href="/style.css"><main class="source-page"><a href="/">← 条目查询</a><h1>页面文本</h1><p>提取后的文本；高亮为所选证据范围。</p><p>'+html.escape(first(p,'path'))+'</p><pre>'+html.escape(text[:start])+'<mark id="evidence">'+html.escape(text[start:end])+'</mark>'+html.escape(text[end:])+'</pre></main></html>'
                    self.send(body.encode(),'text/html; charset=utf-8')
                else: raise LookupError('页面不存在')
        except ValueError as exc: self.send({'error':str(exc)},status=400)
        except LookupError as exc: self.send({'error':str(exc)},status=404)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception:
            self.send({'error':'读取失败；请检查数据库并重启服务。'},status=500)
            raise


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--port',type=int,default=8001); ap.add_argument('--db',type=Path,default=DB); args=ap.parse_args()
    if not args.db.is_file(): ap.error('数据库不存在，请先运行 build.py')
    Handler.db_path=args.db
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'5e anna · http://127.0.0.1:{server.server_port}/ · 只读；Ctrl-C 停止',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
