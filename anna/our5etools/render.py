"""Structured rendering for our5etools JSON blocks, inline tags, and entry cards.

Converts 5etools entry trees and ``{@...}`` inline markup into sanitized HTML
following Anna's presentation conventions (``.parameters``, ``button.ref``,
tables, and reader-safe semantic elements). Entity links resolve strictly within
the our5etools lookup index and never fall back to the 5echm catalog.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import html
from html.parser import HTMLParser
import math
from pathlib import PurePosixPath
import re
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlsplit

COLLECTION_ID = '5etools'
MEDIA_PREFIX = '/our5etools-media/'
REMOTE_PATH_PREFIX = '/__remote/'
REMOTE_IMG_URLS: tuple[str, ...] = (
    'https://5e.kiwee.top/img/',
    'https://raw.githubusercontent.com/5etools-mirror-3/5etools-img/main/',
)
SAFE_REMOTE_MEDIA_EXTS: frozenset[str] = frozenset({
    '.png', '.webp', '.jpg', '.jpeg', '.gif', '.svg', '.avif',
    '.opus', '.ogg', '.mp3', '.wav', '.m4a',
})

# All entity tags resolved against the our5etools lookup index.
KNOWN_ENTITY_TAGS: frozenset[str] = frozenset({
    'spell', 'creature', 'item', 'condition', 'status', 'action', 'skill',
    'feat', 'race', 'background', 'class', 'subclass', 'classFeature',
    'subclassFeature', 'optfeature', 'reward', 'deity', 'vehicle',
    'vehupgrade', 'object', 'facility', 'trap', 'hazard', 'disease',
    'psionic', 'deck', 'card', 'recipe', 'crochet', 'cult', 'boon',
    'charoption', 'language', 'variantrule', 'quickref', 'table',
    'itemProperty', 'itemMastery', 'legroup', 'sense', 'creatureFluff',
    'raceFluff', 'crochetFluff', 'cite',
})

# Formatting, mechanic, navigation, and media inline tags.
KNOWN_NON_ENTITY_TAGS: frozenset[str] = frozenset({
    'book', 'adventure', 'filter',
    'b', 'bold', 'i', 'italic',
    'u', 'underline', 'u2', 'underlineDouble',
    's', 'strike', 's2', 'strikeDouble',
    'sup', 'sub', 'code', 'kbd', 'note', 'tip', 'highlight',
    'color', 'style', 'font',
    'dice', 'autodice', 'damage', 'scaledamage', 'scaledice',
    'hit', 'dc', 'd20', 'initiative', 'chance', 'coinflip', 'recharge',
    'atk', 'atkr', 'h', 'm', 'hom',
    'actSave', 'actSaveFail', 'actSaveFailBy', 'actSaveSuccess',
    'actSaveSuccessOrFail', 'actTrigger', 'actResponse',
    'ability', 'skillCheck', 'savingThrow',
    'hitYourSpellAttack', 'dcYourSpellSave',
    'area', 'unit', 'link', '5etools', '5etoolsImg', '5etoolsAudio',
    'homebrew', 'loader', 'footnote',
    'comic', 'comicH1', 'comicH2', 'comicH3', 'comicH4', 'comicNote',
})

KNOWN_TAGS: frozenset[str] = KNOWN_ENTITY_TAGS | KNOWN_NON_ENTITY_TAGS

# All 40+ 5etools structured block types handled directly by render_blocks.
KNOWN_BLOCK_TYPES: frozenset[str] = frozenset({
    'entries', 'section', 'list', 'item', 'itemSub', 'itemSpell',
    'table', 'tableGroup', 'row', 'cell', 'cellHeader',
    'inset', 'insetReadaloud', 'quote',
    'statblock', 'statblockInline',
    'image', 'gallery', 'options',
    'variant', 'variantInner', 'variantSub',
    'abilityDc', 'abilityAttackMod', 'abilityGeneric',
    'spellcasting',
    'refClassFeature', 'refSubclassFeature', 'refOptionalfeature', 'refFeat',
    'optfeature', 'flowchart', 'flowBlock',
    'homebrew', 'wrapper', 'inline', 'inlineBlock',
    'dice', 'bonus', 'bonusSpeed', 'link', 'hr',
    'ingredient', 'itemUses',
})

# Lookup aliases so tags match either 5etools tag names, raw_kinds, or Anna types.
TAG_LOOKUP_ALIASES: dict[str, tuple[str, ...]] = {
    'spell': ('spell',),
    'creature': ('creature', 'monster'),
    'item': ('item', 'baseitem', 'magicvariant', 'equipment', 'magic_item', 'poison'),
    'condition': ('condition',),
    'status': ('status', 'condition'),
    'action': ('action',),
    'skill': ('skill',),
    'feat': ('feat',),
    'race': ('race', 'subrace', 'species'),
    'background': ('background',),
    'class': ('class',),
    'subclass': ('subclass',),
    'classFeature': ('classFeature', 'classfeature', 'class_feature'),
    'subclassFeature': ('subclassFeature', 'subclassfeature', 'class_feature'),
    'optfeature': ('optfeature', 'optionalfeature', 'class_feature'),
    'reward': ('reward',),
    'deity': ('deity',),
    'vehicle': ('vehicle',),
    'vehupgrade': ('vehupgrade', 'vehicleUpgrade', 'vehicleupgrade', 'vehicle'),
    'object': ('object',),
    'facility': ('facility',),
    'trap': ('trap',),
    'hazard': ('hazard',),
    'disease': ('disease',),
    'psionic': ('psionic',),
    'deck': ('deck',),
    'card': ('card', 'deck'),
    'recipe': ('recipe',),
    'crochet': ('crochet', 'crochetPattern', 'crochetpattern', 'recipe'),
    'cult': ('cult', 'reward'),
    'boon': ('boon', 'reward'),
    'charoption': ('charoption', 'reward'),
    'language': ('language',),
    'variantrule': ('variantrule', 'rule'),
    'quickref': ('quickref', 'variantrule', 'rule', 'action', 'condition'),
    'table': ('table',),
    'itemProperty': ('itemProperty', 'itemproperty', 'rule'),
    'itemMastery': ('itemMastery', 'itemmastery', 'rule'),
    'legroup': ('legroup', 'legendaryGroup', 'legendarygroup', 'rule'),
    'sense': ('sense', 'rule'),
    'creatureFluff': ('creatureFluff', 'monsterFluff', 'creature', 'monster'),
    'raceFluff': ('raceFluff', 'race', 'species'),
    'crochetFluff': ('crochetFluff', 'crochet', 'crochetPattern'),
    'cite': ('cite', 'citation', 'rule'),
}

# Mapping from 5etools {@filter ...|page|...} page names to Anna entry types.
FILTER_PAGE_TO_TYPE: dict[str, str] = {
    'spells': 'spell',
    'bestiary': 'creature',
    'items': 'magic_item',
    'feats': 'feat',
    'races': 'species',
    'backgrounds': 'background',
    'classes': 'class',
    'optionalfeatures': 'class_feature',
    'rewards': 'reward',
    'deities': 'deity',
    'vehicles': 'vehicle',
    'bastions': 'facility',
    'decks': 'deck',
    'recipes': 'recipe',
    'tables': 'table',
    'conditionsdiseases': 'condition',
    'actions': 'action',
    'languages': 'language',
    'variantrules': 'rule',
    'traps': 'trap',
    'trapshazards': 'trap',
    'psionics': 'psionic',
    'objects': 'object',
    'cultsboons': 'reward',
    'charcreationoptions': 'reward',
    'books': 'book',
    'adventures': 'book',
}

# Page-scoped filter key mappings to Anna facet names (emitted as f_<facet> / not_f_<facet>).
FILTER_PAGE_KEY_MAP: dict[str, dict[str, str]] = {
    'spells': {
        'level': 'spell_level',
        'school': 'school',
        'class': 'spell_classes',
        'casting time': 'casting_time',
        'cast time': 'casting_time',
        'time': 'casting_time',
        'concentration': 'concentration',
        'ritual': 'ritual',
    },
    'items': {
        'rarity': 'rarity',
        'type': 'item_type',
    },
    'bestiary': {
        'type': 'creature_type',
        'size': 'size',
    },
    'feats': {
        'category': 'feat_category',
    },
    'optionalfeatures': {
        'feature type': 'feature_kind',
    },
    'psionics': {
        'order': 'psionic_order',
        'type': 'psionic_type',
    },
    'deities': {
        'pantheon': 'pantheon',
        'alignment': 'alignment',
        'domains': 'domains',
        'domain': 'domains',
    },
    'bastions': {
        'level': 'level',
        'type': 'facility_kind',
    },
    'races': {
        'size': 'size',
    },
    'rewards': {
        'type': 'reward_kind',
    },
}

# Fallback global filter key map for generic/unscoped pages (maps to Anna facet names).
FILTER_KEY_MAP: dict[str, str] = {
    'level': 'spell_level',
    'school': 'school',
    'class': 'spell_classes',
    'casting time': 'casting_time',
    'cast time': 'casting_time',
    'time': 'casting_time',
    'concentration': 'concentration',
    'ritual': 'ritual',
    'rarity': 'rarity',
    'size': 'size',
    'category': 'feat_category',
    'feature type': 'feature_kind',
    'order': 'psionic_order',
    'pantheon': 'pantheon',
    'alignment': 'alignment',
    'domains': 'domains',
    'domain': 'domains',
}

# Facet value normalization tables matching anna.our5etools.types.ORDERS exactly.
_FILTER_SCHOOL_ZH: dict[str, str] = {
    'a': '防护', 'abjuration': '防护',
    'c': '咒法', 'conjuration': '咒法',
    'd': '预言', 'divination': '预言',
    'e': '惑控', 'enchantment': '惑控',
    'v': '塑能', 'evocation': '塑能',
    'i': '幻术', 'illusion': '幻术',
    'n': '死灵', 'necromancy': '死灵',
    't': '变化', 'transmutation': '变化',
    'p': '灵能', 'psionic': '灵能',
}

_FILTER_CASTING_TIME_ZH: dict[str, str] = {
    'action': '动作', '动作': '动作',
    'bonus': '附赠动作', 'bonus action': '附赠动作', '附赠动作': '附赠动作',
    'reaction': '反应', '反应': '反应',
    'other': '其他', '其他': '其他',
}

_FILTER_RARITY_ZH: dict[str, str] = {
    'common': '普通', 'uncommon': '非普通', 'rare': '珍稀',
    'very rare': '极珍稀', 'legendary': '传说', 'artifact': '神器',
    'varies': '多种稀有度', 'unknown (magic)': '未知（魔法）',
    'unknown': '未知', 'none': '无',
}

_FILTER_CREATURE_TYPE_ZH: dict[str, str] = {
    'aberration': '异怪', 'beast': '野兽', 'celestial': '天族',
    'construct': '构装', 'dragon': '龙类', 'elemental': '元素',
    'fey': '妖精', 'fiend': '邪魔', 'giant': '巨人', 'humanoid': '类人',
    'monstrosity': '怪兽', 'ooze': '泥怪', 'plant': '植物', 'undead': '亡灵',
    '龙': '龙类',
}

_FILTER_SIZE_ZH: dict[str, str] = {
    'f': '超微型', 'fine': '超微型',
    'd': '微小型', 'diminutive': '微小型',
    't': '微型', 'tiny': '微型',
    's': '小型', 'small': '小型',
    'm': '中型', 'medium': '中型',
    'l': '大型', 'large': '大型',
    'h': '巨型', 'huge': '巨型',
    'g': '超巨型', 'gargantuan': '超巨型',
    'c': '伟岸', 'colossal': '伟岸',
    'v': '不定', 'varies': '不定',
}

_FILTER_FEAT_CATEGORY_ZH: dict[str, str] = {
    'o': '起源', 'g': '通用', 'fs': '战斗风格',
    'fs:p': '战斗风格（圣武士）', 'fs:r': '战斗风格（游侠）',
    'eb': '传奇恩惠', 'd': '龙纹', 'dg': '黑暗赠礼',
}

_FILTER_PSIONIC_TYPE_ZH: dict[str, str] = {
    't': '天赋', 'talent': '天赋', 'd': '训练', 'discipline': '训练',
}

_FILTER_FACILITY_KIND_ZH: dict[str, str] = {
    'basic': '基础设施', 'special': '特殊设施',
}

_FILTER_OPT_FEATURE_TYPE_ZH: dict[str, str] = {
    'AI': '奇械师注法', 'ED': '法门', 'EI': '魔能祈唤', 'MM': '超魔法',
    'MV': '战技', 'MV:B': '战技', 'MV:C2-UA': '战技（骑兵UA）',
    'AS': '奥术射击', 'AS:V1-UA': '奥术射击（V1 UA）', 'AS:V2-UA': '奥术射击（V2 UA）',
    'FS:F': '战斗风格（战士）', 'FS:B': '战斗风格（吟游诗人）',
    'FS:P': '战斗风格（圣武士）', 'FS:R': '战斗风格（游侠）',
    'PB': '魔契恩泽', 'OR': '真名共鸣', 'RN': '符文骑士符文',
    'AF': '炼金配方', 'TT': '旅者技艺', 'RP': '声望特权', 'OTH': '其他',
}

_FILTER_CLASS_NAME_ZH: dict[str, str] = {
    'artificer': '奇械师', 'barbarian': '野蛮人', 'bard': '吟游诗人',
    'cleric': '牧师', 'druid': '德鲁伊', 'fighter': '战士', 'monk': '武僧',
    'mystic': '秘术师', 'paladin': '圣武士', 'ranger': '游侠', 'rogue': '游荡者',
    'sorcerer': '术士', 'warlock': '魔契师', 'wizard': '法师',
    'blood hunter': '血猎', 'sidekick': '协力者', '邪术师': '魔契师',
}

_FILTER_ITEM_TYPE_ZH: dict[str, str] = {
    # 5etools item type codes -> types.py primary item_type facet values
    'm': '武器', 'r': '武器',
    'la': '护甲', 'ma': '护甲', 'ha': '护甲',
    's': '盾牌', 'a': '弹药', 'af': '弹药',
    'p': '药水', 'sc': '卷轴', 'rg': '戒指', 'rd': '权杖', 'wd': '魔杖',
    'scf': '法器', 'g': '冒险用品', 't': '工具', 'at': '工匠工具',
    'gs': '赌具', 'ins': '乐器', 'fd': '食物和饮品', 'mnt': '乘骑',
    'tah': '鞍具和挽具', 'veh': '载具', 'shp': '载具', 'air': '载具', 'spc': '载具',
    'tg': '贸易货物', 'tb': '贸易货物',
    '$': '宝藏', '$a': '宝藏', '$c': '宝藏', '$g': '宝藏',
    'exp': '爆炸物', 'oth': '其他', 'gv': '通用变体',
    # English item type names
    'potion': '药水', 'scroll': '卷轴', 'ring': '戒指', 'rod': '权杖',
    'wand': '魔杖', 'staff': '法杖', 'wondrous item': '奇物',
    'weapon': '武器', 'melee weapon': '武器', 'ranged weapon': '武器',
    'simple weapon': '武器', 'martial weapon': '武器',
    'armor': '护甲', 'light armor': '护甲', 'medium armor': '护甲', 'heavy armor': '护甲',
    'shield': '盾牌', 'ammunition': '弹药', 'spellcasting focus': '法器',
    'adventuring gear': '冒险用品', 'tool': '工具', "artisan's tools": '工匠工具',
    'gaming set': '赌具', 'instrument': '乐器', 'mount': '乘骑', 'vehicle': '载具',
    # Chinese weapon/armor subtypes -> primary item_type facet values
    '简易武器': '武器', '军用武器': '武器', '近战武器': '武器', '远程武器': '武器',
    '轻甲': '护甲', '中甲': '护甲', '重甲': '护甲',
    '施法法器': '法器', '冒险装备': '冒险用品', '坐骑': '乘骑',
}

_FILTER_REWARD_KIND_ZH: dict[str, str] = {
    'blessing': '祝福', 'boon': '恩赐', 'charm': '护咒', 'curse': '诅咒',
    'draconic gift': '龙族赠礼', 'fragment of suffering': '痛苦碎片',
    'inhabitation': '附体', 'piety trait': '虔信特质', 'other': '其他',
}

_FILTER_BOOL_VAL: dict[str, str] = {
    '1': 'true', 'true': 'true', 'yes': 'true', '是': 'true',
    '0': 'false', 'false': 'false', 'no': 'false', '否': 'false',
}

_FILTER_MODE_SUFFIX_RE = re.compile(r'=(?:sand|sor|snot)$', re.IGNORECASE)
_CR_RANGE_RE = re.compile(r'^\[([^\]]+)\]$')


def _parse_cr_number(raw: str) -> str | None:
    cleaned = raw.strip().lstrip('&!').strip()
    if not cleaned:
        return None
    if '/' in cleaned:
        parts = cleaned.split('/', 1)
        try:
            num = float(parts[0].strip())
            den = float(parts[1].strip())
            if den == 0:
                return None
            val = num / den
        except ValueError:
            return None
    else:
        try:
            val = float(cleaned)
        except ValueError:
            return None
    if val == int(val):
        return str(int(val))
    return f'{val:g}'


def _normalize_filter_facet_value(facet: str, raw_val: str) -> str:
    val = raw_val.strip()
    if not val:
        return ''
    low = val.lower()
    if facet == 'school':
        return _FILTER_SCHOOL_ZH.get(low, val)
    if facet == 'spell_classes':
        return _FILTER_CLASS_NAME_ZH.get(low, val)
    if facet == 'casting_time':
        return _FILTER_CASTING_TIME_ZH.get(low, val)
    if facet in ('concentration', 'ritual'):
        return _FILTER_BOOL_VAL.get(low, val)
    if facet == 'rarity':
        return _FILTER_RARITY_ZH.get(low, val)
    if facet == 'item_type':
        return _FILTER_ITEM_TYPE_ZH.get(low, val)
    if facet == 'creature_type':
        return _FILTER_CREATURE_TYPE_ZH.get(low, val)
    if facet == 'size':
        return _FILTER_SIZE_ZH.get(low, val)
    if facet == 'feat_category':
        return _FILTER_FEAT_CATEGORY_ZH.get(low, val)
    if facet == 'feature_kind':
        return _FILTER_OPT_FEATURE_TYPE_ZH.get(val.upper(), val)
    if facet == 'psionic_type':
        return _FILTER_PSIONIC_TYPE_ZH.get(low, val)
    if facet == 'facility_kind':
        return _FILTER_FACILITY_KIND_ZH.get(low, val)
    if facet == 'reward_kind':
        return _FILTER_REWARD_KIND_ZH.get(low, val)
    return val

# Safe CSS colors for {@color text|color}.
_HEX_COLOR_RE = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$')
_NAMED_COLORS: frozenset[str] = frozenset({
    'red', 'green', 'blue', 'orange', 'purple', 'gold', 'gray', 'grey',
    'black', 'white', 'brown', 'cyan', 'magenta', 'pink', 'yellow', 'teal',
    'navy', 'maroon', 'olive', 'silver', 'crimson', 'indigo', 'violet',
    'darkred', 'darkgreen', 'darkblue', 'darkorange', 'darkviolet',
    'goldenrod', 'steelblue', 'forestgreen', 'firebrick', 'royalblue',
})

# Safe style aliases for {@style text|styleName}.
_STYLE_CLASS_MAP: dict[str, str] = {
    'small-caps': 'o5e-small-caps',
    'smallcaps': 'o5e-small-caps',
    '小型大写': 'o5e-small-caps',
    'bold': 'o5e-bold',
    'italic': 'o5e-italic',
    'underline': 'o5e-underline',
    'strike': 'o5e-strike',
    'uppercase': 'o5e-uppercase',
    'lowercase': 'o5e-lowercase',
    'capitalize': 'o5e-capitalize',
    'monospace': 'o5e-mono',
    'sans-serif': 'o5e-sans',
    'serif': 'o5e-serif',
    'help-subtle': 'o5e-help-subtle',
}

# Domain translation tables (aligned with our5etools parser.js Chinese labels).
ABILITY_ABV_ZH: dict[str, str] = {
    'str': '力量', 'strength': '力量',
    'dex': '敏捷', 'dexterity': '敏捷',
    'con': '体质', 'constitution': '体质',
    'int': '智力', 'intelligence': '智力',
    'wis': '感知', 'wisdom': '感知',
    'cha': '魅力', 'charisma': '魅力',
    'special': '特殊', 'spellcasting': '施法',
}

ABILITY_ORDER: tuple[str, ...] = ('str', 'dex', 'con', 'int', 'wis', 'cha')

SIZE_ABV_ZH: dict[str, str] = {
    'F': '超微型', 'D': '微小型', 'T': '微型', 'S': '小型', 'M': '中型',
    'L': '大型', 'H': '巨型', 'G': '超巨型', 'C': '伟岸', 'V': '不定',
}

CREATURE_TYPE_ZH: dict[str, str] = {
    'aberration': '异怪', 'beast': '野兽', 'celestial': '天界生物',
    'construct': '构装体', 'dragon': '龙类', 'elemental': '元素生物',
    'fey': '精类', 'fiend': '邪魔', 'giant': '巨人', 'humanoid': '类人生物',
    'monstrosity': '怪兽', 'ooze': '泥怪', 'plant': '植物', 'undead': '亡灵',
}

ALIGNMENT_ABV_ZH: dict[str, str] = {
    'L': '守序', 'N': '中立', 'NX': '中立', 'NY': '中立', 'C': '混乱',
    'G': '善良', 'E': '邪恶', 'U': '无阵营', 'A': '任意阵营',
}

SPELL_SCHOOL_ZH: dict[str, str] = {
    'A': '防护', 'C': '咒法', 'D': '预言', 'E': '惑控',
    'V': '塑能', 'I': '幻术', 'N': '死灵', 'T': '变化', 'P': '灵能',
}

TIME_UNIT_ZH: dict[str, str] = {
    'action': '动作', 'bonus': '附赠动作', 'reaction': '反应',
    'round': '轮', 'minute': '分钟', 'hour': '小时',
    'day': '天', 'special': '特殊', 'free': '自由动作',
}

RANGE_TYPE_ZH: dict[str, str] = {
    'point': '', 'line': '线状', 'cube': '立方', 'cone': '锥状',
    'radius': '半径', 'sphere': '球状', 'hemisphere': '半球状',
    'cylinder': '柱状', 'emanation': '光环', 'special': '特殊',
}

DISTANCE_TYPE_ZH: dict[str, str] = {
    'self': '自身', 'touch': '触及', 'feet': '尺', 'miles': '英里',
    'sight': '视野内', 'unlimited': '无限', 'plane': '同位面',
    'special': '特殊',
}

RARITY_ZH: dict[str, str] = {
    'none': '无', 'common': '普通', 'uncommon': '非普通', 'rare': '珍稀',
    'very rare': '极珍稀', 'legendary': '传说', 'artifact': '神器',
    'unknown': '未知', 'unknown (magic)': '未知（魔法）', 'varies': '不定',
}

ITEM_TYPE_ZH: dict[str, str] = {
    'M': '近战武器', 'R': '远程武器', 'LA': '轻甲', 'MA': '中甲',
    'HA': '重甲', 'S': '盾牌', 'A': '弹药', 'AF': '枪械弹药',
    'P': '药水', 'SC': '卷轴', 'RD': '权杖', 'RG': '戒指', 'WD': '魔杖',
    'SCF': '施法法器', 'G': '冒险装备', 'T': '工具', 'AT': '工匠工具',
    'GS': '赌具', 'INS': '乐器', 'MNT': '坐骑', 'TAH': '鞍具与马具',
    'TG': '贸易商品', 'SHP': '载具（水运）', 'VEH': '载具（陆运）',
    'AIR': '载具（空运）', 'SPC': '载具（星界）', 'FD': '食物与饮品',
    'EXP': '爆炸物', 'GV': '通用变体', 'OTH': '其他',
    '$': '宝藏', '$A': '艺术品', '$C': '货币', '$G': '宝石',
}

DAMAGE_TYPE_ZH: dict[str, str] = {
    'A': '强酸', 'B': '钝击', 'C': '寒冷', 'F': '火焰', 'O': '力场',
    'L': '闪电', 'N': '暗蚀', 'P': '穿刺', 'I': '毒素', 'Y': '心灵',
    'R': '光耀', 'S': '挥砍', 'T': '雷鸣',
}

WEAPON_PROP_ZH: dict[str, str] = {
    '2H': '双手', 'A': '弹药', 'AF': '弹药（枪械）', 'BF': '连发',
    'F': '灵巧', 'H': '重型', 'L': '轻型', 'LD': '装填', 'R': '触及',
    'RLD': '换弹', 'S': '特殊', 'T': '投掷', 'V': '两用', 'M': '军用',
}

VEHICLE_TYPE_ZH: dict[str, str] = {
    'SHIP': '船只', 'SPELLJAMMER': '魔法船', 'INFWAR': '炼狱战争机器',
    'CREATURE': '生物坐骑', 'OBJECT': '载具物件', 'ELEMENTAL_AIRSHIP': '元素飞艇',
}

CR_XP: dict[str, int] = {
    '0': 10, '1/8': 25, '1/4': 50, '1/2': 100,
    '1': 200, '2': 450, '3': 700, '4': 1100, '5': 1800,
    '6': 2300, '7': 2900, '8': 3900, '9': 5000, '10': 5900,
    '11': 7200, '12': 8400, '13': 10000, '14': 11500, '15': 13000,
    '16': 15000, '17': 18000, '18': 20000, '19': 22000, '20': 25000,
    '21': 33000, '22': 41000, '23': 50000, '24': 62000, '25': 75000,
    '26': 90000, '27': 105000, '28': 120000, '29': 135000, '30': 155000,
}

COPY_STATUS_ZH: dict[str, str] = {
    'missing_base': '继承基底缺失：部分字段可能不完整',
    'ambiguous_base': '继承基底存在歧义：未能唯一解析源条目',
    'cycle': '继承链存在循环引用：已中止展开',
    'mod_warning': '继承修改项包含未完全应用的规则变更',
}

_TEMPLATE_EXPR_RE = re.compile(r'\{=([^}]+)\}')
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]')
_ORDINAL_ZH: dict[str, str] = {
    '1': '第一次', '2': '第二次', '3': '第三次', '4': '第四次', '5': '第五次',
}


def escape_html(value: Any) -> str:
    """Escape text or attribute values for safe HTML emission."""
    return html.escape(str(value), quote=True)


def sanitize_url(url: str, *, allow_relative: bool = True) -> str | None:
    """Allow only safe ``https://``, ``http://``, or single-slash relative URLs."""
    if not isinstance(url, str):
        return None
    cleaned = _CONTROL_CHARS_RE.sub('', url).strip()
    if not cleaned:
        return None
    if cleaned.startswith(REMOTE_PATH_PREFIX):
        rest = cleaned[len(REMOTE_PATH_PREFIX):].lstrip('/')
        if not rest or rest.startswith('/'):
            return None
        cleaned = f'https://{rest}'
    low = cleaned.lower()
    if low.startswith(('https://', 'http://')):
        return cleaned
    if allow_relative and cleaned.startswith('/') and not cleaned.startswith('//'):
        return cleaned
    return None


