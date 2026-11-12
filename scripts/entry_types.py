"""Entry types: the evidence that gives an extracted entry its type, and how strong that evidence is.

Typing reads a page in two passes. `classify` reads one candidate: its name, the text beneath it and
where its page sits. `refine_page_types` then reads the page as a whole, because a heading cannot know
what its page is until the page's own primary entry has a type.

Every way of recognizing a type is a named rule (`classification_rule`) whose evidence is declared once,
in RULE_EVIDENCE; the status anna shows for the type (原文明示, 上下文推得, 未分类段落) follows from that
declaration. Recognizers run in the order they are declared and the first that answers decides the type.
Printed evidence that later recognizers still find is kept, so an entry whose source prints a type other
than the one it received carries the disagreement (`type_conflicts`) instead of hiding it.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
import re
from typing import Callable

from html_extract import CLASSES, GENERIC, SPELL, STATS, CLASS_PATH, Candidate, class_structure, compact, key, split_name

# Types that define a rule of play rather than a game object; core-rule pages split these into subdefinitions.
RULE_TYPES = {'rule','action','condition','hazard','subsystem'}

PRINTED = '原文明示'
INFERRED = '上下文推得'
UNTYPED = '未分类段落'

# The strength of each rule's evidence. A rule is printed evidence when the source itself shows the type
# where the entry is printed: an index row, a stat block or field layout, a descriptor line under the title,
# a label beside the name. It is inferred when the type comes from where the text sits: a folder, a TOC
# section, a catalog heading, a parent entry. document_structure is the decision that a heading is
# structure, not an entry.
RULE_EVIDENCE = {
    # The index or the page layout names the type.
    'index_or_statblock':PRINTED, 'stat_signature':PRINTED, 'spell_signature':PRINTED,
    'magic_item_signature':PRINTED, 'poison_signature':PRINTED,
    'facility_signature':PRINTED, 'facility_list_signature':PRINTED, 'vehicle_signature':PRINTED,
    'hazard_signature':PRINTED, 'trap_signature':PRINTED,
    'equipment_table':PRINTED, 'vehicle_table':PRINTED, 'animal_offer_table':PRINTED,
    'spell_fields':PRINTED, 'stat_fields':PRINTED, 'background_fields':PRINTED, 'species_fields':PRINTED,
    'item_descriptor':PRINTED, 'feat_descriptor':PRINTED, 'poison_descriptor':PRINTED,
    'class_structure':PRINTED, 'level_heading':PRINTED, 'level_statement':PRINTED, 'name_label':PRINTED,
    # The place the text sits implies the type.
    'context_heading':INFERRED, 'context_fields':INFERRED, 'topic_context':INFERRED,
    'property_definition':INFERRED, 'named_table_structure':INFERRED, 'rule_subdefinition':INFERRED,
    'level_inline_heading':INFERRED, 'feature_inline_heading':INFERRED, 'choice_pool_heading':INFERRED,
    'subclass_structure':INFERRED, 'parent_class_context':INFERRED,
    'document_structure':UNTYPED,
}


def evidence_status(rule,kind):
    """The status an entry shows for its type. Every rule must declare its evidence, and only the
    document_structure decision leaves an entry untyped."""
    if rule not in RULE_EVIDENCE:raise ValueError(f'classification rule without declared evidence: {rule}')
    status=RULE_EVIDENCE[rule]
    if (status==UNTYPED)!=(kind=='section'):raise ValueError(f'{rule} cannot type an entry as {kind}')
    return status


def type_conflicts(kind,printed_types):
    """Printed evidence for a type other than the one the entry received."""
    return [{'type':t,'rule':r} for t,r in printed_types if t!=kind]


# ---------------------------------------------------------------------------------------------------
# Page context a candidate cannot see on its own.

CLASS_FEATURE_START=re.compile(r'职业特性|Class Features|可选职业|祈唤|战技|超魔法|灌注',re.I)
CHOICE_POOL=re.compile(r'战斗风格|祈唤|战技|超魔法|灌注')


def mark_class_regions(cs,text):
    """Mark the candidates after a page's class-feature heading, and those inside an option pool
    (战斗风格, 祈唤, 战技, 超魔法, 灌注), for the class-feature recognizers."""
    feature_starts=[c.start for c in cs if CLASS_FEATURE_START.search(c.name)]
    if not feature_starts:
        optional=re.search(r'可选职业特性',text[:1000])
        if optional:feature_starts=[optional.start()]
    for c in cs:
        c.fields['class_feature_region']=bool(feature_starts and c.start>min(feature_starts))
        ancestors=[];pi=c.parent
        while pi is not None:
            ancestors.append(cs[pi].name);pi=cs[pi].parent
        c.fields['choice_pool']=next((n for n in ancestors if CHOICE_POOL.search(n)),None)


# ---------------------------------------------------------------------------------------------------
# Name and layout evidence shared by both passes.

LEVEL_HEADING=re.compile(r'^\s*(\d{1,2})\s*级\s*[:：]')
# Some definitions print their own type beside their name (目盲 Blinded【状态】, 燃烧 Burning【危害】). A
# heading that is only the label, such as 【状态】Condition, defines the label itself and keeps its context type.
PRINTED_TYPE_LABELS={'状态':'condition','动作':'action','危害':'hazard'}
PRINTED_TYPE_LABEL=re.compile(r'[（(【]('+'|'.join(PRINTED_TYPE_LABELS)+r')[）)】]')
# DMG 2024 hazards open with their severity and level band, as traps open with their kind.
HAZARD_DESCRIPTOR=re.compile(r'(?:致命|妨碍)危害\s*[（(]\s*等级')
# A body that applies a defined rule: a DC, a die roll or die table, advantage or disadvantage on a d20 test,
# a condition, exhaustion, difficult terrain or obscurement. Words such as 危险 or 效果 name no rule.
RULES_MECHANICS=re.compile(r'DC\s*\d+|(?<![A-Za-z\d])\d*[dD]\d+(?![A-Za-z])|(?:攻击|豁免|检定)[^。]{0,12}(?:优势|劣势)|(?:优势|劣势)[^。]{0,12}(?:攻击|豁免|检定)'
                           r'|力竭|陷入[^。]{1,8}状态|困难地形|(?:轻度|重度)遮蔽')
CONDITION_RULE_HEADING=re.compile(r'^(?:持续时间|豁免|概述|简介|来源|结束)(?:\s|[A-Za-z]|$)')


def level_heading(name):
    """The level a class or subclass feature heading states, or None for prose and range headings."""
    m=LEVEL_HEADING.match(name)
    return int(m[1]) if m else None


def document_structure_name(name):
    """Names that label a page or a catalog rather than the entry the page is about."""
    return bool(GENERIC.fullmatch(name)) or bool(re.fullmatch(r'(?:专长|法术|魔法物品|装备|背景|种族|物种)(?:描述|详述|列表|总览)(?:\s*[A-Za-z].*)?',name))


def printed_type(name):
    """The type a definition prints beside its own name, or '' when the heading is the label itself."""
    m=PRINTED_TYPE_LABEL.search(name)
    if not m:return ''
    zh,_=split_name(compact(name[:m.start()]+' '+name[m.end():]))
    return PRINTED_TYPE_LABELS[m[1]] if zh and not zh.startswith(m[1]) else ''


# ---------------------------------------------------------------------------------------------------
# First pass: one candidate.

class Probe:
    """What one candidate offers its recognizers: its name, its text window and where its page sits."""

    def __init__(self,c:Candidate,own,full,context,primary):
        self.c=c;self.primary=primary;self.context=context
        self.path=context.split(' | ')[-1]
        self.name=compact(c.name)
        self.class_context=bool(CLASS_PATH.search(self.path))
        # A class page's primary entry reads the whole page, where its hit die, core traits and level
        # features sit under child headings. Any other entry reads its own text, not a nested stat block.
        self.text=full if primary and (self.class_context or class_structure(full,c.name)) else own
        self.intro=self.text[:1800]

    @cached_property
    def after_title(self):return self.text[max(0,self.c.title_end-self.c.start):]

    @cached_property
    def descriptor(self):return compact(self.after_title)[:220]

    @cached_property
    def names(self):return split_name(self.name)

    @property
    def zh(self):return self.names[0]

    @property
    def en(self):return self.names[1]

    @cached_property
    def short_name(self):return not re.search(r'[。！？；]',self.name) and len(self.name)<110

    @property
    def titled(self):
        """A short name that is the page's own or carries an English name: a definition, not a sentence."""
        return self.short_name and (self.primary or bool(self.en))


