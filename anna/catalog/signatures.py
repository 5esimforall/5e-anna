"""Single-entry printed signatures, edition layout markers, and spanned field extraction.

Every signature answers what one candidate's own printed layout states — its bracketed title
badge, its subtitle descriptor line, its labeled parameter block, or its 2014/2024 stat layout —
independently of where its page sits in a book or folder.
"""
from __future__ import annotations

import re

from anna.source.html import NOTATION_JOINER, compact, split_name

CLASSES = {
    '野蛮人', '吟游诗人', '牧师', '德鲁伊', '战士', '武僧', '圣武士', '游侠', '游荡者', '术士',
    '魔契师', '邪术师', '法师', '奇械师', '血猎手', '邪狱使', '拳斗士', '铳士', '魔战士', '怪物猎人',
}
CLASS_SHORT = {
    '诗': '吟游诗人', '吟': '吟游诗人', '牧': '牧师', '德': '德鲁伊',
    '帕': '圣武士', '圣': '圣武士', '软': '游侠', '游': '游侠',
    '术': '术士', '锁': '魔契师', '契': '魔契师', '邪': '邪术师',
    '法': '法师', '械': '奇械师', '魔': '魔战士',
}
# A class's own folder (/战士/, /战士-…), or a class or subclass chapter.
CLASS_PATH = re.compile(
    r'/(?:' + '|'.join(re.escape(x) for x in sorted(CLASSES, key=len, reverse=True))
    + r')(?:/|[-（(.])|/(?:角色职业|职业|子职|子职业)/'
)

CREATURE_SIZES = ('微型', '小型', '中型', '大型', '巨型', '超巨型')
CREATURE_TYPES = (
    '异怪', '野兽', '天族', '构装', '龙类', '元素', '妖精',
    '邪魔', '巨人', '类人', '怪兽', '泥怪', '植物', '亡灵',
)
SPELL_SCHOOLS = ('防护', '咒法', '预言', '惑控', '塑能', '幻术', '死灵', '变化')
FEAT_CATEGORIES = ('起源', '通用', '战斗风格', '传奇恩惠', '种族', '血族')
POISON_MODES = ('服用', '吸入', '接触', '损伤')
ITEM_BASE_KINDS = ('武器', '护甲', '奇物', '戒指', '魔杖', '法杖', '魔棒', '药水', '卷轴', '弹药', '盾牌')
ITEM_WEAPON_SUBKINDS = ('长剑', '短剑', '弯刀', '匕首', '巨剑', '长棍', '权杖', '魔法船')
RARITIES = ('非普通', '极珍稀', '珍稀', '普通', '传说', '神器', '稀有', '罕见', '独特')

GENERIC = re.compile(
    r'^(?:前言|简介|概述|引言|目录|使用本书|专长|法术|魔法物品|装备|种族|背景|职业|角色选项|玩家选项|'
    r'起源专长|通用专长|传奇恩惠专长|战斗风格专长|怪物|生物|图鉴|数据卡|法术详述|'
    r'附录[A-Z]?[：:]?.*|第[一二三四五六七八九十\d]+章.*|[0-9一二三四五六七八九]环|戏法)'
    r'(?:\s*[A-Za-z].*)?$',
    re.I,
)
CATALOG_OVERVIEW_NAME = re.compile(
    r'(?:专长|法术|魔法物品|装备|背景|种族|物种)(?:描述|详述|列表|总览)(?:\s*[A-Za-z].*)?'
)
FIELD_TITLE = re.compile(
    r'^(?:护甲等级|生命值|速度|力量|敏捷|体质|智力|感知|魅力|技能|感官|语言|'
    r'挑战等级|挑战|伤害抗性|伤害免疫|状态免疫)(?:\s*[:：].*|\s*)$'
)
STATBLOCK_PART = re.compile(
    r'(?:动作|附赠动作|反应|特质|传奇动作|巢穴动作)\s*'
    r'(?:Actions?|Bonus Actions?|Reactions?|Traits?|Legendary Actions?|Lair Actions?)?',
    re.I,
)
# A body that applies a defined rule: a DC, a die roll or die table, advantage or disadvantage on a
# d20 test, a condition, exhaustion, difficult terrain or obscurement.
RULES_MECHANICS = re.compile(
    r'DC\s*\d+|(?<![A-Za-z\d])\d*[dD]\d+(?![A-Za-z])|'
    r'(?:攻击|豁免|检定)[^。]{0,12}(?:优势|劣势)|(?:优势|劣势)[^。]{0,12}(?:攻击|豁免|检定)|'
    r'力竭|陷入[^。]{1,8}状态|困难地形|(?:轻度|重度)遮蔽'
)
CONDITION_RULE_HEADING = re.compile(r'^(?:持续时间|豁免|概述|简介|来源|结束)(?:\s|[A-Za-z]|$)')

