"""Queryable effect rows retain one subject and one local effect, never a mixed summary."""
import json
from anna.knowledge.query_schema import EFFECT_QUERY_VALUES as QUERY_VALUES
from anna.knowledge.conditions import local_references, condition_terms
from anna.knowledge.model import effect_usage

COLUMNS=('id','subject_id','effect_id','effect','damage_type','delivery','timing','save_ability','target_scope','use_action')


def check_sources(effect,by_id):
    """Follow typed sequence references, never collect all checks in the spell."""
    if effect['kind']=='saving_throw':return [effect]
    found={}
    for _,ref in local_references(effect):
        target=by_id.get(ref.get('effect_id'))
        if not target:continue
        if ref['kind'] in {'save_result','check_ref'}:found[target['id']]=target
        elif ref['kind'] in {'check_total','sequence_ref'}:
            found.update({c['id']:c for c in check_sources(target,by_id)})
    return list(found.values())


def target_scope(effect,by_id,saves):
    target=effect.get('selection') if effect['kind']=='targets' else effect.get('target')
    if not target and saves:
        targets=[s.get('target') for s in saves]
        if all(t==targets[0] for t in targets):target=targets[0]
    if not target:return 'not_stated'
    if target.get('kind')=='target_ref':target=by_id.get(target.get('effect_id'),{}).get('selection',{})
    return 'area' if target.get('kind')=='area' else 'single' if target.get('count')==1 else 'not_stated'


def resolved_effect_ids(record):
    """Identify locally valid effects; transitive local references fail closed."""
    if record['kind'] != 'procedure':
        return set()
    effects = record['value'].get('effects', [])
    if record['status'] not in {'resolved', 'partial'}:
        return set()
    value = record['value']
    if value.get('prerequisites', {}).get('status') == 'unparsed':
        return set()
    if (value.get('activation') or {}).get('status') == 'unparsed':
        return set()
    competing = {
        (g['evidence']['path'], tuple(g['evidence']['span']))
        for g in value.get('unparsed', [])
        if g.get('reason') == 'competing_sequence_threshold' and g.get('evidence', {}).get('span')
    }
    valid = {
        e['id']
        for e in effects
        if e.get('initial_attack_result') != 'not_stated'
        and (e['evidence']['path'], tuple(e['evidence']['span'])) not in competing
    }
    changed = True
    while changed:
        changed = False
        for e in effects:
            if e['id'] not in valid:
                continue
            for _, ref in local_references(e):
                tid = ref.get('effect_id')
                if not tid or tid not in valid:
                    valid.remove(e['id'])
                    changed = True
                    break
    return valid


def effect_rows(records):
    for record in records:
        if record['kind']!='procedure':continue
        allowed=resolved_effect_ids(record)
        if not allowed:continue
        by_id={e['id']:e for e in record['value']['effects']}
        for effect in record['value']['effects']:
            if effect['id'] not in allowed or effect['kind'] not in QUERY_VALUES['effect']:continue
            refs=[r for _,r in local_references(effect)]
            delivery=effect.get('delivery')
            if not delivery:
                delivery=effect['kind'] if effect['kind'] in {'attack_roll','saving_throw'} else \
                    'attack_roll' if any(r['kind']=='attack_result' for r in refs) else \
                    'saving_throw' if any(r['kind'] in {'save_result','check_ref','check_total','sequence_ref'} for r in refs) else 'not_stated'
            timing=effect.get('timing')
            if not timing:timing='cumulative' if any(r['kind']=='check_total' for r in refs) else 'repeat' if effect.get('check_role')=='repeat' or any(r['kind'] in {'check_ref','save_result'} and r.get('scope')=='repeat' for r in refs) else 'immediate' if delivery in {'attack_roll','saving_throw'} else 'not_stated'
            saves=check_sources(effect,by_id)
            abilities={s.get('ability') for s in saves}
            save_ability=next(iter(abilities)) if len(abilities)==1 else None
            usage=effect_usage(record['value'],effect)
            yield {'id':record['id']+':'+effect['id'],'subject_id':record['subject_id'],'effect_id':effect['id'],
                   'effect':effect['kind'],'damage_type':effect.get('damage_type','not_stated'),'delivery':delivery,'timing':timing,
                   'save_ability':save_ability or 'not_stated',
                   'target_scope':target_scope(effect,by_id,saves),
                   'thresholds':[{'result':r['result'],'count':r['count'],'sequence_id':r['effect_id']} for r in refs if r['kind']=='check_total'],
                   'triggers':sorted({t['kind'] for t in condition_terms(effect.get('when')) if 'kind' in t}),
                   'end_triggers':sorted({t['kind'] for t in condition_terms(effect.get('ends')) if 'kind' in t}),
                   'record_id':record['id'],'review_status':record['review_status'],'statement':effect,'usage':usage,'use_action':usage.get('action','not_stated')}


def build_effect_index(db,records):
    db.executescript('''CREATE TABLE rule_effect_index(id TEXT PRIMARY KEY,subject_id TEXT REFERENCES entries(id),
        effect_id TEXT,effect TEXT,damage_type TEXT,delivery TEXT,timing TEXT,save_ability TEXT,target_scope TEXT,use_action TEXT,data TEXT NOT NULL);
        CREATE INDEX rule_effect_subject ON rule_effect_index(subject_id,effect,damage_type,delivery,timing);''')
    for row in effect_rows(records):
        db.execute('INSERT INTO rule_effect_index VALUES('+','.join('?' for _ in range(len(COLUMNS)+1))+')',tuple(row[k] for k in COLUMNS)+(json.dumps(row,ensure_ascii=False),))


def audit_effect_index(db,records):
    expected={r['id']:{k:r[k] for k in COLUMNS}|{'data':r} for r in effect_rows(records)}
    actual={r[0]:dict(zip(COLUMNS,r[:-1]))|{'data':json.loads(r[-1])}
            for r in db.execute('SELECT '+','.join(COLUMNS)+',data FROM rule_effect_index')}
    return [] if expected==actual else ['rule_effect_index: projection mismatch']