@dataclass(frozen=True)
class Recognizer:
    """One way a candidate shows its type. `test` answers a type, a (type, rule) pair, a list of pairs whose
    first decides, or None."""
    rule: str
    test: Callable[[Probe], str | tuple[str, str] | None]

    @property
    def printed(self):return RULE_EVIDENCE[self.rule]==PRINTED


RECOGNIZERS: list[Recognizer] = []


def recognizer(rule):
    """Declare the next recognizer; declaration order is precedence."""
    if rule not in RULE_EVIDENCE:raise ValueError(f'classification rule without declared evidence: {rule}')
    def register(test):
        RECOGNIZERS.append(Recognizer(rule,test));return test
    return register


@recognizer('index_or_statblock')
def indexed_or_signed(p):
    """An index row named the type, or the page prints a stat block, item line, spell line or poison label.
    The first kind decides; further kinds merged into the same candidate are printed evidence too."""
    if p.c.kinds:
        first=p.c.method if p.c.method.endswith('_signature') else 'index_or_statblock'
        return [(k,first if i==0 else 'index_or_statblock') for i,k in enumerate(p.c.kinds)]


@recognizer('document_structure')
def page_introduction(p):
    if p.c.method=='page_intro':return 'section'


@recognizer('named_table_structure')
def table_caption(p):
    if p.c.method=='table_caption':return 'lore'


