"""Condition descriptions and local effect references; never evaluate character state."""
import re

ABILITIES=('力量','敏捷','体质','智力','感知','魅力')
CLASSES=('吟游诗人','牧师','德鲁伊','圣武士','游侠','术士','法师','魔契师','奇械师')


def conjunction(*terms):
    terms=[term for term in terms if term]
    return terms[0] if len(terms)==1 else {'all':terms} if terms else None


def split_expression(raw, separators):
    """Split only at the current parenthesis depth; malformed groups stay unknown."""
    depth=0;start=0;parts=[];index=0
    while index<len(raw):
        char=raw[index]
        if char=='(':depth+=1
        elif char==')':
            depth-=1
            if depth<0:return None
        if depth==0:
            separator=next((s for s in separators if raw.startswith(s,index)),None)
            if separator:
                parts.append(raw[start:index]);index+=len(separator);start=index;continue
        index+=1
    if depth:return None
    return parts+[raw[start:]]


def boolean_expression(raw, atom):
    """Whole-expression grammar: conjunction, disjunction and explicit grouping."""
    raw=re.sub(r'\s+','',raw).replace('（','(').replace('）',')')
    if not raw:return None
    # Some leaves share a suffix, e.g. 智力或感知13+. Parse them before operators.
    leaf=atom(raw)
    if leaf:return leaf
    for separators,op in [(('，',','),'all'),(('并且','且'),'all'),(('或者','或'),'any')]:
        parts=split_expression(raw,separators)
        if parts is None:return None
        if len(parts)>1:
            if separators==('并且','且') and len(split_expression(raw,('或者','或')))>1:return None
            if separators==('，',','):parts=[parts[0]]+[re.sub(r'^(?:并且|且)','',part) for part in parts[1:]]
            if op=='any':parts=[item for part in parts for item in split_expression(part,('、',))]
            values=[boolean_expression(part,atom) for part in parts]
            if not all(values):return None
            if op=='all':values=[term for value in values for term in value.get('all',[value])]
            return {op:values}
    if raw.startswith('(') and raw.endswith(')'):
        return boolean_expression(raw[1:-1],atom)
    if raw.startswith('是'):return boolean_expression(raw[1:],atom)
    return None


def prerequisite_expression(raw):
    """Full-clause grammar. An unknown term makes the entire conjunction unknown."""
    def atom(part):
        level=re.fullmatch(r'等级(\d+)\+',part)
        ability=re.fullmatch(r'((?:'+'|'.join(ABILITIES)+r')(?:[、或](?:'+'|'.join(ABILITIES)+r'))*)(\d+)\+',part)
        if level:return {'kind':'level','minimum':int(level[1])}
        if ability:
            if '、' in ability[1] and '或' not in ability[1]:return None
            choices=[{'kind':'ability_score','ability':a,'minimum':int(ability[2])} for a in re.split('[、或]',ability[1])]
            return choices[0] if len(choices)==1 else {'any':choices}
        class_level=re.fullmatch(r'('+'|'.join(CLASSES)+r')等级(\d+)\+',part)
        if class_level:return {'kind':'class_level','class':class_level[1],'minimum':int(class_level[2])}
        if part in CLASSES:return {'kind':'class','value':part}
        # These are named requirements, not inferred identities or granted abilities.
        named=re.fullmatch(r'([^(),，、或且]+?)(?:\(([^()]+)\))?(专长|特性|背景)',part)
        if named:return {'kind':'requirement','name':named[1],'entry_type':{'专长':'feat','特性':'class_feature','背景':'background'}[named[3]],
                         **({'qualifier':named[2]} if named[2] else {})}
        campaign=re.fullmatch(r'([^(),，、或且]+)战役',part)
        if campaign:return {'kind':'campaign','name':campaign[1]}
        return None
    # Shared numeric suffixes form one atom even when another alternative follows:
    # (智力或感知13+) 或 某项专长. Parentheses make that lexical scope explicit.
    shared=r'((?:'+'|'.join(ABILITIES)+r')(?:[、或](?:'+'|'.join(ABILITIES)+r'))+\d+\+)'
    return boolean_expression(re.sub(shared,r'(\1)',re.sub(r'\s+','',raw)),atom)


