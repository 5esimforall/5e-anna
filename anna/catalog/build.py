#!/usr/bin/env python3
"""Build the complete local classification dataset. No network; source is read-only."""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
from concurrent.futures import ProcessPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

from anna.serialization import canonical
from anna.source.pages import read_page
from anna.source.html import Document, compact, key, digest, parse_name, split_name, resolve_link
from anna.catalog.frames import extract_page_entries, quickref_rows
from anna.catalog.signatures import CLASSES, CLASS_SHORT, printed_generation, skill_notation
from anna.catalog.types import TYPE_LABELS, evidence_status, type_conflicts
from anna.source.registry import QUICK_REFERENCE, SourceRegistry
from anna.source.search import extract_body_text as extract_search_body, extract_title as extract_search_title, index_signature, load_toc_titles

from anna.paths import PROJECT, WEB as DEFAULT_WEB, COUNTERPARTS as DEFAULT_COUNTERPARTS
from anna.review.store import DEFAULT_ANNOTATIONS, require_current_annotations, pipeline_hash, extraction_hash
INDEX_FIELDS={
    '物品名':'name','怪物名':'name','法术名':'name','稀有度':'rarity','类别':'item_type','子类别':'item_subtypes',
    '同调':'attunement','同调条件':'attunement_requirements','来源':'source_code','体型':'size','类型':'creature_type',
    '传奇动作':'legendary_actions','挑战等级':'cr','环阶':'spell_level','等级':'spell_level','学派':'school',
    '职业':'spell_classes','施法时间':'casting_time','施法动作':'casting_time','言语':'verbal','姿势':'somatic','材料':'material',
    '仪式':'ritual','专注':'concentration',
}
EXCLUDED_DIRS={'速查 - 副本 - 副本','空白页模板','template','template2','Generator','格式转换工具包','鸣谢'}


# Share of a source's marked entries that must print in one generation's layout to set its default.
PRINTED_AGREEMENT=0.9


def page_role(path):
    parts=Path(path).parts
    if len(parts)>1 and parts[1] in EXCLUDED_DIRS:return 'excluded_template_or_copy'
    if len(parts)>1 and parts[1]=='速查':return 'index'
    if parts[-1] in {'写在前面.html','分隔符.htm','旧版说明.htm','更新日志.html','鸣谢列表.htm'}:return 'editorial'
    return 'document'


def normalize_value(field,value):
    value=compact(value)
    if field=='attunement_requirements':
        return re.sub(r'同调$','',re.sub(r'^(?:需要|需)','',value)).strip()
    if field in {'attunement','legendary_actions','ritual','concentration','verbal','somatic'}:
        if value in {'是','有','√','V','S','仪式','专注'} or (field=='attunement' and value=='特殊'):return True
        if value in {'否','无','×','非仪','非专'}:return False
    if field in {'ac','hp','cr'}:
        m=re.match(r'\d+(?:/\d+)?',value)
        if m:return m[0]
    if field=='casting_time':
        if re.match(r'^(?:[1１一]\s*个?)?附赠',value):return '附赠动作'
        if re.match(r'^(?:[1１一]\s*个?)?反应',value):return '反应'
        if re.match(r'^(?:[1１一]\s*个?)?动作',value):return '动作'
        if re.match(r'^\d+\s*(?:分钟|小时|轮)|^其他',value):return '其他'
        return value
    return value


def attunement_req_key(value:str)->tuple[str,...]:
    """Canonical token tuple for equivalent attunement requirement wordings."""
    s=re.sub(r'的生物$','',compact(value))
    parts=[p.strip() for p in re.split(r'\s*(?:/|／|、|，|,|或)\s*',s) if p.strip()]
    if parts and parts[0].startswith('缺失'):
        parts=[parts[0]]+[p if p.startswith('缺失') else '缺失'+p for p in parts[1:]]
    return tuple(sorted(set(parts)))


def _split_dual_tokens(value:object)->list[str]:
    return [t.strip() for t in re.split(r'[/／或]',str(value)) if t.strip()]