@recognizer('feature_inline_heading')
def class_inline_heading(p):
    """Inline headings that the class layout found in class prose (level_inline_heading, feature_inline_heading)."""
    if p.class_context and p.c.method in {'level_inline_heading','feature_inline_heading'}:return 'class_feature',p.c.method


@recognizer('document_structure')
def option_catalog(p):
    if (p.primary or '选项' in p.name) and re.match(r'^(?:魔能祈唤(?:选项)?|禁令恩惠|子职(?:业)?选项|反派选项|超魔(?:法)?选项|灌注选项)(?:\s*[A-Za-z].*)?$',p.name):return 'section'


@recognizer('document_structure')
def structure_name(p):
    if document_structure_name(p.name):return 'section'


@recognizer('class_structure')
def class_layout(p):
    if p.primary and class_structure(p.text,p.name):return 'class'


@recognizer('context_heading')
def poison_procedure(p):
    if re.match(r'^(?:毒药的(?:制作|提取)|采购毒药|制作毒药|提取毒药)',p.name):return 'rule'


@recognizer('context_heading')
def rules_section_name(p):
    if re.search(r'^(?:子职|子职业|特色设施详述|疾病范例|毒药范例|毒药的(?:制作|提取)|毒药|疾病|专长与背景)(?:\s*[A-Za-z].*)?$',p.name):return 'rule'


STATBLOCK_PART=re.compile(r'(?:动作|附赠动作|反应|特质|传奇动作|巢穴动作)\s*(?:Actions?|Bonus Actions?|Reactions?|Traits?|Legendary Actions?|Lair Actions?)?',re.I)
COMBAT_RULES_PATH=re.compile(r'术语|进行游戏|/战斗/')


@recognizer('context_heading')
def combat_rule_heading(p):
    """动作, 反应 and the like define rules in the combat and glossary chapters."""
    if STATBLOCK_PART.fullmatch(p.name) and COMBAT_RULES_PATH.search(p.path):return 'rule'


