"""Queryable effect rows retain one spell and one local effect, never a mixed summary."""
import json
from rule_conditions import local_references, condition_terms, ABILITIES
from spell_effects import DAMAGE_TYPES

QUERY_VALUES={
    'effect':('teleport','healing','saving_throw','condition','attitude','awareness','upcast','targets','attack_roll','damage','modifier','restriction','behavior','check_modifier','end_spell','level_scale','ignite','save_override','target_constraint'),
    'damage_type':tuple(DAMAGE_TYPES.values()),
    'delivery':('attack_roll','saving_throw','automatic','not_stated'),
    'timing':('immediate','ongoing','repeat','not_stated'),
    'save_ability':(*ABILITIES,'not_stated'),
    'trigger':('attack_result','save_result','turn','damage_received','wake_action','outside_spell_range','cover','action_spent','requires_sleep','condition_immunity'),
    'end_trigger':('turn','spell_ends','damage_from'),
}
COLUMNS=('id','spell_id','effect_id','effect','damage_type','delivery','timing','save_ability')


def effect_rows(records):
    for record in records:
        if record['kind']!='spell_effect' or record['status']!='resolved':continue
        by_id={e['id']:e for e in record['value']['effects']}
        for effect in record['value']['effects']:
            if effect['kind'] not in QUERY_VALUES['effect']:continue
            refs=[r for _,r in local_references(effect)]
            delivery=effect.get('delivery')
            if not delivery:
                delivery=effect['kind'] if effect['kind'] in {'attack_roll','saving_throw'} else \
                    'attack_roll' if any(r['kind']=='attack_result' for r in refs) else \
                    'saving_throw' if any(r['kind'] in {'save_result','check_ref'} for r in refs) else 'not_stated'
            timing=effect.get('timing')
            if not timing:timing='repeat' if effect.get('check_role')=='repeat' or any(r['kind'] in {'check_ref','save_result'} and r.get('scope')=='repeat' for r in refs) else 'immediate' if delivery in {'attack_roll','saving_throw'} else 'not_stated'
            saves=[effect] if effect['kind']=='saving_throw' else [by_id[r['effect_id']] for r in refs if r['kind'] in {'save_result','check_ref'} and r.get('effect_id')]
            abilities={s.get('ability') for s in saves}
            save_ability=next(iter(abilities)) if len(abilities)==1 else None
            yield {'id':record['id']+':'+effect['id'],'spell_id':record['subject_id'],'effect_id':effect['id'],
                   'effect':effect['kind'],'damage_type':effect.get('damage_type','not_stated'),'delivery':delivery,'timing':timing,
                   'save_ability':save_ability or 'not_stated',
                   'triggers':sorted({t['kind'] for t in condition_terms(effect.get('when')) if 'kind' in t}),
                   'end_triggers':sorted({t['kind'] for t in condition_terms(effect.get('ends')) if 'kind' in t}),
                   'record_id':record['id'],'review_status':record['review_status'],'statement':effect}


def build_effect_index(db,records):
    db.executescript('''CREATE TABLE spell_effect_index(id TEXT PRIMARY KEY,spell_id TEXT REFERENCES entries(id),
        effect_id TEXT,effect TEXT,damage_type TEXT,delivery TEXT,timing TEXT,save_ability TEXT,data TEXT NOT NULL);
        CREATE INDEX spell_effect_subject ON spell_effect_index(spell_id,effect,damage_type,delivery,timing);''')
    for row in effect_rows(records):
        db.execute('INSERT INTO spell_effect_index VALUES(?,?,?,?,?,?,?,?,?)',tuple(row[k] for k in COLUMNS)+(json.dumps(row,ensure_ascii=False),))


def audit_effect_index(db,records):
    expected={r['id']:{k:r[k] for k in COLUMNS}|{'data':r} for r in effect_rows(records)}
    actual={r[0]:dict(zip(COLUMNS,r[:-1]))|{'data':json.loads(r[-1])}
            for r in db.execute('SELECT '+','.join(COLUMNS)+',data FROM spell_effect_index')}
    return [] if expected==actual else ['spell_effect_index: projection mismatch']
