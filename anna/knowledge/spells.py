"""Evidence-bounded spell clauses, shared across sources and spell identities."""
from copy import deepcopy
import re
from anna.knowledge.values import ABILITIES, NUMBER, number, evidence, sentences, quantity_expression, DAMAGE_TYPES
from anna.knowledge.model import rule_value, finish_effects
from anna.knowledge.conditions import reference, outcome, turn, target_ref
from anna.knowledge.checks import parse_sequence_clause

DAMAGE='(?:'+'|'.join(DAMAGE_TYPES)+')'
ABILITY='(?:'+'|'.join(ABILITIES)+')'


def damage(quantity, damage_type, when, **extra):
    return {'kind':'damage','quantity':quantity_expression(quantity),'damage_type':DAMAGE_TYPES[damage_type],
            'when':when,'timing':'immediate','target':target_ref(),**extra}


def parse_effect_clause(clause, ev):
    """A complete matching clause returns its claims; an unknown suffix rejects it all."""
    sequence=parse_sequence_clause(clause,ev,DAMAGE_TYPES)
    if sequence is not None:return sequence
    nodes=[]
    def emit(kind,**fields):nodes.append({'kind':kind,**fields})
    # Target selection and attack delivery are independent statements. Flavor text is
    # accepted only inside these bounded grammars, never stripped by keyword heuristics.
    target_patterns=[
        r'一道(?:蓝白色的冰冷光束|冰冷的蓝白色光线)朝施法距离内的?(?:一名|一个)生物射去。',
        r'你(?:朝施法距离内一名生物射出一道绿色射线|向施法距离内的一名生物掷出一道光箭)。',
        r'一道恶心的浅绿射线朝施法距离内一个生物射去。',
        r'一支缠绕着碎裂魔力的蓝色能量长矛射向施法距离内的一个生物，并在你与目标之间生成一道持久的闪电弧。',
    ]
    if any(re.fullmatch(p,clause) for p in target_patterns):
        emit('targets',selection={'kind':'creature','count':1,'within':'spell_range'})
    elif re.fullmatch(r'你向施法距离内一个你可见的生物喷出一股毒气。',clause):
        emit('targets',selection={'kind':'creature','count':1,'within':'spell_range','visible':True})
    elif re.fullmatch(r'你对施法距离内(?:一名|一个)生物或物件掷出一把火焰，对目标进行一次远程法术攻击。',clause):
        emit('targets',selection={'kind':'choice','choices':['creature','object'],'count':1,'within':'spell_range'})
        emit('attack_roll',attack_type='ranged_spell',target=target_ref())
    elif re.fullmatch(r'(?:你)?对(?:该)?目标(?:生物)?进行一次(远程|近战)法术攻击(?:检定)?。',clause):
        kind='ranged_spell' if '远程' in clause else 'melee_spell'
        emit('attack_roll',attack_type=kind,target=target_ref())
    else:
        m=re.fullmatch(r'(?:若(?:命中|击中)，(?:则)?|命中(?:时[，,]|则))(?:该)?目标(?:将)?(?:受到|受)(\d+d\d+)(?:点|的)?('+DAMAGE+r')伤害(.*)。',clause)
        if m:
            hit=outcome('attack','hit');nodes.append(damage(m[1],m[2],hit));tail=m[3]
            if tail:
                speed=re.fullmatch(r'(?:，且其速度将被减少(\d+)尺，直到你的下一回合开始为止|，并且在你的下一回合开始前速度减少(\d+)尺)',tail)
                status=re.fullmatch(r'且陷入(.+?)状态，持续至你的下一回合结束',tail)
                blocked=re.fullmatch(r'，并且直至你下一回合的?结束前都无法恢复生命值',tail)
                save=re.fullmatch(r'且必须进行一次('+ABILITY+r')豁免，豁免失败则目标陷入(.+?)，该\2效应持续至你的下一回合结束',tail)
                if speed:emit('modifier',field='speed',operation='subtract',quantity={'kind':'constant','value':int(speed[1] or speed[2]),'unit':'foot'},when=deepcopy(hit),ends=turn('caster','start','next'),target=target_ref())
                elif status:emit('condition',condition=status[1],when=deepcopy(hit),ends=turn('caster','end','next'),target=target_ref())
                elif blocked:emit('restriction',field='healing.allowed',value=False,when=deepcopy(hit),ends=turn('caster','end','next'),target=target_ref())
                elif save:
                    emit('saving_throw',ability=save[1],check_role='initial',when=deepcopy(hit),target=target_ref())
                    emit('condition',condition=save[2],when=outcome('save','failure',selector='same_clause',ability=save[1]),ends=turn('caster','end','next'),target=target_ref())
                else:return None
        else:
            return parse_save_or_duration(clause,ev)
    return [{**n,'evidence':ev} for n in nodes]


