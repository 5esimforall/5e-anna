"""Enumerate printed uses independently of reference discovery; verify exact Stella and Anna destinations."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
from functools import lru_cache
from hashlib import file_digest, sha256
import json
from pathlib import Path
import re
import sqlite3

from anna.paths import PROJECT
from anna.publish.linked_book import text_slice
from anna.source.html import Document, split_name
from anna.source.pages import read_page

DICE = r'(?<![\da-zA-Z])\d+[ \t]*[dD][ \t]*\d+(?:[ \t]*[+＋−-][ \t]*\d+)?[ \t]*[)）]?[ \t]*(?:点[ \t]*)?'
FIELDS = {'法术成分':'components','持续时间':'duration'}
COLUMNS = {'伤害':'damage','属性':'properties','词条':'properties','精通':'mastery'}
TIME = r'([\d一二两三四五六七八九十百千]+\s*(?:个\s*)?(?:小时|分钟|星期|天|日|轮|年|月|周))'


def damage_names(db):
    """The printed type/example table supplies names, even if its row entries weren't extracted."""
    labels=set()
    for raw,page,a,b in db.execute("SELECT data,page_path,start,end FROM raw_tables WHERE data LIKE '%例子%'"):
        rows=json.loads(raw)['rows']
        if rows[0]['cells']!=['类型','例子']:continue
        if db.execute("SELECT 1 FROM entries WHERE page_path=? AND name_zh='伤害类型' AND start<=? AND end>=?",(page,a,b)).fetchone():
            labels.update(split_name(row['cells'][0])[0] for row in rows[1:] if len(row['cells'])==2)
    if not labels or '' in labels:
        raise ValueError('没有可核对的伤害类型定义表')
    return re.compile(DICE+'('+'|'.join(map(re.escape,sorted(labels,key=lambda n:(-len(n),n))))+r')[ \t]*伤害')


def value_spans(role,value,duration_prefix):
    depth=0; masked=[]
    for char in value:
        if char in '(（':depth+=1
        masked.append(' ' if depth or char in ')）' else char)
        if char in ')）':depth=max(0,depth-1)
    top=''.join(masked)
    if role=='duration':
        name=duration_prefix.match(top) if duration_prefix else None
        spans=[m.span(1) for m in re.finditer(r'(?:^|或|[，,；;])\s*(?:(?:至多|最多|最长)\s*)?'+TIME,top)]
        if name:yield name.span(1)
        yield from spans
        if not name and not spans and (m:=re.match(r'\s*([^\s,，；;。]+)',top)):yield m.span(1)
        return
    if role=='damage':
        m=re.match(r'\s*\d+(?:[dD]\d+)?(?:\s*[+＋-]\s*\d+)?\s*(?:点\s*)?',top)
        if not m:return
        top=' '*m.end()+top[m.end():]
    yield from (m.span() for m in re.finditer(r'[\u3400-\u9fff]+[0-9]*|[A-Za-z]+(?:-[A-Za-z]+)*',top))


def inventory(tables,text,pattern,spells,duration_prefix):
    for m in pattern.finditer(text):
        yield *m.span(1),'damage_notation'
    for m in re.finditer(r'(?m)^[ \t]*(法术成分|持续时间)[ \t]*[:：][ \t]*([^\n]*)',text):
        if not any(a<=m.start(1)<b for a,b in spells):continue
        role=FIELDS[m[1]]
        yield *m.span(1), role+'_label'
        for a,b in value_spans(role,m[2],duration_prefix):yield m.start(2)+a,m.start(2)+b,role
    for rows in tables:
        headers=None
        for row in rows:
            vals=row['cells']
            if ('伤害' in vals and {'属性','词条'}&set(vals)) or {'法术','学派'}<=set(vals):
                headers=vals;continue
            if not headers or len(vals)!=len(headers) or not vals[0]:continue
            if '法术' in headers:
                for col,h in enumerate(headers):
                    a,b=row['cell_spans'][col];v=text[a:b]
                    if h=='学派' and re.fullmatch(r'\s*[\u3400-\u9fff]+\s*',v):
                        m=re.search(r'\S+',v);yield a+m.start(),a+m.end(),'school'
                    if h=='特殊':
                        for m in re.finditer(r'(?<![a-zA-Z])[A-Z](?![a-zA-Z])',v):yield a+m.start(),a+m.end(),'legend'
                continue
            for col,h in enumerate(headers):
                if h not in COLUMNS:continue
                a,b=row['cell_spans'][col];value=text[a:b]
                for lo,hi in value_spans(COLUMNS[h],value,duration_prefix):yield a+lo,a+hi,COLUMNS[h]
                if COLUMNS[h]=='properties':
                    for m in re.finditer(r'射程\s*\d+\s*/\s*\d+\s*[；;]\s*([\u3400-\u9fff]+)\s*[）)]',value):
                        yield a+m.start(1),a+m.end(1),'ammunition'