# Page structure names an entry's role where a title or folder name does not. A class prints a hit
# die and a core-traits table; a subclass adds features starting at 3rd level without core traits.
CLASS_STRUCTURE = re.compile(r'生命[值]?骰[\s\S]{0,40}?每\s*([^\s，。；：:]{1,12}?)\s*等级')
CLASS_TRAITS = re.compile(r'核心特质|熟练加值|熟练项')

# Some definitions print their own type beside their name (目盲 Blinded【状态】, 燃烧 Burning【危害】).
# A heading that is only the label (【状态】Condition) defines the label itself.
PRINTED_TYPE_LABELS = {'状态': 'condition', '动作': 'action', '危害': 'hazard'}
PRINTED_TYPE_LABEL_RE = re.compile(r'[（(【](' + '|'.join(PRINTED_TYPE_LABELS) + r')[）)】]')

# The two rule generations print the same rule differently: 2024 stat blocks list initiative and a
# modifier/save column; 2014 stat blocks print ability scores with parenthesized modifiers; 2024
# spells drop the count from casting time (动作 vs 1 动作).
GENERATION_MARKERS = {
    'creature': {
        '5r': (re.compile(r'先攻\s*[+＋\-−]?\d|调整\s*豁免'), 1),
        '5e': (re.compile(r'\d{1,2}\s*[（(]\s*[+＋\-−]\s*\d+\s*[)）]'), 3),
    },
    'spell': {
        '5r': (re.compile(r'施法时间\s*[:：]\s*(?:动作|附赠|反应)'), 1),
        '5e': (re.compile(r'施法时间\s*[:：]\s*[1１一]\s*个?\s*(?:动作|附赠|反应)'), 1),
    },
}


def field_label(*labels: str) -> re.Pattern[str]:
    """A label that opens a line as a field: label, optional 项 and English gloss, then : or 。"""
    return re.compile(r'(?m)^[ \t]*(?:' + '|'.join(labels) + r')项?(?:[ \t]*[A-Za-z][A-Za-z \t]*)?[ \t]*[:：.。]')


def document_structure_name(name: str) -> bool:
    """Names that label a page or a catalog rather than the entry the page is about."""
    return bool(GENERIC.fullmatch(name)) or bool(CATALOG_OVERVIEW_NAME.fullmatch(name))


def class_structure(text: str, title: str = '') -> bool:
    """A hit die plus core traits describes a class page, whatever its name or directory."""
    head = text[:8000]
    if not CLASS_TRAITS.search(head):
        return False
    m = CLASS_STRUCTURE.search(head)
    if not m:
        return False
    label = compact(m[1])
    if not title:
        return True
    return label in CLASSES or label in title


def skill_notation(skill: str, abilities=()) -> re.Pattern[str]:
    """The ability-check notation applying a skill: 力量（运动）检定, 魅力（欺瞒或游说）检定."""
    lead = '(' + '|'.join(map(re.escape, sorted(abilities, key=len, reverse=True))) + r')\s*' if abilities else ''
    return re.compile(
        lead
        + r'[（(]\s*(?:[^（）()\n]{1,20}?' + NOTATION_JOINER + r'\s*)?'
        + re.escape(skill)
        + r'\s*(?:' + NOTATION_JOINER + r'[^（）()\n]{1,20}?)?[）)]\s*检定'
    )


def printed_type(name: str) -> str:
    """The type a definition prints beside its own name, or '' when the heading is the label itself."""
    m = PRINTED_TYPE_LABEL_RE.search(name)
    if not m:
        return ''
    zh, _ = split_name(compact(name[:m.start()] + ' ' + name[m.end():]))
    return PRINTED_TYPE_LABELS[m[1]] if zh and not zh.startswith(m[1]) else ''


