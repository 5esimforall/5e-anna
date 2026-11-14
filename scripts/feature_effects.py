"""A bounded non-spell probe of the common procedure model.

Discovery and grammar depend on clauses, never the entry's name. Other clauses
are retained as gaps; matching one effect does not certify the whole feature.
"""
import re
from rule_model import rule_value, finish_effects
from rule_values import evidence, sentences, quantity_expression
from rule_conditions import reference, turn

CANDIDATE = re.compile(r'附赠动作[^。]*恢复生命值|反应[^。]*伤害减半')


def parse_feature(entry, text, owner=None):
    start, end, path = entry['title_end'], entry['end'], entry['page_path']
    if not CANDIDATE.search(re.sub(r'\s+', '', text[start:end])):
        return None
    value = rule_value()
    effects, gaps = value['effects'], value['unparsed']
    # A class feature's level has an evidenced owner, not a character-level guess.
    heading = text[entry['start']:start]
    level = re.match(r'\s*(\d+)级[：:]', heading)
    if level:
        ev = evidence(path, text, entry['start'], start)
        if owner:
            value['prerequisites'] = {'status': 'parsed', 'expression': {
                'kind': 'class_level', 'class': owner['name_zh'], 'owner_id': owner['id'],
                'minimum': int(level[1])}, 'evidence': ev}
        else:
            gaps.append({'reason': 'unresolved_level_owner', 'evidence': ev})
    for a, b, clause in sentences(text, start, end):
        ev = evidence(path, text, a, b)
        level = re.match(r'第(\d+)级起，', clause)
        if level:
            if owner:
                value['prerequisites'] = {'status': 'parsed', 'expression': {
                    'kind': 'class_level', 'class': owner['name_zh'], 'owner_id': owner['id'],
                    'minimum': int(level[1])}, 'evidence': ev}
            else:
                gaps.append({'reason': 'unresolved_level_owner', 'evidence': ev})
            clause = clause[level.end():]
        if clause in {'你拥有少量精力储备可用来保护自己免受伤害。', '你可以利用有限的精力与体力来重整旗鼓。'}:
            value['descriptions'].append(ev)
            continue
        heal = re.fullmatch(r'(?:你可以在自己回合内用一个附赠动作|以一个附赠动作，你可以用此法)恢复生命值，其总值为(.+?)。', clause)
        if heal and (quantity := quantity_expression(heal[1])):
            effects.append({'kind': 'healing', 'target': {'kind': 'self'}, 'quantity': quantity,
                            'delivery': 'automatic', 'timing': 'immediate',
                            'use': {'action': 'bonus_action', 'resource': {'kind': 'not_stated'}, 'evidence': [ev]},
                            **({'when': turn('self', 'during', 'each')} if '自己回合内' in clause else {}),
                            'evidence': ev})
            continue
        if clause == '你必须完成一次短休或长休才能再次使用该特性。':
            uses = [e for e in effects if 'use' in e]
            if len(uses) == 1 and not gaps and 'recovery' not in uses[0]['use']:
                uses[0]['use'].update(uses={'quantity': {'kind': 'constant', 'value': 1}, 'per': 'feature'},
                                      recovery={'event': 'short_or_long_rest', 'amount': 'all'})
                uses[0]['use']['evidence'].append(ev)
            else:
                gaps.append({'reason': 'unresolved_usage_recovery', 'evidence': ev})
            continue
        attack = re.fullmatch(r'当一个你能看见的(?:敌人攻击命中你|攻击者用一次攻击检定命中你)时，你可以用(?:你的)?反应(?:让此次攻击的伤害减半|将这次攻击对你造成的伤害减半)(（向下取整）)?。', clause)
        if attack:
            effects.append({'kind': 'event', 'event': 'attack_received', 'subject': 'self',
                            'result': 'hit', 'attacker': {'visible': True}, 'evidence': ev})
            effects.append({'kind': 'modifier', 'field': 'damage', 'operation': 'multiply',
                            'factor': {'numerator': 1, 'denominator': 2},
                            'rounding': 'floor' if attack[1] else 'not_stated',
                            'target': {'kind': 'self'}, 'timing': 'immediate',
                            'when': reference('event_ref', selector='same_clause'),
                            'applies_to': reference('event_ref', selector='same_clause'),
                            'use': {'action': 'reaction', 'resource': {'kind': 'not_stated'}, 'evidence': [ev]},
                            'evidence': ev})
            continue
        gaps.append({'reason': 'unsupported_procedure', 'evidence': ev})
    gaps.extend(finish_effects(effects, gaps, path, text, start, end))
    return value
