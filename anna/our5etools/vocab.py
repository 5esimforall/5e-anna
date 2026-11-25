"""Shared 5etools Chinese vocabularies, media constants, and safe utility helpers."""
from __future__ import annotations

from fractions import Fraction
import math
import mimetypes
from pathlib import PurePosixPath
import re
from typing import Any

REMOTE_IMAGE_MIRRORS: tuple[str, ...] = (
    'https://5e.kiwee.top/img/',
    'https://raw.githubusercontent.com/5etools-mirror-3/5etools-img/main/',
)

SAFE_REMOTE_MEDIA_EXTS: frozenset[str] = frozenset({
    '.png', '.webp', '.jpg', '.jpeg', '.gif', '.svg', '.avif',
    '.opus', '.ogg', '.mp3', '.wav', '.m4a',
})

MIME_OVERRIDES: dict[str, str] = {
    '.opus': 'audio/ogg; codecs=opus',
    '.ogg': 'audio/ogg',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.m4a': 'audio/mp4',
    '.webp': 'image/webp',
    '.png': 'image/png',
    '.avif': 'image/avif',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
    '.woff': 'font/woff',
    '.ttf': 'font/ttf',
    '.otf': 'font/otf',
    '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.htm': 'text/html; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
}

ABILITY_ABVS: tuple[str, ...] = ('str', 'dex', 'con', 'int', 'wis', 'cha')

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

FACILITY_KIND_TO_CN: dict[str, str] = {
    'basic': '基础设施',
    'special': '特殊设施',
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

CR_XP: dict[str, int] = {
    '0': 10, '1/8': 25, '1/4': 50, '1/2': 100,
    '1': 200, '2': 450, '3': 700, '4': 1100, '5': 1800,
    '6': 2300, '7': 2900, '8': 3900, '9': 5000, '10': 5900,
    '11': 7200, '12': 8400, '13': 10000, '14': 11500, '15': 13000,
    '16': 15000, '17': 18000, '18': 20000, '19': 22000, '20': 25000,
    '21': 33000, '22': 41000, '23': 50000, '24': 62000, '25': 75000,
    '26': 90000, '27': 105000, '28': 120000, '29': 135000, '30': 155000,
}

_CR_TOKEN_RE = re.compile(r'\d+(?:\s*/\s*\d+|\.\d+)?')


def guess_mime(ext_or_name: str, default: str = 'application/octet-stream') -> str:
    """Resolve MIME type from a file extension or filename via `MIME_OVERRIDES` and `mimetypes`."""
    ext = ext_or_name.lower() if ext_or_name.startswith('.') else PurePosixPath(ext_or_name).suffix.lower()
    if ext in MIME_OVERRIDES:
        return MIME_OVERRIDES[ext]
    mime, _ = mimetypes.guess_type(f'file{ext}' if ext else ext_or_name)
    return mime or default


def safe_relpath(relpath: Any, *, allow_leading_slash: bool = False) -> PurePosixPath | None:
    """Validate a POSIX relative path without traversal, backslashes, NULs, or empty segments."""
    if not isinstance(relpath, str):
        return None
    cleaned = relpath.strip() if allow_leading_slash else relpath
    if not cleaned or '\x00' in cleaned or '\\' in cleaned:
        return None
    if not allow_leading_slash and cleaned.startswith('/'):
        return None
    target = cleaned.lstrip('/') if allow_leading_slash else cleaned
    if not target:
        return None
    posix = PurePosixPath(target)
    if posix.is_absolute() or not posix.parts or any(part in ('', '.', '..') for part in posix.parts):
        return None
    return posix


def parse_cr_number(raw: Any, *, strip_filter_prefix: bool = False) -> float | None:
    """Parse a 5etools Challenge Rating (`'1/2'`, `'1 / 4'`, `'13'`, `'0.25'`) to a finite float."""
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip()
    if strip_filter_prefix:
        text = text.lstrip('&!').strip()
    if not text or not _CR_TOKEN_RE.fullmatch(text):
        return None
    try:
        val = float(Fraction(text.replace(' ', '')))
    except (ValueError, ZeroDivisionError):
        return None
    return val if math.isfinite(val) else None


def format_cr_number(raw: str) -> str | None:
    """Normalize a filter CR string (`&1/2`, `2.0`, `13`) to canonical display text."""
    val = parse_cr_number(raw, strip_filter_prefix=True)
    if val is None:
        return None
    if val == int(val):
        return str(int(val))
    return f'{val:g}'


def split_by_tags(text: str, prefixes: tuple[str, ...] = ('@', '=')) -> list[str]:
    """Split a 5etools string into plain segments and balanced `{@tag ...}` / `{=...}` tokens."""
    if not text:
        return []
    if not any(f'{{{p}' in text for p in prefixes):
        return [text]
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ''
        if ch == '{' and nxt in prefixes:
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
