"""Branch-scoped queries and rule-chain responses over a published read model."""
import json
from anna.serialization import canonical
from anna.knowledge.context import walk_targets
from anna.presentation import contracts
from anna.knowledge.query_schema import EFFECT_QUERY_VALUES, EFFECT_QUERY_PARAMETERS
from anna.presentation.contracts import KNOWLEDGE_SCHEMA as SCHEMA


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='effective_knowledge'").fetchone())


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
        option=contracts.option(json.loads(raw));key_=(option['subject_id'],option['grant_id'],option['branch_id'],canonical(option['acquisition_steps']))
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
        records.extend(contracts.record(json.loads(raw)) for raw, in db.execute('SELECT data FROM effective_knowledge WHERE subject_id=? ORDER BY kind',(sid,)))
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
    from anna.presentation.reading import reading
    return reading(db,entry_id,{'schema':SCHEMA,'records':records,'nodes':nodes,'options':options,'references':references,'incoming':incoming,'selected_spell_id':selected},reading_path,effect_id)


def export(db, entry_id, spell_id=None, reading_path=None, effect_id=None):
    return {'schema': SCHEMA,
            'snapshot': json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0]),
            'chain': chain(db, entry_id, spell_id, reading_path, effect_id)}