MULTI_VALUE_FIELDS={'source_id','source_code','spell_classes','type','ruleset','role'}


def resolve_entry_field(field:str,fs:list[dict])->tuple[object,bool]:
    """Combine one entry's extracted and index facts for `field`, flagging only unresolved disagreements."""
    values={canonical(f['value']):f['value'] for f in fs}
    if len(values)<=1:
        return next(iter(values.values())),False
    if field=='attunement_requirements':
        groups={attunement_req_key(str(f['value'])) for f in fs}
        if len(groups)==1:
            return next((f['value'] for f in fs if f['scope']=='entry'),next(iter(values.values()))),False
    elif field in {'size','creature_type'}:
        entry_vals={canonical(f['value']):f['value'] for f in fs if f['scope']=='entry'}
        other_facts=[f for f in fs if f['scope']!='entry']
        if len(entry_vals)==1 and other_facts:
            entry_val=next(iter(entry_vals.values()))
            dual_tokens=set(_split_dual_tokens(entry_val))
            if '/' in str(entry_val) and all((toks:=_split_dual_tokens(f['value'])) and set(toks)<=dual_tokens for f in other_facts):
                return entry_val,False
    multirarity=field=='rarity' and any('多种' in str(v) for v in values.values())
    conflict=field not in MULTI_VALUE_FIELDS and not multirarity
    return list(values.values()),conflict


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
    web, path, observations, context, toc_place, core_rule_source = args
    page = {'path': path, 'role': page_role(path), 'parse_status': 'ok', 'error': '', 'title': '', 'text': '', 'encoding': '', 'sha256': ''}
    try:
        raw, encoding, h = read_page(Path(web) / path)
        page.update(encoding=encoding, sha256=h)
        search_doc = {'url': path, 'html_title': extract_search_title(raw), 'text': extract_search_body(raw)}
        if page['role'] in {'excluded_template_or_copy', 'editorial'}:
            page['parse_status'] = 'excluded'
            return {'page': page, 'candidates': [], 'tables': [], 'links': [], 'anchors': {}, 'dom': None, 'search': search_doc, 'issues': []}
        doc = Document(path, raw)
        try:
            page.update(title=doc.title, text=doc.text)
            dom = doc.dom_snapshot()
            if page['role'] == 'index':
                return {'page': page, 'candidates': [], 'tables': [], 'links': [], 'anchors': dom['anchors'], 'dom': dom, 'search': search_doc, 'issues': []}
            entries, tables, table_issues = extract_page_entries(doc, observations, context, toc_place, core_rule_source)
        finally:
            doc.close()
        links = [
            {'label': link['link_text'], 'target_path': target[0], 'target_anchor': target[1], 'span': tuple(link['span'])}
            for link in dom['body_links']
            if (target := resolve_link(path, link['href'])) and target[0].startswith('topics/')
        ]
        anchors = dom['anchors']
        issues = list(table_issues) + [
            {'reason': 'duplicate_anchor', 'detail': a, 'span': spans[0]}
            for a, spans in anchors.items()
            if len(spans) > 1
        ]
        return {'page': page, 'candidates': entries, 'tables': tables, 'links': links, 'anchors': anchors, 'dom': dom, 'search': search_doc, 'issues': issues}
    except Exception as e:
        page.update(parse_status='error', error=f'{type(e).__name__}: {e}')
        return {'page': page, 'candidates': [], 'tables': [], 'links': [], 'anchors': {}, 'dom': None, 'search': None, 'issues': []}


