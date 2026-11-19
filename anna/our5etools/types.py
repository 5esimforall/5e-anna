"""Type classification, quick-reference schemas, and structured field extraction for our5etools."""
from __future__ import annotations

from fractions import Fraction
from functools import lru_cache
import json
import math
import re
import unicodedata
from typing import Any

UNKNOWN = '__unknown__'
CONFLICT = '__conflict__'
NONE = '__none__'
NA = '__na__'
MATCH = 'match'
GENERIC = 'generic'
DETAIL_SUFFIX = '_detail'

TYPES: dict[str, str] = {
    'class': '职业',
    'subclass': '子职',
    'class_feature': '职业能力',
    'species': '种族物种',
    'background': '背景',
    'feat': '专长',
    'spell': '法术',
    'psionic': '灵能',
    'equipment': '普通装备',
    'magic_item': '魔法物品',
    'poison': '毒药',
    'creature': '生物数据卡',
    'vehicle': '载具',
    'facility': '设施',
    'object': '物件',
    'deck': '牌组',
    'recipe': '食谱',
    'reward': '奖励',
    'deity': '神祇',
    'trap': '陷阱',
    'hazard': '环境危害',
    'disease': '疾病',
    'table': '随机表',
    'condition': '状态',
    'action': '动作',
    'skill': '技能',
    'language': '语言',
    'rule': '规则',
    'book': '书籍',
}

TYPE_ORDER: tuple[str, ...] = (
    'class',
    'subclass',
    'class_feature',
    'species',
    'background',
    'feat',
    'spell',
    'psionic',
    'equipment',
    'magic_item',
    'poison',
    'creature',
    'vehicle',
    'facility',
    'object',
    'deck',
    'recipe',
    'reward',
    'deity',
    'trap',
    'hazard',
    'disease',
    'table',
    'condition',
    'action',
    'skill',
    'language',
    'rule',
    'book',
)

TYPE_RANK: str = (
    'CASE e.type '
    + ' '.join(f"WHEN '{t}' THEN {i}" for i, t in enumerate(TYPE_ORDER))
    + f' ELSE {len(TYPE_ORDER)} END'
)

# Default kind mapping for every 5etools raw entity key.
# Note: 'item' and 'itemGroup' are further refined in classify_record() into
# 'poison', 'equipment', or 'magic_item' based on item fields.
RAW_TO_ENTRY_TYPE: dict[str, str] = {
    'class': 'class',
    'subclass': 'subclass',
    'classFeature': 'class_feature',
    'subclassFeature': 'class_feature',
    'optionalfeature': 'class_feature',
    'race': 'species',
    'subrace': 'species',
    'background': 'background',
    'feat': 'feat',
    'spell': 'spell',
    'psionic': 'psionic',
    'baseitem': 'equipment',
    'item': 'magic_item',
    'itemGroup': 'magic_item',
    'magicvariant': 'magic_item',
    'poison': 'poison',
    'monster': 'creature',
    'vehicle': 'vehicle',
    'vehicleUpgrade': 'vehicle',
    'facility': 'facility',
    'object': 'object',
    'deck': 'deck',
    'card': 'deck',
    'recipe': 'recipe',
    'crochetPattern': 'recipe',
    'reward': 'reward',
    'charoption': 'reward',
    'cult': 'reward',
    'boon': 'reward',
    'deity': 'deity',
    'trap': 'trap',
    'hazard': 'hazard',
    'disease': 'disease',
    'table': 'table',
    'individual': 'table',
    'hoard': 'table',
    'dragon': 'table',
    'gems': 'table',
    'artObjects': 'table',
    'magicItems': 'table',
    'dragonMundaneItems': 'table',
    'encounter': 'table',
    'name': 'table',
    'lifeClass': 'table',
    'lifeBackground': 'table',
    'lifeTrinket': 'table',
    'monsterfeatures': 'table',
    'condition': 'condition',
    'status': 'condition',
    'action': 'action',
    'skill': 'skill',
    'language': 'language',
    'languageScript': 'language',
    'variantrule': 'rule',
    'sense': 'rule',
    'itemProperty': 'rule',
    'itemMastery': 'rule',
    'itemType': 'rule',
    'itemTypeAdditionalEntries': 'rule',
    'itemEntry': 'rule',
    'legendaryGroup': 'rule',
    'citation': 'rule',
    'book': 'book',
    'adventure': 'book',
}

_MUNDANE_RARITIES = {'none', 'unknown', '未知', '无'}
_MAGIC_TYPE_CODES = {'RG', 'RD', 'WD', 'SC', 'P'}
_MAGIC_INDICATORS = (
    'wondrous',
    'reqAttune',
    'tier',
    'tattoo',
    'staff',
    'wand',
    'rod',
    'bonusWeapon',
    'bonusWeaponAttack',
    'bonusWeaponDamage',
    'bonusAc',
    'bonusSpellAttack',
    'bonusSpellSaveDc',
    'bonusSavingThrow',
    'bonusAbilityCheck',
    'attachedSpells',
    'sentient',
    'curse',
)


def _strip_source_suffix(value: str) -> str:
    return value.split('|', 1)[0].strip()


def _is_poison_item(item: dict[str, Any]) -> bool:
    if item.get('poison') is True:
        return True
    pts = item.get('poisonTypes')
    return isinstance(pts, list) and len(pts) > 0


def classify_record(raw_kind: str, item: dict[str, Any] | None = None) -> str:
    """Map a 5etools raw entity kind + record dict into its Anna entry type."""
    rec = item if isinstance(item, dict) else {}
    if raw_kind in {'item', 'itemGroup', 'baseitem'}:
        if _is_poison_item(rec):
            return 'poison'
        if raw_kind == 'baseitem':
            return 'equipment'
        inherits = rec.get('inherits') if isinstance(rec.get('inherits'), dict) else {}
        rarity = rec.get('rarity')
        if rarity is None:
            rarity = inherits.get('rarity')
        if isinstance(rarity, str) and rarity.strip():
            r_norm = rarity.strip().casefold()
            if r_norm in _MUNDANE_RARITIES:
                return 'equipment'
            return 'magic_item'
        cat = rec.get('_category')
        if isinstance(cat, str) and cat.strip().casefold() in {'basic', '基础'}:
            return 'equipment'
        if any(rec.get(k) or inherits.get(k) for k in _MAGIC_INDICATORS):
            return 'magic_item'
        raw_type = rec.get('type')
        if isinstance(raw_type, str) and _strip_source_suffix(raw_type).upper() in _MAGIC_TYPE_CODES:
            return 'magic_item'
        return 'equipment'
    if raw_kind == 'magicvariant':
        return 'magic_item'
    mapped = RAW_TO_ENTRY_TYPE.get(raw_kind)
    if mapped is not None:
        return mapped
    if raw_kind in TYPES:
        return raw_kind
    return 'rule'


LABELS: dict[str, str] = {
    # Class / subclass / feature
    'hit_die': '生命骰',
    'spellcasting_ability': '施法属性',
    'caster_progression': '施法进程',
    'owner_class': '所属职业',
    'feature_kind': '能力类别',
    'level': '等级',
    # Species / background / feat
    'size': '体型',
    'darkvision': '黑暗视觉',
    'granted_feat': '附赠专长',
    'skills': '技能熟练',
    'feat_category': '专长类别',
    # Spell
    'spell_level': '环阶',
    'school': '学派',
    'spell_classes': '职业',
    'casting_time': '施法时间',
    'concentration': '专注',
    'ritual': '仪式',
    'verbal': '言语',
    'somatic': '姿势',
    'material': '材料',
    'special_material': '特殊材料',
    # Psionic
    'psionic_order': '灵能教派',
    'psionic_type': '灵能类别',
    # Equipment / magic item / poison
    'rarity': '稀有度',
    'item_type': '物品类别',
    'mastery': '精通',
    'attunement': '同调',
    'poison_type': '毒素途径',
    # Creature
    'creature_type': '生物类型',
    'cr': 'CR',
    'legendary_actions': '传奇动作',
    'lair_actions': '巢穴动作',
    'has_local_audio': '本地音频',
    # Vehicle / facility / object
    'vehicle_type': '载具类别',
    'facility_kind': '设施类别',
    'object_type': '物件类别',
    # Deck / recipe / reward / deity
    'deck_set': '所属牌组',
    'dish_types': '菜品类别',
    'diet': '饮食类别',
    'reward_kind': '奖励类别',
    'pantheon': '神系',
    'alignment': '阵营',
    'domains': '领域',
    # Trap / hazard / table
    'trap_kind': '陷阱类别',
    'hazard_kind': '危害类别',
    'table_kind': '表格类别',
    # Condition / action / skill / language / rule / book
    'condition_kind': '状态类别',
    'action_time': '动作时间',
    'ability': '属性',
    'language_type': '语言类别',
    'script': '文字',
    'rule_kind': '规则类别',
    'book_kind': '书籍类别',
    'book_group': '书籍分组',
    # Package / provenance facets
    'package_kind': '内容来源',
    'tier': '内容评级',
}

COLUMN_LABELS: dict[str, str] = {
    **LABELS,
    'components': '成分',
    'spell_markers': '仪式/专注',
    'attunement_requirements': '同调条件',
    'item_subtypes': '子类别',
    'item_tier': '物品阶层',
    'prerequisite': '前置条件',
    'ac': 'AC',
    'hp': 'HP',
    'price': '价格',
    'weight': '重量',
    'damage': '伤害',
    'speed': '速度',
    'crew': '船员',
    'passengers': '乘客',
    'card_count': '牌数',
    'owner_subclass': '所属子职',
    'parent_species': '所属种族',
    'saving_throws': '豁免熟练',
    'published': '出版日期',
    'material_detail': '材料详情',
    'casting_time_detail': '施法时间详情',
    'creature_tags': '生物标签',
}

BOOL_FIELDS: set[str] = {
    'concentration',
    'ritual',
    'verbal',
    'somatic',
    'material',
    'special_material',
    'attunement',
    'legendary_actions',
    'lair_actions',
    'has_local_audio',
}

LEVELS: list[str] = ['戏法'] + [x + '环' for x in '一二三四五六七八九']

CLASSES: dict[str, str] = {
    '诗': '吟游诗人',
    '吟': '吟游诗人',
    '牧': '牧师',
    '德': '德鲁伊',
    '帕': '圣武士',
    '圣': '圣武士',
    '软': '游侠',
    '游': '游侠',
    '术': '术士',
    '法': '法师',
    '锁': '魔契师',
    '契': '魔契师',
    '邪': '魔契师',
    '邪术师': '魔契师',
    '械': '奇械师',
    '魔': '魔战士',
    '无': '无职',
}

CLASS_NAME_TO_CN: dict[str, str] = {
    'artificer': '奇械师',
    'barbarian': '野蛮人',
    'bard': '吟游诗人',
    'cleric': '牧师',
    'druid': '德鲁伊',
    'fighter': '战士',
    'monk': '武僧',
    'mystic': '秘术师',
    'paladin': '圣武士',
    'ranger': '游侠',
    'rogue': '游荡者',
    'sorcerer': '术士',
    'warlock': '魔契师',
    'wizard': '法师',
    'blood hunter': '血猎',
    'sidekick': '协力者',
    '邪术师': '魔契师',
}

