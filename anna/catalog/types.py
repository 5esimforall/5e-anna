"""Entry typing: declared evidence, declarative recognizer tables, and page-scope refinement."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
import re
from typing import Callable

from anna.catalog.signatures import (
    CLASSES,
    CLASS_PATH,
    CONDITION_RULE_HEADING,
    RULES_MECHANICS,
    STATBLOCK_PART,
    class_structure,
    document_structure_name,
    matches_background_fields,
    matches_disease_descriptor,
    matches_facility_descriptor,
    matches_feat_descriptor,
    matches_hazard_descriptor,
    matches_item_descriptor,
    matches_poison_descriptor,
    matches_siege_layout,
    matches_species_fields,
    matches_spell_fields,
    matches_stat_fields,
    matches_tool_card,
    matches_trap_descriptor,
    matches_vehicle_layout,
    printed_type,
)
from anna.source.html import compact, key, split_name

RULE_TYPES = {'rule', 'action', 'condition', 'hazard', 'subsystem', 'ability', 'skill'}
STRUCTURAL_METHODS = {'heading', 'colored_heading', 'word_heading'}

PRINTED = '原文明示'
INFERRED = '上下文推得'
UNTYPED = '未分类段落'

RULE_EVIDENCE = {
    # The index or the page layout names the type.
    'index_or_statblock': PRINTED,
    'stat_signature': PRINTED,
    'spell_signature': PRINTED,
    'magic_item_signature': PRINTED,
    'poison_signature': PRINTED,
    'facility_signature': PRINTED,
    'facility_list_signature': PRINTED,
    'vehicle_signature': PRINTED,
    'hazard_signature': PRINTED,
    'trap_signature': PRINTED,
    'tool_signature': PRINTED,
    'siege_signature': PRINTED,
    'disease_descriptor': PRINTED,
    'equipment_table': PRINTED,
    'vehicle_table': PRINTED,
    'animal_offer_table': PRINTED,
    'spell_fields': PRINTED,
    'stat_fields': PRINTED,
    'background_fields': PRINTED,
    'species_fields': PRINTED,
    'item_descriptor': PRINTED,
    'feat_descriptor': PRINTED,
    'poison_descriptor': PRINTED,
    'class_structure': PRINTED,
    'level_heading': PRINTED,
    'level_statement': PRINTED,
    'name_label': PRINTED,
    'definition_table': PRINTED,
    'reference_family_member': PRINTED,
    'skill_notation': PRINTED,
    # The place the text sits implies the type.
    'checked_ability': INFERRED,
    'ability_check_heading': INFERRED,
    'core_rule_heading': INFERRED,
    'context_heading': INFERRED,
    'context_fields': INFERRED,
    'topic_context': INFERRED,
    'property_definition': INFERRED,
    'named_table_structure': INFERRED,
    'rule_subdefinition': INFERRED,
    'level_inline_heading': INFERRED,
    'feature_inline_heading': INFERRED,
    'choice_pool_heading': INFERRED,
    'subclass_structure': INFERRED,
    'parent_class_context': INFERRED,
    'document_structure': UNTYPED,
}

TYPE_LABELS = {
    'class': '职业', 'subclass': '子职', 'class_feature': '职业能力', 'species': '种族物种',
    'background': '背景', 'feat': '专长', 'spell': '法术', 'equipment': '普通装备',
    'magic_item': '魔法物品', 'creature': '生物数据卡', 'reward': '奖励', 'vehicle': '载具',
    'animal_offer': '动物购置', 'facility': '设施', 'ability': '属性', 'skill': '技能',
    'condition': '状态', 'action': '动作', 'rule': '规则', 'trap': '陷阱', 'poison': '毒药',
    'disease': '疾病', 'hazard': '环境危害', 'subsystem': '扩展机制', 'lore': '资料索引',
    'section': '文献段落',
}


def evidence_status(rule: str, kind: str) -> str:
    """The status an entry shows for its type; only document_structure leaves an entry untyped."""
    if rule not in RULE_EVIDENCE:
        raise ValueError(f'classification rule without declared evidence: {rule}')
    status = RULE_EVIDENCE[rule]
    if (status == UNTYPED) != (kind == 'section'):
        raise ValueError(f'{rule} cannot type an entry as {kind}')
    return status


def type_conflicts(kind: str, printed_types) -> list[dict]:
    """Printed evidence for a type other than the one the entry received."""
    return [{'type': t, 'rule': r} for t, r in printed_types if t != kind]


# ---------------------------------------------------------------------------------------------------
# Page context markers for class/subclass feature regions and option pools.

CLASS_FEATURE_START = re.compile(r'职业特性|Class Features|可选职业|祈唤|战技|超魔法|灌注', re.I)
CHOICE_POOL = re.compile(r'战斗风格|祈唤|战技|超魔法|灌注')
LEVEL_HEADING = re.compile(r'^\s*(\d{1,2})\s*级\s*[:：]')


def level_heading(name: str) -> int | None:
    """The level a class or subclass feature heading states, or None for prose and range headings."""
    m = LEVEL_HEADING.match(name)
    return int(m[1]) if m else None


def mark_class_regions(cs, text: str) -> None:
    """Mark candidates after a page's class-feature heading or inside an option pool."""
    feature_starts = [c.start for c in cs if CLASS_FEATURE_START.search(c.name)]
    if not feature_starts:
        optional = re.search(r'可选职业特性', text[:1000])
        if optional:
            feature_starts = [optional.start()]
    for c in cs:
        c.fields['class_feature_region'] = bool(feature_starts and c.start > min(feature_starts))
        ancestors = []
        pi = c.parent
        while pi is not None:
            ancestors.append(cs[pi].name)
            pi = cs[pi].parent
        c.fields['choice_pool'] = next((n for n in ancestors if CHOICE_POOL.search(n)), None)