def printed_generation(kind: str, text: str) -> tuple[str, tuple[int, int]] | None:
    """(ruleset, span) when an entry's text is laid out in exactly one generation's format."""
    found = {}
    for rule, (pattern, needed) in GENERATION_MARKERS.get(kind, {}).items():
        matches = list(pattern.finditer(text))
        if len(matches) >= needed:
            found[rule] = (matches[0].start(), matches[needed - 1].end())
    return next(iter(found.items())) if len(found) == 1 else None


# ---------------------------------------------------------------------------------------------------
# Printed layout & descriptor patterns shared by candidate promotion, typing, and field extraction.

FEAT_DESCRIPTOR_RE = re.compile(r'\s*(' + '|'.join(FEAT_CATEGORIES) + r')专长(?:\s|[（(，,。：:]|$)')
SPELL_DESCRIPTOR_RE = re.compile(
    r'(戏法|[一二三四五六七八九十\d]+环)\s*(?:[｜|/：:]\s*)?(' + '|'.join(SPELL_SCHOOLS) + r')'
)
SPELL_FIELD_LABELS = (
    field_label('施法时间'),
    field_label('施法距离', '射程', '距离'),
    field_label('法术成分', '成分'),
)
STAT_BLOCK_RE = re.compile(
    r'(?m)^[ \t]*(?:护甲等级|AC)[ \t]*(?:[:：]|\d).{0,120}?(?:生命值|(?<![A-Za-z])HP)[ \t]*(?:[:：]|\d)',
    re.S | re.I,
)
STAT_CONFIRM_RE = re.compile(r'挑战|\bCR\b|力量.*敏捷', re.S)
_HEADER_SIZES = '|'.join((*CREATURE_SIZES, '大形'))
_HEADER_TYPES = '|'.join((*CREATURE_TYPES, '不死', '天界'))
_SIZE_SYNONYMS = {'大形': '大型'}
_CREATURE_TYPE_SYNONYMS = {'不死': '亡灵', '天界': '天族'}
CREATURE_HEADER_RE = re.compile(
    rf'({_HEADER_SIZES})(?:[ \t]*或[ \t]*({_HEADER_SIZES}))?\s*({_HEADER_TYPES})(?:[ \t]*或[ \t]*({_HEADER_TYPES}))?'
)
ITEM_DESCRIPTOR_RE = re.compile(
    r'^(?:' + '|'.join(ITEM_BASE_KINDS) + r').{0,60}(?:普通|珍稀|稀有|传说|神器|独特)'
)
ITEM_EXTRACT_RE = re.compile(
    r'\s*(' + '|'.join(ITEM_BASE_KINDS + ITEM_WEAPON_SUBKINDS) + r')[^\n]{0,80}?(' + '|'.join(RARITIES) + r')',
)
ATTUNEMENT_RE = re.compile(r'[（(]([^\n）)]{0,80}同调[^\n）)]{0,30})[）)]')
BACKGROUND_FIELD_LABELS = (
    field_label('技能熟练'),
    field_label('工具熟练', '语言'),
    field_label('(?:初始|起始)?装备'),
)
POISON_DESCRIPTOR_RE = re.compile(r'^(?:' + '|'.join(POISON_MODES) + r')型毒药')
FACILITY_DESCRIPTOR_RE = re.compile(r'^\s*\d+\s*级据点设施')
HAZARD_DESCRIPTOR_RE = re.compile(r'(?:致命|妨碍)危害\s*[（(]\s*等级')
TRAP_TITLE_SUFFIX_RE = re.compile(r'\s+(?:机械|魔法)陷阱$')
TRAP_DESCRIPTOR_RE = re.compile(r'(?:机械|魔法|简易|复合|致命|妨碍)陷阱(?:\s|[（(]|$)')
VEHICLE_CONTEXT_RE = re.compile(r'载具|船舶|战争机器|魔法船')
VEHICLE_META_RE = re.compile(r'(?m)^\s*(?:大型|巨型|超巨型)\s*(?:载具|车辆|物件|物体|运载工具)')
VEHICLE_COMPONENT_HEADING_RE = re.compile(r'^(?:操纵|移动|动作|武器|船壳|容量|乘员数量|载货量)')

