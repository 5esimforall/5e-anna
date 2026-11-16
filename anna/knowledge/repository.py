#!/usr/bin/env python3
"""Build, inspect and review source-bounded rule knowledge and casting chains."""
from __future__ import annotations
from collections import Counter, defaultdict
from copy import deepcopy
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from anna.knowledge.values import evidence
from anna.knowledge.model import SCHEMA, KINDS, rule_value, validate_structure, grant_is_resolved
from anna.serialization import canonical, digest
from anna.source.html import key
from anna.review.store import (
    DEFAULT_DECISIONS,
    DEFAULT_ANNOTATIONS,
    lifecycle,
    load_events,
    append_event,
    locked,
    require_appendable,
    KNOWLEDGE_OPERATIONS,
    load_annotations,
    annotation_lifecycle,
    append_annotation_events,
    require_annotations_appendable,
)
from anna.paths import PROJECT
from anna.knowledge.context import Context, walk_targets, nested_values
from anna.knowledge.inspection import inspect
from anna.knowledge.conditions import ABILITIES, CLASSES, validate_conditions, condition_terms, conjunction
from anna.knowledge.effects import build_effect_index, audit_effect_index
from anna.knowledge.extract import extract_feat_grant, extract_subclass_features, extract_definition_roles
from anna.knowledge.annotations import evaluate_annotations, compose_records, inspect_clauses, build_annotation_event

REVIEW_OPERATIONS = KNOWLEDGE_OPERATIONS


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='effective_knowledge'").fetchone())


def is_knowledge(event):
    return event['operation'] in REVIEW_OPERATIONS



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
            label={'speed':'速度','attack_roll':'攻击检定','healing.allowed':'治疗','prone.self_end':'倒地','damage':'伤害','armor_class.base':'护甲等级'}[effect['field']]
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
            if any(c.get('kind') in {'check_total','armor_worn','dismiss'} for c in condition_terms(effect['when'])):
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


def discover(context, ann_events=(), evaluated=None):
    records = [record_for(e, 'definition_role', value, context) for e, value in extract_definition_roles(context)]
    for entry in context.entries.values():
        if entry['end'] <= entry['start']:
            continue
        if entry['type'] == 'subclass':
            value = extract_subclass_features(entry, context)
            if value:
                records.append(record_for(entry, 'grant', value, context))
        elif entry['type'] == 'background':
            value = extract_feat_grant(entry, context)
            if value:
                records.append(record_for(entry, 'grant', value, context))
    structural_keys = {(r['subject_id'], r['kind']) for r in records}
    if evaluated is None:
        evaluated = evaluate_annotations(context, ann_events)
    for entry, kind, value, _ in compose_records(context, evaluated, structural_keys=structural_keys):
        records.append(record_for(entry, kind, value, context))
    return records


def binding(record):
    return {'knowledge_id':record['id'],'subject_id':record['subject_id'],'kind':record['kind'],**record['evidence']}


def _annotation_statuses(evaluated):
    grouped = defaultdict(list)
    for row in evaluated or ():
        sid = row.get('subject_id')
        if sid and row['lifecycle_state'] not in {'revoked', 'superseded'}:
            grouped[(sid, row['event']['kind'])].append(row['status'])
    statuses = {}
    for key_pair, group in grouped.items():
        if 'conflict' in group:
            statuses[key_pair] = 'conflict'
        elif 'stale' in group or 'ambiguous' in group:
            statuses[key_pair] = 'stale'
        else:
            statuses[key_pair] = 'applied'
    return statuses