def proper_target(role,label,e,scope,path,position):
    definition=e.get('slot_definition',{});family=definition.get('family')
    if not scope or scope['ruleset']!=e['ruleset']:return False
    if role.endswith('_label'):
        return e['type']=='rule' and e.get('slot_collection')==role.removesuffix('_label')
    if e['type']!=('equipment' if role=='ammunition' else 'rule'):return False
    if role=='legend':
        lo,hi=definition.get('scope_span',[0,0])
        return family==role and e['page_path']==path and lo<=position<hi and definition.get('symbol')==label
    if role=='duration' and re.fullmatch(TIME,label):return family==role and bool(definition.get('measure'))
    if role=='duration' and '/术语汇编/' in e['page_path']:return e['name_zh']==label
    if family!=('damage' if role=='damage_notation' else role):return False
    if label in (e['name_zh'],e['name_en']):return True
    if role=='components':return bool(re.search('[（(]'+re.escape(label)+'[）)]$',e['name']))
    return role=='properties' and bool(re.fullmatch(re.escape(e['name_zh'])+r'\d+',label))


def compare_baseline(db,path,summary):
    db.execute('ATTACH DATABASE ? AS old',(path.resolve().as_uri()+'?mode=ro',))
    old=json.loads(db.execute("SELECT value FROM old.metadata WHERE key='summary'").fetchone()[0])
    if any(old[k]!=summary[k] for k in ('snapshot_commit','input_manifest_sha256')):
        raise ValueError('不能按偏移比较不同原文快照')
    return db.execute("""SELECT count(*) FROM old.effective_references b WHERE b.can_link AND NOT EXISTS (
        SELECT 1 FROM main.effective_references r WHERE r.page_path=b.page_path AND r.start=b.start
        AND r.end=b.end AND r.target_id=b.target_id AND r.can_link)""").fetchone()[0]