@recognizer('document_structure')
def statblock_part_heading(p):
    """Elsewhere the same headings divide a stat block."""
    if STATBLOCK_PART.fullmatch(p.name) and not COMBAT_RULES_PATH.search(p.path):return 'section'


# Catalog headings and field descriptions are not entities: the signatures below require a descriptor
# printed under the title, not merely a keyword inherited from the page or TOC.
@recognizer('facility_signature')
def facility_descriptor(p):
    if re.match(r'^\s*\d+\s*级据点设施',p.after_title):return 'facility'


@recognizer('vehicle_signature')
def vehicle_layout(p):
    vehicle_context=bool(re.search(r'载具|船舶|战争机器|魔法船',p.context))
    vehicle_meta=re.search(r'(?m)^\s*(?:大型|巨型|超巨型)\s*(?:载具|车辆|物件|物体|运载工具)',p.after_title[:250])
    ship_summary=re.search(r'载货量\s*Cargo\s*[:：]',p.intro) and re.search(r'船员\s*Crew\s*[:：]',p.intro)
    vehicle_capacity=re.search(r'(?:生物容量|生物载量|乘员数量|乘员容量)\s*(?:[A-Za-z ]+)?[:：]',p.intro[:900])
    if (ship_summary or vehicle_context and (vehicle_meta or vehicle_capacity)) and not re.match(r'^(?:操纵|移动|动作|武器|船壳|容量|乘员数量|载货量)',p.name):
        return 'vehicle'


@recognizer('hazard_signature')
def hazard_descriptor(p):
    if HAZARD_DESCRIPTOR.match(p.after_title.lstrip()):return 'hazard'


@recognizer('trap_signature')
def trap_descriptor(p):
    if re.search(r'\s(?:机械|魔法)陷阱$',p.name) or re.match(r'(?:机械|魔法|简易|复合|致命|妨碍)陷阱(?:\s|[（(]|$)',p.after_title.lstrip()):return 'trap'


@recognizer('spell_fields')
def spell_layout(p):
    if SPELL.search(p.intro):return 'spell'


@recognizer('stat_fields')
def stat_layout(p):
    if STATS.search(p.intro) and re.search(r'挑战|\bCR\b|力量.*敏捷',p.intro,re.S):return 'creature'


@recognizer('item_descriptor')
def item_descriptor(p):
    if re.match(r'^(?:武器|护甲|奇物|戒指|魔杖|法杖|魔棒|药水|卷轴|弹药|盾牌).{0,60}(?:普通|珍稀|稀有|传说|神器)',p.descriptor):return 'magic_item'


@recognizer('feat_descriptor')
def feat_descriptor(p):
    if re.match(r'^(?:起源|通用|战斗风格|传奇恩惠|种族|血族)专长',p.descriptor):return 'feat'


@recognizer('level_heading')
def class_level_heading(p):
    if re.match(r'^\s*\d+\s*级\s*[:：]',p.name) and p.class_context:return 'class_feature'


@recognizer('background_fields')
def background_layout(p):
    if re.search(r'技能熟练',p.intro) and re.search(r'工具熟练|语言',p.intro) and re.search(r'装备',p.intro) and (p.primary or re.search(r'背景',p.context)):return 'background'


@recognizer('species_fields')
def species_layout(p):
    if re.search(r'生物类型|属性值加成',p.intro) and re.search(r'体型',p.intro) and re.search(r'速度',p.intro) and (p.primary or '种族' in p.context or '物种' in p.context):return 'species'


@recognizer('poison_descriptor')
def poison_descriptor(p):
    if re.match(r'^(?:吸入|服用|损伤|接触)型毒药',p.descriptor):return 'poison'


@recognizer('name_label')
def name_label(p):
    return printed_type(p.name) or None


@recognizer('property_definition')
def equipment_property(p):
    if p.c.method in {'heading','colored_heading','word_heading'} and not p.primary and re.search(r'/装备/(?:词条|精通词条)\.htm',p.path):return 'rule'


