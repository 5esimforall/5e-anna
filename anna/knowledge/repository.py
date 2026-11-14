#!/usr/bin/env python3
"""Build, inspect and review source-bounded rule knowledge and casting chains."""
from __future__ import annotations
from collections import Counter, defaultdict
from copy import deepcopy
import argparse
import json
from pathlib import Path
import re
import sqlite3

from anna.knowledge.casting import parse_access, GRANT
from anna.knowledge.levels import parse_table_access, feature_access
from anna.knowledge.values import evidence
from anna.knowledge.model import SCHEMA, KINDS, rule_value, validate_structure
from anna.catalog.concepts import Resolver, aliases
from anna.source.html import key
from anna.serialization import canonical, digest
from anna.review.store import DEFAULT_DECISIONS, lifecycle, load_events, append_event, locked, require_appendable, KNOWLEDGE_OPERATIONS
from anna.paths import PROJECT
from anna.knowledge.conditions import ABILITIES, CLASSES, validate_conditions, condition_terms, conjunction
from anna.knowledge.spells import parse_spell
from anna.knowledge.effects import QUERY_VALUES as EFFECT_QUERY_VALUES, QUERY_PARAMETERS as EFFECT_QUERY_PARAMETERS, build_effect_index, audit_effect_index

ACCESS_TYPES = {'feat', 'class_feature', 'species', 'magic_item', 'reward'}
REVIEW_OPERATIONS = KNOWLEDGE_OPERATIONS
QUERY_VALUES = {
    'query_subject': ('provider','spell','entry'),
    'mode': ('known', 'prepared', 'cast', 'list'),
    'resource': ('slot_free', 'spell_slot', 'not_stated'),
    'recovery': ('long_rest', 'short_rest', 'short_or_long_rest', 'not_stated'),
    'selection': ('fixed', 'choice'),
    'access_condition': ('level','class_level','ability_score','class','campaign','requirement','item_state','option_selected'),
    'casting_target': ('self','not_stated'),
    **EFFECT_QUERY_VALUES,
}
ACCESS_QUERY_FIELDS={'mode','resource','recovery','selection','access_condition','casting_target'}


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='effective_knowledge'").fetchone())


def is_knowledge(event):
    return event['operation'] in REVIEW_OPERATIONS


