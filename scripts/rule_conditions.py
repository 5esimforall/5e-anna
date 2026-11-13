"""Condition descriptions and local effect references; never evaluate character state."""
import re

ABILITIES=('力量','敏捷','体质','智力','感知','魅力')


def prerequisite_expression(raw):
    """Full-clause grammar. An unknown term makes the entire conjunction unknown."""
    terms=[]
    for part in re.split(r'[，,]|且',re.sub(r'\s+','',raw)):
        level=re.fullmatch(r'等级(\d+)\+',part)
        ability=re.fullmatch(r'((?:'+'|'.join(ABILITIES)+r')(?:或(?:'+'|'.join(ABILITIES)+r'))*)(\d+)\+',part)
        if level:terms.append({'kind':'level','minimum':int(level[1])})
        elif ability:
            choices=[{'kind':'ability_score','ability':a,'minimum':int(ability[2])} for a in ability[1].split('或')]
            terms.append(choices[0] if len(choices)==1 else {'any':choices})
        else:return None
    return terms[0] if len(terms)==1 else {'all':terms}


def condition_terms(value):
    if not isinstance(value,dict):return
    yield value
    for op in ('all','any'):
        for term in value.get(op,[]):yield from condition_terms(term)
    if 'not' in value:yield from condition_terms(value['not'])


def bind_effects(effects, unparsed=()):
    """Only a unique local antecedent binds an outcome; unresolved pronouns stay gaps."""
    gaps=[]
    for index,effect in enumerate(effects):
        effect['id']='effect_'+str(index+1)
        when=effect.get('when',{})
        if when.get('kind')=='save_result':
            candidates=[e for e in effects[:index] if e['kind']=='saving_throw']
            when['candidate_effect_ids']=[e['id'] for e in candidates]
            when['effect_id']=candidates[0]['id'] if len(candidates)==1 else None
        elif when.get('kind')=='condition':
            candidates=[e for e in effects[:index] if e['kind']=='condition' and e['condition']==when['value']]
            when['candidate_effect_ids']=[e['id'] for e in candidates]
            when['effect_id']=candidates[0]['id'] if len(candidates)==1 else None
        if when.get('effect_id'):
            antecedent=candidates[0]
            if any(antecedent['evidence']['span'][1]<=gap['evidence']['span'][0]<effect['evidence']['span'][0] for gap in unparsed):
                when['effect_id']=None
        if when.get('kind') in {'save_result','condition'} and not when.get('effect_id'):
            gaps.append({'reason':'unresolved_effect_condition','evidence':effect['evidence']})
    return gaps


def validate_conditions(record):
    value=record['value'];effects=value.get('effects',[])
    by_id={e['id']:e for e in effects};positions={e['id']:i for i,e in enumerate(effects)}
    if len(by_id)!=len(effects):raise ValueError('Duplicate local effect identity')
    def check(condition, before=None):
        for term in condition_terms(condition):
            ref=term.get('effect_id')
            if not ref:continue
            if ref not in by_id:raise ValueError('Condition refers to a missing local effect')
            target=by_id[ref]
            if before is not None and positions[ref]>=before:raise ValueError('Condition antecedent must precede its outcome')
            if term.get('kind')=='save_result' and target['kind']!='saving_throw':raise ValueError('Condition must refer to a saving throw')
            if term.get('kind')=='condition' and (target['kind']!='condition' or target['condition']!=term['value']):raise ValueError('Condition must refer to the matching status effect')
    for index,effect in enumerate(effects):
        check(effect.get('when'),index)
        check(effect.get('ends'))
        for modifier in effect.get('modifiers',[]):check(modifier.get('when'),index)
    branches={(g['id'],b['id']) for g in value.get('grants',[]) for b in g['branches']}
    for rule in value.get('rules',[]):
        scope=rule['scope']
        if scope['kind']=='effect' and scope['effect_id'] not in by_id:raise ValueError('Rule scope refers to a missing effect')
        if scope['kind']=='usage_branch' and (scope['grant_id'],scope['branch_id']) not in branches:raise ValueError('Rule scope refers to a missing usage branch')
        if scope['kind']=='casting_field' and scope['field'] not in value.get('casting',{}):raise ValueError('Rule scope refers to a missing casting field')
        when=rule.get('when');check(when)
        for term in condition_terms(when):
            if term.get('kind')=='branch_selected' and (term['grant_id'],term['branch_id'])!=(scope.get('grant_id'),scope.get('branch_id')):
                raise ValueError('Rule condition and branch scope disagree')