@recognizer('context_heading')
def glossary_heading(p):
    if re.search(r'术语汇编',p.path) and p.c.method=='heading':return 'rule'


@recognizer('context_heading')
def subclass_in_class_folder(p):
    if p.primary and p.class_context and p.zh not in CLASSES and re.search(r'\d+\s*级|第[三六十]级',p.text):return 'subclass'


def class_region_heading(p):
    """A titled heading on a class page that is not the page's own framing (职业特性, 特性表, 法术列表)."""
    return p.class_context and not p.primary and p.en and not re.search(r'作为|成为|职业特性|子职业|特性表|法术列表|扩展法术',p.name)


@recognizer('level_statement')
def feature_level_statement(p):
    if class_region_heading(p) and re.search(r'(?:从|在|到达|达到|第)\s*\d+\s*级',p.intro[:250]):return 'class_feature'


@recognizer('choice_pool_heading')
def choice_pool_member(p):
    if class_region_heading(p) and p.c.fields.get('choice_pool'):return 'class_feature'


@recognizer('context_heading')
def feature_region_heading(p):
    if class_region_heading(p) and p.c.method in {'colored_heading','word_heading'} and p.c.fields.get('class_feature_region'):return 'class_feature'


# Titled headings take a type from their folder or TOC section.
@recognizer('context_heading')
def feat_folder(p):
    if p.titled and re.search(r'专长',p.path.replace('专长与背景','')) and not re.search(r'前置|先决|特性|组成|核心|来自|选择|复选|总览|概述|背景',p.name):return 'feat'


@recognizer('context_heading')
def background_folder(p):
    if p.titled and re.search(r'角色起源/背景|个性与背景/背景|/背景/',p.path) and p.primary:return 'background'


@recognizer('context_heading')
def species_folder(p):
    if p.titled and re.search(r'/种族/|/物种/|/血统/|/族裔/',p.path) and p.primary and not re.search(r'特质|特性|列表|简介',p.name):return 'species'


@recognizer('context_heading')
def subclass_section(p):
    if p.titled and p.primary and (re.search(r'子职|子职业',p.context) or (re.search(r'/角色职业/|/职业/',p.context) and re.search(r'\d+级|第\d+级|第[三六十]级',p.text))):return 'subclass'


@recognizer('context_heading')
def class_option_section(p):
    if p.titled and re.search(r'祈唤|战技|超魔|灌注|职业特性|子职特性',p.context) and p.en:return 'class_feature'


def interdict_boon_option(p):
    return p.titled and p.class_context and '禁令恩惠选项' in p.path


@recognizer('document_structure')
def interdict_boon_tier(p):
    """A 禁令恩惠 options page groups its boons under level tiers."""
    if interdict_boon_option(p) and re.match(r'^\d+级禁令恩惠',p.name):return 'section'


@recognizer('choice_pool_heading')
def interdict_boon(p):
    if interdict_boon_option(p) and not re.match(r'^\d+级禁令恩惠',p.name):return 'class_feature'


@recognizer('context_heading')
def reward_section(p):
    if not p.titled or not re.search(r'黑暗赠礼|超自然赠礼|超自然奖励|超自然祝福|炼狱祝福|上古邪物的祝福',p.path):return None
    overview=re.search(r'^(?:超自然赠礼|超自然奖励|黑暗赠礼(?:描述)?|黑暗交易|祝福|护咒|炼狱祝福|上古邪物的祝福)(?:\s*[A-Za-z].*)?$',p.name)
    if not overview and not re.search(r'翻译暂缺|Adventures of One Character|Charms$',p.name,re.I):return 'reward'


@recognizer('context_heading')
def trap_rules_page(p):
    if p.titled and '陷阱' in p.path and p.primary:return 'rule'


@recognizer('context_fields')
def poison_section(p):
    if p.titled and re.search(r'毒药|毒素',p.context) and re.search(r'DC|豁免|毒性',p.intro):return 'poison'