def report(db,catalog,book,output,baseline=None):
    manifest=json.loads((book/'anna-manifest.json').read_bytes())
    with catalog.open('rb') as stream:
        if file_digest(stream,'sha256').hexdigest()!=manifest['database_sha256']:
            raise ValueError('链接版与目录数据库不一致；请先 make build')
    summary=json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    source=Path(manifest['source_root']);pattern=damage_names(db)
    entries={};by_page=defaultdict(list)
    for raw, in db.execute('SELECT data FROM entries'):
        e=json.loads(raw);entries[e['id']]=e;by_page[e['page_path']].append(e)

    # Also probe for a named mode at the start of an accidentally joined field/body.
    # Unrecognized values still enter the inventory below; a missing discovery is not success.
    modes={e['name_zh'] for e in entries.values() if e.get('slot_definition',{}).get('family')=='duration'
           and not e.get('slot_collection') and e['name_zh']}
    duration_prefix=re.compile(r'\s*('+'|'.join(map(re.escape,sorted(modes,key=lambda n:(-len(n),n))))+')') if modes else None
    tables=defaultdict(list)
    for p,raw in db.execute("SELECT page_path,data FROM raw_tables WHERE data LIKE '%学派%' OR data LIKE '%伤害%'"):
        tables[p].append(json.loads(raw)['rows'])
    sources={sid:json.loads(raw) for sid,raw in db.execute('SELECT id,data FROM sources')}

    @lru_cache(maxsize=96)
    def bindings(path):
        raw=(book/path).read_bytes()
        if sha256(raw).hexdigest()!=manifest['outputs'][path]:
            raise ValueError(f'链接页指纹不符：{path}')
        data=json.loads(re.search(rb'id="anna-bindings">(.*?)</script>',raw,re.S)[1])
        return {r['reference_id']:r for r in data['references']},{d['id']:d for d in data['definitions']}

    @lru_cache(maxsize=None)
    def valid_destination(eid):
        e=entries[eid]; p=e['page_path']; dest=bindings(p)[1].get(eid,{})
        if sha256((source/p).read_bytes()).hexdigest()!=manifest['inputs'][p]:
            raise ValueError(f'定义页原文指纹不符：{p}')
        with closing(Document(p,read_page(source/p)[0])) as doc:
            return bool(dest.get('name')==e['name'] and dest.get('body') and dest.get('title')
                        and dest['body']==text_slice(doc,e['start'],e['end'])
                        and dest['title']==text_slice(doc,e['start'],min(e['start']+250,e['title_end'])))

    output.mkdir(parents=True,exist_ok=True); counts=defaultdict(Counter); reasons=defaultdict(Counter)
    regression=compare_baseline(db,baseline,summary) if baseline else None
    with (output/'occurrences.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f);w.writerow(['原文提交','原文路径','起点_码点','终点_不含','核对组','词面','上下文','结果','原因','目标路径','目标条目','条目标识'])
        for path,text in db.execute("SELECT path,text FROM pages WHERE parse_status='ok' ORDER BY path"):
            local=by_page[path];spells=[(e['title_end'],e['end']) for e in local if e['type']=='spell']
            uses=sorted(set(inventory(tables[path],text,pattern,spells,duration_prefix)))
            if not uses:continue
            if sha256((source/path).read_bytes()).hexdigest()!=manifest['inputs'][path]:
                raise ValueError(f'原文指纹不符：{path}')
            refs=defaultdict(list)
            for raw, in db.execute('SELECT data FROM effective_references WHERE page_path=? AND is_effective',(path,)):
                r=json.loads(raw);refs[r['start'],r['end']].append(r)
            linked=bindings(path)[0]
            with closing(Document(path,read_page(source/path)[0])) as doc:
                if doc.text!=text:raise ValueError(f'原文坐标不一致：{path}')
                for a,b,role in uses:
                    label=text[a:b];hits=refs[a,b];click=[r for r in hits if r['can_link']]
                    state='missing';why='；'.join(sorted({r['reason'] for r in hits}));e={}
                    if click:
                        r=click[0];e=entries[r['target_id']];binding=linked.get(r['id'],{})
                        owners=[o for o in local if o['title_end']<=a<b<=o['end']]
                        size=min((o['end']-o['start'] for o in owners),default=0)
                        contexts={(tuple(o['source_ids']),o['ruleset']) for o in owners if o['end']-o['start']==size}
                        scope=r['evidence'].get('scope',{})
                        same_scope=(len(contexts)==1 and (tuple(scope.get('source_ids',[])),scope.get('ruleset')) in contexts)
                        applicable=bool(set(e['source_ids']) & set(scope.get('source_ids',[]))) or any(
                            sources[s].get('reference_role')=='core_rules' for s in e['source_ids'])
                        ok=(len(click)==1 and binding.get('id')==e['id'] and binding.get('range')==text_slice(doc,a,b)
                            and valid_destination(e['id']) and same_scope and applicable
                            and proper_target(role,label,e,r['evidence'].get('scope'),path,a))
                        state='verified' if ok else 'wrong_target'
                    elif not why:
                        why='definition_title' if any(e['start']<b and a<e['title_end'] for e in local) else 'no_exact_reference'
                    counts[role][state]+=1
                    if state!='verified':reasons[role][why]+=1
                    w.writerow([summary['snapshot_commit'],path,a,b,role,label,' '.join(text[max(0,a-18):b+18].split()),
                                state,why,e.get('page_path',''),e.get('name',''),e.get('id','')])
    result={'snapshot_commit':summary['snapshot_commit'],'input_manifest_sha256':summary['input_manifest_sha256'],
            'previous_clicks_missing_or_retargeted':regression,'occurrences':counts,'remaining':reasons}
    data=json.dumps(result,indent=2,ensure_ascii=False);(output/'summary.json').write_text(data+'\n');print(data)
    return 1 if regression or any(c['wrong_target'] for c in counts.values()) else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=PROJECT/'data/extracted/catalog.sqlite')
    parser.add_argument('--book',type=Path,default=PROJECT/'data/5echm-anna')
    parser.add_argument('--output',type=Path,default=PROJECT/'output/reference-coverage')
    parser.add_argument('--baseline',type=Path,help='核对同一快照上的所有旧点击是否保留原范围及目标')
    args=parser.parse_args()
    with closing(sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        return report(db,args.db,args.book,args.output,args.baseline)


if __name__=='__main__':
    raise SystemExit(main())
