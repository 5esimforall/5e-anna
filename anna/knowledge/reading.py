"""A bounded reading path over existing definitions, with scoped relationship evidence."""
import json
from collections import defaultdict
from anna.knowledge.conditions import condition_terms


def records_for(db, subject):
    return [json.loads(r[0]) for r in db.execute('SELECT data FROM effective_knowledge WHERE subject_id=? ORDER BY kind',(subject,))]


def rule_rows(records):
    for record in records:
        for link in record['value'].get('rules',[]):
            target=link['target']['resolution']
            status=target['status'] if record['status']=='resolved' else record['status']
            for tid in target['targets'] or [None]:
                row={'id':record['id']+':'+link['id']+':'+str(tid),'source_id':record['subject_id'],
                     'target_id':tid,'relation':link['relation'],'status':status,'record_id':record['id'],
                     'review_status':record['review_status'],'target_status':target['status'],'scope':link['scope'],'when':link['when'],
                     'change':link['change'],'ends':link['ends'],'evidence':link['target']['evidence']}
                yield row


def build_rule_index(db, records):
    db.executescript('''CREATE TABLE knowledge_rule_links(id TEXT PRIMARY KEY,source_id TEXT REFERENCES entries(id),
        target_id TEXT REFERENCES entries(id),relation TEXT,status TEXT,data TEXT NOT NULL);
        CREATE INDEX knowledge_rule_target ON knowledge_rule_links(target_id,relation,status);
        CREATE INDEX knowledge_rule_source ON knowledge_rule_links(source_id);''')
    for row in rule_rows(records):
        db.execute('INSERT INTO knowledge_rule_links VALUES(?,?,?,?,?,?)',
            tuple(row[k] for k in ('id','source_id','target_id','relation','status'))+(json.dumps(row,ensure_ascii=False),))


def path_value(raw):
    if raw is None:return []
    try:value=json.loads(raw) if isinstance(raw,str) else raw
    except ValueError as exc:raise ValueError('Invalid reading path') from exc
    if not isinstance(value,list) or len(value)>16 or any(not isinstance(i,str) or not i or len(i)>120 for i in value):
        raise ValueError('Invalid reading path')
    if len(value)!=len(set(value)):raise ValueError('Reading path contains a cycle')
    return value


def outgoing(db, subject, scoped_options):
    edges=[]
    def usage(option):
        return {'branch_id':option['branch_id'],'conditions':option['conditions'],'target':option['casting_target']}
    def target_edges(record, target, relation, scope, ev):
        resolution=target['resolution']
        status=resolution['status'] if record['status']=='resolved' else record['status']
        for target_id in resolution['targets'] or [None]:
            edges.append({'source_id':subject,'target_id':target_id,'relation':relation,'status':status,
                'record_id':record['id'],'target_status':resolution['status'],'scope':scope,'evidence':ev,'review_status':record['review_status']})
    for record in records_for(db,subject):
        value=record['value']
        if record['kind']=='grant':
            for grant in value['grants']:
                if grant['entry_type']!='spell':target_edges(record,grant['target'],'grants_feat' if grant['entry_type']=='feat' else 'grants_feature',{'kind':'acquisition','constraints':grant['constraints'],'expression':grant.get('when')},grant['target']['evidence'])
            pre=value['prerequisites']
            for term in condition_terms(pre.get('expression')):
                if term.get('target'):
                    target_edges(record,term['target'],'requires_option',{'kind':'acquisition','constraints':{},'expression':pre['expression']},pre['evidence'])
            options=scoped_options.get(subject)
            if options is None:options=[json.loads(r[0]) for r in db.execute('SELECT data FROM casting_options WHERE root_id=?',(subject,))]
            seen={}
            for option in options:
                if option['subject_id']!=subject:continue
                for tid in option.get('spell_ids',[option.get('spell_id')]):
                    token=(option['grant_id'],tid)
                    if token in seen:
                        seen[token]['scope']['usage'].append(usage(option));continue
                    edge={'source_id':subject,'target_id':tid,'relation':'eligible_spell' if option['selection']=='choice' else 'grants_spell',
                        'status':'resolved','record_id':record['id'],'review_status':record['review_status'],
                        'scope':{'kind':'grant','grant_id':option['grant_id'],'constraints':option['constraints'],'usage':[usage(option)]},'evidence':option['grant']['evidence'][0]}
                    seen[token]=edge;edges.append(edge)
            # Unresolved fixed grants remain inspectable, but never assert an effective option.
            for grant in value['grants']:
                if grant['entry_type']=='spell' and grant['target']['kind']=='entry' and (record['status']!='resolved' or grant['target']['resolution']['status']!='resolved'):
                    target_edges(record,grant['target'],'grants_spell',{'kind':'grant','grant_id':grant['id']},grant['evidence'][0])
    for raw, in db.execute('SELECT data FROM knowledge_rule_links WHERE source_id=? ORDER BY id',(subject,)):
        edges.append(json.loads(raw))
    for raw, in db.execute("SELECT data FROM knowledge_rule_links WHERE target_id=? AND relation='exception_to' AND status!='rejected' ORDER BY id",(subject,)):
        edge=json.loads(raw);edge.update(source_id=subject,target_id=edge['source_id'],relation='modified_by');edges.append(edge)
    for root,raw in db.execute('SELECT DISTINCT root_id,data FROM casting_options WHERE spell_id=?',(subject,)):
        option=json.loads(raw)
        prior=next((e for e in edges if e['relation']=='provided_by' and e['target_id']==root and e['scope']['grant_id']==option['grant_id'] and e.get('acquisition_steps')==option['acquisition_steps'] and e['record_id']==option['knowledge_id']),None)
        if prior:prior['scope']['usage'].append(usage(option))
        else:
            acquisition=[]
            for step in option['acquisition_steps']:
                rid=step['knowledge_id']
                record=json.loads(db.execute('SELECT data FROM effective_knowledge WHERE id=?',(rid,)).fetchone()[0])
                grant=next(g for g in record['value']['grants'] if g['id']==step['grant_id'])
                acquisition.append({'record_id':rid,'subject_id':record['subject_id'],'review_status':record['review_status'],
                                    'constraints':grant['constraints'],'evidence':grant['target']['evidence']})
            edges.append({'source_id':subject,'target_id':root,'relation':'provided_by','status':'resolved',
                'record_id':option['knowledge_id'],'acquisition':acquisition,'acquisition_steps':option['acquisition_steps'],
                'scope':{'kind':'grant','grant_id':option['grant_id'],'constraints':option['constraints'],'usage':[usage(option)]},'evidence':option['grant']['evidence'][0]})
    return edges