# ---------------------------------------------------------------------------------------------------
# First pass: candidate probe and declarative recognizer registry.

class Probe:
    """What one candidate offers its recognizers: its name, its text window, and its page context."""

    def __init__(self, c, own: str, full: str, context: str, primary: bool):
        self.c = c
        self.primary = primary
        self.context = context
        self.path = context.split(' | ')[-1]
        self.name = compact(c.name)
        self.class_context = bool(CLASS_PATH.search(self.path))
        self.full = full
        self.text = own
        self.intro = own[:1800]

    @cached_property
    def after_title(self) -> str:
        return self.text[max(0, self.c.title_end - self.c.start):]

    @cached_property
    def descriptor(self) -> str:
        return compact(self.after_title)[:220]

    @cached_property
    def names(self) -> tuple[str, str]:
        return split_name(self.name)

    @property
    def zh(self) -> str:
        return self.names[0]

    @property
    def en(self) -> str:
        return self.names[1]

    @cached_property
    def short_name(self) -> bool:
        return not re.search(r'[。！？；]', self.name) and len(self.name) < 110

    @cached_property
    def prints_layout(self) -> bool:
        return any(r.test(self) for r in RECOGNIZERS if r.printed)

    @property
    def titled(self) -> bool:
        return self.short_name and (self.primary or bool(self.en))


@dataclass(frozen=True)
class Recognizer:
    """One way a candidate shows its type; declaration order is precedence."""
    rule: str
    test: Callable[[Probe], str | tuple[str, str] | list[tuple[str, str]] | None]

    @property
    def printed(self) -> bool:
        return RULE_EVIDENCE[self.rule] == PRINTED


RECOGNIZERS: list[Recognizer] = []


def add_recognizer(rule: str, test: Callable[[Probe], object]) -> None:
    if rule not in RULE_EVIDENCE:
        raise ValueError(f'classification rule without declared evidence: {rule}')
    RECOGNIZERS.append(Recognizer(rule, test))


def recognizer(rule: str):
    """Decorator registering the next recognizer in precedence order."""
    def register(test):
        add_recognizer(rule, test)
        return test
    return register


# 1. Pre-classified candidates, structural guards, and rule-section headings.

