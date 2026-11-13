#!/usr/bin/env python3
"""Read-only queries over the extracted dataset (standard library only)."""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys

DB=Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',type=Path,default=DB)
    ap.add_argument('--type',action='append',help='repeatable: spell, feat, creature, ...')
    ap.add_argument('--view',help='view name, e.g. 魔法武器 or 奖励')
    ap.add_argument('--ruleset',choices=['5e','5r','待核'])
    ap.add_argument('--source',action='append',help='book name or abbreviation; repeatable union')
    ap.add_argument('--origin',choices=['官方','合作','待核'])
    ap.add_argument('--release',choices=['正式','测试','待核'])
    ap.add_argument('--legacy',choices=['entry_marked','source_archived','unmarked'])
    ap.add_argument('--status',choices=['原文明示','上下文推得','未分类段落'])
    ap.add_argument('--search',default='')
    ap.add_argument('--field',action='append',default=[],help='field=value, e.g. school=塑能')
    ap.add_argument('--limit',type=int,default=20)
    ap.add_argument('--include-sections',action='store_true')
    ap.add_argument('--show',help='version ID: include original text, evidence and relations')
    ap.add_argument('--stats',action='store_true');ap.add_argument('--json',action='store_true')
    args=ap.parse_args()
    if not args.db.exists():ap.error('run build.py first')
    db=sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    if args.stats:
        print(json.dumps(json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0]),ensure_ascii=False,indent=2));return
    if args.show:
        row=db.execute('SELECT data FROM entries WHERE id=?',(args.show,)).fetchone()
        if not row:ap.error('version ID not found')
        data=json.loads(row[0]);data['body']=db.execute('SELECT body FROM entry_text WHERE id=?',(args.show,)).fetchone()[0]
        data['facts']=[dict(r) for r in db.execute('SELECT field,value,evidence,rule,scope,status FROM facts WHERE entry_id=?',(args.show,))]
        for f in data['facts']:f['value']=json.loads(f['value']);f['evidence']=json.loads(f['evidence'])
        data['relations']=[dict(r) for r in db.execute('SELECT predicate,object_id,target_label,evidence,status FROM relations WHERE subject_id=?',(args.show,))]
        print(json.dumps(data,ensure_ascii=False,indent=2));return
    clauses=[];params=[]
    if args.view:
        views={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        if args.view not in views:ap.error('unknown view: '+args.view)
        clauses.append('e.id IN (SELECT id FROM "'+args.view.replace('"','""')+'")')
    if not args.include_sections:clauses.append("e.type!='section'")
    if args.type:
        clauses.append('e.type IN ('+','.join('?' for _ in args.type)+')');params+=args.type
    for field,value in [('ruleset',args.ruleset),('legacy',args.legacy),('classification_status',args.status)]:
        if value:clauses.append('e.'+field+'=?');params.append(value)
    if args.search:clauses.append('e.name LIKE ?');params.append('%'+args.search+'%')
    if args.source or args.origin or args.release:
        sourceclauses=[]
        if args.source:
            sc=[]
            for s in args.source:
                sc.append("(s.name LIKE ? OR EXISTS(SELECT 1 FROM json_each(s.data,'$.aliases') a WHERE a.value=? COLLATE NOCASE))")
                params+=['%'+s+'%',s]
            sourceclauses.append('('+' OR '.join(sc)+')')
        for field,value in [('origin',args.origin),('release_status',args.release)]:
            if value:sourceclauses.append('s.'+field+'=?');params.append(value)
        clauses.append('EXISTS(SELECT 1 FROM entry_sources es JOIN sources s ON s.id=es.source_id WHERE es.entry_id=e.id AND '+' AND '.join(sourceclauses)+')')
    for spec in args.field:
        if '=' not in spec:ap.error('--field requires name=value')
        field,value=spec.split('=',1)
        if not re.fullmatch(r'[a-z_]+',field):ap.error('invalid field name')
        clauses.append("EXISTS(SELECT 1 FROM json_each(e.fields,?) x WHERE CAST(x.value AS TEXT)=?)")
        params+=['$.'+field,{'true':'1','false':'0'}.get(value,value)]
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    count=db.execute('SELECT COUNT(*) FROM entries e'+where,params).fetchone()[0]
    rows=[json.loads(r[0]) for r in db.execute('SELECT e.data FROM entries e'+where+' ORDER BY e.name,e.id LIMIT ?',params+[max(0,args.limit)])]
    if args.json:print(json.dumps({'count':count,'rows':rows},ensure_ascii=False,indent=2))
    else:
        print(f'{count} version candidates; showing {len(rows)}. Inferences and pending reviews are not confirmed.')
        for r in rows:print('\t'.join([r['id'],r['name'],r['type'],r['ruleset'],r['legacy'],'; '.join(r['source_names'])]))
    db.close()


if __name__=='__main__':main()