@recognizer('context_fields')
def disease_section(p):
    if p.titled and re.search(r'疾病',p.context) and re.search(r'感染|豁免|治疗',p.intro):return 'disease'


@recognizer('context_heading')
def rules_chapter_page(p):
    if p.primary and re.search(r'术语|/状态\.|/状态/|进行游戏|/战斗/|属性值应用',p.path):return 'rule'


@recognizer('context_heading')
def subsystem_section(p):
    if re.search(r'幕间|停工|虔诚|声望|据点|公司职位|变身',p.context) and p.short_name:return 'subsystem'


@recognizer('context_heading')
def lore_chapter_page(p):
    if p.primary and re.search(r'神祇|神系|诸神|派系|地理|位面|宇宙|城镇|领域|冒险|战役',p.context):return 'lore'


@dataclass(frozen=True)
class Typing:
    """A candidate's type, the rule that decided it, and every type its printed evidence names."""
    type: str
    rule: str
    printed_types: tuple[tuple[str, str], ...]


def classify(c:Candidate,own,full,context,primary=False):
    """Type one candidate: the first recognizer that answers decides; printed evidence found after it is kept."""
    p=Probe(c,own,full,context,primary)
    decided=None;printed=[]
    for r in RECOGNIZERS:
        if decided and not r.printed:continue
        answer=r.test(p)
        if not answer:continue
        answers=answer if isinstance(answer,list) else [answer if isinstance(answer,tuple) else (answer,r.rule)]
        if r.printed:printed.extend(answers)
        if not decided:decided=answers[0]
    kind,rule=decided or ('section','document_structure')
    # A named table is reference material whatever its heading says.
    if kind=='section' and c.fields.get('table_span'):kind,rule='lore','named_table_structure'
    return Typing(kind,rule,tuple(dict.fromkeys(printed)))


# ---------------------------------------------------------------------------------------------------
# Second pass: the page as a whole.

def condition_member(name,body):
    """A bilingual condition title; the topic's own rule headings (持续时间, 豁免) are not conditions."""
    zh,en=split_name(name)
    return bool(zh and en and len(name)<80 and not CONDITION_RULE_HEADING.match(zh))


def hazard_member(name,body):
    """A short title whose body applies a rule; introductions and lookup tables describe hazards without one."""
    return len(name)<80 and not re.search(r'[。！？；]',name) and bool(RULES_MECHANICS.search(body[:6000]))


@dataclass(frozen=True)
class Topic:
    """A catalog that types the definitions it holds.

    `heading` matches the catalog's printed name. `word` marks headings that speak about the topic
    (状态不叠加, 危害度及等级, 自然灾害) rather than define one of its members. `member` states what a
    definition inside the catalog shows.
    """
    type: str
    heading: re.Pattern
    word: re.Pattern
    member: object


# Bare 危害 names a rules section (DMG 2024 危害, 盐沼幽魂 at-sea hazards) whose headings include procedures
# such as 团体检定; catalogs of hazard definitions are printed as 危害物, 危害物实例 or an environment chapter.
TOPICS=(
    Topic('condition',re.compile(r'状态'),re.compile(r'状态'),condition_member),
    Topic('hazard',re.compile(r'(?:野外|地城|地下城)?危害物(?:实例)?|环境(?:灾害|危害|影响)'),re.compile(r'危害|灾害'),hazard_member),
)
# A page with no place in the table of contents.
NO_TOC_PLACE={'above':[],'lists_pages':False}


def topic_named(name):
    """The catalog a page, heading or TOC label names, or None."""
    zh,_=split_name(name)
    zh=key(re.sub(r'^附录[A-Za-z]?[：:]\s*','',zh))
    return next((t for t in TOPICS if t.heading.fullmatch(zh)),None)