ABILITY_TO_CN: dict[str, str] = {
    'str': '力量',
    'dex': '敏捷',
    'con': '体质',
    'int': '智力',
    'wis': '感知',
    'cha': '魅力',
    'strength': '力量',
    'dexterity': '敏捷',
    'constitution': '体质',
    'intelligence': '智力',
    'wisdom': '感知',
    'charisma': '魅力',
}

SKILL_TO_CN: dict[str, str] = {
    'athletics': '运动',
    'acrobatics': '特技',
    'sleight of hand': '巧手',
    'stealth': '隐匿',
    'arcana': '奥秘',
    'history': '历史',
    'investigation': '调查',
    'nature': '自然',
    'religion': '宗教',
    'animal handling': '驯兽',
    'insight': '洞悉',
    'medicine': '医药',
    'perception': '察觉',
    'survival': '生存',
    'deception': '欺瞒',
    'intimidation': '威吓',
    'performance': '表演',
    'persuasion': '游说',
}

SPELL_SCHOOL_TO_CN: dict[str, str] = {
    'A': '防护',
    'C': '咒法',
    'D': '预言',
    'E': '惑控',
    'V': '塑能',
    'I': '幻术',
    'N': '死灵',
    'T': '变化',
    'P': '灵能',
    'abjuration': '防护',
    'conjuration': '咒法',
    'divination': '预言',
    'enchantment': '惑控',
    'evocation': '塑能',
    'illusion': '幻术',
    'necromancy': '死灵',
    'transmutation': '变化',
    'psionic': '灵能',
}

SIZE_TO_CN: dict[str, str] = {
    'F': '超微型',
    'D': '微小型',
    'T': '微型',
    'S': '小型',
    'M': '中型',
    'L': '大型',
    'H': '巨型',
    'G': '超巨型',
    'C': '伟岸',
    'V': '不定',
    'fine': '超微型',
    'diminutive': '微小型',
    'tiny': '微型',
    'small': '小型',
    'medium': '中型',
    'large': '大型',
    'huge': '巨型',
    'gargantuan': '超巨型',
    'colossal': '伟岸',
    'varies': '不定',
    '超型': '超巨型',
}

CREATURE_TYPE_TO_CN: dict[str, str] = {
    'aberration': '异怪',
    'beast': '野兽',
    'celestial': '天族',
    'construct': '构装',
    'dragon': '龙类',
    'elemental': '元素',
    'fey': '妖精',
    'fiend': '邪魔',
    'giant': '巨人',
    'humanoid': '类人',
    'monstrosity': '怪兽',
    'ooze': '泥怪',
    'plant': '植物',
    'undead': '亡灵',
    '龙': '龙类',
}

RARITY_TO_CN: dict[str, str] = {
    'common': '普通',
    'uncommon': '非普通',
    'rare': '珍稀',
    'very rare': '极珍稀',
    'legendary': '传说',
    'artifact': '神器',
    'varies': '多种稀有度',
    'unknown (magic)': '未知（魔法）',
    'unknown': '未知',
    'none': '无',
}

# Maps 5etools item type abbreviation -> (primary item_type, optional subtype)
ITEM_TYPE_CODE_TO_CN: dict[str, tuple[str, str | None]] = {
    'M': ('武器', '近战武器'),
    'R': ('武器', '远程武器'),
    'LA': ('护甲', '轻甲'),
    'MA': ('护甲', '中甲'),
    'HA': ('护甲', '重甲'),
    'S': ('盾牌', None),
    'A': ('弹药', None),
    'AF': ('弹药', '火器弹药'),
    'P': ('药水', None),
    'SC': ('卷轴', None),
    'RG': ('戒指', None),
    'RD': ('权杖', None),
    'WD': ('魔杖', None),
    'SCF': ('法器', None),
    'G': ('冒险用品', None),
    'T': ('工具', None),
    'AT': ('工匠工具', None),
    'GS': ('赌具', None),
    'INS': ('乐器', None),
    'FD': ('食物和饮品', None),
    'MNT': ('乘骑', None),
    'TAH': ('鞍具和挽具', None),
    'VEH': ('载具', '陆运'),
    'SHP': ('载具', '水上'),
    'AIR': ('载具', '飞行'),
    'SPC': ('载具', '太空'),
    'TG': ('贸易货物', None),
    'TB': ('贸易货物', '贸易金属锭'),
    '$': ('宝藏', None),
    '$A': ('宝藏', '艺术品'),
    '$C': ('宝藏', '货币'),
    '$G': ('宝藏', '宝石'),
    'EXP': ('爆炸物', None),
    'OTH': ('其他', None),
    'GV': ('通用变体', None),
}

DMG_TYPE_TO_CN: dict[str, str] = {
    'A': '强酸',
    'B': '钝击',
    'C': '寒冷',
    'F': '火焰',
    'O': '力场',
    'L': '闪电',
    'N': '黯蚀',
    'P': '穿刺',
    'I': '毒素',
    'Y': '心灵',
    'R': '光耀',
    'S': '挥砍',
    'T': '雷鸣',
}

POISON_TYPE_TO_CN: dict[str, str] = {
    'ingested': '服用',
    'injury': '伤害',
    'inhaled': '吸入',
    'contact': '接触',
    '服用': '服用',
    '伤害': '伤害',
    '吸入': '吸入',
    '接触': '接触',
}

FEAT_CATEGORY_TO_CN: dict[str, str] = {
    'O': '起源',
    'G': '通用',
    'FS': '战斗风格',
    'FS:P': '战斗风格（圣武士）',
    'FS:R': '战斗风格（游侠）',
    'EB': '传奇恩惠',
    'D': '龙纹',
    'DG': '黑暗赠礼',
}

OPT_FEATURE_TYPE_TO_CN: dict[str, str] = {
    'AI': '奇械师注法',
    'ED': '法门',
    'EI': '魔能祈唤',
    'MM': '超魔法',
    'MV': '战技',
    'MV:B': '战技',
    'MV:C2-UA': '战技（骑兵UA）',
    'AS': '奥术射击',
    'AS:V1-UA': '奥术射击（V1 UA）',
    'AS:V2-UA': '奥术射击（V2 UA）',
    'FS:F': '战斗风格（战士）',
    'FS:B': '战斗风格（吟游诗人）',
    'FS:P': '战斗风格（圣武士）',
    'FS:R': '战斗风格（游侠）',
    'PB': '魔契恩泽',
    'OR': '真名共鸣',
    'RN': '符文骑士符文',
    'AF': '炼金配方',
    'TT': '旅者技艺',
    'RP': '声望特权',
    'OTH': '其他',
}

OPT_FEATURE_OWNER_CLASS: dict[str, str] = {
    'AI': '奇械师',
    'ED': '武僧',
    'EI': '魔契师',
    'MM': '术士',
    'MV:B': '战士',
    'MV:C2-UA': '战士',
    'AS': '战士',
    'AS:V1-UA': '战士',
    'AS:V2-UA': '战士',
    'FS:F': '战士',
    'FS:B': '吟游诗人',
    'FS:P': '圣武士',
    'FS:R': '游侠',
    'PB': '魔契师',
    'RN': '战士',
    'AF': '奇械师',
}

PSIONIC_TYPE_TO_CN: dict[str, str] = {
    'T': '天赋',
    'D': '训练',
}

VEHICLE_TYPE_TO_CN: dict[str, str] = {
    'SHIP': '船只',
    'SPELLJAMMER': '魔法船',
    'ELEMENTAL_AIRSHIP': '元素飞艇',
    'INFWAR': '地狱战争机器',
    'CREATURE': '生物载具',
    'OBJECT': '物件载具',
}

VEHICLE_UPGRADE_TYPE_TO_CN: dict[str, str] = {
    'SHP:H': '船只升级（船壳）',
    'SHP:M': '船只升级（操纵）',
    'SHP:W': '船只升级（武器）',
    'SHP:F': '船只升级（船首像）',
    'SHP:O': '船只升级（杂项）',
    'IWM:W': '地狱战争机器（武器）',
    'IWM:A': '地狱战争机器（护甲）',
    'IWM:G': '地狱战争机器（装置）',
}

FACILITY_KIND_TO_CN: dict[str, str] = {
    'basic': '基础设施',
    'special': '特殊设施',
}

OBJECT_TYPE_TO_CN: dict[str, str] = {
    'SW': '攻城武器',
    'U': '通用物件',
    'GEN': '通用物件',
}

DIET_TO_CN: dict[str, str] = {
    'V': '纯素',
    'C': '素食',
    'X': '荤素皆有',
}

REWARD_TYPE_TO_CN: dict[str, str] = {
    'Blessing': '祝福',
    'Boon': '恩赐',
    'Charm': '护咒',
    'Curse': '诅咒',
    'Draconic Gift': '龙族赠礼',
    'Fragment of Suffering': '痛苦碎片',
    'Inhabitation': '附体',
    'Piety Trait': '虔信特质',
    'Other': '其他',
}

CHAR_OPTION_TYPE_TO_CN: dict[str, str] = {
    'SG': '超自然赠礼',
    'OF': '可选特性',
    'DG': '黑暗赠礼',
    'RF:B': '替换特性（背景）',
    'CS': '角色秘密',
    'PTH': '道途',
}

CULT_TYPE_TO_CN: dict[str, str] = {
    'Diabolical': '魔鬼邪教',
    'Demonic': '恶魔邪教',
    'Elder Evil': '上古邪物',
    'Elemental': '元素邪教',
}

TRAP_HAZARD_TYPE_TO_CN: dict[str, str] = {
    'MECH': '机械陷阱',
    'MAG': '魔法陷阱',
    'SMPL': '简易陷阱',
    'CMPX': '复杂陷阱',
    'TRP': '陷阱',
    'HAUNT': '灵异陷阱',
    'HAZ': '危害',
    'WTH': '天气',
    'ENV': '环境危害',
    'WLD': '野外危害',
    'GEN': '通用',
    'EST': '奥法风暴',
}

LANGUAGE_TYPE_TO_CN: dict[str, str] = {
    'standard': '标准',
    'exotic': '特种',
    'rare': '稀有',
    'secret': '秘密',
}

CASTER_PROGRESSION_TO_CN: dict[str, str] = {
    'full': '全施法者',
    '1/2': '半施法者',
    'artificer': '半施法者（奇械师）',
    '1/3': '三分之一施法者',
    'pact': '契约魔法',
}

TABLE_KIND_TO_CN: dict[str, str] = {
    'table': '随机表',
    'individual': '个体宝藏',
    'hoard': '库藏宝藏',
    'dragon': '巨龙宝藏',
    'gems': '宝石表',
    'artObjects': '艺术品表',
    'magicItems': '魔法物品表',
    'dragonMundaneItems': '龙巢寻常物',
    'encounter': '随机遭遇',
    'name': '姓名表',
    'lifeClass': '生平（职业）',
    'lifeBackground': '生平（背景）',
    'lifeTrinket': '生平（饰品）',
    'monsterfeatures': '怪物特性表',
}