def segment_status(record):
    rules=record['value'].get('rules',[])
    return {'record_id':record['id'],'subject_id':record['subject_id'],'kind':record['kind'],
            'content_status':record['status'],'review_status':record['review_status'],
            'unparsed':record['value'].get('unparsed',[]),
            'rule_targets':[{'link_id':r['id'],'scope':r['scope'],'name':r['target']['name'],
                             'status':r['target']['resolution']['status'],'candidates':r['target']['resolution']['targets']}
                            for r in rules]}


def reading(db, root, chain, raw_path=None, effect_id=None):
    path=path_value(raw_path) or [root]
    if path[0]!=root:raise ValueError('Reading path must start at its root')
    scoped=defaultdict(list)
    for record in records_for(db,root):
        for grant in record['value'].get('grants',[]):
            if grant['entry_type']!='spell':
                for tid in grant['target']['resolution']['targets']:scoped[tid]=[]
    for option in chain['options']:
        route=[root]+[s['target_id'] for s in option['acquisition_steps']]
        # Keep only the acquisition route inspected by this path. A later rule or
        # reverse-link step does not change the provider's inherited conditions.
        overlap=min(len(route),len(path))
        if route[:overlap]==path[:overlap]:scoped[option['subject_id']].append(option)
        else:scoped.setdefault(option['subject_id'],[])

    edge_cache={}
    def edges(sid):
        if sid not in edge_cache:
            for record in records_for(db,sid):
                for grant in record['value'].get('grants',[]):
                    if grant['entry_type']=='spell':continue
                    options=[json.loads(r[0]) for r in db.execute('SELECT data FROM casting_options WHERE root_id=?',(sid,))]
                    for tid in grant['target']['resolution']['targets']:
                        if tid not in scoped:scoped[tid]=[o for o in options if o['subject_id']==tid]
            edge_cache[sid]=outgoing(db,sid,scoped)
        return edge_cache[sid]
    steps=[]
    for source,target in zip(path,path[1:]):
        matching=[e for e in edges(source) if e['target_id']==target]
        if not matching:raise ValueError('Reading path has no evidenced relationship')
        steps.append({'source_id':source,'target_id':target,'relations':matching})
    # Existing cards and the active path share one root and one choice domain.
    records={r['id']:r for r in chain['records']}
    for sid in path:
        records.update({r['id']:r for r in records_for(db,sid)})
    for r in list(records.values()):edges(r['subject_id'])
    edges(path[-1])
    all_edges=[e for group in edge_cache.values() for e in group]
    ids={root,*path,*[e['target_id'] for e in all_edges if e['target_id']],*[e['source_id'] for e in all_edges]}
    nodes={n['id']:n for n in chain['nodes']}
    for eid in sorted(ids):
        row=db.execute('SELECT data FROM entries WHERE id=?',(eid,)).fetchone()
        if not row:raise ValueError('Reading path target disappeared')
        e=json.loads(row[0]);nodes[eid]={k:e.get(k) for k in ('id','name','type','ruleset','source_ids','page_path','start','end')}
    if effect_id is not None:
        if not isinstance(effect_id,str) or not any(r['kind']=='procedure' and r['subject_id']==path[-1] and any(e['id']==effect_id for e in r['value']['effects']) for r in records.values()):
            raise ValueError('Effect is not part of the focused procedure')
    focus=nodes[path[-1]]
    text=db.execute('SELECT text FROM pages WHERE path=?',(focus['page_path'],)).fetchone()[0]
    chain.update(records=list(records.values()),nodes=list(nodes.values()),edges=all_edges,
        reading={'root_id':root,'path':path,'steps':steps,'focus_id':path[-1],'effect_id':effect_id,
                 'text':text[focus['start']:focus['end']] if len(path)>1 else None},
        segments=[segment_status(r) for r in records.values()])
    # Bind only the inspected path's supplier, not every grant that could mention this spell.
    spell_steps=[step for step in steps if nodes[step['target_id']]['type']=='spell']
    if spell_steps:
        step=spell_steps[-1];spell_id=step['target_id'];supplier=step['source_id']
        options=scoped.get(supplier)
        if options is None:options=[json.loads(r[0]) for r in db.execute('SELECT data FROM casting_options WHERE root_id=?',(supplier,))]
    else:spell_id=chain['selected_spell_id'];options=chain['options']
    chain['bindings']=[{'subject_id':o['subject_id'],'grant_id':o['grant_id'],'branch_id':o['branch_id'],
        'spell_id':spell_id,'variable':'spellcasting','value':o['grant'].get('ability'),
        'conditions':o['conditions'],'casting_target':o['casting_target'],
        'status':'described' if o['grant'].get('ability') else 'not_stated',
        'evidence':o['grant'].get('ability',{}).get('evidence')}
        for o in options if spell_id and spell_id in o.get('spell_ids',[o.get('spell_id')])]
    return chain