# Standalone untagged block signatures used to promote untagged Word/prose lines into candidates.
_STANDALONE_SIZES = '|'.join((*CREATURE_SIZES, '大形', '无与伦比的'))
_STANDALONE_TYPES = '|'.join((*CREATURE_TYPES, '天界', '不死'))
STANDALONE_CREATURE_META_RE = re.compile(
    rf'(?m)^[ \t]*(?:(?:{_STANDALONE_SIZES})(?:或(?:{"|".join(CREATURE_SIZES)}))?|\d+\s*级)\s*'
    rf'(?:{_STANDALONE_TYPES})[^\n]{{0,120}}$'
)
STANDALONE_STATS_RE = re.compile(
    r'(?:护甲等级|(?<![A-Za-z])AC(?=\s|[:：]|\d)).{0,100}(?:生命值|(?<![A-Za-z])HP(?=\s|[:：]|\d))',
    re.S | re.I,
)
STANDALONE_ITEM_META_RE = re.compile(
    r'(?m)^[ \t]*(?:奇物|药水|卷轴|法杖|魔杖|魔棒|戒指|武器|护甲|盔甲|盾牌|'
    + '|'.join(ITEM_WEAPON_SUBKINDS)
    + r')[^\n]{0,65}[，,、 ](?:' + '|'.join(RARITIES) + r')[^\n]{0,100}$'
)
STANDALONE_SPELL_META_RE = re.compile(r'(?m)^[ \t]*' + SPELL_DESCRIPTOR_RE.pattern + r'[^\n]{0,100}$')
STANDALONE_SPELL_CONFIRM_RE = re.compile(
    r'施法时间.{0,250}(?:施法距离|射程|距离).{0,250}(?:法术成分|成分)',
    re.S,
)
STANDALONE_POISON_RE = re.compile(
    r'(?m)^[ \t]*([^\n。]{1,130}[（(](?:' + '|'.join(POISON_MODES) + r')[）)])。'
)
QUICKREF_ATTR_KINDS = {'item': 'magic_item', 'monster': 'creature', 'spell': 'spell'}


def matches_facility_descriptor(after_title: str) -> bool:
    return bool(FACILITY_DESCRIPTOR_RE.match(after_title))


def matches_vehicle_layout(name: str, after_title: str, intro: str, context: str) -> bool:
    if VEHICLE_COMPONENT_HEADING_RE.match(name):
        return False
    ship_summary = bool(
        re.search(r'载货量\s*Cargo\s*[:：]', intro) and re.search(r'船员\s*Crew\s*[:：]', intro)
    )
    if ship_summary:
        return True
    if not VEHICLE_CONTEXT_RE.search(context):
        return False
    vehicle_meta = bool(VEHICLE_META_RE.search(after_title[:250]))
    vehicle_capacity = bool(
        re.search(r'(?:生物容量|生物载量|乘员数量|乘员容量)\s*(?:[A-Za-z ]+)?[:：]', intro[:900])
    )
    return vehicle_meta or vehicle_capacity


def matches_hazard_descriptor(after_title: str) -> bool:
    return bool(HAZARD_DESCRIPTOR_RE.match(after_title.lstrip()))


def matches_trap_descriptor(name: str, after_title: str) -> bool:
    return bool(TRAP_TITLE_SUFFIX_RE.search(name) or TRAP_DESCRIPTOR_RE.match(after_title.lstrip()))


def matches_spell_fields(intro: str) -> bool:
    return all(pattern.search(intro) for pattern in SPELL_FIELD_LABELS)


def matches_stat_fields(intro: str) -> bool:
    return bool(STAT_BLOCK_RE.search(intro) and STAT_CONFIRM_RE.search(intro))


def matches_item_descriptor(descriptor: str) -> bool:
    return bool(ITEM_DESCRIPTOR_RE.match(descriptor))


def matches_feat_descriptor(descriptor: str) -> bool:
    return bool(FEAT_DESCRIPTOR_RE.match(descriptor))


def matches_background_fields(intro: str, primary: bool, context: str) -> bool:
    return all(f.search(intro) for f in BACKGROUND_FIELD_LABELS) and (primary or '背景' in context)


def matches_species_fields(intro: str, primary: bool, context: str) -> bool:
    return (
        bool(re.search(r'生物类型|属性值加成', intro))
        and '体型' in intro
        and '速度' in intro
        and (primary or '种族' in context or '物种' in context)
    )


