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

from casting_extract import parse_access, parse_spell, evidence, CLASSES, ABILITIES, GRANT
from concepts import Resolver, aliases
from html_extract import key
from review_store import (DEFAULT_DECISIONS, canonical, digest, lifecycle, load_events,
                          append_event, locked, require_appendable, KNOWLEDGE_OPERATIONS)
from paths import PROJECT
from rule_conditions import validate_conditions

SCHEMA = 1
ACCESS_TYPES = {'feat', 'class_feature', 'species', 'magic_item', 'reward'}
REVIEW_OPERATIONS = KNOWLEDGE_OPERATIONS
KINDS = {'spell_access', 'spell_effect', 'feat_access', 'definition_role'}
QUERY_VALUES = {
    'mode': ('known', 'prepared', 'cast', 'list'),
    'resource': ('slot_free', 'spell_slot', 'not_stated'),
    'recovery': ('long_rest', 'short_rest', 'short_or_long_rest', 'not_stated'),
    'selection': ('fixed', 'choice'),
}


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='effective_knowledge'").fetchone())


def is_knowledge(event):
    return event['operation'] in REVIEW_OPERATIONS


class Context:
    def __init__(self, db):
        self.entries = {e['id']: e for raw, in db.execute('SELECT data FROM entries') for e in [json.loads(raw)]}
        self.pages = dict(db.execute('SELECT path,text FROM pages'))
        self.sources = [json.loads(raw) for raw, in db.execute('SELECT data FROM sources')]
        self.source_by_id = {s['id']: s for s in self.sources}
        self.resolver = Resolver(list(self.entries.values()), self.sources)
        self.spell_names = defaultdict(set)
        for e in self.entries.values():
            if e['type']=='spell':
                for n in aliases(e):
                    if len(n)>1: self.spell_names[n].add(e['id'])
        self.name_pattern = re.compile('|'.join(re.escape(n) for n in sorted(self.spell_names,key=lambda n:(-len(n),n)))) if self.spell_names else None

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
        import browse
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
        def definition(e):
            return {k:e.get(k) for k in ('id','name','name_en','name_zh','type','ruleset','source_ids','parent_id','fields','page_path','start','title_end','end')} | {
                'body_sha256':digest(self.pages[e['page_path']][e['start']:e['end']])}
        source_ids={s for i in ids if i in self.entries for s in self.entries[i]['source_ids']}
        return {'schema':SCHEMA,'record':record,
            'definitions':{i:definition(self.entries[i]) if i in self.entries else None for i in sorted(ids)},
            'sources':{s:{k:self.source_by_id.get(s,{}).get(k) for k in ('name','ruleset_hint','reference_role','origin','archived')} for s in sorted(source_ids)}}


def walk_targets(value):
    if isinstance(value,dict):
        if 'resolution' in value:yield value
        for v in value.values():yield from walk_targets(v)
    elif isinstance(value,list):
        for v in value:yield from walk_targets(v)


def validate_record(record, context):
    if record['kind'] not in KINDS or record['subject_id'] not in context.entries:raise ValueError('Invalid knowledge subject or kind')
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
    for target in walk_targets(record['value']):
        r=target['resolution']
        if r['status']=='resolved' and len(r['targets'])!=1:raise ValueError('Resolved target must be unique')
        if any(i not in context.entries for i in r['targets']):raise ValueError('Missing knowledge target')