class Build:
    def __init__(self,web):
        self.web=web.resolve()
        self.registry=SourceRegistry(web)
        self.sources=self.registry.sources
        self.pages={};self.dom_by_path={};self.search_rows={};self.entries=[];self.facts=[];self.relations=[];self.tables=[];self.reviews=[]
        self.page_entries=defaultdict(list);self.locators=defaultdict(set);self.observations=[];self.ob_by_id={};self.ob_targets=defaultdict(list)
        self.anchor_spans={};self.raw_links=[]
        self.entry_by_id={};self.printed={}
        self.commit=subprocess.check_output(['git','-C',str(web),'rev-parse','HEAD'],text=True).strip() if (web/'.git').exists() else 'unversioned'
        self._review_ids=set()

    def review(self,target,reason,detail,evidence=None,priority='normal'):
        rid='review_'+digest(target,reason,canonical(detail),canonical(evidence or {}))
        if rid in self._review_ids:return
        self._review_ids.add(rid)
        self.reviews.append({'id':rid,'target':target,'reason':reason,'detail':detail,'evidence':evidence or {},'priority':priority})

    def fact(self,entry,field,value,evidence,rule,scope='entry',status='原文明示'):
        if value in ('',None):return
        f={'id':'fact_'+digest(entry,field,canonical(value),canonical(evidence),rule),'entry_id':entry,'field':field,'value':value,'evidence':evidence,'rule':rule,'scope':scope,'status':status}
        self.facts.append(f)

    def relation(self,subject,predicate,target_label,evidence,object_id='',status='原文明示',target_locator=''):
        self.relations.append({'id':'rel_'+digest(subject,predicate,object_id,target_label,canonical(evidence)), 'subject_id':subject,'predicate':predicate,
            'object_id':object_id,'target_label':target_label,'target_locator':target_locator,'evidence':evidence,'status':status})

    def index_pass(self):
        for file in sorted((self.web/'topics/速查').rglob('*')):
            if file.suffix.lower() not in {'.htm','.html'}:continue
            raw,_,_=read_page(file)
            if not re.search(r'<tr\b[^>]*(?:item|monster|spell)\s*=',raw,re.I):continue
            path = file.relative_to(self.web).as_posix()
            doc = Document(path, raw)
            for ob in quickref_rows(doc):
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
        print(f'Index rows: {len(self.observations)}; body targets: {len(self.ob_targets)}',flush=True)

    def parse_job(self,path):
        owners,_=self.registry.owners(path)
        source=self.sources[owners[0]] if len(owners)==1 else {}
        core_rule_source=source.get('reference_role')=='core_rules' and source['ruleset_hint'] in {'5e','5r'}
        return (str(self.web),path,self.ob_targets.get(path,[]),self.registry.context(path),self.registry.toc_place(path),core_rule_source)

    def consume(self,result):
        page=result['page'];path=page['path']
        owners,method=self.registry.owners(path)
        page.update(source_ids=owners,source_method=method,toc_nodes=[n['node_id'] for n in self.registry.page_toc.get(path,[])])
        page['characters']=len(page['text'])
        self.pages[path]=page
        if result.get('dom') is not None:
            self.dom_by_path[path]=result['dom']
        if result.get('search') is not None:
            self.search_rows[path]=result['search']
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
            name_parse=parse_name(c['name']);zh,en=name_parse['name_zh'],name_parse['name_en']
            archived=[s for s in owners if self.sources[s]['archived']]
            index_legacy=[self.ob_by_id[o] for o in c['observation_ids'] if self.ob_by_id[o]['legacy']]
            legacy='entry_marked' if c['legacy_direct'] or index_legacy else 'source_archived' if archived else 'unmarked'
            status=evidence_status(c['classification_rule'],c['type'])
            e={'id':eid,'entity_id':entity,'identity_status':'provisional','name':compact(c['name']),'name_zh':zh,'name_en':en,'name_parse':name_parse,'type':c['type'],
                'type_label':TYPE_LABELS[c['type']],'page_path':path,'start':c['start'],'title_end':c['title_end'],'end':c['end'],'locator':locator,'anchors':c['anchors'],
                'body_hash':c['body_hash'],'source_ids':owners,'source_names':[self.sources[s]['name'] for s in owners],
                'legacy':legacy,
            'classification_status':status,'classification_rule':c['classification_rule'],'boundary_rule':c['boundary_rule'],
                'type_conflicts':type_conflicts(c['type'],c.get('printed_types',[])),
                'fields':{},'field_conflicts':[],'observation_ids':c['observation_ids'],'parent_id':'','is_primary':c['is_primary'], 'snapshot':self.commit}
            local_entries.append(e);self.entries.append(e);self.entry_by_id[eid]=e;self.page_entries[path].append(e)
            if c.get('slot_collection'):e['slot_collection']=c['slot_collection']
            if c.get('slot_definition'):e['slot_definition']=c['slot_definition']
            if c.get('table_span'):e['table_span']=c['table_span']
            self.locators[path].add(eid) if c['is_primary'] else None
            for a in c['anchors']:self.locators[path+'#'+a].add(eid)
            ev={'path':path,'span':[c['start'],min(c['title_end'],c['end'])],'locator':locator}
            if name_parse['status']=='review':
                self.review(eid,'name_structure_unresolved',name_parse,ev)
            for note in name_parse['annotations']:
                if note['role']=='level_label':self.fact(eid,'level',note['value'],{**ev,'name_spans':note['spans']},'name_level_label')
            self.fact(eid,'type',c['type'],ev,c['classification_rule'],status=status)
            for s in owners:
                self.fact(eid,'source_id',s,{'source_id':s,'toc_nodes':page['toc_nodes']},method,scope='book',status='上下文推得')
            self.source_ruleset(e,ev)
            printed=printed_generation(c['type'],page['text'][c['start']:c['end']])
            if printed:self.printed[eid]=(printed[0],[c['start']+printed[1][0],c['start']+printed[1][1]])
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

    def source_ruleset(self,e,ev):
        """An entry's rule generation defaults to its sources'; sources that disagree leave it for review."""
        rules={self.sources[s]['ruleset_hint'] for s in e['source_ids']}-{'待核'}
        e['ruleset']=next(iter(rules)) if len(rules)==1 and '冲突' not in rules else '待核'
        e['ruleset_status']='上下文推得' if e['ruleset']!='待核' else '待核'
        for s in e['source_ids']:
            rule=self.sources[s]['ruleset_hint']
            if rule!='待核':self.fact(e['id'],'ruleset',rule,{'source_id':s,'source_evidence':self.sources[s]['evidence']},'source_default',scope='book',status='上下文推得')
        if len(rules)>1 or '冲突' in rules:self.review(e['id'],'ruleset_conflict',sorted(rules),ev,'high')

    def infer_source_rulesets(self):
        """A source the catalog leaves 待核 (adventures, digital releases, UA) still prints its stat blocks
        and spells in one generation's layout. When nearly all of its marked entries agree, that layout
        sets the source default; an entry printed unlike its source's generation is queued for a look."""
        votes=defaultdict(Counter)
        for eid,(rule,_) in self.printed.items():
            for s in self.entry_by_id[eid]['source_ids']:votes[s][rule]+=1
        inferred=set()
        for sid,count in votes.items():
            source=self.sources[sid];rule,agreeing=count.most_common(1)[0]
            if source['ruleset_hint']=='待核' and agreeing>=PRINTED_AGREEMENT*sum(count.values()):
                source['ruleset_hint']=rule;inferred.add(sid)
                source['evidence'].append({'rule':'printed_generation','votes':dict(sorted(count.items())),'status':'上下文推得'})
        for e in self.entries:
            if inferred&set(e['source_ids']):self.source_ruleset(e,{'path':e['page_path'],'span':[e['start'],min(e['title_end'],e['end'])],'locator':e['locator']})
        for eid,(rule,span) in self.printed.items():
            e=self.entry_by_id[eid]
            if e['ruleset'] in {'5e','5r'} and e['ruleset']!=rule:
                self.review(eid,'printed_generation_mismatch',{'ruleset':e['ruleset'],'printed':rule},{'path':e['page_path'],'span':span},'low')

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

    def link_skill_abilities(self):
        """A skill printed as a lead states its ability where its text applies it: 力量（运动）检定. The abilities read
        there are the ones the rules define (entries typed ability), not a word list. Skills listed in a definition
        table already carry the table's 属性 cell."""
        abilities={e['name_zh'] for e in self.entries if e['type']=='ability' and e['name_zh']}
        stated={f['entry_id'] for f in self.facts if f['field']=='ability'}
        if not abilities:return
        for e in self.entries:
            if e['type']!='skill' or e['id'] in stated or not e['name_zh']:continue
            text=self.pages[e['page_path']]['text']
            for m in skill_notation(e['name_zh'],abilities).finditer(text,e['title_end'],e['end']):
                self.fact(e['id'],'ability',m[1],{'path':e['page_path'],'span':list(m.span(1)),'raw':m[1],
                    'notation':m[0],'notation_span':list(m.span())},'skill_check_notation')

    def finish(self):
        self.infer_source_rulesets()
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
        self.link_skill_abilities()
        # Deduplicate evidence without dropping alternative statements.
        self.facts=list({f['id']:f for f in self.facts}.values())
        facts=defaultdict(lambda:defaultdict(list))
        for f in self.facts:facts[f['entry_id']][f['field']].append(f)
        for e in self.entries:
            for field,fs in facts[e['id']].items():
                resolved,conflict=resolve_entry_field(field,fs)
                e['fields'][field]=resolved
                if conflict:
                    e['field_conflicts'].append(field)
                    values=[f['value'] for f in fs]
                    deduped=list({canonical(v):v for v in values}.values())
                    self.review(e['id'],'field_conflict',{'field':field,'values':deduped},{'fact_ids':[f['id'] for f in fs]},'high')
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
            ns=[n for n in self.registry.page_toc.get(e['page_path'],[]) if not n['toc_path'].startswith(QUICK_REFERENCE)]
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

    def export(self,out,validation,total_inputs,elapsed,counterparts=None,annotations_path=DEFAULT_ANNOTATIONS):
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
        CREATE TABLE book_search(url TEXT PRIMARY KEY,src TEXT NOT NULL,title TEXT NOT NULL,cat TEXT NOT NULL,text TEXT NOT NULL);
        CREATE INDEX entry_type_idx ON entries(type);
        CREATE INDEX entry_ruleset_idx ON entries(ruleset);
        CREATE INDEX entry_name_idx ON entries(name_en);
        CREATE INDEX fact_entry_idx ON facts(entry_id,field);
        CREATE INDEX relation_subject_idx ON relations(subject_id,predicate);
        CREATE INDEX entry_source_idx ON entry_sources(source_id,entry_id);
        ''')
        sources=sorted(self.sources.values(),key=lambda s:s['id'])
        for s in sources:db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(s['id'],s['name'],s['origin'],s['ruleset_hint'],s['release_status'],s['source_type'],s['archived'],s['coverage'],canonical(s)))
        for p in self.pages.values():db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?)',(p['path'],p['title'],p['role'],p['parse_status'],p['sha256'],p['text'],canonical(p['source_ids']),canonical({k:v for k,v in p.items() if k!='text'})))
        for e in self.entries:
            db.execute('INSERT OR IGNORE INTO entities VALUES(?,?)',(e['entity_id'],e['identity_status']))
            db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(e['id'],e['entity_id'],e['name'],e['name_zh'],e['name_en'],e['type'],e['page_path'],e['start'],e['end'],e['ruleset'],e['legacy'],e['classification_status'],canonical(e['fields']),canonical(e)))
            for s in e['source_ids']:db.execute('INSERT INTO entry_sources VALUES(?,?)',(e['id'],s))
        db.executemany('INSERT INTO facts VALUES(?,?,?,?,?,?,?,?)',[(f['id'],f['entry_id'],f['field'],canonical(f['value']),canonical(f['evidence']),f['rule'],f['scope'],f['status']) for f in self.facts])
        db.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?,?,?)',[(r['id'],r['subject_id'],r['predicate'],r['object_id'] or None,r['target_label'],r['target_locator'],canonical(r['evidence']),r['status']) for r in self.relations])
        db.executemany('INSERT INTO raw_tables VALUES(?,?,?,?,?)',[(t['id'],t['page_path'],t['start'],t['end'],canonical(t)) for t in self.tables])
        db.executemany('INSERT INTO index_observations VALUES(?,?,?,?,?,?)',[(o['id'],o['path'],o['target_path'],o['target_anchor'],o['kind'],canonical(o)) for o in self.observations])
        db.executemany('INSERT INTO review_queue VALUES(?,?,?,?,?)',[(r['id'],r['target'],r['reason'],r['priority'],canonical(r)) for r in self.reviews])
        db.executemany('INSERT INTO identity_candidates VALUES(?,?,?,?)',[(r['group_id'],r['type'],r['status'],canonical(r)) for r in self.identity_candidates])
        from anna.catalog.concepts import build_concepts
        concept_coverage=build_concepts(db,self.web,out,self.entries,self.pages,sources,dom_by_path=self.dom_by_path,field_facts=self.facts)
        from anna.review.context import Context
        shared_context=Context(db)
        from anna.catalog.references import build_references
        reference_coverage=build_references(db,self.web,out,annotations_path,dom_by_path=self.dom_by_path,context=shared_context)
        counterpart_coverage=None
        if counterparts:
            from anna.compare.catalog import build_counterparts
            counterpart_coverage=build_counterparts(db,counterparts,out)
        reviews=[json.loads(row[0]) for row in db.execute('SELECT data FROM review_queue ORDER BY reason,target,id')]
        summary={'snapshot_commit':self.commit,'input_html_files':total_inputs,'processed_html_files':len(self.pages),'full_scan':len(self.pages)==total_inputs,
            'page_roles':dict(Counter(p['role'] for p in self.pages.values())),'parse_status':dict(Counter(p['parse_status'] for p in self.pages.values())),
            'sources':len(sources),'source_coverage':dict(Counter(s['coverage'] for s in sources)), 'versions':len(self.entries),
            'typed_versions':sum(e['type']!='section' for e in self.entries),'types':dict(Counter(e['type'] for e in self.entries)),
            'classification_status':dict(Counter(e['classification_status'] for e in self.entries)),'ruleset':dict(Counter(e['ruleset'] for e in self.entries if e['type']!='section')),
            'legacy':dict(Counter(e['legacy'] for e in self.entries if e['type']!='section')),'index_rows':len(self.observations),
            'index_rows_linked':len({o for e in self.entries for o in e['observation_ids']}),'facts':len(self.facts),'relations':len(self.relations),'raw_tables':len(self.tables),
            'concept_definitions':concept_coverage['definitions'],'concept_observations':concept_coverage['observations'],'identity_candidate_groups':len(self.identity_candidates),'review_reasons':dict(Counter(r['reason'] for r in reviews)),
            'reference_coverage':reference_coverage,
            'counterparts':{k:counterpart_coverage[k] for k in ('catalog','commit','input_sha256','records','entries','outcomes','findings')} if counterpart_coverage else {'catalog':'5etools-cn','status':'skipped'},
            'validation':validation,'runtime_seconds':round(elapsed,2),'identity_policy':'provisional locator identities; no name-based merge',
            'parser':'beautifulsoup4/html5lib','pipeline_sha256':pipeline_hash(),'extraction_sha256':extraction_hash()}
        manifest=sha256('\n'.join(p+':'+self.pages[p]['sha256'] for p in sorted(self.pages)).encode()).hexdigest()
        summary['input_manifest_sha256']=manifest
        toc_titles=load_toc_titles(self.web)
        for path in sorted(self.search_rows):
            s=self.search_rows[path]
            src=path.split('/',1)[1] if '/' in path else path
            title=toc_titles.get(src) or s['html_title'] or Path(path).stem
            cat=src.split('/',1)[0] if '/' in src else src
            db.execute('INSERT INTO book_search VALUES(?,?,?,?,?)',(path,src,title,cat,s['text']))
        if len(self.pages)==total_inputs and (self.web/'topics').is_dir():
            db.execute('INSERT INTO metadata VALUES(?,?)',('book_search_signature',index_signature(self.web)))
        db.execute('INSERT INTO metadata VALUES(?,?)',('summary',canonical(summary)))
        db.commit()
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        db.close()
        (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        (out/'.generated').write_text('Generated by anna.catalog.build\n')
        return summary


def unquote_path(href):
    from urllib.parse import unquote,urlsplit
    return unquote(urlsplit(href).path)


def publish(stage,dest):
    """Replace generated files and remove stale build outputs without touching unrelated user files."""
    dest.mkdir(parents=True,exist_ok=True)
    if (dest/'.generated').exists():
        views_dir=dest/'views'
        if views_dir.is_dir():shutil.rmtree(views_dir)
        keep={p.name for p in stage.iterdir()}|{'audit.json','audit.md'}
        for item in list(dest.iterdir()):
            if item.name not in keep and item.is_file():item.unlink()
    for p in sorted(stage.rglob('*')):
        if p.is_file():
            target=dest/p.relative_to(stage);target.parent.mkdir(parents=True,exist_ok=True);os.replace(p,target)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--web',type=Path,default=DEFAULT_WEB,help='5echm-web mirror to extract');ap.add_argument('--output',type=Path,default=PROJECT/'data/extracted')
    ap.add_argument('--annotations',type=Path,default=DEFAULT_ANNOTATIONS)
    ap.add_argument('--counterparts',type=Path,default=DEFAULT_COUNTERPARTS,help='5etools-cn mirror compared with the extracted entries')
    ap.add_argument('--no-counterparts',action='store_true',help='skip the 5etools-cn comparison; the summary records it as skipped')
    ap.add_argument('--workers',type=int,default=min(4,os.cpu_count() or 1));ap.add_argument('--limit',type=int,help='development only; summary explicitly marks partial scans')
    args=ap.parse_args()
    counterparts=None if args.no_counterparts else args.counterparts
    if counterparts and not (counterparts/'data').is_dir():
        raise SystemExit(f'5etools-cn mirror missing: {counterparts}; run make clone to download it, or pass --no-counterparts to skip the comparison')
    started=time.monotonic();extraction_at_start=extraction_hash();b=Build(args.web)
    paths=sorted(p.relative_to(args.web).as_posix() for p in (args.web/'topics').rglob('*') if p.suffix.lower() in {'.htm','.html'})
    total=len(paths)
    b.registry.register_orphans([p for p in paths if page_role(p)=='document'])
    b.index_pass()
    if args.limit:paths=paths[:args.limit]
    jobs=[b.parse_job(p) for p in paths]
    if args.workers==1:
        results=map(parse_one,jobs)
        for i,result in enumerate(results,1):
            b.consume(result)
            if i%500==0:print(f'Pages: {i}/{len(paths)}; candidates: {len(b.entries)}',flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for i,result in enumerate(pool.map(parse_one,jobs,chunksize=8),1):
                b.consume(result)
                if i%500==0:print(f'Pages: {i}/{len(paths)}; candidates: {len(b.entries)}',flush=True)
    b.finish();validation=b.validate(paths)
    with tempfile.TemporaryDirectory(prefix='.build-',dir=PROJECT) as tmp:
        summary=b.export(Path(tmp),validation,total,time.monotonic()-started,counterparts,annotations_path=args.annotations)
        b.validate(paths)
        if extraction_hash()!=extraction_at_start:
            raise ValueError('Extraction implementation changed during the build; nothing was published. Rebuild.')
        with sqlite3.connect(Path(tmp)/'catalog.sqlite') as staged_db:
            require_current_annotations(staged_db,args.annotations)
        publish(Path(tmp),args.output)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