OPTION_CATALOG_RE = re.compile(
    r'^(?:魔能祈唤(?:选项)?|禁令恩惠|子职(?:业)?选项|反派选项|超魔(?:法)?选项|灌注选项|奇械师注法(?:选项)?)(?:\s*[A-Za-z].*)?$'
)
POISON_PROCEDURE_RE = re.compile(r'^(?:毒药的(?:制作|提取)|采购毒药|制作毒药|提取毒药)')
RULES_SECTION_RE = re.compile(
    r'^(?:子职|子职业|特色设施详述|疾病范例|毒药范例|毒药的(?:制作|提取)|毒药|疾病|'
    r'诅咒与魔法疫病|魔法疫病|范例疫病|专长与背景)(?:\s*[A-Za-z].*)?$'
)
COMBAT_RULES_PATH_RE = re.compile(r'术语|进行游戏|/战斗/')


@recognizer('index_or_statblock')
def indexed_or_signed(p: Probe):
    if p.c.kinds:
        first = p.c.method if p.c.method.endswith('_signature') else 'index_or_statblock'
        return [(k, first if i == 0 else 'index_or_statblock') for i, k in enumerate(p.c.kinds)]


@recognizer('skill_notation')
def skill_lead(p: Probe):
    if p.c.method == 'skill_lead':
        return 'skill'


@recognizer('document_structure')
def page_introduction(p: Probe):
    if p.c.method == 'page_intro' and not p.prints_layout:
        return 'section'


@recognizer('named_table_structure')
def table_caption(p: Probe):
    if p.c.method == 'table_caption' and not p.prints_layout:
        return 'lore'


@recognizer('feature_inline_heading')
def class_inline_heading(p: Probe):
    if p.class_context and p.c.method in {'level_inline_heading', 'feature_inline_heading'}:
        return 'class_feature', p.c.method


@recognizer('document_structure')
def option_catalog(p: Probe):
    if (p.primary or '选项' in p.name) and OPTION_CATALOG_RE.match(p.name):
        return 'section'


@recognizer('document_structure')
def structure_name(p: Probe):
    if document_structure_name(p.name) and not ('术语汇编' in p.path and p.c.method == 'heading'):
        return 'section'


@recognizer('class_structure')
def class_layout(p: Probe):
    if p.primary and class_structure(p.full, p.name):
        return 'class'


@recognizer('context_heading')
def poison_procedure(p: Probe):
    if POISON_PROCEDURE_RE.match(p.name):
        return 'rule'


@recognizer('context_heading')
def rules_section_name(p: Probe):
    if RULES_SECTION_RE.search(p.name):
        return 'rule'


@recognizer('context_heading')
def combat_rule_heading(p: Probe):
    if STATBLOCK_PART.fullmatch(p.name) and COMBAT_RULES_PATH_RE.search(p.path):
        return 'rule'


@recognizer('document_structure')
def statblock_part_heading(p: Probe):
    if STATBLOCK_PART.fullmatch(p.name) and not COMBAT_RULES_PATH_RE.search(p.path):
        return 'section'


# 2. Printed descriptor and field-layout signatures (shared with anna.catalog.signatures).

PRINTED_SIGNATURE_RULES: tuple[tuple[str, str, Callable[[Probe], bool]], ...] = (
    ('facility_signature', 'facility', lambda p: matches_facility_descriptor(p.after_title)),
    ('vehicle_signature', 'vehicle', lambda p: matches_vehicle_layout(
        p.name, p.after_title, p.intro, p.context, p.full[:1800] if p.primary else p.intro,
    )),
    ('hazard_signature', 'hazard', lambda p: matches_hazard_descriptor(p.after_title)),
    ('trap_signature', 'trap', lambda p: matches_trap_descriptor(p.name, p.after_title)),
    ('tool_signature', 'equipment', lambda p: not p.primary and matches_tool_card(p.after_title)),
    ('siege_signature', 'equipment', lambda p: not p.primary and matches_siege_layout(p.name, p.after_title, p.intro, p.context)),
    ('spell_fields', 'spell', lambda p: matches_spell_fields(p.intro)),
    ('stat_fields', 'creature', lambda p: matches_stat_fields(p.intro)),
    ('item_descriptor', 'magic_item', lambda p: matches_item_descriptor(p.descriptor)),
    ('feat_descriptor', 'feat', lambda p: matches_feat_descriptor(p.descriptor)),
    ('level_heading', 'class_feature', lambda p: level_heading(p.name) is not None and p.class_context),
    ('background_fields', 'background', lambda p: matches_background_fields(p.intro, p.primary, p.context)),
    ('species_fields', 'species', lambda p: matches_species_fields(p.intro, p.primary, p.context)),
    ('poison_descriptor', 'poison', lambda p: matches_poison_descriptor(p.descriptor)),
    ('disease_descriptor', 'disease', lambda p: matches_disease_descriptor(p.after_title)),
)

