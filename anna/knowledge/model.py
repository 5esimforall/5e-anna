"""Canonical source-bounded rule bundles, independent of the carrier's type.

Grant and procedure records use the same conditions, usage, quantities and effect
graph. Spell-specific values are optional domain data, not a second lifecycle.
"""
from anna.knowledge.conditions import bind_effects, local_references, condition_terms
from anna.knowledge.values import evidence

SCHEMA = 2
KINDS = {'grant', 'procedure', 'definition_role'}
EFFECT_KINDS = ('teleport','healing','saving_throw','condition','attitude','awareness','upcast','targets','attack_roll','damage','modifier','restriction','behavior','check_modifier','end_spell','level_scale','ignite','save_override','target_constraint','event','check_sequence','sequence_policy','duration_override','no_effect')
ACTIONS = ('action', 'bonus_action', 'reaction', 'timed', 'not_stated')


def rule_value(**parts):
    return {'prerequisites': {'status': 'not_stated'}, 'activation': None,
            'contexts': {}, 'grants': [], 'modifiers': [], 'parameters': {},
            'effects': [], 'descriptions': [], 'unparsed': [], **parts}


def finish_effects(effects, gaps, path, text, start, end):
    """Bind one local graph; source paragraphs constrain rather than pick targets."""
    for effect in effects:
        a, b = effect['evidence']['span']
        pa = text.rfind('\n', start, a) + 1
        pb = text.find('\n', b, end)
        effect['paragraph'] = evidence(path, text, max(start, pa), pb if pb != -1 else end)
        if effect.get('initial_attack_result') == 'not_stated':
            gaps.append({'reason': 'unresolved_ongoing_precondition', 'evidence': effect['evidence']})
    return bind_effects(effects, gaps)


def validate_usage(use):
    if not isinstance(use, dict):
        raise ValueError('Invalid rule usage')
    if 'action' in use and use['action'] not in ACTIONS:
        raise ValueError('Invalid usage action')
    if 'frequency' in use and use['frequency'] not in {'at_will', 'once_per_turn'}:
        raise ValueError('Invalid usage frequency')
    if 'resource' in use:
        resource = use['resource']
        if not isinstance(resource, dict) or resource.get('kind') not in {'spell_slot', 'not_stated'}:
            raise ValueError('Invalid usage resource')
        if resource['kind'] == 'spell_slot' and type(resource.get('consumed')) is not bool:
            raise ValueError('Invalid spell-slot consumption')
    limit = use.get('uses')
    if limit is not None and (not isinstance(limit, dict) or limit.get('per') not in {'spell', 'grant', 'feature'} or not isinstance(limit.get('quantity'), dict)):
        raise ValueError('Invalid usage limit')
    if limit and limit['quantity'].get('kind') == 'constant' and (type(limit['quantity'].get('value')) is not int or limit['quantity']['value'] <= 0):
        raise ValueError('Invalid usage quantity')
    recovery = use.get('recovery')
    if recovery is not None and (not isinstance(recovery, dict) or recovery.get('event') not in {'long_rest', 'short_rest', 'short_or_long_rest'} or recovery.get('amount') != 'all'):
        raise ValueError('Invalid usage recovery')


def validate_structure(record):
    if record.get('schema') != SCHEMA:
        raise ValueError('Unsupported rule knowledge schema')
    if record['kind'] == 'definition_role':
        return
    value = record['value']
    if set(rule_value()) - value.keys() or 'casting' in value:
        raise ValueError('Incomplete common rule structure')
    for field in ('grants', 'effects', 'modifiers', 'descriptions', 'unparsed'):
        if not isinstance(value[field], list):
            raise ValueError('Invalid common rule collection')
    for field in ('parameters', 'contexts', 'prerequisites'):
        if not isinstance(value[field], dict):
            raise ValueError('Invalid common rule object')
    if record['kind'] == 'grant' and value['effects'] or record['kind'] == 'procedure' and value['grants']:
        raise ValueError('Rule kind and statement role disagree')
    grants = value['grants']
    if len({g['id'] for g in grants}) != len(grants):
        raise ValueError('Duplicate grant identity')
    for grant in grants:
        if grant.get('entry_type') not in {'spell', 'feat', 'class_feature'}:
            raise ValueError('Invalid grant target type')
        if grant['mode'] not in ({'known', 'prepared', 'cast', 'list', 'selection'} if grant['entry_type'] == 'spell' else {'gain'}):
            raise ValueError('Invalid grant mode')
        if len({b['id'] for b in grant['branches']}) != len(grant['branches']):
            raise ValueError('Duplicate usage branch identity')
        for branch in grant['branches']:
            validate_usage(branch)
    for statement in [*grants,*value['modifiers']]:
        for term in condition_terms(statement.get('when')):
            if term.get('kind')=='option_selected':
                context=value['contexts'].get(term.get('context_id'),{})
                options=[o for o in context.get('options',[]) if o['id']==term.get('option_id')]
                if context.get('kind')!='choose_one' or len(options)!=1 or term.get('name')!=options[0]['name']:
                    raise ValueError('Invalid local grant choice')
    for modifier in value['modifiers']:
        if modifier.get('kind')=='replace_choice' and modifier.get('trigger')=='long_rest':
            if modifier.get('grant_id') not in {g['id'] for g in grants} or modifier.get('target',{}).get('kind')!='choice' or modifier.get('count')!=1:
                raise ValueError('Invalid table spell replacement')
    for effect in value['effects']:
        if effect.get('kind') not in EFFECT_KINDS:
            raise ValueError('Unsupported effect kind')
        if 'use' in effect:
            validate_usage(effect['use'])
        if effect.get('operation') == 'multiply':
            factor = effect.get('factor', {})
            if set(factor) != {'numerator', 'denominator'} or any(type(n) is not int or n <= 0 for n in factor.values()):
                raise ValueError('Invalid multiplicative effect factor')
            if effect.get('rounding') not in {'floor', 'not_stated'}:
                raise ValueError('Invalid effect rounding')
            if effect.get('applies_to', {}).get('kind') != 'event_ref' or effect.get('when') != effect.get('applies_to'):
                raise ValueError('Damage modification must name its triggering event')
    for parameter in value['parameters'].values():
        if not isinstance(parameter, dict) or not parameter.get('evidence'):
            raise ValueError('Rule parameter requires source evidence')


def effect_usage(value, effect):
    """An explicit local use replaces the default action, never another branch's use."""
    if 'use' in effect:
        return effect['use']
    # A repeat, expiry, threshold or scaling statement is not another execution
    # of the source's initial action. In particular, a later save is not a cast.
    executable = {'teleport', 'healing', 'targets', 'attack_roll', 'saving_throw',
                  'damage', 'condition', 'attitude', 'modifier', 'restriction', 'behavior', 'ignite'}
    if effect['kind'] not in executable or effect.get('check_role') == 'repeat':
        return {}
    if any(ref.get('scope') == 'repeat' or ref['kind'] in {'check_total', 'sequence_ref'}
           for _, ref in local_references(effect)):
        return {}
    if any(term.get('kind') in {'turn', 'damage_received', 'wake_action', 'spell_ends', 'concentration_duration'}
           for term in condition_terms(effect.get('when'))):
        return {}
    action = value['parameters'].get('action')
    return {'action': action['kind'], 'evidence': [action['evidence']]} if action else {}
