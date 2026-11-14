"""Target-qualified checks and cumulative outcomes, without rolling or counting state.

These grammars preserve source scopes. A threshold references a repeated-check
sequence; it does not quietly add the initial save to that sequence's count.
"""
from copy import deepcopy
import re
from casting_extract import NUMBER, number, quantity_expression
from rule_conditions import ABILITIES, reference, outcome, target_ref, turn

ABILITY='(?:'+'|'.join(ABILITIES)+')'
SHAPES={'锥状':'cone','球状':'sphere','立方':'cube'}


def parse_sequence_clause(clause,ev,damage_types):
    damage_pattern='(?:'+'|'.join(damage_types)+')'
    nodes=[]
    def emit(kind,**fields):nodes.append({'kind':kind,**fields,'evidence':ev})
    def total(result,count):return reference('check_total',result=result,count=number(count))
    def emit_sequence(ability,when,target=None):
        target=target or target_ref()
        # Each nested target is its own reference instance, bound and audited below.
        emit('saving_throw',ability=ability,check_role='repeat',target=deepcopy(target),when=when,
             **({'ability_source':'repeat_of'} if ability is None else {}),
             repeat_of=reference('check_ref',scope='initial',**({'ability':ability} if ability else {}),target=deepcopy(target)))
        emit('check_sequence',check=reference('check_ref',scope='repeat',selector='same_clause'),
             target=deepcopy(target),counting_scope='per_target',initial_inclusion='not_stated')
    m=re.fullmatch(r'你制造出一块冰晶裂片，并将它射向施法距离内的一个生物。',clause)
    if m:emit('targets',selection={'kind':'creature','count':1,'within':'spell_range'})
    elif re.fullmatch(r'无论击中与否，裂片都会在(?:那)?之后(?:立刻)?爆裂。',clause):
        emit('event',event='burst',after=reference('attack_complete'),initial_attack_result='irrelevant')
    elif (m:=re.fullmatch(r'(?:攻击目标和其(\d+)尺范围内的每名生物都|目标和周围(\d+)尺范围内的每个生物)必须通过一次('+ABILITY+r')豁免检定，否则(?:将)?受到(\d+d\d+)点?('+damage_pattern+r')伤害。',clause)):
        emit('targets',selection={'kind':'area','shape':'radius','size':int(m[1] or m[2]),'unit':'foot',
             'members':'creature','origin':target_ref(selector='attack_target'),'includes_origin':True})
        emit('saving_throw',ability=m[3],check_role='initial',target=target_ref(selector='same_clause'),when=reference('event_ref'))
        emit('damage',quantity=quantity_expression(m[4]),damage_type=damage_types[m[5]],timing='immediate',
             when=outcome('save','failure',selector='same_clause'),target=target_ref(selector='same_clause'))
    elif (m:=re.fullmatch(r'(?:你使用的法术位每比('+NUMBER+r')环高一环，|当你使用('+NUMBER+r')环或更高的法术位施放该法术时，使用的法术位每高一环，)('+damage_pattern+r')伤害(?:就|便)增加(\d+d\d+)点?。',clause)):
        level=number(m[1]) if m[1] else number(m[2])-1
        if level<0:return None
        emit('upcast',threshold=level,per_slot_level=1,effect={'kind':'damage','quantity':quantity_expression(m[4])},
             applies_to=reference('damage_ref',damage_type=damage_types[m[3]]))
    # A named area is an explicit cross-paragraph subject, not "the last save".
    elif (m:=re.fullmatch(r'该(锥状|球状|立方)区域内的生物豁免失败时，受到(\d+d\d+)点?('+damage_pattern+r')伤害。',clause)):
        target=target_ref(selector='area',shape=SHAPES[m[1]])
        emit('damage',quantity=quantity_expression(m[2]),damage_type=damage_types[m[3]],timing='immediate',
             target=target,when=outcome('save','failure','any',target=target_ref(selector='area',shape=SHAPES[m[1]])))
    elif (m:=re.fullmatch(r'该(锥状|球状|立方)区域内的每个生物在其每回合结束时必须重复此豁免。',clause)):
        emit_sequence(None,turn('target','end','each'),target_ref(selector='area',shape=SHAPES[m[1]]))
    elif (m:=re.fullmatch(r'该(锥状|球状|立方)区域内的每个生物累计('+NUMBER+r')次豁免失败后，在持续时间内陷入(.+?)状态。',clause)):
        if number(m[2])<1:return None
        emit('condition',condition=m[3],target=target_ref(selector='area',shape=SHAPES[m[1]]),
             when=reference('check_total',result='failure',count=number(m[2]),target=target_ref(selector='area',shape=SHAPES[m[1]])),ends={'kind':'spell_ends'})
    elif re.fullmatch(r'你尝试将施法距离内(?:一个你能看见|一名你可见)的生物变为石头。',clause):
        emit('targets',selection={'kind':'creature','count':1,'within':'spell_range','visible':True})
    elif (m:=re.fullmatch(r'目标必须进行一次('+ABILITY+r')豁免。',clause)):
        emit('saving_throw',ability=m[1],check_role='initial',target=target_ref())
    elif (m:=re.fullmatch(r'如果该目标的身体由血肉组成，则其必须进行一次('+ABILITY+r')豁免。',clause)):
        emit('saving_throw',ability=m[1],check_role='initial',target=target_ref(),when={'kind':'target_property','property':'flesh'})
    elif (m:=re.fullmatch(r'若豁免失败，则目标在法术持续时间内陷入(.+?)状态。',clause)):
        emit('condition',condition=m[1],target=target_ref(),when=outcome('save','failure',selector='same_paragraph'),ends={'kind':'spell_ends'})
    elif re.fullmatch(r'豁免失败者的肉体将变得生硬并因此而受到束缚。',clause):
        emit('condition',condition='束缚',target=target_ref(),when=outcome('save','failure',selector='same_paragraph'),ends={'kind':'spell_ends'})
    elif re.fullmatch(r'豁免成功者将不受该效应的影响。',clause):
        emit('no_effect',target=target_ref(),when=outcome('save','success',selector='same_paragraph'))
    elif (m:=re.fullmatch(r'若豁免成功，则目标的速度直至你的下个回合开始前变为(\d+)。',clause)):
        emit('modifier',field='speed',operation='set',value=int(m[1]),target=target_ref(),
             when=outcome('save','success',selector='same_paragraph'),ends=turn('caster','start','next'))
    elif (m:=re.fullmatch(r'(构装|亡灵)生物进行此豁免时自动成功。',clause)):
        emit('save_override',applies_to=reference('check_ref',scope='initial',selector='previous_check'),result='success',
             when={'kind':'creature_type','subject':'target','value':{'构装':'construct','亡灵':'undead'}[m[1]]})
    elif (m:=re.fullmatch(r'因该法术(?:陷入|受)(.+?)的生物必须在其每回合结束时再进行一次('+ABILITY+r')豁免。',clause)):
        emit_sequence(m[2],{'all':[{'kind':'condition','value':m[1]},turn('target','end','each')]})
    elif (m:=re.fullmatch(r'在累计('+NUMBER+r')次豁免成功后，该法术终止。',clause)):
        if number(m[1])<1:return None
        emit('end_spell',scope='spell',target=target_ref(),when=total('success',m[1]))
    elif (m:=re.fullmatch(r'在累计('+NUMBER+r')次豁免失败后，该生物(?:变为|被转化为)石头并在(?:法术)?持续时间内(?:视为)?陷入石化状态。',clause)):
        if number(m[1])<1:return None
        emit('condition',condition='石化',form='stone',target=target_ref(),when=total('failure',m[1]),ends={'kind':'spell_ends'})
    elif (m:=re.fullmatch(r'成功或失败的检定不需要连续；只要记录各情况的次数直至其中一方累计('+NUMBER+r')次为止。',clause)):
        if number(m[1])<1:return None
        emit('sequence_policy',sequence=reference('sequence_ref'),counting='separate_totals',consecutive=False,
             stop={'success':number(m[1]),'failure':number(m[1])})
    elif (m:=re.fullmatch(r'如果你为该法术维持的专注持续至法术最大持续时间，则该生物陷入的(.+?)状态将持续至被一道([\u3400-\u9fff]+?)(?:[A-Za-z]+)?或类似的魔法移除为止。',clause)):
        emit('duration_override',applies_to=reference('condition',value=m[1]),target=target_ref(),
             operation='replace_end',
             when={'kind':'concentration_duration','duration':'spell_maximum'},
             ends={'kind':'magic_removal','spell_name':m[2],'similar_magic':True})
    else:return None
    return nodes