for _rule, _kind, _predicate in PRINTED_SIGNATURE_RULES:
    add_recognizer(_rule, (lambda k, pred: (lambda p: k if pred(p) else None))(_kind, _predicate))


@recognizer('name_label')
def name_label(p: Probe):
    return printed_type(p.name) or None


# 3. Declarative folder, TOC, and class-region context rules.

CLASS_FRAMING_HEADING_RE = re.compile(r'作为|成为|职业特性|子职业|特性表|法术列表|扩展法术')
SUBCLASS_LEVEL_TEXT_RE = re.compile(r'\d+\s*级|第[三六十]级')
SUBCLASS_SECTION_LEVEL_RE = re.compile(r'\d+级|第\d+级|第[三六十]级')
FEATURE_LEVEL_STATEMENT_RE = re.compile(r'(?:从|在|到达|达到|第)\s*\d+\s*级')
INTERDICT_TIER_RE = re.compile(r'^\d+级禁令恩惠')


def class_region_heading(p: Probe) -> bool:
    """A titled heading on a class page that is not the page's own framing."""
    return (
        p.class_context
        and not p.primary
        and bool(p.en)
        and not CLASS_FRAMING_HEADING_RE.search(p.name)
        and not STATBLOCK_PART.fullmatch(p.name)
        and not document_structure_name(p.name)
        and not option_catalog(p)
        and 'spell' not in p.c.kinds
        and not matches_spell_fields(p.intro)
    )


@dataclass(frozen=True)
class ContextRule:
    """Declarative contextual classification rule over a candidate's path, TOC context, and heading form."""
    kind: str
    rule: str = 'context_heading'
    path: str | None = None
    context: str | None = None
    intro: str | None = None
    exclude_name: str | None = None
    exclude_flags: int = 0
    primary: bool = False
    titled: bool = False
    short_name: bool = False
    english: bool = False
    predicate: Callable[[Probe], bool] | None = None
    _path_re: re.Pattern[str] | None = field(init=False, repr=False, compare=False)
    _context_re: re.Pattern[str] | None = field(init=False, repr=False, compare=False)
    _intro_re: re.Pattern[str] | None = field(init=False, repr=False, compare=False)
    _exclude_re: re.Pattern[str] | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, '_path_re', re.compile(self.path) if self.path else None)
        object.__setattr__(self, '_context_re', re.compile(self.context) if self.context else None)
        object.__setattr__(self, '_intro_re', re.compile(self.intro) if self.intro else None)
        object.__setattr__(self, '_exclude_re', re.compile(self.exclude_name, self.exclude_flags) if self.exclude_name else None)

    def matches(self, p: Probe) -> str | None:
        if self.primary and not p.primary:
            return None
        if self.titled and not p.titled:
            return None
        if self.short_name and not p.short_name:
            return None
        if self.english and not p.en:
            return None
        if self._path_re and not self._path_re.search(p.path):
            return None
        if self._context_re and not self._context_re.search(p.context):
            return None
        if self._intro_re and not self._intro_re.search(p.intro):
            return None
        if self._exclude_re and self._exclude_re.search(p.name):
            return None
        if self.predicate and not self.predicate(p):
            return None
        return self.kind


