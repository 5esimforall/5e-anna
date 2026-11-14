"""Source evidence and symbolic quantities shared by all rule domains."""
import re
from rule_conditions import ABILITIES, CLASSES

DAMAGE_TYPES=dict(zip(('强酸','寒冷','火焰','力场','闪电','暗蚀','毒素','心灵','光耀','雷鸣','钝击','穿刺','挥砍'),
                     ('acid','cold','fire','force','lightning','necrotic','poison','psychic','radiant','thunder','bludgeoning','piercing','slashing')))

DIGITS = {'零': 0, '一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
NUMBER = r'(?:\d+|[零一两二三四五六七八九十])'

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
    raw = re.sub(r'\s+', '', raw).replace('＋', '+')
    m = re.fullmatch(r'(\d+d\d+)\+你的?('+'|'.join(CLASSES)+r')职业等级', raw)
    if m:
        return {'kind': 'sum', 'terms': [quantity_expression(m[1]),
                {'kind': 'class_level', 'class': m[2]}]}
    m = re.fullmatch(r'(\d+)d(\d+)(?:\+你的?施法属性调整值)?', raw)
    if m:
        dice = {'kind': 'dice', 'count': int(m[1]), 'sides': int(m[2])}
        return {'kind': 'sum', 'terms': [dice, {'kind': 'modifier', 'ability': 'spellcasting'}]} if '+' in raw else dice
    m = re.fullmatch(r'(?:你的?)?('+'|'.join(ABILITIES)+r')调整值(?:[（(]至少('+NUMBER+r')次?[）)])?', raw)
    if m: return {'kind': 'modifier', 'ability': m[1], **({'minimum': number(m[2])} if m[2] else {})}
    n = number(raw)
    return {'kind': 'constant', 'value': n} if n is not None else None