def rule_links(entry, value, context):
    """Every rule dependency names the local slot it governs; exceptions stay scoped."""
    links=[]
    def add(label, relation, scope, ev, *, change=None, when=None):
        links.append({'id':'rule_'+str(len(links)+1),'relation':relation,'scope':scope,
            'change':change,'when':when,'target':{'kind':'entry','name':label,
            'resolution':context.resolve(entry,label,'concept'),'evidence':ev}})
    for field,label in [('action','施法时间'),('range','施法距离'),('components','法术成分'),('duration','持续时间')]:
        if field in value.get('casting',{}):add(label,'uses_rule',{'kind':'casting_field','field':field},value['casting'][field]['evidence'])
    for grant in value.get('grants',[]):
        for branch in grant['branches']:
            scope={'kind':'usage_branch','grant_id':grant['id'],'branch_id':branch['id']}
            if branch['resource']['kind']=='spell_slot':
                free=branch['resource'].get('consumed') is False
                add('法术位','exception_to' if free else 'uses_rule',scope,branch['evidence'][0],
                    change={'field':'spell_slot.consumed','value':False} if free else None,
                    when={'kind':'branch_selected','grant_id':grant['id'],'branch_id':branch['id']})
            if branch.get('recovery',{}).get('event')=='long_rest':add('长休','uses_rule',scope,branch['evidence'][-1])
    for effect in value.get('effects',[]):
        scope={'kind':'effect','effect_id':effect['id']}
        if effect['kind']=='saving_throw':add('豁免检定','uses_rule',scope,effect['evidence'])
        if effect['kind']=='condition':add(effect['condition'],'uses_rule',scope,effect['evidence'],when=effect.get('when'))
        for modifier in effect.get('modifiers',[]):
            if modifier['effect']=='advantage':
                add('优势','uses_rule',scope,effect['evidence'],when=modifier['when'])
    return links


def record_for(entry, kind, value, context):
    value['rules']=rule_links(entry,value,context)
    dependencies=set()
    for t in walk_targets(value): dependencies.update(t['resolution']['targets'])
    for g in value.get('grants',[]):
        if g['target']['kind']=='choice':
            g['target']['options']=context.choice_options(entry,g['target'])
            dependencies.update(g['target']['options'])
    # Bind every competing name, including currently inapplicable editions: a change
    # to generation/type/source must reopen an approved target selection.
    for t in walk_targets(value):dependencies.update(context.resolver.names.get(key(t.get('name','')),()))
    gaps=value.get('unparsed',[])
    unresolved=any(t['resolution']['status']!='resolved' for k,v in value.items() if k!='rules' for t in walk_targets(v))
    unfinished=value.get('prerequisites',{}).get('status')=='unparsed' or kind=='spell_access' and not value.get('grants') or kind=='spell_effect' and not value.get('effects') or any(g['mode']=='selection' or g['target']['kind']=='choice' and not g['target'].get('options') for g in value.get('grants',[]))
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
    return {'target':target,'constraints':{'class_list':classes} if classes else {},
        'unparsed':[{'reason':'unparsed_grant_qualifier','evidence':ev}] if unknown else []}


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
    records=list(definition_roles(context))
    for e in context.entries.values():
        if e['end']<=e['start']:continue
        if e['type']=='spell':
            records.append(record_for(e,'spell_effect',parse_spell(e,context.pages[e['page_path']]),context))
        elif e['type'] in ACCESS_TYPES:
            if not GRANT.search(re.sub(r'\s+','',context.pages[e['page_path']][e['title_end']:e['end']])):continue
            value=parse_access(e,context.pages[e['page_path']],context.mentions(e))
            if value:records.append(record_for(e,'spell_access',value,context))
        elif e['type']=='background':
            value=feat_access(e,context)
            if value:records.append(record_for(e,'feat_access',value,context))
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
    by_subject=defaultdict(dict)
    for record in effective:by_subject[record['subject_id']][record['kind']]=record
    def usable(record):return record and record['status']=='resolved'
    for root, kinds in list(by_subject.items()):
        acquisition=[];subject=root;restrictions={};visited=set()
        while usable(by_subject.get(subject,{}).get('feat_access')):
            record=by_subject.get(subject,{})['feat_access'];target=record['value']['target']['resolution']
            if target['status']!='resolved':break
            acquisition.append(record['id']); restrictions.update(record['value']['constraints'])
            subject=target['targets'][0]
            if subject in visited:break
            visited.add(subject)
        access=by_subject.get(subject,{}).get('spell_access')
        if not usable(access):continue
        for grant in access['value']['grants']:
            if grant['mode']=='selection':continue
            target=grant['target']
            if target['kind']=='entry':
                spells=target['resolution']['targets'] if target['resolution']['status']=='resolved' else []
            else:spells=context.choice_options(context.entries[subject],target,restrictions)
            for branch in grant['branches']:
                resource=branch['resource'];resource_kind='slot_free' if resource.get('consumed') is False else 'spell_slot' if resource.get('consumed') is True else 'not_stated'
                for spell_id in spells:
                    data={'root_id':root,'subject_id':subject,'knowledge_id':access['id'], 'grant_id':grant['id'],'branch_id':branch['id'],
                        'spell_id':spell_id,'mode':grant['mode'],'resource':resource_kind,'recovery':branch.get('recovery',{}).get('event','not_stated'),
                        'selection':'fixed' if target['kind']=='entry' else 'choice','acquisition':acquisition,'constraints':restrictions,
                        'grant':{**grant,'target':{k:v for k,v in target.items() if k!='options'}},'branch':branch}
                    data['id']='casting_'+digest([root,subject,grant['id'],branch['id'],spell_id])[:24]
                    yield data


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
    from chain_reading import build_rule_index
    build_rule_index(db,effective)
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
    from chain_reading import rule_rows
    expected_rules={r['id']:r for r in rule_rows(records)}
    rule_columns=('id','source_id','target_id','relation','status')
    actual_rules={r[0]:dict(zip(rule_columns,r[:-1]))|{'data':json.loads(r[-1])}
                  for r in db.execute('SELECT id,source_id,target_id,relation,status,data FROM knowledge_rule_links')}
    expected_rules={key:{k:row[k] for k in rule_columns}|{'data':row} for key,row in expected_rules.items()}
    if expected_rules!=actual_rules:errors.append('knowledge_rule_links: projection mismatch')
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
    if not isinstance(query,dict) or not query or any(k not in {*QUERY_VALUES,'spell'} for k in query):raise ValueError('Invalid casting query fields')
    for k,v in query.items():
        if k=='spell':
            if not isinstance(v,str) or not v.strip() or len(v)>200:raise ValueError('Invalid spell search')
        elif v not in QUERY_VALUES[k]:raise ValueError('Invalid casting query value')
    return query