def matches_poison_descriptor(descriptor: str) -> bool:
    return bool(POISON_DESCRIPTOR_RE.match(descriptor))


def clean_entry_name(name: str, kind: str) -> str:
    """Strip type labels printed as a trailing suffix of the heading when needed."""
    return TRAP_TITLE_SUFFIX_RE.sub('', name) if kind == 'trap' else name


# ---------------------------------------------------------------------------------------------------
# Spanned field extraction (labeled lines + type-specific descriptor/header lines).

FIELD_LABELS = {
    'casting_time': ['施法时间'],
    'range': ['施法距离', '射程'],
    'components': ['法术成分', '成分'],
    'duration': ['持续时间'],
    'prerequisite': ['先决条件', '先决', '先决要求', '前置条件'],
    'ability_scores': ['属性值', '属性值加成', '属性提升'],
    'skills': ['技能熟练', '技能熟练项'],
    'tools': ['工具熟练', '工具熟练项'],
    'granted_feat': ['专长'],
    'starting_equipment': ['装备'],
    'creature_type': ['生物类型'],
    'size': ['体型'],
    'speed': ['速度'],
    'languages': ['语言'],
    'senses': ['感官'],
    'resistances': ['伤害抗性'],
    'immunities': ['伤害免疫'],
    'condition_immunities': ['状态免疫'],
    'ac': ['护甲等级', 'AC'],
    'hp': ['生命值', 'HP'],
    'cr': ['挑战等级', '挑战', 'CR'],
}
LABEL_TO_FIELD = {label: field for field, labels in FIELD_LABELS.items() for label in labels}
LABEL_RE = re.compile(
    r'(?<![\u3400-\u9fffA-Za-z])('
    + '|'.join(re.escape(x) for x in sorted(LABEL_TO_FIELD, key=len, reverse=True))
    + r')(?:\s*[A-Za-z][A-Za-z ]{0,35})?\s*[:：]\s*'
)

ALLOWED_LABELED_FIELDS: dict[str, set[str]] = {
    'creature': {
        'ac', 'hp', 'cr', 'speed', 'languages', 'senses', 'skills',
        'resistances', 'immunities', 'condition_immunities',
        'strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma',
    },
    'spell': {'casting_time', 'range', 'components', 'duration'},
    'feat': {'prerequisite'},
    'background': {'ability_scores', 'skills', 'tools', 'granted_feat', 'starting_equipment', 'languages'},
    'species': {'ability_scores', 'creature_type', 'size', 'speed', 'languages', 'senses', 'resistances', 'immunities'},
    'class_feature': {'prerequisite'},
    'class': set(),
    'subclass': {'prerequisite'},
    'vehicle': {'speed'},
    'facility': set(),
    'section': set(),
    'lore': set(),
    'magic_item': set(),
    'ability': set(),
    'skill': set(),
}

_CREATURE_STAT_BODY_RE = re.compile(
    r'(?m)^[ \t]*(?:特质|特性|动作|行动|附赠动作|反应|传奇动作|神话动作|巢穴动作)(?:\s*[A-Za-z][A-Za-z .]*)?[ \t]*[:：]?[ \t]*$',
    re.I,
)
CREATURE_NUMERIC_LINES = (
    ('ac', r'AC|护甲等级'),
    ('hp', r'HP|生命值'),
    ('cr', r'CR|挑战等级|挑战'),
)
CREATURE_TRAIT_LINES = (
    ('speed', '速度'),
    ('languages', '语言'),
    ('senses', '感官'),
    ('skills', r'技能(?:熟练项?|加值)?'),
    ('resistances', '伤害抗性'),
    ('immunities', '伤害免疫'),
    ('condition_immunities', '状态免疫'),
)
CREATURE_ABILITIES = (
    ('strength', '力量'),
    ('dexterity', '敏捷'),
    ('constitution', '体质'),
    ('intelligence', '智力'),
    ('wisdom', '感知'),
    ('charisma', '魅力'),
)


