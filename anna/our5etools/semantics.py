"""Semantic resolution for our5etools corpus: provenance, _copy, _versions, inheritance, fluff, relations, and lookup index."""
from __future__ import annotations

import ast
from collections import defaultdict
import copy
from dataclasses import dataclass, field
from hashlib import sha256
import math
import re
from typing import Any, TYPE_CHECKING

from anna.serialization import canonical

if TYPE_CHECKING:
    from anna.our5etools.inventory import Inventory

COLLECTION = 'our5etools'

COPY_ENTRY_PROPS: tuple[str, ...] = (
    'action', 'bonus', 'reaction', 'trait', 'legendary', 'mythic', 'variant', 'spellcasting',
    'actionHeader', 'bonusHeader', 'reactionHeader', 'legendaryHeader', 'mythicHeader',
)

MERGE_REQUIRES_PRESERVE_BASE: frozenset[str] = frozenset({
    'page', 'otherSources', 'referenceSources', 'srd', 'srd52',
    'basicRules', 'basicRules2024', 'reprintedAs',
    'hasFluff', 'hasFluffImages', 'hasToken', 'tokenCredit',
    'tokenCustom', 'foundryTokenScale', 'altArt', '_versions',
})

MERGE_REQUIRES_PRESERVE_BY_KIND: dict[str, frozenset[str]] = {
    'monster': frozenset({
        'ENG_name', 'legendaryGroup', 'environment', 'soundClip',
        'altArt', 'variant', 'dragonCastingColor', 'familiar',
    }),
    'item': frozenset({'lootTables', 'tier'}),
    'itemGroup': frozenset({'lootTables', 'tier'}),
    'magicvariant': frozenset({'lootTables', 'tier'}),
}

NON_ENTITY_PROPS: frozenset[str] = frozenset({
    'bookData', 'adventureData', 'roll20Spell', 'makebrewCreatureTrait',
    'itemTypeAdditionalEntries', 'dragonMundaneItems',
})

# Cross-kind lookup pools matching 5etools DataUtil behavior
COPY_POOL_GROUP: dict[str, str] = {
    'item': 'item_pool',
    'baseitem': 'item_pool',
    'itemGroup': 'item_pool',
}

FLUFF_TO_PRIMARY_KINDS: dict[str, tuple[str, ...]] = {
    'monsterFluff': ('monster',),
    'itemFluff': ('item', 'baseitem', 'itemGroup', 'magicvariant'),
    'raceFluff': ('race', 'subrace'),
    'classFluff': ('class',),
    'subclassFluff': ('subclass',),
    'backgroundFluff': ('background',),
    'featFluff': ('feat',),
    'spellFluff': ('spell',),
    'vehicleFluff': ('vehicle',),
    'objectFluff': ('object',),
    'facilityFluff': ('facility',),
    'recipeFluff': ('recipe',),
    'crochetPatternFluff': ('crochetPattern',),
    'rewardFluff': ('reward',),
    'charoptionFluff': ('charoption',),
    'conditionFluff': ('condition', 'status'),
    'languageFluff': ('language',),
    'trapFluff': ('trap',),
    'hazardFluff': ('hazard',),
    'optionalfeatureFluff': ('optionalfeature',),
}

RAW_KIND_TO_TAGS: dict[str, tuple[str, ...]] = {
    'spell': ('spell',),
    'monster': ('creature', 'monster', 'creatureFluff'),
    'item': ('item',),
    'baseitem': ('item', 'baseitem'),
    'itemGroup': ('item', 'itemGroup'),
    'magicvariant': ('item', 'magicvariant'),
    'condition': ('condition',),
    'status': ('status', 'condition'),
    'action': ('action',),
    'skill': ('skill',),
    'feat': ('feat',),
    'race': ('race', 'species', 'raceFluff'),
    'subrace': ('race', 'subrace', 'species', 'raceFluff'),
    'background': ('background',),
    'class': ('class',),
    'subclass': ('subclass',),
    'classFeature': ('classFeature', 'classfeature'),
    'subclassFeature': ('subclassFeature', 'subclassfeature'),
    'optionalfeature': ('optfeature', 'optionalfeature'),
    'reward': ('reward',),
    'deity': ('deity',),
    'vehicle': ('vehicle',),
    'vehicleUpgrade': ('vehupgrade', 'vehicleUpgrade'),
    'object': ('object',),
    'facility': ('facility',),
    'trap': ('trap',),
    'hazard': ('hazard',),
    'disease': ('disease',),
    'psionic': ('psionic',),
    'deck': ('deck',),
    'card': ('card',),
    'recipe': ('recipe',),
    'crochetPattern': ('crochet', 'crochetPattern', 'crochetFluff'),
    'cult': ('cult',),
    'boon': ('boon',),
    'charoption': ('charoption',),
    'language': ('language',),
    'languageScript': ('language', 'languageScript'),
    'variantrule': ('variantrule', 'quickref'),
    'table': ('table',),
    'individual': ('table', 'individual'),
    'hoard': ('table', 'hoard'),
    'dragon': ('table', 'dragon'),
    'gems': ('table', 'gems'),
    'artObjects': ('table', 'artObjects'),
    'magicItems': ('table', 'magicItems'),
    'encounter': ('table', 'encounter'),
    'name': ('table', 'name'),
    'lifeClass': ('table', 'lifeClass'),
    'lifeBackground': ('table', 'lifeBackground'),
    'lifeTrinket': ('table', 'lifeTrinket'),
    'monsterfeatures': ('table', 'monsterfeatures'),
    'itemProperty': ('itemProperty', 'itemproperty'),
    'itemMastery': ('itemMastery', 'itemmastery'),
    'itemType': ('itemType', 'itemtype'),
    'itemEntry': ('itemEntry',),
    'legendaryGroup': ('legroup', 'legendaryGroup'),
    'sense': ('sense',),
    'book': ('book',),
    'adventure': ('adventure',),
    'citation': ('citation', 'cite'),
}

SIZE_ABVS: tuple[str, ...] = ('F', 'D', 'T', 'S', 'M', 'L', 'H', 'G', 'C', 'V')
SIZE_MULT: dict[str, int] = {'L': 2, 'H': 3, 'G': 4}

ABILITY_ABVS: tuple[str, ...] = ('str', 'dex', 'con', 'int', 'wis', 'cha')

SKILL_TO_ABILITY: dict[str, str] = {
    'acrobatics': 'dex',
    'animal handling': 'wis',
    'arcana': 'int',
    'athletics': 'str',
    'deception': 'cha',
    'history': 'int',
    'insight': 'wis',
    'intimidation': 'cha',
    'investigation': 'int',
    'medicine': 'wis',
    'nature': 'int',
    'perception': 'wis',
    'performance': 'cha',
    'persuasion': 'cha',
    'religion': 'int',
    'sleight of hand': 'dex',
    'stealth': 'dex',
    'survival': 'wis',
}

CR_TO_XP: dict[str, int] = {
    '0': 10, '1/8': 25, '1/4': 50, '1/2': 100,
    '1': 200, '2': 450, '3': 700, '4': 1100, '5': 1800,
    '6': 2300, '7': 2900, '8': 3900, '9': 5000, '10': 5900,
    '11': 7200, '12': 8400, '13': 10000, '14': 11500, '15': 13000,
    '16': 15000, '17': 18000, '18': 20000, '19': 22000, '20': 25000,
    '21': 33000, '22': 41000, '23': 50000, '24': 62000, '25': 75000,
    '26': 90000, '27': 105000, '28': 120000, '29': 135000, '30': 155000,
}

SPELL_USE_KEYS: frozenset[str] = frozenset({
    '1', '2', '3', '4', '5', '6', '7', '8', '9',
    '1e', '2e', '3e', '4e', '5e', '6e', '7e', '8e', '9e',
})

SPELL_FREQ_PROPS: tuple[str, ...] = (
    'recharge', 'legendary', 'charges', 'rest', 'restLong',
    'daily', 'weekly', 'monthly', 'yearly',
)

OFFICIAL_DEFAULT_SOURCES: dict[str, str] = {
    'monsterfeatures': 'DMG',
    'lifeTrinket': 'XGE',
}

_RE_VAR = re.compile(r'<\$([^$]+)\$>')
_RE_MUSTACHE = re.compile(r'\{\{([^}]+)\}\}')
_RE_HIT_TAG = re.compile(r'\{@hit ([-+]?\d+)\}')
_RE_DC_TAG = re.compile(r'\{@dc (\d+)(?:\|[^}]+)?\}')
_RE_ABIL_WORD = re.compile(r'\b(str|dex|con|int|wis|cha)\b', re.IGNORECASE)


@dataclass
class ResolvedCorpus:
    entries: list[dict[str, Any]]
    sources: dict[str, dict[str, Any]]
    packages: dict[str, dict[str, Any]]
    relations: list[dict[str, Any]]
    identity_groups: list[dict[str, Any]]
    lookup_index: dict[tuple[str, ...], list[str]]
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class _RawRecord:
    seq: int
    package_id: str
    relpath: str
    raw_kind: str
    raw: dict[str, Any]
    resolved: dict[str, Any] | None = None
    copy_status: str = 'ok'
    copy_warnings: list[str] = field(default_factory=list)
    copied_from_seq: int | None = None
    state: int = 0  # 0=unvisited, 1=visiting, 2=done
    merged_into_parent: bool = False


# ---------------------------------------------------------------------------
# Basic math / 5e helper utilities (safe, no eval)
# ---------------------------------------------------------------------------

def _norm(value: Any) -> str:
    if value is None:
        return ''
    return str(value).strip().lower()


def _cr_to_str(cr: Any) -> str:
    if isinstance(cr, dict):
        return str(cr.get('cr') or '')
    if cr is None:
        return ''
    return str(cr).strip()


def _cr_to_num(cr: Any) -> float:
    s = _cr_to_str(cr)
    if not s or s.lower() in {'unknown', 'varies', 'none', '—', '-'}:
        return 0.0
    if '/' in s:
        parts = s.split('/', 1)
        try:
            return float(parts[0]) / float(parts[1])
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _cr_to_pb(cr: Any) -> int:
    num = _cr_to_num(cr)
    if num <= 4:
        return 2
    return max(2, math.floor((num - 1) / 4) + 2)


def _cr_to_xp(cr: Any) -> int:
    if isinstance(cr, dict) and isinstance(cr.get('xp'), (int, float)):
        return int(cr['xp'])
    s = _cr_to_str(cr)
    return CR_TO_XP.get(s, 0)


def _ability_mod(score: Any) -> int:
    try:
        val = int(score)
    except (TypeError, ValueError):
        return 0
    return math.floor((val - 10) / 2)