def replay(records, events, context, evaluated=None):
    states=lifecycle(events);results={};claims=defaultdict(list);by_id={r['id']:r for r in records}
    ann_statuses = _annotation_statuses(evaluated)
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
        row=deepcopy(record)
        default_review = ann_statuses.get((row['subject_id'], row['kind']))
        if not default_review:
            reasons = {g.get('reason') for g in row['value'].get('unparsed', [])}
            default_review = 'conflict' if 'conflicting_annotation' in reasons else 'stale' if 'stale_annotation' in reasons else 'unreviewed'
        row.update(review_status=default_review, decision_ids=[])
        choices=claims[record['id']]
        if len(choices)>1:
            row.update(status='review',review_status='conflict')
            for e,_ in choices:results[e['id']]['status']='conflict'
        elif choices:
            e,valid=choices[0]
            if not valid:row.update(status='review',review_status='stale')
            elif e['operation']=='knowledge_reject':row.update(status='rejected',review_status='applied')
            elif default_review not in {'stale', 'conflict'}:row['review_status']='applied'
        elif any(e['binding']['knowledge_id']==record['id'] and states[e['id']]=='revoked' for e in events if is_knowledge(e)):
            if default_review not in {'stale', 'conflict'}:
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
        if subject in visited or not access or access['status'] not in {'resolved', 'partial'}:return
        visited=visited|{subject}
        for grant in access['value']['grants']:
            if not grant_is_resolved(access, grant):continue
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


def _annotation_row(result):
    event = result['event']
    return {
        'id': event['id'],
        'annotation_id': event['annotation_id'],
        'kind': event['kind'],
        'status': result['status'],
        'lifecycle_state': result['lifecycle_state'],
        'subject_match': result['subject_match'],
        'subject_id': result['subject_id'],
        'subject': event['subject'],
        'detail': result['detail'],
        'event': event,
        'current_basis': result['current_basis'],
    }