def parse_save_or_duration(clause,ev):
    nodes=[]
    def emit(kind,**fields):nodes.append({'kind':kind,**fields})
    scoped=parse_scoped_checks(clause,ev)
    if scoped is not None:return scoped
    m=re.fullmatch(r'处于(\d+)尺(锥状|球状|立方)区域内的生物必须进行一次('+ABILITY+r')豁免。',clause)
    if m:
        emit('targets',selection={'kind':'area','shape':{'锥状':'cone','球状':'sphere','立方':'cube'}[m[2]],'size':int(m[1]),'unit':'foot','members':'creature'})
        emit('saving_throw',ability=m[3],target=target_ref(selector='same_clause'),check_role='initial')
    elif (m:=re.fullmatch(r'施法距离内你可见的一名生物进行一次('+ABILITY+r')豁免。',clause)):
        emit('targets',selection={'kind':'creature','count':1,'visible':True,'within':'spell_range'})
        emit('saving_throw',ability=m[1],target=target_ref(selector='same_clause'),check_role='initial')
    elif (m:=re.fullmatch(r'豁免失败者(?:将)?受到(\d+d\d+)点?('+DAMAGE+r')伤害，豁免成功(?:者受到一半伤害|则伤害减半)。',clause)):
        nodes.append(damage(m[1],m[2],outcome('save','failure')))
        emit('damage',quantity=reference('quantity_ref',selector='base_damage',factor={'numerator':1,'denominator':2}),
             damage_type=DAMAGE_TYPES[m[2]],when=outcome('save','success'),timing='immediate',target=target_ref())
    elif (m:=re.fullmatch(r'豁免失败，目标在持续时间内陷入(.+?)和(.+?)状态。',clause)):
        for status in m.groups():emit('condition',condition=status,when=outcome('save','failure'),ends={'kind':'spell_ends'},target=target_ref())
    elif re.fullmatch(r'在此期间，目标会不受控制地狂笑（只要它能够发笑），且无法结束自身的倒地状态。',clause):
        emit('behavior',behavior='laugh',control='involuntary',when={'all':[outcome('save','failure'),{'kind':'spell_active'},{'kind':'can_laugh','subject':'target'}]},ends={'kind':'spell_ends'},target=target_ref())
        emit('restriction',field='prone.self_end',value=False,when={'all':[{'kind':'spell_active'},{'kind':'condition','value':'倒地'}]},ends={'kind':'spell_ends'},target=target_ref())
    elif (m:=re.fullmatch(r'目标在其每回合结束或受到伤害时，可以再进行一次('+ABILITY+r')豁免。',clause)):
        emit('saving_throw',ability=m[1],check_role='repeat',repeat_of=reference('check_ref',ability=m[1],scope='initial'),target=target_ref(),
             when={'all':[outcome('save','failure'),{'kind':'spell_active'},{'any':[turn('target','end','each'),{'kind':'damage_received','subject':'target'}]}]})
    elif re.fullmatch(r'如果豁免是因受到伤害所致，则该次豁免具有优势。',clause):
        emit('check_modifier',check=reference('check_ref',scope='repeat'),modifier='advantage',when={'kind':'damage_received','subject':'target'})
    elif re.fullmatch(r'豁免成功时，法术终止。',clause):
        emit('end_spell',scope='spell',when=outcome('save','success','repeat'))
    elif (m:=re.fullmatch(r'无论首次攻击是否命中，在你接下来的每个回合中，你都可以用附赠动作直接对目标造成(\d+d\d+)点?('+DAMAGE+r')伤害。',clause)):
        nodes.append(damage(m[1],m[2],{'all':[{'kind':'spell_active'},turn('caster','during','subsequent')]},timing='ongoing',
            use={'action':'bonus_action','frequency':'once_per_turn'},delivery='automatic',initial_attack_result='irrelevant'))
    elif (m:=re.fullmatch(r'在法术持续时间内，你在每个自己的回合中可以用一个动作直接对目标造成(\d+d\d+)点?('+DAMAGE+r')伤害。',clause)):
        nodes.append(damage(m[1],m[2],{'all':[{'kind':'spell_active'},turn('caster','during','each')]},timing='ongoing',
            use={'action':'action','frequency':'once_per_turn'},delivery='automatic',initial_attack_result='not_stated'))
    elif re.fullmatch(r'如果你执行动作做其他任何事情，法术将随即终止。',clause):
        emit('end_spell',scope='spell',when={'kind':'action_spent','subject':'caster','purpose':'other'})
    elif re.fullmatch(r'此外，如果目标与你之间的距离超出了法术的施法距离，或者目标相对你而言处于全身掩护状态，法术将提前终止。',clause) or re.fullmatch(r'此外，如果目标离开法术的施法距离或相对你处于全身掩护状态，法术也将随即终止。',clause):
        emit('end_spell',scope='spell',when={'any':[{'kind':'outside_spell_range','subject':'target'},{'kind':'cover','subject':'target','value':'total'}]})
    elif (m:=re.fullmatch(r'(?:你)?使用的法术位每比('+NUMBER+r')环高一环，(?:此法术的|法术的)?(初始)?伤害就(?:会)?(?:增加|提升|提高)(\d+d\d+)点?。',clause)):
        emit('upcast',threshold=number(m[1]),per_slot_level=1,effect={'kind':'damage','quantity':quantity_expression(m[3])},
             applies_to=reference('damage_ref',selector='initial_damage' if m[2] else 'base_damage'))
    elif (m:=re.fullmatch(r'(?:使用二环或更高法术位施展该法术时，)?你使用的法术位每比('+NUMBER+r')环高一环，(?:法术的)?(初始)?伤害就增加(\d+d\d+)。',clause)):
        emit('upcast',threshold=number(m[1]),per_slot_level=1,effect={'kind':'damage','quantity':quantity_expression(m[3])},
             applies_to=reference('damage_ref',selector='initial_damage' if m[2] else 'base_damage'))
    elif (m:=re.fullmatch(r'你到达(\d+)级时，该法术的伤害增加(\d+d\d+)（变为(\d+d\d+)）。',clause)):
        emit('level_scale',applies_to=reference('damage_ref',selector='base_damage'),levels=[{'level':int(m[1]),'quantity':quantity_expression(m[3])}])
    elif (m:=re.fullmatch(r'(\d+)级时再加(\d+d\d+)（变为(\d+d\d+)），(\d+)级时再加(\d+d\d+)（变为(\d+d\d+)）。',clause)):
        emit('level_scale',applies_to=reference('damage_ref',selector='base_damage'),levels=[{'level':int(m[1]),'quantity':quantity_expression(m[3])},{'level':int(m[4]),'quantity':quantity_expression(m[6])}])
    elif (m:=re.fullmatch(r'到达特定等级后，此戏法的伤害将增加(\d+d\d+)：5级（(\d+d\d+)）、11级（(\d+d\d+)）、17级（(\d+d\d+)）。',clause)):
        emit('level_scale',applies_to=reference('damage_ref',selector='base_damage'),
             levels=[{'level':n,'quantity':quantity_expression(q)} for n,q in zip((5,11,17),m.groups()[1:])])
    elif re.fullmatch(r'(?:未被着装或携带的可燃物件被该法术命中时将(?:被点燃|开始燃烧)|在此锥状区域内的未被着装或携带的易燃物件会开始燃烧|该法术的火焰会点燃范围内未被着装或携带的可燃物)。',clause):
        predicates=[{'kind':'target_property','property':'object'},{'kind':'target_property','property':'flammable'},
            {'not':{'any':[{'kind':'target_property','property':'worn'},{'kind':'target_property','property':'carried'}]}}]
        if '命中' in clause:predicates.insert(0,outcome('attack','hit'))
        else:predicates.insert(0,{'kind':'within_area','area':target_ref(selector='area',**({'shape':'cone'} if '锥状' in clause else {}))})
        emit('ignite',when={'all':predicates})
    elif (m:=re.fullmatch(r'在你下个回合结束前，对目标发动的下一次攻击检定具有优势。',clause)):
        emit('modifier',field='attack_roll',operation='advantage',applies='next_attack_against_target',when=outcome('attack','hit'),
             ends=turn('caster','end','next'),uses=1,target=target_ref())
    else:return None
    return [{**n,'evidence':ev} for n in nodes]