def _safe_eval_math(expr: str) -> float:
    cleaned = re.sub(r'[^-+/*0-9.()\s]+', '', expr).strip()
    if not cleaned:
        return 0.0
    tree = ast.parse(cleaned, mode='eval')

    def _eval_node(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp):
            val = _eval_node(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +val
            if isinstance(node.op, ast.USub):
                return -val
        if isinstance(node, ast.BinOp):
            left = _eval_node(node.left)
            right = _eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right if right != 0 else 0.0
        raise ValueError(f'Unsupported math expression: {expr!r}')

    return _eval_node(tree)


def _get_nested(obj: Any, path: list[str] | None) -> Any:
    if not path:
        return obj
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _set_nested(obj: dict[str, Any], path: list[str], value: Any) -> None:
    if not path:
        return
    cur: Any = obj
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[path[-1]] = value


def _del_nested(obj: dict[str, Any], path: list[str]) -> None:
    if not path:
        return
    cur: Any = obj
    for key in path[:-1]:
        if not isinstance(cur, dict) or key not in cur:
            return
        cur = cur[key]
    if isinstance(cur, dict):
        cur.pop(path[-1], None)


def _walk_strings(obj: Any, fn) -> Any:
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk_strings(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: _walk_strings(v, fn) for k, v in obj.items()}
    return obj


def _split_by_tags(text: str) -> list[str]:
    """Split a 5etools string into non-tag text and balanced {@...} tag tokens."""
    if '{@' not in text:
        return [text]
    out: list[str] = []
    i = 0
    n = len(text)
    buf_start = 0
    while i < n:
        if text[i] == '{' and i + 1 < n and text[i + 1] == '@':
            if i > buf_start:
                out.append(text[buf_start:i])
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                j += 1
            out.append(text[i:j])
            i = j
            buf_start = i
        else:
            i += 1
    if buf_start < n:
        out.append(text[buf_start:])
    return out


def _js_flags_to_re(flags: str | None) -> int:
    result = 0
    if not flags:
        return result
    if 'i' in flags:
        result |= re.IGNORECASE
    if 'm' in flags:
        result |= re.MULTILINE
    if 's' in flags:
        result |= re.DOTALL
    return result


def _replace_string_handler(pattern: re.Pattern[str], with_str: str, tag_insensitive: bool, text: str) -> str:
    # Convert JS $1..$9 backreferences in replacement string to Python \g<1>
    py_repl = re.sub(r'\$(\d+)', r'\\g<\1>', str(with_str))
    py_repl = py_repl.replace('$&', r'\g<0>')
    if tag_insensitive:
        return pattern.sub(py_repl, text)
    parts = _split_by_tags(text)
    for idx, part in enumerate(parts):
        if part.startswith('{@'):
            continue
        parts[idx] = pattern.sub(py_repl, part)
    return ''.join(parts)


def _short_monster_name(ent: dict[str, Any], *, title_case: bool = False) -> str:
    sn = ent.get('shortName')
    if isinstance(sn, str) and sn.strip():
        return sn.strip()
    name = str(ent.get('name') or '')
    if sn is True:
        return name
    base = name.split(',')[0].strip() if ',' in name else name
    if title_case and base:
        return base[0].upper() + base[1:]
    return base


def _resolve_dynamic_variables(obj: Any, ent: dict[str, Any]) -> Any:
    def _sub(match: re.Match[str]) -> str:
        raw_var = match.group(1)
        if '__' in raw_var:
            mode, detail = raw_var.split('__', 1)
        else:
            mode, detail = raw_var, ''
        if mode == 'name':
            return str(ent.get('name') or '')
        if mode == 'short_name':
            return _short_monster_name(ent, title_case=False)
        if mode == 'title_short_name':
            return _short_monster_name(ent, title_case=True)
        if mode in {'dc', 'spell_dc'}:
            if detail in ABILITY_ABVS:
                return str(8 + _ability_mod(ent.get(detail)) + _cr_to_pb(ent.get('cr')))
            return match.group(0)
        if mode == 'to_hit':
            if detail in ABILITY_ABVS:
                total = _cr_to_pb(ent.get('cr')) + _ability_mod(ent.get(detail))
                return f'+{total}' if total >= 0 else str(total)
            return match.group(0)
        if mode == 'damage_mod':
            if detail in ABILITY_ABVS:
                total = _ability_mod(ent.get(detail))
                if total == 0:
                    return ''
                return f' + {total}' if total > 0 else f' - {abs(total)}'
            return match.group(0)
        if mode == 'damage_avg':
            sizes = ent.get('size') or ['M']
            sz = sizes[0] if isinstance(sizes, list) and sizes else (sizes if isinstance(sizes, str) else 'M')
            smult = SIZE_MULT.get(sz, 1)
            replaced = _RE_ABIL_WORD.sub(lambda m: str(_ability_mod(ent.get(m.group(1).lower()))), detail)
            replaced = re.sub(r'\bsize_mult\b', str(smult), replaced)
            try:
                return str(math.floor(_safe_eval_math(replaced)))
            except ValueError:
                return match.group(0)
        if mode == 'size_mult':
            sizes = ent.get('size') or ['M']
            sz = sizes[0] if isinstance(sizes, list) and sizes else (sizes if isinstance(sizes, str) else 'M')
            smult = SIZE_MULT.get(sz, 1)
            if not detail:
                return str(smult)
            try:
                return str(math.floor(_safe_eval_math(f'{smult} * ({detail})')))
            except ValueError:
                return match.group(0)
        return match.group(0)

    return _walk_strings(obj, lambda s: _RE_VAR.sub(_sub, s))


# ---------------------------------------------------------------------------
# _copy & _mod engine
# ---------------------------------------------------------------------------

def _ensure_list(val: Any) -> list[Any]:
    if isinstance(val, list):
        return val
    return [val]


def _item_matches_name(item: Any, target: Any) -> bool:
    if isinstance(item, dict):
        return item.get('name') == target or item.get('ENG_name') == target
    return item == target


def _apply_single_mod(
    copy_to: dict[str, Any],
    mod_info: Any,
    prop_path: list[str] | None,
    warnings: list[str],
    ctx_label: str,
) -> None:
    if isinstance(mod_info, str):
        if mod_info == 'remove':
            if prop_path:
                _del_nested(copy_to, prop_path)
            return
        warnings.append(f'{ctx_label}: unhandled string mod {mod_info!r}')
        return

    if not isinstance(mod_info, dict):
        warnings.append(f'{ctx_label}: invalid mod object {type(mod_info).__name__}')
        return

    mode = mod_info.get('mode')
    path_str = '.'.join(prop_path) if prop_path else '_'

    if mode == 'appendStr':
        existing = _get_nested(copy_to, prop_path)
        joiner = mod_info.get('joiner') or ''
        text = mod_info.get('str') or ''
        new_val = f'{existing}{joiner}{text}' if existing else text
        if prop_path:
            _set_nested(copy_to, prop_path, new_val)
        return

    if mode == 'replaceName':
        ents = _get_nested(copy_to, prop_path)
        if not isinstance(ents, list):
            return
        try:
            pattern = re.compile(str(mod_info.get('replace', '')), _js_flags_to_re(mod_info.get('flags')))
        except re.error as exc:
            warnings.append(f'{ctx_label}: invalid regex in replaceName on {path_str}: {exc}')
            return
        with_str = str(mod_info.get('with', ''))
        tag_insensitive = bool(mod_info.get('tagInsensitive'))
        for item in ents:
            if isinstance(item, dict) and isinstance(item.get('name'), str):
                item['name'] = _replace_string_handler(pattern, with_str, tag_insensitive, item['name'])
        return

    if mode == 'replaceTxt':
        ents = _get_nested(copy_to, prop_path)
        if ents is None:
            return
        try:
            pattern = re.compile(str(mod_info.get('replace', '')), _js_flags_to_re(mod_info.get('flags')))
        except re.error as exc:
            warnings.append(f'{ctx_label}: invalid regex in replaceTxt on {path_str}: {exc}')
            return
        with_str = str(mod_info.get('with', ''))
        tag_insensitive = bool(mod_info.get('tagInsensitive'))
        props = mod_info.get('props')
        if props is None:
            props = [None, 'entries', 'headerEntries', 'footerEntries']
        if not props:
            return
        repl_fn = lambda s: _replace_string_handler(pattern, with_str, tag_insensitive, s)
        if isinstance(ents, str):
            if prop_path:
                _set_nested(copy_to, prop_path, repl_fn(ents))
            return
        if isinstance(ents, list):
            if None in props and prop_path:
                ents = [repl_fn(it) if isinstance(it, str) else it for it in ents]
                _set_nested(copy_to, prop_path, ents)
            for item in ents:
                if not isinstance(item, dict):
                    continue
                for p in props:
                    if p is None:
                        continue
                    if item.get(p) is not None:
                        item[p] = _walk_strings(item[p], repl_fn)
        elif isinstance(ents, dict):
            for p in props:
                if p is None:
                    continue
                if ents.get(p) is not None:
                    ents[p] = _walk_strings(ents[p], repl_fn)
        return

    if mode == 'prependArr':
        items = copy.deepcopy(_ensure_list(mod_info.get('items', [])))
        existing = _get_nested(copy_to, prop_path)
        if prop_path:
            _set_nested(copy_to, prop_path, items + existing if isinstance(existing, list) else items)
        return

    if mode == 'appendArr':
        items = copy.deepcopy(_ensure_list(mod_info.get('items', [])))
        existing = _get_nested(copy_to, prop_path)
        if prop_path:
            _set_nested(copy_to, prop_path, existing + items if isinstance(existing, list) else items)
        return

    if mode == 'appendIfNotExistsArr':
        items = copy.deepcopy(_ensure_list(mod_info.get('items', [])))
        existing = _get_nested(copy_to, prop_path)
        if not isinstance(existing, list):
            if prop_path:
                _set_nested(copy_to, prop_path, items)
            return
        added = [it for it in items if not any(it == ex for ex in existing)]
        if prop_path:
            _set_nested(copy_to, prop_path, existing + added)
        return

    if mode in {'replaceArr', 'replaceOrAppendArr'}:
        items = copy.deepcopy(_ensure_list(mod_info.get('items', [])))
        existing = _get_nested(copy_to, prop_path)
        if not isinstance(existing, list):
            if mode == 'replaceOrAppendArr' and prop_path:
                _set_nested(copy_to, prop_path, items)
            else:
                warnings.append(f'{ctx_label}: {mode} could not find array {path_str}')
            return
        rep = mod_info.get('replace')
        ix_old = -1
        if isinstance(rep, dict) and 'regex' in rep:
            try:
                pat = re.compile(str(rep['regex']), _js_flags_to_re(rep.get('flags')))
            except re.error as exc:
                warnings.append(f'{ctx_label}: invalid regex in {mode} on {path_str}: {exc}')
                return
            for i, it in enumerate(existing):
                if isinstance(it, dict) and isinstance(it.get('name'), str) and pat.search(it['name']):
                    ix_old = i
                    break
                if isinstance(it, dict) and isinstance(it.get('ENG_name'), str) and pat.search(it['ENG_name']):
                    ix_old = i
                    break
                if isinstance(it, str) and pat.search(it):
                    ix_old = i
                    break
        elif isinstance(rep, dict) and rep.get('index') is not None:
            idx_val = int(rep['index'])
            if -len(existing) <= idx_val < len(existing):
                ix_old = idx_val if idx_val >= 0 else len(existing) + idx_val
        else:
            for i, it in enumerate(existing):
                if _item_matches_name(it, rep):
                    ix_old = i
                    break
        if ix_old >= 0:
            existing[ix_old:ix_old + 1] = items
        elif mode == 'replaceOrAppendArr':
            existing.extend(items)
        else:
            warnings.append(f'{ctx_label}: replaceArr could not find {rep!r} in {path_str}')
        return

    if mode == 'insertArr':
        items = copy.deepcopy(_ensure_list(mod_info.get('items', [])))
        existing = _get_nested(copy_to, prop_path)
        if not isinstance(existing, list):
            warnings.append(f'{ctx_label}: insertArr could not find array {path_str}')
            return
        idx = int(mod_info.get('index', -1))
        if idx == -1 or idx >= len(existing):
            existing.extend(items)
        else:
            pos = max(0, idx)
            existing[pos:pos] = items
        return

    if mode == 'removeArr':
        existing = _get_nested(copy_to, prop_path)
        if not isinstance(existing, list):
            if not mod_info.get('force'):
                warnings.append(f'{ctx_label}: removeArr could not find array {path_str}')
            return
        if 'names' in mod_info and mod_info['names'] is not None:
            for name_to_remove in _ensure_list(mod_info['names']):
                ix_old = next(
                    (i for i, it in enumerate(existing)
                     if isinstance(it, dict) and (it.get('name') == name_to_remove or it.get('ENG_name') == name_to_remove)),
                    -1,
                )
                if ix_old >= 0:
                    existing.pop(ix_old)
                elif not mod_info.get('force'):
                    warnings.append(f'{ctx_label}: removeArr could not find name {name_to_remove!r} in {path_str}')
        elif 'items' in mod_info and mod_info['items'] is not None:
            for item_to_remove in _ensure_list(mod_info['items']):
                ix_old = next((i for i, it in enumerate(existing) if it == item_to_remove), -1)
                if ix_old >= 0:
                    existing.pop(ix_old)
                elif not mod_info.get('force'):
                    warnings.append(f'{ctx_label}: removeArr could not find item {item_to_remove!r} in {path_str}')
        else:
            warnings.append(f'{ctx_label}: removeArr missing names/items on {path_str}')
        return

    if mode == 'renameArr':
        existing = _get_nested(copy_to, prop_path)
        if not isinstance(existing, list):
            warnings.append(f'{ctx_label}: renameArr could not find array {path_str}')
            return
        for ren in _ensure_list(mod_info.get('renames', [])):
            if not isinstance(ren, dict):
                continue
            target_name = ren.get('rename')
            found = next(
                (it for it in existing
                 if isinstance(it, dict) and (it.get('name') == target_name or it.get('ENG_name') == target_name)),
                None,
            )
            if found is not None:
                found['name'] = ren.get('with')
            else:
                warnings.append(f'{ctx_label}: renameArr could not find {target_name!r} in {path_str}')
        return

    if mode == 'setProp':
        combined = str(mod_info['prop']).split('.') if mod_info.get('prop') else []
        if prop_path and prop_path != ['*']:
            combined = list(prop_path) + combined
        if combined:
            _set_nested(copy_to, combined, copy.deepcopy(mod_info.get('value')))
        return

    if mode == 'prefixSuffixStringProp':
        combined = str(mod_info['prop']).split('.') if mod_info.get('prop') else []
        if prop_path and prop_path != ['*']:
            combined = list(prop_path) + combined
        val = _get_nested(copy_to, combined)
        if isinstance(val, str):
            _set_nested(copy_to, combined, f"{mod_info.get('prefix') or ''}{val}{mod_info.get('suffix') or ''}")
        return

    if mode == 'calculateProp':
        tgt = _get_nested(copy_to, prop_path)
        if not isinstance(tgt, dict):
            tgt = {}
            if prop_path:
                _set_nested(copy_to, prop_path, tgt)
        formula = str(mod_info.get('formula') or '')

        def _var_sub(m: re.Match[str]) -> str:
            var = m.group(1)
            if var == 'prof_bonus':
                return str(_cr_to_pb(copy_to.get('cr')))
            if var == 'dex_mod':
                return str(_ability_mod(copy_to.get('dex')))
            raise ValueError(f'Unknown formula variable {var!r}')

        try:
            expr = _RE_VAR.sub(_var_sub, formula)
            val = _safe_eval_math(expr)
            tgt[mod_info['prop']] = int(val) if val.is_integer() else val
        except Exception as exc:
            warnings.append(f'{ctx_label}: calculateProp failed on {path_str}: {exc}')
        return

    if mode in {'scalarAddProp', 'scalarMultProp'}:
        tgt = _get_nested(copy_to, prop_path)
        if not isinstance(tgt, dict):
            return
        scalar = mod_info.get('scalar', 0)
        do_floor = bool(mod_info.get('floor'))

        def _apply_scalar(k: str) -> None:
            raw_v = tgt.get(k)
            try:
                num = float(raw_v)
            except (TypeError, ValueError):
                return
            out = num + scalar if mode == 'scalarAddProp' else num * scalar
            if do_floor:
                out = math.floor(out)
            elif isinstance(out, float) and out.is_integer():
                out = int(out)
            if isinstance(raw_v, str):
                tgt[k] = f'+{out}' if out >= 0 else str(out)
            else:
                tgt[k] = out

        prop_key = mod_info.get('prop')
        if prop_key == '*':
            for k in list(tgt.keys()):
                _apply_scalar(k)
        elif prop_key in tgt:
            _apply_scalar(prop_key)
        return

    if mode == 'scalarAddHit':
        existing = _get_nested(copy_to, prop_path)
        if existing is None or not prop_path:
            return
        scalar = int(mod_info.get('scalar', 0))
        _set_nested(
            copy_to,
            prop_path,
            _walk_strings(existing, lambda s: _RE_HIT_TAG.sub(lambda m: f'{{@hit {int(m.group(1)) + scalar}}}', s)),
        )
        return

    if mode == 'scalarAddDc':
        existing = _get_nested(copy_to, prop_path)
        if existing is None or not prop_path:
            return
        scalar = int(mod_info.get('scalar', 0))
        _set_nested(
            copy_to,
            prop_path,
            _walk_strings(existing, lambda s: _RE_DC_TAG.sub(lambda m: f'{{@dc {int(m.group(1)) + scalar}}}', s)),
        )
        return

    if mode == 'addSenses':
        senses_to_add = _ensure_list(mod_info.get('senses', []))
        existing = copy_to.setdefault('senses', [])
        if not isinstance(existing, list):
            existing = copy_to['senses'] = []
        for sense in senses_to_add:
            if not isinstance(sense, dict):
                continue
            s_type = str(sense.get('type') or '')
            s_range = sense.get('range')
            if not s_type or s_range is None:
                continue
            pat = re.compile(rf'{re.escape(s_type)}\s+(\d+)', re.IGNORECASE)
            found = False
            for i, cur in enumerate(existing):
                if isinstance(cur, str):
                    m = pat.search(cur)
                    if m:
                        found = True
                        if int(m.group(1)) < int(s_range):
                            existing[i] = f'{s_type} {s_range} ft.'
                        break
            if not found:
                existing.append(f'{s_type} {s_range} ft.')
        return

    if mode in {'addSaves', 'addAllSaves'}:
        saves_map = (
            {ab: mod_info.get('saves', 1) for ab in ABILITY_ABVS}
            if mode == 'addAllSaves'
            else (mod_info.get('saves') or {})
        )
        save_dict = copy_to.setdefault('save', {})
        if not isinstance(save_dict, dict):
            save_dict = copy_to['save'] = {}
        pb = _cr_to_pb(copy_to.get('cr'))
        for ab, mult in saves_map.items():
            total = int(mult) * pb + _ability_mod(copy_to.get(ab))
            as_text = f'+{total}' if total >= 0 else str(total)
            if ab in save_dict:
                try:
                    if int(save_dict[ab]) < total:
                        save_dict[ab] = as_text
                except (TypeError, ValueError):
                    pass
            else:
                save_dict[ab] = as_text
        return

    if mode in {'addSkills', 'addAllSkills'}:
        skills_map = (
            {sk: mod_info.get('skills', 1) for sk in SKILL_TO_ABILITY}
            if mode == 'addAllSkills'
            else (mod_info.get('skills') or {})
        )
        skill_dict = copy_to.setdefault('skill', {})
        if not isinstance(skill_dict, dict):
            skill_dict = copy_to['skill'] = {}
        pb = _cr_to_pb(copy_to.get('cr'))
        for sk, mult in skills_map.items():
            ab = SKILL_TO_ABILITY.get(sk.lower())
            if not ab:
                continue
            total = int(mult) * pb + _ability_mod(copy_to.get(ab))
            as_text = f'+{total}' if total >= 0 else str(total)
            if sk in skill_dict:
                try:
                    if int(skill_dict[sk]) < total:
                        skill_dict[sk] = as_text
                except (TypeError, ValueError):
                    pass
            else:
                skill_dict[sk] = as_text
        return

    if mode == 'addSpells':
        sc_list = copy_to.get('spellcasting')
        if not isinstance(sc_list, list) or not sc_list:
            warnings.append(f'{ctx_label}: addSpells target missing spellcasting')
            return
        target_name = mod_info.get('name')
        sc = next((x for x in sc_list if isinstance(x, dict) and x.get('name') == target_name), None) if target_name else sc_list[0]
        if not isinstance(sc, dict):
            warnings.append(f'{ctx_label}: addSpells could not find spellcasting {target_name!r}')
            return
        if isinstance(mod_info.get('spells'), dict):
            spells = sc.setdefault('spells', {})
            for lvl, cat_new in mod_info['spells'].items():
                if lvl not in spells:
                    spells[lvl] = copy.deepcopy(cat_new)
                elif isinstance(cat_new, dict) and isinstance(spells[lvl], dict):
                    for kk, vv in cat_new.items():
                        if kk not in spells[lvl]:
                            spells[lvl][kk] = copy.deepcopy(vv)
                        elif isinstance(spells[lvl][kk], list) and isinstance(vv, list):
                            spells[lvl][kk] = sorted(spells[lvl][kk] + copy.deepcopy(vv), key=lambda x: str(x).lower())
                        else:
                            spells[lvl][kk] = copy.deepcopy(vv)
        for prop in ('constant', 'will', 'ritual'):
            if mod_info.get(prop):
                arr = sc.setdefault(prop, [])
                arr.extend(copy.deepcopy(_ensure_list(mod_info[prop])))
        for prop in SPELL_FREQ_PROPS:
            if isinstance(mod_info.get(prop), dict):
                bucket = sc.setdefault(prop, {})
                for k, sp_arr in mod_info[prop].items():
                    if k not in SPELL_USE_KEYS:
                        continue
                    bucket.setdefault(k, []).extend(copy.deepcopy(_ensure_list(sp_arr)))
        return

    if mode == 'replaceSpells':
        sc_list = copy_to.get('spellcasting')
        if not isinstance(sc_list, list) or not sc_list or not isinstance(sc_list[0], dict):
            warnings.append(f'{ctx_label}: replaceSpells target missing spellcasting')
            return
        sc = sc_list[0]

        def _do_replace_spell(container: dict[str, Any], rep_meta: dict[str, Any], key: str) -> None:
            arr = container.get(key)
            if not isinstance(arr, list):
                warnings.append(f'{ctx_label}: replaceSpells missing list {key!r}')
                return
            rep_target = rep_meta.get('replace')
            with_list = copy.deepcopy(_ensure_list(rep_meta.get('with', [])))
            try:
                ix = arr.index(rep_target)
            except ValueError:
                warnings.append(f'{ctx_label}: replaceSpells could not find spell {rep_target!r}')
                return
            arr[ix:ix + 1] = with_list
            arr.sort(key=lambda x: str(x).lower())

        if isinstance(mod_info.get('spells'), dict) and isinstance(sc.get('spells'), dict):
            for lvl, rep_metas in mod_info['spells'].items():
                if lvl in sc['spells'] and isinstance(sc['spells'][lvl], dict):
                    for rep_meta in _ensure_list(rep_metas):
                        if isinstance(rep_meta, dict):
                            _do_replace_spell(sc['spells'][lvl], rep_meta, 'spells')
        for prop in ('daily',):
            if isinstance(mod_info.get(prop), dict) and isinstance(sc.get(prop), dict):
                for k, rep_metas in mod_info[prop].items():
                    if k not in SPELL_USE_KEYS:
                        continue
                    for rep_meta in _ensure_list(rep_metas):
                        if isinstance(rep_meta, dict):
                            _do_replace_spell(sc[prop], rep_meta, k)
        return

    if mode == 'removeSpells':
        sc_list = copy_to.get('spellcasting')
        if not isinstance(sc_list, list) or not sc_list or not isinstance(sc_list[0], dict):
            warnings.append(f'{ctx_label}: removeSpells target missing spellcasting')
            return
        sc = sc_list[0]
        if isinstance(mod_info.get('spells'), dict) and isinstance(sc.get('spells'), dict):
            for lvl, to_rem in mod_info['spells'].items():
                rem_set = set(_ensure_list(to_rem))
                if isinstance(sc['spells'].get(lvl), dict) and isinstance(sc['spells'][lvl].get('spells'), list):
                    sc['spells'][lvl]['spells'] = [s for s in sc['spells'][lvl]['spells'] if s not in rem_set]
        for prop in ('constant', 'will', 'ritual'):
            if mod_info.get(prop) and isinstance(sc.get(prop), list):
                rem_set = set(_ensure_list(mod_info[prop]))
                sc[prop] = [s for s in sc[prop] if s not in rem_set]
        for prop in SPELL_FREQ_PROPS:
            if isinstance(mod_info.get(prop), dict) and isinstance(sc.get(prop), dict):
                for k, to_rem in mod_info[prop].items():
                    if k not in SPELL_USE_KEYS:
                        continue
                    rem_set = set(_ensure_list(to_rem))
                    if isinstance(sc[prop].get(k), list):
                        sc[prop][k] = [s for s in sc[prop][k] if s not in rem_set]
        return

    if mode == 'maxSize':
        sizes = copy_to.get('size')
        if not isinstance(sizes, list) or not sizes:
            return
        max_sz = mod_info.get('max')
        if max_sz not in SIZE_ABVS:
            warnings.append(f'{ctx_label}: maxSize unhandled max {max_sz!r}')
            return
        ix_max = SIZE_ABVS.index(max_sz)
        ixs = [SIZE_ABVS.index(s) for s in sizes if s in SIZE_ABVS]
        ixs.sort()
        nxt = [ix for ix in ixs if ix <= ix_max] or [ix_max]
        copy_to['size'] = [SIZE_ABVS[ix] for ix in nxt]
        return

    if mode == 'scalarMultXp':
        scalar = mod_info.get('scalar', 1)
        do_floor = bool(mod_info.get('floor'))
        cur_xp = _cr_to_xp(copy_to.get('cr'))
        out = cur_xp * scalar
        if do_floor:
            out = math.floor(out)
        elif isinstance(out, float) and out.is_integer():
            out = int(out)
        if not isinstance(copy_to.get('cr'), dict):
            copy_to['cr'] = {'cr': copy_to.get('cr')}
        copy_to['cr']['xp'] = out
        return

    warnings.append(f'{ctx_label}: unhandled _mod mode {mode!r} on {path_str}')


def _apply_copy(
    raw_kind: str,
    base_dict: dict[str, Any],
    target_raw: dict[str, Any],
    templates_by_kind: dict[str, list[dict[str, Any]]],
    *,
    ctx_label: str,
) -> tuple[dict[str, Any], list[str]]:
    """Apply 5etools _copy merge, _preserve, _templates, variable resolution, and _mod."""
    warnings: list[str] = []
    copy_from = copy.deepcopy(base_dict)
    copy_to = copy.deepcopy(target_raw)
    copy_meta = copy.deepcopy(copy_to.get('_copy') or {})
    if not isinstance(copy_meta, dict):
        copy_meta = {}

    # Normalise _mod entries to lists
    raw_mod = copy_meta.get('_mod')
    mods: dict[str, list[Any]] = {}
    if isinstance(raw_mod, dict):
        for k, v in raw_mod.items():
            mods[k] = list(v) if isinstance(v, list) else [v]

    # Collect & attach _templates
    templates_to_apply: list[dict[str, Any]] = []
    tpl_refs = copy_meta.get('_templates')
    if isinstance(tpl_refs, list) and tpl_refs:
        tpl_pool = templates_by_kind.get(raw_kind, [])
        for ref in tpl_refs:
            if not isinstance(ref, dict):
                continue
            t_name = _norm(ref.get('name'))
            t_en = _norm(ref.get('ENG_name'))
            t_src = _norm(ref.get('source'))
            matched = next(
                (t for t in tpl_pool
                 if _norm(t.get('source')) == t_src
                 and (
                     (t_name and _norm(t.get('name')) == t_name)
                     or (t_name and _norm(t.get('ENG_name')) == t_name)
                     or (t_en and _norm(t.get('ENG_name')) == t_en)
                     or (t_en and _norm(t.get('name')) == t_en)
                 )),
                None,
            )
            if matched is None:
                warnings.append(f'{ctx_label}: missing template {ref.get("name") or ref.get("ENG_name")!r}|{ref.get("source")!r}')
                continue
            tpl_copy = copy.deepcopy(matched)
            templates_to_apply.append(tpl_copy)
            tpl_mod = (tpl_copy.get('apply') or {}).get('_mod')
            if isinstance(tpl_mod, dict):
                for k, v in tpl_mod.items():
                    v_list = list(v) if isinstance(v, list) else [v]
                    mods.setdefault(k, []).extend(v_list)
        copy_to['_copy_templates'] = [
            {'name': r.get('name'), 'source': r.get('source')}
            for r in tpl_refs if isinstance(r, dict)
        ]

    orig_root_keys = set(copy_to.keys())
    preserve = copy_meta.get('_preserve')
    if not isinstance(preserve, dict):
        preserve = {}
    preserve_all = bool(preserve.get('*'))
    kind_preserve = MERGE_REQUIRES_PRESERVE_BY_KIND.get(raw_kind, frozenset())

    # Copy base keys into copy_to
    for k, v in copy_from.items():
        if k in copy_to and copy_to[k] is None:
            del copy_to[k]
            continue
        if k not in copy_to or copy_to[k] is None:
            if k in MERGE_REQUIRES_PRESERVE_BASE or k in kind_preserve:
                if preserve_all or preserve.get(k):
                    copy_to[k] = v
            else:
                copy_to[k] = v

    # Apply template _root properties for keys not originally on target root
    for tpl in templates_to_apply:
        tpl_root = (tpl.get('apply') or {}).get('_root')
        if isinstance(tpl_root, dict):
            for k, v in tpl_root.items():
                if k not in orig_root_keys:
                    copy_to[k] = copy.deepcopy(v)

    # Apply mods: regular properties first, then '_', then '*'
    if mods:
        resolved_mods = {k: _resolve_dynamic_variables(v, copy_to) for k, v in mods.items()}

        def _mod_order(key: str) -> tuple[int, str]:
            if key == '_':
                return (1, key)
            if key == '*':
                return (2, key)
            return (0, key)

        for prop in sorted(resolved_mods.keys(), key=_mod_order):
            mod_list = resolved_mods[prop]
            if prop == '*':
                for entry_prop in COPY_ENTRY_PROPS:
                    for mod_info in mod_list:
                        _apply_single_mod(copy_to, mod_info, [entry_prop], warnings, ctx_label)
            elif prop == '_':
                for mod_info in mod_list:
                    _apply_single_mod(copy_to, mod_info, None, warnings, ctx_label)
            else:
                prop_path = prop.split('.')
                for mod_info in mod_list:
                    _apply_single_mod(copy_to, mod_info, prop_path, warnings, ctx_label)

    copy_to['_isCopy'] = True
    copy_to.pop('_copy', None)
    return copy_to, warnings


# ---------------------------------------------------------------------------
# Provenance & Inventory Extraction Helpers
# ---------------------------------------------------------------------------

def _iter_official_docs(inventory: Inventory) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for item in getattr(inventory, 'official_docs', []) or []:
        if isinstance(item, dict):
            disp = item.get('disposition')
            if disp is not None and disp not in {'entity_data', 'fluff_data'}:
                continue
            rel = item.get('relpath') or item.get('path') or ''
            doc = item.get('doc') if 'doc' in item else item.get('data')
            if isinstance(doc, dict):
                out.append((str(rel), doc))
        elif isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], dict):
            out.append((str(item[0]), item[1]))
    return out