RULE_KIND_TO_CN: dict[str, str] = {
    'variantrule': '变体规则',
    'sense': '感官',
    'itemProperty': '物品属性',
    'itemMastery': '武器精通',
    'itemType': '物品类型',
    'itemTypeAdditionalEntries': '物品类型说明',
    'itemEntry': '物品说明',
    'legendaryGroup': '传奇组',
    'citation': '引用',
}

BOOK_GROUP_TO_CN: dict[str, str] = {
    'core': '核心规则书',
    'supplement': '扩展规则',
    'supplement-alt': '扩展规则（别版）',
    'setting': '设定集',
    'setting-alt': '设定集（别版）',
    'screen': 'DM帷幕',
    'recipe': '食谱',
    'homecraft': '手作',
    'organized-play': '组织赛事',
    'other': '其他',
    'homebrew': '社区扩展',
}

ALIGNMENT_CODE_TO_CN: dict[str, str] = {
    'L': '守序',
    'N': '中立',
    'C': '混乱',
    'G': '善良',
    'E': '邪恶',
    'NX': '中立（守序/混乱轴）',
    'NY': '中立（善良/邪恶轴）',
    'U': '无阵营',
    'A': '任意阵营',
}

ORDERS: dict[str, list[str]] = {
    'spell_level': list(map(str, range(10))),
    'level': list(map(str, range(1, 21))),
    'school': ['防护', '咒法', '预言', '惑控', '塑能', '幻术', '死灵', '变化', '灵能'],
    'spell_classes': ['吟游诗人', '牧师', '德鲁伊', '圣武士', '游侠', '术士', '法师', '魔契师', '奇械师', '魔战士', '无职'],
    'owner_class': ['野蛮人', '吟游诗人', '牧师', '德鲁伊', '战士', '武僧', '圣武士', '游侠', '游荡者', '术士', '魔契师', '法师', '奇械师', '秘术师'],
    'casting_time': ['动作', '附赠动作', '反应', '其他'],
    'action_time': ['动作', '附赠动作', '反应', '自由', '多类', '其他'],
    'rarity': ['普通', '非普通', '珍稀', '极珍稀', '传说', '神器', '多种稀有度', '未知（魔法）', '未知', '无'],
    'size': ['超微型', '微小型', '微型', '小型', '中型', '大型', '巨型', '超巨型', '伟岸', '不定'],
    'creature_type': ['异怪', '野兽', '天族', '构装', '龙类', '元素', '妖精', '邪魔', '巨人', '类人', '怪兽', '泥怪', '植物', '亡灵'],
    'feat_category': ['起源', '通用', '战斗风格', '战斗风格（圣武士）', '战斗风格（游侠）', '传奇恩惠', '龙纹', '黑暗赠礼'],
    'ability': ['力量', '敏捷', '体质', '智力', '感知', '魅力'],
    'spellcasting_ability': ['智力', '感知', '魅力'],
    'hit_die': ['d6', 'd8', 'd10', 'd12'],
    'caster_progression': ['全施法者', '半施法者', '半施法者（奇械师）', '三分之一施法者', '契约魔法'],
    'poison_type': ['服用', '伤害', '吸入', '接触'],
    'psionic_type': ['天赋', '训练'],
    'facility_kind': ['基础设施', '特殊设施'],
    'diet': ['纯素', '素食', '荤素皆有'],
    'language_type': ['标准', '稀有', '特种', '秘密', '文字'],
    'alignment': ['守序善良', '中立善良', '混乱善良', '守序中立', '绝对中立', '混乱中立', '守序邪恶', '中立邪恶', '混乱邪恶', '无阵营', '任意阵营'],
    'book_kind': ['规则书', '模组', '漫画'],
    'package_kind': ['官方基础', '合作出版', '本站精选', '社区扩展'],
    'tier': ['A', 'B', 'C'],
}

COMPOSITES: dict[str, list[str]] = {
    'components': ['verbal', 'somatic', 'material'],
    'spell_markers': ['ritual', 'concentration'],
}

SCHEMAS: dict[str, dict[str, Any]] = {
    'class': {
        'facets': ['hit_die', 'spellcasting_ability', 'caster_progression'],
        'flags': [],
        'columns': ['name', 'hit_die', 'spellcasting_ability', 'caster_progression', 'saving_throws', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'hit_die', 'caster_progression'],
        'default_sort': 'name',
    },
    'subclass': {
        'facets': ['owner_class', 'spellcasting_ability'],
        'flags': [],
        'columns': ['name', 'owner_class', 'spellcasting_ability', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'owner_class'],
        'default_sort': 'owner_class',
    },
    'class_feature': {
        'facets': ['owner_class', 'feature_kind', 'level'],
        'flags': [],
        'columns': ['name', 'owner_class', 'feature_kind', 'level', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'owner_class', 'feature_kind', 'level'],
        'default_sort': 'level',
    },
    'species': {
        'facets': ['size', 'darkvision'],
        'flags': [],
        'columns': ['name', 'size', 'darkvision', 'speed', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'size', 'darkvision'],
        'default_sort': 'name',
    },
    'background': {
        'facets': ['granted_feat', 'skills'],
        'flags': [],
        'columns': ['name', 'granted_feat', 'skills', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'granted_feat'],
        'default_sort': 'name',
    },
    'feat': {
        'facets': ['feat_category'],
        'flags': [],
        'columns': ['name', 'feat_category', 'prerequisite', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'feat_category'],
        'default_sort': 'name',
    },
    'spell': {
        'facets': ['spell_level', 'school', 'spell_classes', 'casting_time'],
        'flags': ['concentration', 'ritual', 'verbal', 'somatic', 'material', 'special_material'],
        'columns': ['name', 'spell_level', 'school', 'spell_classes', 'casting_time', 'components', 'spell_markers', 'sources', 'ruleset'],
        'export_columns': ['name', 'spell_level', 'school', 'spell_classes', 'casting_time', 'verbal', 'somatic', 'material', 'special_material', 'ritual', 'concentration', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'spell_level', 'school', 'casting_time'],
        'default_sort': 'spell_level',
    },
    'psionic': {
        'facets': ['psionic_order', 'psionic_type'],
        'flags': [],
        'columns': ['name', 'psionic_order', 'psionic_type', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'psionic_order', 'psionic_type'],
        'default_sort': 'psionic_order',
    },
    'equipment': {
        'facets': ['item_type', 'mastery'],
        'flags': [],
        'columns': ['name', 'item_type', 'price', 'weight', 'damage', 'ac', 'mastery', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'item_type', 'mastery', 'ac'],
        'default_sort': 'name',
    },
    'magic_item': {
        'facets': ['rarity', 'item_type'],
        'flags': ['attunement'],
        'columns': ['name', 'rarity', 'item_type', 'item_subtypes', 'attunement', 'attunement_requirements', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'rarity', 'item_type'],
        'default_sort': 'rarity',
    },
    'poison': {
        'facets': ['poison_type'],
        'flags': [],
        'columns': ['name', 'poison_type', 'price', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'poison_type'],
        'default_sort': 'name',
    },
    'creature': {
        'facets': ['size', 'creature_type'],
        'flags': ['legendary_actions', 'lair_actions', 'has_local_audio'],
        'range': 'cr',
        'columns': ['name', 'size', 'creature_type', 'cr', 'ac', 'hp', 'legendary_actions', 'lair_actions', 'has_local_audio', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'cr', 'size', 'creature_type', 'ac', 'hp'],
        'default_sort': 'cr',
    },
    'vehicle': {
        'facets': ['vehicle_type', 'size'],
        'flags': [],
        'columns': ['name', 'vehicle_type', 'size', 'crew', 'passengers', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'vehicle_type', 'size'],
        'default_sort': 'name',
    },
    'facility': {
        'facets': ['facility_kind', 'level'],
        'flags': [],
        'columns': ['name', 'facility_kind', 'level', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'facility_kind', 'level'],
        'default_sort': 'level',
    },
    'object': {
        'facets': ['size', 'object_type'],
        'flags': [],
        'columns': ['name', 'size', 'object_type', 'ac', 'hp', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'size', 'ac', 'hp'],
        'default_sort': 'name',
    },
    'deck': {
        'facets': ['deck_set'],
        'flags': [],
        'columns': ['name', 'deck_set', 'card_count', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'deck_set'],
        'default_sort': 'deck_set',
    },
    'recipe': {
        'facets': ['dish_types', 'diet'],
        'flags': [],
        'columns': ['name', 'dish_types', 'diet', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'diet'],
        'default_sort': 'name',
    },
    'reward': {
        'facets': ['reward_kind'],
        'flags': [],
        'columns': ['name', 'reward_kind', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'reward_kind'],
        'default_sort': 'reward_kind',
    },
    'deity': {
        'facets': ['pantheon', 'alignment', 'domains'],
        'flags': [],
        'columns': ['name', 'pantheon', 'alignment', 'domains', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'pantheon', 'alignment'],
        'default_sort': 'pantheon',
    },
    'trap': {
        'facets': ['trap_kind'],
        'flags': [],
        'columns': ['name', 'trap_kind', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'trap_kind'],
        'default_sort': 'name',
    },
    'hazard': {
        'facets': ['hazard_kind'],
        'flags': [],
        'columns': ['name', 'hazard_kind', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'hazard_kind'],
        'default_sort': 'name',
    },
    'table': {
        'facets': ['table_kind'],
        'flags': [],
        'columns': ['name', 'table_kind', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'table_kind'],
        'default_sort': 'table_kind',
    },
    'condition': {
        'facets': ['condition_kind'],
        'flags': [],
        'columns': ['name', 'condition_kind', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'condition_kind'],
        'default_sort': 'name',
    },
    'action': {
        'facets': ['action_time'],
        'flags': [],
        'columns': ['name', 'action_time', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'action_time'],
        'default_sort': 'name',
    },
    'skill': {
        'facets': ['ability'],
        'flags': [],
        'columns': ['name', 'ability', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'ability'],
        'default_sort': 'ability',
    },
    'language': {
        'facets': ['language_type', 'script'],
        'flags': [],
        'columns': ['name', 'language_type', 'script', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'language_type', 'script'],
        'default_sort': 'language_type',
    },
    'rule': {
        'facets': ['rule_kind'],
        'flags': [],
        'columns': ['name', 'rule_kind', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'rule_kind'],
        'default_sort': 'name',
    },
    'book': {
        'facets': ['book_kind', 'book_group'],
        'flags': [],
        'columns': ['name', 'book_kind', 'book_group', 'published', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'book_kind', 'book_group'],
        'default_sort': 'book_kind',
    },
    'generic': {
        'facets': [],
        'flags': [],
        'columns': ['name', 'type', 'sources', 'ruleset'],
        'sorts': ['source', 'name', 'name_en', 'type', 'ruleset'],
        'default_sort': 'type',
    },
}