def parse_scoped_checks(clause,ev):
    """Local outcomes, anaphoric repeats and ordinal checks retain their own scope."""
    nodes=[]
    def emit(kind,**fields):nodes.append({'kind':kind,**fields})
    m=re.fullmatch(r'指定施法距离内一个你能看见的(类人生物|生物)。',clause)
    if m:emit('targets',selection={'kind':'humanoid' if m[1]=='类人生物' else 'creature','count':1,'visible':True,'within':'spell_range'})
    elif (m:=re.fullmatch(r'该目标必须进行一次('+ABILITY+r')豁免，豁免失败则其在法术持续时间内陷入(.+?)(?:状态)?。',clause)):
        emit('saving_throw',ability=m[1],check_role='initial',target=target_ref())
        emit('condition',condition=m[2],when=outcome('save','failure',selector='same_clause',ability=m[1]),ends={'kind':'spell_ends'},target=target_ref())
    elif (m:=re.fullmatch(r'目标在其每回合结束时可以(?:再进行一次('+ABILITY+r')豁免|重新进行这次豁免)，豁免成功则终止其身上该法术的效应。',clause)):
        repeat=reference('check_ref',scope='initial',**({'ability':m[1]} if m[1] else {'selector':'previous_check'}))
        emit('saving_throw',ability=m[1],**({'ability_source':'repeat_of'} if not m[1] else {}),check_role='repeat',
             repeat_of=repeat,target=target_ref(),when={'all':[outcome('save','failure',**({'ability':m[1]} if m[1] else {'selector':'previous_check'})),
                 {'kind':'spell_active'},turn('target','end','each')]})
        emit('end_spell',scope='target',target=target_ref(),when=outcome('save','success','repeat',selector='same_clause'))
    elif (m:=re.fullmatch(r'以施法距离内一点为源点，(\d+)尺半径球状区域内每个你选择的生物都必须成功通过一次('+ABILITY+r')豁免否则直到其下个回合结束陷入(.+?)状态。',clause)):
        emit('targets',selection={'kind':'area','shape':'sphere','size':int(m[1]),'measure':'radius','unit':'foot','members':'creature','chosen_by':'caster','within':'spell_range'})
        emit('saving_throw',ability=m[2],check_role='initial',occurrence=1,target=target_ref())
        emit('condition',condition=m[3],when=outcome('save','failure',selector='same_clause'),ends=turn('target','end','next'),target=target_ref())
    elif re.fullmatch(r'在其下个回合结束时，它必须重复此豁免。',clause):
        emit('saving_throw',ability=None,ability_source='repeat_of',check_role='repeat',occurrence=2,
             repeat_of=reference('check_ref',scope='initial',selector='previous_check'),target=target_ref(),
             when={'all':[outcome('save','failure',selector='previous_check'),turn('target','end','next')]})
    elif (m:=re.fullmatch(r'如果目标第('+NUMBER+r')次豁免失败，目标在持续时间内陷入(.+?)状态。',clause)):
        occurrence=number(m[1])
        if occurrence<1:return None
        emit('condition',condition=m[2],when=outcome('save','failure','initial' if occurrence==1 else 'repeat',occurrence=occurrence),ends={'kind':'spell_ends'},target=target_ref())
    elif (m:=re.fullmatch(r'若目标受到伤害或其(\d+)尺内有人用一个动作将其摇醒，目标身上的法术结束。',clause)):
        emit('end_spell',scope='target',target=target_ref(),when={'any':[{'kind':'damage_received','subject':'target'},
            {'kind':'wake_action','subject':'other','distance':{'maximum':int(m[1]),'unit':'foot'},'action':'action'}]})
    elif (m:=re.fullmatch(r'不需要睡眠的生物（例如.+?）或免疫(.+?)状态的生物自动成功通过对抗此法术的豁免。',clause)):
        emit('save_override',applies_to={'kind':'spell_saves'},result='success',when={'any':[
            {'not':{'kind':'requires_sleep','subject':'target'}},{'kind':'condition_immunity','subject':'target','condition':m[1]}]})
    elif (m:=re.fullmatch(r'(?:使用三环或更高法术位施展该法术时，)?你使用的法术位每比二环高一环，(?:你)?就可以额外指定一个类人(?:生物|作为目标)。',clause)):
        emit('upcast',threshold=2,per_slot_level=1,effect={'kind':'additional_targets','count':1,'target_kind':'humanoid'})
    elif (m:=re.fullmatch(r'指定目标时，每个目标生物必须身处其他目标(\d+)尺范围内。',clause)):
        emit('target_constraint',target=target_ref(),constraint={'kind':'pairwise_distance','maximum':int(m[1]),'unit':'foot'})
    elif (m:=re.fullmatch(r'(?:该次|这次)豁免具有(优势|劣势)。',clause)):
        emit('check_modifier',check=reference('check_ref',scope='any',selector='previous_check'),modifier='advantage' if m[1]=='优势' else 'disadvantage')
    else:return None
    return [{**n,'evidence':ev} for n in nodes]