CONTEXT_RULES: tuple[ContextRule, ...] = (
    ContextRule(
        'rule', rule='property_definition', path=r'/装备/(?:词条|精通词条)\.htm',
        predicate=lambda p: p.c.method in STRUCTURAL_METHODS and not p.primary,
    ),
    ContextRule('rule', path=r'术语汇编', predicate=lambda p: p.c.method == 'heading'),
    ContextRule(
        'subclass', primary=True, exclude_name=r'法术',
        predicate=lambda p: p.class_context and p.zh not in CLASSES and bool(SUBCLASS_LEVEL_TEXT_RE.search(p.full)),
    ),
    ContextRule(
        'class_feature', rule='level_statement',
        predicate=lambda p: class_region_heading(p) and bool(FEATURE_LEVEL_STATEMENT_RE.search(p.intro[:250])),
    ),
    ContextRule(
        'class_feature', rule='choice_pool_heading',
        predicate=lambda p: class_region_heading(p) and bool(p.c.fields.get('choice_pool')),
    ),
    ContextRule(
        'class_feature',
        predicate=lambda p: (
            class_region_heading(p)
            and p.c.method in {'colored_heading', 'word_heading'}
            and bool(p.c.fields.get('class_feature_region'))
        ),
    ),
    ContextRule(
        'feat', titled=True,
        exclude_name=r'前置|先决|特性|组成|核心|来自|选择|复选|总览|概述|背景',
        predicate=lambda p: '专长' in p.path.replace('专长与背景', ''),
    ),
    ContextRule('background', titled=True, primary=True, path=r'角色起源/背景|个性与背景/背景|/背景/'),
    ContextRule('species', titled=True, primary=True, path=r'/种族/|/物种/|/血统/|/族裔/', exclude_name=r'特质|特性|列表|简介'),
    ContextRule(
        'subclass', titled=True, primary=True, exclude_name=r'法术',
        predicate=lambda p: bool(
            re.search(r'子职|子职业', p.context)
            or (re.search(r'/角色职业/|/职业/', p.context) and SUBCLASS_SECTION_LEVEL_RE.search(p.full))
        ),
    ),
    ContextRule('class_feature', titled=True, english=True, context=r'祈唤|战技|超魔|灌注|职业特性|子职特性'),
    ContextRule(
        'section', rule='document_structure', titled=True,
        predicate=lambda p: p.class_context and '禁令恩惠选项' in p.path and bool(INTERDICT_TIER_RE.match(p.name)),
    ),
    ContextRule(
        'class_feature', rule='choice_pool_heading', titled=True,
        predicate=lambda p: p.class_context and '禁令恩惠选项' in p.path and not INTERDICT_TIER_RE.match(p.name),
    ),
    ContextRule(
        'reward', titled=True,
        path=r'黑暗赠礼|超自然赠礼|超自然奖励|超自然祝福|炼狱祝福|上古邪物的祝福',
        exclude_name=(
            r'^(?:超自然赠礼|超自然奖励|黑暗赠礼(?:描述)?|黑暗交易|祝福|护咒|炼狱祝福|上古邪物的祝福)'
            r'(?:\s*[A-Za-z].*)?$|翻译暂缺|Adventures of One Character|Charms$'
        ),
        exclude_flags=re.I,
    ),
    ContextRule('rule', titled=True, primary=True, path=r'陷阱'),
    ContextRule('poison', rule='context_fields', titled=True, context=r'毒药|毒素', intro=r'DC|豁免|毒性'),
    ContextRule('disease', rule='context_fields', titled=True, context=r'疾病', intro=r'感染|豁免|治疗'),
    ContextRule(
        'rule', rule='core_rule_heading', titled=True,
        predicate=lambda p: bool(p.c.fields.get('core_rule_chapter')) and p.c.method in STRUCTURAL_METHODS,
    ),
    ContextRule('rule', primary=True, path=r'术语|/状态\.|/状态/|进行游戏|/战斗/|属性值应用'),
    ContextRule('subsystem', short_name=True, context=r'幕间|停工|休整期|修整期|虔诚|声望|据点|公司职位|变身|恐惧与精神压力|恐惧与压力|心慌与惊恐', predicate=lambda p: not p.c.fields.get('table_span')),
    ContextRule('subsystem', short_name=True, path=r'/(?:疯狂|伤患|伤残)\.(?:htm|html)$', predicate=lambda p: not p.c.fields.get('table_span')),
    ContextRule('lore', primary=True, context=r'神祇|神系|诸神|派系|地理|位面|宇宙|城镇|领域|冒险|战役'),
)

