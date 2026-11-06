#!/usr/bin/env python3
"""Read-only corpus audit, optional before/after diff; writes reports, never edits the DB."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sqlite3
from build import TYPE_LABELS, csv_write


def connect(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    return db


def audit_concepts(db,folder):
    """Validate occurrence accounting independently of semantic review completion."""
    errors=[];ids=set();outcomes=Counter()
    pages=dict(db.execute('SELECT path,length(text) FROM pages'))
    entries={r[0] for r in db.execute('SELECT id FROM entries')}
    for row in db.execute('SELECT id,page_path,start,end,signal,status,target_id,data FROM concept_observations'):
        mid,path,a,b,signal,status,target,raw=row;o=json.loads(raw);ids.add(mid);outcomes[(signal,status)]+=1
        if not 0<=a<=b<=pages.get(path,-1):errors.append(mid+': invalid range')
        if [mid,path,a,b,signal,status,target]!=[o[k] for k in ('id','path','start','end','signal','status','target_id')]:errors.append(mid+': data mismatch')
        if any(c not in entries for c in o['candidates']):errors.append(mid+': missing candidate')
        if status=='resolved':
            if not target or o['candidates']!=[target]:errors.append(mid+': invalid resolved target')
        elif target is not None:errors.append(mid+': unresolved target')
        if signal=='format' and status=='resolved' and (o['role']!='mention' or not o['evidence'].get('stylesheet_sha256')):
            errors.append(mid+': missing format provenance')
    exported=set();p=Path(folder)/'concept_observations.jsonl'
    if p.exists():
        with p.open() as f:
            for line in f:
                mid=json.loads(line)['id']
                if mid in exported:errors.append(mid+': duplicate export')
                exported.add(mid)
    if ids!=exported:errors.append('concept_observations.jsonl: IDs mismatch')
    coverage=json.loads(db.execute("SELECT value FROM metadata WHERE key='concept_coverage'").fetchone()[0])
    if coverage['observations']!=len(ids):errors.append('coverage: total mismatch')
    expected={(s,status):n for s,values in coverage['outcomes'].items() for status,n in values.items()}
    if dict(outcomes)!=expected:errors.append('coverage: outcome mismatch')
    return {'observations':len(ids),'errors':errors,'outcomes':coverage['outcomes'],
            'gap_counts':{k:len(v) for k,v in coverage['gaps'].items()}}


def audit(db_path,baseline=None):
    db=connect(db_path)
    summary=json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    entries={r['id']:dict(r) for r in db.execute('SELECT id,name,type,page_path,fields FROM entries')}
    view_counts={r[0]:db.execute('SELECT count(*) FROM "'+r[0].replace('"','""')+'"').fetchone()[0]
                 for r in db.execute("SELECT name FROM sqlite_master WHERE type='view' AND name!='entry_text'")}
    # Counts AND identities must agree across every exported CSV and SQL view.
    mismatches=[]
    for name in view_counts:
        p=Path(db_path).parent/'views'/f'{name}.csv'
        if not p.exists():mismatches.append(name+': CSV missing');continue
        with p.open(encoding='utf-8-sig',newline='') as f:ids=[r['id'] for r in csv.DictReader(f)]
        sql_ids={r[0] for r in db.execute('SELECT id FROM "'+name.replace('"','""')+'"')}
        if len(ids)!=len(sql_ids) or set(ids)!=sql_ids:mismatches.append(name+': IDs mismatch')
    reasons=dict(db.execute('SELECT reason,count(*) FROM review_queue GROUP BY reason'))
    catalog_reviews=[json.loads(r[0]) for r in db.execute("SELECT data FROM review_queue WHERE reason IN ('animal_statblock_unresolved','mount_example_unresolved','catalog_table_unhandled','catalog_row_layout_unresolved') ORDER BY reason,target")]
    coverage=[dict(r) for r in db.execute("SELECT type,classification_status,count(*) AS count FROM entries GROUP BY type,classification_status ORDER BY type,classification_status")]
    concepts=audit_concepts(db,Path(db_path).parent) if db.execute("SELECT 1 FROM sqlite_master WHERE name='concept_observations'").fetchone() else None
    old_counts={};changes=[];same_manifest=None
    if baseline:
        old=connect(baseline)
        prior=json.loads(old.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
        old_counts=prior['types'];same_manifest=prior['input_manifest_sha256']==summary['input_manifest_sha256']
        old_entries={r['id']:dict(r) for r in old.execute('SELECT id,name,type,page_path,fields FROM entries')}
        for eid in sorted(entries.keys()|old_entries.keys()):
            a=old_entries.get(eid);b=entries.get(eid)
            if a==b:continue
            e=b or a
            changes.append({'id':eid,'name':e['name'],'page_path':e['page_path'],
                'change':'added' if a is None else 'removed' if b is None else 'type_changed' if a['type']!=b['type'] else 'fields_changed',
                'before_type':a['type'] if a else '', 'after_type':b['type'] if b else '',
                'before_fields':json.loads(a['fields']) if a else {},'after_fields':json.loads(b['fields']) if b else {}})
        old.close()
    result={'snapshot_commit':summary['snapshot_commit'],'input_manifest_sha256':summary['input_manifest_sha256'],
        'full_scan':summary['full_scan'],'processed_pages':summary['processed_html_files'],
        'parse_errors':summary['parse_status'].get('error',0),'same_input_as_baseline':same_manifest,
        'integrity_check':db.execute('PRAGMA integrity_check').fetchone()[0],
        'foreign_key_errors':len(db.execute('PRAGMA foreign_key_check').fetchall()),'csv_sql_mismatches':mismatches,
        'views':view_counts,'before_types':old_counts,'after_types':summary['types'],'classification_coverage':coverage,
        'review_reasons':reasons,'catalog_reviews':catalog_reviews,'concepts':concepts,'change_counts':dict(Counter(c['change'] for c in changes))}
    db.close();return result,changes


def export_report(out,result,changes):
    out.mkdir(parents=True,exist_ok=True)
    (out/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    csv_write(out/'audit_changes.csv',changes,['id','name','page_path','change','before_type','after_type','before_fields','after_fields'])
    lines=['# 提取规则审计','',
        '检查范围：全库分类计数和证据状态、所有导出视图；逐表检查商品表列结构，检查原载具／设施／奖励／陷阱的上下文分类结果；原文回归覆盖两版PHB、两版DMG、MM、TCE、XGE及合作内容。',
        '这不是全库人工核验。未分类段落、待核关联和既有索引问题仍保留，不将空视图解释为原文不存在。','',
        '## 本轮修正','',
        '- 坐骑表按“价格＋载重”拆为动物购置条目，与数据卡分开；同版核心数据卡唯一精确匹配后关联，不复制价格、不跨版合并。',
        '- 坐骑用途保留来源及推断状态；飞蛇为宠物，雪橇犬使用狼数据卡；正文明确列出的稀有坐骑另建用途证据。',
        '- 分拆双栏装备表；保留左右各自价格、重量、份量、护甲属性等；不把载重当重量。',
        '- 从清单补出水陆空载具、商业货物、机巧奇具和基础设施；船只部件、操作规则、据点事件不再误作实体。',
        '- 据点设施按条目描述识别，补出目录路径未含“设施”的费伦阵营设施。',
        '- 奖励总标题、职业选项总标题不作奖励／子职；禁令恩惠归职业能力。陷阱按自身类型描述识别，不靠页面出现“陷阱＋DC”。','',
        '## 校验','',
        f"- 全量扫描：{result['processed_pages']} 页；解析错误 {result['parse_errors']}。",
        f"- SQLite：{result['integrity_check']}；外键错误 {result['foreign_key_errors']}；CSV／SQL视图不一致 {len(result['csv_sql_mismatches'])}。",
        f"- 输入与修改前一致：{result['same_input_as_baseline']}。",'',
        '## 分类计数（版本候选，不是去重实体）','','| 分类 | 修改前 | 修改后 |','|---|---:|---:|']
    for typ in sorted(result['after_types'].keys()|result['before_types'].keys()):
        lines.append(f"| {TYPE_LABELS.get(typ,typ)} | {result['before_types'].get(typ,'—')} | {result['after_types'].get(typ,0)} |")
    lines+=['',f"坐骑视图：{result['views'].get('坐骑',0)} 条；动物购置：{result['views'].get('动物购置',0)} 条。",'',
        '## 本轮仍需核对','']
    for r in result['catalog_reviews']:
        detail=json.dumps(r['detail'],ensure_ascii=False)
        lines.append(f"- `{r['reason']}`：{detail}；`{r['evidence'].get('path','')}`。")
    lines+=['','未支持的混合饮食／住宿价格表保留原表并进入审核队列，不伪装成装备条目。驴／骡的并列表述逐个关联；未找到独立驴数据卡时保留待核，不擅用骡替代。',
        '', '全库待核原因与数量见 `audit.json`；逐条增删、类型和字段变化见 `audit_changes.csv`。计数下降可能是纠正误分类，并不表示删去原文。']
    (out/'提取审计.md').write_text('\n'.join(lines)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite')
    p.add_argument('--baseline',type=Path);p.add_argument('--output',type=Path)
    a=p.parse_args();result,changes=audit(a.db,a.baseline);export_report(a.output or a.db.parent,result,changes)
    print(json.dumps({k:result[k] for k in ['full_scan','parse_errors','same_input_as_baseline','integrity_check','foreign_key_errors','csv_sql_mismatches','concepts','change_counts']},ensure_ascii=False,indent=2))
    if result['parse_errors'] or result['foreign_key_errors'] or result['csv_sql_mismatches'] or result['integrity_check']!='ok' or (result['concepts'] and result['concepts']['errors']):raise SystemExit(1)

if __name__=='__main__':main()