def parse_spell(entry, text):
    """Typed recurring effect clauses; all unmatched effect prose remains explicit."""
    start,end=entry['title_end'],entry['end'];path=entry['page_path'];fields=entry.get('fields',{})
    casting={};effects=[];gaps=[];descriptions=[]
    # Evidence uses original field lines, not normalized field values without provenance.
    last=start
    for m in re.finditer(r'(施法时间|施法距离|法术成分|持续时间)[：:]([^\n]+)', text[start:end]):
        key={'施法时间':'action','施法距离':'range','法术成分':'components','持续时间':'duration'}[m[1]]
        ev=evidence(path,text,start+m.start(),start+m.end());raw=re.sub(r'\s+','',m[2]);last=max(last,start+m.end())
        value={'raw':m[2].strip()}
        if key=='action':
            value.update(kind='bonus_action' if '附赠' in raw else 'reaction' if '反应' in raw else 'action' if '动作' in raw else 'timed',ritual='仪式' in raw)
        elif key=='range':
            measure=re.fullmatch(r'(\d+)(尺|里)',raw)
            value.update(kind='self' if raw=='自身' else 'touch' if raw=='触碰' else 'distance' if measure else 'unparsed')
            if measure: value.update(quantity=int(measure[1]),unit='foot' if measure[2]=='尺' else 'mile')
        elif key=='components': value.update(required=[v for v in ('V','S','M') if v in raw])
        elif key=='duration':
            value.update(concentration=raw.startswith('专注'),kind='instantaneous' if raw=='立即' else 'timed')
            m2=re.search(r'(\d+)(分钟|小时|轮|日)',raw)
            if m2:value.update(quantity=int(m2[1]),unit={'分钟':'minute','小时':'hour','轮':'round','日':'day'}[m2[2]],maximum='至多' in raw)
            elif raw!='立即':value['kind']='unparsed'
        casting[key]={**value,'evidence':ev}
    for a,b,clause in sentences(text,last,end):
        ev=evidence(path,text,a,b)
        if not re.search(r'[\u3400-\u9fff]',clause):continue
        if clause in {'升环施法。','戏法强化。'}:continue
        if re.fullmatch(r'你向前喷射出一片火焰。|你双手拇指相对，其他手指张开，一道火焰随即从手指间喷出。',clause):
            descriptions.append(ev);continue
        parsed=parse_effect_clause(clause,ev)
        if parsed is not None:effects.extend(parsed);continue
        # Teleportation states the destination predicates explicitly.
        m=re.fullmatch(r'(?:你短暂地被[^。]+[，,])?传送到至多(\d+)尺内一个你能看见且未(?:被占据的位置|占据空间)[。]?',clause)
        if m:
            effects.append({'kind':'teleport','subject':'self','destination':{'distance':{'maximum':int(m[1]),'unit':'foot'},'visible':True,'unoccupied':True},'evidence':ev});continue
        m=re.fullmatch(r'你触碰的一名生物恢复等同于(.+?)点生命值[。]?',clause)
        if m and (expr:=quantity_expression(m[1])):
            effects.append({'kind':'healing','target':{'kind':'creature','count':1,'contact':True},'quantity':expr,'evidence':ev});continue
        m=re.fullmatch(r'(?:升环施法[。])?使用的法术位每比('+NUMBER+r')环高一环[，,](?:此法术的治疗量就增加|法术的治疗量就增加)(\d+d\d+)点?[。]?',clause)
        if m:
            effects.append({'kind':'upcast','threshold':number(m[1]),'per_slot_level':1,'effect':{'kind':'healing','quantity':quantity_expression(m[2])},'evidence':ev});continue
        if clause=='升环施法。':continue
        # Conditions are bound to a specific outcome, not flattened into unconditional effects.
        m=re.fullmatch(r'选择法术距离内你可见的一名(类人生物|生物)[，,]迫使该生物进行一次('+ '|'.join(ABILITIES)+r')豁免[，,]如果你或者你的同伴正在与它战斗[，,]它进行的这次豁免将具有优势[。]?',clause)
        if m:
            effects.append({'kind':'saving_throw','target':{'kind':'humanoid' if m[1]=='类人生物' else 'creature','count':1,'visible':True,'within':'spell_range'},'ability':m[2],
                'modifiers':[{'when':{'kind':'combat_with','actors':['caster','caster_allies']},'effect':'advantage'}],'evidence':ev});continue
        m=re.fullmatch(r'如果它豁免失败[，,]它陷入(.+?)状态直到法术结束[，,]或者直到你或你的同伴对它造成伤害[。]?',clause)
        if m:
            effects.append({'kind':'condition','condition':m[1],'when':{'kind':'save_result','result':'failure'},
                'ends':{'any':[{'kind':'spell_ends'},{'kind':'damage_from','actors':['caster','caster_allies']}]},'evidence':ev});continue
        m=re.fullmatch(r'被(.+?)的生物将对你友好[。]?',clause)
        if m:
            effects.append({'kind':'attitude','value':'friendly','toward':'caster','when':{'kind':'condition','value':m[1]},'evidence':ev});continue
        m=re.fullmatch(r'在法术结束时[，,]生物会知道它曾被你(.+?)[。]?',clause)
        if m:
            effects.append({'kind':'awareness','when':{'kind':'spell_ends'},'subject':'target','knows':{'kind':'condition_from','value':m[1],'actor':'caster'},'evidence':ev});continue
        m=re.fullmatch(r'使用的法术位每比('+NUMBER+r')环高一环[，,]你就可以额外选择一个生物作为目标[。]?',clause)
        if m:
            effects.append({'kind':'upcast','threshold':number(m[1]),'per_slot_level':1,'effect':{'kind':'additional_targets','count':1},'evidence':ev});continue
        gaps.append({'reason':'unsupported_effect','evidence':ev})
    for field,reading in casting.items():
        if reading.get('kind')=='unparsed':gaps.append({'reason':'unsupported_casting_field','field':field,'evidence':reading['evidence']})
    gaps.extend(finish_effects(effects, gaps, path, text, last, end))
    return rule_value(parameters=casting, effects=effects, descriptions=descriptions, unparsed=gaps)