for _ctx_rule in CONTEXT_RULES:
    add_recognizer(_ctx_rule.rule, _ctx_rule.matches)


@dataclass(frozen=True)
class Typing:
    """A candidate's type, the rule that decided it, and every type its printed evidence names."""
    type: str
    rule: str
    printed_types: tuple[tuple[str, str], ...]


def classify(c, own: str, full: str, context: str, primary: bool = False) -> Typing:
    """Type one candidate: the first recognizer that answers decides; later printed evidence is kept."""
    p = Probe(c, own, full, context, primary)
    decided = None
    printed: list[tuple[str, str]] = []
    for r in RECOGNIZERS:
        if decided and not r.printed:
            continue
        answer = r.test(p)
        if not answer:
            continue
        answers = answer if isinstance(answer, list) else [answer if isinstance(answer, tuple) else (answer, r.rule)]
        if r.printed:
            printed.extend(answers)
        if not decided:
            decided = answers[0]
    kind, rule = decided or ('section', 'document_structure')
    if kind == 'section' and c.fields.get('table_span'):
        kind, rule = 'lore', 'named_table_structure'
    return Typing(kind, rule, tuple(dict.fromkeys(printed)))


# ---------------------------------------------------------------------------------------------------
# Second pass: whole-page container and catalog refinement.

def condition_member(name: str, body: str) -> bool:
    """A bilingual condition title; the topic's own rule headings (持续时间, 豁免) are not conditions."""
    zh, en = split_name(name)
    return bool(zh and en and len(name) < 80 and not CONDITION_RULE_HEADING.match(zh))


def hazard_member(name: str, body: str) -> bool:
    """A short title whose body applies a rule; introductions and lookup tables describe hazards without one."""
    return len(name) < 80 and not re.search(r'[。！？；]', name) and bool(RULES_MECHANICS.search(body[:6000]))


def action_member(name: str, body: str) -> bool:
    """A bilingual action option title inside a 2014 action catalog section."""
    zh, en = split_name(name)
    return bool(zh and en and len(name) < 80)


def tool_proficiency_member(name: str, body: str) -> bool:
    """A bilingual tool proficiency entry whose body states sample DCs."""
    zh, en = split_name(name)
    return bool(zh and en and len(name) < 80 and 'DC' in body)


@dataclass(frozen=True)
class Topic:
    """A catalog that types the definitions it holds at TOC, page, or heading scope."""
    type: str
    heading: re.Pattern[str]
    word: re.Pattern[str]
    member: Callable[[str, str], bool]


TOPICS = (
    Topic('condition', re.compile(r'状态'), re.compile(r'状态'), condition_member),
    Topic('hazard', re.compile(r'(?:野外|地城|地下城)?危害物(?:实例)?|环境(?:灾害|危害|影响)'), re.compile(r'危害|灾害'), hazard_member),
    Topic('action', re.compile(r'战斗动作|动作选用项'), re.compile(r'动作|边栏'), action_member),
    Topic('rule', re.compile(r'工具熟练'), re.compile(r'工具熟练|工具详述'), tool_proficiency_member),
)
NO_TOC_PLACE = {'above': [], 'lists_pages': False}


def topic_named(name: str) -> Topic | None:
    zh, _ = split_name(name)
    zh = key(re.sub(r'^附录[A-Za-z]?[：:]\s*', '', zh))
    return next((t for t in TOPICS if t.heading.fullmatch(zh)), None)