def type_topic_members(entries,primary,text,toc_place):
    """A catalog types the untyped definitions it holds (see TOPICS), at three scopes.

    - A TOC section such as 环境灾害 holds one definition per page beneath it. A page whose own TOC node
      lists further pages introduces a subsection, and a stat block owns its page's identity.
    - A page named for a catalog is the rule that introduces it; its headings are the candidates.
    - A heading named for a catalog inside another page holds its child headings, or else the run of
      sibling headings that follows it, until a heading no longer qualifies.
    """
    starts=sorted({e['start'] for e in entries})

    def body(e,whole=False):
        end=e['end'] if whole else min(e['end'],next((s for s in starts if s>e['start']),e['end']))
        return text[e['title_end']:end]

    def ancestors(e):
        p=e['parent_index']
        while p is not None:
            yield entries[p];p=entries[p]['parent_index']

    def qualifies(e,topic,whole=False):
        zh,_=split_name(e['name'])
        return e['type']=='section' and not topic.word.search(zh) and not topic_named(e['name']) and topic.member(e['name'],body(e,whole))

    def admit(e,topic):
        e['type']=topic.type;e['classification_rule']='topic_context'

    def within(topic,root):
        """Candidates on the whole page when the root is the primary entry, else the root's descendants."""
        for e in entries:
            if e is root or e is primary or e['boundary_rule']=='page_intro':continue
            lineage=list(ancestors(e))
            if root is not primary and not any(a is root for a in lineage):continue
            # Definitions nested in another typed entity, such as a stat block's 动作 heading, belong to that entity.
            if any(a['type'] not in {'section','rule','lore',topic.type} for a in lineage if a is not root):continue
            if qualifies(e,topic):admit(e,topic)

    toc_topic=next((t for t in map(topic_named,reversed(toc_place['above'])) if t),None)
    if toc_topic:
        holds_statblock=any(e is not primary and e['type']=='creature' for e in entries)
        if not toc_place['lists_pages'] and not holds_statblock and qualifies(primary,toc_topic,whole=True):admit(primary,toc_topic)
        within(toc_topic,primary)
    page_topic=topic_named(primary['name'])
    if page_topic:
        if primary['type']=='section':
            primary['type']='rule';primary['classification_rule']='topic_context'
        within(page_topic,primary)
    for i,heading in enumerate(entries):
        topic=topic_named(heading['name'])
        if heading is primary or not topic:continue
        nested=[e for e in entries if e['parent_index']==i]
        if nested:
            within(topic,heading);continue
        for e in sorted((e for e in entries if e['parent_index']==heading['parent_index'] and e['start']>heading['start']),key=lambda e:e['start']):
            if e['type']!='section':continue
            if not qualifies(e,topic):break
            admit(e,topic)


def refine_page_types(entries,text,toc_place=NO_TOC_PLACE):
    """Second pass: the page's primary entry and its catalogs type the headings beneath them.

    Classification of a heading cannot know what its page is until the page's own primary entry
    has been classified. This pass applies that context: a class or subclass owns its level
    features, and a catalog owns its definitions (type_topic_members).
    """
    if not entries:return
    primary_index=1 if len(entries)>1 and entries[0]['boundary_rule']=='page_intro' else 0
    primary=entries[primary_index]
    full=text[primary['start']:primary['end']]
    structured=not document_structure_name(primary['name'])
    if primary['type']=='section' and structured and class_structure(full,primary['name']):
        primary['type']='class';primary['classification_rule']='class_structure'
    levels=[(e,level_heading(e['name'])) for e in entries if e is not primary and e['type'] in {'section','class_feature'}]
    levels=[(e,n) for e,n in levels if n is not None]
    # A subclass adds only features above the class's own levels; a class always prints 1st level.
    if primary['type']=='section' and structured and len(levels)>=2 and min(n for _,n in levels)>=3:
        primary['type']='subclass';primary['classification_rule']='subclass_structure'
    if primary['type'] in {'class','subclass'}:
        for e,n in levels:
            e['type']='class_feature'
            e['classification_rule']='level_heading' if primary['type']=='class' else 'parent_class_context'
    type_topic_members(entries,primary,text,toc_place)
