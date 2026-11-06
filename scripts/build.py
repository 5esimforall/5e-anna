#!/usr/bin/env python3
"""Build the complete local classification dataset. No network; source is read-only."""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
from concurrent.futures import ProcessPoolExecutor
import csv
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

from html_extract import Document, Candidate, compact, key, digest, split_name, resolve_link, classify, text_fields, VIEW_TYPES, EXTRA_VIEWS, CLASS_SHORT, CLASSES
from source_extract import SourceRegistry
from table_extract import extract_tables

PROJECT=Path(__file__).resolve().parents[1]
DEFAULT_WEB=PROJECT.parent/'5echm-web'
INDEX_FIELDS={
    '物品名':'name','怪物名':'name','法术名':'name','稀有度':'rarity','类别':'item_type','子类别':'item_subtypes',
    '同调':'attunement','同调条件':'attunement_requirements','来源':'source_code','体型':'size','类型':'creature_type',
    '传奇动作':'legendary_actions','挑战等级':'cr','环阶':'spell_level','等级':'spell_level','学派':'school',
    '职业':'spell_classes','施法时间':'casting_time','施法动作':'casting_time','言语':'verbal','姿势':'somatic','材料':'material',
    '仪式':'ritual','专注':'concentration',
}
TYPE_LABELS={'class':'职业','subclass':'子职','class_feature':'职业能力','species':'种族物种','background':'背景','feat':'专长',
    'spell':'法术','equipment':'普通装备','magic_item':'魔法物品','creature':'生物数据卡','reward':'奖励','vehicle':'载具',
    'animal_offer':'动物购置','facility':'设施','condition':'状态','action':'动作','rule':'规则','trap':'陷阱','poison':'毒药','disease':'疾病','hazard':'环境危害',
    'subsystem':'扩展机制','lore':'资料索引','section':'文献段落'}
EXCLUDED_DIRS={'速查 - 副本 - 副本','空白页模板','template','template2','Generator','格式转换工具包','鸣谢'}