TYPE_KINDS: dict[str, str] = {
    'class': 'class',
    'subclass': 'subclass',
    'class_feature': 'class_feature',
    'species': 'species',
    'background': 'background',
    'feat': 'feat',
    'spell': 'spell',
    'psionic': 'psionic',
    'equipment': 'equipment',
    'magic_item': 'magic_item',
    'poison': 'poison',
    'creature': 'creature',
    'vehicle': 'vehicle',
    'facility': 'facility',
    'object': 'object',
    'deck': 'deck',
    'recipe': 'recipe',
    'reward': 'reward',
    'deity': 'deity',
    'trap': 'trap',
    'hazard': 'hazard',
    'disease': 'generic',
    'table': 'table',
    'condition': 'condition',
    'action': 'action',
    'skill': 'skill',
    'language': 'language',
    'rule': 'rule',
    'book': 'book',
}

PRESETS: tuple[dict[str, str], ...] = (
    {'name': '魔法武器', 'type': 'magic_item', 'field': 'item_type', 'value': '武器'},
    {'name': '魔法护甲', 'type': 'magic_item', 'field': 'item_type', 'value': '护甲'},
    {'name': '药水', 'type': 'magic_item', 'field': 'item_type', 'value': '药水'},
    {'name': '起源专长', 'type': 'feat', 'field': 'feat_category', 'value': '起源'},
    {'name': '战斗风格', 'type': 'feat', 'field': 'feat_category', 'value': '战斗风格'},
    {'name': '传奇恩惠', 'type': 'feat', 'field': 'feat_category', 'value': '传奇恩惠'},
    {'name': '魔能祈唤', 'type': 'class_feature', 'field': 'feature_kind', 'value': '魔能祈唤'},
    {'name': '超魔法', 'type': 'class_feature', 'field': 'feature_kind', 'value': '超魔法'},
    {'name': '战技', 'type': 'class_feature', 'field': 'feature_kind', 'value': '战技'},
)

METADATA_FIELDS: tuple[str, ...] = (
    'type',
    'source_id',
    'source_code',
    'ruleset',
    'legacy',
    'package_kind',
    'tier',
)

STACKABLE_TYPES: tuple[str, ...] = (
    'spell',
    'psionic',
    'magic_item',
    'creature',
    'feat',
    'equipment',
    'poison',
    'species',
    'background',
    'class',
    'subclass',
    'vehicle',
    'facility',
    'object',
    'deck',
    'recipe',
    'reward',
    'deity',
    'trap',
    'hazard',
    'disease',
    'condition',
    'action',
    'skill',
    'language',
)

RULESET_PREFERENCES: dict[str, tuple[str, str]] = {
    '5r': ('5r', '5e'),
    '5e': ('5e', '5r'),
}

IDENTITY_COLUMNS: set[str] = {
    'name',
    'sources',
    'ruleset',
    'type',
    'classification_status',
    'legacy',
    'package_kind',
    'tier',
}

COMPARE_EXTRA: dict[str, list[str]] = {
    'components': ['special_material'],
}


# ---------------------------------------------------------------------------
# Schema & query helpers (API-compatible with anna.catalog.views)
# ---------------------------------------------------------------------------

def finish(config: dict[str, Any]) -> dict[str, Any]:
    config.setdefault('export_columns', config['columns'])
    config['composites'] = {k: COMPOSITES[k] for k in config['columns'] if k in COMPOSITES}
    return config


def configure(kind: str) -> dict[str, Any]:
    return finish({'kinds': [kind], **SCHEMAS[kind]})


def combine(kinds: list[str]) -> dict[str, Any]:
    base, own = SCHEMAS[GENERIC], [SCHEMAS[k] for k in kinds]
    merge = lambda key: list(dict.fromkeys(x for s in [base, *own] for x in s.get(key, [])))
    lead = [c for c in base['columns'] if c not in IDENTITY_COLUMNS or c in {'name', 'type'}]
    tail = [c for c in base['columns'] if c not in lead]
    readings = lambda key: [c for s in own for c in s.get(key, s['columns']) if c not in IDENTITY_COLUMNS]
    return finish({
        'kinds': [GENERIC, *kinds],
        'facets': merge('facets'),
        'flags': merge('flags'),
        'range': next((s['range'] for s in own if s.get('range')), None),
        'columns': list(dict.fromkeys(lead + readings('columns') + tail)),
        'export_columns': list(dict.fromkeys(lead + readings('export_columns') + tail)),
        'sorts': merge('sorts'),
        'default_sort': base['default_sort'],
    })


def type_conditions(entry_type: str) -> list[str]:
    own = SCHEMAS[TYPE_KINDS.get(entry_type, GENERIC)]
    return [*own['facets'], *own['flags'], *([own['range']] if own.get('range') else [])]


def schema_for(types: Any) -> dict[str, Any]:
    ordered_types = sorted(
        set(types),
        key=lambda t: TYPE_ORDER.index(t) if t in TYPE_ORDER else len(TYPE_ORDER),
    )
    kinds = list(dict.fromkeys(TYPE_KINDS.get(t, GENERIC) for t in ordered_types))
    if len(kinds) <= 1:
        return configure(kinds[0] if kinds else GENERIC)
    return combine([k for k in kinds if k != GENERIC])


def field_order(entry: dict[str, Any]) -> list[str]:
    fields = entry['fields']
    config = configure(TYPE_KINDS.get(entry['type'], GENERIC))
    lead = [f for column in config['columns'] for f in [column, *config['composites'].get(column, [])]]
    rules = [f for f in fields if f not in METADATA_FIELDS]
    ordered = [f for f in dict.fromkeys(lead) if f in rules] + [f for f in rules if f not in lead]
    details = [f for f in ordered if f.endswith(DETAIL_SUFFIX) and f[:-len(DETAIL_SUFFIX)] in ordered]
    for f in details:
        ordered.remove(f)
        ordered.insert(ordered.index(f[:-len(DETAIL_SUFFIX)]) + 1, f)
    return ordered + [f for f in METADATA_FIELDS if f in fields]


def scalar_values(v: Any) -> list[Any]:
    return v if isinstance(v, list) else ([] if v is None else [v])


def number(v: Any) -> float | None:
    try:
        text = str(v).strip()
        if not re.fullmatch(r'\d+(?:/\d+|\.\d+)?', text):
            return None
        result = float(Fraction(text))
        return result if math.isfinite(result) else None
    except (ValueError, ZeroDivisionError):
        return None


_BOOL_TOKEN_MAP = {
    'true': 'true',
    'false': 'false',
    'True': 'true',
    'False': 'false',
    '是': 'true',
    '否': 'false',
    '有': 'true',
    '无': 'false',
    '√': 'true',
    '×': 'false',
}


def normalized(fields: dict[str, Any], field: str) -> list[str]:
    """Exact tokens, explicit missing/conflict states, no absence-as-false inference."""
    if field == 'special_material':
        raw = fields.get('special_material')
        from_material = False
        if raw is None and 'special_material' not in fields:
            mat = fields.get('material')
            if mat in {'M', 'M*', '×'} or (isinstance(mat, list) and any(x in {'M', 'M*', '×'} for x in mat)):
                raw = mat
                from_material = True
    else:
        raw = fields.get(field)
        from_material = False

    out: list[str] = []
    for value in scalar_values(raw):
        if value is None or value == '':
            continue
        if isinstance(value, bool):
            text = 'true' if value else 'false'
        else:
            text = str(value).strip()
        if not text:
            continue
        if field == 'spell_level':
            if text in LEVELS:
                out.append(str(LEVELS.index(text)))
            elif re.fullmatch(r'[0-9]环', text):
                out.append(text[0])
            elif text == '零环':
                out.append('0')
            else:
                out.append(text)
        elif field == 'spell_classes':
            for x in re.split(r'[\s、,，/；;]+', text):
                if x:
                    out.append(_normalize_class_name(x))
        elif field == 'casting_time':
            out.append({'附赠': '附赠动作', '1个动作': '动作', '一动作': '动作'}.get(text, text))
        elif field == 'size':
            out.append(_normalize_size(text))
        elif field == 'creature_type':
            for x in re.split(r'[/／]', text):
                if x:
                    out.append(_normalize_creature_type(x))
        elif field in BOOL_FIELDS:
            if field == 'material':
                result = {'M': 'true', 'M*': 'true', '×': 'false', **_BOOL_TOKEN_MAP}.get(text)
            elif field == 'special_material':
                if from_material:
                    result = {'M': 'false', 'M*': 'true', '×': NA}.get(text)
                else:
                    result = {'M': 'false', 'M*': 'true', '×': NA, NA: NA, **_BOOL_TOKEN_MAP}.get(text)
            else:
                result = _BOOL_TOKEN_MAP.get(text)
            out.append(result or text)
        else:
            out.append(text)

    out = list(dict.fromkeys(out))
    if field in BOOL_FIELDS and len(out) > 1:
        return [CONFLICT]
    return out or [UNKNOWN]


@lru_cache(maxsize=4096)
def parse_fields(raw: str) -> dict[str, Any]:
    return json.loads(raw)


def field_values(raw: str, field: str) -> str:
    return json.dumps(normalized(parse_fields(raw), field), ensure_ascii=False)


def sort_value(raw: str, field: str) -> Any:
    vals = normalized(parse_fields(raw), field)
    if len(vals) != 1 or vals[0].startswith('__'):
        return None
    v = vals[0]
    if field in {'cr', 'ac', 'hp', 'spell_level', 'level'}:
        return number(v)
    if field == 'darkvision':
        m = re.match(r'^(\d+)', v)
        return float(m.group(1)) if m else None
    if field in ORDERS:
        return ORDERS[field].index(v) if v in ORDERS[field] else None
    return v.casefold()


def name_key(text: str | None) -> str | None:
    """Name collation: digit runs compare by value (2级 before 10级); case and width ignored."""
    if not text:
        return None
    return re.sub(r'\d+', lambda m: m[0].zfill(8), unicodedata.normalize('NFKC', text).casefold())


def match_rank(zh: str | None, en: str | None, name: str | None, query: str | None) -> int:
    """0 exact name, 1 prefix match, 2 substring/fallback match."""
    words = lambda text: unicodedata.normalize('NFKC', text or '').casefold().strip()
    q = words(query)
    names = [words(n) for n in (zh, en) if n] or [words(name)]
    if not q:
        return 2
    return 0 if q in names else 1 if any(n.startswith(q) for n in names) else 2


def register(db: Any) -> None:
    db.create_function('browse_values', 2, field_values, deterministic=True)
    db.create_function('browse_rank', 2, sort_value, deterministic=True)
    db.create_function('browse_name', 1, name_key, deterministic=True)
    db.create_function('browse_match', 4, match_rank, deterministic=True)
    db.create_function('browse_query', 0, lambda: '')