def type_topic_members(entries: list[dict], primary: dict, text: str, toc_place: dict) -> None:
    """Apply catalog topic rules across TOC section, page title, and nested/sibling heading scopes."""
    starts = sorted({e['start'] for e in entries})

    def body(e: dict, whole: bool = False) -> str:
        end = e['end'] if whole else min(e['end'], next((s for s in starts if s > e['start']), e['end']))
        return text[e['title_end']:end]

    def ancestors(e: dict):
        p = e['parent_index']
        while p is not None:
            yield entries[p]
            p = entries[p]['parent_index']

    def qualifies(e: dict, topic: Topic, whole: bool = False) -> bool:
        zh, _ = split_name(e['name'])
        return (
            e['type'] == 'section'
            and not topic.word.search(zh)
            and not topic_named(e['name'])
            and topic.member(e['name'], body(e, whole))
        )

    def admit(e: dict, topic: Topic) -> None:
        e['type'] = topic.type
        e['classification_rule'] = 'topic_context'

    def within(topic: Topic, root: dict) -> None:
        for e in entries:
            if e is root or e is primary or e['boundary_rule'] == 'page_intro':
                continue
            lineage = list(ancestors(e))
            if root is not primary and not any(a is root for a in lineage):
                continue
            if any(a['type'] not in {'section', 'rule', 'lore', topic.type} for a in lineage if a is not root):
                continue
            if qualifies(e, topic):
                admit(e, topic)

    toc_topic = next((t for t in map(topic_named, reversed(toc_place['above'])) if t), None)
    if toc_topic:
        holds_statblock = any(e is not primary and e['type'] == 'creature' for e in entries)
        if not toc_place['lists_pages'] and not holds_statblock and qualifies(primary, toc_topic, whole=True):
            admit(primary, toc_topic)
        within(toc_topic, primary)

    page_topic = topic_named(primary['name'])
    if page_topic:
        if primary['type'] == 'section':
            primary['type'] = 'rule'
            primary['classification_rule'] = 'topic_context'
        within(page_topic, primary)

    for i, heading in enumerate(entries):
        topic = topic_named(heading['name'])
        if heading is primary or not topic:
            continue
        nested = [e for e in entries if e['parent_index'] == i]
        if nested:
            within(topic, heading)
            continue
        siblings = sorted(
            (e for e in entries if e['parent_index'] == heading['parent_index'] and e['start'] > heading['start']),
            key=lambda e: e['start'],
        )
        for e in siblings:
            if e['type'] != 'section':
                continue
            if not qualifies(e, topic):
                break
            admit(e, topic)


def type_checked_abilities(entries: list[dict]) -> None:
    """Pair adjacent (力量 Strength, 力量检定 Strength Checks) sibling headings as ability + rule."""
    siblings: dict[int | None, list[dict]] = defaultdict(list)
    for e in entries:
        siblings[e['parent_index']].append(e)
    for group in siblings.values():
        group.sort(key=lambda e: e['start'])
        for ability, check in zip(group, group[1:]):
            if ability['type'] not in {'section', 'rule'} or check['type'] not in {'section', 'rule'}:
                continue
            zh, en = split_name(ability['name'])
            check_zh, check_en = split_name(check['name'])
            if not (zh and en and check_zh == zh + '检定' and re.fullmatch(re.escape(en) + r'\s+Checks?', check_en, re.I)):
                continue
            ability['type'], ability['classification_rule'] = 'ability', 'checked_ability'
            check['type'], check['classification_rule'] = 'rule', 'ability_check_heading'


def refine_page_types(entries: list[dict], text: str, toc_place: dict = NO_TOC_PLACE) -> None:
    """Second pass: apply page-primary class/subclass, catalog topic, and checked-ability frames."""
    if not entries:
        return
    primary_index = 1 if len(entries) > 1 and entries[0]['boundary_rule'] == 'page_intro' else 0
    primary = entries[primary_index]
    full = text[primary['start']:primary['end']]
    structured = not document_structure_name(primary['name'])

    if primary['type'] == 'section' and structured and class_structure(full, primary['name']):
        primary['type'] = 'class'
        primary['classification_rule'] = 'class_structure'

    levels = [
        (e, lvl)
        for e in entries
        if e is not primary and e['type'] in {'section', 'class_feature'}
        if (lvl := level_heading(e['name'])) is not None
    ]
    if primary['type'] == 'section' and structured and len(levels) >= 2 and min(n for _, n in levels) >= 3:
        primary['type'] = 'subclass'
        primary['classification_rule'] = 'subclass_structure'
    if primary['type'] in {'class', 'subclass'}:
        for e, _ in levels:
            e['type'] = 'class_feature'
            e['classification_rule'] = 'level_heading' if primary['type'] == 'class' else 'parent_class_context'

    type_topic_members(entries, primary, text, toc_place)
    type_checked_abilities(entries)