def sanitize_media_path(path: str) -> str | None:
    """Normalize a relative media path and reject traversal or absolute paths."""
    if not isinstance(path, str):
        return None
    cleaned = _CONTROL_CHARS_RE.sub('', path).strip().replace('\\', '/')
    cleaned = cleaned.lstrip('/')
    if not cleaned:
        return None
    parts = [p for p in cleaned.split('/') if p and p != '.']
    if not parts or any(p == '..' for p in parts):
        return None
    return '/'.join(parts)


def _media_url(relpath: str) -> str:
    encoded = '/'.join(quote(segment, safe='-._~') for segment in relpath.split('/'))
    return f'{MEDIA_PREFIX}{encoded}'


def normalize_internal_media_path(raw_path: str, prefix: str = 'img/') -> str | None:
    """Normalize an internal 5etools asset path under ``img/`` or ``audio/`` without double prefixing."""
    cleaned = sanitize_media_path(raw_path)
    if not cleaned:
        return None
    if prefix and not cleaned.startswith(prefix):
        return sanitize_media_path(f'{prefix}{cleaned}')
    return cleaned


def internal_image_remote_urls(relpath: str) -> list[str]:
    """Return upstream mirror URLs matching 5etools web.js for a normalized ``img/...`` path."""
    sub = relpath.removeprefix('img/').lstrip('/')
    if not sub:
        return []
    quoted = '/'.join(quote(seg, safe="()'!,;-_~") for seg in sub.split('/'))
    return [f'{base}{quoted}' for base in REMOTE_IMG_URLS]


def remote_media_cache_path(url: str, default_ext: str = '') -> str | None:
    """Return a deterministic ``ext/<sha256[:24]><ext>`` cache key for an allowlisted external URL."""
    safe = sanitize_url(url, allow_relative=False)
    if not safe:
        return None
    suffix = PurePosixPath(urlsplit(safe).path).suffix.lower()
    ext = suffix if suffix in SAFE_REMOTE_MEDIA_EXTS else default_ext
    digest = hashlib.sha256(safe.encode('utf-8')).hexdigest()[:24]
    return f'ext/{digest}{ext}'


def _sanitize_color(color: str) -> str | None:
    cleaned = color.strip()
    if not cleaned:
        return None
    if _HEX_COLOR_RE.match(cleaned):
        return cleaned
    if cleaned.lower() in _NAMED_COLORS:
        return cleaned.lower()
    return None


def _is_our5etools_id(entry_id: str) -> bool:
    """Guard strict isolation: resolved links must point to our5etools entries only."""
    return isinstance(entry_id, str) and entry_id.startswith('o5e_')


