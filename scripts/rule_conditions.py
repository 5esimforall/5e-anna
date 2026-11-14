"""Condition descriptions and local effect references; never evaluate character state."""
import re

ABILITIES=('力量','敏捷','体质','智力','感知','魅力')


def prerequisite_expression(raw):
    """Full-clause grammar. An unknown term makes the entire conjunction unknown."""
    terms=[]
    for part in re.split(r'[，,]|且',re.sub(r'\s+','',raw)):
        level=re.fullmatch(r'等级(\d+)\+',part)
        ability=re.fullmatch(r'((?:'+'|'.join(ABILITIES)+r')(?:[、或](?:'+'|'.join(ABILITIES)+r'))*)(\d+)\+',part)
        if level:terms.append({'kind':'level','minimum':int(level[1])})
        elif ability:
            if '、' in ability[1] and '或' not in ability[1]:return None
            choices=[{'kind':'ability_score','ability':a,'minimum':int(ability[2])} for a in re.split('[、或]',ability[1])]
            terms.append(choices[0] if len(choices)==1 else {'any':choices})
        else:return None
    return terms[0] if len(terms)==1 else {'all':terms}


def condition_terms(value):
    if not isinstance(value,dict):return
    yield value
    for op in ('all','any'):
        for term in value.get(op,[]):yield from condition_terms(term)
    if 'not' in value:yield from condition_terms(value['not'])


REFERENCE_KINDS={'attack_result':'attack_roll','save_result':'saving_throw','target_ref':'targets',
                 'check_ref':'saving_throw','damage_ref':'damage','quantity_ref':'damage','condition':'condition'}


def local_references(value,path=''):
    """Enumerate semantic references, not link-index rows or the effect's own identity."""
    if isinstance(value,dict):
        kind=value.get('kind')
        if kind in REFERENCE_KINDS and (kind!='condition' or 'value' in value):yield path,value
        for key,item in value.items():
            if key!='links':yield from local_references(item,path+'.'+key if path else key)
    elif isinstance(value,list):
        for index,item in enumerate(value):yield from local_references(item,path+'.'+str(index))


def matches_reference(effect,ref):
    if effect['kind']!=REFERENCE_KINDS[ref['kind']]:return False
    if ref['kind']=='condition':return effect['condition']==ref['value']
    if ref['kind']=='target_ref' and ref.get('selector')=='area':
        return effect['selection']['kind']=='area' and (not ref.get('shape') or ref['shape']==effect['selection'].get('shape'))
    if ref['kind'] in {'save_result','check_ref'}:
        return effect.get('check_role','initial')==ref.get('scope','initial') and (not ref.get('ability') or effect.get('ability')==ref['ability'])
    if ref['kind'] in {'damage_ref','quantity_ref'}:
        return effect['quantity']['kind']!='quantity_ref' and (ref.get('selector')!='initial_damage' or effect.get('timing')=='immediate')
    return True


def effect_links(effect):
    return sorted([{'field':field,'role':ref['kind'],'target_effect_id':ref.get('effect_id'),
             'candidate_effect_ids':ref.get('candidate_effect_ids',[]),'status':'resolved' if ref.get('effect_id') else 'unresolved'}
            for field,ref in local_references(effect)],key=lambda link:link['field'])


def bind_effects(effects,unparsed=()):
    """Each reference binds a unique evidenced antecedent of the right role and type."""
    gaps=[]
    for index,effect in enumerate(effects):effect['id']='effect_'+str(index+1)
    for index,effect in enumerate(effects):
        for field,ref in local_references(effect):
            candidates=[e for e in effects[:index] if matches_reference(e,ref)]
            ref['candidate_effect_ids']=[e['id'] for e in candidates]
            ref['effect_id']=candidates[0]['id'] if len(candidates)==1 else None
            if ref['effect_id'] and any(candidates[0]['evidence']['span'][1]<=g['evidence']['span'][0]<effect['evidence']['span'][0] for g in unparsed):
                ref['effect_id']=None
            if not ref['effect_id']:
                reason='unresolved_effect_condition' if ref['kind'] in {'attack_result','save_result','condition'} else 'unresolved_effect_reference'
                gap={'reason':reason,'field':field,'evidence':effect['evidence']}
                if gap not in gaps:gaps.append(gap)
        effect['links']=effect_links(effect)
    return gaps


def validate_conditions(record):
    value=record['value'];effects=value.get('effects',[])
    by_id={e['id']:e for e in effects};positions={e['id']:i for i,e in enumerate(effects)}
    if len(by_id)!=len(effects):raise ValueError('Duplicate local effect identity')
    def check(value,before=None):
        for _,ref in local_references(value):
            target_id=ref.get('effect_id')
            if not target_id:continue
            if target_id not in by_id:raise ValueError('Condition refers to a missing local effect')
            if before is not None and positions[target_id]>=before:raise ValueError('Condition antecedent must precede its outcome')
            if not matches_reference(by_id[target_id],ref):raise ValueError('Local effect reference has the wrong type or scope')
    for index,effect in enumerate(effects):
        check(effect,index)
        if 'links' in effect and effect['links']!=effect_links(effect):raise ValueError('Local effect links disagree with their references')
    branches={(g['id'],b['id']) for g in value.get('grants',[]) for b in g['branches']}
    for rule in value.get('rules',[]):
        scope=rule['scope']
        if scope['kind']=='effect' and scope['effect_id'] not in by_id:raise ValueError('Rule scope refers to a missing effect')
        if scope['kind']=='usage_branch' and (scope['grant_id'],scope['branch_id']) not in branches:raise ValueError('Rule scope refers to a missing usage branch')
        if scope['kind']=='casting_field' and scope['field'] not in value.get('casting',{}):raise ValueError('Rule scope refers to a missing casting field')
        when=rule.get('when');check(when);check(rule.get('ends'))
        for term in condition_terms(when):
            if term.get('kind')=='branch_selected' and (term['grant_id'],term['branch_id'])!=(scope.get('grant_id'),scope.get('branch_id')):
                raise ValueError('Rule condition and branch scope disagree')