def item_condition(raw):
    """Deictic equipment requirements refer to this source item, not a guessed name."""
    named=r'(?:这[件根面本双顶枚支副把]|此|该)(?:物品|魔杖|法杖|武器|盾牌|书|牌组|戒指|头盔|靴子|帽子|帽)'
    antecedent=re.search(named,raw)
    if '其' in raw and (not antecedent or raw.index('其')<antecedent.end()):return None
    item=r'(?:'+named+r'|其)'
    def atom(part):
        m=re.fullmatch(r'(?:你)?(持握|穿戴|着装|携带)('+item+r')',part)
        if m:return {'kind':'item_state','state':{'持握':'held','穿戴':'worn','着装':'worn','携带':'carried'}[m[1]],'subject':'source_item'}
        if re.fullmatch(r'(?:你)?与'+item+r'(?:维持)?同调',part):return {'kind':'item_state','state':'attuned','subject':'source_item'}
        return None
    return boolean_expression(raw,atom)


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
        return (ref.get('scope')=='any' or effect.get('check_role','initial')==ref.get('scope','initial')) and \
            (not ref.get('ability') or effect.get('ability')==ref['ability']) and \
            ('occurrence' not in ref or effect.get('occurrence')==ref['occurrence'])
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
        # Resolve an inherited check first. Its ability is a supplied value, with an
        # explicit link to the statement supplying it, rather than a second observation.
        refs=list(local_references(effect))
        refs.sort(key=lambda item:0 if item[0]=='repeat_of' else 1)
        for field,ref in refs:
            prior=effects[:index]
            selector=ref.get('selector')
            if selector=='same_clause':prior=[e for e in prior if e['evidence']['span']==effect['evidence']['span']]
            elif selector=='previous_check':
                checks=[e for e in prior if e['kind'] in {'saving_throw','attack_roll'}]
                # Pick a statement group, not the last matching value. A later check
                # of a different type/ability must not silently revive an older check.
                prior=[e for e in checks if e['evidence']['span']==checks[-1]['evidence']['span']] if checks else []
            candidates=[e for e in prior if matches_reference(e,ref)]
            ref['candidate_effect_ids']=[e['id'] for e in candidates]
            ref['effect_id']=candidates[0]['id'] if len(candidates)==1 else None
            if ref['effect_id'] and any(candidates[0]['evidence']['span'][1]<=g['evidence']['span'][0]<effect['evidence']['span'][0] for g in unparsed):
                ref['effect_id']=None
            if not ref['effect_id']:
                reason='unresolved_effect_condition' if ref['kind'] in {'attack_result','save_result','condition'} else 'unresolved_effect_reference'
                gap={'reason':reason,'field':field,'evidence':effect['evidence']}
                if gap not in gaps:gaps.append(gap)
            if field=='repeat_of' and effect.get('ability_source')=='repeat_of':
                effect['ability']=candidates[0].get('ability') if ref['effect_id'] else None
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
        if effect.get('ability_source')=='repeat_of':
            target=by_id.get(effect['repeat_of'].get('effect_id'))
            if effect.get('ability')!=(target.get('ability') if target else None):raise ValueError('Inherited check ability disagrees with its antecedent')
        for _,ref in local_references(effect):
            target=by_id.get(ref.get('effect_id'))
            candidates=ref.get('candidate_effect_ids')
            if candidates is not None:
                if any(cid not in by_id or positions[cid]>=index for cid in candidates):raise ValueError('Invalid local reference candidate')
                if target and candidates!=[target['id']]:raise ValueError('Resolved local reference must have one candidate')
            if not target:continue
            if ref.get('selector')=='same_clause' and target['evidence']['span']!=effect['evidence']['span']:
                raise ValueError('Local check reference escapes its clause')
            if ref.get('selector')=='previous_check':
                checks=[e for e in effects[:index] if e['kind'] in {'saving_throw','attack_roll'}]
                if not checks or target['evidence']['span']!=checks[-1]['evidence']['span']:
                    raise ValueError('Local check reference skips a later check')
        if 'links' in effect and effect['links']!=effect_links(effect):raise ValueError('Local effect links disagree with their references')
    branches={(g['id'],b['id']) for g in value.get('grants',[]) for b in g['branches']}
    for rule in value.get('rules',[]):
        scope=rule['scope']
        if scope['kind']=='effect' and scope['effect_id'] not in by_id:raise ValueError('Rule scope refers to a missing effect')
        if scope['kind']=='usage_branch' and (scope['grant_id'],scope['branch_id']) not in branches:raise ValueError('Rule scope refers to a missing usage branch')
        if scope['kind']=='casting_field' and scope['field'] not in value.get('casting',{}):raise ValueError('Rule scope refers to a missing casting field')
        when=rule.get('when');check(when);check(rule.get('ends'));check(rule.get('change'))
        for term in condition_terms(when):
            if term.get('kind')=='branch_selected' and (term['grant_id'],term['branch_id'])!=(scope.get('grant_id'),scope.get('branch_id')):
                raise ValueError('Rule condition and branch scope disagree')