def comparable(fields: dict[str, Any], field: str) -> tuple[str, ...] | None:
    """Normalized value for version comparison; unknown, conflicting or inapplicable values never differ."""
    values = normalized(fields, field)
    if any(v.startswith('__') for v in values):
        return None
    return tuple(sorted(unicodedata.normalize('NFKC', re.sub(r'\s+', '', v)).casefold() for v in values))


def values_differ(a: dict[str, Any], b: dict[str, Any], field: str) -> bool:
    x, y = comparable(a, field), comparable(b, field)
    return x is not None and y is not None and x != y


def column_fields(config: dict[str, Any], column: str) -> list[str]:
    return [*config['composites'].get(column, [column]), *COMPARE_EXTRA.get(column, [])]


def version_diffs(config: dict[str, Any], head: dict[str, Any], others: list[dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for column in config['columns']:
        if column in IDENTITY_COLUMNS:
            continue
        ids = [
            o['id']
            for o in others
            if any(values_differ(head['fields'], o['fields'], f) for f in column_fields(config, column))
        ]
        if ids:
            out[column] = ids
    return out


def option_label(field: str, value: str) -> str:
    if value == UNKNOWN:
        return '未提取'
    if value == CONFLICT:
        return '冲突'
    if value == NA:
        return '无材料'
    if field == 'spell_level' and value in ORDERS['spell_level']:
        return LEVELS[int(value)]
    if field in BOOL_FIELDS:
        return {'true': '是', 'false': '否'}.get(value, value)
    return value


def option_order(field: str, value: str) -> tuple[int, int, str]:
    ordered = ORDERS.get(field, ['true', 'false', '特殊'])
    if value in ordered:
        return (0, ordered.index(value), '')
    if value == UNKNOWN:
        return (3, 0, '')
    if value == CONFLICT:
        return (2, 0, '')
    if value == NA:
        return (1, 1, value)
    return (1, 0, value)


def enrich(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get('fields') or {}
    row['quickref'] = {f: normalized(fields, f) for f in LABELS}
    return row


# ---------------------------------------------------------------------------
# Normalization & formatting helpers for 5etools JSON records
# ---------------------------------------------------------------------------

def _normalize_class_name(raw: str) -> str:
    s = _strip_source_suffix(raw).strip()
    if not s:
        return s
    if s in CLASSES:
        return CLASSES[s]
    mapped = CLASS_NAME_TO_CN.get(s.casefold())
    return mapped or s


def _normalize_ability(raw: str) -> str:
    s = raw.strip()
    return ABILITY_TO_CN.get(s.casefold(), s)


def _normalize_skill(raw: str) -> str:
    s = raw.strip()
    return SKILL_TO_CN.get(s.casefold(), s)


def _normalize_size(raw: str) -> str:
    s = raw.strip()
    if s in SIZE_TO_CN:
        return SIZE_TO_CN[s]
    if s.upper() in SIZE_TO_CN:
        return SIZE_TO_CN[s.upper()]
    if s.casefold() in SIZE_TO_CN:
        return SIZE_TO_CN[s.casefold()]
    return s


def _normalize_creature_type(raw: str) -> str:
    s = raw.strip()
    if s in CREATURE_TYPE_TO_CN:
        return CREATURE_TYPE_TO_CN[s]
    mapped = CREATURE_TYPE_TO_CN.get(s.casefold())
    return mapped or s


def _normalize_rarity(raw: str) -> str:
    s = raw.strip()
    mapped = RARITY_TO_CN.get(s.casefold())
    return mapped or s


def _normalize_school(raw: str, ctx: dict[str, Any]) -> str:
    s = raw.strip()
    custom_map = ctx.get('spell_schools') or ctx.get('spellSchools') or {}
    if isinstance(custom_map, dict):
        hit = custom_map.get(s)
        if hit is None:
            for k, v in custom_map.items():
                if isinstance(k, str) and k.casefold() == s.casefold():
                    hit = v
                    break
        if isinstance(hit, dict):
            label = hit.get('full') or hit.get('short')
            if isinstance(label, str) and label.strip():
                return label.strip()
        elif isinstance(hit, str) and hit.strip():
            return hit.strip()
    if s in SPELL_SCHOOL_TO_CN:
        return SPELL_SCHOOL_TO_CN[s]
    if s.upper() in SPELL_SCHOOL_TO_CN:
        return SPELL_SCHOOL_TO_CN[s.upper()]
    mapped = SPELL_SCHOOL_TO_CN.get(s.casefold())
    return mapped or s


def _format_money_cp(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if value < 0:
        return None
    v = int(value) if float(value).is_integer() else value
    if v == 0:
        return '0 gp'
    if isinstance(v, int):
        if v >= 100 and v % 100 == 0:
            return f'{v // 100} gp'
        if v >= 10 and v % 10 == 0:
            if v >= 100:
                return f'{v / 100:g} gp'
            return f'{v // 10} sp'
        return f'{v} cp'
    if v >= 100:
        return f'{v / 100:g} gp'
    if v >= 10:
        return f'{v / 10:g} sp'
    return f'{v:g} cp'


def _format_weight(weight: Any) -> str | None:
    if isinstance(weight, (int, float)) and not isinstance(weight, bool):
        w = int(weight) if float(weight).is_integer() else weight
        return f'{w} 磅'
    if isinstance(weight, str) and weight.strip():
        return weight.strip()
    return None


def _extract_ac(raw_ac: Any) -> str | None:
    if isinstance(raw_ac, int) and not isinstance(raw_ac, bool):
        return str(raw_ac)
    if isinstance(raw_ac, str) and raw_ac.strip():
        return raw_ac.strip()
    if isinstance(raw_ac, list) and raw_ac:
        first = raw_ac[0]
        if isinstance(first, int) and not isinstance(first, bool):
            return str(first)
        if isinstance(first, dict):
            if first.get('ac') is not None:
                return str(first['ac'])
            if isinstance(first.get('special'), str) and first['special'].strip():
                return first['special'].strip()
        if isinstance(first, str) and first.strip():
            return first.strip()
    return None


def _extract_hp(raw_hp: Any) -> str | None:
    if isinstance(raw_hp, int) and not isinstance(raw_hp, bool):
        return str(raw_hp)
    if isinstance(raw_hp, str) and raw_hp.strip():
        return raw_hp.strip()
    if isinstance(raw_hp, dict):
        if raw_hp.get('average') is not None:
            return str(raw_hp['average'])
        if isinstance(raw_hp.get('special'), str) and raw_hp['special'].strip():
            return raw_hp['special'].strip()
        if isinstance(raw_hp.get('formula'), str) and raw_hp['formula'].strip():
            return raw_hp['formula'].strip()
    return None


def _extract_cr(raw_cr: Any) -> str | None:
    if isinstance(raw_cr, str):
        s = raw_cr.strip()
        return s if s and s.casefold() != 'unknown' else None
    if isinstance(raw_cr, (int, float)) and not isinstance(raw_cr, bool):
        return str(int(raw_cr) if float(raw_cr).is_integer() else raw_cr)
    if isinstance(raw_cr, dict):
        val = raw_cr.get('cr')
        if val is not None:
            s = str(val).strip()
            return s if s and s.casefold() != 'unknown' else None
    return None


def _extract_sizes(raw_size: Any) -> list[str]:
    if isinstance(raw_size, str) and raw_size.strip():
        return [_normalize_size(raw_size)]
    if isinstance(raw_size, list):
        out = [_normalize_size(str(s)) for s in raw_size if isinstance(s, str) and s.strip()]
        return list(dict.fromkeys(out))
    return []


def _format_speed(raw_speed: Any) -> str | None:
    if isinstance(raw_speed, (int, float)) and not isinstance(raw_speed, bool):
        return f'{int(raw_speed)}尺'
    if isinstance(raw_speed, str) and raw_speed.strip():
        return raw_speed.strip()
    if isinstance(raw_speed, dict):
        parts: list[str] = []
        mode_labels = {'walk': '步行', 'fly': '飞行', 'swim': '游泳', 'climb': '攀爬', 'burrow': '掘穴'}
        for key, label in mode_labels.items():
            val = raw_speed.get(key)
            if val is None:
                continue
            if isinstance(val, bool) and val:
                parts.append(label)
            elif isinstance(val, (int, float)):
                parts.append(f'{int(val)}尺' if key == 'walk' else f'{label}{int(val)}尺')
            elif isinstance(val, dict) and val.get('number') is not None:
                n = int(val['number'])
                parts.append(f'{n}尺' if key == 'walk' else f'{label}{n}尺')
        if parts:
            return '，'.join(parts)
    return None


def _format_casting_time(raw_time: Any) -> tuple[list[str], str | None]:
    if not isinstance(raw_time, list) or not raw_time:
        return [], None
    unit_cn = {
        'action': '动作',
        'bonus': '附赠动作',
        'reaction': '反应',
        'round': '轮',
        'minute': '分钟',
        'hour': '小时',
        'special': '特殊',
    }
    buckets: list[str] = []
    details: list[str] = []
    for entry in raw_time:
        if isinstance(entry, str) and entry.strip():
            s = entry.strip()
            buckets.append(s if s in {'动作', '附赠动作', '反应', '自由', '多类'} else '其他')
            details.append(s)
            continue
        if not isinstance(entry, dict):
            continue
        unit = str(entry.get('unit') or '').strip().casefold()
        num = entry.get('number', 1)
        cond = entry.get('condition')
        if unit == 'action' and num == 1:
            buckets.append('动作')
        elif unit == 'bonus' and num == 1:
            buckets.append('附赠动作')
        elif unit == 'reaction' and num == 1:
            buckets.append('反应')
        elif unit:
            buckets.append('其他')
        u_label = unit_cn.get(unit, unit)
        if unit == 'special':
            text = '特殊'
        elif u_label:
            text = f'{num} {u_label}'
        else:
            text = ''
        if isinstance(cond, str) and cond.strip():
            text = f'{text}（{cond.strip()}）' if text else cond.strip()
        if text:
            details.append(text)
    buckets = list(dict.fromkeys(buckets))
    detail = '或'.join(details) if details else None
    if detail in {'1 动作', '1 附赠动作', '1 反应'}:
        detail = None
    return buckets, detail


def _format_alignment(raw_align: Any) -> str | None:
    if isinstance(raw_align, str) and raw_align.strip():
        return ALIGNMENT_CODE_TO_CN.get(raw_align.strip(), raw_align.strip())
    if not isinstance(raw_align, list) or not raw_align:
        return None
    codes = [str(x).strip() for x in raw_align if isinstance(x, str) and x.strip()]
    if not codes:
        return None
    if codes == ['N']:
        return '绝对中立'
    if len(codes) == 1:
        return ALIGNMENT_CODE_TO_CN.get(codes[0], codes[0])
    return ''.join(ALIGNMENT_CODE_TO_CN.get(c, c) for c in codes)


def _format_prerequisite(raw_prereq: Any) -> str | None:
    if isinstance(raw_prereq, str) and raw_prereq.strip():
        return raw_prereq.strip()
    if not isinstance(raw_prereq, list) or not raw_prereq:
        return None
    parts: list[str] = []
    for p in raw_prereq:
        if isinstance(p, str) and p.strip():
            parts.append(p.strip())
            continue
        if not isinstance(p, dict):
            continue
        sub: list[str] = []
        lvl = p.get('level')
        if isinstance(lvl, int):
            sub.append(f'{lvl}级')
        elif isinstance(lvl, dict):
            lv = lvl.get('level')
            cls = lvl.get('class', {}).get('name') if isinstance(lvl.get('class'), dict) else None
            if cls and lv is not None:
                sub.append(f'{_normalize_class_name(cls)}{lv}级')
            elif lv is not None:
                sub.append(f'{lv}级')
        if p.get('spellcasting') or p.get('spellcasting2020') or p.get('spellcastingFeature'):
            sub.append('施法能力')
        if p.get('pact'):
            sub.append(f"魔契：{p['pact']}")
        if p.get('patron'):
            sub.append(f"宗主：{p['patron']}")
        if isinstance(p.get('ability'), list):
            ab_parts = []
            for ab in p['ability']:
                if isinstance(ab, dict):
                    ab_parts.append('、'.join(f'{_normalize_ability(k)} {v}' for k, v in ab.items()))
            if ab_parts:
                sub.append('或'.join(ab_parts))
        if isinstance(p.get('race'), list):
            r_names = [r.get('displayEntry') or r.get('name', '') for r in p['race'] if isinstance(r, dict)]
            r_names = [r for r in r_names if r]
            if r_names:
                sub.append('种族：' + '或'.join(r_names))
        if isinstance(p.get('feat'), list):
            f_names = [_strip_source_suffix(f) for f in p['feat'] if isinstance(f, str)]
            if f_names:
                sub.append('专长：' + '、'.join(f_names))
        if isinstance(p.get('spell'), list):
            s_names = [_strip_source_suffix(s).split('#', 1)[0] for s in p['spell'] if isinstance(s, str)]
            if s_names:
                sub.append('法术：' + '、'.join(s_names))
        if isinstance(p.get('campaign'), list):
            sub.append('战役：' + '、'.join(str(x) for x in p['campaign']))
        if isinstance(p.get('other'), str) and p['other'].strip():
            sub.append(p['other'].strip())
        if isinstance(p.get('otherSummary'), dict) and p['otherSummary'].get('entry'):
            sub.append(str(p['otherSummary']['entry']))
        if sub:
            parts.append('，'.join(sub))
    return '；'.join(parts) if parts else None


def _resolve_item_type_and_subtypes(
    raw_kind: str,
    item: dict[str, Any],
    effective: dict[str, Any],
    ctx: dict[str, Any],
) -> tuple[str | None, list[str]]:
    custom_item_types = ctx.get('item_types') or ctx.get('itemTypes') or {}
    subtypes: list[str] = []
    primary: str | None = None

    raw_type = effective.get('type') or item.get('type')
    code = _strip_source_suffix(raw_type).upper() if isinstance(raw_type, str) and raw_type.strip() else ''

    if code == 'GV' or raw_kind == 'magicvariant':
        subtypes.append('通用变体')
        # Infer target item_type from requires
        reqs = item.get('requires') if isinstance(item.get('requires'), list) else []
        inferred: list[str] = []
        for req in reqs:
            if not isinstance(req, dict):
                continue
            if req.get('weapon'):
                inferred.append('武器')
            if req.get('armor'):
                inferred.append('护甲')
            rt = req.get('type')
            if isinstance(rt, str) and rt.strip():
                rc = _strip_source_suffix(rt).upper()
                if rc in ITEM_TYPE_CODE_TO_CN:
                    inferred.append(ITEM_TYPE_CODE_TO_CN[rc][0])
        if item.get('ammo') and not inferred:
            inferred.append('弹药')
        inferred = list(dict.fromkeys(inferred))
        if len(inferred) == 1:
            primary = inferred[0]
        elif len(inferred) > 1:
            primary = inferred[0]
            subtypes.extend(inferred[1:])
        else:
            primary = '通用变体'
    elif code:
        if isinstance(custom_item_types, dict) and code in custom_item_types:
            primary = str(custom_item_types[code])
        elif code in ITEM_TYPE_CODE_TO_CN:
            primary, sub = ITEM_TYPE_CODE_TO_CN[code]
            if sub:
                subtypes.append(sub)
        else:
            primary = _strip_source_suffix(raw_type)
    else:
        if effective.get('wondrous'):
            primary = '奇物'
        elif effective.get('staff'):
            primary = '法杖'
        elif effective.get('weapon'):
            primary = '武器'
        elif effective.get('armor'):
            primary = '护甲'
        elif effective.get('poison') or effective.get('poisonTypes'):
            primary = '毒药'

    if effective.get('tattoo'):
        subtypes.append('魔法纹身')
    if effective.get('scfType'):
        subtypes.append('法器')
    wcat = effective.get('weaponCategory')
    if isinstance(wcat, str) and wcat.strip():
        subtypes.append({'simple': '简易', 'martial': '军用'}.get(wcat.strip().casefold(), wcat.strip()))
    if effective.get('firearm'):
        subtypes.append('火器')
    if effective.get('cursed') or effective.get('curse'):
        subtypes.append('诅咒')
    if effective.get('sentient'):
        subtypes.append('智能')

    subtypes = list(dict.fromkeys(s for s in subtypes if s and s != primary))
    return primary, subtypes


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------

def extract_fields(
    raw_kind: str,
    entry_type: str,
    item: dict[str, Any] | None,
    *,
    resolved_context: dict[str, Any] | None = None,
    copy_status: str = 'ok',
) -> tuple[dict[str, Any], dict[str, list[str]], list[str]]:
    """Extract structured quick-reference fields from a 5etools entity record.

    Returns (fields, quickref, field_conflicts).

    Never infers False from absence when copy_status != 'ok' or when a field is
    missing from an object that does not define it: unextracted booleans remain
    None in `fields` and normalize to ['__unknown__'] in `quickref`.
    """
    rec = item if isinstance(item, dict) else {}
    ctx = resolved_context if isinstance(resolved_context, dict) else {}
    can_infer_false = copy_status == 'ok'
    fields: dict[str, Any] = {}
    conflicts: list[str] = []

    if entry_type == 'spell':
        lvl = rec.get('level')
        if lvl is not None:
            fields['spell_level'] = str(lvl)

        school_raw = rec.get('school')
        if isinstance(school_raw, str) and school_raw.strip():
            fields['school'] = _normalize_school(school_raw, ctx)

        # Spell classes from resolved_context and/or embedded item.classes
        cls_list: list[str] = []
        ctx_classes = ctx.get('spell_classes') or ctx.get('classes')
        if isinstance(ctx_classes, list):
            for c in ctx_classes:
                if isinstance(c, str) and c.strip():
                    cls_list.append(_normalize_class_name(c))
        raw_classes = rec.get('classes')
        if isinstance(raw_classes, dict):
            for list_key in ('fromClassList', 'fromClassListVariant'):
                for c_obj in raw_classes.get(list_key) or []:
                    if isinstance(c_obj, dict) and isinstance(c_obj.get('name'), str):
                        cls_list.append(_normalize_class_name(c_obj['name']))
        cls_list = list(dict.fromkeys(cls_list))
        if cls_list:
            fields['spell_classes'] = cls_list

        ct_buckets, ct_detail = _format_casting_time(rec.get('time'))
        if ct_buckets:
            fields['casting_time'] = ct_buckets[0] if len(ct_buckets) == 1 else ct_buckets
        if ct_detail:
            fields['casting_time_detail'] = ct_detail

        # Duration -> concentration
        duration = rec.get('duration')
        has_duration = isinstance(duration, list) and len(duration) > 0
        if has_duration:
            has_conc = any(isinstance(d, dict) and bool(d.get('concentration')) for d in duration)
            if has_conc:
                fields['concentration'] = True
            elif can_infer_false:
                fields['concentration'] = False
            else:
                fields['concentration'] = None
        else:
            fields['concentration'] = None

        # Meta -> ritual
        meta = rec.get('meta')
        explicit_ritual = isinstance(meta, dict) and meta.get('ritual') is True
        explicit_not_ritual = isinstance(meta, dict) and meta.get('ritual') is False
        has_core_spell = (
            rec.get('level') is not None
            and bool(rec.get('school'))
            and bool(rec.get('time'))
            and has_duration
        )
        if explicit_ritual:
            fields['ritual'] = True
        elif explicit_not_ritual:
            fields['ritual'] = False
        elif can_infer_false and has_core_spell:
            fields['ritual'] = False
        else:
            fields['ritual'] = None

        # Components -> verbal, somatic, material, special_material
        comps = rec.get('components')
        if isinstance(comps, dict) and len(comps) > 0:
            v_val = comps.get('v')
            s_val = comps.get('s')
            m_val = comps.get('m')
            if v_val is True:
                fields['verbal'] = True
            elif v_val is False or can_infer_false:
                fields['verbal'] = False
            else:
                fields['verbal'] = None

            if s_val is True:
                fields['somatic'] = True
            elif s_val is False or can_infer_false:
                fields['somatic'] = False
            else:
                fields['somatic'] = None

            if m_val is not None and m_val is not False:
                fields['material'] = True
                if isinstance(m_val, dict):
                    if isinstance(m_val.get('text'), str) and m_val['text'].strip():
                        fields['material_detail'] = m_val['text'].strip()
                    has_special = bool(m_val.get('cost')) or bool(m_val.get('consume'))
                    if has_special:
                        fields['special_material'] = True
                    elif can_infer_false:
                        fields['special_material'] = False
                    else:
                        fields['special_material'] = None
                elif isinstance(m_val, str) and m_val.strip():
                    fields['material_detail'] = m_val.strip()
                    fields['special_material'] = False if can_infer_false else None
                else:
                    fields['special_material'] = False if can_infer_false else None
            elif m_val is False or can_infer_false:
                fields['material'] = False
                fields['special_material'] = NA
            else:
                fields['material'] = None
                fields['special_material'] = None
        else:
            fields['verbal'] = None
            fields['somatic'] = None
            fields['material'] = None
            fields['special_material'] = None

    elif entry_type == 'creature':
        sizes = _extract_sizes(rec.get('size'))
        if sizes:
            fields['size'] = sizes[0] if len(sizes) == 1 else sizes

        raw_type = rec.get('type')
        ctypes: list[str] = []
        ctags: list[str] = []
        if isinstance(raw_type, str) and raw_type.strip():
            ctypes.append(_normalize_creature_type(raw_type))
        elif isinstance(raw_type, dict):
            inner = raw_type.get('type')
            if isinstance(inner, str) and inner.strip():
                ctypes.append(_normalize_creature_type(inner))
            elif isinstance(inner, dict) and isinstance(inner.get('choose'), list):
                for ch in inner['choose']:
                    if isinstance(ch, str) and ch.strip():
                        ctypes.append(_normalize_creature_type(ch))
            for tg in raw_type.get('tags') or []:
                if isinstance(tg, str) and tg.strip():
                    ctags.append(tg.strip())
                elif isinstance(tg, dict) and isinstance(tg.get('tag'), str):
                    prefix = tg.get('prefix')
                    ctags.append(f'{prefix} {tg["tag"]}'.strip() if prefix else tg['tag'])
            if raw_type.get('swarmSize'):
                ctags.append('集群')
        ctypes = list(dict.fromkeys(ctypes))
        if ctypes:
            fields['creature_type'] = ctypes[0] if len(ctypes) == 1 else ctypes
        if ctags:
            fields['creature_tags'] = list(dict.fromkeys(ctags))

        cr = _extract_cr(rec.get('cr'))
        if cr is not None:
            fields['cr'] = cr
        ac = _extract_ac(rec.get('ac'))
        if ac is not None:
            fields['ac'] = ac
        hp = _extract_hp(rec.get('hp'))
        if hp is not None:
            fields['hp'] = hp

        has_statblock = any(rec.get(k) is not None for k in ('cr', 'ac', 'hp', 'str', 'dex', 'con'))

        # legendary_actions
        has_leg = bool(rec.get('legendary')) or bool(rec.get('legendaryActions')) or bool(rec.get('legendaryActionsLair'))
        if has_leg:
            fields['legendary_actions'] = True
        elif can_infer_false and has_statblock:
            fields['legendary_actions'] = False
        else:
            fields['legendary_actions'] = None

        # lair_actions
        ctx_lair = ctx.get('has_lair_actions')
        lg_obj = ctx.get('legendary_group')
        if isinstance(ctx_lair, bool):
            fields['lair_actions'] = ctx_lair if (ctx_lair or can_infer_false) else None
        elif isinstance(lg_obj, dict):
            has_lair = bool(lg_obj.get('lairActions'))
            fields['lair_actions'] = True if has_lair else (False if can_infer_false else None)
        elif rec.get('lairActions') or rec.get('legendaryActionsLair') or (isinstance(rec.get('cr'), dict) and rec['cr'].get('lair')):
            fields['lair_actions'] = True
        elif rec.get('legendaryGroup'):
            # LegendaryGroup exists but was not resolved in context -> unknown
            fields['lair_actions'] = None
        elif can_infer_false and has_statblock:
            fields['lair_actions'] = False
        else:
            fields['lair_actions'] = None

        # has_local_audio
        ctx_audio = ctx.get('has_local_audio')
        if isinstance(ctx_audio, bool):
            fields['has_local_audio'] = ctx_audio
        else:
            fields['has_local_audio'] = None

    elif entry_type == 'magic_item':
        inherits = rec.get('inherits') if isinstance(rec.get('inherits'), dict) else {}
        effective = {**inherits, **{k: v for k, v in rec.items() if v is not None}}

        raw_rarity = effective.get('rarity')
        if isinstance(raw_rarity, str) and raw_rarity.strip():
            fields['rarity'] = _normalize_rarity(raw_rarity)

        primary, subtypes = _resolve_item_type_and_subtypes(raw_kind, rec, effective, ctx)
        if primary:
            fields['item_type'] = primary
        if subtypes:
            fields['item_subtypes'] = subtypes

        tier_val = effective.get('tier')
        if isinstance(tier_val, str) and tier_val.strip():
            fields['item_tier'] = {'minor': '次要', 'major': '主要'}.get(tier_val.strip().casefold(), tier_val.strip())

        req_attune = effective.get('reqAttune')
        has_item_body = (
            raw_rarity is not None
            or bool(effective.get('entries'))
            or bool(effective.get('wondrous'))
            or bool(effective.get('type'))
            or bool(inherits)
        )
        if req_attune is True:
            fields['attunement'] = True
        elif req_attune is False:
            fields['attunement'] = False
        elif isinstance(req_attune, str) and req_attune.strip():
            fields['attunement'] = True
            req_text = req_attune.strip()
            fields['attunement_requirements'] = '可选' if req_text.casefold() == 'optional' else req_text
        elif can_infer_false and has_item_body:
            fields['attunement'] = False
        else:
            fields['attunement'] = None

    elif entry_type == 'equipment':
        primary, subtypes = _resolve_item_type_and_subtypes(raw_kind, rec, rec, ctx)
        if primary:
            fields['item_type'] = primary
        if subtypes:
            fields['item_subtypes'] = subtypes

        price = _format_money_cp(rec.get('value'))
        if price is not None:
            fields['price'] = price
        weight = _format_weight(rec.get('weight'))
        if weight is not None:
            fields['weight'] = weight

        dmg1 = rec.get('dmg1')
        dmg2 = rec.get('dmg2')
        dmg_type = rec.get('dmgType')
        if isinstance(dmg1, str) and dmg1.strip():
            dice = f'{dmg1.strip()}/{dmg2.strip()}' if isinstance(dmg2, str) and dmg2.strip() else dmg1.strip()
            dt = DMG_TYPE_TO_CN.get(str(dmg_type).strip().upper(), str(dmg_type).strip()) if dmg_type else ''
            fields['damage'] = f'{dice} {dt}'.strip()

        if rec.get('ac') is not None:
            fields['ac'] = str(rec['ac'])

        raw_mastery = rec.get('mastery')
        if isinstance(raw_mastery, list) and raw_mastery:
            masteries: list[str] = []
            for m in raw_mastery:
                if isinstance(m, str) and m.strip():
                    masteries.append(_strip_source_suffix(m))
                elif isinstance(m, dict) and isinstance(m.get('uid'), str):
                    masteries.append(_strip_source_suffix(m['uid']))
            masteries = list(dict.fromkeys(masteries))
            if masteries:
                fields['mastery'] = masteries[0] if len(masteries) == 1 else masteries

    elif entry_type == 'poison':
        pts = rec.get('poisonTypes')
        if isinstance(pts, list) and pts:
            mapped = [POISON_TYPE_TO_CN.get(str(p).strip().casefold(), POISON_TYPE_TO_CN.get(str(p).strip(), str(p).strip())) for p in pts if str(p).strip()]
            mapped = list(dict.fromkeys(mapped))
            if mapped:
                fields['poison_type'] = mapped[0] if len(mapped) == 1 else mapped
        price = _format_money_cp(rec.get('value'))
        if price is not None:
            fields['price'] = price

    elif entry_type == 'class':
        hd = rec.get('hd')
        if isinstance(hd, dict) and hd.get('faces'):
            fields['hit_die'] = f"d{hd['faces']}"
        sca = rec.get('spellcastingAbility')
        if isinstance(sca, str) and sca.strip():
            fields['spellcasting_ability'] = _normalize_ability(sca)
        cp = rec.get('casterProgression')
        if isinstance(cp, str) and cp.strip():
            fields['caster_progression'] = CASTER_PROGRESSION_TO_CN.get(cp.strip(), cp.strip())
        profs = rec.get('proficiency')
        if isinstance(profs, list) and profs:
            fields['saving_throws'] = [_normalize_ability(str(p)) for p in profs if isinstance(p, str) and p.strip()]

    elif entry_type == 'subclass':
        owner = ctx.get('owner_class') or rec.get('className')
        if isinstance(owner, str) and owner.strip():
            fields['owner_class'] = _normalize_class_name(owner)
        sca = rec.get('spellcastingAbility')
        if isinstance(sca, str) and sca.strip():
            fields['spellcasting_ability'] = _normalize_ability(sca)
        cp = rec.get('casterProgression')
        if isinstance(cp, str) and cp.strip():
            fields['caster_progression'] = CASTER_PROGRESSION_TO_CN.get(cp.strip(), cp.strip())

    elif entry_type == 'class_feature':
        owner = ctx.get('owner_class') or rec.get('className')
        owner_sub = ctx.get('owner_subclass') or rec.get('subclassShortName')
        if raw_kind == 'classFeature':
            fields['feature_kind'] = '职业特性'
        elif raw_kind == 'subclassFeature':
            fields['feature_kind'] = '子职特性'
        elif raw_kind == 'optionalfeature':
            custom_opt = ctx.get('optional_feature_types') or ctx.get('optionalFeatureTypes') or {}
            fts = rec.get('featureType')
            if isinstance(fts, str):
                fts = [fts]
            kinds: list[str] = []
            inferred_classes: list[str] = []
            if isinstance(fts, list):
                for ft in fts:
                    if not isinstance(ft, str) or not ft.strip():
                        continue
                    code = ft.strip()
                    label = (custom_opt.get(code) if isinstance(custom_opt, dict) else None) or OPT_FEATURE_TYPE_TO_CN.get(code, code)
                    kinds.append(str(label))
                    if not owner and code in OPT_FEATURE_OWNER_CLASS:
                        inferred_classes.append(OPT_FEATURE_OWNER_CLASS[code])
            kinds = list(dict.fromkeys(kinds))
            fields['feature_kind'] = (kinds[0] if len(kinds) == 1 else kinds) if kinds else '可选特性'
            inferred_classes = list(dict.fromkeys(inferred_classes))
            if not owner and len(inferred_classes) == 1:
                owner = inferred_classes[0]
        else:
            fields['feature_kind'] = '职业特性'

        if isinstance(owner, str) and owner.strip():
            fields['owner_class'] = _normalize_class_name(owner)
        if isinstance(owner_sub, str) and owner_sub.strip():
            fields['owner_subclass'] = owner_sub.strip()

        lvl = rec.get('level')
        if lvl is None and isinstance(rec.get('prerequisite'), list):
            for p in rec['prerequisite']:
                if isinstance(p, dict) and p.get('level') is not None:
                    lv = p['level']
                    if isinstance(lv, int):
                        lvl = lv
                        break
                    if isinstance(lv, dict) and isinstance(lv.get('level'), int):
                        lvl = lv['level']
                        break
        if lvl is not None:
            fields['level'] = str(lvl)

    elif entry_type == 'species':
        sizes = _extract_sizes(rec.get('size'))
        if sizes:
            fields['size'] = sizes[0] if len(sizes) == 1 else sizes
        dv = rec.get('darkvision')
        if isinstance(dv, (int, float)) and not isinstance(dv, bool) and dv > 0:
            fields['darkvision'] = f'{int(dv)}尺'
        elif isinstance(dv, str) and dv.strip():
            fields['darkvision'] = dv.strip()
        speed = _format_speed(rec.get('speed'))
        if speed:
            fields['speed'] = speed
        if raw_kind == 'subrace' and isinstance(rec.get('raceName'), str) and rec['raceName'].strip():
            fields['parent_species'] = rec['raceName'].strip()

    elif entry_type == 'background':
        feats_raw = rec.get('feats')
        granted: list[str] = []
        if isinstance(feats_raw, list):
            for f_obj in feats_raw:
                if isinstance(f_obj, dict):
                    for k, v in f_obj.items():
                        if not v:
                            continue
                        if k == 'any':
                            granted.append('任意专长')
                        elif k == 'anyFromCategory':
                            granted.append('自选类别专长')
                        else:
                            granted.append(_strip_source_suffix(k))
        granted = list(dict.fromkeys(granted))
        if granted:
            fields['granted_feat'] = granted[0] if len(granted) == 1 else granted

        skills_raw = rec.get('skillProficiencies')
        skills: list[str] = []
        if isinstance(skills_raw, list):
            for s_obj in skills_raw:
                if isinstance(s_obj, dict):
                    for k, v in s_obj.items():
                        if k in {'choose', 'any'}:
                            continue
                        if v is True:
                            skills.append(_normalize_skill(k))
            if not skills and any(isinstance(s_obj, dict) and ('choose' in s_obj or 'any' in s_obj) for s_obj in skills_raw):
                skills.append('自选')
        skills = list(dict.fromkeys(skills))
        if skills:
            fields['skills'] = skills

    elif entry_type == 'feat':
        custom_fc = ctx.get('feat_categories') or ctx.get('featCategories') or {}
        cat_raw = rec.get('category')
        if cat_raw is not None:
            cats_in = cat_raw if isinstance(cat_raw, list) else [cat_raw]
            cats_out: list[str] = []
            for c in cats_in:
                if not isinstance(c, str) or not c.strip():
                    continue
                code = c.strip()
                label = (custom_fc.get(code) if isinstance(custom_fc, dict) else None) or FEAT_CATEGORY_TO_CN.get(code, code)
                cats_out.append(str(label))
            cats_out = list(dict.fromkeys(cats_out))
            if cats_out:
                fields['feat_category'] = cats_out[0] if len(cats_out) == 1 else cats_out
        prereq = _format_prerequisite(rec.get('prerequisite'))
        if prereq:
            fields['prerequisite'] = prereq

    elif entry_type == 'psionic':
        custom_psi = ctx.get('psionic_types') or ctx.get('psionicTypes') or {}
        pt = rec.get('type')
        if isinstance(pt, str) and pt.strip():
            code = pt.strip()
            hit = custom_psi.get(code) if isinstance(custom_psi, dict) else None
            if isinstance(hit, dict):
                fields['psionic_type'] = str(hit.get('full') or hit.get('short') or code)
            elif isinstance(hit, str) and hit.strip():
                fields['psionic_type'] = hit.strip()
            else:
                fields['psionic_type'] = PSIONIC_TYPE_TO_CN.get(code, code)
        order = rec.get('order')
        if isinstance(order, str) and order.strip() and order.strip().casefold() != 'none':
            fields['psionic_order'] = order.strip()

    elif entry_type == 'vehicle':
        if raw_kind == 'vehicleUpgrade':
            custom_vut = ctx.get('vehicle_upgrade_types') or ctx.get('vehicleUpgradeTypes') or {}
            uts = rec.get('upgradeType')
            if isinstance(uts, str):
                uts = [uts]
            vtypes: list[str] = []
            if isinstance(uts, list):
                for ut in uts:
                    if isinstance(ut, str) and ut.strip():
                        code = ut.strip()
                        label = (custom_vut.get(code) if isinstance(custom_vut, dict) else None) or VEHICLE_UPGRADE_TYPE_TO_CN.get(code, code)
                        vtypes.append(str(label))
            vtypes = list(dict.fromkeys(vtypes))
            fields['vehicle_type'] = (vtypes[0] if len(vtypes) == 1 else vtypes) if vtypes else '载具升级'
        else:
            vt = rec.get('vehicleType')
            if isinstance(vt, str) and vt.strip():
                fields['vehicle_type'] = VEHICLE_TYPE_TO_CN.get(vt.strip(), vt.strip())
        sizes = _extract_sizes(rec.get('size'))
        if sizes:
            fields['size'] = sizes[0] if len(sizes) == 1 else sizes
        if rec.get('capCrew') is not None:
            fields['crew'] = str(rec['capCrew'])
        if rec.get('capPassenger') is not None:
            fields['passengers'] = str(rec['capPassenger'])

    elif entry_type == 'facility':
        ft = rec.get('facilityType')
        if isinstance(ft, str) and ft.strip():
            fields['facility_kind'] = FACILITY_KIND_TO_CN.get(ft.strip().casefold(), ft.strip())
        if rec.get('level') is not None:
            fields['level'] = str(rec['level'])

    elif entry_type == 'object':
        sizes = _extract_sizes(rec.get('size'))
        if sizes:
            fields['size'] = sizes[0] if len(sizes) == 1 else sizes
        ot = rec.get('objectType')
        if isinstance(ot, str) and ot.strip():
            fields['object_type'] = OBJECT_TYPE_TO_CN.get(ot.strip(), ot.strip())
        ac = _extract_ac(rec.get('ac'))
        if ac is not None:
            fields['ac'] = ac
        hp = _extract_hp(rec.get('hp'))
        if hp is not None:
            fields['hp'] = hp

    elif entry_type == 'deck':
        dset = ctx.get('deck_set') or (rec.get('set') if raw_kind == 'card' else rec.get('name'))
        if isinstance(dset, str) and dset.strip():
            fields['deck_set'] = dset.strip()
        cc = ctx.get('card_count')
        if cc is not None:
            fields['card_count'] = str(cc)
        elif raw_kind == 'deck' and isinstance(rec.get('cards'), list):
            total = 0
            for c in rec['cards']:
                if isinstance(c, dict) and isinstance(c.get('count'), int):
                    total += c['count']
                else:
                    total += 1
            fields['card_count'] = str(total)

    elif entry_type == 'recipe':
        if raw_kind == 'crochetPattern':
            pt = rec.get('patternType')
            if isinstance(pt, list) and pt:
                fields['dish_types'] = [str(x) for x in pt if str(x).strip()]
            elif isinstance(pt, str) and pt.strip():
                fields['dish_types'] = [pt.strip()]
            else:
                fields['dish_types'] = ['钩织图样']
        else:
            dt = rec.get('dishTypes')
            if isinstance(dt, list) and dt:
                fields['dish_types'] = list(dict.fromkeys(str(x).strip() for x in dt if str(x).strip()))
            diet = rec.get('diet')
            if isinstance(diet, str) and diet.strip():
                fields['diet'] = DIET_TO_CN.get(diet.strip().upper(), diet.strip())

    elif entry_type == 'reward':
        if raw_kind == 'charoption':
            custom_co = ctx.get('char_option_types') or ctx.get('charOption') or {}
            ots = rec.get('optionType')
            if isinstance(ots, str):
                ots = [ots]
            kinds = []
            if isinstance(ots, list):
                for ot in ots:
                    if isinstance(ot, str) and ot.strip():
                        code = ot.strip()
                        label = (custom_co.get(code) if isinstance(custom_co, dict) else None) or CHAR_OPTION_TYPE_TO_CN.get(code, code)
                        kinds.append(str(label))
            kinds = list(dict.fromkeys(kinds))
            fields['reward_kind'] = (kinds[0] if len(kinds) == 1 else kinds) if kinds else '角色创建选项'
        elif raw_kind == 'cult':
            ct = rec.get('type')
            fields['reward_kind'] = CULT_TYPE_TO_CN.get(str(ct).strip(), f'邪教（{ct}）') if ct else '邪教'
        elif raw_kind == 'boon':
            bt = rec.get('type')
            fields['reward_kind'] = '恶魔恩赐' if bt == 'Demonic' else (str(bt) if bt else '恩赐')
        else:
            rt = rec.get('type')
            if isinstance(rt, str) and rt.strip():
                fields['reward_kind'] = REWARD_TYPE_TO_CN.get(rt.strip(), rt.strip())

    elif entry_type == 'deity':
        pan = rec.get('pantheon')
        if isinstance(pan, str) and pan.strip():
            fields['pantheon'] = pan.strip()
        align = _format_alignment(rec.get('alignment'))
        if align:
            fields['alignment'] = align
        doms = rec.get('domains')
        if isinstance(doms, list) and doms:
            fields['domains'] = list(dict.fromkeys(str(d).strip() for d in doms if str(d).strip()))

    elif entry_type == 'trap':
        th = rec.get('trapHazType')
        if isinstance(th, str) and th.strip():
            fields['trap_kind'] = TRAP_HAZARD_TYPE_TO_CN.get(th.strip(), th.strip())

    elif entry_type == 'hazard':
        th = rec.get('trapHazType')
        if isinstance(th, str) and th.strip():
            fields['hazard_kind'] = TRAP_HAZARD_TYPE_TO_CN.get(th.strip(), th.strip())

    elif entry_type == 'table':
        fields['table_kind'] = TABLE_KIND_TO_CN.get(raw_kind, '随机表')

    elif entry_type == 'condition':
        fields['condition_kind'] = '持续状态' if raw_kind == 'status' else '状态'

    elif entry_type == 'action':
        at_buckets, _ = _format_casting_time(rec.get('time'))
        if at_buckets:
            fields['action_time'] = at_buckets[0] if len(at_buckets) == 1 else at_buckets

    elif entry_type == 'skill':
        ab = rec.get('ability')
        if isinstance(ab, str) and ab.strip():
            fields['ability'] = _normalize_ability(ab)

    elif entry_type == 'language':
        if raw_kind == 'languageScript':
            fields['language_type'] = '文字'
        else:
            lt = rec.get('type')
            if isinstance(lt, str) and lt.strip():
                fields['language_type'] = LANGUAGE_TYPE_TO_CN.get(lt.strip().casefold(), lt.strip())
        sc = rec.get('script')
        if isinstance(sc, str) and sc.strip():
            fields['script'] = '无' if sc.strip().casefold() == 'none' else sc.strip()

    elif entry_type == 'rule':
        fields['rule_kind'] = RULE_KIND_TO_CN.get(raw_kind, '规则')

    elif entry_type == 'book':
        bk = ctx.get('book_kind')
        if isinstance(bk, str) and bk.strip():
            fields['book_kind'] = bk.strip()
        else:
            fields['book_kind'] = '模组' if raw_kind == 'adventure' else '规则书'
        bg = rec.get('group')
        if isinstance(bg, str) and bg.strip():
            fields['book_group'] = BOOK_GROUP_TO_CN.get(bg.strip(), bg.strip())
        pub = rec.get('published')
        if isinstance(pub, str) and pub.strip():
            fields['published'] = pub.strip()

    # Common provenance / curation facets from resolved_context
    if isinstance(ctx.get('package_kind'), str) and ctx['package_kind'].strip():
        fields['package_kind'] = ctx['package_kind'].strip()
    if isinstance(ctx.get('tier'), str) and ctx['tier'].strip():
        fields['tier'] = ctx['tier'].strip()

    # Build quickref for this entry's schema conditions + any common facet fields present
    quickref_fields = list(dict.fromkeys([
        *[f for f in type_conditions(entry_type) if f in LABELS],
        *[f for f in ('package_kind', 'tier') if f in fields],
    ]))
    quickref: dict[str, list[str]] = {}
    for f in quickref_fields:
        vals = normalized(fields, f)
        quickref[f] = vals
        if vals == [CONFLICT]:
            conflicts.append(f)

    return fields, quickref, conflicts
