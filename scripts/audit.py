#!/usr/bin/env python3
"""Read-only corpus audit, optional before/after diff; writes reports, never edits the DB."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import posixpath
import re
import sqlite3
from build import TYPE_LABELS, csv_write
from entry_types import RULE_TYPES

# Leads for the classifier, not classifications. An untyped section whose own text prints a mechanic
# (a save DC, dice damage, a dice-rolled table) probably defines something no recognizer reads yet.
# The audit reports where; deciding the type stays with extraction.
GAP_SIGNALS={
    'save_dc':re.compile(r'DC\s*\d{1,2}\s*的?\s*(?:力量|敏捷|体质|智力|感知|魅力)?\s*(?:[（(][^）)\n]{0,8}[）)])?\s*(?:豁免|检定)'),
    'dice_damage':re.compile(r'\d+\s*[dD]\s*\d+[^。；\n]{0,12}?伤害'),
}
DICE_HEADER=re.compile(r'\s*[1１]?\s*[dD]\s*(?:\d+|%)\s*')
SIGNAL_LABELS={'save_dc':'豁免 DC','dice_damage':'骰子伤害','dice_table':'骰值表'}
TITLE_LABEL=re.compile(r'【\s*([^】]{1,12}?)\s*】')
# 资料索引 points at material rather than owning it, so a section inside one still counts as untyped.
UNOWNED_TYPES={'section','lore'}


def connect(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    return db


def audit_concepts(db,folder):
    """Validate occurrence accounting independently of semantic review completion."""
    errors=[];ids=set();outcomes=Counter()
    pages=dict(db.execute('SELECT path,length(text) FROM pages'))
    entries={r[0] for r in db.execute('SELECT id FROM entries')}
    rule_targets={r[0]:r[1] for r in db.execute(
        "SELECT definition_id,json_extract(data,'$.ruleset'),json_extract(data,'$.type') FROM concepts") if r[2] in RULE_TYPES}
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
        if signal=='plain' and status=='resolved':
            syntax=o['evidence'].get('syntax',{})
            scope=o['evidence'].get('scope',{})
            cue=syntax.get('cue_span',[])
            # A rules-use clause ends where the name starts; a d20-test notation contains the name it applies.
            located=len(cue)==2 and (cue[0]<=a<b<=cue[1] if syntax.get('rule')=='check_notation' else 0<=cue[0]<cue[1]==a)
            if (o['role']!='mention' or not located
                or scope.get('ruleset') not in {'5e','5r'} or len(scope.get('source_ids',[]))!=1
                or not scope.get('owner_ids') or any(e not in entries for e in scope['owner_ids'])):
                errors.append(mid+': missing plain reference provenance')
            elif rule_targets.get(target)!=scope['ruleset']:
                errors.append(mid+': plain reference target ruleset mismatch')
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


def audit_counterparts(db,folder):
    """Counterpart outcomes must be internally consistent and match their exports; agreement with
    5etools-cn is reported, not required."""
    errors=[]
    records=set(r[0] for r in db.execute('SELECT id FROM counterpart_records'))
    entries=set(r[0] for r in db.execute('SELECT id FROM entries'))
    outcomes=Counter();rows=set()
    for eid,outcome,rule,record,raw in db.execute('SELECT entry_id,outcome,rule,record_id,data FROM counterparts'):
        o=json.loads(raw);rows.add(eid);outcomes[outcome]+=1
        if [eid,outcome,rule,record]!=[o[k] for k in ('entry_id','outcome','rule','record_id')]:errors.append(eid+': data mismatch')
        if any(c not in records for c in o['candidates']):errors.append(eid+': missing candidate')
        if outcome=='resolved':
            if not record or o['candidates']!=[record]:errors.append(eid+': invalid resolved record')
        elif record is not None:errors.append(eid+': unresolved record')
        if (outcome=='unmatched')!=(not o['candidates']):errors.append(eid+': candidates disagree with outcome')
    findings=Counter();finding_ids=set()
    for fid,kind,eid,record,raw in db.execute('SELECT id,kind,entry_id,record_id,data FROM counterpart_findings'):
        f=json.loads(raw);finding_ids.add(fid);findings[kind]+=1
        detail=f['detail']
        if record not in records or (eid is not None and eid not in entries):errors.append(fid+': missing target')
        if kind=='reprinted_as' and (detail['other_entry_id'] not in entries or detail['other_record_id'] not in records):errors.append(fid+': missing reprint target')
        if (kind=='unclaimed_record')!=(eid is None):errors.append(fid+': entry presence disagrees with kind')
    for name,column,ids in (('counterparts.csv','entry_id',rows),('counterpart_findings.csv','id',finding_ids)):
        p=Path(folder)/name
        if not p.exists():errors.append(name+': missing');continue
        with p.open(encoding='utf-8-sig',newline='') as f:exported=[r[column] for r in csv.DictReader(f)]
        if len(exported)!=len(ids) or set(exported)!=ids:errors.append(name+': IDs mismatch')
    coverage=json.loads(db.execute("SELECT value FROM metadata WHERE key='counterpart_coverage'").fetchone()[0])
    if coverage['outcomes']!=dict(outcomes) or coverage['entries']!=len(rows):errors.append('coverage: outcome mismatch')
    if coverage['findings']!=dict(findings):errors.append('coverage: finding mismatch')
    if coverage['records']!=len(records):errors.append('coverage: record mismatch')
    return {'catalog':coverage['catalog'],'commit':coverage['commit'],'records':len(records),'entries':len(rows),
            'outcomes':dict(outcomes),'outcomes_by_type':coverage['outcomes_by_type'],'findings':dict(findings),
            'alignment_status':coverage['alignment_status'],'errors':errors}


def classification_gaps(db):
    """Where typed coverage stops: untyped pages, and untyped sections that still print a mechanic.

    A section that begins inside a typed entry belongs to it (a stat block's 动作 heading), so only
    sections outside every owning entry count. A section's own text runs to the next entry on its
    page, the same window the classifier reads. Title labels such as 【危害】 are tallied by the type
    each labelled entry received, so one label split across types stands out.
    Returns (summary, page rows, section rows).
    """
    source_names=dict(db.execute('SELECT id,name FROM sources'))
    pages={}
    for path,title,text,ids in db.execute("SELECT path,title,text,source_ids FROM pages WHERE role='document' AND parse_status='ok'"):
        source='；'.join(source_names.get(i,i) for i in json.loads(ids or '[]')) or '未定来源'
        pages[path]={'title':title,'text':text,'source':source,'directory':posixpath.dirname(path).removeprefix('topics/')}
    entries=defaultdict(list);labels=defaultdict(Counter)
    for eid,name,typ,path,a,b in db.execute('SELECT id,name,type,page_path,start,end FROM entries'):
        entries[path].append((a,b,typ,eid,name))
        m=TITLE_LABEL.search(name or '')
        if m:labels['【'+m[1]+'】'][typ]+=1
    tables=defaultdict(list)
    for path,a,b,raw in db.execute('SELECT page_path,start,end,data FROM raw_tables'):
        headers=json.loads(raw).get('headers') or ['']
        if DICE_HEADER.fullmatch(headers[0] or ''):tables[path].append((a,b,headers[0]))
    page_rows=[];section_rows=[];outside=0;signals=Counter()
    for path in sorted(entries):
        page=pages.get(path)
        if not page:continue
        found=sorted(entries[path]);text=page['text']
        starts=sorted({a for a,*_ in found})
        owners=[(a,b) for a,b,typ,*_ in found if typ not in UNOWNED_TYPES]
        untyped=all(typ=='section' for _,_,typ,*_ in found)
        flagged=0
        for a,b,typ,eid,name in found:
            if typ!='section' or any(x<=a<y for x,y in owners):continue
            outside+=1
            end=min(b,next((s for s in starts if s>a),b))
            evidence=[]
            for signal,pattern in GAP_SIGNALS.items():
                m=pattern.search(text,a,end)
                if m:evidence.append({'signal':signal,'span':[m.start(),m.end()],'text':m[0]})
            table=next(((x,y,h) for x,y,h in tables[path] if a<=x and y<=end),None)
            if table:evidence.append({'signal':'dice_table','span':[table[0],table[1]],'text':table[2]})
            if not evidence:continue
            flagged+=1;signals.update(e['signal'] for e in evidence)
            section_rows.append({'id':eid,'name':name,'page_path':path,'source':page['source'],'directory':page['directory'],
                'page_untyped':untyped,'signals':';'.join(e['signal'] for e in evidence),'evidence':evidence})
        if untyped:
            page_rows.append({'path':path,'title':page['title'],'source':page['source'],'directory':page['directory'],
                'sections':len(found),'flagged_sections':flagged})
    by_source=defaultdict(Counter)
    for r in page_rows:by_source[r['source']]['untyped_pages']+=1
    for r in section_rows:by_source[r['source']]['flagged_sections']+=1
    summary={'untyped_pages':len(page_rows),'outside_sections':outside,'flagged_sections':len(section_rows),
        'signals':{k:signals[k] for k in SIGNAL_LABELS},
        'by_source':[{'source':s,'untyped_pages':c['untyped_pages'],'flagged_sections':c['flagged_sections']}
                     for s,c in sorted(by_source.items(),key=lambda x:(-x[1]['untyped_pages']-x[1]['flagged_sections'],x[0]))],
        'labels':{k:dict(v.most_common()) for k,v in sorted(labels.items(),key=lambda x:(-sum(x[1].values()),x[0]))}}
    return summary,page_rows,section_rows


def type_conflict_report(db):
    """Entries whose source prints evidence for a type other than the one they received.

    Recognizers decide in their declared order; printed evidence that later recognizers still find is
    kept on the entry as `type_conflicts`. Returns (summary, rows), or (None, []) for a catalog built
    before entries carried the field.
    """
    if not db.execute("SELECT 1 FROM entries WHERE json_type(data,'$.type_conflicts')='array' LIMIT 1").fetchone():return None,[]
    rows=[];pairs=Counter()
    for eid,name,typ,path,rule,raw in db.execute("SELECT id,name,type,page_path,json_extract(data,'$.classification_rule'),json_extract(data,'$.type_conflicts') "
                                                 "FROM entries WHERE json_array_length(json_extract(data,'$.type_conflicts'))>0 ORDER BY page_path,start"):
        printed=json.loads(raw)
        for c in printed:pairs[(typ,rule,c['type'],c['rule'])]+=1
        rows.append({'id':eid,'name':name,'page_path':path,'type':typ,'rule':rule,'printed':';'.join(f"{c['type']}/{c['rule']}" for c in printed)})
    summary={'entries':len(rows),'pairs':[{'type':t,'rule':r,'printed_type':pt,'printed_rule':pr,'count':n} for (t,r,pt,pr),n in pairs.most_common()]}
    return summary,rows


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
    counterparts=audit_counterparts(db,Path(db_path).parent) if db.execute("SELECT 1 FROM sqlite_master WHERE name='counterparts'").fetchone() else None
    gaps,gap_pages,gap_sections=classification_gaps(db)
    conflicts,conflict_rows=type_conflict_report(db)
    old_counts={};changes=[];same_manifest=None;gaps_before=None;conflicts_before=None
    if baseline:
        old=connect(baseline)
        prior=json.loads(old.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
        old_counts=prior['types'];same_manifest=prior['input_manifest_sha256']==summary['input_manifest_sha256']
        prior_gaps=classification_gaps(old)[0]
        gaps_before={k:prior_gaps[k] for k in ('untyped_pages','outside_sections','flagged_sections','signals')}
        prior_conflicts=type_conflict_report(old)[0]
        conflicts_before=prior_conflicts['entries'] if prior_conflicts else None
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
        'review_reasons':reasons,'catalog_reviews':catalog_reviews,'concepts':concepts,'counterparts':counterparts,'change_counts':dict(Counter(c['change'] for c in changes)),
        'classification_gaps':gaps,'classification_gaps_before':gaps_before,
        'type_conflicts':conflicts,'type_conflicts_before':conflicts_before}
    db.close();return result,changes,{'pages':gap_pages,'sections':gap_sections,'conflicts':conflict_rows}


def gap_report(result,gaps):
    """Markdown for the classification gaps: totals, the directories to read first, and label splits."""
    now=result['classification_gaps'];before=result['classification_gaps_before'] or {}
    def compare(key):return f"{now[key]}（修改前 {before[key]}）" if before else str(now[key])
    signals='，'.join(f"{SIGNAL_LABELS[k]} {n}" for k,n in now['signals'].items())
    lines=['## 分类缺口','',
        '未分类段落是改进识别规则的线索，不代表原文缺少定义。带信号的段落在自身正文中出现豁免 DC、骰子伤害或骰值表；位于已分类条目之内的段落属于该条目，不计入。','',
        f"- 无类型正文页：{compare('untyped_pages')}。",
        f"- 已分类条目之外的未分类段落：{compare('outside_sections')}。",
        f"- 其中带信号的段落：{compare('flagged_sections')}；{signals}。",'',
        '| 目录 | 无类型页 | 带信号段落 |','|---|---:|---:|']
    directories=defaultdict(Counter)
    for r in gaps['pages']:directories[r['directory']]['pages']+=1
    for r in gaps['sections']:directories[r['directory']]['sections']+=1
    for d,c in sorted(directories.items(),key=lambda x:(-x[1]['pages']-x[1]['sections'],x[0]))[:20]:
        lines.append(f"| `{d}` | {c['pages']} | {c['sections']} |")
    # A label worth reading recurs, or already disagrees with itself; one-off labels stay in audit.json.
    shown={k:v for k,v in now['labels'].items() if sum(v.values())>=3 or len(v)>1}
    if shown:
        lines+=['','标题标注与条目所得分类：','','| 标注 | 分类 |','|---|---|']
        for label,types in shown.items():
            lines.append(f"| {label} | {'，'.join(f'{TYPE_LABELS.get(t,t)} {n}' for t,n in types.items())} |")
    lines+=['','逐页见 `untyped_pages.csv`；逐段的信号与原文位置见 `classification_gaps.csv`。']
    return lines


def conflict_report(result):
    """Markdown for the type conflicts: how many entries carry one, grouped by decision and printed evidence."""
    now=result['type_conflicts']
    if now is None:return []
    before=result['type_conflicts_before']
    count=f"{now['entries']}（修改前 {before}）" if before is not None else str(now['entries'])
    lines=['','## 分类冲突','',
        '识别规则按声明顺序判定类型，先命中的规则决定；之后仍命中的原文明示证据记在条目的 `type_conflicts` 中。冲突是线索：其中有误分类，也有在别处合理出现的版式，例如职业核心特质同样列出技能与工具熟练，物品正文也会写护甲等级与生命值。','',
        f"- 带冲突的条目：{count}。"]
    if now['pairs']:
        lines+=['','| 所得分类（规则） | 另有原文证据（规则） | 条目 |','|---|---|---:|']
        for p in now['pairs']:
            lines.append(f"| {TYPE_LABELS.get(p['type'],p['type'])}（`{p['rule']}`） | {TYPE_LABELS.get(p['printed_type'],p['printed_type'])}（`{p['printed_rule']}`） | {p['count']} |")
    lines+=['','逐条见 `type_conflicts.csv`。']
    return lines


COUNTERPART_OUTCOMES={'resolved':'唯一对应','ambiguous':'多个候选','other_source':'仅见于其他来源','unaligned':'来源未对齐','unmatched':'无同名记录'}
COUNTERPART_FINDINGS={'field_differs':'字段取值不同','name_en_differs':'英文名不同','name_en_missing':'anna 缺英文名',
    'name_zh_differs':'不全书译名不同','reprinted_as':'重印关系候选','unclaimed_record':'不全书译文无对应条目'}


def counterpart_report(result):
    """Markdown for the 5etools-cn comparison: outcomes by type and the disagreements to read."""
    c=result['counterparts']
    if not c:return ['## 来源对照','','本次构建未运行 5etools-cn 对照。']
    lines=['## 来源对照','',
        f"对照 {c['catalog']} `{c['commit'][:12]}` 的 {c['records']} 条记录。对应关系是候选：依据名称、已对齐来源与所属职业得出，不改写不全书原文和已提取的值。"
        '“无同名记录”多为 5etools 不收录的第三方、UA 与玩家包内容，也可能是名称解析差异。','',
        '| 分类 | '+' | '.join(COUNTERPART_OUTCOMES.values())+' |','|---|'+'---:|'*len(COUNTERPART_OUTCOMES)]
    for typ,counts in sorted(c['outcomes_by_type'].items(),key=lambda x:-sum(x[1].values())):
        lines.append(f"| {TYPE_LABELS.get(typ,typ)} | "+' | '.join(str(counts.get(k,0)) for k in COUNTERPART_OUTCOMES)+' |')
    lines+=['','核对线索：','']
    for kind,label in COUNTERPART_FINDINGS.items():
        lines.append(f"- {label}：{c['findings'].get(kind,0)}")
    lines+=['',f"来源对齐：{'，'.join(f'{k} {n}' for k,n in c['alignment_status'].items())}；逐对见 `counterpart_sources.csv`。逐条对应见 `counterparts.csv`，线索与 5etools-cn 记录位置见 `counterpart_findings.csv`。"]
    return lines


def export_report(out,result,changes,gaps):
    out.mkdir(parents=True,exist_ok=True)
    (out/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    csv_write(out/'audit_changes.csv',changes,['id','name','page_path','change','before_type','after_type','before_fields','after_fields'])
    csv_write(out/'untyped_pages.csv',gaps['pages'],['path','title','source','directory','sections','flagged_sections'])
    csv_write(out/'classification_gaps.csv',gaps['sections'],['id','name','page_path','source','directory','page_untyped','signals','evidence'])
    csv_write(out/'type_conflicts.csv',gaps['conflicts'],['id','name','page_path','type','rule','printed'])
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
        '', '全库待核原因与数量见 `audit.json`；逐条增删、类型和字段变化见 `audit_changes.csv`。计数下降可能是纠正误分类，并不表示删去原文。','']
    lines+=gap_report(result,gaps)
    lines+=conflict_report(result)
    lines+=['',*counterpart_report(result)]
    (out/'audit.md').write_text('\n'.join(lines)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite')
    p.add_argument('--baseline',type=Path);p.add_argument('--output',type=Path)
    a=p.parse_args();result,changes,gaps=audit(a.db,a.baseline);export_report(a.output or a.db.parent,result,changes,gaps)
    report={k:result[k] for k in ['full_scan','parse_errors','same_input_as_baseline','integrity_check','foreign_key_errors','csv_sql_mismatches','concepts','change_counts']}
    report['classification_gaps']={k:result['classification_gaps'][k] for k in ('untyped_pages','outside_sections','flagged_sections','signals')}
    report['type_conflicts']=result['type_conflicts']['entries'] if result['type_conflicts'] else None
    report['counterparts']={k:result['counterparts'][k] for k in ('commit','outcomes','findings','errors')} if result['counterparts'] else None
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if result['parse_errors'] or result['foreign_key_errors'] or result['csv_sql_mismatches'] or result['integrity_check']!='ok' or (result['concepts'] and result['concepts']['errors']) or (result['counterparts'] and result['counterparts']['errors']):raise SystemExit(1)

if __name__=='__main__':main()
