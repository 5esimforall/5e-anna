"""Source-bounded spell access and effect statements, without executing game rules.

Grammars describe recurring clauses, not individual spell/feature names. Unhandled
clauses remain evidence-bearing gaps; an explicit exception never becomes a default.
"""
from __future__ import annotations
from copy import deepcopy
import re

ABILITIES = ('力量', '敏捷', '体质', '智力', '感知', '魅力')
CLASSES = ('吟游诗人', '牧师', '德鲁伊', '圣武士', '游侠', '术士', '法师', '魔契师', '奇械师')
SCHOOLS = ('防护', '咒法', '预言', '惑控', '塑能', '幻术', '死灵', '变化')
DIGITS = {'零': 0, '一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
NUMBER = r'(?:\d+|[零一两二三四五六七八九十])'
RELEVANT = re.compile(r'法术|戏法|施法|施展|习得|准备着|长休|短休|充能|仅限|仅当|只有|必须|否则|条件|如果|若')
GRANT = re.compile(r'(?:你[^。；]{0,45}(?:习得|学会|知晓|始终准备|可以[^。；]{0,30}施展)|选择[^。；]{0,30}(?:法术|戏法))')


def number(text):
    return int(text) if text.isdigit() else DIGITS.get(text)


def evidence(path, text, start, end):
    return {'path': path, 'span': [start, end], 'raw': text[start:end]}


def sentences(text, start, end):
    for m in re.finditer(r'[^。！？]+[。！？]?', text[start:end]):
        a, b = start+m.start(), start+m.end()
        while a < b and text[a].isspace(): a += 1
        while b > a and text[b-1].isspace(): b -= 1
        if a < b: yield a, b, re.sub(r'\s+', '', text[a:b])


def quantity_expression(raw):
    """A small quantity language with explicit operands, not an evaluated score."""
    raw = re.sub(r'\s+', '', raw)
    m = re.fullmatch(r'(\d+)d(\d+)(?:\+你的?施法属性调整值)?', raw)
    if m:
        dice = {'kind': 'dice', 'count': int(m[1]), 'sides': int(m[2])}
        return {'kind': 'sum', 'terms': [dice, {'kind': 'modifier', 'ability': 'spellcasting'}]} if '+' in raw else dice
    m = re.fullmatch(r'(?:你的?)?('+'|'.join(ABILITIES)+r')调整值(?:[（(]至少('+NUMBER+r')次?[）)])?', raw)
    if m: return {'kind': 'modifier', 'ability': m[1], **({'minimum': number(m[2])} if m[2] else {})}
    n = number(raw)
    return {'kind': 'constant', 'value': n} if n is not None else None


def parse_prerequisites(entry, text):
    match=re.search(r'(?:先决|先决条件|前提|前置条件)[：:]([^）)\n]+)',text[entry['title_end']:entry['end']])
    if not match:return {'status':'not_stated'}
    a=entry['title_end']+match.start();b=entry['title_end']+match.end()
    raw=match[1].strip();level=re.fullmatch(r'等级\s*(\d+)\+',raw)
    return {'status':'parsed' if level else 'unparsed','expression':{'kind':'level','minimum':int(level[1])} if level else None,
            'evidence':evidence(entry['page_path'],text,a,b)}


def supported_access_clause(clause, mentions):
    """A partial verb match must not silently consume extra conditions or exceptions.

    These whole-clause grammars fence the supported statements. Other observations
    remain partial and are not projected as executable/fully constrained options.
    """
    clause=clause.lstrip('·')
    for m in sorted(mentions,key=lambda m:-len(m['evidence']['raw'])):
        clause=clause.replace(re.sub(r'\s+','',m['evidence']['raw']),'SPELL')
    n=NUMBER
    school=r'(?:'+ '|'.join(SCHOOLS)+r')(?:系|学派)?'
    classes=r'(?:'+ '|'.join(CLASSES)+r')(?:法术列表)?'
    patterns=[
        r'指定一个职业：'+classes+r'(?:、'+classes+r')+。',
        r'你从指定职业法术列表里习得'+n+r'个戏法。',
        r'此外，你从相同列表里选择习得'+n+r'个'+n+r'环法术。',
        r'从'+classes+r'(?:、'+classes+r')+中选择一个列表，你从该法术列表中选择'+n+r'道戏法并习得之。',
        r'你从前面所选的法术列表中再选择'+n+r'道'+n+r'环法术，并始终准备着这道法术。',
        r'你习得SPELL以及'+n+r'个由你选择的'+n+r'环法术。',
        r'选择'+n+r'道'+school+r'(?:(?:或|或者)'+school+r')*(?:学派的|的)'+n+r'环法术。',
        r'该'+n+r'环法术必须是'+school+r'(?:(?:或|或者)'+school+r')*法术。',
        r'你始终准备着你选择的这道法术与SPELL。',
        r'你(?:习得|学会|知晓)SPELL。',
        r'你始终准备着SPELL。',
        r'你可以在不消耗法术位的情况下施展这些法术各一次。',
        r'当你以此法施展某个法术后，你不能再次以此法施展该法术，直至你完成一次长休。',
        r'你也可以消耗合适的法术位来施展这些法术。',
        r'这些法术的施法属性是你(?:由|以)此专长提升的属性。',
        r'你可以无需消耗法术位地施展每道法术各一次，当你完成长休时，你重获以此法施展这些法术的能力。',
        r'你也能够以你拥有的任意合适环阶的法术位施展这些法术。',
        r'依靠这个专长，你可以以最低环阶施展这个法术一次，你必须完成一次长休后才能再次以该方式施展这个法术。',
        r'这些法术的施法属性取决于你指定的职业：'+classes+r'(?:、'+classes+r')*用(?:智力|感知|魅力)(?:；'+classes+r'(?:、'+classes+r')*用(?:智力|感知|魅力))*。',
        r'再从智力、感知、魅力中选择一项属性，你从这个专长中习得的法术使用该属性作为施法属性。',
        r'你可以无需消耗法术位地施展该法术一次，并在完成长休时重获以此法施展该法术的能力。',
        r'你也可以使用你拥有的任意法术位来施展该法术。',
        r'每当你获得一级新等级，你都能将通过本专长习得的一道法术替换为同法术列表中同环阶的另一道法术。',
        r'你可多次选择本专长，但是你每次都必须选择一个不同的法术列表。',
    ]
    return any(re.fullmatch(p,clause) for p in patterns)


def parse_access(entry, text, spell_mentions):
    """Parse the subject's spell-access scope. Mentions carry name-resolution evidence.

    A selection remains a selection, even when its recorded domain has one member.
    Usage alternatives attach to grants, so recovery never leaks into a slot branch.
    """
    path = entry['page_path']; start, end = entry['title_end'], entry['end']
    body = text[start:end]
    if not GRANT.search(re.sub(r'\s+', '', body)): return None
    grants = []; active = []; contexts = {}; ability = None; modifiers = []; gaps = []; handled = []
    prerequisites = parse_prerequisites(entry, text)
    def mark(ev):
        if ev not in handled: handled.append(ev)
    def grant(target, mode, ev):
        g = {'id': 'grant_'+str(len(grants)+1), 'mode': mode, 'target': target, 'branches': [], 'evidence': [ev]}
        grants.append(g); return g
    for a, b, clause in sentences(text, start, end):
        ev = evidence(path, text, a, b)
        mentions = [m for m in spell_mentions if a <= m['evidence']['span'][0] < b]
        # Formatting labels are structure, not a statement. Their explanatory text follows.
        if not re.search(r'你|可以|能够|习得|始终', clause) and re.fullmatch(r'[\u3400-\u9fff\d]+[A-Za-z][A-Za-z\d \-]*[。]?', re.sub(r'\s+', '', text[a:b])):
            continue
        if re.search(r'不能|无法|不可|不会|不得|不允许|未能', clause) and not re.search(r'(?:不能再次|无法再次).*直至.*(?:长休|短休)', clause):
            if RELEVANT.search(clause): gaps.append({'reason': 'negated_or_conditional_clause', 'evidence': ev})
            continue
        found = False
        if re.search(r'提升1|增加1', clause) and re.search(r'智力.*感知.*魅力', clause):
            contexts['ability_increase']={'kind':'choose_one','values':[v for v in ABILITIES if v in clause],'increase':1,'maximum':20 if re.search(r'(?:上限为|至多提升至)20',clause) else None,'evidence':ev}
        classes = [v for v in CLASSES if v in clause]
        if classes and re.search(r'(?:指定|选择)一个(?:职业|列表)|列表中选择一个列表', clause):
            contexts['class_list'] = {'kind': 'choose_one', 'values': classes, 'evidence': ev}
            found = True
        schools = [v for v in SCHOOLS if v in clause]
        level = re.search(r'('+NUMBER+r')环', clause)
        counts = list(re.finditer(r'(?:习得|学会|选择|知晓)(?:习得)?('+NUMBER+r')(?:个|道|项)', clause))
        count = counts[-1] if counts else None
        choice = bool(re.search(r'(?:选择|习得|学会).*(?:戏法|环法术)', clause)) and not re.search(r'替换|改变法术', clause)
        mode = 'prepared' if '始终准备' in clause else 'known' if re.search(r'习得|学会|知晓', clause) else None
        # Only clauses introducing an actual choice create a domain. Do not interpret the
        # spell-count or school explanation that follows as another acquisition.
        if choice and (count or re.search(r'由你选择的|选择一道', clause)):
            constraints = {'levels': [0 if '戏法' in clause and not level else number(level[1]) if level else None]}
            if constraints['levels']==[None]: constraints.pop('levels')
            if schools: constraints['schools'] = schools
            if re.search(r'列表|指定职业|相同列表|前面所选', clause) and contexts.get('class_list'):
                constraints['class_list'] = deepcopy(contexts['class_list'])
            target = {'kind': 'choice', 'count': number(count[1]) if count else 1, 'constraints': constraints}
            active = [grant(target, mode or 'selection', ev)]
            found = True
        elif schools and level and active and re.search(r'必须是|必须为', clause):
            choices = [g for g in active if g['target']['kind']=='choice']
            for g in choices:
                g['target']['constraints'].update(levels=[number(level[1])], schools=schools); g['evidence'].append(ev)
            found = bool(choices)
        if mode and mentions:
            # "you learn X and one spell of your choice" shares later usage clauses.
            new = [grant(deepcopy(m), mode, ev) for m in mentions]
            if found and active and active[0]['evidence']==[ev]: active = new+active
            else:
                selections = [g for g in active if g['mode']=='selection'] if re.search(r'选择的|这道', clause) else []
                for g in selections: g['mode']=mode; g['evidence'].append(ev)
                active = selections+new
            found = True
        elif mode and active and re.search(r'选择的|这道法术|该法术', clause):
            for g in active:
                if g['mode']=='selection': g['mode']=mode; g['evidence'].append(ev); found=True
        if mentions and re.search(r'你可以[^。]{0,35}施展', clause) and not mode:
            active = [grant(deepcopy(m), 'cast', ev) for m in mentions]; found=True
        # A named mention alone is not a grant, nor is prose describing how another caster acts.
        casting = bool(re.search(r'你(?:也)?(?:能够|可以)|依靠这个专长', clause) and '施展' in clause)
        free = bool(re.search(r'(?:不|无需)消耗法术位', clause))
        slots = bool(not free and re.search(r'(?:消耗|使用|以你拥有的).*法术位.*施展', clause))
        once = bool(re.search(r'(?:各)?一次', clause))
        if active and casting and (free or slots or once):
            resource = {'kind': 'spell_slot', 'consumed': not free} if free or slots else {'kind': 'not_stated'}
            branch = {'id': 'branch_'+str(len(active[0]['branches'])+1), 'resource': resource, 'evidence': [ev]}
            if slots: resource.update(count=1, level='appropriate' if re.search(r'合适|对应', clause) else 'available')
            if '最低环阶' in clause: branch['cast_level']={'kind':'spell_level'}
            if once: branch['uses']={'quantity': {'kind': 'constant', 'value': 1}, 'per': 'spell' if re.search(r'各|每道', clause) else 'grant'}
            m = re.search(r'次数等于([^，。]+)', clause)
            if m and (expr := quantity_expression(m[1])): branch['uses']={'quantity':expr,'per':'grant'}
            if '长休' in clause: branch['recovery']={'event':'long_rest','amount':'all'}
            if '短休' in clause: branch['recovery']={'event':'short_or_long_rest' if '长休' in clause else 'short_rest','amount':'all'}
            for g in active: g['branches'].append(deepcopy(branch))
            found=True
        elif active and re.search(r'长休|短休', clause) and re.search(r'再次|重获|恢复', clause):
            branches=[g['branches'][-1] for g in active if g['branches'] and g['branches'][-1]['resource'].get('consumed') is not True]
            for br in branches:
                br['recovery']={'event':'short_or_long_rest' if '短休' in clause and '长休' in clause else 'short_rest' if '短休' in clause else 'long_rest','amount':'all'}
                br['evidence'].append(ev)
            found=bool(branches)
        if '施法属性' in clause:
            abilities=[v for v in ABILITIES if v in clause]
            if re.search(r'提升的属性', clause): ability={'kind':'ability_increase_choice','evidence':ev}; found=True
            elif abilities and re.search(r'选择一项属性', clause): ability={'kind':'choose_one','values':abilities,'evidence':ev}; found=True
            elif len(abilities)==1: ability={'kind':'fixed','value':abilities[0],'evidence':ev}; found=True
            elif classes and '取决于' in clause:
                mapping={}
                for group in re.split(r'[；;：:]', clause):
                    vals=[v for v in ABILITIES if v in group]
                    owners=[v for v in CLASSES if v in group]
                    if len(vals)==1:
                        for owner in owners: mapping[owner]=vals[0]
                if mapping: ability={'kind':'by_class','values':mapping,'evidence':ev};found=True
        if '替换' in clause and '法术' in clause:
            if re.search(r'获得一级新等级', clause) and '同法术列表' in clause and '同环阶' in clause:
                modifiers.append({'kind':'replace_choice','trigger':'gain_level','count':1,'constraints':['same_list','same_level'],'evidence':ev});found=True
        if re.search(r'多次选择本专长', clause) and '不同的法术列表' in clause:
            modifiers.append({'kind':'repeatable','constraint':'different_class_list','evidence':ev});found=True
        if found:
            mark(ev)
            if not supported_access_clause(clause, mentions):gaps.append({'reason':'partially_parsed_clause','evidence':ev})
        elif RELEVANT.search(clause): gaps.append({'reason':'unsupported_clause','evidence':ev})
    for g in grants:
        if ability: g['ability']=deepcopy(ability)
        if not g['branches']:
            g['branches']=[{'id':'branch_1','resource':{'kind':'not_stated'},'evidence':g['evidence']}]
    return {'grants':grants,'contexts':contexts,'prerequisites':prerequisites,'modifiers':modifiers,'unparsed':gaps,'evidence':handled}


def parse_spell(entry, text):
    """Typed recurring effect clauses; all unmatched effect prose remains explicit."""
    start,end=entry['title_end'],entry['end'];path=entry['page_path'];fields=entry.get('fields',{})
    casting={};effects=[];gaps=[]
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
    return {'casting':casting,'effects':effects,'unparsed':gaps}