def build_knowledge(db, out, decision_path=DEFAULT_DECISIONS, annotations_path=DEFAULT_ANNOTATIONS):
    context=Context(db)
    annotations_file = Path(annotations_path).resolve()
    raw_annotations = annotations_file.read_bytes() if annotations_file.exists() else b''
    ann_events, ann_hash = load_annotations(annotations_file)
    ann_snapshot = {
        'path': str(annotations_file),
        'sha256': ann_hash,
        'bytes': len(raw_annotations),
        'events': len(ann_events),
    }
    evaluated = evaluate_annotations(context, ann_events)
    records=discover(context, ann_events, evaluated=evaluated)
    events,_=load_events(decision_path)
    effective,decisions=replay(records,events,context,evaluated=evaluated)
    db.executescript('''
        CREATE TABLE rule_knowledge(id TEXT PRIMARY KEY,subject_id TEXT REFERENCES entries(id),kind TEXT,status TEXT,data TEXT NOT NULL);
        CREATE TABLE effective_knowledge(id TEXT PRIMARY KEY REFERENCES rule_knowledge(id),subject_id TEXT REFERENCES entries(id),kind TEXT,status TEXT,review_status TEXT,data TEXT NOT NULL);
        CREATE INDEX knowledge_subject ON effective_knowledge(subject_id,kind);
        CREATE TABLE knowledge_decisions(id TEXT PRIMARY KEY,knowledge_id TEXT,status TEXT,data TEXT NOT NULL);
        CREATE TABLE knowledge_annotations(id TEXT PRIMARY KEY,annotation_id TEXT,subject_id TEXT REFERENCES entries(id),kind TEXT,status TEXT,data TEXT NOT NULL);
        CREATE INDEX knowledge_annotation_subject ON knowledge_annotations(subject_id,annotation_id,status);
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
    ann_rows = [_annotation_row(r) for r in evaluated]
    for row in ann_rows:
        db.execute(
            'INSERT INTO knowledge_annotations VALUES(?,?,?,?,?,?)',
            (row['id'], row['annotation_id'], row['subject_id'], row['kind'], row['status'], canonical(row)),
        )
        if row['status'] in {'stale', 'missing', 'ambiguous', 'conflict'}:
            review = {
                'id': 'annotation_review_' + row['id'],
                'target': row['subject_id'] or row['annotation_id'],
                'reason': 'knowledge_annotation_' + row['status'],
                'priority': 'high',
                'detail': {
                    'annotation_id': row['annotation_id'],
                    'revision_id': row['id'],
                    'status': row['status'],
                    'subject_match': row['subject_match'],
                    'detail': row['detail'],
                },
                'evidence': row['subject'],
            }
            db.execute('INSERT INTO review_queue VALUES(?,?,?,?,?)', (review['id'], review['target'], review['reason'], review['priority'], canonical(review)))
    db.execute('INSERT INTO metadata VALUES(?,?)', ('annotation_snapshot', canonical(ann_snapshot)))
    from anna.knowledge.rule_index import build_rule_index
    build_rule_index(db,effective)
    build_effect_index(db,effective)
    count=0
    for option in branches(context,effective):
        db.execute('INSERT INTO casting_options VALUES(?,?,?,?,?,?,?,?,?)',tuple(option[k] for k in ('id','root_id','subject_id','spell_id','mode','resource','recovery','selection'))+(canonical(option),));count+=1
    coverage={'schema':SCHEMA,'observations':len(records),'casting_options':count,
        'by_kind':dict(Counter(r['kind'] for r in records)),
        'outcomes':dict(Counter(r['status'] for r in effective)),
        'reviews':dict(Counter(r['review_status'] for r in effective)),
        'annotations':dict(Counter(row['status'] for row in ann_rows)),
        'unparsed':dict(Counter(g['reason'] for r in records for g in r['value'].get('unparsed',[])))}
    db.execute('INSERT INTO metadata VALUES(?,?)',('knowledge_coverage',canonical(coverage)))
    for name,rows in [('rule_knowledge',records),('effective_knowledge',effective),('knowledge_decisions',decisions),('knowledge_annotations',ann_rows)]:
        (Path(out)/(name+'.jsonl')).write_text(''.join(canonical(r)+'\n' for r in rows))
    (Path(out)/'knowledge_coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2)+'\n')
    return coverage


def audit_knowledge(db, folder):
    """Check exact evidence, projections and exports; summarize by actual source."""
    if not exists(db):return None
    context=Context(db);errors=[];matrix=Counter()
    present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    records=[json.loads(r[0]) for r in db.execute('SELECT data FROM effective_knowledge')]
    for record in records:
        try:validate_record(record,context)
        except ValueError as exc:errors.append(record['id']+': '+str(exc))
        for sid in context.entries[record['subject_id']]['source_ids'] or ['unknown']:
            matrix[(sid,record['kind'],record['status'])]+=1
    exports = ['rule_knowledge','effective_knowledge','knowledge_decisions']
    if 'knowledge_annotations' in present:
        exports.append('knowledge_annotations')
    for name in exports:
        path=Path(folder)/(name+'.jsonl')
        expected={r[0]:json.loads(r[1]) for r in db.execute('SELECT id,data FROM '+name)}
        exported=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        actual={r['event']['id'] if name=='knowledge_decisions' else r['id']:r for r in exported}
        if actual!=expected or len(actual)!=len(exported):errors.append(name+': export mismatch')
    expected={r['id']:r for r in branches(context,records)}
    actual={r[0]:json.loads(r[1]) for r in db.execute('SELECT id,data FROM casting_options')}
    if expected!=actual:errors.append('casting_options: projection mismatch')
    from anna.knowledge.rule_index import rule_rows
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


def save_annotation(db, path, entry_id, kind, anchors, statements, reviewer, reason, *,
                    annotation_id=None, scope=None, supersedes=None):
    if not reviewer.strip() or not reason.strip():
        raise ValueError('Reviewer and reason are required')
    context = Context(db)
    with locked(path):
        require_annotations_appendable(db, path)
        events, log_hash = load_annotations(path)
        pending = build_annotation_event(
            context, entry_id, kind, anchors, statements, reviewer.strip(), reason.strip(),
            annotation_id=annotation_id, scope=scope, supersedes=supersedes,
        )
        states, _ = annotation_lifecycle(events)
        active = [
            e['id'] for e in events
            if e['operation'] == 'annotate'
            and e['annotation_id'] == pending['annotation_id']
            and states[e['id']] in {'active', 'conflict'}
        ]
        if active != ([supersedes] if supersedes else []):
            raise ValueError('Supersede the active annotation revision or revoke competing revisions')
        return append_annotation_events(path, [pending], log_hash)[0]


def revoke_annotation(path, rev_id, reviewer, reason):
    if not reviewer.strip() or not reason.strip():
        raise ValueError('Reviewer and reason are required')
    with locked(path):
        events, log_hash = load_annotations(path)
        states, _ = annotation_lifecycle(events)
        if states.get(rev_id) not in {'active', 'conflict'}:
            raise ValueError('Only active or conflicting annotation revisions can be revoked')
        return append_annotation_events(
            path,
            [{'operation': 'revoke', 'target_revision_id': rev_id, 'reviewer': reviewer.strip(), 'reason': reason.strip()}],
            log_hash,
        )[0]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=PROJECT/'data/extracted/catalog.sqlite')
    parser.add_argument('--decisions',type=Path,default=DEFAULT_DECISIONS)
    parser.add_argument('--annotations',type=Path,default=DEFAULT_ANNOTATIONS)
    commands=parser.add_subparsers(dest='command',required=True)
    listing=commands.add_parser('list');listing.add_argument('--kind',choices=sorted(KINDS));listing.add_argument('--status');listing.add_argument('--limit',type=int,default=20)
    viewing=commands.add_parser('inspect');viewing.add_argument('id')
    clauses_cmd=commands.add_parser('clauses');clauses_cmd.add_argument('id')
    ann_list=commands.add_parser('annotations');ann_list.add_argument('--status');ann_list.add_argument('--limit',type=int,default=50)
    ann_cmd=commands.add_parser('annotate')
    ann_cmd.add_argument('id')
    ann_cmd.add_argument('--kind',required=True,choices=('grant','procedure'))
    ann_cmd.add_argument('--anchors')
    ann_cmd.add_argument('--statements')
    ann_cmd.add_argument('--scope')
    ann_cmd.add_argument('--file',type=Path)
    ann_cmd.add_argument('--annotation-id')
    ann_cmd.add_argument('--reviewer',required=True)
    ann_cmd.add_argument('--reason',required=True)
    ann_cmd.add_argument('--supersedes')
    for name in ('accept','reject'):
        cmd=commands.add_parser(name);cmd.add_argument('id');cmd.add_argument('--basis',required=True);cmd.add_argument('--reviewer',required=True);cmd.add_argument('--reason',required=True);cmd.add_argument('--supersedes')
    reading=commands.add_parser('chain');reading.add_argument('id')
    undo=commands.add_parser('revoke');undo.add_argument('id');undo.add_argument('--reviewer',required=True);undo.add_argument('--reason',required=True)
    args=parser.parse_args()
    try:
        if args.command=='revoke':
            if args.id.startswith('ann_rev_'):
                result = revoke_annotation(args.annotations, args.id, args.reviewer, args.reason)
            else:
                from anna.review.commands import revoke
                result=revoke(args.decisions,args.id,args.reviewer,args.reason)
        else:
            with sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True) as db:
                if args.command=='inspect':result=inspect(db,args.id)
                elif args.command=='clauses':
                    result=inspect_clauses(Context(db),args.id)
                elif args.command=='annotations':
                    if not 1<=args.limit<=500:raise ValueError('Limit must be between 1 and 500')
                    filters=[];values=[]
                    if args.status:filters.append('status=?');values.append(args.status)
                    result=[json.loads(r[0]) for r in db.execute('SELECT data FROM knowledge_annotations'+(' WHERE '+' AND '.join(filters) if filters else '')+' ORDER BY annotation_id,id LIMIT ?',values+[args.limit])]
                elif args.command=='annotate':
                    payload = json.loads(args.file.read_text()) if args.file else {}
                    anchors = json.loads(args.anchors) if args.anchors else payload.get('anchors')
                    statements = json.loads(args.statements) if args.statements else payload.get('statements')
                    scope = json.loads(args.scope) if args.scope else payload.get('scope')
                    aid = args.annotation_id or payload.get('annotation_id')
                    if not isinstance(anchors, list) or not isinstance(statements, dict):
                        raise ValueError('Both anchors (list) and statements (object) are required')
                    result = save_annotation(
                        db, args.annotations, args.id, args.kind, anchors, statements,
                        args.reviewer, args.reason, annotation_id=aid, scope=scope, supersedes=args.supersedes,
                    )
                elif args.command=='chain':
                    from anna.presentation.knowledge import chain
                    result=chain(db,args.id)
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
