"""Queryable effect rows retain one spell and one local effect, never a mixed summary."""
import json
from rule_conditions import local_references
from spell_effects import DAMAGE_TYPES

QUERY_VALUES={
    'effect':('teleport','healing','saving_throw','condition','attitude','awareness','upcast','targets','attack_roll','damage','modifier','restriction','behavior','check_modifier','end_spell','level_scale','ignite'),
    'damage_type':tuple(DAMAGE_TYPES.values()),
    'delivery':('attack_roll','saving_throw','automatic','not_stated'),
    'timing':('immediate','ongoing','repeat','not_stated'),
}
COLUMNS=('id','spell_id','effect_id','effect','damage_type','delivery','timing')


def effect_rows(records):
    for record in records:
        if record['kind']!='spell_effect' or record['status']!='resolved':continue
        for effect in record['value']['effects']:
            if effect['kind'] not in QUERY_VALUES['effect']:continue
            refs=[r for _,r in local_references(effect)]
            delivery=effect.get('delivery')
            if not delivery:
                delivery='attack_roll' if effect['kind']=='attack_roll' or any(r['kind']=='attack_result' for r in refs) else 'saving_throw' if any(r['kind'] in {'save_result','check_ref'} for r in refs) or effect['kind']=='saving_throw' else 'not_stated'
            timing=effect.get('timing')
            if not timing:timing='repeat' if effect.get('check_role')=='repeat' or any(r['kind'] in {'check_ref','save_result'} and r.get('scope')=='repeat' for r in refs) else 'immediate' if delivery in {'attack_roll','saving_throw'} else 'not_stated'
            yield {'id':record['id']+':'+effect['id'],'spell_id':record['subject_id'],'effect_id':effect['id'],
                   'effect':effect['kind'],'damage_type':effect.get('damage_type','not_stated'),'delivery':delivery,'timing':timing,
                   'record_id':record['id'],'review_status':record['review_status'],'statement':effect}


def build_effect_index(db,records):
    db.executescript('''CREATE TABLE spell_effect_index(id TEXT PRIMARY KEY,spell_id TEXT REFERENCES entries(id),
        effect_id TEXT,effect TEXT,damage_type TEXT,delivery TEXT,timing TEXT,data TEXT NOT NULL);
        CREATE INDEX spell_effect_subject ON spell_effect_index(spell_id,effect,damage_type,delivery,timing);''')
    for row in effect_rows(records):
        db.execute('INSERT INTO spell_effect_index VALUES(?,?,?,?,?,?,?,?)',tuple(row[k] for k in COLUMNS)+(json.dumps(row,ensure_ascii=False),))


def audit_effect_index(db,records):
    expected={r['id']:{k:r[k] for k in COLUMNS}|{'data':r} for r in effect_rows(records)}
    actual={r[0]:dict(zip(COLUMNS,r[:-1]))|{'data':json.loads(r[-1])}
            for r in db.execute('SELECT '+','.join(COLUMNS)+',data FROM spell_effect_index')}
    return [] if expected==actual else ['spell_effect_index: projection mismatch']