def _effective_source_code(raw_kind: str, item: dict[str, Any], default_source: str = '') -> str:
    src = item.get('source')
    if src:
        return str(src)
    if raw_kind == 'magicvariant' and isinstance(item.get('inherits'), dict):
        inh_src = item['inherits'].get('source')
        if inh_src:
            return str(inh_src)
    if raw_kind == 'subrace' and item.get('raceSource'):
        return str(item['raceSource'])
    cp = item.get('_copy')
    if isinstance(cp, dict) and cp.get('source'):
        return str(cp['source'])
    return default_source or OFFICIAL_DEFAULT_SOURCES.get(raw_kind, '')


def _record_lookup_names(raw_kind: str, item: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for k in ('name', 'ENG_name', 'abbreviation', 'shortName'):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            names.add(_norm(v))
    if raw_kind == 'subrace' and not item.get('name') and item.get('raceName'):
        names.add(_norm(item['raceName']))
    return names


def _build_sources_and_packages(
    inventory: Inventory,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[tuple[str, str], str], dict[str, Any]]:
    """Build five-layer provenance tables: packages, sources, and (package_id, code_lower) -> source_id map."""
    sources: dict[str, dict[str, Any]] = {}
    packages: dict[str, dict[str, Any]] = {}
    pkg_code_to_source_id: dict[tuple[str, str], str] = {}
    code_owners: dict[str, list[str]] = defaultdict(list)

    ratings = getattr(inventory, 'ratings', {}) or {}
    tiers = ratings.get('tiers') if isinstance(ratings.get('tiers'), dict) else {}

    # 1. Official package & official sources
    packages['official'] = {
        'id': 'official',
        'kind': 'official',
        'name': '官方与合作出版物',
        'origin': '官方',
        'package_kind': '官方基础',
        'ours': False,
        'edition': None,
        'ruleset': '待核',
        'groups': ['official'],
        'sha': None,
        'file': 'data/',
        'source_ids': [],
        'counts': {},
        'spell_schools': {},
        'authors': ['Wizards of the Coast'],
        'translators': [],
        'url': None,
        'version': None,
    }

    official_sources = getattr(inventory, 'official_sources', {}) or {}
    for code, meta in sorted(official_sources.items()):
        sid = f'src:{code}'
        is_partnered = bool(meta.get('is_partnered') or meta.get('partnered'))
        is_classic = meta.get('is_classic')
        if is_classic is False:
            ruleset = '5r'
            edition = 'one'
        elif is_classic is True:
            ruleset = '5e'
            edition = 'classic'
        else:
            ruleset = '待核'
            edition = None
        sources[sid] = {
            'id': sid,
            'code': code,
            'package_id': 'official',
            'name': str(meta.get('full') or meta.get('name') or code),
            'short_label': str(meta.get('abv') or meta.get('abbreviation') or code),
            'origin': '合作' if is_partnered else '官方',
            'authors': ['Wizards of the Coast'] if not is_partnered else [],
            'translators': [],
            'ours': False,
            'tier': tiers.get(code),
            'edition': edition,
            'ruleset': ruleset,
            'license_note': None,
            'date': meta.get('date'),
            'is_classic': is_classic,
            'is_partnered': is_partnered,
            'is_non_standard': bool(meta.get('is_non_standard') or meta.get('non_standard')),
            'is_legacy': bool(meta.get('is_legacy') or meta.get('legacy')),
        }
        packages['official']['source_ids'].append(sid)
        pkg_code_to_source_id[('official', code.lower())] = sid
        code_owners[code.lower()].append('official')

    # 2. First pass over brew / site packages to detect code collisions
    raw_packages = list(getattr(inventory, 'packages', []) or [])
    package_docs = getattr(inventory, 'package_docs', {}) or {}

    def _pkg_id(p: dict[str, Any]) -> str:
        pid = p.get('package_id') or p.get('id')
        if pid and (str(pid).startswith('brew:') or str(pid).startswith('site:') or pid == 'official'):
            return str(pid)
        kind = p.get('kind') or 'brew'
        return f'{kind}:{pid}'

    for p in raw_packages:
        pid = _pkg_id(p)
        doc = package_docs.get(pid) or package_docs.get(p.get('id', '')) or {}
        meta = doc.get('_meta') if isinstance(doc, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        seen_codes: set[str] = set()
        for src_list in (p.get('sources') or [], meta.get('sources') or []):
            for s in src_list:
                if isinstance(s, dict) and s.get('json'):
                    c = str(s['json']).lower()
                    if c not in seen_codes:
                        seen_codes.add(c)
                        code_owners[c].append(pid)

    colliding_codes = {c for c, owners in code_owners.items() if len(set(owners)) > 1}

    # 3. Populate brew / site packages and sources
    for p in raw_packages:
        pid = _pkg_id(p)
        doc = package_docs.get(pid) or package_docs.get(p.get('id', '')) or {}
        meta = doc.get('_meta') if isinstance(doc, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        is_site = pid.startswith('site:')
        ours = bool(p.get('ours')) or is_site
        edition = p.get('edition') or meta.get('edition')
        if edition == 'one':
            pkg_ruleset = '5r'
        elif edition == 'classic':
            pkg_ruleset = '5e'
        else:
            pkg_ruleset = '待核'
        origin = '本站精选' if ours else '社区'
        package_kind = '本站精选' if ours else '社区扩展'

        # Merge source dicts from package record and _meta.sources
        src_by_code: dict[str, dict[str, Any]] = {}
        for s in (meta.get('sources') or []):
            if isinstance(s, dict) and s.get('json'):
                src_by_code[str(s['json'])] = dict(s)
        for s in (p.get('sources') or []):
            if isinstance(s, dict) and s.get('json'):
                c = str(s['json'])
                merged = dict(src_by_code.get(c, {}))
                for k, v in s.items():
                    if v is not None:
                        merged[k] = v
                src_by_code[c] = merged

        meta_translators = meta.get('translators')
        if isinstance(meta_translators, str):
            meta_translators = [meta_translators]
        elif not isinstance(meta_translators, list):
            meta_translators = []

        pkg_source_ids: list[str] = []
        pkg_authors: list[str] = []
        pkg_translators: list[str] = list(dict.fromkeys(str(t) for t in meta_translators if t))
        pkg_url: str | None = None
        pkg_version: str | None = None

        sha8 = sha256(pid.encode('utf-8')).hexdigest()[:8]
        for code, s in src_by_code.items():
            sid = f'src:{code}@{sha8}' if code.lower() in colliding_codes else f'src:{code}'
            s_authors = s.get('authors') or []
            if isinstance(s_authors, str):
                s_authors = [s_authors]
            s_authors = [str(a) for a in s_authors if a]
            s_trans = s.get('translator') or s.get('translators') or []
            if isinstance(s_trans, str):
                s_trans = [s_trans]
            elif not isinstance(s_trans, list):
                s_trans = []
            s_trans = [str(t) for t in s_trans if t]
            if not s_trans and pkg_translators:
                s_trans = list(pkg_translators)
            for a in s_authors:
                if a not in pkg_authors:
                    pkg_authors.append(a)
            for t in s_trans:
                if t not in pkg_translators:
                    pkg_translators.append(t)
            if not pkg_url and s.get('url'):
                pkg_url = str(s['url'])
            if not pkg_version and s.get('version'):
                pkg_version = str(s['version'])
            s_edition = s.get('edition') or edition
            s_ruleset = '5r' if s_edition == 'one' else ('5e' if s_edition == 'classic' else pkg_ruleset)
            sources[sid] = {
                'id': sid,
                'code': code,
                'package_id': pid,
                'name': str(s.get('full') or s.get('name') or code),
                'short_label': str(s.get('abbreviation') or s.get('abv') or code),
                'origin': origin,
                'authors': s_authors,
                'translators': s_trans,
                'ours': ours,
                'tier': tiers.get(code),
                'edition': s_edition,
                'ruleset': s_ruleset,
                'license_note': str(s.get('url') or '') or None,
                'date': None,
                'is_classic': True if s_edition == 'classic' else (False if s_edition == 'one' else None),
                'is_partnered': False,
                'is_non_standard': False,
                'is_legacy': False,
            }
            pkg_source_ids.append(sid)
            pkg_code_to_source_id[(pid, code.lower())] = sid

        raw_name = p.get('title') or p.get('name') or p.get('id') or pid
        if src_by_code:
            first_src = next(iter(src_by_code.values()))
            display_name = str(first_src.get('full') or raw_name)
        else:
            display_name = str(raw_name)

        packages[pid] = {
            'id': pid,
            'kind': 'site' if is_site else 'brew',
            'name': display_name,
            'origin': origin,
            'package_kind': package_kind,
            'ours': ours,
            'edition': edition,
            'ruleset': pkg_ruleset,
            'groups': list(p.get('groups') or []),
            'sha': p.get('sha'),
            'file': p.get('file') or p.get('relpath'),
            'source_ids': pkg_source_ids,
            'counts': dict(p.get('actual_counts') or p.get('expected_counts') or p.get('counts') or {}),
            'spell_schools': dict(meta.get('spellSchools') or {}) if isinstance(meta.get('spellSchools'), dict) else {},
            'meta_lookups': {
                k: dict(meta[k])
                for k in (
                    'spellSchools', 'optionalFeatureTypes', 'featCategories',
                    'psionicTypes', 'vehicleUpgradeTypes', 'charOption', 'itemTypes',
                )
                if isinstance(meta.get(k), dict)
            },
            'authors': pkg_authors,
            'translators': pkg_translators,
            'url': pkg_url,
            'version': pkg_version,
        }

    prov_diag = {
        'colliding_source_codes': sorted(colliding_codes),
    }
    return sources, packages, pkg_code_to_source_id, prov_diag


def _resolve_source_id(
    package_id: str,
    source_code: str,
    pkg_code_to_source_id: dict[tuple[str, str], str],
    sources: dict[str, dict[str, Any]],
    packages: dict[str, dict[str, Any]],
    official_casefold: dict[str, str],
) -> tuple[str, str]:
    """Return (source_id, canonical_source_code) for a record, registering fallback source if needed."""
    code = (source_code or '').strip()
    if not code:
        pkg = packages.get(package_id)
        if pkg and pkg.get('source_ids'):
            sid = pkg['source_ids'][0]
            return sid, sources[sid]['code']
        return 'src:__unknown__', ''

    low = code.lower()
    if (package_id, low) in pkg_code_to_source_id:
        sid = pkg_code_to_source_id[(package_id, low)]
        return sid, sources[sid]['code']

    # Official source referenced from official or brew
    if low in official_casefold:
        canon = official_casefold[low]
        sid = pkg_code_to_source_id.get(('official', canon.lower()))
        if sid:
            return sid, canon

    # If a brew references another brew's source code that is uniquely registered
    if package_id != 'official':
        direct_sid = f'src:{code}'
        if direct_sid in sources:
            return direct_sid, sources[direct_sid]['code']

    # Synthesize undeclared source scoped to package if needed
    sha8 = sha256(package_id.encode('utf-8')).hexdigest()[:8]
    sid = f'src:{code}' if f'src:{code}' not in sources else f'src:{code}@{sha8}'
    pkg = packages.get(package_id, {})
    sources[sid] = {
        'id': sid,
        'code': code,
        'package_id': package_id,
        'name': code,
        'short_label': code,
        'origin': pkg.get('origin', '社区' if package_id != 'official' else '官方'),
        'authors': list(pkg.get('authors') or []),
        'translators': list(pkg.get('translators') or []),
        'ours': bool(pkg.get('ours')),
        'tier': None,
        'edition': pkg.get('edition'),
        'ruleset': pkg.get('ruleset', '待核'),
        'license_note': pkg.get('url'),
        'date': None,
        'is_classic': None,
        'is_partnered': False,
        'is_non_standard': False,
        'is_legacy': False,
    }
    pkg_code_to_source_id[(package_id, low)] = sid
    if package_id in packages and sid not in packages[package_id]['source_ids']:
        packages[package_id]['source_ids'].append(sid)
    return sid, code


def _compute_ruleset(item: dict[str, Any], source_rec: dict[str, Any] | None, package_rec: dict[str, Any] | None) -> str:
    ed = item.get('edition')
    if ed == 'one':
        return '5r'
    if ed == 'classic':
        return '5e'
    if isinstance(item.get('inherits'), dict):
        inh_ed = item['inherits'].get('edition')
        if inh_ed == 'one':
            return '5r'
        if inh_ed == 'classic':
            return '5e'
    if source_rec:
        if source_rec.get('edition') == 'one' or source_rec.get('is_classic') is False:
            return '5r'
        if source_rec.get('edition') == 'classic' or source_rec.get('is_classic') is True:
            return '5e'
        if source_rec.get('ruleset') in {'5r', '5e'}:
            return str(source_rec['ruleset'])
    if package_rec:
        if package_rec.get('edition') == 'one':
            return '5r'
        if package_rec.get('edition') == 'classic':
            return '5e'
        if package_rec.get('ruleset') in {'5r', '5e'}:
            return str(package_rec['ruleset'])
    return '待核'


def _make_entry_id(
    package_id: str,
    raw_kind: str,
    source_code: str,
    owner_class: str,
    owner_subclass: str,
    owner_level: int | None,
    name: str,
    name_en: str,
    dup_index: int,
) -> str:
    payload = canonical((
        package_id,
        raw_kind,
        source_code,
        owner_class,
        owner_subclass,
        owner_level,
        name,
        name_en,
        dup_index,
    ))
    return 'o5e_' + sha256(payload.encode('utf-8')).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Pipe ref & Spell class helpers
# ---------------------------------------------------------------------------

def _parse_class_feature_ref(ref: Any, default_class: str, default_class_src: str) -> dict[str, Any] | None:
    raw_str = ref.get('classFeature') if isinstance(ref, dict) else (ref if isinstance(ref, str) else None)
    if not raw_str:
        return None
    parts = raw_str.split('|')
    name = parts[0].strip() if len(parts) > 0 else ''
    cls_name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else default_class
    cls_src = parts[2].strip() if len(parts) > 2 and parts[2].strip() else default_class_src
    lvl_str = parts[3].strip() if len(parts) > 3 else ''
    feat_src = parts[4].strip() if len(parts) > 4 and parts[4].strip() else cls_src
    try:
        level = int(lvl_str) if lvl_str else None
    except ValueError:
        level = None
    return {
        'name': name,
        'className': cls_name,
        'classSource': cls_src,
        'level': level,
        'source': feat_src,
        'gainSubclassFeature': bool(ref.get('gainSubclassFeature')) if isinstance(ref, dict) else False,
    }


def _parse_subclass_feature_ref(
    ref: Any,
    default_class: str,
    default_class_src: str,
    default_sub_short: str,
    default_sub_src: str,
) -> dict[str, Any] | None:
    raw_str = ref.get('subclassFeature') if isinstance(ref, dict) else (ref if isinstance(ref, str) else None)
    if not raw_str:
        return None
    parts = raw_str.split('|')
    name = parts[0].strip() if len(parts) > 0 else ''
    cls_name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else default_class
    cls_src = parts[2].strip() if len(parts) > 2 and parts[2].strip() else default_class_src
    sub_short = parts[3].strip() if len(parts) > 3 and parts[3].strip() else default_sub_short
    sub_src = parts[4].strip() if len(parts) > 4 and parts[4].strip() else default_sub_src
    lvl_str = parts[5].strip() if len(parts) > 5 else ''
    feat_src = parts[6].strip() if len(parts) > 6 and parts[6].strip() else sub_src
    try:
        level = int(lvl_str) if lvl_str else None
    except ValueError:
        level = None
    return {
        'name': name,
        'className': cls_name,
        'classSource': cls_src,
        'subclassShortName': sub_short,
        'subclassSource': sub_src,
        'level': level,
        'source': feat_src,
    }


def _collect_additional_spell_refs(obj: Any) -> list[str]:
    """Extract all spell UID strings from a 5etools additionalSpells structure."""
    refs: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            # Ignore filter/choose metadata strings like 'level=1|class=Wizard'
            if '=' in node:
                return
            refs.append(node.split('#', 1)[0].strip())
        elif isinstance(node, list):
            for x in node:
                _walk(x)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k in {'ability', 'name', 'resourceName', 'choose', 'count', 'all'}:
                    continue
                _walk(v)

    _walk(obj)
    return refs


def _build_spell_class_index(
    inventory: Inventory,
    resolved_records: list[_RawRecord],
) -> tuple[dict[tuple[str, str], list[str]], dict[tuple[str, str], list[str]]]:
    """Build (source_lower, spell_name_lower) -> ordered classes & subclasses from official lookups + brew."""
    spell_classes: dict[tuple[str, str], list[str]] = defaultdict(list)
    spell_subclasses: dict[tuple[str, str], list[str]] = defaultdict(list)

    def _add_cls(src_low: str, sp_low: str, cls_name: str) -> None:
        if not cls_name:
            return
        lst = spell_classes[(src_low, sp_low)]
        if cls_name not in lst:
            lst.append(cls_name)

    def _add_sub(src_low: str, sp_low: str, sub_label: str) -> None:
        if not sub_label:
            return
        lst = spell_subclasses[(src_low, sp_low)]
        if sub_label not in lst:
            lst.append(sub_label)

    lookups = getattr(inventory, 'generated_lookups', {}) or {}

    # 1. gendata-spell-source-lookup.json
    gen_lookup = lookups.get('data/generated/gendata-spell-source-lookup.json') or {}
    if isinstance(gen_lookup, dict):
        for src_code, sp_map in gen_lookup.items():
            if not isinstance(sp_map, dict):
                continue
            src_low = _norm(src_code)
            for sp_name, info in sp_map.items():
                if not isinstance(info, dict):
                    continue
                sp_low = _norm(sp_name)
                for cls_key in ('class', 'classVariant'):
                    cls_by_src = info.get(cls_key)
                    if isinstance(cls_by_src, dict):
                        for _csrc, cmap in cls_by_src.items():
                            if isinstance(cmap, dict):
                                for cname in cmap.keys():
                                    _add_cls(src_low, sp_low, str(cname))
                sub_by_src = info.get('subclass')
                if isinstance(sub_by_src, dict):
                    for _csrc, cmap in sub_by_src.items():
                        if not isinstance(cmap, dict):
                            continue
                        for cname, sc_src_map in cmap.items():
                            if not isinstance(sc_src_map, dict):
                                continue
                            for _scsrc, sc_map in sc_src_map.items():
                                if not isinstance(sc_map, dict):
                                    continue
                                for sc_short, sc_meta in sc_map.items():
                                    sc_name = sc_meta.get('name') if isinstance(sc_meta, dict) else sc_short
                                    _add_sub(src_low, sp_low, f'{cname}（{sc_name or sc_short}）')

    # 2. data/spells/sources.json
    src_lookup = lookups.get('data/spells/sources.json') or {}
    if isinstance(src_lookup, dict):
        for src_code, sp_map in src_lookup.items():
            if not isinstance(sp_map, dict):
                continue
            src_low = _norm(src_code)
            for sp_name, info in sp_map.items():
                if not isinstance(info, dict):
                    continue
                sp_low = _norm(sp_name)
                for cls_key in ('class', 'classVariant'):
                    for entry in info.get(cls_key) or []:
                        if isinstance(entry, dict) and entry.get('name'):
                            _add_cls(src_low, sp_low, str(entry['name']))
                for sc_entry in info.get('subclass') or []:
                    if isinstance(sc_entry, dict):
                        cname = (sc_entry.get('class') or {}).get('name') or ''
                        sc_meta = sc_entry.get('subclass') or {}
                        sname = sc_meta.get('name') or sc_meta.get('shortName') or ''
                        if cname and sname:
                            _add_sub(src_low, sp_low, f'{cname}（{sname}）')

    # 3. Inline spell.classes + brew class/subclass additionalSpells
    for rec in resolved_records:
        item = rec.resolved
        if not isinstance(item, dict):
            continue
        if rec.raw_kind == 'spell' and isinstance(item.get('classes'), dict):
            src_low = _norm(_effective_source_code('spell', item))
            names_low = [n for n in (_norm(item.get('name')), _norm(item.get('ENG_name'))) if n]
            cls_obj = item['classes']
            for key in ('fromClassList', 'fromClassListVariant'):
                for c_item in cls_obj.get(key) or []:
                    if isinstance(c_item, dict) and (c_item.get('name') or c_item.get('ENG_name')):
                        cname = str(c_item.get('name') or c_item.get('ENG_name'))
                        for n in names_low:
                            _add_cls(src_low, n, cname)
            for sc_item in cls_obj.get('fromSubclass') or []:
                if isinstance(sc_item, dict):
                    cname = (sc_item.get('class') or {}).get('name') or ''
                    sc_meta = sc_item.get('subclass') or {}
                    sname = sc_meta.get('name') or sc_meta.get('shortName') or ''
                    if cname and sname:
                        for n in names_low:
                            _add_sub(src_low, n, f'{cname}（{sname}）')

        if rec.raw_kind in {'class', 'subclass'} and item.get('additionalSpells'):
            owner_cls = str(item.get('name') if rec.raw_kind == 'class' else (item.get('className') or ''))
            owner_sub = str(item.get('shortName') or item.get('name') or '') if rec.raw_kind == 'subclass' else ''
            for uid in _collect_additional_spell_refs(item['additionalSpells']):
                if '|' in uid:
                    sp_name, sp_src = uid.split('|', 1)
                    src_low = _norm(sp_src)
                else:
                    sp_name, src_low = uid, ''
                sp_low = _norm(sp_name)
                if not sp_low:
                    continue
                if rec.raw_kind == 'class' and owner_cls:
                    _add_cls(src_low, sp_low, owner_cls)
                elif rec.raw_kind == 'subclass' and owner_cls and owner_sub:
                    _add_sub(src_low, sp_low, f'{owner_cls}（{owner_sub}）')

    return spell_classes, spell_subclasses


# ---------------------------------------------------------------------------
# Main resolution pipeline
# ---------------------------------------------------------------------------

def resolve_corpus(inventory: Inventory, *, collection: str = COLLECTION) -> ResolvedCorpus:
    """Resolve the full our5etools corpus from an Inventory into normalized entries, provenance, relations, and lookup index."""
    from anna.our5etools.types import STACKABLE_TYPES, classify_record, extract_fields
    collection_key = (collection or COLLECTION).strip() or COLLECTION

    sources, packages, pkg_code_to_source_id, prov_diag = _build_sources_and_packages(inventory)
    official_casefold: dict[str, str] = dict(getattr(inventory, 'official_source_casefold', {}) or {})
    media_files: dict[str, Any] = getattr(inventory, 'media_files', {}) or {}
    lookups = getattr(inventory, 'generated_lookups', {}) or {}

    # Load templates from data/bestiary/template.json
    template_doc = lookups.get('data/bestiary/template.json') or {}
    templates_by_kind: dict[str, list[dict[str, Any]]] = {
        'monster': list(template_doc.get('monsterTemplate') or []),
        'legendaryGroup': list(template_doc.get('legendaryGroupTemplate') or []),
    }

    diagnostics: dict[str, Any] = {
        'colliding_source_codes': prov_diag['colliding_source_codes'],
        'copy_summary': defaultdict(int),
        'copy_missing_base': [],
        'copy_ambiguous_base': [],
        'copy_cycles': [],
        'copy_mod_warnings': [],
        'version_count': 0,
        'version_warnings': [],
        'orphan_fluff': [],
        'orphan_subraces': [],
        'missing_local_audio': [],
        'unresolved_reprints': [],
        'duplicate_identity_keys': [],
        'skipped_non_entity_props': defaultdict(int),
        'skipped_non_dict_items': defaultdict(int),
    }

    # -----------------------------------------------------------------------
    # Stage 1: Collect all raw entity records, *Fluff records, and auxiliary merges
    # -----------------------------------------------------------------------
    raw_records: list[_RawRecord] = []
    fluff_records: list[_RawRecord] = []
    item_type_additions: list[dict[str, Any]] = []
    seq_counter = 0

    def _collect_doc(package_id: str, relpath: str, doc: dict[str, Any]) -> None:
        nonlocal seq_counter
        for prop, val in doc.items():
            if prop.startswith('_'):
                continue
            if prop.startswith('foundry') or prop in NON_ENTITY_PROPS:
                if prop == 'itemTypeAdditionalEntries' and isinstance(val, list):
                    for it in val:
                        if isinstance(it, dict):
                            item_type_additions.append(it)
                else:
                    diagnostics['skipped_non_entity_props'][prop] += len(val) if isinstance(val, list) else 1
                continue
            if not isinstance(val, list):
                continue

            if prop == 'lifeTrinket' and val and all(isinstance(x, str) for x in val):
                synth = {
                    'name': '饰品表（人生经历）',
                    'ENG_name': 'Trinkets (This Is Your Life)',
                    'source': 'XGE',
                    'items': list(val),
                }
                raw_records.append(_RawRecord(seq=seq_counter, package_id=package_id, relpath=relpath, raw_kind=prop, raw=synth))
                seq_counter += 1
                continue

            is_fluff = prop.endswith('Fluff')
            target_list = fluff_records if is_fluff else raw_records
            for item in val:
                if not isinstance(item, dict):
                    diagnostics['skipped_non_dict_items'][prop] += 1
                    continue
                target_list.append(_RawRecord(seq=seq_counter, package_id=package_id, relpath=relpath, raw_kind=prop, raw=item))
                seq_counter += 1

    # Official documents
    for relpath, doc in _iter_official_docs(inventory):
        _collect_doc('official', relpath, doc)

    # Ensure legendarygroups.json is loaded if not already present in official_docs
    has_official_lg = any(r.package_id == 'official' and r.raw_kind == 'legendaryGroup' for r in raw_records)
    if not has_official_lg:
        lg_doc = lookups.get('data/bestiary/legendarygroups.json')
        if isinstance(lg_doc, dict):
            _collect_doc('official', 'data/bestiary/legendarygroups.json', lg_doc)

    # Brew & site packages
    package_docs = getattr(inventory, 'package_docs', {}) or {}
    for p in list(getattr(inventory, 'packages', []) or []):
        pid = p.get('package_id') or p.get('id')
        if pid and not (str(pid).startswith('brew:') or str(pid).startswith('site:') or pid == 'official'):
            pid = f"{p.get('kind', 'brew')}:{pid}"
        pid = str(pid)
        doc = package_docs.get(pid) or package_docs.get(p.get('id', ''))
        if isinstance(doc, dict):
            _collect_doc(pid, str(p.get('file') or p.get('relpath') or pid), doc)

    # -----------------------------------------------------------------------
    # Stage 2: Full _copy resolution (for both primary entities and *Fluff)
    # -----------------------------------------------------------------------
    def _resolve_copy_pool(records: list[_RawRecord]) -> None:
        # Index by (pool_key, name_lower, source_lower)
        by_pool_name_src: dict[tuple[str, str, str], list[_RawRecord]] = defaultdict(list)
        by_pool_name: dict[tuple[str, str], list[_RawRecord]] = defaultdict(list)

        for rec in records:
            pool = COPY_POOL_GROUP.get(rec.raw_kind, rec.raw_kind)
            src = _norm(_effective_source_code(rec.raw_kind, rec.raw))
            names = _record_lookup_names(rec.raw_kind, rec.raw)
            if not names and isinstance(rec.raw.get('_copy'), dict):
                names = _record_lookup_names(rec.raw_kind, rec.raw['_copy'])
            for n in names:
                by_pool_name_src[(pool, n, src)].append(rec)
                by_pool_name[(pool, n)].append(rec)

        def _narrow_candidates(rec: _RawRecord, copy_ref: dict[str, Any], cands: list[_RawRecord]) -> list[_RawRecord]:
            filtered = [c for c in cands if c.seq != rec.seq]
            if not filtered:
                return []

            rk = rec.raw_kind
            if rk in {'subclass', 'subclassFluff'}:
                c_cls = _norm(copy_ref.get('className'))
                c_csrc = _norm(copy_ref.get('classSource'))
                c_short = _norm(copy_ref.get('shortName'))
                if c_cls:
                    sub = [c for c in filtered if _norm(c.raw.get('className')) == c_cls]
                    if sub:
                        filtered = sub
                if c_csrc:
                    sub = [c for c in filtered if _norm(c.raw.get('classSource')) == c_csrc]
                    if sub:
                        filtered = sub
                if c_short:
                    sub = [c for c in filtered if _norm(c.raw.get('shortName')) == c_short]
                    if sub:
                        filtered = sub
            elif rk == 'subclassFeature':
                for fkey in ('className', 'classSource', 'subclassShortName', 'subclassSource'):
                    val = _norm(copy_ref.get(fkey))
                    if val:
                        sub = [c for c in filtered if _norm(c.raw.get(fkey)) == val]
                        if sub:
                            filtered = sub
                if copy_ref.get('level') is not None:
                    sub = [c for c in filtered if c.raw.get('level') == copy_ref.get('level')]
                    if sub:
                        filtered = sub
            elif rk == 'subrace':
                r_name = _norm(copy_ref.get('raceName'))
                r_src = _norm(copy_ref.get('raceSource'))
                if r_name:
                    sub = [c for c in filtered if _norm(c.raw.get('raceName')) == r_name]
                    if sub:
                        filtered = sub
                if r_src:
                    sub = [c for c in filtered if _norm(c.raw.get('raceSource')) == r_src]
                    if sub:
                        filtered = sub
            elif rk == 'deity':
                pantheon = _norm(copy_ref.get('pantheon'))
                if pantheon:
                    sub = [c for c in filtered if _norm(c.raw.get('pantheon')) == pantheon]
                    if sub:
                        filtered = sub

            # Prefer exact raw_kind when using a shared pool
            if len(filtered) > 1:
                same_kind = [c for c in filtered if c.raw_kind == rk]
                if same_kind:
                    filtered = same_kind

            # Prefer same package, then official
            if len(filtered) > 1:
                same_pkg = [c for c in filtered if c.package_id == rec.package_id]
                if same_pkg:
                    filtered = same_pkg
                else:
                    official_cands = [c for c in filtered if c.package_id == 'official']
                    if official_cands:
                        filtered = official_cands

            return filtered

        def _find_copy_base(rec: _RawRecord, copy_ref: dict[str, Any]) -> tuple[_RawRecord | None, str]:
            pool = COPY_POOL_GROUP.get(rec.raw_kind, rec.raw_kind)
            c_src = _norm(copy_ref.get('source'))
            lookup_keys = [
                _norm(copy_ref.get(k))
                for k in ('name', 'ENG_name', 'abbreviation', 'shortName')
                if isinstance(copy_ref.get(k), str) and copy_ref.get(k).strip()
            ]
            cands: list[_RawRecord] = []
            seen_seq: set[int] = set()
            for lk in lookup_keys:
                hits = by_pool_name_src.get((pool, lk, c_src), []) if c_src else by_pool_name.get((pool, lk), [])
                for h in hits:
                    if h.seq not in seen_seq:
                        seen_seq.add(h.seq)
                        cands.append(h)

            # Fallback: try source casefold alias if official source
            if not cands and c_src and c_src in official_casefold:
                alt_src = official_casefold[c_src].lower()
                for lk in lookup_keys:
                    for h in by_pool_name_src.get((pool, lk, alt_src), []):
                        if h.seq not in seen_seq:
                            seen_seq.add(h.seq)
                            cands.append(h)

            cands = _narrow_candidates(rec, copy_ref, cands)
            if not cands:
                return None, 'missing_base'
            if len(cands) == 1:
                return cands[0], 'ok'
            # Check whether all remaining candidates are structurally identical duplicates
            first_canon = canonical(cands[0].raw)
            if all(canonical(c.raw) == first_canon for c in cands[1:]):
                return cands[0], 'ok'
            return cands[0], 'ambiguous_base'

        def _preserve_fallback_fields(rec: _RawRecord, copy_ref: dict[str, Any]) -> dict[str, Any]:
            out = copy.deepcopy(rec.raw)
            if not out.get('name'):
                fallback_name = copy_ref.get('name') or copy_ref.get('ENG_name') or copy_ref.get('abbreviation')
                if fallback_name:
                    out['name'] = fallback_name
            if not out.get('ENG_name') and copy_ref.get('ENG_name'):
                out['ENG_name'] = copy_ref['ENG_name']
            if not out.get('shortName') and copy_ref.get('shortName'):
                out['shortName'] = copy_ref['shortName']
            out.pop('_copy', None)
            return out

        def _dfs(rec: _RawRecord, stack: list[str]) -> dict[str, Any]:
            if rec.state == 2 and rec.resolved is not None:
                return rec.resolved
            if rec.state == 1:
                rec.copy_status = 'cycle'
                rec.copy_warnings.append(f'Cycle detected: {" -> ".join(stack)}')
                diagnostics['copy_cycles'].append({
                    'package_id': rec.package_id,
                    'raw_kind': rec.raw_kind,
                    'name': rec.raw.get('name') or rec.raw.get('ENG_name'),
                    'source': _effective_source_code(rec.raw_kind, rec.raw),
                    'chain': list(stack),
                })
                cp = rec.raw.get('_copy') if isinstance(rec.raw.get('_copy'), dict) else {}
                return _preserve_fallback_fields(rec, cp)

            copy_ref = rec.raw.get('_copy')
            if not isinstance(copy_ref, dict):
                rec.resolved = copy.deepcopy(rec.raw)
                rec.state = 2
                return rec.resolved

            rec.state = 1
            label = f"{rec.raw_kind}:{rec.raw.get('name') or copy_ref.get('name') or '?'}|{rec.raw.get('source') or '?'}"
            stack.append(label)

            base_rec, lookup_status = _find_copy_base(rec, copy_ref)
            if base_rec is None:
                rec.copy_status = 'missing_base'
                msg = f"Missing _copy base {copy_ref.get('name') or copy_ref.get('ENG_name') or copy_ref.get('abbreviation')!r}|{copy_ref.get('source')!r}"
                rec.copy_warnings.append(msg)
                diagnostics['copy_missing_base'].append({
                    'package_id': rec.package_id,
                    'raw_kind': rec.raw_kind,
                    'name': rec.raw.get('name') or copy_ref.get('name') or copy_ref.get('ENG_name'),
                    'source': _effective_source_code(rec.raw_kind, rec.raw),
                    'copy_ref': {k: v for k, v in copy_ref.items() if not k.startswith('_')},
                })
                rec.resolved = _preserve_fallback_fields(rec, copy_ref)
                rec.state = 2
                stack.pop()
                return rec.resolved

            if base_rec.state == 1:
                rec.copy_status = 'cycle'
                msg = f'Cycle detected via {label} -> {base_rec.raw.get("name")}|{base_rec.raw.get("source")}'
                rec.copy_warnings.append(msg)
                diagnostics['copy_cycles'].append({
                    'package_id': rec.package_id,
                    'raw_kind': rec.raw_kind,
                    'name': rec.raw.get('name') or copy_ref.get('name'),
                    'source': _effective_source_code(rec.raw_kind, rec.raw),
                    'chain': list(stack),
                })
                rec.resolved = _preserve_fallback_fields(rec, copy_ref)
                rec.state = 2
                stack.pop()
                return rec.resolved

            base_resolved = _dfs(base_rec, stack)
            rec.copied_from_seq = base_rec.seq

            merged, mod_warnings = _apply_copy(
                rec.raw_kind,
                base_resolved,
                rec.raw,
                templates_by_kind,
                ctx_label=f'{rec.package_id}/{label}',
            )

            # Propagate base copy failure if base itself was broken
            if base_rec.copy_status in {'missing_base', 'cycle'}:
                rec.copy_status = base_rec.copy_status
                rec.copy_warnings.append(f'Inherited from base with status {base_rec.copy_status}')
            elif lookup_status == 'ambiguous_base':
                rec.copy_status = 'ambiguous_base'
                rec.copy_warnings.append(f"Ambiguous _copy base for {copy_ref.get('name') or copy_ref.get('ENG_name')!r}|{copy_ref.get('source')!r}")
                diagnostics['copy_ambiguous_base'].append({
                    'package_id': rec.package_id,
                    'raw_kind': rec.raw_kind,
                    'name': merged.get('name'),
                    'source': _effective_source_code(rec.raw_kind, merged),
                    'copy_ref': {k: v for k, v in copy_ref.items() if not k.startswith('_')},
                })

            if mod_warnings:
                rec.copy_warnings.extend(mod_warnings)
                if rec.copy_status == 'ok':
                    rec.copy_status = 'mod_warning'
                diagnostics['copy_mod_warnings'].extend(mod_warnings)

            # Ensure fallback name if still missing
            if not merged.get('name'):
                fallback_name = copy_ref.get('name') or copy_ref.get('ENG_name') or copy_ref.get('abbreviation')
                if fallback_name:
                    merged['name'] = fallback_name

            rec.resolved = merged
            rec.state = 2
            stack.pop()
            return rec.resolved

        for rec in records:
            if rec.state != 2:
                _dfs(rec, [])
            if isinstance(rec.raw.get('_copy'), dict):
                diagnostics['copy_summary'][rec.copy_status] += 1

    # Resolve *Fluff first, then primary entities
    _resolve_copy_pool(fluff_records)
    _resolve_copy_pool(raw_records)

    # -----------------------------------------------------------------------
    # Stage 3: Pre-version inheritances (itemTypeAdditionalEntries, magicvariant inherits, nameless subrace merge)
    # -----------------------------------------------------------------------
    # 3a. Merge itemTypeAdditionalEntries onto matching itemType
    if item_type_additions:
        item_type_index: dict[tuple[str, str], _RawRecord] = {}
        for rec in raw_records:
            if rec.raw_kind == 'itemType' and rec.resolved:
                abbr = _norm(rec.resolved.get('abbreviation'))
                src = _norm(rec.resolved.get('source'))
                if abbr:
                    item_type_index[(abbr, src)] = rec
        for add in item_type_additions:
            applies = str(add.get('appliesTo') or '')
            if '|' in applies:
                abbr_s, src_s = applies.split('|', 1)
            else:
                abbr_s, src_s = applies, 'PHB'
            target_rec = item_type_index.get((_norm(abbr_s), _norm(src_s)))
            if target_rec and target_rec.resolved and isinstance(add.get('entries'), list):
                target_rec.resolved.setdefault('entries', []).extend(copy.deepcopy(add['entries']))

    # 3b. Merge magicvariant inherits onto effective dict (preserve requires/excludes)
    for rec in raw_records:
        if rec.raw_kind == 'magicvariant' and isinstance(rec.resolved, dict):
            inherits = rec.resolved.get('inherits')
            if isinstance(inherits, dict):
                eff = copy.deepcopy(inherits)
                # Preserve top-level magicvariant identity & filter metadata
                for k, v in rec.resolved.items():
                    if k == 'inherits':
                        eff['inherits'] = copy.deepcopy(v)
                    elif v is not None:
                        eff[k] = copy.deepcopy(v)
                if not eff.get('source') and inherits.get('source'):
                    eff['source'] = inherits['source']
                rec.resolved = eff

    # 3c. Index parent races and handle nameless base subraces BEFORE _versions expansion
    race_index_by_pkg: dict[tuple[str, str, str], _RawRecord] = {}
    race_index_global: dict[tuple[str, str], _RawRecord] = {}
    for rec in raw_records:
        if rec.raw_kind == 'race' and isinstance(rec.resolved, dict):
            rsrc = _norm(_effective_source_code('race', rec.resolved))
            for n in (_norm(rec.resolved.get('name')), _norm(rec.resolved.get('ENG_name'))):
                if n:
                    race_index_by_pkg[(rec.package_id, n, rsrc)] = rec
                    if (n, rsrc) not in race_index_global or rec.package_id == 'official':
                        race_index_global[(n, rsrc)] = rec

    def _find_parent_race(rec: _RawRecord) -> _RawRecord | None:
        item = rec.resolved or {}
        rname = _norm(item.get('raceName'))
        rsrc = _norm(item.get('raceSource') or item.get('source'))
        if not rname:
            return None
        return (
            race_index_by_pkg.get((rec.package_id, rname, rsrc))
            or race_index_global.get((rname, rsrc))
        )

    subrace_parent_map: dict[int, _RawRecord] = {}
    for rec in raw_records:
        if rec.raw_kind != 'subrace' or not isinstance(rec.resolved, dict):
            continue
        parent = _find_parent_race(rec)
        if not rec.resolved.get('name'):
            # Nameless base subrace -> merge onto parent race
            if parent and isinstance(parent.resolved, dict):
                for k, v in rec.resolved.items():
                    if k in {'raceName', 'raceSource', 'source', 'page'} and parent.resolved.get(k) is not None:
                        continue
                    if k == '_versions' and isinstance(v, list):
                        parent.resolved.setdefault('_versions', []).extend(copy.deepcopy(v))
                    elif k == 'entries' and isinstance(v, list):
                        parent.resolved.setdefault('entries', []).extend(copy.deepcopy(v))
                    elif v is not None:
                        parent.resolved[k] = copy.deepcopy(v)
                rec.merged_into_parent = True
            else:
                # Orphan nameless subrace -> keep with fallback raceName
                if rec.resolved.get('raceName'):
                    rec.resolved['name'] = rec.resolved['raceName']
                diagnostics['orphan_subraces'].append({
                    'package_id': rec.package_id,
                    'raceName': rec.resolved.get('raceName'),
                    'raceSource': rec.resolved.get('raceSource'),
                    'source': rec.resolved.get('source'),
                })
        else:
            # Named subrace -> inherit missing traits from parent race
            if parent and isinstance(parent.resolved, dict):
                subrace_parent_map[rec.seq] = parent
                overwrite = rec.resolved.get('overwrite') if isinstance(rec.resolved.get('overwrite'), dict) else {}
                for inherit_key in ('size', 'speed', 'creatureTypes', 'darkvision', 'languageProficiencies', 'traitTags'):
                    if inherit_key not in rec.resolved and parent.resolved.get(inherit_key) is not None:
                        rec.resolved[inherit_key] = copy.deepcopy(parent.resolved[inherit_key])
                if not overwrite.get('ability') and 'ability' not in rec.resolved and parent.resolved.get('ability') is not None:
                    rec.resolved['ability'] = copy.deepcopy(parent.resolved['ability'])
                # Store combined display name metadata while keeping clean race/subrace info
                p_name = parent.resolved.get('name') or rec.resolved.get('raceName') or ''
                p_en = parent.resolved.get('ENG_name') or ''
                sr_name = str(rec.resolved.get('name') or '')
                sr_en = str(rec.resolved.get('ENG_name') or '')
                rec.resolved['_subraceShortName'] = sr_name
                if p_name and sr_name and p_name not in sr_name:
                    rec.resolved['_combinedName'] = f'{p_name} ({sr_name})'
                if p_en and sr_en and p_en.lower() not in sr_en.lower():
                    rec.resolved['_combinedENGName'] = f'{p_en} ({sr_en})'
            elif rec.resolved.get('raceName'):
                diagnostics['orphan_subraces'].append({
                    'package_id': rec.package_id,
                    'name': rec.resolved.get('name'),
                    'raceName': rec.resolved.get('raceName'),
                    'raceSource': rec.resolved.get('raceSource'),
                    'source': rec.resolved.get('source'),
                })

    active_records: list[_RawRecord] = [r for r in raw_records if not r.merged_into_parent]

    # -----------------------------------------------------------------------
    # Stage 4: _versions expansion (basic overrides, _abstract+_implementations, monster variant._version)
    # -----------------------------------------------------------------------
    expanded_versions: list[tuple[_RawRecord, _RawRecord]] = []  # (version_rec, parent_rec)

    def _expand_version_spec(parent_rec: _RawRecord, ver_spec: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(ver_spec.get('_abstract'), dict) and isinstance(ver_spec.get('_implementations'), list):
            out_specs: list[dict[str, Any]] = []
            for impl in ver_spec['_implementations']:
                if not isinstance(impl, dict):
                    continue
                tpl = copy.deepcopy(ver_spec['_abstract'])
                impl_copy = copy.deepcopy(impl)
                variables = impl_copy.pop('_variables', None)
                if isinstance(variables, dict):
                    tpl = _walk_strings(
                        tpl,
                        lambda s: _RE_MUSTACHE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), s),
                    )
                for k, v in impl_copy.items():
                    tpl[k] = v
                out_specs.append(tpl)
            return out_specs
        return [copy.deepcopy(ver_spec)]

    def _monster_variant_versions(mon: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        variants = mon.get('variant')
        if not isinstance(variants, list):
            return out
        for var in variants:
            if not isinstance(var, dict) or not isinstance(var.get('_version'), dict):
                continue
            vmeta = var['_version']
            add_as = vmeta.get('addAs')
            add_headers_as = vmeta.get('addHeadersAs')
            if not add_as and not add_headers_as:
                continue
            spec: dict[str, Any] = {
                'name': vmeta.get('name') or var.get('name'),
                'source': vmeta.get('source') or var.get('source') or mon.get('source'),
                'variant': None,
            }
            if vmeta.get('ENG_name') or var.get('ENG_name'):
                spec['ENG_name'] = vmeta.get('ENG_name') or var.get('ENG_name')
            if add_as:
                item_copy = copy.deepcopy(var)
                for k in ('_version', 'type', 'source', 'page'):
                    item_copy.pop(k, None)
                spec['_mod'] = {add_as: {'mode': 'appendArr', 'items': item_copy}}
                out.append(spec)
            elif add_headers_as:
                ents = [
                    {k: v for k, v in copy.deepcopy(e).items() if k not in {'type', 'source'}}
                    for e in (var.get('entries') or [])
                    if isinstance(e, dict) and e.get('name') and e.get('entries')
                ]
                spec['_mod'] = {add_headers_as: {'mode': 'appendArr', 'items': ents}}
                out.append(spec)
        return out

    for rec in active_records:
        parent_dict = rec.resolved
        if not isinstance(parent_dict, dict):
            continue
        ver_specs: list[dict[str, Any]] = []
        raw_versions = parent_dict.get('_versions')
        if isinstance(raw_versions, list):
            for v in raw_versions:
                if isinstance(v, dict):
                    ver_specs.extend(_expand_version_spec(rec, v))
        if rec.raw_kind == 'monster':
            ver_specs.extend(_monster_variant_versions(parent_dict))

        if not ver_specs:
            continue

        base_for_ver = copy.deepcopy(parent_dict)
        base_for_ver.pop('_versions', None)
        for drop_k in ('hasToken', 'hasFluff', 'hasFluffImages'):
            base_for_ver.pop(drop_k, None)

        for vspec in ver_specs:
            # Convert version spec into _copy structure with default _preserve={'*': True}
            v_target = copy.deepcopy(vspec)
            v_mod = v_target.pop('_mod', None)
            v_tpls = v_target.pop('_templates', None)
            v_preserve = v_target.pop('_preserve', None) or {'*': True}
            v_target['_copy'] = {
                '_mod': v_mod,
                '_templates': v_tpls,
                '_preserve': v_preserve,
            }
            v_base = copy.deepcopy(base_for_ver)
            v_src = v_target.get('source')
            if v_src:
                for src_prop in ('additionalSources', 'otherSources', 'referenceSources'):
                    if isinstance(v_base.get(src_prop), list):
                        v_base[src_prop] = [
                            s for s in v_base[src_prop]
                            if not (isinstance(s, dict) and s.get('source') == v_src)
                        ]
                        if not v_base[src_prop]:
                            v_base.pop(src_prop, None)

            ctx = f"{rec.package_id}/{rec.raw_kind}:ver:{v_target.get('name') or '?'}"
            merged_ver, ver_warnings = _apply_copy(
                rec.raw_kind,
                v_base,
                v_target,
                templates_by_kind,
                ctx_label=ctx,
            )
            merged_ver['_versionBase_isVersion'] = True
            merged_ver['_versionBase_name'] = parent_dict.get('name')
            merged_ver['_versionBase_ENG_name'] = parent_dict.get('ENG_name')
            merged_ver['_versionBase_source'] = _effective_source_code(rec.raw_kind, parent_dict)

            vrec = _RawRecord(
                seq=seq_counter,
                package_id=rec.package_id,
                relpath=rec.relpath,
                raw_kind=rec.raw_kind,
                raw=vspec,
                resolved=merged_ver,
                copy_status='mod_warning' if ver_warnings else 'ok',
                copy_warnings=ver_warnings,
                state=2,
            )
            seq_counter += 1
            if ver_warnings:
                diagnostics['version_warnings'].extend(ver_warnings)
            diagnostics['version_count'] += 1
            expanded_versions.append((vrec, rec))

    all_records: list[_RawRecord] = active_records + [vrec for vrec, _ in expanded_versions]
    version_parent_seq: dict[int, int] = {vrec.seq: prec.seq for vrec, prec in expanded_versions}

    # -----------------------------------------------------------------------
    # Stage 5: Domain-specific cross-entity indices (spell classes, legendaryGroup, decks, fluff)
    # -----------------------------------------------------------------------
    spell_classes_idx, spell_subclasses_idx = _build_spell_class_index(inventory, all_records)

    # Index legendaryGroups by (name_lower, source_lower)
    lg_index: dict[tuple[str, str], _RawRecord] = {}
    for rec in all_records:
        if rec.raw_kind == 'legendaryGroup' and isinstance(rec.resolved, dict):
            src_low = _norm(_effective_source_code('legendaryGroup', rec.resolved))
            for n in (_norm(rec.resolved.get('name')), _norm(rec.resolved.get('ENG_name'))):
                if n:
                    if (n, src_low) not in lg_index or rec.package_id == 'official':
                        lg_index[(n, src_low)] = rec

    # Count cards per deck (set_lower, source_lower)
    deck_card_counts: dict[tuple[str, str], int] = defaultdict(int)
    for rec in all_records:
        if rec.raw_kind == 'card' and isinstance(rec.resolved, dict):
            dset = _norm(rec.resolved.get('set'))
            dsrc = _norm(_effective_source_code('card', rec.resolved))
            if dset:
                deck_card_counts[(dset, dsrc)] += 1

    # Build fluff attachment lookup
    # Map (primary_kind, key_tuple) -> list[fluff_rec]
    # Standard key: (name_lower, source_lower)
    # Subclass key: (name_or_short_lower, source_lower, class_lower, class_src_lower)
    fluff_used: set[int] = set()
    fluff_by_kind_name_src: dict[tuple[str, str, str], list[_RawRecord]] = defaultdict(list)
    fluff_subclass_map: dict[tuple[str, str, str, str], list[_RawRecord]] = defaultdict(list)

    for frec in fluff_records:
        fdict = frec.resolved
        if not isinstance(fdict, dict):
            continue
        pkinds = FLUFF_TO_PRIMARY_KINDS.get(frec.raw_kind)
        if not pkinds:
            continue
        fsrc = _norm(_effective_source_code(frec.raw_kind, fdict))
        fnames = [n for n in (_norm(fdict.get('name')), _norm(fdict.get('ENG_name')), _norm(fdict.get('shortName'))) if n]
        if frec.raw_kind == 'subclassFluff':
            fcls = _norm(fdict.get('className'))
            fcsrc = _norm(fdict.get('classSource'))
            for fn in fnames:
                fluff_subclass_map[(fn, fsrc, fcls, fcsrc)].append(frec)
                fluff_by_kind_name_src[('subclass', fn, fsrc)].append(frec)
        else:
            for pk in pkinds:
                for fn in fnames:
                    fluff_by_kind_name_src[(pk, fn, fsrc)].append(frec)

    def _find_fluff_for_record(rec: _RawRecord) -> dict[str, Any] | None:
        item = rec.resolved or {}
        src_low = _norm(_effective_source_code(rec.raw_kind, item))
        cands: list[_RawRecord] = []
        if rec.raw_kind == 'subclass':
            cls_low = _norm(item.get('className'))
            csrc_low = _norm(item.get('classSource'))
            for n in (_norm(item.get('name')), _norm(item.get('shortName')), _norm(item.get('ENG_name'))):
                if not n:
                    continue
                hits = fluff_subclass_map.get((n, src_low, cls_low, csrc_low))
                if hits:
                    cands.extend(hits)
                    break
            if not cands:
                for n in (_norm(item.get('name')), _norm(item.get('shortName')), _norm(item.get('ENG_name'))):
                    if n and ('subclass', n, src_low) in fluff_by_kind_name_src:
                        cands.extend(fluff_by_kind_name_src[('subclass', n, src_low)])
                        break
        else:
            probe_names = [
                _norm(item.get('_combinedName')),
                _norm(item.get('_combinedENGName')),
                _norm(item.get('name')),
                _norm(item.get('ENG_name')),
            ]
            if rec.raw_kind == 'race':
                if item.get('name'):
                    probe_names.append(_norm(f"{item['name']} (本相)"))
                if item.get('ENG_name'):
                    probe_names.append(_norm(f"{item['ENG_name']} (Base)"))
            for n in probe_names:
                if n and (rec.raw_kind, n, src_low) in fluff_by_kind_name_src:
                    cands.extend(fluff_by_kind_name_src[(rec.raw_kind, n, src_low)])
                    break

        if not cands:
            return None
        # Prefer same-package fluff
        same_pkg = [c for c in cands if c.package_id == rec.package_id]
        chosen = same_pkg[0] if same_pkg else cands[0]
        fluff_used.add(chosen.seq)
        fdict = chosen.resolved or {}
        entries_val = fdict.get('entries')
        images_val = fdict.get('images')
        if not entries_val and not images_val:
            return None
        return {
            'entries': copy.deepcopy(entries_val) if isinstance(entries_val, list) else [],
            'images': copy.deepcopy(images_val) if isinstance(images_val, list) else [],
            'source': _effective_source_code(chosen.raw_kind, fdict),
            'copy_status': chosen.copy_status,
        }

    # -----------------------------------------------------------------------
    # Stage 6: Materialize entries with deterministic IDs & extract_fields
    # -----------------------------------------------------------------------
    entries: list[dict[str, Any]] = []
    seq_to_entry_id: dict[int, str] = {}
    entry_by_id: dict[str, dict[str, Any]] = {}
    dup_counter: dict[tuple[Any, ...], int] = defaultdict(int)

    for rec in all_records:
        item = rec.resolved
        if not isinstance(item, dict):
            continue

        raw_kind = rec.raw_kind
        package_id = rec.package_id
        pkg_rec = packages.get(package_id, {})

        raw_src = _effective_source_code(raw_kind, item)
        source_id, source_code = _resolve_source_id(
            package_id, raw_src, pkg_code_to_source_id, sources, packages, official_casefold,
        )
        source_rec = sources.get(source_id, {})
        ruleset = _compute_ruleset(item, source_rec, pkg_rec)

        # Owner fields for class/subclass features
        owner_class = ''
        owner_subclass = ''
        owner_level: int | None = None
        if raw_kind == 'subclass':
            owner_class = str(item.get('className') or '')
            owner_subclass = str(item.get('shortName') or item.get('name') or '')
        elif raw_kind == 'classFeature':
            owner_class = str(item.get('className') or '')
            if item.get('level') is not None:
                try:
                    owner_level = int(item['level'])
                except (TypeError, ValueError):
                    owner_level = None
        elif raw_kind == 'subclassFeature':
            owner_class = str(item.get('className') or '')
            owner_subclass = str(item.get('subclassShortName') or '')
            if item.get('level') is not None:
                try:
                    owner_level = int(item['level'])
                except (TypeError, ValueError):
                    owner_level = None

        # Display name & English name
        if raw_kind == 'subrace' and item.get('_combinedName'):
            name = str(item['_combinedName'])
            name_en = str(item.get('_combinedENGName') or item.get('ENG_name') or '')
        else:
            name = str(item.get('name') or item.get('ENG_name') or item.get('abbreviation') or '')
            name_en = str(item.get('ENG_name') or '')

        base_id_key = (package_id, raw_kind, source_code, owner_class, owner_subclass, owner_level, name, name_en)
        dup_idx = dup_counter[base_id_key]
        dup_counter[base_id_key] += 1
        if dup_idx > 0:
            diagnostics['duplicate_identity_keys'].append({
                'package_id': package_id,
                'raw_kind': raw_kind,
                'source_code': source_code,
                'name': name,
                'name_en': name_en,
                'dup_index': dup_idx,
            })

        entry_id = _make_entry_id(
            package_id, raw_kind, source_code, owner_class, owner_subclass, owner_level, name, name_en, dup_idx,
        )
        seq_to_entry_id[rec.seq] = entry_id

        # Build resolved_context for types.extract_fields
        meta_lookups = pkg_rec.get('meta_lookups') or {}
        resolved_context: dict[str, Any] = {
            **meta_lookups,
            'package_id': package_id,
            'package_kind': pkg_rec.get('package_kind') or ('合作出版' if source_rec.get('is_partnered') else ('官方基础' if package_id == 'official' else '社区扩展')),
            'origin': source_rec.get('origin') or pkg_rec.get('origin') or '社区',
            'tier': source_rec.get('tier'),
            'ours': bool(source_rec.get('ours') or pkg_rec.get('ours')),
            'spell_schools': pkg_rec.get('spell_schools') or {},
            'owner_class': owner_class,
            'owner_subclass': owner_subclass,
            'owner_level': owner_level,
        }
        if raw_kind in {'book', 'adventure'}:
            if package_id.startswith('site:') or '漫画' in str(item.get('name') or ''):
                resolved_context['book_kind'] = '漫画'
            elif raw_kind == 'adventure':
                resolved_context['book_kind'] = '模组'
            else:
                resolved_context['book_kind'] = '规则书'
        if raw_kind == 'card' and item.get('set'):
            resolved_context['deck_set'] = str(item['set'])
        if package_id == 'official' and source_rec.get('is_partnered'):
            resolved_context['package_kind'] = '合作出版'

        # Spell classes enrichment
        spell_classes_val: list[str] = []
        spell_subclasses_val: list[str] = []
        if raw_kind == 'spell':
            src_low = _norm(source_code)
            for sp_n in (_norm(item.get('name')), _norm(item.get('ENG_name'))):
                if not sp_n:
                    continue
                for c in spell_classes_idx.get((src_low, sp_n), []):
                    if c not in spell_classes_val:
                        spell_classes_val.append(c)
                for sc in spell_subclasses_idx.get((src_low, sp_n), []):
                    if sc not in spell_subclasses_val:
                        spell_subclasses_val.append(sc)
                # Also merge unscoped brew additionalSpells targeting spell name
                for c in spell_classes_idx.get(('', sp_n), []):
                    if c not in spell_classes_val:
                        spell_classes_val.append(c)
                for sc in spell_subclasses_idx.get(('', sp_n), []):
                    if sc not in spell_subclasses_val:
                        spell_subclasses_val.append(sc)
            resolved_context['spell_classes'] = spell_classes_val
            resolved_context['spell_subclasses'] = spell_subclasses_val

        # Monster legendaryGroup & soundClip audio verification
        lg_rec: _RawRecord | None = None
        lg_data: dict[str, Any] | None = None
        has_local_audio: bool | None = None
        local_audio_path: str | None = None
        if raw_kind == 'monster':
            lg_ref = item.get('legendaryGroup')
            if isinstance(lg_ref, dict):
                lg_src = _norm(lg_ref.get('source') or source_code)
                for lg_n in (_norm(lg_ref.get('name')), _norm(lg_ref.get('ENG_name'))):
                    if lg_n and (lg_n, lg_src) in lg_index:
                        lg_rec = lg_index[(lg_n, lg_src)]
                        break
                if lg_rec and isinstance(lg_rec.resolved, dict):
                    lg_data = {
                        'name': lg_rec.resolved.get('name'),
                        'ENG_name': lg_rec.resolved.get('ENG_name'),
                        'source': _effective_source_code('legendaryGroup', lg_rec.resolved),
                        'lairActions': copy.deepcopy(lg_rec.resolved.get('lairActions')),
                        'regionalEffects': copy.deepcopy(lg_rec.resolved.get('regionalEffects')),
                        'mythicEncounter': copy.deepcopy(lg_rec.resolved.get('mythicEncounter')),
                    }
                    resolved_context['lair_actions'] = bool(lg_data.get('lairActions'))
                    resolved_context['has_lair_actions'] = bool(lg_data.get('lairActions'))
                    resolved_context['legendary_group'] = lg_data

            sc = item.get('soundClip')
            if isinstance(sc, dict) and sc.get('type') == 'internal' and sc.get('path'):
                raw_path = str(sc['path']).lstrip('/')
                candidates = [raw_path, f'audio/{raw_path}'] if not raw_path.startswith('audio/') else [raw_path]
                # 5etools data may reference .mp3 while our5etools stores transcoded .opus locally
                expanded_candidates = list(candidates)
                for cand in candidates:
                    if '.' in cand:
                        stem = cand.rsplit('.', 1)[0]
                        for ext in ('.opus', '.mp3', '.ogg', '.wav'):
                            alt = f'{stem}{ext}'
                            if alt not in expanded_candidates:
                                expanded_candidates.append(alt)
                matched_audio = next((c for c in expanded_candidates if c in media_files), None)
                if matched_audio:
                    has_local_audio = True
                    local_audio_path = matched_audio
                else:
                    has_local_audio = False
                    diagnostics['missing_local_audio'].append({
                        'entry_id': entry_id,
                        'name': name,
                        'source': source_code,
                        'path': raw_path,
                    })
            resolved_context['has_local_audio'] = has_local_audio
            resolved_context['local_audio_path'] = local_audio_path

        # Deck card count
        if raw_kind == 'deck':
            d_src = _norm(source_code)
            c_count = (
                deck_card_counts.get((_norm(item.get('name')), d_src))
                or deck_card_counts.get((_norm(item.get('ENG_name')), d_src))
                or (len(item['cards']) if isinstance(item.get('cards'), list) else None)
            )
            if c_count is not None:
                resolved_context['card_count'] = c_count

        # Fluff attachment
        fluff_data = _find_fluff_for_record(rec)
        if fluff_data is not None:
            resolved_context['fluff'] = fluff_data

        # Classify and extract fields via types.py
        entry_type = classify_record(raw_kind, item)
        fields, quickref, field_conflicts = extract_fields(
            raw_kind,
            entry_type,
            item,
            resolved_context=resolved_context,
            copy_status=rec.copy_status,
        )

        # Ensure provenance facets are present in quickref/fields if useful
        entry: dict[str, Any] = {
            'id': entry_id,
            'entity_id': None,  # populated in Stage 8
            'collection': collection_key,
            'package_id': package_id,
            'source_id': source_id,
            'source_code': source_code,
            'source_name': source_rec.get('name', source_code),
            'source_short': source_rec.get('short_label', source_code),
            'origin': resolved_context['origin'],
            'package_kind': resolved_context['package_kind'],
            'tier': resolved_context['tier'],
            'ours': resolved_context['ours'],
            'ruleset': ruleset,
            'raw_kind': raw_kind,
            'type': entry_type,
            'name': name,
            'name_zh': name,
            'name_en': name_en,
            'owner_class': owner_class,
            'owner_subclass': owner_subclass,
            'owner_level': owner_level,
            'is_version': rec.seq in version_parent_seq,
            'version_base_id': None,  # linked in Stage 7
            'copy_status': rec.copy_status,
            'copy_warnings': list(rec.copy_warnings),
            'fields': fields,
            'quickref': quickref,
            'field_conflicts': field_conflicts,
            'fluff': fluff_data,
            'spell_classes': spell_classes_val if raw_kind == 'spell' else [],
            'spell_subclasses': spell_subclasses_val if raw_kind == 'spell' else [],
            'legendary_group': lg_data,
            'has_local_audio': has_local_audio,
            'local_audio_path': local_audio_path,
            'relpath': rec.relpath,
            'data': item,
            '_seq': rec.seq,
            '_lg_seq': lg_rec.seq if lg_rec is not None else None,
            '_copied_from_seq': rec.copied_from_seq,
        }
        entries.append(entry)
        entry_by_id[entry_id] = entry

    # Report orphan fluff
    for frec in fluff_records:
        if frec.seq not in fluff_used and isinstance(frec.resolved, dict):
            diagnostics['orphan_fluff'].append({
                'package_id': frec.package_id,
                'raw_kind': frec.raw_kind,
                'name': frec.resolved.get('name') or frec.resolved.get('ENG_name'),
                'source': _effective_source_code(frec.raw_kind, frec.resolved),
            })

    # -----------------------------------------------------------------------
    # Stage 7: Relations & Lookup Index
    # -----------------------------------------------------------------------
    relations: list[dict[str, Any]] = []
    seen_relations: set[tuple[str, str, str]] = set()

    def _add_relation(src_id: str, rel_type: str, dst_id: str, meta: dict[str, Any] | None = None) -> None:
        if not src_id or not dst_id or src_id == dst_id:
            return
        key = (src_id, rel_type, dst_id)
        if key in seen_relations:
            return
        seen_relations.add(key)
        rel = {'source_id': src_id, 'type': rel_type, 'target_id': dst_id}
        if meta:
            rel['meta'] = meta
        relations.append(rel)

    # Indices for resolving relations
    class_index: dict[tuple[str, str], list[str]] = defaultdict(list)       # (name_lower, src_lower) -> [eid]
    subclass_index: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)  # (short_or_name, src, cls, cls_src) -> [eid]
    class_feat_index: dict[tuple[str, str, str, int | None, str], list[str]] = defaultdict(list)
    sub_feat_index: dict[tuple[str, str, str, str, str, int | None, str], list[str]] = defaultdict(list)
    deck_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    reprint_index: dict[tuple[str, str, str], list[str]] = defaultdict(list)  # (pool_or_kind, name_lower, src_lower) -> [eid]

    for e in entries:
        eid = e['id']
        rk = e['raw_kind']
        item = e['data']
        src_low = _norm(e['source_code'])
        names = [
            n for n in (
                _norm(e['name']),
                _norm(e['name_en']),
                _norm(item.get('name')),
                _norm(item.get('ENG_name')),
                _norm(item.get('abbreviation')),
            )
            if n
        ]

        reprint_pool = (
            'item_pool' if rk in {'item', 'baseitem', 'itemGroup', 'magicvariant'}
            else ('race_pool' if rk in {'race', 'subrace'} else rk)
        )
        for n in set(names):
            reprint_index[(reprint_pool, n, src_low)].append(eid)
            reprint_index[(rk, n, src_low)].append(eid)
            for tag_alias in RAW_KIND_TO_TAGS.get(rk, ()):
                reprint_index[(tag_alias, n, src_low)].append(eid)

        if rk == 'class':
            for n in set(names):
                class_index[(n, src_low)].append(eid)
        elif rk == 'subclass':
            c_low = _norm(item.get('className'))
            csrc_low = _norm(item.get('classSource'))
            s_names = set(names)
            if item.get('shortName'):
                s_names.add(_norm(item['shortName']))
            for sn in s_names:
                subclass_index[(sn, src_low, c_low, csrc_low)].append(eid)
                subclass_index[(sn, src_low, c_low, '')].append(eid)
        elif rk == 'classFeature':
            c_low = _norm(item.get('className'))
            csrc_low = _norm(item.get('classSource'))
            lvl = e['owner_level']
            for n in set(names):
                class_feat_index[(n, c_low, csrc_low, lvl, src_low)].append(eid)
                class_feat_index[(n, c_low, '', lvl, src_low)].append(eid)
                class_feat_index[(n, c_low, csrc_low, lvl, '')].append(eid)
        elif rk == 'subclassFeature':
            c_low = _norm(item.get('className'))
            csrc_low = _norm(item.get('classSource'))
            ss_low = _norm(item.get('subclassShortName'))
            ssrc_low = _norm(item.get('subclassSource'))
            lvl = e['owner_level']
            for n in set(names):
                sub_feat_index[(n, c_low, csrc_low, ss_low, ssrc_low, lvl, src_low)].append(eid)
                sub_feat_index[(n, c_low, '', ss_low, ssrc_low, lvl, src_low)].append(eid)
                sub_feat_index[(n, c_low, csrc_low, ss_low, ssrc_low, lvl, '')].append(eid)
        elif rk == 'deck':
            for n in set(names):
                deck_index[(n, src_low)].append(eid)

    def _pick_best(cands: list[str], pref_pkg: str) -> str | None:
        if not cands:
            return None
        for cid in cands:
            if entry_by_id[cid]['package_id'] == pref_pkg:
                return cid
        for cid in cands:
            if entry_by_id[cid]['package_id'] == 'official':
                return cid
        return cands[0]

    # Build relations
    reprint_edges: list[tuple[str, str]] = []
    for e in entries:
        eid = e['id']
        rk = e['raw_kind']
        pkg_id = e['package_id']
        item = e['data']
        seq = e['_seq']

        # 7a. variant_of
        if seq in version_parent_seq:
            parent_eid = seq_to_entry_id.get(version_parent_seq[seq])
            if parent_eid:
                e['version_base_id'] = parent_eid
                _add_relation(eid, 'variant_of', parent_eid)

        # 7b. copied_from
        if e['_copied_from_seq'] is not None:
            base_eid = seq_to_entry_id.get(e['_copied_from_seq'])
            if base_eid:
                _add_relation(eid, 'copied_from', base_eid)

        # 7c. subrace -> parent race (part_of)
        if rk == 'subrace' and seq in subrace_parent_map:
            parent_eid = seq_to_entry_id.get(subrace_parent_map[seq].seq)
            if parent_eid:
                _add_relation(eid, 'part_of', parent_eid)

        # 7d. monster -> legendaryGroup
        if rk == 'monster' and e['_lg_seq'] is not None:
            lg_eid = seq_to_entry_id.get(e['_lg_seq'])
            if lg_eid:
                _add_relation(eid, 'has_legendary_group', lg_eid)

        # 7e. card -> deck (part_of)
        if rk == 'card' and item.get('set'):
            d_cands = deck_index.get((_norm(item['set']), _norm(e['source_code'])), [])
            deck_eid = _pick_best(d_cands, pkg_id)
            if deck_eid:
                _add_relation(eid, 'part_of', deck_eid)

        # 7f. class / subclass / classFeature / subclassFeature relations
        if rk == 'class':
            cls_name = str(item.get('name') or '')
            cls_src = e['source_code']
            parsed_cfs = []
            for cf_ref in item.get('classFeatures') or []:
                parsed = _parse_class_feature_ref(cf_ref, cls_name, cls_src)
                if not parsed:
                    continue
                parsed_cfs.append(parsed)
                cands = (
                    class_feat_index.get((_norm(parsed['name']), _norm(parsed['className']), _norm(parsed['classSource']), parsed['level'], _norm(parsed['source'])))
                    or class_feat_index.get((_norm(parsed['name']), _norm(parsed['className']), '', parsed['level'], _norm(parsed['source'])))
                    or class_feat_index.get((_norm(parsed['name']), _norm(parsed['className']), _norm(parsed['classSource']), parsed['level'], ''))
                    or []
                )
                cf_eid = _pick_best(cands, pkg_id)
                if cf_eid:
                    _add_relation(cf_eid, 'part_of', eid, {'level': parsed['level']})
                    _add_relation(cf_eid, 'belongs_to_class', eid, {'level': parsed['level']})
            e['class_feature_refs'] = parsed_cfs

        elif rk == 'subclass':
            cls_name = str(item.get('className') or '')
            cls_src = str(item.get('classSource') or e['source_code'])
            sub_short = str(item.get('shortName') or item.get('name') or '')
            sub_src = e['source_code']
            cls_eid = _pick_best(class_index.get((_norm(cls_name), _norm(cls_src)), []), pkg_id)
            if cls_eid:
                _add_relation(eid, 'part_of', cls_eid)
                _add_relation(eid, 'belongs_to_class', cls_eid)
            parsed_sfs = []
            for sf_ref in item.get('subclassFeatures') or []:
                parsed = _parse_subclass_feature_ref(sf_ref, cls_name, cls_src, sub_short, sub_src)
                if not parsed:
                    continue
                parsed_sfs.append(parsed)
                cands = (
                    sub_feat_index.get((
                        _norm(parsed['name']), _norm(parsed['className']), _norm(parsed['classSource']),
                        _norm(parsed['subclassShortName']), _norm(parsed['subclassSource']),
                        parsed['level'], _norm(parsed['source']),
                    ))
                    or sub_feat_index.get((
                        _norm(parsed['name']), _norm(parsed['className']), '',
                        _norm(parsed['subclassShortName']), _norm(parsed['subclassSource']),
                        parsed['level'], _norm(parsed['source']),
                    ))
                    or sub_feat_index.get((
                        _norm(parsed['name']), _norm(parsed['className']), _norm(parsed['classSource']),
                        _norm(parsed['subclassShortName']), _norm(parsed['subclassSource']),
                        parsed['level'], '',
                    ))
                    or []
                )
                sf_eid = _pick_best(cands, pkg_id)
                if sf_eid:
                    _add_relation(sf_eid, 'part_of', eid, {'level': parsed['level']})
                    if cls_eid:
                        _add_relation(sf_eid, 'belongs_to_class', cls_eid, {'level': parsed['level']})
            e['subclass_feature_refs'] = parsed_sfs

        elif rk == 'classFeature':
            cls_eid = _pick_best(
                class_index.get((_norm(item.get('className')), _norm(item.get('classSource'))), []),
                pkg_id,
            )
            if cls_eid:
                _add_relation(eid, 'part_of', cls_eid, {'level': e['owner_level']})
                _add_relation(eid, 'belongs_to_class', cls_eid, {'level': e['owner_level']})

        elif rk == 'subclassFeature':
            c_low = _norm(item.get('className'))
            csrc_low = _norm(item.get('classSource'))
            ss_low = _norm(item.get('subclassShortName'))
            ssrc_low = _norm(item.get('subclassSource'))
            sub_cands = (
                subclass_index.get((ss_low, ssrc_low, c_low, csrc_low))
                or subclass_index.get((ss_low, ssrc_low, c_low, ''))
                or []
            )
            sub_eid = _pick_best(sub_cands, pkg_id)
            if sub_eid:
                _add_relation(eid, 'part_of', sub_eid, {'level': e['owner_level']})
            cls_eid = _pick_best(class_index.get((c_low, csrc_low), []), pkg_id)
            if cls_eid:
                _add_relation(eid, 'belongs_to_class', cls_eid, {'level': e['owner_level']})

        # 7g. reprintedAs
        reprinted = item.get('reprintedAs')
        if isinstance(reprinted, list):
            reprint_pool = (
                'item_pool' if rk in {'item', 'baseitem', 'itemGroup', 'magicvariant'}
                else ('race_pool' if rk in {'race', 'subrace'} else rk)
            )
            compat_kinds: dict[str, tuple[str, ...]] = {
                'action': ('variantrule',),
                'boon': ('cult',),
                'cult': ('boon',),
                'optionalfeature': ('feat',),
                'reward': ('feat',),
                'subrace': ('feat', 'race'),
            }
            for rep in reprinted:
                uid = rep.get('uid') if isinstance(rep, dict) else (rep if isinstance(rep, str) else None)
                rep_tag = rep.get('tag') if isinstance(rep, dict) else None
                if not uid:
                    continue
                parts = str(uid).split('|')
                if rk == 'subclass' and len(parts) >= 4:
                    # Format: shortName|className|classSource|subSource
                    sn_low = _norm(parts[0])
                    c_low = _norm(parts[1])
                    csrc_low = _norm(parts[2])
                    ssrc_low = _norm(parts[3])
                    cands = subclass_index.get((sn_low, ssrc_low, c_low, csrc_low)) or subclass_index.get((sn_low, ssrc_low, c_low, '')) or []
                else:
                    r_name = _norm(parts[0])
                    r_src = _norm(parts[1]) if len(parts) > 1 else ''
                    if r_src in official_casefold:
                        r_src = official_casefold[r_src].lower()
                    cands = []
                    if rep_tag:
                        cands = reprint_index.get((rep_tag, r_name, r_src)) or []
                    if not cands:
                        cands = reprint_index.get((rk, r_name, r_src)) or reprint_index.get((reprint_pool, r_name, r_src)) or []
                    if not cands:
                        for ck in compat_kinds.get(rk, ()):
                            cands = reprint_index.get((ck, r_name, r_src)) or []
                            if cands:
                                break
                non_self = [c for c in cands if c != eid]
                if not non_self and eid in cands:
                    continue  # self-reprint marker (e.g. brew repeating its own source)
                target_eid = _pick_best(non_self, pkg_id)
                if target_eid:
                    _add_relation(eid, 'reprinted_as', target_eid)
                    reprint_edges.append((eid, target_eid))
                else:
                    diagnostics['unresolved_reprints'].append({
                        'entry_id': eid,
                        'raw_kind': rk,
                        'name': e['name'],
                        'source': e['source_code'],
                        'reprintedAs': uid,
                    })

    # -----------------------------------------------------------------------
    # Stage 8: Identity Groups (stackable types: reprintedAs chains + official 5e/5r pairs only)
    # -----------------------------------------------------------------------
    parent_uf: dict[str, str] = {e['id']: e['id'] for e in entries}

    def _uf_find(x: str) -> str:
        while parent_uf[x] != x:
            parent_uf[x] = parent_uf[parent_uf[x]]
            x = parent_uf[x]
        return x

    def _uf_union(a: str, b: str) -> None:
        ra, rb = _uf_find(a), _uf_find(b)
        if ra != rb:
            if ra < rb:
                parent_uf[rb] = ra
            else:
                parent_uf[ra] = rb

    def _can_stack(ea: dict[str, Any], eb: dict[str, Any]) -> bool:
        if ea['type'] != eb['type'] or ea['type'] not in STACKABLE_TYPES:
            return False
        # Never merge distinct community packages, and never merge official with community
        if ea['package_id'] != eb['package_id']:
            return False
        # Never merge distinct variants/versions of different names unless reprintedAs
        return True

    # 8a. Union via reprintedAs edges (within same package & stackable type)
    for src_eid, dst_eid in reprint_edges:
        ea, eb = entry_by_id[src_eid], entry_by_id[dst_eid]
        if _can_stack(ea, eb):
            _uf_union(src_eid, dst_eid)

    # 8b. Union official 5e / 5r pairs sharing normalized name_en (or name if no name_en)
    official_by_type_key: dict[tuple[str, str, str, str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        if e['package_id'] != 'official' or e['type'] not in STACKABLE_TYPES:
            continue
        if e['ruleset'] not in {'5e', '5r'}:
            continue
        match_key = _norm(e['name_en']) or _norm(e['name'])
        if not match_key:
            continue
        # Scope subclasses/features by owner to avoid false collisions across classes
        scope_key = (
            e['type'],
            match_key,
            _norm(e['owner_class']),
            _norm(e['owner_subclass']),
            e['owner_level'],
        )
        official_by_type_key[scope_key].append(e)

    for _scope, members in official_by_type_key.items():
        rulesets = {m['ruleset'] for m in members}
        if '5e' in rulesets and '5r' in rulesets:
            first_id = members[0]['id']
            for m in members[1:]:
                _uf_union(first_id, m['id'])

    grouped_members: dict[str, list[str]] = defaultdict(list)
    for e in entries:
        grouped_members[_uf_find(e['id'])].append(e['id'])

    identity_groups: list[dict[str, Any]] = []
    for _root, member_ids in sorted(grouped_members.items()):
        member_ids.sort()
        if len(member_ids) > 1:
            gid = 'o5eg_' + sha256(canonical(member_ids).encode('utf-8')).hexdigest()[:16]
            sample = entry_by_id[member_ids[0]]
            identity_groups.append({
                'id': gid,
                'type': sample['type'],
                'package_id': sample['package_id'],
                'name': sample['name'],
                'name_en': sample['name_en'],
                'member_ids': member_ids,
                'rulesets': sorted({entry_by_id[m]['ruleset'] for m in member_ids}),
            })
            for mid in member_ids:
                entry_by_id[mid]['entity_id'] = gid
        else:
            mid = member_ids[0]
            entry_by_id[mid]['entity_id'] = 'o5ee_' + mid[4:]

    # -----------------------------------------------------------------------
    # Stage 9: Build lookup_index for render.py ({@spell ...}, {@creature ...}, etc.)
    # -----------------------------------------------------------------------
    lookup_index: dict[tuple[str, ...], list[str]] = defaultdict(list)

    def _index_add(key: tuple[str, ...], eid: str) -> None:
        lst = lookup_index[key]
        if eid not in lst:
            lst.append(eid)

    # Sort official entries before brew entries so lookup order is deterministic & official-first
    ordered_for_lookup = sorted(entries, key=lambda e: (0 if e['package_id'] == 'official' else 1, e['id']))
    for e in ordered_for_lookup:
        eid = e['id']
        rk = e['raw_kind']
        etype = e['type']
        item = e['data']
        tags = list(dict.fromkeys((*RAW_KIND_TO_TAGS.get(rk, (rk,)), etype)))
        src_low = _norm(e['source_code'])
        abv_low = _norm(e['source_short'])
        src_keys = [s for s in dict.fromkeys((src_low, abv_low)) if s]

        name_keys: list[str] = []
        for candidate in (
            e['name'],
            e['name_en'],
            item.get('name'),
            item.get('ENG_name'),
            item.get('abbreviation'),
            item.get('shortName'),
            item.get('_subraceShortName'),
            item.get('_combinedName'),
            item.get('_combinedENGName'),
        ):
            nk = _norm(candidate)
            if nk and nk not in name_keys:
                name_keys.append(nk)

        # Disambiguated composite keys for classFeature, subclassFeature, subclass, card, deity
        if rk == 'classFeature' and e['owner_class'] and e['owner_level'] is not None:
            for base_n in (_norm(item.get('name')), _norm(item.get('ENG_name'))):
                if base_n:
                    name_keys.append(f"{base_n}|{_norm(e['owner_class'])}|{e['owner_level']}")
        elif rk == 'subclassFeature' and e['owner_class'] and e['owner_subclass'] and e['owner_level'] is not None:
            for base_n in (_norm(item.get('name')), _norm(item.get('ENG_name'))):
                if base_n:
                    name_keys.append(
                        f"{base_n}|{_norm(e['owner_class'])}|{_norm(e['owner_subclass'])}|{e['owner_level']}"
                    )
        elif rk == 'subclass' and e['owner_class']:
            for base_n in (_norm(item.get('name')), _norm(item.get('shortName')), _norm(item.get('ENG_name'))):
                if base_n:
                    name_keys.append(f"{base_n}|{_norm(e['owner_class'])}")
        elif rk == 'card' and item.get('set'):
            for base_n in (_norm(item.get('name')), _norm(item.get('ENG_name'))):
                if base_n:
                    name_keys.append(f"{base_n}|{_norm(item['set'])}")
        elif rk == 'deity' and item.get('pantheon'):
            for base_n in (_norm(item.get('name')), _norm(item.get('ENG_name'))):
                if base_n:
                    name_keys.append(f"{base_n}|{_norm(item['pantheon'])}")

        for tag in tags:
            for nk in name_keys:
                _index_add((tag, nk), eid)
                for sk in src_keys:
                    _index_add((tag, nk, sk), eid)

    # Strip internal temporary fields from entries
    for e in entries:
        e.pop('_seq', None)
        e.pop('_lg_seq', None)
        e.pop('_copied_from_seq', None)

    # Finalize diagnostics summary counts
    diagnostics['copy_summary'] = dict(diagnostics['copy_summary'])
    diagnostics['skipped_non_entity_props'] = dict(diagnostics['skipped_non_entity_props'])
    diagnostics['skipped_non_dict_items'] = dict(diagnostics['skipped_non_dict_items'])
    diagnostics['counts'] = {
        'entries': len(entries),
        'sources': len(sources),
        'packages': len(packages),
        'relations': len(relations),
        'identity_groups': len(identity_groups),
        'lookup_keys': len(lookup_index),
        'missing_base': len(diagnostics['copy_missing_base']),
        'ambiguous_base': len(diagnostics['copy_ambiguous_base']),
        'cycles': len(diagnostics['copy_cycles']),
        'mod_warnings': len(diagnostics['copy_mod_warnings']),
        'version_count': diagnostics['version_count'],
        'orphan_fluff': len(diagnostics['orphan_fluff']),
        'missing_local_audio': len(diagnostics['missing_local_audio']),
        'unresolved_reprints': len(diagnostics['unresolved_reprints']),
        'duplicate_identity_keys': len(diagnostics['duplicate_identity_keys']),
    }

    return ResolvedCorpus(
        entries=entries,
        sources=sources,
        packages=packages,
        relations=relations,
        identity_groups=identity_groups,
        lookup_index=dict(lookup_index),
        diagnostics=diagnostics,
    )
