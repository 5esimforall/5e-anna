"""Source-bounded spell access and effect statements, without executing game rules.

Grammars describe recurring clauses, not individual spell/feature names. Unhandled
clauses remain evidence-bearing gaps; an explicit exception never becomes a default.
"""
from __future__ import annotations
from copy import deepcopy
import re
from rule_conditions import prerequisite_expression, item_condition, CLASSES
from rule_values import ABILITIES, NUMBER, number, evidence, sentences, quantity_expression
from rule_model import rule_value

SCHOOLS = ('防护', '咒法', '预言', '惑控', '塑能', '幻术', '死灵', '变化')
RELEVANT = re.compile(r'法术|戏法|施法|施展|习得|准备着|长休|短休|充能|仅限|仅当|只有|必须|否则|条件|如果|若')
GRANT = re.compile(r'(?:你[^。；]{0,45}(?:习得|学会|知晓|始终准备|(?:可以|能)[^。；]{0,30}施展)|选择[^。；]{0,30}(?:法术|戏法))')


def parse_prerequisites(entry, text):
    match=re.search(r'(?:先决条件|先决|前提|前置条件)[：:]',text[entry['title_end']:entry['end']])
    if not match:return {'status':'not_stated'}
    a=entry['title_end']+match.start();begin=entry['title_end']+match.end();b=begin;depth=0
    while b<entry['end']:
        char=text[b]
        if char in '\n。':break
        if char in '（(':depth+=1
        elif char in '）)':
            if not depth:break
            depth-=1
        b+=1
    raw=text[begin:b].strip();expression=prerequisite_expression(raw) if not depth else None
    return {'status':'parsed' if expression else 'unparsed','expression':expression,
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
        r'你(?:可以|能)(?:随意)?(?:对自身)?(?:无需消耗法术位地)?施展SPELL(?:，且无需消耗法术位和材料成分)?。',
        r'你可以(?:从中|用它)施展(?:法术)?SPELL。',
        r'你可以(?:从中|用其)施展(?:法术)?SPELL(?:，但(?:只能以自身为目标|目标只能为自身))?。',
        r'你可以用一个动作对自身施展法术SPELL，次数不限。',
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
    prerequisites = parse_prerequisites(entry, text);activation=None
    def mark(ev):
        if ev not in handled: handled.append(ev)
    def grant(target, mode, ev):
        g = {'id': 'grant_'+str(len(grants)+1), 'mode': mode, 'entry_type':'spell', 'target': target, 'branches': [], 'evidence': [ev]}
        grants.append(g); return g
    # A prerequisite is a separate acquisition statement, not a prefix of the first
    # grant sentence. Preserve its full nested expression and exact evidence.
    if prerequisites.get('evidence'):
        pre_start=prerequisites['evidence']['span'][0]
        pre_end=prerequisites['evidence']['span'][1]
        line_end=text.find('\n',pre_end,end)
        prefix=re.sub(r'\s+','',text[start:pre_start])
        suffix=text[pre_end:line_end if line_end>=0 else end].strip()
        if prefix in {'','通用专长（','通用专长('} and suffix in {'','）',')'}:
            start=line_end+1 if line_end>=0 else pre_end
    if entry['type']=='magic_item':
        header=re.match(r'\s*(?:奇物|戒指|魔杖|法杖|权杖)[，,](?:普通|非普通|珍稀|极珍稀|传说)(（需同调）)?[ \t]*\n',text[start:end])
        if header:
            if header[1]:activation={'expression':{'kind':'item_state','state':'attuned','subject':'source_item'},
                                    'evidence':evidence(path,text,start+header.start(1),start+header.end(1))}
            start+=header.end()
    for a, b, clause in sentences(text, start, end):
        ev = evidence(path, text, a, b)
        mentions = [m for m in spell_mentions if a <= m['evidence']['span'][0] < b]
        when=None
        frame=re.fullmatch(r'(?:(?:当|在)?(.+?)时|(?:在)?(.+?)期间)，(你.+)',clause)
        if frame and entry['type']=='magic_item':
            when=item_condition(frame[1] or frame[2])
            if when:clause=frame[3]
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
        if mentions and re.search(r'你(?:可以|能)[^。]{0,35}施展', clause) and not mode:
            active = [grant(deepcopy(m), 'cast', ev) for m in mentions]; found=True
        # A named mention alone is not a grant, nor is prose describing how another caster acts.
        casting = bool(re.search(r'你(?:也)?(?:能够|可以|能)|依靠这个专长', clause) and '施展' in clause)
        free = bool(re.search(r'(?:不|无需)消耗法术位', clause))
        slots = bool(not free and re.search(r'(?:消耗|使用|以你拥有的).*法术位.*施展', clause))
        once = bool(re.search(r'(?:各)?一次', clause))
        if active and casting and (free or slots or once or mentions):
            resource = {'kind': 'spell_slot', 'consumed': not free} if free or slots else {'kind': 'not_stated'}
            branch = {'id': 'branch_'+str(len(active[0]['branches'])+1), 'resource': resource, 'evidence': [ev]}
            if slots: resource.update(count=1, level='appropriate' if re.search(r'合适|对应', clause) else 'available')
            if '最低环阶' in clause: branch['cast_level']={'kind':'spell_level'}
            if when:branch['when']=deepcopy(when)
            if re.search(r'对自身|只能以自身为目标|目标只能为自身',clause):branch['target']={'kind':'self'}
            if '无需消耗法术位和材料成分' in clause:branch['components']={'waived':['M']}
            if re.search(r'随意|次数不限',clause):branch['frequency']='at_will'
            if '用一个动作' in clause:branch['action']='action'
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
            if when and not casting:
                for g in active:
                    if ev in g['evidence']:g['when']=deepcopy(when)
            mark(ev)
            if not supported_access_clause(clause, mentions):gaps.append({'reason':'partially_parsed_clause','evidence':ev})
        elif RELEVANT.search(clause): gaps.append({'reason':'unsupported_clause','evidence':ev})
    for g in grants:
        if ability: g['ability']=deepcopy(ability)
        if not g['branches']:
            g['branches']=[{'id':'branch_1','resource':{'kind':'not_stated'},'evidence':g['evidence']}]
    return rule_value(grants=grants, contexts=contexts, prerequisites=prerequisites, activation=activation, modifiers=modifiers, unparsed=gaps, evidence=handled)