class Context:
    def __init__(self, db):
        self.entries = {e['id']: e for raw, in db.execute('SELECT data FROM entries') for e in [json.loads(raw)]}
        self.pages = dict(db.execute('SELECT path,text FROM pages'))
        present={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.tables=[json.loads(r[0]) for r in db.execute('SELECT data FROM raw_tables')] if 'raw_tables' in present else []
        self.class_links=[{'subject_id':r[0],'object_id':r[1],'evidence':json.loads(r[2])} for r in db.execute("SELECT subject_id,object_id,evidence FROM relations WHERE predicate='belongs_to_class' ORDER BY subject_id,object_id,evidence")] if 'relations' in present else []
        self.sources = [json.loads(raw) for raw, in db.execute('SELECT data FROM sources')]
        self.source_by_id = {s['id']: s for s in self.sources}
        self.resolver = Resolver(list(self.entries.values()), self.sources)
        self.spell_names = defaultdict(set)
        for e in self.entries.values():
            if e['type']=='spell':
                for n in aliases(e):
                    if len(n)>1: self.spell_names[n].add(e['id'])
        self.name_pattern = re.compile('|'.join(re.escape(n) for n in sorted(self.spell_names,key=lambda n:(-len(n),n))),re.IGNORECASE) if self.spell_names else None

    def class_owner(self, entry):
        ids=set();seen=set();current=entry
        while current and current['id'] not in seen:
            seen.add(current['id'])
            if current['type']=='class':ids.add(current['id'])
            ids.update(r['object_id'] for r in self.class_links if r['subject_id']==current['id'])
            current=self.entries.get(current.get('parent_id'))
        if len(ids)!=1:return None
        owner=self.entries.get(next(iter(ids)))
        return owner if owner and owner['type']=='class' and owner['ruleset']==entry['ruleset'] and owner['name_zh'] in CLASSES else None

    def resolve(self, entry, name, kind):
        return self.resolver.resolve(name, kind, entry['page_path'], entry['title_end'],
            scope={'source_ids':entry['source_ids'], 'ruleset':entry['ruleset']})

    def mentions(self, entry):
        if not self.name_pattern:return []
        text=self.pages[entry['page_path']];out=[];prior=None
        for m in self.name_pattern.finditer(text,entry['title_end'],entry['end']):
            if m[0].isascii() and ((m.start() and text[m.start()-1].isascii() and text[m.start()-1].isalpha()) or (m.end()<len(text) and text[m.end()].isascii() and text[m.end()].isalpha())):continue
            result=self.resolve(entry,m[0],'spell')
            # Adjacent bilingual names are one reference, not two grants.
            if prior and not text[prior['evidence']['span'][1]:m.start()].strip() and set(result['targets'])==set(prior['resolution']['targets']):
                prior['evidence']=evidence(entry['page_path'],text,prior['evidence']['span'][0],m.end());continue
            prior={'kind':'entry','name':m[0],'resolution':result,'evidence':evidence(entry['page_path'],text,m.start(),m.end())}
            out.append(prior)
        return out

    def choice_options(self, entry, target, restrictions=None):
        constraints=target['constraints'];levels=constraints.get('levels')
        if not levels or entry['ruleset'] not in {'5e','5r'}:return []
        classes=constraints.get('class_list',{}).get('values',[])
        if restrictions and restrictions.get('class_list'):
            classes=[c for c in classes if c in restrictions['class_list']]
            if not classes:return []
        schools=constraints.get('schools',[])
        import anna.catalog.views as browse
        options=[]
        for e in self.entries.values():
            if e['type']!='spell' or e['ruleset']!=entry['ruleset']:continue
            # Membership is evidenced by spell-list fields, in the relevant source or
            # the recorded core dependency, never by global name uniqueness.
            same=set(e['source_ids']) & set(entry['source_ids'])
            core=any(self.source_by_id.get(s,{}).get('reference_role')=='core_rules' and
                     self.source_by_id[s]['ruleset_hint']==entry['ruleset'] for s in e['source_ids'])
            trusted=all(self.source_by_id.get(s,{}).get('ruleset_hint')==entry['ruleset'] for s in entry['source_ids'])
            if not same and not (core and trusted):continue
            if not set(map(str,levels)) & set(browse.normalized(e['fields'],'spell_level')):continue
            if schools and not set(schools) & set(browse.normalized(e['fields'],'school')):continue
            if classes and not set(classes) & set(browse.normalized(e['fields'],'spell_classes')):continue
            options.append(e['id'])
        return sorted(options)

    def basis(self, record):
        entry=self.entries[record['subject_id']]
        ids=set(record.get('dependencies',[]))
        ids.add(entry['id'])
        if any(t.get('owner_id') for t in nested_values(record['value'])):
            parent=self.entries.get(entry.get('parent_id'));seen=set()
            while parent and parent['id'] not in seen:
                seen.add(parent['id']);ids.add(parent['id']);parent=self.entries.get(parent.get('parent_id'))
        def definition(e):
            return {k:e.get(k) for k in ('id','name','name_en','name_zh','type','ruleset','source_ids','parent_id','fields','page_path','start','title_end','end')} | {
                'body_sha256':digest(self.pages[e['page_path']][e['start']:e['end']])}
        source_ids={s for i in ids if i in self.entries for s in self.entries[i]['source_ids']}
        return {'schema':SCHEMA,'record':record,
            'definitions':{i:definition(self.entries[i]) if i in self.entries else None for i in sorted(ids)},
            'class_ownership':[r for r in self.class_links if r['subject_id'] in ids],
            'sources':{s:{k:self.source_by_id.get(s,{}).get(k) for k in ('name','ruleset_hint','reference_role','origin','archived')} for s in sorted(source_ids)}}


def walk_targets(value):
    if isinstance(value,dict):
        if 'resolution' in value:yield value
        for k,v in value.items():
            if k!='resolution_evidence':yield from walk_targets(v)
    elif isinstance(value,list):
        for v in value:yield from walk_targets(v)


def nested_values(value):
    if isinstance(value,dict):
        yield value
        for item in value.values():yield from nested_values(item)
    elif isinstance(value,list):
        for item in value:yield from nested_values(item)


def validate_record(record, context):
    if record['kind'] not in KINDS or record['subject_id'] not in context.entries:raise ValueError('Invalid knowledge subject or kind')
    validate_structure(record)
    def walk(value):
        if isinstance(value,dict):
            if set(('path','span','raw'))<=value.keys():
                text=context.pages.get(value['path']);span=value['span']
                if text is None or not isinstance(span,list) or len(span)!=2 or not all(type(n) is int for n in span) or not 0<=span[0]<span[1]<=len(text) or text[span[0]:span[1]]!=value['raw']:
                    raise ValueError('Knowledge evidence is not an exact source range')
            for v in value.values():walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
    walk(record)
    validate_conditions(record)
    for term in nested_values(record['value']):
        if term.get('kind')!='class_level' or not term.get('owner_id'):continue
        owner=context.class_owner(context.entries[record['subject_id']])
        if not owner or owner['id']!=term['owner_id'] or owner['name_zh']!=term.get('class'):
            raise ValueError('Class-level requirement disagrees with its source owner')
        if type(term.get('minimum')) is not int or term['minimum']<1:
            raise ValueError('Invalid class-level requirement')
    for grant in record['value'].get('grants',[]):
        target=grant['target']
        if target['kind']=='entry' and target['resolution']['status']=='resolved':
            if any(context.entries.get(tid,{}).get('type')!=grant['entry_type'] for tid in target['resolution']['targets']):
                raise ValueError('Grant target has the wrong definition type')
    for target in walk_targets(record['value']):
        r=target['resolution']
        if r['status']=='resolved' and len(r['targets'])!=1:raise ValueError('Resolved target must be unique')
        if any(i not in context.entries for i in r['targets']):raise ValueError('Missing knowledge target')



def rule_links(entry, value, context):
    """Every rule dependency names the local slot it governs; exceptions stay scoped."""
    links=[]
    def add(label, relation, scope, ev, *, change=None, when=None, ends=None, target_kind='concept'):
        # A target restriction governs spell targets, not range or a magic item's
        # unrelated "special purpose". Keep both evidenced English lookup names;
        # singular/plural chapter and glossary definitions still compete.
        lookup_names=['Target','Targets'] if label=='目标' else [label]
        lookups=[{'name':name,'resolution':context.resolve(entry,name,target_kind)} for name in lookup_names]
        if len(lookups)==1:resolution=lookups[0]['resolution']
        else:
            applicable=[x['resolution'] for x in lookups if x['resolution']['status'] in {'resolved','ambiguous'}]
            candidates=sorted({tid for r in applicable or [x['resolution'] for x in lookups] for tid in r['targets']})
            resolution={'targets':candidates,'status':('resolved' if len(candidates)==1 else 'ambiguous') if applicable else 'review' if candidates else 'unresolved',
                        'reason':'rule_lookup_names','resolution_evidence':{'lookups':lookups}}
        links.append({'id':'rule_'+str(len(links)+1),'relation':relation,'scope':scope,
            'change':change,'when':when,'ends':ends,'target':{'kind':'entry','name':label,
            'lookup_names':lookup_names,'resolution':resolution,'evidence':ev}})
    for field,label in [('action','施法时间'),('range','施法距离'),('components','法术成分'),('duration','持续时间')]:
        if field in value.get('parameters',{}):add('动作' if field=='action' and entry['type']!='spell' else label,'uses_rule',{'kind':'parameter','field':field},value['parameters'][field]['evidence'])
    if value.get('parameters',{}).get('duration',{}).get('concentration'):
        add('专注','uses_rule',{'kind':'parameter','field':'duration'},value['parameters']['duration']['evidence'])
    for grant in value.get('grants',[]):
        for branch in grant['branches']:
            scope={'kind':'usage_branch','grant_id':grant['id'],'branch_id':branch['id']}
            when=conjunction({'kind':'branch_selected','grant_id':grant['id'],'branch_id':branch['id']},
                             (value.get('activation') or {}).get('expression'),grant.get('when'),branch.get('when'))
            if branch['resource']['kind']=='spell_slot':
                free=branch['resource'].get('consumed') is False
                add('法术位','exception_to' if free else 'uses_rule',scope,branch['evidence'][0],
                    change={'field':'spell_slot.consumed','value':False} if free else None,
                    when=when)
            if branch.get('target'):
                add('目标','exception_to',scope,branch['evidence'][0],when=when,
                    change={'field':'casting.target','value':branch['target']['kind']})
            if branch.get('components'):
                add('法术成分','exception_to',scope,branch['evidence'][0],when=when,
                    change={'field':'components.waived','value':branch['components']['waived']})
            if branch.get('action'):add('动作','uses_rule',scope,branch['evidence'][0],when=when)
            for term in condition_terms(when):
                if term.get('kind')=='item_state' and term['state']=='attuned':
                    add('同调','uses_rule',scope,(value.get('activation') or {}).get('evidence',branch['evidence'][0]),when=when)
            if branch.get('recovery',{}).get('event')=='long_rest':add('长休','uses_rule',scope,branch['evidence'][-1])
    for effect in value.get('effects',[]):
        scope={'kind':'effect','effect_id':effect['id']}
        if effect['kind']=='event' and effect.get('event')=='attack_received':add('攻击检定','uses_rule',scope,effect['evidence'])
        if effect['kind']=='healing':add('治疗','uses_rule',scope,effect['evidence'],when=effect.get('when'))
        if effect['kind']=='attack_roll':add('攻击检定','uses_rule',scope,effect['evidence'])
        if effect['kind']=='saving_throw':add('豁免检定','uses_rule',scope,effect['evidence'],when=effect.get('when'))
        if effect['kind'] in {'check_sequence','sequence_policy'}:
            add('豁免检定','uses_rule',scope,effect['evidence'])
        if effect['kind']=='damage':
            add('伤害','uses_rule',scope,effect['evidence'],when=effect.get('when'))
            add('伤害掷骰','uses_rule',scope,effect['evidence'],when=effect.get('when'))
        use=effect.get('use',{})
        if use.get('action'):
            add({'bonus_action':'附赠动作','reaction':'反应'}.get(use['action'],'动作'),'uses_rule',scope,effect['evidence'],when=effect.get('when'))
        recovery=use.get('recovery',{}).get('event')
        for event,label in [('short_rest','短休'),('long_rest','长休')]:
            if recovery in {event,'short_or_long_rest'}:add(label,'uses_rule',scope,use.get('evidence',[effect['evidence']])[-1],when=effect.get('when'))
        if effect['kind'] in {'modifier','restriction'}:
            label={'speed':'速度','attack_roll':'攻击检定','healing.allowed':'治疗','prone.self_end':'倒地','damage':'伤害'}[effect['field']]
            change={k:effect[k] for k in ('field','operation','quantity','value','applies','uses','factor','rounding','applies_to') if k in effect}
            add(label,'exception_to',scope,effect['evidence'],when=effect.get('when'),ends=effect.get('ends'),change=change)
            if effect.get('operation')=='advantage':add('优势','uses_rule',scope,effect['evidence'],when=effect.get('when'),ends=effect.get('ends'))
        if effect['kind']=='check_modifier':
            add('豁免检定','exception_to',scope,effect['evidence'],when=effect.get('when'),
                change={'field':'saving_throw.modifier','value':effect['modifier'],'check':effect['check']})
            add('优势' if effect['modifier']=='advantage' else '劣势','uses_rule',scope,effect['evidence'],when=effect.get('when'))
        if effect['kind']=='save_override':
            add('豁免检定','exception_to',scope,effect['evidence'],when=effect['when'],
                change={'field':'saving_throw.result','value':effect['result'],'applies_to':effect['applies_to']})
        if effect['kind']=='duration_override':
            add('持续时间','exception_to',scope,effect['evidence'],when=effect['when'],ends=effect['ends'],
                change={'field':'effect.duration','operation':'replace_end','value':'until_removed','applies_to':effect['applies_to']})
            if effect['ends'].get('spell_name'):
                add(effect['ends']['spell_name'],'removed_by',scope,effect['evidence'],target_kind='spell',when=effect['when'])
        if effect['kind']=='target_constraint':
            add('目标','exception_to',scope,effect['evidence'],change={'field':'casting.targets','value':effect['constraint']})
        if effect['kind']=='end_spell':
            if any(c.get('kind')=='check_total' for c in condition_terms(effect['when'])):
                add('持续时间','exception_to',scope,effect['evidence'],when=effect['when'],change={'field':'effect.duration','value':'end'})
            for condition in condition_terms(effect['when']):
                if condition.get('kind')=='cover':add('全身掩护','uses_rule',scope,effect['evidence'],when=condition)
                if condition.get('kind')=='outside_spell_range':add('施法距离','uses_rule',scope,effect['evidence'],when=condition)
        if effect['kind']=='condition':add(effect['condition'],'uses_rule',scope,effect['evidence'],when=effect.get('when'))
        for modifier in effect.get('modifiers',[]):
            if modifier['effect']=='advantage':
                add('优势','uses_rule',scope,effect['evidence'],when=modifier['when'])
    return links


def record_for(entry, kind, value, context):
    pre=value.get('prerequisites',{})
    for term in condition_terms(pre.get('expression')):
        if term.get('kind')=='requirement':
            term['target']={'kind':'entry','name':term['name'],'resolution':context.resolve(entry,term['name'],term['entry_type']),
                            'evidence':pre['evidence']}
    value['rules']=rule_links(entry,value,context)
    dependencies={t['owner_id'] for t in nested_values(value) if t.get('owner_id')}
    for t in walk_targets(value): dependencies.update(t['resolution']['targets'])
    for g in [*value.get('grants',[]),*value.get('modifiers',[])]:
        if g.get('target',{}).get('kind')=='choice':
            g['target']['options']=context.choice_options(entry,g['target'])
            dependencies.update(g['target']['options'])
    # Bind every competing name, including currently inapplicable editions: a change
    # to generation/type/source must reopen an approved target selection.
    for t in walk_targets(value):
        for name in [t.get('name',''),*t.get('lookup_names',[])]:dependencies.update(context.resolver.names.get(key(name),()))
    gaps=value.get('unparsed',[])
    unresolved=any(t['resolution']['status']!='resolved' for k,v in value.items() if k!='rules' for t in walk_targets(v))
    choices=[g['target'] for g in [*value.get('grants',[]),*value.get('modifiers',[])] if g.get('target',{}).get('kind')=='choice']
    unfinished=(value.get('prerequisites',{}).get('status')=='unparsed'
        or kind=='grant' and not value.get('grants') or kind=='procedure' and not value.get('effects')
        or any(g['mode']=='selection' for g in value.get('grants',[])) or any(not t.get('options') for t in choices))
    record={'id':'knowledge_'+digest([entry['id'],kind])[:24], 'schema':SCHEMA,
        'subject_id':entry['id'],'kind':kind,'ruleset':entry['ruleset'],
        'evidence':evidence(entry['page_path'],context.pages[entry['page_path']],entry['start'],entry['end']),
        'value':value,'status':'partial' if gaps or unresolved or unfinished else 'resolved',
        'dependencies':sorted(dependencies)}
    validate_record(record,context)
    return record


def feat_access(entry, context):
    if entry['type']!='background':return None
    text=context.pages[entry['page_path']]
    match=re.search(r'(?m)^[ \t]*专长[：:]([^\n]+)',text[entry['title_end']:entry['end']])
    if not match:return None
    label=match[1].strip();name=re.split(r'[（(]',label)[0].strip()
    a=entry['title_end']+match.start();b=entry['title_end']+match.end()
    qualifiers=re.findall(r'[（(]([^）)]+)[）)]',label)
    classes=[c for c in CLASSES if any(q==c for q in qualifiers)]
    unknown=[q for q in qualifiers if q not in classes and not re.fullmatch(r'见第[^）]+章',q)]
    ev=evidence(entry['page_path'],text,a,b)
    target={'kind':'entry','name':name,'resolution':context.resolve(entry,name,'feat'),'evidence':ev}
    return rule_value(grants=[{'id':'grant_1','entry_type':'feat','mode':'gain','target':target,
        'constraints':{'class_list':classes} if classes else {},'branches':[],'evidence':[ev]}],
        unparsed=[{'reason':'unparsed_grant_qualifier','evidence':ev}] if unknown else [])



def definition_roles(context):
    """Content roles explain definitions; they never establish equivalence or priority."""
    groups=defaultdict(list)
    core=[]
    for e in context.entries.values():
        if e['type'] not in {'rule','section','action','condition','ability','skill'} or not e.get('name_en'):continue
        if not any(context.source_by_id.get(s,{}).get('reference_role')=='core_rules' for s in e['source_ids']):continue
        core.append(e)
        for sid in e['source_ids']:groups[(sid,key(e['name_en']))].append(e)
    for e in core:
        if '/术语汇编/' in e['page_path']:role='glossary'
        elif e['type']=='section' and not e.get('is_primary') and any(len({x['page_path'] for x in groups[(s,key(e['name_en']))] if x['type']=='section' and not x.get('is_primary')})>1 for s in e['source_ids']):role='template'
        elif e['type'] in {'rule','action','condition','ability','skill'}:role='explanation'
        else:continue
        value={'role':role,'scope_entry_id':e.get('parent_id'),'unparsed':[]}
        yield record_for(e,'definition_role',value,context)


def discover(context):
    from anna.knowledge.features import parse_feature
    records=list(definition_roles(context))
    for entry in context.entries.values():
        if entry['end']<=entry['start']:continue
        text=context.pages[entry['page_path']]
        if entry['type']=='spell':
            records.append(record_for(entry,'procedure',parse_spell(entry,text),context))
        if entry['type'] in ACCESS_TYPES:
            value=parse_table_access(entry,context)
            if value is None:value=parse_access(entry,text,context.mentions(entry)) if GRANT.search(re.sub(r'\s+','',text[entry['title_end']:entry['end']])) else None
            if value:records.append(record_for(entry,'grant',value,context))
            procedure=parse_feature(entry,text,context.class_owner(entry))
            if procedure:records.append(record_for(entry,'procedure',procedure,context))
        if entry['type']=='subclass':
            value=feature_access(entry,context)
            if value:records.append(record_for(entry,'grant',value,context))
        if entry['type']=='background':
            value=feat_access(entry,context)
            if value:records.append(record_for(entry,'grant',value,context))
    return records


def binding(record):
    return {'knowledge_id':record['id'],'subject_id':record['subject_id'],'kind':record['kind'],**record['evidence']}


def replay(records, events, context):
    states=lifecycle(events);results={};claims=defaultdict(list);by_id={r['id']:r for r in records}
    for event in events:
        if not is_knowledge(event):continue
        rid=event['binding']['knowledge_id'];record=by_id.get(rid)
        result={'event':event,'status':states[event['id']], 'knowledge_id':rid}
        if states[event['id']]=='active':
            current=context.basis(record) if record else None
            valid=record is not None and event['binding']==binding(record) and event['basis_sha256']==digest(current)
            result.update(status='applied' if valid else 'stale' if record else 'missing',
                          basis_sha256=digest(current) if current else None)
            if record:claims[rid].append((event,valid))
        results[event['id']]=result
    effective=[]
    for record in records:
        row=deepcopy(record);row.update(review_status='unreviewed',decision_ids=[])
        choices=claims[record['id']]
        if len(choices)>1:
            row.update(status='review',review_status='conflict')
            for e,_ in choices:results[e['id']]['status']='conflict'
        elif choices:
            e,valid=choices[0]
            if not valid:row.update(status='review',review_status='stale')
            elif e['operation']=='knowledge_reject':row.update(status='rejected',review_status='applied')
            else:row['review_status']='applied'
        elif any(e['binding']['knowledge_id']==record['id'] and states[e['id']]=='revoked' for e in events if is_knowledge(e)):
            row['review_status']='revoked'
        row['decision_ids']=[e['id'] for e,_ in choices]
        effective.append(row)
    return effective,list(results.values())


def branches(context, effective):
    by_subject={r['subject_id']:r for r in effective if r['kind']=='grant'}
    def conditions(record,grant=None,branch=None):
        value=record['value'];subject=record['subject_id'];out=[]
        for part,scope in [(value['prerequisites'],'acquisition'),(value.get('activation'),'provider')]:
            if part and part.get('expression'):out.append({'scope':{'kind':scope,'subject_id':subject},'expression':part['expression'],'evidence':part['evidence']})
        for part,kind in [(grant,'grant'),(branch,'usage_branch')]:
            if part and part.get('when'):out.append({'scope':{'kind':kind,'subject_id':subject,'grant_id':grant['id'],**({'branch_id':branch['id']} if kind=='usage_branch' else {})},'expression':part['when'],'evidence':part['evidence'][0]})
        return out
    def walk(root,subject,steps,inherited,restrictions,visited):
        access=by_subject.get(subject)
        if subject in visited or not access or access['status']!='resolved':return
        visited=visited|{subject}
        for grant in access['value']['grants']:
            target=grant['target']
            if grant['entry_type']!='spell':
                if target['kind']!='entry' or target['resolution']['status']!='resolved':continue
                limited=deepcopy(restrictions)
                for name,values in grant.get('constraints',{}).items():
                    limited[name]=[v for v in values if v in limited[name]] if name in limited else values
                if any(not values for values in limited.values()):continue
                tid=target['resolution']['targets'][0]
                for branch in grant['branches'] or [None]:
                    step={'subject_id':subject,'target_id':tid,'knowledge_id':access['id'],'grant_id':grant['id'],
                          'branch_id':branch['id'] if branch else None,'evidence':grant['evidence'][0]}
                    yield from walk(root,tid,steps+[step],inherited+conditions(access,grant,branch),limited,visited)
                continue
            if grant['mode']=='selection':continue
            spells=target['resolution']['targets'] if target['kind']=='entry' and target['resolution']['status']=='resolved' else []
            if target['kind']=='choice':spells=context.choice_options(context.entries[subject],target,restrictions)
            for branch in grant['branches']:
                resource=branch['resource'];resource_kind='slot_free' if resource.get('consumed') is False else 'spell_slot' if resource.get('consumed') is True else 'not_stated'
                constrained=inherited+conditions(access,grant,branch)
                for spell_id in spells:
                    data={'root_id':root,'subject_id':subject,'knowledge_id':access['id'],'grant_id':grant['id'],'branch_id':branch['id'],
                        'spell_id':spell_id,'mode':grant['mode'],'resource':resource_kind,'recovery':branch.get('recovery',{}).get('event','not_stated'),
                        'selection':'fixed' if target['kind']=='entry' else 'choice','acquisition':[s['knowledge_id'] for s in steps],
                        'acquisition_steps':steps,'constraints':restrictions,'grant':{**grant,'target':{k:v for k,v in target.items() if k!='options'}},'branch':branch,
                        'conditions':constrained,'condition_kinds':sorted({t['kind'] for c in constrained for t in condition_terms(c['expression']) if 'kind' in t}),
                        'casting_target':branch.get('target',{}).get('kind','not_stated')}
                    identity=[root,subject,grant['id'],branch['id'],spell_id]
                    if steps:identity.append([[s[k] for k in ('subject_id','grant_id','branch_id','target_id')] for s in steps])
                    data['id']='casting_'+digest(identity)[:24]
                    yield data
    for root in by_subject:yield from walk(root,root,[],[],{},set())


def build_knowledge(db, out, decision_path=DEFAULT_DECISIONS):
    context=Context(db);records=discover(context);events,_=load_events(decision_path)
    effective,decisions=replay(records,events,context)
    db.executescript('''
        CREATE TABLE rule_knowledge(id TEXT PRIMARY KEY,subject_id TEXT REFERENCES entries(id),kind TEXT,status TEXT,data TEXT NOT NULL);
        CREATE TABLE effective_knowledge(id TEXT PRIMARY KEY REFERENCES rule_knowledge(id),subject_id TEXT REFERENCES entries(id),kind TEXT,status TEXT,review_status TEXT,data TEXT NOT NULL);
        CREATE INDEX knowledge_subject ON effective_knowledge(subject_id,kind);
        CREATE TABLE knowledge_decisions(id TEXT PRIMARY KEY,knowledge_id TEXT,status TEXT,data TEXT NOT NULL);
        CREATE TABLE casting_options(id TEXT PRIMARY KEY,root_id TEXT REFERENCES entries(id),subject_id TEXT REFERENCES entries(id),
            spell_id TEXT REFERENCES entries(id),mode TEXT,resource TEXT,recovery TEXT,selection TEXT,data TEXT NOT NULL);
        CREATE INDEX casting_root ON casting_options(root_id);
        CREATE INDEX casting_query ON casting_options(mode,resource,recovery,root_id);
    ''')
    for r in records:db.execute('INSERT INTO rule_knowledge VALUES(?,?,?,?,?)',(r['id'],r['subject_id'],r['kind'],r['status'],canonical(r)))
    for r in effective:db.execute('INSERT INTO effective_knowledge VALUES(?,?,?,?,?,?)',(r['id'],r['subject_id'],r['kind'],r['status'],r['review_status'],canonical(r)))
    for r in decisions:
        db.execute('INSERT INTO knowledge_decisions VALUES(?,?,?,?)',(r['event']['id'],r['knowledge_id'],r['status'],canonical(r)))
        if r['status'] in {'stale','missing','conflict'}:
            review={'id':'knowledge_review_'+r['event']['id'],'target':r['event']['binding']['subject_id'],
                    'reason':'knowledge_decision_'+r['status'],'priority':'high','detail':r,'evidence':r['event']['binding']}
            db.execute('INSERT INTO review_queue VALUES(?,?,?,?,?)',(review['id'],review['target'],review['reason'],review['priority'],canonical(review)))
    from anna.knowledge.reading import build_rule_index
    build_rule_index(db,effective)
    build_effect_index(db,effective)
    count=0
    for option in branches(context,effective):
        db.execute('INSERT INTO casting_options VALUES(?,?,?,?,?,?,?,?,?)',tuple(option[k] for k in ('id','root_id','subject_id','spell_id','mode','resource','recovery','selection'))+(canonical(option),));count+=1
    coverage={'schema':SCHEMA,'observations':len(records),'casting_options':count,
        'by_kind':dict(Counter(r['kind'] for r in records)),
        'outcomes':dict(Counter(r['status'] for r in effective)),
        'reviews':dict(Counter(r['review_status'] for r in effective)),
        'unparsed':dict(Counter(g['reason'] for r in records for g in r['value'].get('unparsed',[])))}
    db.execute('INSERT INTO metadata VALUES(?,?)',('knowledge_coverage',canonical(coverage)))
    for name,rows in [('rule_knowledge',records),('effective_knowledge',effective),('knowledge_decisions',decisions)]:
        (Path(out)/(name+'.jsonl')).write_text(''.join(canonical(r)+'\n' for r in rows))
    (Path(out)/'knowledge_coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2)+'\n')
    return coverage


def audit_knowledge(db, folder):
    """Check exact evidence, projections and exports; summarize by actual source."""
    if not exists(db):return None
    context=Context(db);errors=[];matrix=Counter()
    records=[json.loads(r[0]) for r in db.execute('SELECT data FROM effective_knowledge')]
    for record in records:
        try:validate_record(record,context)
        except ValueError as exc:errors.append(record['id']+': '+str(exc))
        for sid in context.entries[record['subject_id']]['source_ids'] or ['unknown']:
            matrix[(sid,record['kind'],record['status'])]+=1
    for name in ('rule_knowledge','effective_knowledge','knowledge_decisions'):
        path=Path(folder)/(name+'.jsonl')
        expected={r[0]:json.loads(r[1]) for r in db.execute('SELECT id,data FROM '+name)}
        exported=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        actual={r['event']['id'] if name=='knowledge_decisions' else r['id']:r for r in exported}
        if actual!=expected or len(actual)!=len(exported):errors.append(name+': export mismatch')
    expected={r['id']:r for r in branches(context,records)}
    actual={r[0]:json.loads(r[1]) for r in db.execute('SELECT id,data FROM casting_options')}
    if expected!=actual:errors.append('casting_options: projection mismatch')
    from anna.knowledge.reading import rule_rows
    expected_rules={r['id']:r for r in rule_rows(records)}
    rule_columns=('id','source_id','target_id','relation','status')
    actual_rules={r[0]:dict(zip(rule_columns,r[:-1]))|{'data':json.loads(r[-1])}
                  for r in db.execute('SELECT id,source_id,target_id,relation,status,data FROM knowledge_rule_links')}
    expected_rules={key:{k:row[k] for k in rule_columns}|{'data':row} for key,row in expected_rules.items()}
    if expected_rules!=actual_rules:errors.append('knowledge_rule_links: projection mismatch')
    errors.extend(audit_effect_index(db,records))
    return {'records':len(records),'casting_options':len(actual),'errors':errors,
        'by_source':[{'source_id':sid,'kind':kind,'status':status,'count':count}
                     for (sid,kind,status),count in sorted(matrix.items())]}


def knowledge_diff(before, after):
    def rows(db):
        return {r[0]:json.loads(r[1]) for r in db.execute('SELECT id,data FROM effective_knowledge')} if exists(db) else {}
    old,new=rows(before),rows(after);changes=[]
    for rid in sorted(old.keys()|new.keys()):
        a,b=old.get(rid),new.get(rid)
        if a!=b:changes.append({'id':rid,'change':'added' if a is None else 'removed' if b is None else 'changed','before':a,'after':b})
    return changes


def casting_query(raw):
    try:query=json.loads(raw) if isinstance(raw,str) else raw
    except (ValueError,TypeError) as exc:raise ValueError('Invalid casting query') from exc
    if not isinstance(query,dict) or not query or any(k not in {*QUERY_VALUES,*EFFECT_QUERY_PARAMETERS,'spell'} for k in query):raise ValueError('Invalid casting query fields')
    for k,v in query.items():
        if k=='spell':
            if not isinstance(v,str) or not v.strip() or len(v)>200:raise ValueError('Invalid spell search')
        elif k=='cumulative_count':
            if type(v) is not int or not 1<=v<=1000000:raise ValueError('Invalid cumulative count')
        elif v not in QUERY_VALUES[k]:raise ValueError('Invalid casting query value')
    if query.get('query_subject') in {'spell','entry'} and ACCESS_QUERY_FIELDS & query.keys():
        raise ValueError('Direct queries cannot contain provider conditions')
    return query


def casting_condition(raw, alias='e'):
    query=casting_query(raw);clauses=[];values=[];effects=[];effect_values=[];thresholds=[];threshold_values=[]
    for k,v in query.items():
        if k=='query_subject':continue
        if k in {'cumulative_result','cumulative_count'}:
            thresholds.append("json_extract(th.value,'$."+('result' if k=='cumulative_result' else 'count')+"')=?")
            threshold_values.append(v);continue
        if k in EFFECT_QUERY_VALUES:
            effects.append('EXISTS(SELECT 1 FROM json_each(ef.data,\'$.'+k+'s\') WHERE value=?)' if k in {'trigger','end_trigger'} else 'ef.'+k+'=?')
            effect_values.append(v);continue
        if k=='spell':clauses.append('instr(lower('+alias+'.name),lower(?))>0' if query.get('query_subject') in {'spell','entry'} else 'EXISTS(SELECT 1 FROM entries s WHERE s.id=c.spell_id AND instr(lower(s.name),lower(?))>0)')
        elif k=='access_condition':clauses.append("EXISTS(SELECT 1 FROM json_each(c.data,'$.condition_kinds') WHERE value=?)")
        elif k=='casting_target':clauses.append("json_extract(c.data,'$.casting_target')=?")
        else:clauses.append('c.'+k+'=?')
        values.append(v)
    if thresholds:
        effects.append("EXISTS(SELECT 1 FROM json_each(ef.data,'$.thresholds') th WHERE "+' AND '.join(thresholds)+')')
        effect_values.extend(threshold_values)
    if effects:
        spell=alias+'.id' if query.get('query_subject') in {'spell','entry'} else 'c.spell_id'
        clauses.append('EXISTS(SELECT 1 FROM rule_effect_index ef WHERE ef.subject_id='+spell+' AND '+' AND '.join(effects)+')');values.extend(effect_values)
    if query.get('query_subject') in {'spell','entry'}:
        direct=[alias+".type='spell'"] if query['query_subject']=='spell' else []
        direct+=clauses
        # With no semantic predicate this is a discovery scope, not a claim that
        # every procedure is complete. Effect predicates still use only valid rows.
        if query['query_subject']=='entry':
            direct.append("EXISTS(SELECT 1 FROM effective_knowledge k WHERE k.subject_id="+alias+".id AND k.kind='procedure')")
        return '('+' AND '.join(direct)+')',values
    if not clauses:return alias+'.id IN(SELECT root_id FROM casting_options)',values
    return alias+'.id IN(SELECT c.root_id FROM casting_options c WHERE '+' AND '.join(clauses)+')',values


def chain(db, entry_id, spell_id=None, reading_path=None, effect_id=None):
    if not exists(db):return {'schema':SCHEMA,'records':[],'nodes':[],'options':[],'incoming':[]}
    if not db.execute('SELECT 1 FROM entries WHERE id=?',(entry_id,)).fetchone():raise LookupError('Entry not found')
    subject_ids={entry_id};options=[]
    # Group identical grants and branches; choice domains do not duplicate the whole UI.
    grouped={}
    for raw, in db.execute('SELECT data FROM casting_options WHERE root_id=? ORDER BY subject_id,id',(entry_id,)):
        option=json.loads(raw);key_=(option['subject_id'],option['grant_id'],option['branch_id'],canonical(option['acquisition_steps']))
        if key_ not in grouped:grouped[key_]={k:v for k,v in option.items() if k not in {'id','spell_id'}}|{'spell_ids':[]}
        grouped[key_]['spell_ids'].append(option['spell_id']);subject_ids.add(option['subject_id'])
    options=list(grouped.values())
    subject_ids.update(s['subject_id'] for o in options for s in o['acquisition_steps'])
    available={i for o in options for i in o['spell_ids']}
    fixed={i for o in options if o['selection']=='fixed' for i in o['spell_ids']}
    if spell_id is not None and spell_id not in available:raise ValueError('Spell is not an effective option of this chain')
    selected=spell_id or (next(iter(fixed)) if len(fixed)==1 else None)
    if selected:subject_ids.add(selected)
    records=[]
    for sid in sorted(subject_ids):
        records.extend(json.loads(raw) for raw, in db.execute('SELECT data FROM effective_knowledge WHERE subject_id=? ORDER BY kind',(sid,)))
    target_ids={i for o in options for i in o['spell_ids']}
    for r in records:
        for target in walk_targets(r['value']):target_ids.update(target['resolution']['targets'])
    # Definition links are shared effective references, not a second name resolver.
    references=[]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='effective_references'").fetchone():
        for sid in sorted(subject_ids):
            e=db.execute('SELECT page_path,start,end FROM entries WHERE id=?',(sid,)).fetchone()
            if e:
                for rid,tid,raw in db.execute("SELECT r.id,r.target_id,r.data FROM effective_references r JOIN entries t ON t.id=r.target_id WHERE r.page_path=? AND r.start>=? AND r.end<=? AND r.status='resolved' AND t.type IN ('rule','action','condition','ability','skill')",tuple(e)):
                    references.append({'reference_id':rid,'target_id':tid,'evidence':json.loads(raw).get('evidence',{})});target_ids.add(tid)
    nodes=[]
    for eid in sorted(subject_ids|target_ids):
        r=db.execute('SELECT id,name,type,ruleset,page_path,start,end FROM entries WHERE id=?',(eid,)).fetchone()
        if r:nodes.append(dict(zip(('id','name','type','ruleset','page_path','start','end'),r)))
    incoming=[{'entry_id':r[0],'name':r[1],'ruleset':r[2]} for r in db.execute('SELECT DISTINCT e.id,e.name,e.ruleset FROM casting_options c JOIN entries e ON e.id=c.root_id WHERE c.spell_id=? ORDER BY e.name,e.id',(entry_id,))]
    if reading_path is None and spell_id:
        paths={tuple([entry_id]+[s['target_id'] for s in o['acquisition_steps']]+[spell_id]) for o in options if spell_id in o['spell_ids']}
        if len(paths)==1:reading_path=list(next(iter(paths)))
    from anna.knowledge.reading import reading
    return reading(db,entry_id,{'schema':SCHEMA,'records':records,'nodes':nodes,'options':options,'references':references,'incoming':incoming,'selected_spell_id':selected},reading_path,effect_id)


def inspect(db, rid):
    row=db.execute('SELECT data FROM rule_knowledge WHERE id=?',(rid,)).fetchone()
    if not row:raise ValueError('Knowledge record not found')
    record=json.loads(row[0]);context=Context(db);basis=context.basis(record)
    effective=db.execute('SELECT data FROM effective_knowledge WHERE id=?',(rid,)).fetchone()
    return {'observation':record,'effective':json.loads(effective[0]),'basis':basis,'basis_sha256':digest(basis)}


def save(db,path,rid,operation,expected_basis,reviewer,reason,supersedes=None):
    if operation not in REVIEW_OPERATIONS:raise ValueError('Invalid knowledge operation')
    if not reviewer.strip() or not reason.strip():raise ValueError('Reviewer and reason are required')
    with locked(path):
        require_appendable(db,path);view=inspect(db,rid)
        if view['basis_sha256']!=expected_basis:raise ValueError('Knowledge basis changed; inspect again')
        events,log_hash=load_events(path);states=lifecycle(events)
        active=[e['id'] for e in events if is_knowledge(e) and e['binding']['knowledge_id']==rid and states[e['id']]=='active']
        if active!=([supersedes] if supersedes else []):raise ValueError('Supersede the active decision or revoke competing decisions')
        return append_event(path,{'operation':operation,'binding':binding(view['observation']),
            'basis':view['basis'],'basis_sha256':expected_basis,'reviewer':reviewer.strip(),'reason':reason.strip(),'supersedes':supersedes},log_hash)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=PROJECT/'data/extracted/catalog.sqlite')
    parser.add_argument('--decisions',type=Path,default=DEFAULT_DECISIONS)
    commands=parser.add_subparsers(dest='command',required=True)
    listing=commands.add_parser('list');listing.add_argument('--kind',choices=sorted(KINDS));listing.add_argument('--status');listing.add_argument('--limit',type=int,default=20)
    viewing=commands.add_parser('inspect');viewing.add_argument('id')
    for name in ('accept','reject'):
        cmd=commands.add_parser(name);cmd.add_argument('id');cmd.add_argument('--basis',required=True);cmd.add_argument('--reviewer',required=True);cmd.add_argument('--reason',required=True);cmd.add_argument('--supersedes')
    reading=commands.add_parser('chain');reading.add_argument('id')
    undo=commands.add_parser('revoke');undo.add_argument('id');undo.add_argument('--reviewer',required=True);undo.add_argument('--reason',required=True)
    args=parser.parse_args()
    try:
        if args.command=='revoke':
            from anna.review.commands import revoke
            result=revoke(args.decisions,args.id,args.reviewer,args.reason)
        else:
            with sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True) as db:
                if args.command=='inspect':result=inspect(db,args.id)
                elif args.command=='chain':result=chain(db,args.id)
                elif args.command=='list':
                    if not 1<=args.limit<=300:raise ValueError('Limit must be between 1 and 300')
                    filters=[];values=[]
                    for k in ('kind','status'):
                        if getattr(args,k):filters.append(k+'=?');values.append(getattr(args,k))
                    result=[json.loads(r[0]) for r in db.execute('SELECT data FROM effective_knowledge'+(' WHERE '+' AND '.join(filters) if filters else '')+' ORDER BY subject_id,kind LIMIT ?',values+[args.limit])]
                else:result=save(db,args.decisions,args.id,'knowledge_'+args.command,args.basis,args.reviewer,args.reason,args.supersedes)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,sqlite3.Error,OSError) as exc:parser.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