def casting_condition(raw, alias='e'):
    query=casting_query(raw);clauses=['c.root_id='+alias+'.id'];values=[]
    for k,v in query.items():
        if k=='spell':clauses.append('EXISTS(SELECT 1 FROM entries s WHERE s.id=c.spell_id AND instr(lower(s.name),lower(?))>0)')
        else:clauses.append('c.'+k+'=?')
        values.append(v)
    return 'EXISTS(SELECT 1 FROM casting_options c WHERE '+' AND '.join(clauses)+')',values


def chain(db, entry_id, spell_id=None, reading_path=None):
    if not exists(db):return {'schema':SCHEMA,'records':[],'nodes':[],'options':[],'incoming':[]}
    if not db.execute('SELECT 1 FROM entries WHERE id=?',(entry_id,)).fetchone():raise LookupError('Entry not found')
    subject_ids={entry_id};options=[]
    # Group identical grants and branches; choice domains do not duplicate the whole UI.
    grouped={}
    for raw, in db.execute('SELECT data FROM casting_options WHERE root_id=? ORDER BY subject_id,id',(entry_id,)):
        option=json.loads(raw);key_=(option['subject_id'],option['grant_id'],option['branch_id'])
        if key_ not in grouped:grouped[key_]={k:v for k,v in option.items() if k not in {'id','spell_id'}}|{'spell_ids':[]}
        grouped[key_]['spell_ids'].append(option['spell_id']);subject_ids.add(option['subject_id'])
    options=list(grouped.values())
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
        providers={o['subject_id'] for o in options if spell_id in o['spell_ids']}
        if len(providers)==1:
            provider=next(iter(providers));reading_path=[entry_id]+([provider] if provider!=entry_id else [])+[spell_id]
    from chain_reading import reading
    return reading(db,entry_id,{'schema':SCHEMA,'records':records,'nodes':nodes,'options':options,'references':references,'incoming':incoming,'selected_spell_id':selected},reading_path)


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
            from review import revoke
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