def labeled_fields(text: str, kind: str) -> dict[str, list[tuple[str, int, int]]]:
    """Extract colon-labeled field lines and statblock attribute lines with relative spans."""
    result: dict[str, list[tuple[str, int, int]]] = {}
    if kind == 'creature':
        body_match = _CREATURE_STAT_BODY_RE.search(text)
        scan = text[:body_match.start()] if body_match else text
    else:
        scan = text
    matches = list(LABEL_RE.finditer(scan))
    for i, m in enumerate(matches):
        next_match = matches[i + 1].start() if i + 1 < len(matches) else len(scan)
        newline = scan.find('\n', m.end())
        end = min(next_match, newline if newline != -1 else len(scan))
        value = compact(scan[m.end():end])
        if value and len(value) <= 600:
            result.setdefault(LABEL_TO_FIELD[m.group(1)], []).append((value, m.start(), end))

    if kind == 'creature':
        for field, labels in CREATURE_NUMERIC_LINES:
            if field in result:
                continue
            m = re.search(r'(?:^|[\s\n])(?:' + labels + r')\s*[:：]?\s*(\d+(?:/\d+)?[^\n]{0,65})', scan, re.I)
            if m:
                result.setdefault(field, []).append((compact(m[1]), m.start(), m.end()))
        for field, label in CREATURE_TRAIT_LINES:
            if field in result:
                continue
            m = re.search(r'(?:^|[\s\n])(?:' + label + r')\s*[:：]?\s*([^\n]{1,250})', scan[:1800])
            if m:
                result.setdefault(field, []).append((compact(m[1]), m.start(), m.end()))
        for field, label in CREATURE_ABILITIES:
            m = re.search(label + r'\s*[:：]?\s*(\d{1,2})', scan[:1200])
            if m:
                result.setdefault(field, []).append((m[1], m.start(), m.end()))

    if kind in ALLOWED_LABELED_FIELDS:
        allowed = ALLOWED_LABELED_FIELDS[kind]
        result = {k: v for k, v in result.items() if k in allowed}
    return result


def extract_entry_fields(text: str, kind: str, title_offset: int) -> dict[str, list[tuple[str, int, int]]]:
    """Extract all spanned (value, start, end) fields for one classified entry."""
    fields = labeled_fields(text, kind)
    raw_descriptor = text[title_offset:]

    if kind == 'feat':
        m = FEAT_DESCRIPTOR_RE.match(raw_descriptor)
        if m:
            fields['feat_category'] = [(m[1], title_offset + m.start(), title_offset + m.end())]
    elif kind == 'spell':
        m = SPELL_DESCRIPTOR_RE.search(raw_descriptor[:250])
        if m:
            span = (title_offset + m.start(), title_offset + m.end())
            fields['spell_level'] = [(m[1], *span)]
            fields['school'] = [(m[2], *span)]
        for value, a, b in fields.get('casting_time', []):
            fields.setdefault('casting_time_detail', []).append((value, a, b))
            if '仪式' in value:
                fields.setdefault('ritual', []).append(('是', a, b))
        for value, a, b in fields.get('duration', []):
            fields.setdefault('concentration', []).append(('是' if value.startswith('专注') else '否', a, b))
    elif kind == 'magic_item':
        m = ITEM_EXTRACT_RE.match(raw_descriptor[:250])
        if m:
            span = (title_offset, title_offset + m.end())
            fields['item_type'] = [(m[1], *span)]
            fields['rarity'] = [(m[2], *span)]
        attune = ATTUNEMENT_RE.search(raw_descriptor[:250])
        if attune:
            required = not re.search(r'不需|无需', attune[1])
            span = (title_offset + attune.start(), title_offset + attune.end())
            fields['attunement'] = [('是' if required else '否', *span)]
            if required:
                fields['attunement_requirements'] = [(attune[1], *span)]
    elif kind == 'creature':
        m = CREATURE_HEADER_RE.search(text[:500])
        if m:
            s1 = _SIZE_SYNONYMS.get(m[1], m[1])
            s2 = _SIZE_SYNONYMS.get(m[2], m[2]) if m[2] else ''
            t1 = _CREATURE_TYPE_SYNONYMS.get(m[3], m[3])
            t2 = _CREATURE_TYPE_SYNONYMS.get(m[4], m[4]) if m[4] else ''
            size_val = f'{s1}/{s2}' if s2 and s2 != s1 else s1
            type_val = f'{t1}/{t2}' if t2 and t2 != t1 else t1
            fields['size'] = [(size_val, m.start(), m.end())]
            fields['creature_type'] = [(type_val, m.start(), m.end())]

    return fields