class _TextExtractor(HTMLParser):
    """Extract readable plain text from sanitized HTML for search and previews."""

    _BLOCK_TAGS = frozenset({
        'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr',
        'dt', 'dd', 'blockquote', 'aside', 'section', 'header', 'figure',
        'figcaption', 'hr', 'br', 'table', 'caption',
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCK_TAGS:
            self._parts.append('\n')
        elif tag in {'td', 'th'}:
            self._parts.append(' ')

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_TAGS:
            self._parts.append('\n')
        elif tag in {'td', 'th'}:
            self._parts.append(' ')

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def text(self) -> str:
        raw = ''.join(self._parts)
        lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in raw.splitlines()]
        compact = [line for line in lines if line]
        return '\n'.join(compact)


def strip_html_to_text(html_text: str) -> str:
    """Convert rendered HTML into normalized, searchable plain text."""
    if not html_text:
        return ''
    parser = _TextExtractor()
    parser.feed(html_text)
    parser.close()
    return parser.text()


def split_by_tags(text: str) -> list[str]:
    """Split a 5etools string into plain segments and balanced ``{@...}`` / ``{=...}`` tokens."""
    if not text:
        return []
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ''
        if ch == '{' and nxt in ('@', '='):
            if depth == 0:
                if buf:
                    out.append(''.join(buf))
                    buf = []
                buf.append('{')
                buf.append(nxt)
                depth = 1
                i += 2
                continue
            depth += 1
            buf.append('{')
            buf.append(nxt)
            i += 2
            continue
        if ch == '{' and depth > 0:
            depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch == '}' and depth > 0:
            depth -= 1
            buf.append(ch)
            if depth == 0:
                out.append(''.join(buf))
                buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        out.append(''.join(buf))
    return out


def split_tag_by_pipe(text: str) -> list[str]:
    """Split tag payload by ``|`` at brace depth 0 so nested tags keep their pipes."""
    if not text:
        return ['']
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch == '{':
            depth += 1
            buf.append(ch)
        elif ch == '}':
            if depth > 0:
                depth -= 1
            buf.append(ch)
        elif ch == '|' and depth == 0:
            parts.append(''.join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append(''.join(buf))
    return parts


@dataclass(frozen=True)
class ResolveResult:
    """Outcome of resolving one entity reference within our5etools."""

    status: str  # 'resolved' | 'ambiguous' | 'unresolved'
    entry_id: str | None = None
    candidates: tuple[str, ...] = ()
    tag: str = ''
    name: str = ''
    source: str = ''


class LinkResolverProtocol(Protocol):
    """Protocol for resolving our5etools entity, book, and filter references."""

    collection: str

    def resolve_entity(
        self,
        tag: str,
        name: str,
        source: str = '',
        *,
        extra: Mapping[str, str] | None = None,
    ) -> ResolveResult: ...

    def resolve_book(
        self,
        kind: str,
        book_id: str,
        chapter: str = '',
        header: str = '',
    ) -> tuple[str, str, str] | None: ...

    def build_filter_href(self, page: str, filters: Sequence[str]) -> str: ...


class LinkResolver:
    """Strict our5etools-only link resolver wrapping lookup_index and book_anchor_lookup."""

    def __init__(
        self,
        lookup_index: Mapping[tuple[str, ...], Any] | None = None,
        book_anchor_lookup: Mapping[tuple[str, ...], Any] | None = None,
        *,
        collection: str = COLLECTION_ID,
        tag_redirects: Mapping[str, Any] | None = None,
        default_source: str = '',
        entry_index: Mapping[str, Mapping[str, Any]] | None = None,
        source_ids: Mapping[str, str] | None = None,
        remote_img_base: str = '',
    ) -> None:
        self.lookup_index: Mapping[tuple[str, ...], Any] = lookup_index or {}
        self.book_anchor_lookup: Mapping[tuple[str, ...], Any] = book_anchor_lookup or {}
        self.collection: str = collection or COLLECTION_ID
        self.tag_redirects: Mapping[str, Any] = tag_redirects or {}
        self.default_source: str = default_source
        self.entry_index: Mapping[str, Mapping[str, Any]] = entry_index or {}
        self.source_ids: dict[str, str] = {
            str(k).strip().lower(): str(v).strip()
            for k, v in (source_ids or {}).items()
            if str(k).strip() and str(v).strip()
        }
        self.remote_img_base: str = sanitize_url(remote_img_base, allow_relative=False) or ''

    def _lookup_ids(self, key: tuple[str, ...]) -> list[str]:
        raw = self.lookup_index.get(key)
        if not raw:
            return []
        if isinstance(raw, str):
            items = [raw]
        else:
            items = [str(item) for item in raw if isinstance(item, str)]
        # Strict isolation: keep only our5etools IDs, deduplicated in order.
        seen: set[str] = set()
        out: list[str] = []
        for eid in items:
            if _is_our5etools_id(eid) and eid not in seen:
                seen.add(eid)
                out.append(eid)
        return out

    def _filter_by_extra(
        self,
        candidates: list[str],
        extra: Mapping[str, str] | None,
    ) -> list[str]:
        if not extra or len(candidates) <= 1 or not self.entry_index:
            return candidates
        filtered = list(candidates)
        for field_key, extra_key in (
            ('owner_class', 'class_name'),
            ('owner_subclass', 'subclass_name'),
            ('owner_level', 'level'),
        ):
            target = (extra.get(extra_key) or '').strip().lower()
            if not target:
                continue
            narrowed = []
            for eid in filtered:
                meta = self.entry_index.get(eid)
                if not meta:
                    continue
                val = meta.get(field_key)
                if val is None and isinstance(meta.get('fields'), Mapping):
                    val = meta['fields'].get(field_key)
                if val is not None and str(val).strip().lower() == target:
                    narrowed.append(eid)
            if narrowed:
                filtered = narrowed
        return filtered

    def resolve_entity(
        self,
        tag: str,
        name: str,
        source: str = '',
        *,
        extra: Mapping[str, str] | None = None,
    ) -> ResolveResult:
        clean_name = name.strip()
        clean_source = source.strip()
        if not clean_name:
            return ResolveResult(status='unresolved', tag=tag, name=clean_name, source=clean_source)

        name_low = clean_name.lower()
        source_low = clean_source.lower()
        default_src_low = self.default_source.strip().lower()
        kinds = TAG_LOOKUP_ALIASES.get(tag, (tag, tag.lower()))

        # Optional tag redirect lookup when source is specified or omitted.
        redirect_sources: list[str] = []
        if self.tag_redirects and isinstance(self.tag_redirects, Mapping):
            for k in kinds:
                bucket = self.tag_redirects.get(k)
                if isinstance(bucket, Mapping):
                    target = bucket.get(name_low) or bucket.get(clean_name)
                    if isinstance(target, str) and target.strip():
                        redirect_sources.append(target.strip().lower())
                    elif isinstance(target, Mapping) and source_low:
                        mapped = target.get(source_low) or target.get(clean_source)
                        if isinstance(mapped, str) and mapped.strip():
                            redirect_sources.append(mapped.strip().lower())

        # Composite lookup keys for classFeature / subclassFeature if indexed that way.
        composite_names: list[str] = []
        if extra:
            cls_name = (extra.get('class_name') or '').strip().lower()
            sub_name = (extra.get('subclass_name') or '').strip().lower()
            lvl = (extra.get('level') or '').strip().lower()
            if tag == 'classFeature' and cls_name and lvl:
                composite_names.append(f'{name_low}|{cls_name}|{lvl}')
            elif tag == 'subclassFeature' and cls_name and sub_name and lvl:
                composite_names.append(f'{name_low}|{cls_name}|{sub_name}|{lvl}')
            elif tag == 'subclass' and cls_name:
                composite_names.append(f'{name_low}|{cls_name}')
            elif tag == 'card' and extra.get('deck_name'):
                composite_names.append(f'{name_low}|{extra["deck_name"].strip().lower()}')
            elif tag == 'deity' and extra.get('pantheon'):
                composite_names.append(f'{name_low}|{extra["pantheon"].strip().lower()}')

        search_names = [*composite_names, name_low]

        # 1. Exact (kind, name, source) lookup when source is given.
        if source_low:
            for k in kinds:
                for nm in search_names:
                    for src_try in (source_low, *redirect_sources):
                        ids = self._filter_by_extra(self._lookup_ids((k, nm, src_try)), extra)
                        if len(ids) == 1:
                            return ResolveResult('resolved', ids[0], (ids[0],), tag, clean_name, clean_source)
                        if len(ids) > 1:
                            return ResolveResult('ambiguous', None, tuple(ids), tag, clean_name, clean_source)
            # Do not silently jump to a different source when explicit source didn't match.
            return ResolveResult('unresolved', None, (), tag, clean_name, clean_source)

        # 2. If default_source is set (current entry's source), prefer unique match there first.
        if default_src_low:
            for k in kinds:
                for nm in search_names:
                    ids = self._filter_by_extra(self._lookup_ids((k, nm, default_src_low)), extra)
                    if len(ids) == 1:
                        return ResolveResult('resolved', ids[0], (ids[0],), tag, clean_name, default_src_low)

        # 3. Try redirect sources if provided without explicit source.
        for src_try in redirect_sources:
            for k in kinds:
                for nm in search_names:
                    ids = self._filter_by_extra(self._lookup_ids((k, nm, src_try)), extra)
                    if len(ids) == 1:
                        return ResolveResult('resolved', ids[0], (ids[0],), tag, clean_name, src_try)

        # 4. Unscoped (kind, name) lookup within our5etools only.
        collected: list[str] = []
        seen: set[str] = set()
        for k in kinds:
            for nm in search_names:
                ids = self._filter_by_extra(self._lookup_ids((k, nm)), extra)
                if len(ids) == 1 and nm in composite_names:
                    return ResolveResult('resolved', ids[0], (ids[0],), tag, clean_name, '')
                for eid in ids:
                    if eid not in seen:
                        seen.add(eid)
                        collected.append(eid)
            if collected:
                break

        if len(collected) == 1:
            return ResolveResult('resolved', collected[0], (collected[0],), tag, clean_name, '')
        if len(collected) > 1:
            return ResolveResult('ambiguous', None, tuple(collected), tag, clean_name, '')
        return ResolveResult('unresolved', None, (), tag, clean_name, '')

    def resolve_book(
        self,
        kind: str,
        book_id: str,
        chapter: str = '',
        header: str = '',
    ) -> tuple[str, str, str] | None:
        bid = book_id.strip().lower()
        ch = chapter.strip().lower()
        hd = header.strip().lower()
        if not bid and kind != 'quickref':
            return None

        probes: list[tuple[str, ...]] = []
        if bid:
            bkeys = [f'{kind.lower()}:{bid}', bid] if kind in ('book', 'adventure') else [bid]
            for bkey in bkeys:
                if ch and hd:
                    probes.append((bkey, ch, hd))
                if ch:
                    probes.append((bkey, ch, ''))
                if hd:
                    probes.append((bkey, '', hd))
                probes.append((bkey, '', ''))
        if kind == 'quickref':
            if ch and hd:
                probes.append(('quickref', ch, hd))
            if hd:
                probes.append(('quickref', '', hd))

        for key in probes:
            hit = self.book_anchor_lookup.get(key)
            if not hit:
                continue
            if isinstance(hit, Mapping):
                root = str(hit.get('root') or '')
                page = str(hit.get('page') or '')
                frag = str(hit.get('fragment') or hit.get('anchor') or '')
                if page:
                    return (root, page, frag)
            elif isinstance(hit, (tuple, list)):
                if len(hit) == 2:
                    page, frag = str(hit[0] or ''), str(hit[1] or '')
                    if page:
                        return ('', page, frag)
                elif len(hit) >= 3:
                    root, page, frag = str(hit[0] or ''), str(hit[1] or ''), str(hit[2] or '')
                    if page:
                        return (root, page, frag)
        return None

    def _normalize_source_filter_value(self, raw_val: str) -> str:
        val = raw_val.strip()
        if not val:
            return ''
        mapped = self.source_ids.get(val.lower())
        if mapped:
            return mapped
        if re.fullmatch(r'[A-Za-z0-9_-]+', val):
            return val.upper()
        return val

    def build_filter_href(self, page: str, filters: Sequence[str]) -> str:
        page_key = page.strip().lower().removesuffix('.html')
        entry_type = FILTER_PAGE_TO_TYPE.get(page_key)
        page_map = FILTER_PAGE_KEY_MAP.get(page_key)
        extra_params: list[tuple[str, str]] = []

        for token in filters:
            raw = token.strip()
            if not raw or '=' not in raw:
                continue
            fkey_raw, fval_raw = raw.split('=', 1)
            fkey = fkey_raw.strip().lower()
            fval = _FILTER_MODE_SUFFIX_RE.sub('', fval_raw.strip()).strip()
            if not fval:
                continue

            # items: category=基础 / basic switches type to equipment instead of magic_item.
            if page_key == 'items' and fkey == 'category':
                vals = [v.strip().lower() for v in fval.split(';') if v.strip()]
                if any(v in ('基础', 'basic') for v in vals):
                    entry_type = 'equipment'
                continue

            # bestiary: challenge rating=[&0;&1/4] or cr=... -> cr_min & cr_max.
            if page_key == 'bestiary' and fkey in ('challenge rating', 'cr'):
                m = _CR_RANGE_RE.match(fval)
                if m:
                    bounds = [b.strip() for b in m.group(1).split(';')]
                    if len(bounds) >= 1:
                        lo = _parse_cr_number(bounds[0])
                        if lo is not None:
                            extra_params.append(('cr_min', lo))
                    if len(bounds) >= 2:
                        hi = _parse_cr_number(bounds[1])
                        if hi is not None:
                            extra_params.append(('cr_max', hi))
                else:
                    parts = [p.strip() for p in fval.split(';') if p.strip()]
                    nums = [_parse_cr_number(p) for p in parts if not p.startswith('!')]
                    nums_clean = [n for n in nums if n is not None]
                    if len(nums_clean) == 1:
                        extra_params.append(('cr_min', nums_clean[0]))
                        extra_params.append(('cr_max', nums_clean[0]))
                    elif len(nums_clean) >= 2:
                        ordered = sorted(nums_clean, key=float)
                        extra_params.append(('cr_min', ordered[0]))
                        extra_params.append(('cr_max', ordered[-1]))
                continue

            # source is a common Anna filter (source / not_source, never f_source).
            if fkey == 'source':
                for part in fval.split(';'):
                    item = _FILTER_MODE_SUFFIX_RE.sub('', part.strip()).strip()
                    if not item:
                        continue
                    negated = item.startswith('!')
                    if negated:
                        item = item[1:].strip()
                    norm_src = self._normalize_source_filter_value(item)
                    if norm_src:
                        extra_params.append(('not_source' if negated else 'source', norm_src))
                continue

            # Domain facets: resolve strictly against page_map (or global FILTER_KEY_MAP for unknown pages).
            if page_map is not None:
                facet = page_map.get(fkey)
            else:
                facet = FILTER_KEY_MAP.get(fkey)
            if not facet:
                # Drop unmapped 5etools filter tokens (e.g. speed type=!fly, miscellaneous=!swarm) cleanly.
                continue

            for part in fval.split(';'):
                item = _FILTER_MODE_SUFFIX_RE.sub('', part.strip()).strip()
                if not item:
                    continue
                negated = item.startswith('!')
                if negated:
                    item = item[1:].strip()
                item = item.lstrip('&').strip()
                norm_val = _normalize_filter_facet_value(facet, item)
                if not norm_val:
                    continue
                param_key = f'not_f_{facet}' if negated else f'f_{facet}'
                extra_params.append((param_key, norm_val))

        params: list[tuple[str, str]] = [('collection', self.collection)]
        if entry_type:
            params.append(('type', entry_type))
        params.extend(extra_params)
        return '/?' + urlencode(params)


@dataclass
class RenderContext:
    """Per-render state accumulating diagnostics, link events, and media usage."""

    resolver: LinkResolverProtocol = field(default_factory=LinkResolver)
    media_checker: Callable[[str], bool] = field(default=lambda _rel: False)
    default_source: str = ''
    heading_depth: int = 3
    remote_img_base: str = ''
    template_vars: Mapping[str, Any] = field(default_factory=dict)
    link_events: list[dict[str, Any]] = field(default_factory=list)
    unknown_tags: list[str] = field(default_factory=list)
    unsupported_structures: list[str] = field(default_factory=list)
    local_media: list[str] = field(default_factory=list)
    remote_media: list[str] = field(default_factory=list)
    missing_local_media: list[str] = field(default_factory=list)
    remote_media_map: dict[str, list[str]] = field(default_factory=dict)
    _seen_unknown_tags: set[str] = field(default_factory=set, repr=False)
    _seen_unsupported: set[str] = field(default_factory=set, repr=False)
    _seen_local_media: set[str] = field(default_factory=set, repr=False)
    _seen_remote_media: set[str] = field(default_factory=set, repr=False)
    _seen_missing_media: set[str] = field(default_factory=set, repr=False)

    def record_unknown_tag(self, tag: str) -> None:
        if tag and tag not in self._seen_unknown_tags:
            self._seen_unknown_tags.add(tag)
            self.unknown_tags.append(tag)

    def record_unsupported(self, block_type: str) -> None:
        if block_type and block_type not in self._seen_unsupported:
            self._seen_unsupported.add(block_type)
            self.unsupported_structures.append(block_type)

    def record_local_media(self, relpath: str) -> None:
        if relpath and relpath not in self._seen_local_media:
            self._seen_local_media.add(relpath)
            self.local_media.append(relpath)

    def record_remote_media(self, ref: str) -> None:
        if ref and ref not in self._seen_remote_media:
            self._seen_remote_media.add(ref)
            self.remote_media.append(ref)

    def record_missing_local_media(self, relpath: str) -> None:
        if relpath and relpath not in self._seen_missing_media:
            self._seen_missing_media.add(relpath)
            self.missing_local_media.append(relpath)

    def record_remote_urls(self, cache_rel: str, urls: Sequence[str]) -> None:
        if not cache_rel or not urls:
            return
        bucket = self.remote_media_map.setdefault(cache_rel, [])
        for url in urls:
            if url and url not in bucket:
                bucket.append(url)

    def record_link_event(
        self,
        *,
        tag: str,
        raw: str,
        name: str,
        source: str,
        status: str,
        candidates: Sequence[str] = (),
    ) -> None:
        self.link_events.append({
            'tag': tag,
            'raw': raw,
            'name': name,
            'source': source,
            'status': status,
            'candidates': list(candidates),
        })

    def media_summary(self) -> dict[str, list[str]]:
        return {
            'local_media': list(self.local_media),
            'remote_media': list(self.remote_media),
            'missing_local_media': list(self.missing_local_media),
        }

    def check_local_media(self, *candidates: str) -> str | None:
        for cand in candidates:
            rel = sanitize_media_path(cand)
            if rel and self.media_checker(rel):
                return rel
        return None


def _heading_tag(depth: int) -> str:
    return f'h{min(max(depth, 3), 6)}'


def _format_signed(value: str | int) -> str:
    s = str(value).strip()
    if not s:
        return ''
    if s[0] in ('+', '-', '−'):
        return s
    try:
        num = int(s)
        return f'+{num}' if num >= 0 else str(num)
    except ValueError:
        return f'+{s}'


def _format_attack_tag(tag_str: str, *, is_roll: bool = False) -> str:
    def _part(chars: list[str]) -> str:
        t = '近战' if 'm' in chars else ('远程' if 'r' in chars else ('魔法' if 'g' in chars else ('范围' if 'a' in chars else '')))
        m = '武器' if 'w' in chars else ('法术' if 's' in chars else ('威能' if 'p' in chars else ''))
        return f'{t}{m}'

    groups = [list(g.strip()) for g in tag_str.lower().split(',') if g.strip()]
    if len(groups) > 1:
        seen = set(groups[-1])
        for idx in range(len(groups) - 2, -1, -1):
            kept = []
            for ch in groups[idx]:
                if ch not in seen:
                    kept.append(ch)
                    seen.add(ch)
            groups[idx] = kept
    joined = ' 或 '.join(p for p in (_part(g) for g in groups) if p)
    suffix = '攻击检定：' if is_roll else '攻击：'
    return f'{joined}{suffix}'


def _parse_entity_tag(tag: str, payload: str) -> tuple[str, str, str, dict[str, str]]:
    """Return ``(name, source, display_text, extra)`` for an entity tag payload."""
    parts = [p.strip() for p in split_tag_by_pipe(payload)]
    extra: dict[str, str] = {}

    if tag == 'classFeature':
        # 5etools: name|className|classSource|level|source|displayText
        # Also tolerate 5-part name|class|classSource|level|displayText when 5th looks like display text.
        name = parts[0] if len(parts) > 0 else ''
        cls_name = parts[1] if len(parts) > 1 else ''
        cls_src = parts[2] if len(parts) > 2 else ''
        level = parts[3] if len(parts) > 3 else ''
        source = ''
        display = ''
        if len(parts) >= 6:
            source = parts[4] or cls_src
            display = parts[5]
        elif len(parts) == 5:
            # Distinguish source code (short ASCII like PHB/TCE/XPHB) vs display text.
            if re.fullmatch(r'[A-Za-z0-9_:-]{1,20}', parts[4]):
                source = parts[4]
            else:
                source = cls_src
                display = parts[4]
        else:
            source = cls_src
        if cls_name:
            extra['class_name'] = cls_name
        if level:
            extra['level'] = level
        return name, source, display or name, extra

    if tag == 'subclassFeature':
        # 5etools: name|className|classSource|subclassShortName|subclassSource|level|source|displayText
        name = parts[0] if len(parts) > 0 else ''
        cls_name = parts[1] if len(parts) > 1 else ''
        cls_src = parts[2] if len(parts) > 2 else ''
        sub_name = parts[3] if len(parts) > 3 else ''
        sub_src = parts[4] if len(parts) > 4 else ''
        level = parts[5] if len(parts) > 5 else ''
        source = ''
        display = ''
        if len(parts) >= 8:
            source = parts[6] or sub_src or cls_src
            display = parts[7]
        elif len(parts) == 7:
            if re.fullmatch(r'[A-Za-z0-9_:-]{1,20}', parts[6]):
                source = parts[6]
            else:
                source = sub_src or cls_src
                display = parts[6]
        else:
            source = sub_src or cls_src
        if cls_name:
            extra['class_name'] = cls_name
        if sub_name:
            extra['subclass_name'] = sub_name
        if level:
            extra['level'] = level
        return name, source, display or name, extra

    if tag == 'subclass':
        # 5etools: shortName|className|classSource|source|displayText
        name = parts[0] if len(parts) > 0 else ''
        cls_name = parts[1] if len(parts) > 1 else ''
        cls_src = parts[2] if len(parts) > 2 else ''
        source = parts[3] if len(parts) > 3 and parts[3] else cls_src
        display = parts[4] if len(parts) > 4 else ''
        if cls_name:
            extra['class_name'] = cls_name
        return name, source, display or name, extra

    if tag == 'deity':
        # 5etools: name|pantheon|source|displayText
        name = parts[0] if len(parts) > 0 else ''
        pantheon = parts[1] if len(parts) > 1 else ''
        source = parts[2] if len(parts) > 2 else ''
        display = parts[3] if len(parts) > 3 else ''
        if pantheon:
            extra['pantheon'] = pantheon
        return name, source, display or name, extra

    if tag == 'card':
        # 5etools: name|deckName|source|displayText
        name = parts[0] if len(parts) > 0 else ''
        deck_name = parts[1] if len(parts) > 1 else ''
        source = parts[2] if len(parts) > 2 else ''
        display = parts[3] if len(parts) > 3 else ''
        if deck_name:
            extra['deck_name'] = deck_name
        return name, source, display or name, extra

    if tag == 'cite':
        # 5etools: displayText|citationId|...
        display = parts[0] if len(parts) > 0 else ''
        cite_id = parts[1] if len(parts) > 1 else ''
        return cite_id or display, '', display or cite_id, extra

    # Standard name|source|displayText|...
    name = parts[0] if len(parts) > 0 else ''
    source = parts[1] if len(parts) > 1 else ''
    display = parts[2] if len(parts) > 2 else ''
    if tag == 'itemProperty' and not display:
        display = WEAPON_PROP_ZH.get(name, name)
    return name, source, display or name, extra


def _render_entity_ref(
    tag: str,
    payload: str,
    raw_token: str,
    ctx: RenderContext,
) -> str:
    name, source, display, extra = _parse_entity_tag(tag, payload)
    display_html = render_inline(display, ctx) if ('{@' in display or '{=' in display) else escape_html(display)
    if not name and not display:
        return ''

    # Special case: {@quickref name|source|chapter|header|displayText} can resolve in book_anchor_lookup first.
    if tag == 'quickref':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        q_name = parts[0] if len(parts) > 0 else ''
        q_source = parts[1] if len(parts) > 1 else ''
        q_chapter = parts[2] if len(parts) > 2 else ''
        q_header = parts[3] if len(parts) > 3 else q_name
        q_display = parts[4] if len(parts) > 4 and parts[4] else q_name
        display_html = render_inline(q_display, ctx) if '{@' in q_display else escape_html(q_display)
        book_hit = ctx.resolver.resolve_book('quickref', q_source or 'quickref', q_chapter, q_header)
        if book_hit is not None:
            return _render_book_anchor(book_hit, display_html, ctx.resolver.collection)
        res = ctx.resolver.resolve_entity('quickref', q_name, q_source)
        name, source = q_name, q_source
    else:
        res = ctx.resolver.resolve_entity(tag, name, source, extra=extra)

    if res.status == 'resolved' and res.entry_id and _is_our5etools_id(res.entry_id):
        return f'<button type="button" class="ref" data-entry="{escape_html(res.entry_id)}">{display_html}</button>'

    status = 'ambiguous' if res.status == 'ambiguous' else 'unresolved'
    ctx.record_link_event(
        tag=tag,
        raw=raw_token,
        name=name,
        source=source,
        status=status,
        candidates=res.candidates,
    )
    if status == 'ambiguous':
        title = f'5etools 中存在 {len(res.candidates)} 个同名候选（{name}）'
    else:
        title = f'5etools 中未找到对应条目：{name}' + (f'（{source}）' if source else '')
    return (
        f'<span class="ref-unresolved" data-ref-status="{status}" '
        f'title="{escape_html(title)}">{display_html}</span>'
    )


def _render_book_anchor(
    target: tuple[str, str, str],
    display_html: str,
    collection: str,
) -> str:
    root, page, fragment = target
    params: list[tuple[str, str]] = [('collection', collection)]
    if root:
        params.append(('root', root))
    params.append(('page', page))
    if fragment:
        params.append(('anchor', fragment))
    href = '/read?' + urlencode(params)
    return f'<a class="ref-book" href="{escape_html(href)}">{display_html}</a>'


def _resolve_template_expr(expr: str, ctx: RenderContext) -> str:
    """Resolve 5etools ``{=key[/flags]}`` template expressions from ``ctx.template_vars``."""
    parts = expr.split('/', 1)
    key = parts[0].strip()
    flags = parts[1].strip() if len(parts) > 1 else ''
    tvars = ctx.template_vars
    val: Any = None
    if isinstance(tvars, Mapping):
        if key in tvars and tvars[key] is not None:
            val = tvars[key]
        elif isinstance(tvars.get('inherits'), Mapping) and key in tvars['inherits'] and tvars['inherits'][key] is not None:
            val = tvars['inherits'][key]
        elif key == 'baseName':
            val = tvars.get('name')
    if val is None:
        return escape_html(expr)
    if key == 'dmgType' and isinstance(val, str):
        val = DAMAGE_TYPE_ZH.get(val, val)
    if isinstance(val, list):
        rendered = '、'.join(str(x) for x in val if x is not None)
    else:
        rendered = str(val)
    if 'l' in flags:
        rendered = rendered.lower()
    return render_inline(rendered, ctx) if ('{@' in rendered) else escape_html(rendered)


def _render_tag(raw_token: str, ctx: RenderContext) -> str:
    # Handle {=...} template expressions from magicvariant/recipe fields.
    if raw_token.startswith('{=') and raw_token.endswith('}'):
        expr = raw_token[2:-1].strip()
        return _resolve_template_expr(expr, ctx)

    if not (raw_token.startswith('{@') and raw_token.endswith('}')):
        return escape_html(raw_token)

    inner = raw_token[2:-1]
    space_idx = inner.find(' ')
    if space_idx == -1:
        tag = inner.strip()
        payload = ''
    else:
        tag = inner[:space_idx].strip()
        payload = inner[space_idx + 1:]

    # 1. Entity link tags (strict our5etools isolation).
    if tag in KNOWN_ENTITY_TAGS:
        return _render_entity_ref(tag, payload, raw_token, ctx)

    # 2. Book & Adventure links.
    if tag in ('book', 'adventure'):
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        display = parts[0] if len(parts) > 0 else ''
        book_id = parts[1] if len(parts) > 1 else ''
        chapter = parts[2] if len(parts) > 2 else ''
        header = parts[3] if len(parts) > 3 else ''
        display_html = render_inline(display, ctx) if '{@' in display else escape_html(display)
        hit = ctx.resolver.resolve_book(tag, book_id, chapter, header)
        if hit is not None:
            return _render_book_anchor(hit, display_html, ctx.resolver.collection)
        return display_html

    # 3. Filter links.
    if tag == 'filter':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        display = parts[0] if len(parts) > 0 else ''
        page = parts[1] if len(parts) > 1 else ''
        filters = parts[2:] if len(parts) > 2 else []
        display_html = render_inline(display, ctx) if '{@' in display else escape_html(display)
        href = ctx.resolver.build_filter_href(page, filters)
        return f'<a class="ref-filter" href="{escape_html(href)}">{display_html}</a>'

    # 4. Formatting & Styling tags.
    if tag in ('b', 'bold'):
        return f'<strong>{render_inline(payload, ctx)}</strong>'
    if tag in ('i', 'italic'):
        return f'<em>{render_inline(payload, ctx)}</em>'
    if tag in ('u', 'underline'):
        return f'<u>{render_inline(payload, ctx)}</u>'
    if tag in ('u2', 'underlineDouble'):
        return f'<u class="o5e-u2">{render_inline(payload, ctx)}</u>'
    if tag in ('s', 'strike'):
        return f'<s>{render_inline(payload, ctx)}</s>'
    if tag in ('s2', 'strikeDouble'):
        return f'<s class="o5e-s2">{render_inline(payload, ctx)}</s>'
    if tag == 'sup':
        return f'<sup>{render_inline(payload, ctx)}</sup>'
    if tag == 'sub':
        return f'<sub>{render_inline(payload, ctx)}</sub>'
    if tag == 'code':
        return f'<code>{escape_html(payload)}</code>'
    if tag == 'kbd':
        return f'<kbd>{escape_html(payload)}</kbd>'
    if tag == 'note':
        return f'<span class="o5e-note">{render_inline(payload, ctx)}</span>'
    if tag == 'highlight':
        return f'<mark class="o5e-highlight">{render_inline(payload, ctx)}</mark>'
    if tag == 'tip':
        parts = split_tag_by_pipe(payload)
        display = parts[0] if len(parts) > 0 else ''
        tip_text = parts[1] if len(parts) > 1 else ''
        if tip_text:
            return f'<span class="o5e-tip" title="{escape_html(tip_text)}">{render_inline(display, ctx)}</span>'
        return f'<span class="o5e-tip">{render_inline(display, ctx)}</span>'
    if tag == 'color':
        parts = split_tag_by_pipe(payload)
        display = parts[0] if len(parts) > 0 else ''
        color = _sanitize_color(parts[1]) if len(parts) > 1 else None
        if color:
            return f'<span class="o5e-color" style="color: {escape_html(color)}">{render_inline(display, ctx)}</span>'
        return f'<span class="o5e-color">{render_inline(display, ctx)}</span>'
    if tag == 'style':
        parts = split_tag_by_pipe(payload)
        display = parts[0] if len(parts) > 0 else ''
        raw_styles = parts[1] if len(parts) > 1 else ''
        classes = ['o5e-style']
        for st in re.split(r'[;\s,]+', raw_styles.strip()):
            mapped = _STYLE_CLASS_MAP.get(st.lower()) or _STYLE_CLASS_MAP.get(st)
            if mapped and mapped not in classes:
                classes.append(mapped)
        return f'<span class="{escape_html(" ".join(classes))}" data-style="{escape_html(raw_styles)}">{render_inline(display, ctx)}</span>'
    if tag == 'font':
        parts = split_tag_by_pipe(payload)
        display = parts[0] if len(parts) > 0 else ''
        font_path = parts[1].strip() if len(parts) > 1 else ''
        local_font = ctx.check_local_media(f'fonts/{font_path}', font_path) if font_path else None
        if local_font:
            ctx.record_local_media(local_font)
            return (
                f'<span class="o5e-font o5e-font-local" data-font="{escape_html(_media_url(local_font))}">'
                f'{render_inline(display, ctx)}</span>'
            )
        if font_path:
            san = sanitize_media_path(f'fonts/{font_path}')
            if san:
                ctx.record_missing_local_media(san)
        return f'<span class="o5e-font">{render_inline(display, ctx)}</span>'
    if tag == 'footnote':
        parts = split_tag_by_pipe(payload)
        display = parts[0] if len(parts) > 0 else ''
        note = parts[1] if len(parts) > 1 else ''
        title_part = parts[2] if len(parts) > 2 else ''
        tip = f'{title_part}：{note}' if (title_part and note) else (note or title_part)
        return f'<span class="o5e-footnote" title="{escape_html(tip)}">{render_inline(display, ctx)}</span>'
    if tag == 'homebrew':
        parts = split_tag_by_pipe(payload)
        new_text = parts[0] if len(parts) > 0 else ''
        old_text = parts[1] if len(parts) > 1 else ''
        tip = f'自制修订（原文本：{old_text}）' if old_text else '自制补充内容'
        shown = new_text or old_text
        return f'<span class="o5e-homebrew" title="{escape_html(tip)}">{render_inline(shown, ctx)}</span>'
    if tag == 'comic':
        return f'<span class="o5e-comic">{render_inline(payload, ctx)}</span>'
    if tag in ('comicH1', 'comicH2', 'comicH3', 'comicH4'):
        lvl = tag[-1]
        return f'<span class="o5e-comic o5e-comic-h{lvl}">{render_inline(payload, ctx)}</span>'
    if tag == 'comicNote':
        return f'<span class="o5e-comic-note">{render_inline(payload, ctx)}</span>'

    # 5. Mechanic, roll, and combat action tags.
    if tag in ('dice', 'autodice', 'damage'):
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        expr = parts[0] if len(parts) > 0 else ''
        display = parts[1] if len(parts) > 1 and parts[1] else expr
        display = (
            display.replace('summonSpellLevel', '法术环阶')
            .replace('summonClassLevel', '你的职业等级')
            .replace(';', '/')
        )
        cls = 'o5e-damage' if tag == 'damage' else 'o5e-dice'
        return f'<span class="{cls}">{escape_html(display)}</span>'
    if tag in ('scaledamage', 'scaledice'):
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        # format: base|levels|addPerLevel|displayText
        display = parts[3] if len(parts) > 3 and parts[3] else (parts[2] if len(parts) > 2 and parts[2] else (parts[0] if parts else ''))
        return f'<span class="o5e-dice">{escape_html(display)}</span>'
    if tag == 'hit':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        mod = parts[0] if len(parts) > 0 else ''
        display = parts[1] if len(parts) > 1 and parts[1] else _format_signed(mod)
        return f'<span class="o5e-hit">{escape_html(display)}</span>'
    if tag == 'dc':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        val = parts[0] if len(parts) > 0 else ''
        display = parts[1] if len(parts) > 1 and parts[1] else f'DC {val}'
        return f'<span class="o5e-dc">{escape_html(display)}</span>'
    if tag in ('d20', 'initiative'):
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        mod = parts[0] if len(parts) > 0 else ''
        display = parts[1] if len(parts) > 1 and parts[1] else _format_signed(mod)
        return f'<span class="o5e-dice">{escape_html(display)}</span>'
    if tag == 'chance':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        pct = parts[0] if len(parts) > 0 else ''
        display = parts[1] if len(parts) > 1 and parts[1] else f'{pct}%'
        return f'<span class="o5e-chance">{escape_html(display)}</span>'
    if tag == 'coinflip':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        display = parts[0] if parts and parts[0] else '抛硬币'
        return f'<span class="o5e-dice">{escape_html(display)}</span>'
    if tag == 'recharge':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        num = parts[0] if len(parts) > 0 else '6'
        flags = parts[1] if len(parts) > 1 else ''
        range_str = '6' if num in ('', '6') else f'{num}–6'
        text_out = f'充能 {range_str}' if 'm' in flags else f'(充能 {range_str})'
        return f'<span class="o5e-recharge">{escape_html(text_out)}</span>'
    if tag in ('atk', 'atkr'):
        return f'<em>{escape_html(_format_attack_tag(payload, is_roll=(tag == "atkr")))}</em>'
    if tag == 'h':
        return '<em>命中：</em> '
    if tag == 'm':
        return '<em>未命中：</em> '
    if tag == 'hom':
        return '<em>命中或未命中：</em> '
    if tag == 'actSave':
        abv = payload.strip().lower()
        ab_zh = ABILITY_ABV_ZH.get(abv, payload.strip())
        return f'<em>{escape_html(ab_zh)}豁免：</em>'
    if tag == 'actSaveFail':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        ordinal = parts[0] if parts and parts[0] else ''
        if ordinal:
            ord_zh = _ORDINAL_ZH.get(ordinal, f'第{ordinal}次')
            return f'<em>{escape_html(ord_zh)}失败：</em>'
        return '<em>失败：</em>'
    if tag == 'actSaveFailBy':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        amount = parts[0] if parts else ''
        return f'<em>失败差值 {escape_html(amount)} 或更多：</em>'
    if tag == 'actSaveSuccess':
        return '<em>成功：</em>'
    if tag == 'actSaveSuccessOrFail':
        return '<em>失败或成功：</em>'
    if tag == 'actTrigger':
        return '<em>触发：</em>'
    if tag == 'actResponse':
        return '<em>响应：</em>'
    if tag == 'ability':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        head = parts[0] if len(parts) > 0 else ''
        mod = parts[1] if len(parts) > 1 else ''
        tokens = head.split()
        if len(tokens) == 2 and tokens[0].lower() in ABILITY_ABV_ZH:
            head = f'{ABILITY_ABV_ZH[tokens[0].lower()]} {tokens[1]}'
        out_txt = f'{head} ({mod})' if mod else head
        return f'<span class="o5e-ability">{escape_html(out_txt)}</span>'
    if tag == 'skillCheck':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        head = parts[0] if len(parts) > 0 else ''
        display = parts[1] if len(parts) > 1 and parts[1] else ''
        if not display:
            tokens = head.rsplit(' ', 1)
            if len(tokens) == 2 and re.fullmatch(r'[+-]?\d+', tokens[1]):
                display = f'{tokens[0]} ({_format_signed(tokens[1])})'
            else:
                display = head
        return f'<span class="o5e-skill-check">{escape_html(display)}</span>'
    if tag == 'savingThrow':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        head = parts[0] if len(parts) > 0 else ''
        display = parts[1] if len(parts) > 1 and parts[1] else ''
        if not display:
            tokens = head.rsplit(' ', 1)
            if len(tokens) == 2 and re.fullmatch(r'[+-]?\d+', tokens[1]):
                ab = ABILITY_ABV_ZH.get(tokens[0].lower(), tokens[0])
                display = f'{ab}豁免 ({_format_signed(tokens[1])})'
            else:
                display = head
        return f'<span class="o5e-saving-throw">{escape_html(display)}</span>'
    if tag == 'hitYourSpellAttack':
        display = payload.strip() or '你的法术攻击加值'
        return f'<span class="o5e-hit">{escape_html(display)}</span>'
    if tag == 'dcYourSpellSave':
        display = payload.strip() or '你的法术豁免DC'
        return f'<span class="o5e-dc">{escape_html(display)}</span>'
    if tag == 'area':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        display = parts[0] if parts else ''
        return f'<span class="o5e-area">{render_inline(display, ctx)}</span>'
    if tag == 'unit':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        display = parts[0] if parts else ''
        return f'<span class="o5e-unit">{escape_html(display)}</span>'

    # 6. External links, 5etools site refs, media links, loaders.
    if tag == 'link':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        display = parts[0] if len(parts) > 0 else ''
        raw_url = parts[1] if len(parts) > 1 and parts[1] else display
        safe = sanitize_url(raw_url, allow_relative=False)
        display_html = render_inline(display, ctx) if '{@' in display else escape_html(display)
        if safe:
            return f'<a class="o5e-ext-link" href="{escape_html(safe)}" target="_blank" rel="noopener noreferrer">{display_html}</a>'
        return f'<span class="o5e-ext-link-plain">{display_html}</span>'
    if tag == '5etools':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        display = parts[0] if len(parts) > 0 else ''
        page = parts[1] if len(parts) > 1 else ''
        display_html = render_inline(display, ctx) if '{@' in display else escape_html(display)
        page_key = page.lower().removesuffix('.html')
        if page_key in FILTER_PAGE_TO_TYPE:
            href = ctx.resolver.build_filter_href(page_key, ())
            return f'<a class="ref-filter" href="{escape_html(href)}">{display_html}</a>'
        return f'<span class="o5e-site-ref">{display_html}</span>'
    if tag == '5etoolsImg':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        display = parts[0] if len(parts) > 0 else ''
        path = parts[1] if len(parts) > 1 else ''
        san = normalize_internal_media_path(path, 'img/') if path else None
        local = ctx.check_local_media(*(c for c in (san, path) if c)) if path else None
        if local:
            ctx.record_local_media(local)
            return f'<a class="o5e-media-link" href="{escape_html(_media_url(local))}" target="_blank" rel="noopener noreferrer">{escape_html(display or local)}</a>'
        if san:
            ctx.record_missing_local_media(san)
            ctx.record_remote_media(san)
            ctx.record_remote_urls(san, internal_image_remote_urls(san))
        return f'<span class="o5e-media-remote-inline" title="远端配图（未离线收录）">{escape_html(display or path)}</span>'
    if tag == '5etoolsAudio':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        display = parts[0] if len(parts) > 0 else ''
        path = parts[1] if len(parts) > 1 else ''
        cands = _audio_path_candidates(path) if path else []
        local = ctx.check_local_media(*cands) if cands else None
        if local:
            ctx.record_local_media(local)
            return (
                f'<span class="o5e-audio-inline">{escape_html(display)} '
                f'<audio controls preload="none" src="{escape_html(_media_url(local))}"></audio></span>'
            )
        san = sanitize_media_path(f'audio/{path}') if path else None
        if san:
            ctx.record_missing_local_media(san)
            ctx.record_remote_media(san)
        return f'<span class="o5e-media-remote-inline" title="远端音频（未离线收录）">{escape_html(display or path)}</span>'
    if tag == 'loader':
        parts = [p.strip() for p in split_tag_by_pipe(payload)]
        display = parts[0] if parts else ''
        return f'<span class="o5e-loader">{escape_html(display)}</span>'

    # 7. Unknown tags: record and strip cleanly to readable text.
    ctx.record_unknown_tag(tag)
    parts = [p.strip() for p in split_tag_by_pipe(payload)]
    readable = parts[2] if len(parts) > 2 and parts[2] else (parts[0] if parts else '')
    if not readable:
        return ''
    return render_inline(readable, ctx) if '{@' in readable else escape_html(readable)


def render_inline(text: Any, ctx: RenderContext) -> str:
    """Render a 5etools inline string containing ``{@...}`` tags into safe HTML."""
    if text is None:
        return ''
    if not isinstance(text, str):
        return render_block(text, ctx)
    if '{@' not in text and '{=' not in text:
        return escape_html(text)
    out: list[str] = []
    for chunk in split_by_tags(text):
        if (chunk.startswith('{@') or chunk.startswith('{=')) and chunk.endswith('}'):
            out.append(_render_tag(chunk, ctx))
        else:
            out.append(escape_html(chunk))
    return ''.join(out)


def _render_image_block(block: Mapping[str, Any], ctx: RenderContext) -> str:
    href = block.get('href')
    title = str(block.get('title') or '').strip()
    alt_text = str(block.get('altText') or title or '配图').strip()
    credit = str(block.get('credit') or '').strip()
    caption_parts: list[str] = []
    if title:
        caption_parts.append(render_inline(title, ctx))
    if credit:
        caption_parts.append(f'<span class="o5e-media-credit">绘制：{escape_html(credit)}</span>')
    figcaption = f'<figcaption>{" · ".join(caption_parts)}</figcaption>' if caption_parts else ''

    href_type = href.get('type') if isinstance(href, Mapping) else ''
    if href_type == 'internal':
        raw_path = str(href.get('path') or '').strip()
        san = normalize_internal_media_path(raw_path, 'img/') if raw_path else None
        local = ctx.check_local_media(*(c for c in (san, raw_path) if c)) if raw_path else None
        if local:
            ctx.record_local_media(local)
            return (
                f'<figure class="o5e-figure">'
                f'<img src="{escape_html(_media_url(local))}" alt="{escape_html(alt_text)}" loading="lazy" decoding="async" data-o5e-fallback="1">'
                f'<span class="o5e-media-fallback-note" hidden>远端配图（未离线收录）</span>'
                f'{figcaption}</figure>'
            )
        ref_label = san or raw_path
        if san:
            ctx.record_missing_local_media(san)
            ctx.record_remote_urls(san, internal_image_remote_urls(san))
        if ref_label:
            ctx.record_remote_media(ref_label)
        remote_base = ctx.remote_img_base or getattr(ctx.resolver, 'remote_img_base', '')
        link_html = ''
        if remote_base and san:
            full_remote = sanitize_url(f'{remote_base.rstrip("/")}/{san}', allow_relative=False)
            if full_remote:
                link_html = f'<a class="o5e-media-link" href="{escape_html(full_remote)}" target="_blank" rel="noopener noreferrer">查看原图 ↗</a>'
        lazy_attr = f' data-lazy-media="{escape_html(_media_url(san))}"' if san else ''
        title_attr = f' title="{escape_html(ref_label)}"' if ref_label else ''
        return (
            f'<figure class="o5e-figure o5e-media-remote"{lazy_attr} data-media-alt="{escape_html(alt_text)}"{title_attr}>'
            f'<span class="o5e-media-badge">远端配图（未离线收录）</span>'
            f'{figcaption}{link_html}</figure>'
        )

    # External (or unspecified href): never pretend offline.
    raw_url = str(href.get('url') or '') if isinstance(href, Mapping) else (str(href) if isinstance(href, str) else '')
    safe_url = sanitize_url(raw_url, allow_relative=False)
    cache_rel = remote_media_cache_path(safe_url) if safe_url else None
    if safe_url:
        ctx.record_remote_media(safe_url)
        if cache_rel:
            ctx.record_remote_urls(cache_rel, (safe_url,))
        link_html = f'<a class="o5e-media-link" href="{escape_html(safe_url)}" target="_blank" rel="noopener noreferrer">查看原图 ↗</a>'
    else:
        if raw_url:
            ctx.record_remote_media(raw_url)
        link_html = ''
    lazy_attr = f' data-lazy-media="{escape_html(_media_url(cache_rel))}"' if cache_rel else ''
    return (
        f'<figure class="o5e-figure o5e-media-remote"{lazy_attr} data-media-alt="{escape_html(alt_text)}">'
        f'<span class="o5e-media-badge">远端配图（未离线收录）</span>'
        f'{figcaption}{link_html}</figure>'
    )


def _render_cell_value(cell: Any, ctx: RenderContext) -> str:
    if cell is None:
        return ''
    if isinstance(cell, (int, float, bool)):
        return escape_html(cell)
    if isinstance(cell, str):
        return render_inline(cell, ctx)
    if isinstance(cell, Mapping):
        ctype = cell.get('type')
        if ctype == 'cell':
            if 'entry' in cell and cell['entry'] is not None:
                return render_inline(cell['entry'], ctx) if isinstance(cell['entry'], str) else render_block(cell['entry'], ctx)
            roll = cell.get('roll')
            if isinstance(roll, Mapping):
                if 'exact' in roll:
                    return escape_html(roll['exact'])
                if 'min' in roll and 'max' in roll:
                    lo, hi = roll['min'], roll['max']
                    return escape_html(lo if lo == hi else f'{lo}–{hi}')
            if 'entries' in cell:
                return render_blocks(cell['entries'], ctx=ctx)
            return ''
        if ctype == 'cellHeader':
            return render_inline(cell.get('entry') or cell.get('name') or '', ctx)
        return render_block(cell, ctx)
    if isinstance(cell, list):
        return ' '.join(_render_cell_value(item, ctx) for item in cell if item is not None)
    return escape_html(cell)


def _render_table(block: Mapping[str, Any], ctx: RenderContext) -> str:
    out: list[str] = []
    if block.get('intro'):
        out.append(render_blocks(block['intro'], ctx=ctx))

    out.append('<table class="o5e-table">')
    caption = block.get('caption')
    if caption:
        out.append(f'<caption>{render_inline(caption, ctx)}</caption>')

    col_labels = block.get('colLabels')
    if isinstance(col_labels, list) and col_labels:
        out.append('<thead><tr>')
        for label in col_labels:
            if isinstance(label, Mapping) and label.get('type') == 'cellHeader':
                span = int(label['width']) if str(label.get('width', '')).isdigit() and 1 < int(label['width']) <= 50 else 1
                span_attr = f' colspan="{span}"' if span > 1 else ''
                out.append(f'<th scope="col"{span_attr}>{_render_cell_value(label, ctx)}</th>')
            else:
                out.append(f'<th scope="col">{_render_cell_value(label, ctx)}</th>')
        out.append('</tr></thead>')

    rows = block.get('rows')
    if isinstance(rows, list) and rows:
        out.append('<tbody>')
        for row in rows:
            row_style = ''
            cells: list[Any]
            if isinstance(row, Mapping) and row.get('type') == 'row':
                style = str(row.get('style') or '').strip()
                if style:
                    row_style = f' class="o5e-{escape_html(re.sub(r"[^a-zA-Z0-9_-]+", "-", style))}"'
                cells = row.get('row') if isinstance(row.get('row'), list) else []
            elif isinstance(row, list):
                cells = row
            else:
                cells = [row]
            out.append(f'<tr{row_style}>')
            for cell in cells:
                width_attr = ''
                if isinstance(cell, Mapping) and str(cell.get('width', '')).isdigit():
                    w = int(cell['width'])
                    if 1 < w <= 50:
                        width_attr = f' colspan="{w}"'
                out.append(f'<td{width_attr}>{_render_cell_value(cell, ctx)}</td>')
            out.append('</tr>')
        out.append('</tbody>')

    footnotes = block.get('footnotes')
    if isinstance(footnotes, list) and footnotes:
        colspan = max(len(col_labels) if isinstance(col_labels, list) else 1, 1)
        out.append(f'<tfoot><tr><td colspan="{colspan}">')
        for fn in footnotes:
            out.append(f'<p class="o5e-footnote-row">{render_inline(fn, ctx)}</p>')
        out.append('</td></tr></tfoot>')

    out.append('</table>')
    if block.get('outro'):
        out.append(render_blocks(block['outro'], ctx=ctx))
    return ''.join(out)


def _render_dice_block(block: Mapping[str, Any]) -> str:
    if block.get('displayText'):
        return f'<span class="o5e-dice">{escape_html(block["displayText"])}</span>'
    to_roll = block.get('toRoll')
    if isinstance(to_roll, str):
        return f'<span class="o5e-dice">{escape_html(to_roll)}</span>'
    if isinstance(to_roll, list):
        parts: list[str] = []
        for die in to_roll:
            if isinstance(die, Mapping):
                num = die.get('number', 1)
                faces = die.get('faces', '')
                mod = die.get('modifier')
                seg = f'{num}d{faces}' if faces != '' else str(num)
                if mod is not None and not die.get('hideModifier'):
                    seg += _format_signed(mod)
                parts.append(seg)
            else:
                parts.append(str(die))
        return f'<span class="o5e-dice">{escape_html(" + ".join(parts))}</span>'
    return ''


def _render_spellcasting_block(block: Mapping[str, Any], ctx: RenderContext) -> str:
    out: list[str] = ['<section class="o5e-spellcasting">']
    htag = _heading_tag(ctx.heading_depth)
    name = block.get('name')
    if name:
        out.append(f'<{htag}>{render_inline(name, ctx)}</{htag}>')
    if block.get('headerEntries'):
        out.append(render_blocks(block['headerEntries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))

    def _spell_list_item(label: str, items: Sequence[Any]) -> str:
        rendered = '、'.join(
            render_inline(s.get('entry', ''), ctx) if isinstance(s, Mapping) else render_inline(s, ctx)
            for s in items
        )
        return f'<li><strong>{escape_html(label)}：</strong>{rendered}</li>'

    lines: list[str] = []
    if isinstance(block.get('constant'), list) and block['constant']:
        lines.append(_spell_list_item('常驻', block['constant']))
    if isinstance(block.get('will'), list) and block['will']:
        lines.append(_spell_list_item('随意', block['will']))
    if isinstance(block.get('ritual'), list) and block['ritual']:
        lines.append(_spell_list_item('仪式', block['ritual']))

    for key, unit_label in (
        ('restShort', '短休'),
        ('restLong', '长休'),
        ('daily', '每日'),
        ('weekly', '每周'),
        ('monthly', '每月'),
        ('yearly', '每年'),
    ):
        bucket = block.get(key)
        if isinstance(bucket, Mapping):
            for freq, spells in bucket.items():
                if not isinstance(spells, list) or not spells:
                    continue
                each = str(freq).endswith('e')
                count = str(freq).rstrip('e')
                lbl = f'{unit_label}各 {count} 次' if each else f'{unit_label} {count} 次'
                lines.append(_spell_list_item(lbl, spells))

    if isinstance(block.get('charges'), Mapping):
        for cost, spells in block['charges'].items():
            if isinstance(spells, list) and spells:
                lines.append(_spell_list_item(f'消耗 {cost} 充能', spells))

    if isinstance(block.get('spells'), Mapping):
        def _lvl_key(k: str) -> tuple[int, str]:
            return (int(k), '') if k.isdigit() else (99, k)

        for lvl in sorted(block['spells'].keys(), key=_lvl_key):
            info = block['spells'][lvl]
            if not isinstance(info, Mapping):
                continue
            spells = info.get('spells')
            if not isinstance(spells, list) or not spells:
                continue
            slots = info.get('slots')
            if str(lvl) == '0':
                lbl = '戏法（随意）'
            elif slots is not None:
                lbl = f'{lvl} 环（{slots} 法术位）'
            else:
                lbl = f'{lvl} 环'
            lines.append(_spell_list_item(lbl, spells))

    if lines:
        out.append('<ul class="o5e-spell-list">' + ''.join(lines) + '</ul>')

    if block.get('footerEntries'):
        out.append(render_blocks(block['footerEntries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))
    out.append('</section>')
    return ''.join(out)


def _render_ingredient_block(block: Mapping[str, Any], ctx: RenderContext) -> str:
    raw_entry = str(block.get('entry') or '')

    def _subst(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        key = expr.split('/', 1)[0].strip()
        val = block.get(key)
        if val is None:
            return ''
        return str(val)

    text = _TEMPLATE_EXPR_RE.sub(_subst, raw_entry).strip()
    return f'<span class="o5e-ingredient">{render_inline(text, ctx)}</span>'


def _render_item_uses(block: Mapping[str, Any], ctx: RenderContext) -> str:
    val = block.get('value')
    target = block.get('target')
    label = ''
    if isinstance(target, Mapping):
        consumes = target.get('consumes')
        if isinstance(consumes, Mapping):
            label = str(consumes.get('name') or consumes.get('ENG_name') or '')
        elif isinstance(consumes, str):
            label = consumes
    if val is not None and label:
        txt = f'{val} {label}'
    elif val is not None:
        txt = str(val)
    else:
        txt = label
    return f'<span class="o5e-item-uses">{escape_html(txt)}</span>'


def _render_ref_block(tag: str, ref_str: Any, ctx: RenderContext) -> str:
    if not isinstance(ref_str, str) or not ref_str.strip():
        return ''
    raw_token = f'{{@{tag} {ref_str.strip()}}}'
    return f'<p class="o5e-ref-block">{_render_entity_ref(tag, ref_str.strip(), raw_token, ctx)}</p>'


def _render_link_block(block: Mapping[str, Any], ctx: RenderContext) -> str:
    text = str(block.get('text') or '')
    text_html = render_inline(text, ctx) if '{@' in text else escape_html(text)
    href = block.get('href')
    if isinstance(href, Mapping):
        htype = href.get('type')
        if htype == 'external':
            safe = sanitize_url(str(href.get('url') or ''), allow_relative=False)
            if safe:
                return f'<a class="o5e-ext-link" href="{escape_html(safe)}" target="_blank" rel="noopener noreferrer">{text_html}</a>'
            return f'<span class="o5e-ext-link-plain">{text_html}</span>'
        if htype == 'internal':
            path = str(href.get('path') or '').strip()
            page_key = path.lower().removesuffix('.html')
            if page_key in FILTER_PAGE_TO_TYPE:
                url = ctx.resolver.build_filter_href(page_key, ())
                return f'<a class="ref-filter" href="{escape_html(url)}">{text_html}</a>'
            return f'<span class="o5e-site-ref">{text_html}</span>'
    return f'<span class="o5e-link-plain">{text_html}</span>'


def _render_unknown_block(block: Mapping[str, Any], ctx: RenderContext) -> str:
    btype = str(block.get('type') or 'unknown')
    ctx.record_unsupported(btype)
    parts: list[str] = []
    htag = _heading_tag(ctx.heading_depth)
    title = block.get('name') or block.get('title') or block.get('caption') or block.get('header')
    if isinstance(title, str) and title.strip():
        parts.append(f'<{htag}>{render_inline(title, ctx)}</{htag}>')

    handled_keys = {'type', 'id', 'style', 'page', 'source', 'ENG_name', 'name', 'title', 'caption', 'header'}
    if 'text' in block and isinstance(block['text'], str):
        parts.append(f'<p>{render_inline(block["text"], ctx)}</p>')
        handled_keys.add('text')
    if 'entry' in block and block['entry'] is not None:
        val = block['entry']
        parts.append(f'<p>{render_inline(val, ctx)}</p>' if isinstance(val, str) else render_block(val, ctx))
        handled_keys.add('entry')
    for list_key in ('entries', 'items', 'blocks', 'wrappeds', 'tables'):
        if list_key in block and isinstance(block[list_key], list):
            parts.append(render_blocks(block[list_key], ctx=ctx, heading_depth=ctx.heading_depth + 1))
            handled_keys.add(list_key)
    if 'rows' in block and isinstance(block['rows'], list):
        parts.append(_render_table(block, ctx))
        handled_keys.add('rows')
    if 'data' in block and isinstance(block['data'], Mapping):
        parts.append(_render_unknown_dict_values(block['data'], ctx))
        handled_keys.add('data')

    # Extract any remaining readable text fields so no content is ever swallowed.
    for key, val in block.items():
        if key in handled_keys or val is None:
            continue
        if isinstance(val, str) and val.strip():
            parts.append(f'<p>{render_inline(val, ctx)}</p>')
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.strip():
                    parts.append(f'<p>{render_inline(item, ctx)}</p>')
                elif isinstance(item, Mapping):
                    parts.append(render_block(item, ctx))
        elif isinstance(val, Mapping):
            parts.append(_render_unknown_dict_values(val, ctx))

    inner = ''.join(parts)
    if not inner:
        return ''
    return f'<div class="o5e-fallback" data-block-type="{escape_html(btype)}">{inner}</div>'


def _render_unknown_dict_values(mapping: Mapping[str, Any], ctx: RenderContext) -> str:
    parts: list[str] = []
    for key, val in mapping.items():
        if key in {'type', 'id', 'style', 'page', 'source', 'ENG_name'} or val is None:
            continue
        if isinstance(val, str) and val.strip():
            parts.append(f'<p>{render_inline(val, ctx)}</p>')
        elif isinstance(val, list):
            parts.append(render_blocks(val, ctx=ctx))
        elif isinstance(val, Mapping):
            if 'type' in val:
                parts.append(render_block(val, ctx))
            else:
                parts.append(_render_unknown_dict_values(val, ctx))
    return ''.join(parts)


def render_block(block: Any, ctx: RenderContext) -> str:
    """Render one 5etools block (string, dict, or list) into sanitized HTML."""
    if block is None:
        return ''
    if isinstance(block, str):
        if not block.strip():
            return ''
        return f'<p>{render_inline(block, ctx)}</p>'
    if isinstance(block, list):
        return ''.join(render_block(item, ctx) for item in block)
    if not isinstance(block, Mapping):
        return f'<p>{escape_html(block)}</p>'

    btype = block.get('type')
    if not isinstance(btype, str):
        # Plain dict without explicit type: render entries/name if present, else fallback.
        if 'entries' in block or 'name' in block:
            parts: list[str] = []
            if block.get('name'):
                htag = _heading_tag(ctx.heading_depth)
                parts.append(f'<{htag}>{render_inline(block["name"], ctx)}</{htag}>')
            if block.get('entries'):
                parts.append(render_blocks(block['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))
            return ''.join(parts)
        return _render_unknown_dict_values(block, ctx)

    if btype in ('entries', 'section'):
        parts = []
        name = block.get('name')
        htag = _heading_tag(ctx.heading_depth)
        sec_id = block.get('id')
        id_attr = f' id="sec-{escape_html(sec_id)}"' if isinstance(sec_id, str) and sec_id.strip() else ''
        if name:
            parts.append(f'<{htag}{id_attr}>{render_inline(name, ctx)}</{htag}>')
        if block.get('entries'):
            parts.append(render_blocks(block['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))
        cls = 'o5e-section' if btype == 'section' else 'o5e-entries'
        return f'<section class="{cls}">{"".join(parts)}</section>'

    if btype == 'list':
        items = block.get('items')
        if not isinstance(items, list) or not items:
            return ''
        style = str(block.get('style') or '')
        cls = 'o5e-list o5e-list-plain' if 'no-bullets' in style or 'hang' in style else 'o5e-list'
        out = [f'<ul class="{cls}">']
        for item in items:
            if isinstance(item, str):
                out.append(f'<li>{render_inline(item, ctx)}</li>')
            elif isinstance(item, Mapping) and item.get('type') in ('item', 'itemSub', 'itemSpell'):
                out.append(f'<li>{render_block(item, ctx)}</li>')
            elif isinstance(item, Mapping) and item.get('type') == 'ingredient':
                out.append(f'<li>{_render_ingredient_block(item, ctx)}</li>')
            else:
                out.append(f'<li>{render_block(item, ctx)}</li>')
        out.append('</ul>')
        return ''.join(out)

    if btype in ('item', 'itemSub', 'itemSpell'):
        name = str(block.get('name') or '').strip()
        prefix = f'<strong class="o5e-item-name">{render_inline(name, ctx)}</strong> ' if name else ''
        cls = f'o5e-{btype}'
        if 'entry' in block and block['entry'] is not None:
            body = render_inline(block['entry'], ctx) if isinstance(block['entry'], str) else render_block(block['entry'], ctx)
            return f'<div class="{cls}">{prefix}{body}</div>'
        entries = block.get('entries')
        if isinstance(entries, list) and entries:
            first = entries[0]
            rest = entries[1:]
            first_html = render_inline(first, ctx) if isinstance(first, str) else render_block(first, ctx)
            rest_html = render_blocks(rest, ctx=ctx, heading_depth=ctx.heading_depth + 1) if rest else ''
            return f'<div class="{cls}"><p>{prefix}{first_html}</p>{rest_html}</div>'
        return f'<div class="{cls}">{prefix}</div>' if prefix else ''

    if btype == 'table':
        return _render_table(block, ctx)

    if btype == 'tableGroup':
        parts = ['<section class="o5e-table-group">']
        if block.get('name'):
            htag = _heading_tag(ctx.heading_depth)
            parts.append(f'<{htag}>{render_inline(block["name"], ctx)}</{htag}>')
        for tbl in block.get('tables') or ():
            parts.append(render_block(tbl, ctx))
        parts.append('</section>')
        return ''.join(parts)

    if btype == 'row':
        cells = block.get('row') if isinstance(block.get('row'), list) else []
        return '<tr>' + ''.join(f'<td>{_render_cell_value(c, ctx)}</td>' for c in cells) + '</tr>'

    if btype in ('cell', 'cellHeader'):
        return _render_cell_value(block, ctx)

    if btype == 'inset':
        parts = ['<aside class="o5e-inset">']
        if block.get('name'):
            htag = _heading_tag(ctx.heading_depth)
            parts.append(f'<{htag}>{render_inline(block["name"], ctx)}</{htag}>')
        if block.get('entries'):
            parts.append(render_blocks(block['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))
        parts.append('</aside>')
        return ''.join(parts)

    if btype == 'insetReadaloud':
        parts = ['<blockquote class="o5e-readaloud">']
        if block.get('name'):
            htag = _heading_tag(ctx.heading_depth)
            parts.append(f'<{htag}>{render_inline(block["name"], ctx)}</{htag}>')
        if block.get('entries'):
            parts.append(render_blocks(block['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))
        parts.append('</blockquote>')
        return ''.join(parts)

    if btype == 'quote':
        parts = ['<blockquote class="o5e-quote">']
        if block.get('entries'):
            parts.append(render_blocks(block['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))
        by = block.get('by')
        frm = block.get('from')
        if by or frm:
            cite_bits = [render_inline(x, ctx) for x in (by, frm) if x]
            parts.append(f'<footer class="o5e-quote-cite">—— {"，".join(cite_bits)}</footer>')
        parts.append('</blockquote>')
        return ''.join(parts)

    if btype == 'statblock':
        tag = str(block.get('tag') or 'creature')
        name = str(block.get('name') or '')
        source = str(block.get('source') or '')
        display = str(block.get('displayName') or name)
        payload = f'{name}|{source}|{display}' if display != name else f'{name}|{source}'
        ref_html = _render_entity_ref(tag, payload, f'{{@{tag} {payload}}}', ctx)
        return f'<div class="o5e-statblock-ref">{ref_html}</div>'

    if btype == 'statblockInline':
        data_type = str(block.get('dataType') or 'generic')
        data = block.get('data')
        if isinstance(data, Mapping):
            sub_entry = {'type': 'creature' if data_type == 'monster' else data_type, 'raw_kind': data_type, 'raw': data}
            prev_vars = ctx.template_vars
            ctx.template_vars = data
            try:
                body = _render_specialized_body(sub_entry, ctx)
            finally:
                ctx.template_vars = prev_vars
            title = str(data.get('name') or '')
            head = f'<{_heading_tag(ctx.heading_depth)}>{escape_html(title)}</{_heading_tag(ctx.heading_depth)}>' if title else ''
            return f'<aside class="o5e-statblock-inline">{head}{body}</aside>'
        return ''

    if btype == 'image':
        return _render_image_block(block, ctx)

    if btype == 'gallery':
        images = block.get('images')
        if not isinstance(images, list) or not images:
            return ''
        inner = ''.join(_render_image_block(img, ctx) for img in images if isinstance(img, Mapping))
        return f'<div class="o5e-gallery">{inner}</div>'

    if btype == 'options':
        parts = ['<div class="o5e-options">']
        count = block.get('count')
        if count:
            parts.append(f'<p class="o5e-options-note">选择以下 {escape_html(count)} 项：</p>')
        if block.get('entries'):
            parts.append(render_blocks(block['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))
        parts.append('</div>')
        return ''.join(parts)

    if btype in ('variant', 'variantInner', 'variantSub'):
        cls = f'o5e-{btype}'
        tag_wrap = 'aside' if btype == 'variant' else 'div'
        parts = [f'<{tag_wrap} class="{cls}">']
        if block.get('name'):
            htag = _heading_tag(ctx.heading_depth)
            parts.append(f'<{htag}>{render_inline(block["name"], ctx)}</{htag}>')
        if block.get('entries'):
            parts.append(render_blocks(block['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))
        parts.append(f'</{tag_wrap}>')
        return ''.join(parts)

    if btype == 'abilityDc':
        name = str(block.get('name') or '')
        attrs = [ABILITY_ABV_ZH.get(str(a).lower(), str(a)) for a in (block.get('attributes') or ())]
        attr_str = ' 或 '.join(attrs) if attrs else '施法'
        return f'<p class="o5e-ability-dc"><strong>{escape_html(name)}豁免 DC</strong> = 8 + 熟练加值 + {escape_html(attr_str)}调整值</p>'

    if btype == 'abilityAttackMod':
        name = str(block.get('name') or '')
        attrs = [ABILITY_ABV_ZH.get(str(a).lower(), str(a)) for a in (block.get('attributes') or ())]
        attr_str = ' 或 '.join(attrs) if attrs else '施法'
        return f'<p class="o5e-ability-atk"><strong>{escape_html(name)}攻击调整值</strong> = 熟练加值 + {escape_html(attr_str)}调整值</p>'

    if btype == 'abilityGeneric':
        name = str(block.get('name') or '')
        text = str(block.get('text') or '')
        attrs = [ABILITY_ABV_ZH.get(str(a).lower(), str(a)) for a in (block.get('attributes') or ())]
        prefix = f'<strong>{escape_html(name)}</strong> = ' if name else ''
        suffix = f' + {" 或 ".join(attrs)}调整值' if attrs else ''
        return f'<p class="o5e-ability-generic">{prefix}{render_inline(text, ctx)}{escape_html(suffix)}</p>'

    if btype == 'spellcasting':
        return _render_spellcasting_block(block, ctx)

    if btype == 'refClassFeature':
        return _render_ref_block('classFeature', block.get('classFeature'), ctx)
    if btype == 'refSubclassFeature':
        return _render_ref_block('subclassFeature', block.get('subclassFeature'), ctx)
    if btype == 'refOptionalfeature':
        return _render_ref_block('optfeature', block.get('optionalfeature'), ctx)
    if btype == 'refFeat':
        return _render_ref_block('feat', block.get('feat'), ctx)

    if btype == 'optfeature':
        parts = ['<section class="o5e-optfeature">']
        if block.get('name'):
            htag = _heading_tag(ctx.heading_depth)
            parts.append(f'<{htag}>{render_inline(block["name"], ctx)}</{htag}>')
        if block.get('prerequisite'):
            parts.append(f'<p class="o5e-prereq"><em>先决条件：{render_inline(block["prerequisite"], ctx)}</em></p>')
        if block.get('entries'):
            parts.append(render_blocks(block['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))
        parts.append('</section>')
        return ''.join(parts)

    if btype == 'flowchart':
        blocks = block.get('blocks') if isinstance(block.get('blocks'), list) else []
        inner = ''.join(render_block(b, ctx) for b in blocks)
        return f'<div class="o5e-flowchart">{inner}</div>'

    if btype == 'flowBlock':
        parts = ['<div class="o5e-flow-block">']
        if block.get('name'):
            htag = _heading_tag(ctx.heading_depth)
            parts.append(f'<{htag}>{render_inline(block["name"], ctx)}</{htag}>')
        if block.get('entries'):
            parts.append(render_blocks(block['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))
        parts.append('</div>')
        return ''.join(parts)

    if btype == 'homebrew':
        parts = ['<div class="o5e-homebrew-block">']
        if block.get('movedTo'):
            parts.append(f'<p class="o5e-homebrew-note">移动至：{render_inline(block["movedTo"], ctx)}</p>')
        if block.get('entries'):
            parts.append(render_blocks(block['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))
        parts.append('</div>')
        return ''.join(parts)

    if btype == 'wrapper':
        items = block.get('wrappeds') if isinstance(block.get('wrappeds'), list) else block.get('entries')
        if not items:
            return ''
        return f'<div class="o5e-wrapper">{render_blocks(items, ctx=ctx, heading_depth=ctx.heading_depth)}</div>'

    if btype == 'inline':
        entries = block.get('entries') if isinstance(block.get('entries'), list) else []
        inner = ''.join(
            render_inline(it, ctx) if isinstance(it, str) else render_block(it, ctx)
            for it in entries
        )
        return f'<span class="o5e-inline">{inner}</span>'

    if btype == 'inlineBlock':
        entries = block.get('entries') if isinstance(block.get('entries'), list) else []
        inner = ''.join(
            render_inline(it, ctx) if isinstance(it, str) else render_block(it, ctx)
            for it in entries
        )
        return f'<div class="o5e-inline-block">{inner}</div>'

    if btype == 'dice':
        return _render_dice_block(block)

    if btype == 'bonus':
        val = block.get('value')
        return f'<span class="o5e-bonus">{escape_html(_format_signed(val) if val is not None else "")}</span>'

    if btype == 'bonusSpeed':
        val = block.get('value')
        if val in (None, 0, '0'):
            return '<span class="o5e-bonus-speed">—</span>'
        return f'<span class="o5e-bonus-speed">{escape_html(_format_signed(val))} 尺</span>'

    if btype == 'link':
        return _render_link_block(block, ctx)

    if btype == 'hr':
        return '<hr>'

    if btype == 'ingredient':
        return _render_ingredient_block(block, ctx)

    if btype == 'itemUses':
        return _render_item_uses(block, ctx)

    # Fallback for any unrecognized block type: never swallow content.
    return _render_unknown_block(block, ctx)


def render_blocks(
    blocks: Any,
    resolver: LinkResolverProtocol | None = None,
    media_checker: Callable[[str], bool] | None = None,
    *,
    ctx: RenderContext | None = None,
    heading_depth: int = 3,
    default_source: str = '',
) -> str:
    """Render a list or single 5etools entry block into sanitized HTML."""
    if ctx is None:
        ctx = RenderContext(
            resolver=resolver or LinkResolver(),
            media_checker=media_checker or (lambda _rel: False),
            default_source=default_source,
            heading_depth=heading_depth,
        )
        prev_depth = None
    else:
        prev_depth = ctx.heading_depth
        ctx.heading_depth = heading_depth

    try:
        if blocks is None:
            return ''
        if isinstance(blocks, list):
            return ''.join(render_block(item, ctx) for item in blocks)
        return render_block(blocks, ctx)
    finally:
        if prev_depth is not None:
            ctx.heading_depth = prev_depth


def render_blocks_with_meta(
    blocks: Any,
    resolver: LinkResolverProtocol | None = None,
    media_checker: Callable[[str], bool] | None = None,
    *,
    heading_depth: int = 3,
    default_source: str = '',
) -> dict[str, Any]:
    """Render blocks and return HTML together with plain text and diagnostics."""
    ctx = RenderContext(
        resolver=resolver or LinkResolver(),
        media_checker=media_checker or (lambda _rel: False),
        default_source=default_source,
        heading_depth=heading_depth,
    )
    rendered_html = render_blocks(blocks, ctx=ctx, heading_depth=heading_depth)
    return {
        'html': rendered_html,
        'body_text': strip_html_to_text(rendered_html),
        'media_summary': ctx.media_summary(),
        'link_events': ctx.link_events,
        'unknown_tags': ctx.unknown_tags,
        'unsupported_structures': ctx.unsupported_structures,
    }


# ---------------------------------------------------------------------------
# Specialized card renderers
# ---------------------------------------------------------------------------

def _render_params_dl(pairs: Sequence[tuple[str, str, bool]]) -> str:
    items = [(lbl, val, wide) for lbl, val, wide in pairs if val]
    if not items:
        return ''
    out = ['<dl class="parameters">']
    for label, val_html, wide in items:
        cls = ' class="wide"' if wide or len(strip_html_to_text(val_html)) > 24 else ''
        out.append(f'<div{cls}><dt>{escape_html(label)}</dt><dd>{val_html}</dd></div>')
    out.append('</dl>')
    return ''.join(out)


def _render_fluff_section(fluff: Any, ctx: RenderContext) -> str:
    if not isinstance(fluff, Mapping):
        return ''
    parts: list[str] = []
    entries = fluff.get('entries')
    if entries:
        parts.append(render_blocks(entries, ctx=ctx, heading_depth=ctx.heading_depth + 1))
    images = fluff.get('images')
    if isinstance(images, list) and images:
        imgs_html = ''.join(_render_image_block(img, ctx) for img in images if isinstance(img, Mapping))
        if imgs_html:
            parts.append(f'<div class="o5e-fluff-images">{imgs_html}</div>')
    if not parts:
        return ''
    htag = _heading_tag(ctx.heading_depth)
    return f'<section class="o5e-fluff"><{htag}>背景与配图</{htag}>{"".join(parts)}</section>'


def _format_spell_time(raw_time: Any, ctx: RenderContext) -> str:
    if not isinstance(raw_time, list):
        return render_inline(raw_time, ctx) if raw_time else ''
    parts: list[str] = []
    for item in raw_time:
        if not isinstance(item, Mapping):
            parts.append(escape_html(item))
            continue
        num = item.get('number')
        unit = str(item.get('unit') or '')
        unit_zh = TIME_UNIT_ZH.get(unit, unit)
        seg = f'{num} {unit_zh}'.strip() if num is not None else unit_zh
        cond = item.get('condition')
        if cond:
            seg += f'（{render_inline(cond, ctx)}）'
        note = item.get('note')
        if note:
            seg += f'（{render_inline(note, ctx)}）'
        parts.append(seg)
    return ' 或 '.join(parts)


def _format_spell_range(raw_range: Any, ctx: RenderContext) -> str:
    if not isinstance(raw_range, Mapping):
        return render_inline(raw_range, ctx) if raw_range else ''
    rtype = str(raw_range.get('type') or '')
    if rtype == 'special':
        return '特殊'
    dist = raw_range.get('distance')
    dist_str = ''
    if isinstance(dist, Mapping):
        dtype = str(dist.get('type') or '')
        amt = dist.get('amount')
        unit_zh = DISTANCE_TYPE_ZH.get(dtype, dtype)
        if amt is not None:
            dist_str = f'{amt} {unit_zh}'.strip()
        else:
            dist_str = unit_zh
    shape = RANGE_TYPE_ZH.get(rtype, '')
    if shape and dist_str:
        return escape_html(f'自身（{dist_str}{shape}）')
    return escape_html(dist_str or shape)


def _format_spell_components(comp: Any, ctx: RenderContext) -> str:
    if not isinstance(comp, Mapping):
        return render_inline(comp, ctx) if comp else ''
    bits: list[str] = []
    if comp.get('v'):
        bits.append('声音（V）')
    if comp.get('s'):
        bits.append('姿势（S）')
    mat = comp.get('m')
    if mat:
        if isinstance(mat, Mapping):
            m_txt = render_inline(mat.get('text') or '', ctx)
            bits.append(f'材料（M：{m_txt}）' if m_txt else '材料（M）')
        elif isinstance(mat, str):
            bits.append(f'材料（M：{render_inline(mat, ctx)}）')
        else:
            bits.append('材料（M）')
    if comp.get('r'):
        bits.append('皇室印记（R）')
    return '、'.join(bits)


def _format_spell_duration(raw_dur: Any, ctx: RenderContext) -> str:
    if not isinstance(raw_dur, list):
        return render_inline(raw_dur, ctx) if raw_dur else ''
    parts: list[str] = []
    for item in raw_dur:
        if not isinstance(item, Mapping):
            parts.append(escape_html(item))
            continue
        dtype = str(item.get('type') or '')
        if dtype == 'instant':
            seg = '立即'
        elif dtype == 'permanent':
            ends = item.get('ends') or ()
            end_map = {'dispel': '直至被解除', 'trigger': '直至被触发', 'discharge': '直至释放'}
            ends_zh = '或'.join(end_map.get(str(e), str(e)) for e in ends)
            seg = ends_zh or '永久'
        elif dtype == 'special':
            seg = '特殊'
        elif dtype == 'timed':
            d = item.get('duration') or {}
            amt = d.get('amount') if isinstance(d, Mapping) else None
            unit = str(d.get('type') or '') if isinstance(d, Mapping) else ''
            unit_zh = TIME_UNIT_ZH.get(unit, unit)
            prefix = '专注，至多 ' if item.get('concentration') else ('至多 ' if isinstance(d, Mapping) and d.get('upTo') else '')
            seg = f'{prefix}{amt} {unit_zh}'.strip()
        else:
            seg = dtype
        if item.get('condition'):
            seg += f'（{render_inline(item["condition"], ctx)}）'
        parts.append(seg)
    return ' 或 '.join(parts)


def _render_spell_card(entry: Mapping[str, Any], raw: Mapping[str, Any], ctx: RenderContext) -> str:
    parts: list[str] = []
    level = raw.get('level')
    school_code = str(raw.get('school') or '')
    school_zh = SPELL_SCHOOL_ZH.get(school_code, school_code)
    meta = raw.get('meta') if isinstance(raw.get('meta'), Mapping) else {}
    ritual = bool(meta.get('ritual'))
    if level == 0 or str(level) == '0':
        subtitle = f'{school_zh}戏法' if school_zh else '戏法'
    elif level is not None:
        subtitle = f'{level} 环 {school_zh}'.strip()
    else:
        subtitle = school_zh
    if ritual:
        subtitle += '（仪式）'
    if subtitle:
        parts.append(f'<p class="o5e-subtitle">{escape_html(subtitle)}</p>')

    params = [
        ('施法时间', _format_spell_time(raw.get('time'), ctx), False),
        ('施法距离', _format_spell_range(raw.get('range'), ctx), False),
        ('法术成分', _format_spell_components(raw.get('components'), ctx), True),
        ('持续时间', _format_spell_duration(raw.get('duration'), ctx), False),
    ]
    parts.append(_render_params_dl(params))

    if raw.get('entries'):
        parts.append(render_blocks(raw['entries'], ctx=ctx))

    if raw.get('entriesHigherLevel'):
        parts.append('<div class="o5e-higher-level">' + render_blocks(raw['entriesHigherLevel'], ctx=ctx) + '</div>')

    # Available classes from fields, semantics entry, or raw.classes.
    class_names: list[str] = []
    fields = entry.get('fields') if isinstance(entry.get('fields'), Mapping) else {}
    sc = fields.get('spell_classes') or entry.get('spell_classes')
    if isinstance(sc, list):
        class_names.extend(str(c) for c in sc if c)
    elif isinstance(raw.get('classes'), Mapping):
        for item in raw['classes'].get('fromClassList') or ():
            if isinstance(item, Mapping) and item.get('name'):
                class_names.append(str(item['name']))
    if class_names:
        dedup = list(dict.fromkeys(class_names))
        class_links = []
        for cname in dedup:
            res = ctx.resolver.resolve_entity('class', cname)
            if res.status == 'resolved' and res.entry_id:
                class_links.append(f'<button type="button" class="ref" data-entry="{escape_html(res.entry_id)}">{escape_html(cname)}</button>')
            else:
                class_links.append(escape_html(cname))
        parts.append(f'<p class="o5e-spell-classes"><strong>可用职业：</strong>{"、".join(class_links)}</p>')

    parts.append(_render_fluff_section(entry.get('fluff') or raw.get('fluff'), ctx))
    return ''.join(parts)


def _format_creature_type(raw_type: Any) -> str:
    if isinstance(raw_type, str):
        return CREATURE_TYPE_ZH.get(raw_type.lower(), raw_type)
    if isinstance(raw_type, Mapping):
        base = raw_type.get('type')
        if isinstance(base, Mapping) and isinstance(base.get('choose'), list):
            base_zh = '或'.join(CREATURE_TYPE_ZH.get(str(t).lower(), str(t)) for t in base['choose'])
        else:
            base_zh = CREATURE_TYPE_ZH.get(str(base or '').lower(), str(base or ''))
        tags = raw_type.get('tags') or ()
        tag_strs = [t.get('tag', '') if isinstance(t, Mapping) else str(t) for t in tags]
        tag_strs = [t for t in tag_strs if t]
        swarm = raw_type.get('swarmSize')
        if swarm:
            base_zh = f'{SIZE_ABV_ZH.get(str(swarm), str(swarm))}{base_zh}集群'
        if tag_strs:
            return f'{base_zh}（{"、".join(tag_strs)}）'
        return base_zh
    return ''


def _format_alignment(raw_align: Any) -> str:
    if not isinstance(raw_align, list):
        return str(raw_align or '')
    out: list[str] = []
    for item in raw_align:
        if isinstance(item, str):
            out.append(ALIGNMENT_ABV_ZH.get(item, item))
        elif isinstance(item, Mapping):
            if item.get('special'):
                out.append(str(item['special']))
            elif isinstance(item.get('alignment'), list):
                out.append(''.join(ALIGNMENT_ABV_ZH.get(str(a), str(a)) for a in item['alignment']))
    if all(isinstance(x, str) for x in raw_align):
        return ''.join(out)
    return ' 或 '.join(x for x in out if x)


def _format_ac(raw_ac: Any, ctx: RenderContext) -> str:
    if not isinstance(raw_ac, list):
        return escape_html(raw_ac) if raw_ac is not None else ''
    parts: list[str] = []
    for item in raw_ac:
        if isinstance(item, (int, float)):
            parts.append(escape_html(item))
        elif isinstance(item, Mapping):
            if item.get('special'):
                parts.append(render_inline(item['special'], ctx))
                continue
            val = escape_html(item.get('ac', ''))
            notes: list[str] = []
            if isinstance(item.get('from'), list):
                notes.append('、'.join(render_inline(f, ctx) for f in item['from']))
            if item.get('condition'):
                notes.append(render_inline(item['condition'], ctx))
            if notes:
                val += f'（{"；".join(notes)}）'
            parts.append(val)
    return '，'.join(parts)


def _format_hp(raw_hp: Any, ctx: RenderContext) -> str:
    if isinstance(raw_hp, (int, float)):
        return escape_html(raw_hp)
    if isinstance(raw_hp, Mapping):
        if raw_hp.get('special'):
            return render_inline(raw_hp['special'], ctx)
        avg = raw_hp.get('average')
        formula = raw_hp.get('formula')
        if avg is not None and formula:
            return f'{escape_html(avg)}（<span class="o5e-dice">{escape_html(formula)}</span>）'
        if avg is not None:
            return escape_html(avg)
        if formula:
            return f'<span class="o5e-dice">{escape_html(formula)}</span>'
    return ''


def _format_speed(raw_speed: Any, ctx: RenderContext) -> str:
    if isinstance(raw_speed, (int, float)):
        return escape_html(f'{raw_speed} 尺')
    if not isinstance(raw_speed, Mapping):
        return render_inline(raw_speed, ctx) if raw_speed else ''
    labels = {'walk': '步行', 'fly': '飞行', 'swim': '游泳', 'climb': '攀爬', 'burrow': '掘穴'}
    parts: list[str] = []
    for key, zh in labels.items():
        val = raw_speed.get(key)
        if val is None or val is False:
            continue
        if isinstance(val, Mapping):
            num = val.get('number')
            cond = val.get('condition')
            seg = f'{zh} {num} 尺' if num is not None else zh
            if cond:
                seg += f'（{render_inline(cond, ctx)}）'
            parts.append(seg)
        elif val is True:
            parts.append(zh)
        else:
            parts.append(f'{zh} {escape_html(val)} 尺')
    if raw_speed.get('canHover') and parts:
        parts.append('（悬浮）')
    return '，'.join(parts)


def _format_damage_list(items: Any, ctx: RenderContext) -> str:
    if not isinstance(items, list):
        return render_inline(items, ctx) if items else ''
    parts: list[str] = []
    for item in items:
        if isinstance(item, str):
            parts.append(render_inline(item, ctx))
        elif isinstance(item, Mapping):
            if item.get('special'):
                parts.append(render_inline(item['special'], ctx))
                continue
            sub_key = next((k for k in ('resist', 'immune', 'vulnerable', 'conditionImmune') if isinstance(item.get(k), list)), None)
            inner = _format_damage_list(item[sub_key], ctx) if sub_key else ''
            pre = f'{render_inline(item["preNote"], ctx)} ' if item.get('preNote') else ''
            note = f'（{render_inline(item["note"], ctx)}）' if item.get('note') else ''
            parts.append(f'{pre}{inner}{note}'.strip())
    return '；'.join(p for p in parts if p)


def _format_cr(raw_cr: Any) -> str:
    if raw_cr is None:
        return ''
    if isinstance(raw_cr, str):
        xp = CR_XP.get(raw_cr)
        return f'{raw_cr}（{xp:,} XP）' if xp is not None else raw_cr
    if isinstance(raw_cr, Mapping):
        base = str(raw_cr.get('cr') or '')
        xp = CR_XP.get(base)
        txt = f'{base}（{xp:,} XP）' if xp is not None else base
        if raw_cr.get('lair'):
            txt += f'；巢穴内 CR {raw_cr["lair"]}'
        if raw_cr.get('coven'):
            txt += f'；集会中 CR {raw_cr["coven"]}'
        return txt
    return str(raw_cr)


def _render_ability_table(raw: Mapping[str, Any]) -> str:
    if not any(raw.get(ab) is not None for ab in ABILITY_ORDER):
        return ''
    headers = ''.join(f'<th scope="col">{ABILITY_ABV_ZH[ab]}</th>' for ab in ABILITY_ORDER)
    cells: list[str] = []
    for ab in ABILITY_ORDER:
        val = raw.get(ab)
        if isinstance(val, (int, float)):
            mod = math.floor((int(val) - 10) / 2)
            mod_str = f'+{mod}' if mod >= 0 else str(mod)
            cells.append(f'<td class="short">{int(val)}（{mod_str}）</td>')
        elif val is not None:
            cells.append(f'<td class="short">{escape_html(val)}</td>')
        else:
            cells.append('<td class="short">—</td>')
    return f'<table class="o5e-abilities"><thead><tr>{headers}</tr></thead><tbody><tr>{"".join(cells)}</tr></tbody></table>'


def _render_creature_entries_section(title: str, items: Any, ctx: RenderContext, header_note: Any = None) -> str:
    if not items and not header_note:
        return ''
    htag = _heading_tag(ctx.heading_depth)
    out = [f'<section class="o5e-stat-section"><{htag}>{escape_html(title)}</{htag}>']
    if header_note:
        out.append(render_blocks(header_note, ctx=ctx, heading_depth=ctx.heading_depth + 1))
    if isinstance(items, list):
        for item in items:
            if isinstance(item, Mapping):
                name = str(item.get('name') or '').strip()
                entries = item.get('entries')
                if name and isinstance(entries, list) and entries:
                    first = entries[0]
                    rest = entries[1:]
                    if isinstance(first, str):
                        out.append(f'<p><strong>{render_inline(name, ctx)}：</strong>{render_inline(first, ctx)}</p>')
                    else:
                        out.append(f'<p><strong>{render_inline(name, ctx)}：</strong></p>{render_block(first, ctx)}')
                    if rest:
                        out.append(render_blocks(rest, ctx=ctx, heading_depth=ctx.heading_depth + 1))
                else:
                    out.append(render_block(item, ctx))
            else:
                out.append(render_block(item, ctx))
    elif items:
        out.append(render_block(items, ctx))
    out.append('</section>')
    return ''.join(out)


def _audio_path_candidates(raw_path: str) -> list[str]:
    """Generate exact and stem-swapped audio candidate paths (.mp3 -> .opus fallback)."""
    clean = raw_path.strip().lstrip('/')
    if not clean:
        return []
    candidates = [f'audio/{clean}', clean]
    lower = clean.lower()
    audio_exts = ('.opus', '.mp3', '.ogg', '.wav')
    stem = None
    for ext in audio_exts:
        if lower.endswith(ext):
            stem = clean[:-len(ext)]
            break
    if stem:
        for ext in audio_exts:
            alt = f'{stem}{ext}'
            for cand in (f'audio/{alt}', alt):
                if cand not in candidates:
                    candidates.append(cand)
    return candidates


def _render_audio_clip(
    sound_clip: Any,
    ctx: RenderContext,
    *,
    local_audio_path: str = '',
) -> str:
    if local_audio_path:
        local = ctx.check_local_media(local_audio_path, f'audio/{local_audio_path}')
        if local:
            ctx.record_local_media(local)
            return (
                f'<div class="o5e-audio">'
                f'<span class="o5e-audio-label">读音</span>'
                f'<audio controls preload="none" src="{escape_html(_media_url(local))}"></audio>'
                f'</div>'
            )
    if not isinstance(sound_clip, Mapping):
        return ''
    if sound_clip.get('type') == 'internal':
        raw_path = str(sound_clip.get('path') or '').strip()
        cands = _audio_path_candidates(raw_path)
        local = ctx.check_local_media(*cands) if cands else None
        if local:
            ctx.record_local_media(local)
            return (
                f'<div class="o5e-audio">'
                f'<span class="o5e-audio-label">读音</span>'
                f'<audio controls preload="none" src="{escape_html(_media_url(local))}"></audio>'
                f'</div>'
            )
        san = sanitize_media_path(f'audio/{raw_path}') if raw_path else None
        if san:
            ctx.record_missing_local_media(san)
            ctx.record_remote_media(san)
        return '<div class="o5e-audio o5e-media-remote"><span class="o5e-media-badge">远端音频（未离线收录）</span></div>'
    if sound_clip.get('type') == 'external':
        url = sanitize_url(str(sound_clip.get('url') or ''), allow_relative=False)
        if url:
            ctx.record_remote_media(url)
            cache_rel = remote_media_cache_path(url)
            if cache_rel:
                ctx.record_remote_urls(cache_rel, (url,))
            lazy_attr = f' data-lazy-media="{escape_html(_media_url(cache_rel))}" data-media-kind="audio"' if cache_rel else ''
            return (
                f'<div class="o5e-audio o5e-media-remote"{lazy_attr}>'
                f'<span class="o5e-media-badge">远端音频（未离线收录）</span> '
                f'<a class="o5e-media-link" href="{escape_html(url)}" target="_blank" rel="noopener noreferrer">收听原音频 ↗</a>'
                f'</div>'
            )
    return ''


def _render_creature_card(entry: Mapping[str, Any], raw: Mapping[str, Any], ctx: RenderContext) -> str:
    parts: list[str] = []
    sizes = raw.get('size') if isinstance(raw.get('size'), list) else ([raw['size']] if raw.get('size') else [])
    size_zh = '或'.join(SIZE_ABV_ZH.get(str(s), str(s)) for s in sizes)
    ctype_zh = _format_creature_type(raw.get('type'))
    align_zh = _format_alignment(raw.get('alignment'))
    subtitle = ' '.join(x for x in (size_zh, ctype_zh) if x)
    if align_zh:
        subtitle = f'{subtitle}，{align_zh}' if subtitle else align_zh
    if subtitle:
        parts.append(f'<p class="o5e-subtitle">{escape_html(subtitle)}</p>')

    audio_html = _render_audio_clip(
        raw.get('soundClip'),
        ctx,
        local_audio_path=str(entry.get('local_audio_path') or ''),
    )
    if audio_html:
        parts.append(audio_html)

    core_params = [
        ('护甲等级', _format_ac(raw.get('ac'), ctx), False),
        ('生命值', _format_hp(raw.get('hp'), ctx), False),
        ('速度', _format_speed(raw.get('speed'), ctx), True),
        ('挑战等级', escape_html(_format_cr(raw.get('cr'))), False),
    ]
    parts.append(_render_params_dl(core_params))
    parts.append(_render_ability_table(raw))

    sec_params: list[tuple[str, str, bool]] = []
    if isinstance(raw.get('save'), Mapping) and raw['save']:
        saves = '，'.join(f'{ABILITY_ABV_ZH.get(k.lower(), k)} {v}' for k, v in raw['save'].items())
        sec_params.append(('豁免', escape_html(saves), False))
    if isinstance(raw.get('skill'), Mapping) and raw['skill']:
        skills = '，'.join(f'{k} {v}' for k, v in raw['skill'].items() if isinstance(v, (str, int, float)))
        sec_params.append(('技能', escape_html(skills), True))
    if raw.get('vulnerable'):
        sec_params.append(('伤害易伤', _format_damage_list(raw['vulnerable'], ctx), True))
    if raw.get('resist'):
        sec_params.append(('伤害抗性', _format_damage_list(raw['resist'], ctx), True))
    if raw.get('immune'):
        sec_params.append(('伤害免疫', _format_damage_list(raw['immune'], ctx), True))
    if raw.get('conditionImmune'):
        sec_params.append(('状态免疫', _format_damage_list(raw['conditionImmune'], ctx), True))
    senses_list = list(raw.get('senses') or ()) if isinstance(raw.get('senses'), list) else []
    if raw.get('passive') is not None:
        senses_list.append(f'被动察觉 {raw["passive"]}')
    if senses_list:
        sec_params.append(('感官', '，'.join(render_inline(s, ctx) for s in senses_list), True))
    langs = raw.get('languages')
    if isinstance(langs, list) and langs:
        sec_params.append(('语言', '，'.join(render_inline(l, ctx) for l in langs), True))
    parts.append(_render_params_dl(sec_params))

    parts.append(_render_creature_entries_section('特质', raw.get('trait'), ctx))
    if isinstance(raw.get('spellcasting'), list) and raw['spellcasting']:
        for sc in raw['spellcasting']:
            if isinstance(sc, Mapping):
                parts.append(_render_spellcasting_block(sc, ctx))
    parts.append(_render_creature_entries_section('动作', raw.get('action'), ctx, raw.get('actionHeader')))
    parts.append(_render_creature_entries_section('附赠动作', raw.get('bonus'), ctx, raw.get('bonusHeader')))
    parts.append(_render_creature_entries_section('反应', raw.get('reaction'), ctx, raw.get('reactionHeader')))
    parts.append(_render_creature_entries_section('传奇动作', raw.get('legendary'), ctx, raw.get('legendaryHeader')))
    parts.append(_render_creature_entries_section('神话动作', raw.get('mythic'), ctx, raw.get('mythicHeader')))
    if raw.get('variant'):
        parts.append(_render_creature_entries_section('变体', raw.get('variant'), ctx))

    # Legendary group (lair actions & regional effects).
    legroup = entry.get('legendary_group') or raw.get('legendaryGroupData')
    if isinstance(legroup, Mapping):
        if legroup.get('lairActions'):
            parts.append(_render_creature_entries_section('巢穴动作', legroup['lairActions'], ctx))
        if legroup.get('regionalEffects'):
            parts.append(_render_creature_entries_section('区域效应', legroup['regionalEffects'], ctx))
        if legroup.get('mythicEncounter'):
            parts.append(_render_creature_entries_section('神话遭遇', legroup['mythicEncounter'], ctx))

    parts.append(_render_fluff_section(entry.get('fluff') or raw.get('fluff'), ctx))
    return ''.join(parts)


def _parse_class_feature_ref(item: Any) -> tuple[str, int, str]:
    """Extract ``(ref_string, level_int, display_name)`` from a classFeatures entry."""
    ref = item.get('classFeature') if isinstance(item, Mapping) else (item if isinstance(item, str) else '')
    if not ref:
        return ('', 0, '')
    parts = [p.strip() for p in ref.split('|')]
    name = parts[0] if len(parts) > 0 else ''
    lvl_str = parts[3] if len(parts) > 3 else ''
    lvl = int(lvl_str) if lvl_str.isdigit() else 0
    return (ref, lvl, name)


def _format_starting_proficiencies(sp: Any, ctx: RenderContext) -> list[tuple[str, str, bool]]:
    if not isinstance(sp, Mapping):
        return []
    pairs: list[tuple[str, str, bool]] = []
    for key, label in (('armor', '护甲熟练'), ('weapons', '武器熟练'), ('tools', '工具熟练')):
        vals = sp.get(key)
        if isinstance(vals, list) and vals:
            formatted = []
            for v in vals:
                if isinstance(v, Mapping):
                    formatted.append(render_inline(v.get('full') or v.get('proficiency') or '', ctx))
                else:
                    formatted.append(render_inline(v, ctx))
            pairs.append((label, '、'.join(x for x in formatted if x), False))
    skills = sp.get('skills')
    if isinstance(skills, list) and skills:
        skill_bits = []
        for s in skills:
            if isinstance(s, Mapping) and isinstance(s.get('choose'), Mapping):
                ch = s['choose']
                cnt = ch.get('count', 1)
                frm = ch.get('from') or ()
                skill_bits.append(f'任选 {cnt} 项：{"、".join(escape_html(x) for x in frm)}')
            elif isinstance(s, Mapping) and s.get('any'):
                skill_bits.append(f'任选 {escape_html(s["any"])} 项技能')
            else:
                skill_bits.append(render_inline(s, ctx))
        pairs.append(('技能熟练', '；'.join(skill_bits), True))
    return pairs


def _render_class_progression_table(
    raw: Mapping[str, Any],
    features_by_level: Mapping[int, list[tuple[str, str]]],
    ctx: RenderContext,
) -> str:
    table_groups = raw.get('classTableGroups') if isinstance(raw.get('classTableGroups'), list) else []
    extra_cols: list[tuple[str, list[Any]]] = []
    for grp in table_groups:
        if not isinstance(grp, Mapping):
            continue
        col_labels = grp.get('colLabels') if isinstance(grp.get('colLabels'), list) else []
        rows = grp.get('rows') if isinstance(grp.get('rows'), list) else grp.get('rowsSpellProgression')
        if not isinstance(rows, list) or not col_labels:
            continue
        is_spell_slots = 'rowsSpellProgression' in grp and 'rows' not in grp
        for col_idx, lbl in enumerate(col_labels):
            col_cells = []
            for lvl_idx in range(20):
                val = rows[lvl_idx][col_idx] if lvl_idx < len(rows) and col_idx < len(rows[lvl_idx]) else ''
                if is_spell_slots and (val == 0 or val == '0'):
                    val = '—'
                col_cells.append(val)
            extra_cols.append((str(lbl), col_cells))

    out = ['<table class="o5e-progression"><thead><tr>']
    out.append('<th scope="col">等级</th><th scope="col">熟练加值</th><th scope="col">职业特性</th>')
    for lbl, _ in extra_cols:
        out.append(f'<th scope="col">{render_inline(lbl, ctx)}</th>')
    out.append('</tr></thead><tbody>')

    for lvl in range(1, 21):
        pb = 2 + (lvl - 1) // 4
        feat_links: list[str] = []
        for ref_str, _name in features_by_level.get(lvl, ()):
            feat_links.append(_render_entity_ref('classFeature', ref_str, f'{{@classFeature {ref_str}}}', ctx))
        feat_cell = '、'.join(feat_links) if feat_links else '—'
        out.append(f'<tr><td class="short">{lvl}</td><td class="short">+{pb}</td><td>{feat_cell}</td>')
        for _, col_cells in extra_cols:
            cell_val = col_cells[lvl - 1]
            out.append(f'<td class="short">{_render_cell_value(cell_val, ctx)}</td>')
        out.append('</tr>')

    out.append('</tbody></table>')
    return ''.join(out)


def _render_class_card(
    entry: Mapping[str, Any],
    raw: Mapping[str, Any],
    ctx: RenderContext,
    relations: Sequence[Mapping[str, Any]],
) -> str:
    parts: list[str] = []
    hd = raw.get('hd')
    hd_str = f'1d{hd["faces"]}' if isinstance(hd, Mapping) and hd.get('faces') else ''
    profs = raw.get('proficiency') if isinstance(raw.get('proficiency'), list) else []
    saves_str = '、'.join(ABILITY_ABV_ZH.get(str(p).lower(), str(p)) for p in profs)
    spell_ab = ABILITY_ABV_ZH.get(str(raw.get('spellcastingAbility') or '').lower(), str(raw.get('spellcastingAbility') or ''))

    params: list[tuple[str, str, bool]] = [
        ('生命骰', escape_html(hd_str), False),
        ('豁免熟练', escape_html(saves_str), False),
        ('施法关键属性', escape_html(spell_ab), False),
    ]
    params.extend(_format_starting_proficiencies(raw.get('startingProficiencies'), ctx))

    se = raw.get('startingEquipment')
    if isinstance(se, Mapping):
        eq_entries = se.get('default') or se.get('entries')
        if isinstance(eq_entries, list) and eq_entries:
            eq_html = '；'.join(render_inline(x, ctx) for x in eq_entries)
            params.append(('起始装备', eq_html, True))
    parts.append(_render_params_dl(params))

    features_by_level: dict[int, list[tuple[str, str]]] = {}
    for item in raw.get('classFeatures') or ():
        ref, lvl, name = _parse_class_feature_ref(item)
        if ref and lvl:
            features_by_level.setdefault(lvl, []).append((ref, name))

    htag = _heading_tag(ctx.heading_depth)
    parts.append(f'<{htag}>职业成长表</{htag}>')
    parts.append(_render_class_progression_table(raw, features_by_level, ctx))

    # Level-by-level feature index (and inline feature entries if attached).
    attached_features = entry.get('class_features') or raw.get('classFeatureEntries') or ()
    if attached_features:
        parts.append(f'<section class="o5e-class-features"><{htag}>职业特性</{htag}>')
        for feat in attached_features:
            if isinstance(feat, Mapping):
                fname = str(feat.get('name') or '')
                flvl = feat.get('level')
                sub_h = _heading_tag(ctx.heading_depth + 1)
                hdr = f'{flvl} 级：{fname}' if flvl else fname
                parts.append(f'<{sub_h}>{escape_html(hdr)}</{sub_h}>')
                if feat.get('entries'):
                    parts.append(render_blocks(feat['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 2))
        parts.append('</section>')
    elif features_by_level:
        parts.append(f'<section class="o5e-class-features"><{htag}>按等级特性列表</{htag}><ol class="o5e-level-features">')
        for lvl in sorted(features_by_level):
            links = '、'.join(
                _render_entity_ref('classFeature', ref, f'{{@classFeature {ref}}}', ctx)
                for ref, _ in features_by_level[lvl]
            )
            parts.append(f'<li><strong>{lvl} 级：</strong>{links}</li>')
        parts.append('</ol></section>')

    # Subclass entry points from relations or attached subclasses list.
    subclass_links: list[str] = []
    seen_sub_ids: set[str] = set()
    self_id = str(entry.get('id') or '')
    entry_index = getattr(ctx.resolver, 'entry_index', None) or {}
    for rel in relations:
        if not isinstance(rel, Mapping):
            continue
        pred = str(rel.get('predicate') or rel.get('type') or '')
        if pred not in ('has_subclass', 'subclass', 'belongs_to_class'):
            continue
        src_id = str(rel.get('source_id') or '')
        dst_id = str(rel.get('object_id') or rel.get('target_id') or '')
        # Handle both parent->child (has_subclass/subclass) and semantics.py child->parent (belongs_to_class).
        if pred == 'belongs_to_class' and src_id and (not self_id or dst_id == self_id):
            cand_id = src_id
            rel_meta = rel.get('meta')
            # Exclude classFeature/subclassFeature relations that carry a level in meta.
            if isinstance(rel_meta, Mapping) and rel_meta.get('level') is not None:
                continue
        else:
            cand_id = dst_id
        if not cand_id or cand_id == self_id or not _is_our5etools_id(cand_id):
            continue
        meta_entry = entry_index.get(cand_id) if isinstance(entry_index, Mapping) else None
        if isinstance(meta_entry, Mapping):
            cand_type = str(meta_entry.get('type') or meta_entry.get('raw_kind') or '')
            if cand_type and cand_type != 'subclass':
                continue
        label = str(
            rel.get('target_label')
            or rel.get('source_label')
            or rel.get('label')
            or (meta_entry.get('name_zh') or meta_entry.get('name') if isinstance(meta_entry, Mapping) else '')
            or ''
        )
        if cand_id not in seen_sub_ids:
            seen_sub_ids.add(cand_id)
            subclass_links.append(f'<li><button type="button" class="ref" data-entry="{escape_html(cand_id)}">{escape_html(label or cand_id)}</button></li>')
    for sub in entry.get('subclasses') or raw.get('subclasses') or ():
        if isinstance(sub, Mapping):
            sid = sub.get('id')
            sname = str(sub.get('name') or sub.get('shortName') or '')
            if sid and _is_our5etools_id(str(sid)) and str(sid) not in seen_sub_ids:
                seen_sub_ids.add(str(sid))
                subclass_links.append(f'<li><button type="button" class="ref" data-entry="{escape_html(sid)}">{escape_html(sname or sid)}</button></li>')
            elif sname:
                subclass_links.append(f'<li>{escape_html(sname)}</li>')
    if subclass_links:
        sub_title = str(raw.get('subclassTitle') or '子职')
        parts.append(f'<section class="o5e-subclasses"><{htag}>{escape_html(sub_title)}</{htag}><ul>{"".join(subclass_links)}</ul></section>')

    parts.append(_render_fluff_section(entry.get('fluff') or raw.get('fluff'), ctx))
    return ''.join(parts)


def _render_subclass_card(entry: Mapping[str, Any], raw: Mapping[str, Any], ctx: RenderContext) -> str:
    parts: list[str] = []
    cls_name = str(raw.get('className') or '')
    cls_src = str(raw.get('classSource') or '')
    cls_html = _render_entity_ref('class', f'{cls_name}|{cls_src}', f'{{@class {cls_name}|{cls_src}}}', ctx) if cls_name else ''
    spell_ab = ABILITY_ABV_ZH.get(str(raw.get('spellcastingAbility') or '').lower(), str(raw.get('spellcastingAbility') or ''))
    params = [
        ('所属职业', cls_html, False),
        ('子职简称', escape_html(raw.get('shortName') or ''), False),
        ('施法关键属性', escape_html(spell_ab), False),
    ]
    parts.append(_render_params_dl(params))

    sub_refs = raw.get('subclassFeatures') if isinstance(raw.get('subclassFeatures'), list) else []
    if sub_refs:
        htag = _heading_tag(ctx.heading_depth)
        links = []
        for ref in sub_refs:
            rstr = ref.get('subclassFeature') if isinstance(ref, Mapping) else (ref if isinstance(ref, str) else '')
            if rstr:
                links.append(f'<li>{_render_entity_ref("subclassFeature", rstr, f"{{@subclassFeature {rstr}}}", ctx)}</li>')
        if links:
            parts.append(f'<section class="o5e-subclass-features"><{htag}>子职特性</{htag}><ul>{"".join(links)}</ul></section>')

    if raw.get('entries'):
        parts.append(render_blocks(raw['entries'], ctx=ctx))
    parts.append(_render_fluff_section(entry.get('fluff') or raw.get('fluff'), ctx))
    return ''.join(parts)


def _format_item_value(val: Any) -> str:
    if not isinstance(val, (int, float)):
        return str(val or '')
    # 5etools stores item values in copper pieces (cp).
    cp = int(val)
    if cp >= 100 and cp % 100 == 0:
        return f'{cp // 100:,} gp'
    if cp >= 10 and cp % 10 == 0:
        return f'{cp // 10:,} sp'
    return f'{cp:,} cp'


def _render_item_card(entry: Mapping[str, Any], raw: Mapping[str, Any], ctx: RenderContext) -> str:
    parts: list[str] = []
    raw_type = str(raw.get('type') or '').split('|', 1)[0]
    type_zh = ITEM_TYPE_ZH.get(raw_type, raw_type)
    if raw.get('wondrous') and not type_zh:
        type_zh = '奇物'
    rarity_raw = str(raw.get('rarity') or '').lower()
    rarity_zh = RARITY_ZH.get(rarity_raw, str(raw.get('rarity') or ''))

    attune = raw.get('reqAttune')
    attune_str = ''
    if attune is True:
        attune_str = '需要同调'
    elif isinstance(attune, str) and attune.strip():
        attune_str = f'需要同调（{attune.strip()}）'

    dmg1 = raw.get('dmg1')
    dmg_type = DAMAGE_TYPE_ZH.get(str(raw.get('dmgType') or ''), str(raw.get('dmgType') or ''))
    dmg_str = f'{dmg1} {dmg_type}'.strip() if dmg1 else ''
    if raw.get('dmg2'):
        dmg_str += f'（两用 {raw["dmg2"]}）'

    props = raw.get('property') if isinstance(raw.get('property'), list) else []
    prop_str = '、'.join(WEAPON_PROP_ZH.get(str(p).split('|', 1)[0], str(p).split('|', 1)[0]) for p in props)
    mastery = raw.get('mastery') if isinstance(raw.get('mastery'), list) else []
    mastery_str = '、'.join(str(m).split('|', 1)[0] for m in mastery)

    params = [
        ('类型', escape_html(type_zh), False),
        ('稀有度', escape_html(rarity_zh), False),
        ('同调', escape_html(attune_str), False),
        ('价值', escape_html(_format_item_value(raw.get('value'))) if raw.get('value') is not None else '', False),
        ('重量', escape_html(f'{raw["weight"]} 磅') if raw.get('weight') is not None else '', False),
        ('伤害', escape_html(dmg_str), False),
        ('护甲等级', escape_html(raw.get('ac')) if raw.get('ac') is not None else '', False),
        ('射程', escape_html(f'{raw["range"]} 尺') if raw.get('range') else '', False),
        ('属性', escape_html(prop_str), False),
        ('精通', escape_html(mastery_str), False),
        ('充能', escape_html(f'{raw.get("charges")} 次（{raw.get("recharge")}）' if raw.get('recharge') else str(raw.get('charges'))) if raw.get('charges') is not None else '', False),
    ]
    parts.append(_render_params_dl(params))

    if raw.get('entries'):
        parts.append(render_blocks(raw['entries'], ctx=ctx))
    if raw.get('additionalEntries'):
        parts.append(render_blocks(raw['additionalEntries'], ctx=ctx))
    if isinstance(raw.get('items'), list) and raw['items']:
        htag = _heading_tag(ctx.heading_depth)
        links = [
            f'<li>{_render_entity_ref("item", it, f"{{@item {it}}}", ctx)}</li>'
            for it in raw['items'] if isinstance(it, str)
        ]
        if links:
            parts.append(f'<section class="o5e-item-group"><{htag}>包含物品</{htag}><ul>{"".join(links)}</ul></section>')
    if isinstance(raw.get('attachedSpells'), list) and raw['attachedSpells']:
        sp_links = '、'.join(_render_entity_ref('spell', s, f'{{@spell {s}}}', ctx) for s in raw['attachedSpells'] if isinstance(s, str))
        parts.append(f'<p class="o5e-attached-spells"><strong>附带法术：</strong>{sp_links}</p>')

    parts.append(_render_fluff_section(entry.get('fluff') or raw.get('fluff'), ctx))
    return ''.join(parts)


def _render_vehicle_card(entry: Mapping[str, Any], raw: Mapping[str, Any], ctx: RenderContext) -> str:
    parts: list[str] = []
    vtype = VEHICLE_TYPE_ZH.get(str(raw.get('vehicleType') or ''), str(raw.get('vehicleType') or ''))
    size = SIZE_ABV_ZH.get(str(raw.get('size') or ''), str(raw.get('size') or ''))
    params = [
        ('载具类型', escape_html(vtype), False),
        ('体型', escape_html(size), False),
        ('船员', escape_html(raw.get('capCrew')) if raw.get('capCrew') is not None else '', False),
        ('乘客', escape_html(raw.get('capPassenger')) if raw.get('capPassenger') is not None else '', False),
        ('载货', escape_html(f'{raw["capCargo"]} 吨') if raw.get('capCargo') is not None else '', False),
        ('旅行步速', escape_html(raw.get('pace')) if raw.get('pace') is not None else '', False),
        ('护甲等级', _format_ac(raw.get('ac'), ctx) if raw.get('ac') is not None else '', False),
        ('生命值', _format_hp(raw.get('hp'), ctx) if raw.get('hp') is not None else '', False),
        ('速度', _format_speed(raw.get('speed'), ctx) if raw.get('speed') is not None else '', True),
    ]
    parts.append(_render_params_dl(params))
    parts.append(_render_ability_table(raw))
    for key, label in (
        ('hull', '船体'),
        ('control', '控制组件'),
        ('movement', '动力组件'),
        ('weapon', '武器组件'),
        ('other', '其他组件'),
        ('trait', '特质'),
        ('action', '动作'),
        ('reaction', '反应'),
    ):
        val = raw.get(key)
        if val:
            parts.append(_render_creature_entries_section(label, val if isinstance(val, list) else [val], ctx))
    if raw.get('entries'):
        parts.append(render_blocks(raw['entries'], ctx=ctx))
    parts.append(_render_fluff_section(entry.get('fluff') or raw.get('fluff'), ctx))
    return ''.join(parts)


def _render_facility_card(entry: Mapping[str, Any], raw: Mapping[str, Any], ctx: RenderContext) -> str:
    params = [
        ('设施类别', escape_html(raw.get('facilityType') or ''), False),
        ('等级需求', escape_html(raw.get('level')) if raw.get('level') is not None else '', False),
        ('占地空间', escape_html('、'.join(str(x) for x in raw.get('space') or ())), False),
        ('指令', escape_html('、'.join(str(x) for x in raw.get('orders') or ())), False),
    ]
    parts = [_render_params_dl(params)]
    if raw.get('entries'):
        parts.append(render_blocks(raw['entries'], ctx=ctx))
    parts.append(_render_fluff_section(entry.get('fluff') or raw.get('fluff'), ctx))
    return ''.join(parts)


def _render_deck_or_card(entry: Mapping[str, Any], raw: Mapping[str, Any], ctx: RenderContext, raw_kind: str) -> str:
    parts: list[str] = []
    if raw_kind == 'card':
        params = [
            ('所属牌组', escape_html(raw.get('set') or ''), False),
            ('花色', escape_html(raw.get('suit') or ''), False),
            ('牌值', escape_html(raw.get('value')) if raw.get('value') is not None else '', False),
        ]
        parts.append(_render_params_dl(params))
        if isinstance(raw.get('face'), Mapping):
            parts.append(_render_image_block(raw['face'], ctx))
    else:
        cards = raw.get('cards') if isinstance(raw.get('cards'), list) else []
        params = [('卡牌数量', escape_html(len(cards)) if cards else '', False)]
        parts.append(_render_params_dl(params))
    if raw.get('entries'):
        parts.append(render_blocks(raw['entries'], ctx=ctx))
    if raw_kind != 'card' and isinstance(raw.get('cards'), list) and raw['cards']:
        htag = _heading_tag(ctx.heading_depth)
        items = []
        for c in raw['cards']:
            if isinstance(c, str):
                items.append(f'<li>{_render_entity_ref("card", c, f"{{@card {c}}}", ctx)}</li>')
            elif isinstance(c, Mapping) and c.get('uid'):
                uid = str(c['uid'])
                items.append(f'<li>{_render_entity_ref("card", uid, f"{{@card {uid}}}", ctx)}</li>')
        if items:
            parts.append(f'<section class="o5e-deck-cards"><{htag}>包含卡牌</{htag}><ul>{"".join(items)}</ul></section>')
    parts.append(_render_fluff_section(entry.get('fluff') or raw.get('fluff'), ctx))
    return ''.join(parts)


def _render_recipe_card(entry: Mapping[str, Any], raw: Mapping[str, Any], ctx: RenderContext) -> str:
    params = [
        ('菜式类型', escape_html('、'.join(str(x) for x in raw.get('dishTypes') or ())), False),
        ('饮食标签', escape_html(raw.get('diet') or ''), False),
    ]
    if isinstance(raw.get('serves'), Mapping):
        sv = raw['serves']
        s_txt = str(sv.get('exact') or f'{sv.get("min", "")}–{sv.get("max", "")}').strip('–')
        if s_txt:
            params.append(('份量', escape_html(f'{s_txt} 人份'), False))
    parts = [_render_params_dl(params)]
    if raw.get('ingredients'):
        htag = _heading_tag(ctx.heading_depth)
        parts.append(f'<{htag}>原料</{htag}>' + render_blocks(raw['ingredients'], ctx=ctx))
    if raw.get('instructions'):
        htag = _heading_tag(ctx.heading_depth)
        parts.append(f'<{htag}>步骤</{htag}>' + render_blocks(raw['instructions'], ctx=ctx))
    if raw.get('entries'):
        parts.append(render_blocks(raw['entries'], ctx=ctx))
    if raw.get('noteCook'):
        parts.append('<aside class="o5e-inset">' + render_blocks(raw['noteCook'], ctx=ctx) + '</aside>')
    parts.append(_render_fluff_section(entry.get('fluff') or raw.get('fluff'), ctx))
    return ''.join(parts)


def _render_table_or_loot_card(entry: Mapping[str, Any], raw: Mapping[str, Any], ctx: RenderContext) -> str:
    parts: list[str] = []
    if raw.get('colLabels') or raw.get('rows'):
        parts.append(_render_table(raw, ctx))
    if isinstance(raw.get('table'), list) and raw['table']:
        rows_html = []
        for row in raw['table']:
            if isinstance(row, Mapping):
                lo, hi = row.get('min'), row.get('max')
                rng = '' if lo is None else (str(lo) if lo == hi or hi is None else f'{lo}–{hi}')
                if 'result' in row:
                    val = render_inline(row['result'], ctx)
                elif 'item' in row:
                    val = render_inline(row['item'], ctx)
                elif 'coins' in row and isinstance(row['coins'], Mapping):
                    val = '，'.join(f'{v} {k}' for k, v in row['coins'].items())
                    val = escape_html(val)
                else:
                    val = escape_html(str(row))
                rows_html.append(f'<tr><td class="short">{escape_html(rng)}</td><td>{val}</td></tr>')
            else:
                rows_html.append(f'<tr><td class="short"></td><td>{render_inline(row, ctx)}</td></tr>')
        parts.append('<table class="o5e-table"><thead><tr><th scope="col">骰值</th><th scope="col">结果</th></tr></thead><tbody>' + ''.join(rows_html) + '</tbody></table>')
    if isinstance(raw.get('tables'), list) and raw['tables']:
        htag = _heading_tag(ctx.heading_depth)
        for sub in raw['tables']:
            if not isinstance(sub, Mapping):
                continue
            caption = sub.get('caption') or sub.get('option') or sub.get('diceExpression') or ''
            if caption:
                parts.append(f'<{htag}>{escape_html(caption)}</{htag}>')
            parts.append(_render_table_or_loot_card({}, sub, ctx))
    if raw.get('entries'):
        parts.append(render_blocks(raw['entries'], ctx=ctx))
    return ''.join(parts)


def _render_general_card(entry: Mapping[str, Any], raw: Mapping[str, Any], ctx: RenderContext) -> str:
    parts: list[str] = []
    params: list[tuple[str, str, bool]] = []
    if raw.get('pantheon'):
        params.append(('神系', escape_html(raw['pantheon']), False))
    if raw.get('alignment'):
        params.append(('阵营', escape_html(_format_alignment(raw['alignment'])), False))
    if isinstance(raw.get('domains'), list) and raw['domains']:
        params.append(('领域', escape_html('、'.join(str(d) for d in raw['domains'])), False))
    if raw.get('symbol'):
        params.append(('圣徽', render_inline(raw['symbol'], ctx), True))
    if raw.get('order'):
        params.append(('灵能学派', escape_html(raw['order']), False))
    if raw.get('focus'):
        params.append(('灵能专注', render_inline(raw['focus'], ctx), True))
    if raw.get('trapHazType'):
        params.append(('类别', escape_html(raw['trapHazType']), False))
    if raw.get('script'):
        params.append(('文字', escape_html(raw['script']), False))
    if isinstance(raw.get('typicalSpeakers'), list) and raw['typicalSpeakers']:
        params.append(('常见使用者', '、'.join(render_inline(s, ctx) for s in raw['typicalSpeakers']), True))
    if raw.get('published'):
        params.append(('出版日期', escape_html(raw['published']), False))
    if raw.get('author'):
        params.append(('作者', escape_html(raw['author']), False))
    parts.append(_render_params_dl(params))

    if raw.get('entries'):
        parts.append(render_blocks(raw['entries'], ctx=ctx))

    # Psionic modes.
    if isinstance(raw.get('modes'), list) and raw['modes']:
        htag = _heading_tag(ctx.heading_depth)
        for mode in raw['modes']:
            if not isinstance(mode, Mapping):
                continue
            mname = str(mode.get('name') or '')
            cost = mode.get('cost')
            cost_str = ''
            if isinstance(cost, Mapping):
                lo, hi = cost.get('min'), cost.get('max')
                cost_str = f'（{lo} 灵能点）' if lo == hi else f'（{lo}–{hi} 灵能点）'
            parts.append(f'<{htag}>{escape_html(mname + cost_str)}</{htag}>')
            if mode.get('entries'):
                parts.append(render_blocks(mode['entries'], ctx=ctx, heading_depth=ctx.heading_depth + 1))

    # Book / adventure table of contents summary.
    if isinstance(raw.get('contents'), list) and raw['contents']:
        htag = _heading_tag(ctx.heading_depth)
        toc_items = [
            f'<li>{escape_html(ch.get("name") or "")}</li>'
            for ch in raw['contents'] if isinstance(ch, Mapping) and ch.get('name')
        ]
        if toc_items:
            parts.append(f'<section class="o5e-book-toc"><{htag}>目录</{htag}><ol>{"".join(toc_items)}</ol></section>')

    parts.append(_render_fluff_section(entry.get('fluff') or raw.get('fluff'), ctx))
    return ''.join(parts)


def _entry_payload(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    """Extract the underlying 5etools JSON record from an entry wrapper (``raw`` or ``data``)."""
    raw = entry.get('raw')
    if isinstance(raw, Mapping):
        return raw
    data = entry.get('data')
    if isinstance(data, Mapping):
        return data
    return entry


def _render_specialized_body(
    entry: Mapping[str, Any],
    ctx: RenderContext,
    relations: Sequence[Mapping[str, Any]] = (),
) -> str:
    raw = _entry_payload(entry)
    entry_type = str(entry.get('type') or '')
    raw_kind = str(entry.get('raw_kind') or '')

    if entry_type == 'spell' or raw_kind == 'spell' or ('level' in raw and 'school' in raw and 'time' in raw):
        return _render_spell_card(entry, raw, ctx)
    if entry_type == 'creature' or raw_kind == 'monster' or ('ac' in raw and 'hp' in raw and 'str' in raw and 'vehicleType' not in raw):
        return _render_creature_card(entry, raw, ctx)
    if entry_type == 'class' or raw_kind == 'class' or ('classFeatures' in raw and 'hd' in raw):
        return _render_class_card(entry, raw, ctx, relations)
    if entry_type == 'subclass' or raw_kind == 'subclass' or 'subclassFeatures' in raw:
        return _render_subclass_card(entry, raw, ctx)
    if entry_type in ('magic_item', 'equipment', 'poison') or raw_kind in ('item', 'baseitem', 'itemGroup', 'magicvariant'):
        return _render_item_card(entry, raw, ctx)
    if entry_type == 'vehicle' or raw_kind in ('vehicle', 'vehicleUpgrade'):
        return _render_vehicle_card(entry, raw, ctx)
    if entry_type == 'facility' or raw_kind == 'facility':
        return _render_facility_card(entry, raw, ctx)
    if entry_type == 'deck' or raw_kind in ('deck', 'card'):
        return _render_deck_or_card(entry, raw, ctx, raw_kind or ('card' if 'set' in raw else 'deck'))
    if entry_type == 'recipe' or raw_kind in ('recipe', 'crochetPattern'):
        return _render_recipe_card(entry, raw, ctx)
    if entry_type == 'table' or raw_kind in (
        'table', 'individual', 'hoard', 'dragon', 'gems', 'artObjects',
        'magicItems', 'encounter', 'name', 'lifeClass', 'lifeBackground',
        'lifeTrinket', 'monsterfeatures',
    ):
        return _render_table_or_loot_card(entry, raw, ctx)
    return _render_general_card(entry, raw, ctx)


def _render_copy_warning_banner(entry: Mapping[str, Any]) -> str:
    status = str(entry.get('copy_status') or 'ok')
    if status == 'ok':
        return ''
    reason = COPY_STATUS_ZH.get(status, f'继承解析异常（{status}）')
    warnings = entry.get('copy_warnings')
    details = ''
    if isinstance(warnings, list) and warnings:
        items = ''.join(f'<li>{escape_html(w)}</li>' for w in warnings)
        details = f'<ul>{items}</ul>'
    return (
        f'<div class="o5e-copy-warning" role="alert" data-copy-status="{escape_html(status)}">'
        f'<strong>数据继承提示：</strong>{escape_html(reason)}{details}'
        f'</div>'
    )


def render_entry_detail(
    entry: Mapping[str, Any],
    resolver: LinkResolverProtocol | None = None,
    media_checker: Callable[[str], bool] | None = None,
    relations: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Render one our5etools entry into HTML, plain body text, and media/link diagnostics."""
    raw = _entry_payload(entry)
    default_source = str(entry.get('source_code') or raw.get('source') or '')
    ctx = RenderContext(
        resolver=resolver or LinkResolver(default_source=default_source),
        media_checker=media_checker or (lambda _rel: False),
        default_source=default_source,
        heading_depth=3,
        template_vars=raw,
    )

    banner = _render_copy_warning_banner(entry)
    body_html = _render_specialized_body(entry, ctx, relations=relations)
    full_html = f'<div class="o5e-detail">{banner}{body_html}</div>' if (banner or body_html) else '<div class="o5e-detail"></div>'
    return {
        'html': full_html,
        'body_text': strip_html_to_text(full_html),
        'media_summary': ctx.media_summary(),
        'remote_media_map': {k: list(v) for k, v in ctx.remote_media_map.items()},
        'link_events': ctx.link_events,
        'unknown_tags': ctx.unknown_tags,
        'unsupported_structures': ctx.unsupported_structures,
    }