def dumps(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def csv_write(path,rows,fields):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for r in rows:
            w.writerow({k:dumps(v) if isinstance(v,(dict,list,tuple,set)) else v for k,v in r.items() if k in fields})


def jsonl_write(path,rows):
    with path.open('w',encoding='utf-8') as f:
        for row in rows:f.write(dumps(row)+'\n')


def page_role(path):
    parts=Path(path).parts
    if len(parts)>1 and parts[1] in EXCLUDED_DIRS:return 'excluded_template_or_copy'
    if len(parts)>1 and parts[1]=='速查':return 'index'
    if parts[-1] in {'写在前面.html','分隔符.htm','旧版说明.htm','更新日志.html','鸣谢列表.htm'}:return 'editorial'
    return 'document'


def read_page(path):
    raw=path.read_bytes()
    try:return raw.decode('utf-8-sig'),'utf-8',sha256(raw).hexdigest()
    except UnicodeDecodeError:
        try:return raw.decode('gb18030'),'gb18030-inferred',sha256(raw).hexdigest()
        except UnicodeDecodeError:return raw.decode('utf-8',errors='replace'),'utf-8-replacement',sha256(raw).hexdigest()


def normalize_value(field,value):
    value=compact(value)
    if field=='attunement_requirements':
        return re.sub(r'同调$','',re.sub(r'^(?:需要|需)','',value)).strip()
    if field in {'attunement','legendary_actions','ritual','concentration','verbal','somatic'}:
        if value in {'是','有','√','V','S','仪式','专注'}:return True
        if value in {'否','无','×','非仪','非专'}:return False
    if field in {'ac','hp','cr'}:
        m=re.match(r'\d+(?:/\d+)?',value)
        if m:return m[0]
    if field=='casting_time':
        if re.match(r'^(?:1\s*)?附赠',value):return '附赠动作'
        if re.match(r'^(?:1\s*)?反应',value):return '反应'
        if re.match(r'^(?:1\s*)?动作',value):return '动作'
        if re.match(r'^\d+\s*(?:分钟|小时|轮)|^其他',value):return '其他'
        return value
    return value


def observation_fields(ob):
    result={}
    for h,v in zip(ob['headers'],ob['cells']):
        h=compact(h).replace(' ','')
        field=INDEX_FIELDS.get(h)
        if field and v and field!='name':result[field]=normalize_value(field,v)
    # The table has short headings in some editions; preserve cells regardless.
    if ob['kind']=='spell' and len(ob['cells'])==11:
        for field,i in [('spell_level',1),('school',2),('spell_classes',3),('casting_time',4),('verbal',5),('somatic',6),('material',7),('ritual',8),('concentration',9),('source_code',10)]:
            if ob['cells'][i]:result.setdefault(field,normalize_value(field,ob['cells'][i]))
    return result


def parse_one(args):
    web,path,observations,context=args
    page={'path':path,'role':page_role(path),'parse_status':'ok','error':'','title':'','text':'','encoding':'','sha256':''}
    try:
        raw,encoding,h=read_page(Path(web)/path)
        page.update(encoding=encoding,sha256=h)
        if page['role'] in {'excluded_template_or_copy','editorial'}:
            page['parse_status']='excluded';return {'page':page,'candidates':[],'tables':[],'links':[],'anchors':{},'issues':[]}
        doc=Document(path,raw)
        page.update(title=doc.title,text=doc.text)
        if page['role']=='index':
            out={'page':page,'candidates':[],'tables':[],'links':[],'anchors':{},'issues':[]}
            doc.close();return out
        cs=doc.candidates(observations)
        feature_starts=[c.start for c in cs if re.search(r'职业特性|Class Features|可选职业|祈唤|战技|超魔法|灌注',c.name,re.I)]
        if not feature_starts:
            optional=re.search(r'可选职业特性',doc.text[:1000])
            if optional:feature_starts=[optional.start()]
        output=[]
        for i,c in enumerate(cs):
            full=doc.text[c.start:c.end]
            # Classify from a section's own introduction, not a nested stat block.
            next_start=cs[i+1].start if i+1<len(cs) else c.end
            own=doc.text[c.start:min(c.end,next_start)]
            primary=i==0 or (i==1 and cs[0].method=='page_intro')
            c.fields['class_feature_region']=bool(feature_starts and c.start>min(feature_starts))
            ancestors=[];pi=c.parent
            while pi is not None:
                ancestors.append(cs[pi].name);pi=cs[pi].parent
            c.fields['choice_pool']=next((n for n in ancestors if re.search(r'战斗风格|祈唤|战技|超魔法|灌注',n)),None)
            class_path=re.search(r'/(?:'+ '|'.join(re.escape(x) for x in CLASSES) +r')(?:/|[-（(.])|/角色职业/|/职业/|/子职/',path)
            classify_text=full if primary and class_path else own
            kind,rule=classify(c,classify_text,context+' | '+path,primary)
            if kind=='class':full_for_fields=full
            elif c.explicit_end:full_for_fields=full
            else:full_for_fields=own
            fields=text_fields(full_for_fields,kind)
            title_offset=max(0,c.title_end-c.start)
            raw_descriptor=full_for_fields[title_offset:]
            descriptor=compact(raw_descriptor)[:200]
            if kind=='feat':
                m=re.match(r'\s*(起源|通用|战斗风格|传奇恩惠|种族|血族)专长',raw_descriptor)
                if m:fields['feat_category']=[(m[1],title_offset+m.start(),title_offset+m.end())]
            if kind=='spell':
                m=re.search(r'(戏法|[一二三四五六七八九十\d]+环)\s*(?:[｜|/：:]\s*)?(防护|咒法|预言|惑控|塑能|幻术|死灵|变化)',raw_descriptor[:250])
                if m:
                    fields['spell_level']=[(m[1],title_offset+m.start(),title_offset+m.end())]
                    fields['school']=[(m[2],title_offset+m.start(),title_offset+m.end())]
                for value,a,b in fields.get('casting_time',[]):
                    fields.setdefault('casting_time_detail',[]).append((value,a,b))
                    if '仪式' in value:fields.setdefault('ritual',[]).append(('是',a,b))
                for value,a,b in fields.get('duration',[]):
                    fields.setdefault('concentration',[]).append(('是' if value.startswith('专注') else '否',a,b))
            if kind=='magic_item':
                m=re.match(r'\s*(武器|护甲|奇物|戒指|魔杖|法杖|魔棒|药水|卷轴|弹药|盾牌|长剑|短剑|弯刀|匕首|巨剑|长棍|权杖|魔法船).*?(非普通|极珍稀|珍稀|普通|传说|神器|稀有|罕见)',raw_descriptor[:250],re.S)
                if m:
                    fields['item_type']=[(m[1],title_offset,title_offset+m.end())]
                    fields['rarity']=[(m[2],title_offset,title_offset+m.end())]
                attune=re.search(r'[（(]([^\n）)]{0,80}同调[^\n）)]{0,30})[）)]',raw_descriptor[:250])
                if attune:
                    required=not re.search(r'不需|无需',attune[1])
                    fields['attunement']=[('是' if required else '否',title_offset+attune.start(),title_offset+attune.end())]
                    if required:fields['attunement_requirements']=[(attune[1],title_offset+attune.start(),title_offset+attune.end())]
            if kind=='creature':
                m=re.search(r'(微型|小型|中型|大型|巨型|超巨型)\s*(异怪|野兽|天族|构装|龙类|元素|妖精|邪魔|巨人|类人|怪兽|泥怪|植物|亡灵)',full_for_fields[:500])
                if m:
                    fields['size']=[(m[1],m.start(),m.end())]
                    fields['creature_type']=[(m[2],m.start(),m.end())]
            output.append({'name':re.sub(r'\s+(?:机械|魔法)陷阱$','',c.name) if kind=='trap' else c.name,'start':c.start,'title_end':c.title_end,'end':c.end,'anchors':c.anchors,'parent_index':c.parent,
                'type':kind,'classification_rule':rule,'boundary_rule':c.method,'legacy_direct':c.local_legacy,'fields':fields,
                'observation_ids':[o['id'] for o in c.observations], 'body_hash':sha256(compact(full).encode()).hexdigest(),
                'level':c.rank,'is_primary':primary})
        # PHB/DMG calls this a table but the HTML encodes it as a simple list.
        for ul in doc.body.find_all('ul'):
            caption=ul.find_previous_sibling()
            if not caption or not re.fullmatch(r'基础设施\s*Basic Facilities',compact(caption.get_text(' ',strip=True)),re.I):continue
            for li in ul.find_all('li',recursive=False):
                name=compact(li.get_text(' ',strip=True));a,b=doc.span(li)
                if not name or len(name)>30:continue
                output.append({'name':name,'start':a,'title_end':b,'end':b,'anchors':[],'parent_index':None,
                    'type':'facility','classification_rule':'facility_list_signature','boundary_rule':'list_item',
                    'legacy_direct':False,'fields':{},'observation_ids':[],
                    'extra_facts':[('facility_kind','基础',{'path':path,'span':list(doc.span(caption))},'facility_list','原文明示')],
                    'body_hash':sha256(compact(doc.text[a:b]).encode()).hexdigest(),'level':9,'is_primary':False})
        table_entries,tables,table_issues=extract_tables(doc,cs)
        output.extend(table_entries)
        links=[]
        for a in doc.body.find_all('a',href=True):
            target=resolve_link(path,a['href'])
            if target and target[0].startswith('topics/'):
                links.append({'label':compact(a.get_text(' ',strip=True)), 'target_path':target[0],'target_anchor':target[1],'span':doc.span(a)})
        anchors={a:[list(doc.span(t)) for t in tags] for a,tags in doc.anchors.items()}
        issues=list(table_issues)
        for a,spans in anchors.items():
            if len(spans)>1:issues.append({'reason':'duplicate_anchor','detail':a,'span':spans[0]})
        out={'page':page,'candidates':output,'tables':tables,'links':links,'anchors':anchors,'issues':issues}
        doc.close();return out
    except Exception as e:
        page.update(parse_status='error',error=f'{type(e).__name__}: {e}')
        return {'page':page,'candidates':[],'tables':[],'links':[],'anchors':{},'issues':[]}


class Build:
    def __init__(self,web):
        self.web=web.resolve()
        self.registry=SourceRegistry(web)
        self.sources=self.registry.sources
        self.pages={};self.entries=[];self.facts=[];self.relations=[];self.tables=[];self.reviews=[]
        self.page_entries=defaultdict(list);self.locators=defaultdict(set);self.observations=[];self.ob_by_id={};self.ob_targets=defaultdict(list)
        self.anchor_spans={};self.raw_links=[]
        self.entry_by_id={}
        self.commit=subprocess.check_output(['git','-C',str(web),'rev-parse','HEAD'],text=True).strip() if (web/'.git').exists() else 'unversioned'
        self._review_ids=set()

    def review(self,target,reason,detail,evidence=None,priority='normal'):
        rid='review_'+digest(target,reason,dumps(detail),dumps(evidence or {}))
        if rid in self._review_ids:return
        self._review_ids.add(rid)
        self.reviews.append({'id':rid,'target':target,'reason':reason,'detail':detail,'evidence':evidence or {},'priority':priority})

    def fact(self,entry,field,value,evidence,rule,scope='entry',status='原文明示'):
        if value in ('',None):return
        f={'id':'fact_'+digest(entry,field,dumps(value),dumps(evidence),rule),'entry_id':entry,'field':field,'value':value,'evidence':evidence,'rule':rule,'scope':scope,'status':status}
        self.facts.append(f)

    def relation(self,subject,predicate,target_label,evidence,object_id='',status='原文明示',target_locator=''):
        self.relations.append({'id':'rel_'+digest(subject,predicate,object_id,target_label,dumps(evidence)), 'subject_id':subject,'predicate':predicate,
            'object_id':object_id,'target_label':target_label,'target_locator':target_locator,'evidence':evidence,'status':status})

    def index_pass(self):
        for file in sorted((self.web/'topics/速查').rglob('*')):
            if file.suffix.lower() not in {'.htm','.html'}:continue
            raw,_,_=read_page(file)
            if not re.search(r'<tr\b[^>]*(?:item|monster|spell)\s*=',raw,re.I):continue
            path=file.relative_to(self.web).as_posix();doc=Document(path,raw)
            for ob in doc.quickrefs():
                ob['fields']=observation_fields(ob)
                self.observations.append(ob);self.ob_by_id[ob['id']]=ob
                if ob['target_path'] and (self.web/ob['target_path']).is_file():
                    self.ob_targets[ob['target_path']].append(ob)
                else:
                    # Some source HTML omits ../ on otherwise exact topics paths.
                    rooted='topics/'+unquote_path(ob['href']).lstrip('/')
                    if (self.web/rooted).is_file() and not any(p=='..' for p in Path(rooted).parts):
                        ob['resolved_target_path']=rooted;ob['resolution_rule']='exact_topics_root'
                        self.ob_targets[rooted].append(ob)
                        self.review(ob['id'],'index_link_recovered',{'original_href':ob['href'],'resolved_path':rooted},{'path':path,'span':ob['span']},'low')
                    else:self.review(ob['id'],'index_target_missing',ob['href'],{'path':path,'span':ob['span']},'high')
            doc.close()
        print(f'索引行：{len(self.observations)}；正文目标：{len(self.ob_targets)}',flush=True)

    def consume(self,result):
        page=result['page'];path=page['path']
        owners,method=self.registry.owners(path)
        page.update(source_ids=owners,source_method=method,toc_nodes=[n['node_id'] for n in self.registry.page_toc.get(path,[])])
        page['characters']=len(page['text'])
        self.pages[path]=page
        if page['parse_status']=='error':
            self.review(path,'parse_error',page['error'],priority='high');return
        if page['role']!='document':return
        if not owners:self.review(path,'source_unresolved','未找到可靠来源归属',{'path':path})
        if page['encoding']!='utf-8':self.review(path,'encoding_inferred',page['encoding'],{'path':path},'high')
        if re.search(r'未翻译|暂无翻译|待翻译|暂缺|尚未翻译',page['text'][:600]):self.review(path,'possible_incomplete_content',compact(page['text'][:600]),{'path':path})
        for issue in result['issues']:self.review(path,issue['reason'],issue['detail'],{'path':path,'span':issue['span']})
        self.anchor_spans[path]=result['anchors']
        self.tables+=result['tables']
        self.raw_links += [(path,l) for l in result['links']]
        name_counts=Counter();local_entries=[]
        for c in result['candidates']:
            name_counts[key(c['name'])]+=1
            occurrence=name_counts[key(c['name'])]
            unique_anchors=[a for a in c['anchors'] if len(result['anchors'].get(a,[]))==1]
            locator=path+('#'+unique_anchors[0] if unique_anchors else '::'+c['name']+'::'+str(occurrence))
            entity='ent_'+digest(locator)
            eid='ver_'+digest(entity,c['body_hash'])
            zh,en=split_name(c['name'])
            rules={self.sources[s]['ruleset_hint'] for s in owners if self.sources[s]['ruleset_hint'] not in {'待核'}}
            ruleset=next(iter(rules)) if len(rules)==1 and '冲突' not in rules else '待核'
            archived=[s for s in owners if self.sources[s]['archived']]
            index_legacy=[self.ob_by_id[o] for o in c['observation_ids'] if self.ob_by_id[o]['legacy']]
            legacy='entry_marked' if c['legacy_direct'] or index_legacy else 'source_archived' if archived else 'unmarked'
            status='原文明示' if c['classification_rule'].endswith('_signature') or c['classification_rule'].endswith('_table') or c['classification_rule'] in {'index_or_statblock','spell_fields','stat_fields','item_descriptor','feat_descriptor','poison_descriptor','level_heading','level_statement','background_fields','species_fields','class_structure','name_label','equipment_table'} else '上下文推得' if c['type']!='section' else '未分类段落'
            e={'id':eid,'entity_id':entity,'identity_status':'provisional','name':compact(c['name']),'name_zh':zh,'name_en':en,'type':c['type'],
                'type_label':TYPE_LABELS[c['type']],'page_path':path,'start':c['start'],'end':c['end'],'locator':locator,'anchors':c['anchors'],
                'body_hash':c['body_hash'],'source_ids':owners,'source_names':[self.sources[s]['name'] for s in owners],
                'ruleset':ruleset,'ruleset_status':'上下文推得' if ruleset!='待核' else '待核','legacy':legacy,
            'classification_status':status,'classification_rule':c['classification_rule'],'boundary_rule':c['boundary_rule'],
                'fields':{},'field_conflicts':[],'observation_ids':c['observation_ids'],'parent_id':'','is_primary':c['is_primary'], 'snapshot':self.commit}
            local_entries.append(e);self.entries.append(e);self.entry_by_id[eid]=e;self.page_entries[path].append(e)
            self.locators[path].add(eid) if c['is_primary'] else None
            for a in c['anchors']:self.locators[path+'#'+a].add(eid)
            ev={'path':path,'span':[c['start'],min(c['title_end'],c['end'])],'locator':locator}
            self.fact(eid,'type',c['type'],ev,c['classification_rule'],status=status)
            for s in owners:
                self.fact(eid,'source_id',s,{'source_id':s,'toc_nodes':page['toc_nodes']},method,scope='book',status='上下文推得')
                rule=self.sources[s]['ruleset_hint']
                if rule!='待核':self.fact(eid,'ruleset',rule,{'source_id':s,'source_evidence':self.sources[s]['evidence']},'source_default',scope='book',status='上下文推得')
            if e['type']=='creature':
                role_context=path+' | '+self.registry.context(path)
                for role,pattern in [('NPC',r'/NPC/|/NPC\.| / 非玩家角色 / | / NPC / '),('伙伴',r'/魔宠/|/伙伴/|/协力者/| / 魔宠 / | / 协力者 / '),('坐骑',r'/坐骑/| / 坐骑 / ')]:
                    if re.search(pattern,role_context,re.I):self.fact(eid,'role',role,{'path':path,'toc_nodes':page['toc_nodes']},'role_context',status='上下文推得')
            if c['legacy_direct']:self.fact(eid,'legacy',True,{'path':path,'span':[c['start'],min(c['end'],c['title_end']+50)]},'title_marker',status='原文明示')
            for ob in index_legacy:self.fact(eid,'legacy',True,{'path':ob['path'],'span':ob['span'],'observation_id':ob['id']},'index_legacy_marker',scope='index')
            if archived:self.fact(eid,'legacy',True,{'sources':archived},'source_archive',scope='book',status='上下文推得')
            for field,values in c['fields'].items():
                for value,start,end in values:
                    a,b=c['start']+start,min(c['end'],c['start']+end)
                    rule='duration_concentration' if field=='concentration' else 'casting_ritual' if field=='ritual' else 'body_label'
                    self.fact(eid,field,normalize_value(field,value),{'path':path,'span':[a,b],'raw':page['text'][a:b]},rule,status='上下文推得' if field=='concentration' and value=='否' else '原文明示')
            for field,value,evidence,rule,status in c.get('extra_facts',[]):
                self.fact(eid,field,value,evidence,rule,status=status)
            for oid in c['observation_ids']:
                ob=self.ob_by_id[oid]
                for field,value in ob['fields'].items():
                    self.fact(eid,field,value,{'path':ob['path'],'span':ob['span'],'observation_id':oid},'quickref_cell',scope='index')
                self.fact(eid,'type',ob['kind'],{'path':ob['path'],'span':ob['span'],'observation_id':oid},'quickref_attribute',scope='index')
                if 'index_name' in c['boundary_rule'] and ob['target_anchor']:
                    self.review(eid,'anchor_recovered_by_name',ob['target_anchor'],{'path':path,'observation_id':oid},'low')
            if len(rules)>1 or '冲突' in rules:self.review(eid,'ruleset_conflict',sorted(rules),ev,'high')
        for i,(c,e) in enumerate(zip(result['candidates'],local_entries)):
            pi=c['parent_index']
            if pi is not None and pi<len(local_entries):
                parent=local_entries[pi];e['parent_id']=parent['id']
                pred='part_of' if e['type']=='class_feature' and parent['type'] in {'class','subclass'} else 'section_of'
                self.relation(e['id'],pred,parent['name'],{'path':path,'span':[e['start'],e['end']]},parent['id'],'上下文推得')
            if e['type']=='class_feature':
                owners_here=[p for p in local_entries if p['type'] in {'class','subclass'} and p['start']<=e['start'] and p['end']>=e['end']]
                if owners_here:
                    p=min(owners_here,key=lambda p:p['end']-p['start'])
                    self.relation(e['id'],'part_of',p['name'],{'path':path,'span':[e['start'],e['end']]},p['id'],'上下文推得')
        if not any(e['type']!='section' for e in local_entries):self.review(path,'unclassified_page',page['title'],{'path':path},'normal')
        matched={oid for e in local_entries for oid in e['observation_ids']}
        for ob in self.ob_targets.get(path,[]):
            if ob['id'] not in matched:
                self.review(ob['id'],'index_entry_unresolved',ob['name'],{'target_path':path,'target_anchor':ob['target_anchor'],'index_path':ob['path']},'high')

    def link_animal_catalogs(self):
        """Link catalog uses to uniquely identified core statblocks of the SAME ruleset.

        A purchase row is not a creature, and no price/capacity is copied into a
        statblock. Name equivalence does not merge identities or cross editions.
        """
        facts=defaultdict(list)
        for f in self.facts:facts[f['entry_id']].append(f)
        def animal_key(name):
            name=compact(name)
            # Printed inverted English names: Horse, Draft -> Draft Horse.
            zh,en=split_name(name)
            en=re.sub(r'^Horse,\s*(Draft|Riding)$',r'\1 Horse',en,flags=re.I)
            return key(en or zh)
        creatures=defaultdict(list)
        for e in self.entries:
            if e['type']=='creature':creatures[(e['ruleset'],animal_key(e['name']))].append(e)
        core={'5e':{'怪物图鉴2014','玩家手册2014'},'5r':{'怪物图鉴2025','玩家手册2024'}}
        for e in self.entries:
            if e['type']!='animal_offer':continue
            fs=facts[e['id']];refs=[f for f in fs if f['field']=='statblock_name']
            names=[f['value'] for f in refs] or [e['name']]
            evidence={'path':e['page_path'],'span':[e['start'],e['end']],'catalog_entry_id':e['id']}
            for name in names:
                # Donkey or Mule is an explicit alternative, not a fuzzy alias.
                for part in re.split(r'\s+或\s*',name):
                    targets=creatures.get((e['ruleset'],animal_key(part)),[]) if e['ruleset'] in core else []
                    targets=[t for t in targets if set(t['source_names']) & core[e['ruleset']]]
                    if len(targets)!=1:
                        self.review(e['id'],'animal_statblock_unresolved',{'name':part,'ruleset':e['ruleset'],'candidates':[t['id'] for t in targets]},evidence)
                        self.relation(e['id'],'uses_statblock',part,evidence,status='待核');continue
                    target=targets[0]
                    ev={**evidence,'matching_rule':'exact_name_same_ruleset_core','reference_evidence':[f['evidence'] for f in refs]}
                    self.relation(e['id'],'uses_statblock',part,ev,target['id'],'上下文推得')
                    self.relation(target['id'],'catalog_offer',e['name'],ev,e['id'],'上下文推得')
                    for f in fs:
                        if f['field']=='role':
                            self.fact(target['id'],'role',f['value'],{**ev,'role_evidence':f['evidence']},'animal_catalog_role',status='上下文推得')

    def link_mount_examples(self):
        # Explicit prose lists, not arbitrary mentions of riding in a statblock.
        # Only inflection normalization; identity still needs a unique same-edition core target.
        plurals={'pegasi':'pegasus','griffons':'griffon','hippogriffs':'hippogriff','giant sea horses':'giant sea horse'}
        for path,page in self.pages.items():
            for match in re.finditer(r'(飞行|水栖)坐骑[（(]如([^）)]+)[）)]',page['text']):
                containers=[e for e in self.page_entries[path] if e['start']<=match.start()<e['end']]
                if not containers:continue
                subject=min(containers,key=lambda e:e['end']-e['start'])
                for name in re.findall(r'[A-Za-z]+(?:[ \t]+[A-Za-z]+)*',match[2]):
                    canonical=plurals.get(name.lower(),name.lower())
                    targets=[e for e in self.entries if e['type']=='creature' and key(e['name_en'])==key(canonical)
                        and e['ruleset']==subject['ruleset'] and subject['ruleset'] in {'5e','5r'}
                        and set(e['source_names']) & {'怪物图鉴2014' if subject['ruleset']=='5e' else '怪物图鉴2025'}]
                    ev={'path':path,'span':[match.start(),match.end()],'raw_name':name,'canonical_name':canonical}
                    if len(targets)!=1:
                        self.review(subject['id'],'mount_example_unresolved',name,ev);continue
                    target=targets[0]
                    self.fact(target['id'],'role','坐骑',ev,'explicit_mount_example',status='上下文推得')
                    self.relation(subject['id'],'lists_mount',target['name'],ev,target['id'],'上下文推得')

    def finish(self):
        # Recover broken index links only with an exact English/Chinese name,
        # matching type and unambiguous source. Never fuzzy-match records.
        linked={o for e in self.entries for o in e['observation_ids']}
        names=defaultdict(list)
        for e in self.entries:
            for n in {key(e['name_en']),key(e['name_zh'])}- {''}:
                names[(e['type'],n)].append(e)
        recovered=set()
        for ob in self.observations:
            if ob['id'] in linked:continue
            zh,en=split_name(ob['name'])
            candidates=names.get((ob['kind'],key(en or zh)),[])
            owners=self.registry.alias_candidates(str(ob['fields'].get('source_code','')),ob['target_path'])
            candidates=[e for e in candidates if set(e['source_ids'])&set(owners)]
            if len(candidates)!=1:continue
            e=candidates[0];e['observation_ids'].append(ob['id']);ob['recovered_entry_id']=e['id'];recovered.add(ob['id'])
            for field,value in ob['fields'].items():self.fact(e['id'],field,value,{'path':ob['path'],'span':ob['span'],'observation_id':ob['id']},'quickref_exact_name_recovery',scope='index')
            self.review(ob['id'],'index_link_recovered',{'original_href':ob['href'],'entry_id':e['id'],'locator':e['locator']},{'path':ob['path'],'span':ob['span']},'low')
        self.reviews=[r for r in self.reviews if not (r['target'] in recovered and r['reason'] in {'index_target_missing','index_entry_unresolved'})]
        self.link_animal_catalogs()
        self.link_mount_examples()
        # Deduplicate evidence without dropping alternative statements.
        self.facts=list({f['id']:f for f in self.facts}.values())
        facts=defaultdict(lambda:defaultdict(list))
        for f in self.facts:facts[f['entry_id']][f['field']].append(f)
        multi={'source_id','source_code','spell_classes','type','ruleset','role'}
        for e in self.entries:
            for field,fs in facts[e['id']].items():
                values={dumps(f['value']):f['value'] for f in fs}
                e['fields'][field]=list(values.values()) if len(values)>1 else next(iter(values.values()))
                multirarity=field=='rarity' and any('多种' in str(v) for v in values.values())
                if len(values)>1 and field not in multi and not multirarity:
                    e['field_conflicts'].append(field)
                    self.review(e['id'],'field_conflict',{'field':field,'values':list(values.values())},{'fact_ids':[f['id'] for f in fs]},'high')
            if e['type']=='background' and e['fields'].get('granted_feat'):
                for f in facts[e['id']]['granted_feat']:
                    self.relation(e['id'],'grants_feat',f['value'],f['evidence'])
            if e['fields'].get('prerequisite'):
                for f in facts[e['id']]['prerequisite']:self.relation(e['id'],'requires_description',f['value'],f['evidence'])
            for oid in e['observation_ids']:
                ob=self.ob_by_id[oid]
                if ob['kind']=='spell':
                    listed={c for c in CLASSES if re.search(r'(?:^|\s)'+re.escape(c)+r'(?:\s|$)',ob['tags'])}
                    if not listed:
                        listed={CLASS_SHORT[t] for t in str(ob['fields'].get('spell_classes','')).split() if t in CLASS_SHORT}
                    for name in sorted(listed):self.relation(e['id'],'listed_for_class',name,{'path':ob['path'],'span':ob['span'],'observation_id':oid},status='原文明示')
        # Resolve links against anchors first, then the unique containing record.
        for path,link in self.raw_links:
            candidates=[e for e in self.page_entries[path] if e['start']<=link['span'][0]<e['end']]
            if not candidates:continue
            subject=min(candidates,key=lambda e:e['end']-e['start'])
            targetloc=link['target_path']+('#'+link['target_anchor'] if link['target_anchor'] else '')
            targets=set(self.locators.get(targetloc,set()))
            if not targets and link['target_anchor']:
                spans=self.anchor_spans.get(link['target_path'],{}).get(link['target_anchor'],[])
                if len(spans)==1:
                    containers=[e for e in self.page_entries.get(link['target_path'],[]) if e['start']<=spans[0][0]<e['end']]
                    if containers:targets={min(containers,key=lambda e:e['end']-e['start'])['id']}
            target=next(iter(targets)) if len(targets)==1 else ''
            self.relation(subject['id'],'references',link['label'],{'path':path,'span':list(link['span'])},target,'原文明示' if target else '待核',targetloc)
        # Cross-page class ownership is recorded from the original TOC, not quick lookup placement.
        for e in self.entries:
            if e['type'] not in {'subclass','class_feature'}:continue
            ns=[n for n in self.registry.page_toc.get(e['page_path'],[]) if not n['toc_path'].startswith('本书速查')]
            for node in ns:
                pid=node['parent_id']
                while pid:
                    parent=self.registry.nodes[pid]
                    pp=unquote_path(parent['href'])
                    matches=[x for x in self.page_entries.get(pp,[]) if x['type']=='class']
                    if len(matches)==1:
                        p=matches[0]
                        if p['id']!=e['id']:self.relation(e['id'],'belongs_to_class',p['name'],{'path':'webhelpcontents.htm','node_id':node['node_id'],'parent_node_id':pid},p['id'],'上下文推得')
                        break
                    pid=parent['parent_id']
        self.relations=list({r['id']:r for r in self.relations}.values())
        for s in self.sources.values():
            if s['coverage']=='catalog_only':self.review(s['id'],'catalog_without_locator',s['name'],{'catalog_ids':s['catalog_ids']})
            if s['ruleset_hint'] in {'待核','冲突'} and s['paths']:self.review(s['id'],'source_ruleset_unresolved',s['name'],{'source_evidence':s['evidence']})
        # Equality groups are suggestions, never automatic cross-source merges.
        names=defaultdict(list)
        for e in self.entries:
            if e['type']!='section' and e['name_en']:names[(e['type'],key(e['name_en']))].append(e)
        self.identity_candidates=[]
        for (kind,name),es in sorted(names.items()):
            if len(es)>1:
                bodies={e['body_hash'] for e in es}
                self.identity_candidates.append({'group_id':'match_'+digest(kind,name),'type':kind,'english_key':name,'entry_ids':[e['id'] for e in es],
                    'names':[e['name'] for e in es],'source_names':sorted({s for e in es for s in e['source_names']}),'body_hashes':sorted(bodies),
                    'status':'exact_text_candidate' if len(bodies)==1 else 'same_name_candidate'})
        self.facts.sort(key=lambda f:f['id']);self.relations.sort(key=lambda r:r['id']);self.reviews.sort(key=lambda r:(r['reason'],r['target'],r['id']))

    def validate(self,expected):
        assert set(self.pages)==set(expected),'Every input must have an outcome'
        assert len(self.entries)==len(self.entry_by_id),'Version IDs must be unique'
        for e in self.entries:
            assert 0<=e['start']<=e['end']<=len(self.pages[e['page_path']]['text']),e['id']
            assert all(s in self.sources for s in e['source_ids'])
        for f in self.facts:
            assert f['entry_id'] in self.entry_by_id
            ev=f['evidence']
            if 'span' in ev and ev.get('path') in self.pages:
                a,b=ev['span'];assert 0<=a<=b<=len(self.pages[ev['path']]['text']),(f, len(self.pages[ev['path']]['text']))
                if 'raw' in ev:assert ev['raw']==self.pages[ev['path']]['text'][a:b]
        for r in self.relations:
            assert r['subject_id'] in self.entry_by_id
            assert not r['object_id'] or r['object_id'] in self.entry_by_id
        for path,page in self.pages.items():
            assert sha256((self.web/path).read_bytes()).hexdigest()==page['sha256'],('Source changed during build',path)
        return {'input_accounting':'pass','unique_ids':'pass','evidence_ranges':'pass','raw_evidence':'pass','foreign_keys':'pass','source_unchanged':'pass'}

    def export(self,out,validation,total_inputs,elapsed):
        out.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(out/'catalog.sqlite')
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE sources(id TEXT PRIMARY KEY,name TEXT NOT NULL,origin TEXT,ruleset_hint TEXT,release_status TEXT,source_type TEXT,archived INTEGER,coverage TEXT,data TEXT NOT NULL);
        CREATE TABLE pages(path TEXT PRIMARY KEY,title TEXT,role TEXT,parse_status TEXT,sha256 TEXT,text TEXT,source_ids TEXT,data TEXT NOT NULL);
        CREATE TABLE entities(id TEXT PRIMARY KEY,identity_status TEXT);
        CREATE TABLE entries(id TEXT PRIMARY KEY,entity_id TEXT REFERENCES entities(id),name TEXT,name_zh TEXT,name_en TEXT,type TEXT,page_path TEXT REFERENCES pages(path),start INTEGER,end INTEGER,ruleset TEXT,legacy TEXT,classification_status TEXT,fields TEXT,data TEXT NOT NULL);
        CREATE TABLE entry_sources(entry_id TEXT REFERENCES entries(id),source_id TEXT REFERENCES sources(id),PRIMARY KEY(entry_id,source_id));
        CREATE TABLE facts(id TEXT PRIMARY KEY,entry_id TEXT REFERENCES entries(id),field TEXT,value TEXT,evidence TEXT,rule TEXT,scope TEXT,status TEXT);
        CREATE TABLE relations(id TEXT PRIMARY KEY,subject_id TEXT REFERENCES entries(id),predicate TEXT,object_id TEXT REFERENCES entries(id),target_label TEXT,target_locator TEXT,evidence TEXT,status TEXT);
        CREATE TABLE raw_tables(id TEXT PRIMARY KEY,page_path TEXT REFERENCES pages(path),start INTEGER,end INTEGER,data TEXT);
        CREATE TABLE index_observations(id TEXT PRIMARY KEY,page_path TEXT,target_path TEXT,target_anchor TEXT,kind TEXT,data TEXT);
        CREATE TABLE review_queue(id TEXT PRIMARY KEY,target TEXT,reason TEXT,priority TEXT,data TEXT);
        CREATE TABLE identity_candidates(group_id TEXT PRIMARY KEY,type TEXT,status TEXT,data TEXT);
        CREATE INDEX entry_type_idx ON entries(type);
        CREATE INDEX entry_ruleset_idx ON entries(ruleset);
        CREATE INDEX entry_name_idx ON entries(name_en);
        CREATE INDEX fact_entry_idx ON facts(entry_id,field);
        CREATE INDEX relation_subject_idx ON relations(subject_id,predicate);
        CREATE INDEX entry_source_idx ON entry_sources(source_id,entry_id);
        CREATE VIEW entry_text AS SELECT e.id,e.name,e.type,e.ruleset,e.legacy,p.path,substr(p.text,e.start+1,e.end-e.start) AS body FROM entries e JOIN pages p ON p.path=e.page_path;
        ''')
        sources=sorted(self.sources.values(),key=lambda s:s['id'])
        for s in sources:db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(s['id'],s['name'],s['origin'],s['ruleset_hint'],s['release_status'],s['source_type'],s['archived'],s['coverage'],dumps(s)))
        for p in self.pages.values():db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?)',(p['path'],p['title'],p['role'],p['parse_status'],p['sha256'],p['text'],dumps(p['source_ids']),dumps({k:v for k,v in p.items() if k!='text'})))
        for e in self.entries:
            db.execute('INSERT OR IGNORE INTO entities VALUES(?,?)',(e['entity_id'],e['identity_status']))
            db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(e['id'],e['entity_id'],e['name'],e['name_zh'],e['name_en'],e['type'],e['page_path'],e['start'],e['end'],e['ruleset'],e['legacy'],e['classification_status'],dumps(e['fields']),dumps(e)))
            for s in e['source_ids']:db.execute('INSERT INTO entry_sources VALUES(?,?)',(e['id'],s))
        db.executemany('INSERT INTO facts VALUES(?,?,?,?,?,?,?,?)',[(f['id'],f['entry_id'],f['field'],dumps(f['value']),dumps(f['evidence']),f['rule'],f['scope'],f['status']) for f in self.facts])
        db.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?,?,?)',[(r['id'],r['subject_id'],r['predicate'],r['object_id'] or None,r['target_label'],r['target_locator'],dumps(r['evidence']),r['status']) for r in self.relations])
        db.executemany('INSERT INTO raw_tables VALUES(?,?,?,?,?)',[(t['id'],t['page_path'],t['start'],t['end'],dumps(t)) for t in self.tables])
        db.executemany('INSERT INTO index_observations VALUES(?,?,?,?,?,?)',[(o['id'],o['path'],o['target_path'],o['target_anchor'],o['kind'],dumps(o)) for o in self.observations])
        db.executemany('INSERT INTO review_queue VALUES(?,?,?,?,?)',[(r['id'],r['target'],r['reason'],r['priority'],dumps(r)) for r in self.reviews])
        db.executemany('INSERT INTO identity_candidates VALUES(?,?,?,?)',[(r['group_id'],r['type'],r['status'],dumps(r)) for r in self.identity_candidates])
        for name,types in VIEW_TYPES.items():
            where='type IN ('+','.join("'"+x+"'" for x in sorted(types))+')'
            if name=='奖励':where+=" OR (type='feat' AND json_extract(fields,'$.feat_category')='传奇恩惠')"
            db.execute('CREATE VIEW "'+name+'" AS SELECT * FROM entries WHERE '+where)
        for name,(where,_) in EXTRA_VIEWS.items():db.execute('CREATE VIEW "'+name+'" AS SELECT * FROM entries WHERE '+where)
        summary={'snapshot_commit':self.commit,'input_html_files':total_inputs,'processed_html_files':len(self.pages),'full_scan':len(self.pages)==total_inputs,
            'page_roles':dict(Counter(p['role'] for p in self.pages.values())),'parse_status':dict(Counter(p['parse_status'] for p in self.pages.values())),
            'sources':len(sources),'source_coverage':dict(Counter(s['coverage'] for s in sources)), 'versions':len(self.entries),
            'typed_versions':sum(e['type']!='section' for e in self.entries),'types':dict(Counter(e['type'] for e in self.entries)),
            'classification_status':dict(Counter(e['classification_status'] for e in self.entries)),'ruleset':dict(Counter(e['ruleset'] for e in self.entries if e['type']!='section')),
            'legacy':dict(Counter(e['legacy'] for e in self.entries if e['type']!='section')),'index_rows':len(self.observations),
            'index_rows_linked':len({o for e in self.entries for o in e['observation_ids']}),'facts':len(self.facts),'relations':len(self.relations),'raw_tables':len(self.tables),
            'identity_candidate_groups':len(self.identity_candidates),'review_reasons':dict(Counter(r['reason'] for r in self.reviews)),
            'validation':validation,'runtime_seconds':round(elapsed,2),'identity_policy':'provisional locator identities; no name-based merge',
            'parser':'beautifulsoup4/html5lib','pipeline_sha256':sha256(b''.join(p.read_bytes() for p in sorted((PROJECT/'scripts').glob('*.py')))).hexdigest()}
        manifest=sha256('\n'.join(p+':'+self.pages[p]['sha256'] for p in sorted(self.pages)).encode()).hexdigest()
        summary['input_manifest_sha256']=manifest
        db.execute('INSERT INTO metadata VALUES(?,?)',('summary',dumps(summary)))
        db.commit()
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        db.close()
        csv_write(out/'sources.csv',sources,['id','name','origin','release_status','source_type','ruleset_hint','archived','setting','coverage','aliases','paths','catalog_ids','evidence'])
        csv_write(out/'source_catalog.csv',self.registry.catalog,['id','section','name_zh','name_en','abbreviations','aliases','level','ruleset_hint','evidence'])
        csv_write(out/'pages.csv',self.pages.values(),['path','title','role','parse_status','encoding','sha256','characters','source_ids','source_method','toc_nodes','error'])
        fields=['id','entity_id','name','name_zh','name_en','type','ruleset','ruleset_status','legacy','classification_status','source_names','page_path','locator','start','end','fields','field_conflicts']
        csv_write(out/'entries.csv',self.entries,fields)
        for name,types in VIEW_TYPES.items():csv_write(out/'views'/f'{name}.csv',[e for e in self.entries if e['type'] in types or (name=='奖励' and e['type']=='feat' and e['fields'].get('feat_category')=='传奇恩惠')],fields)
        for name,(_,match) in EXTRA_VIEWS.items():csv_write(out/'views'/f'{name}.csv',[e for e in self.entries if match(e)],fields)
        csv_write(out/'review_queue.csv',self.reviews,['id','target','reason','priority','detail','evidence'])
        csv_write(out/'identity_candidates.csv',self.identity_candidates,['group_id','type','english_key','entry_ids','names','source_names','status','body_hashes'])
        csv_write(out/'relations.csv',self.relations,['id','subject_id','predicate','object_id','target_label','target_locator','evidence','status'])
        jsonl_write(out/'facts.jsonl',self.facts)
        jsonl_write(out/'index_observations.jsonl',self.observations)
        (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        (out/'.generated').write_text('Generated by 5e-anna/scripts/build.py\n')
        lines=['# 提取结果','',f"基线：`{self.commit[:12]}`；扫描 {len(self.pages)}/{total_inputs} 页。",'',
            '| 项目 | 数量 |','|---|---:|',f"| 已分类版本候选 | {summary['typed_versions']} |",f"| 保留的未分类段落 | {summary['types'].get('section',0)} |",f"| 来源记录（含仅名录） | {len(sources)} |",f"| 索引行已关联 / 总数 | {summary['index_rows_linked']} / {len(self.observations)} |",f"| 字段证据 | {len(self.facts)} |",f"| 关系 | {len(self.relations)} |",'',
            '**全量扫描不等于全部分类已核实。** 页面、条目、版本候选分别计数；同名不自动合并。',
            '原文明示与上下文推断均带证据；未分类、冲突、断链及来源歧义留在审核队列。',
            '旧版状态区分条目标记、整书归档、未见标记；未见标记不表示新版。','',
            '数据：`catalog.sqlite`；分类表：`views/`；待核：`review_queue.csv`；统计：`summary.json`。','',
            '校验：输入逐页登记、ID唯一、证据范围、外键与SQLite完整性。分类准确率需抽样，不能由这些检查替代。']
        (out/'结果.md').write_text('\n'.join(lines)+'\n')
        return summary


def unquote_path(href):
    from urllib.parse import unquote,urlsplit
    return unquote(urlsplit(href).path)


def publish(stage,dest):
    """Replace generated files only. Never delete unrelated user files."""
    dest.mkdir(parents=True,exist_ok=True)
    for p in sorted(stage.rglob('*')):
        if p.is_file():
            target=dest/p.relative_to(stage);target.parent.mkdir(parents=True,exist_ok=True);os.replace(p,target)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,default=DEFAULT_WEB);ap.add_argument('--output',type=Path,default=PROJECT/'data/extracted')
    ap.add_argument('--workers',type=int,default=min(4,os.cpu_count() or 1));ap.add_argument('--limit',type=int,help='development only; summary explicitly marks partial scans')
    args=ap.parse_args()
    started=time.monotonic();b=Build(args.source)
    paths=sorted(p.relative_to(args.source).as_posix() for p in (args.source/'topics').rglob('*') if p.suffix.lower() in {'.htm','.html'})
    total=len(paths)
    b.registry.register_orphans([p for p in paths if page_role(p)=='document'])
    b.index_pass()
    if args.limit:paths=paths[:args.limit]
    jobs=[(str(args.source.resolve()),p,b.ob_targets.get(p,[]),b.registry.context(p)) for p in paths]
    if args.workers==1:
        results=map(parse_one,jobs)
        for i,result in enumerate(results,1):
            b.consume(result)
            if i%500==0:print(f'正文：{i}/{len(paths)}；候选：{len(b.entries)}',flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for i,result in enumerate(pool.map(parse_one,jobs,chunksize=8),1):
                b.consume(result)
                if i%500==0:print(f'正文：{i}/{len(paths)}；候选：{len(b.entries)}',flush=True)
    b.finish();validation=b.validate(paths)
    with tempfile.TemporaryDirectory(prefix='.build-',dir=PROJECT) as tmp:
        summary=b.export(Path(tmp),validation,total,time.monotonic()-started)
        publish(Path(tmp),args.output)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
