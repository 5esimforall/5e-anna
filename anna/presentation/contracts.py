"""Explicit wire projections, independent of extractor classes and storage-only metadata.

These are the existing consumer contracts, not a new wire version. Optional keys
stay absent; nulls, ordering, identities, evidence and review states are retained.
Conditions, quantities, evidence, field maps and other semantic JSON values are
intentional parts of the contract, copied without evaluating or normalizing them.
A new storage schema needs an explicit adapter here, not a frontend rewrite.
"""
from copy import deepcopy

KNOWLEDGE_SCHEMA = 2

ENTRY_FIELDS = frozenset('''anchors body_hash boundary_rule classification_rule classification_status
    end entity_id field_conflicts fields id identity_status is_primary legacy locator name name_en
    name_parse name_zh observation_ids page_path parent_id ruleset ruleset_status snapshot source_ids
    source_names start table_span title_end type type_conflicts type_label'''.split())
RECORD_FIELDS = frozenset('''schema id subject_id kind ruleset value evidence dependencies status
    review_status decision_ids'''.split())
VALUE_FIELDS = frozenset('''prerequisites activation contexts grants modifiers parameters effects
    descriptions unparsed evidence role rules scope scope_entry_id'''.split())
GRANT_FIELDS = frozenset('id entry_type mode target constraints ability branches evidence when'.split())
BRANCH_FIELDS = frozenset('''id action cast_level components evidence frequency recovery resource
    target uses when'''.split())
EFFECT_FIELDS = frozenset('''ability ability_source after applies applies_to attack_type attacker behavior
    check check_role condition consecutive constraint control counting counting_scope damage_type delivery
    destination effect ends event evidence factor field form id initial_attack_result initial_inclusion
    kind knows levels links modifier modifiers occurrence operation paragraph per_slot_level quantity
    repeat_of result rounding scope selection sequence stop subject target threshold timing toward use
    uses value when'''.split())
OPTION_FIELDS = frozenset('''acquisition acquisition_steps branch branch_id casting_target condition_kinds
    conditions constraints grant grant_id id knowledge_id mode recovery resource root_id selection
    spell_id spell_ids subject_id'''.split())
REFERENCE_FIELDS = frozenset('''automatic can_link candidates decision_ids end evidence id kind
    observation_ids path presentation reason review_status role signal start status target_id text'''.split())
OBSERVATION_FIELDS = frozenset('''candidates end evidence id kind path reason role signal start status
    target_id text'''.split())


def select(value, fields):
    """Copy only named public fields, retaining presence and source order."""
    return {key: deepcopy(item) for key, item in value.items() if key in fields}


def entry(value):
    return select(value, ENTRY_FIELDS)


def grant(value):
    result = select(value, GRANT_FIELDS)
    if 'branches' in result:
        result['branches'] = [select(branch, BRANCH_FIELDS) for branch in result['branches']]
    return result


def record(value):
    if value.get('schema') != 2:
        raise ValueError('Unsupported stored knowledge schema for presentation')
    result = select(value, RECORD_FIELDS)
    result['schema'] = KNOWLEDGE_SCHEMA
    body = select(value['value'], VALUE_FIELDS)
    if 'grants' in body:
        body['grants'] = [grant(item) for item in body['grants']]
    if 'effects' in body:
        body['effects'] = [select(item, EFFECT_FIELDS) for item in body['effects']]
    result['value'] = body
    return result


def option(value):
    result = select(value, OPTION_FIELDS)
    if 'grant' in result:
        result['grant'] = grant(result['grant'])
    if 'branch' in result:
        result['branch'] = select(result['branch'], BRANCH_FIELDS)
    return result


def reference(value):
    return select(value, REFERENCE_FIELDS)


def observation(value):
    return select(value, OBSERVATION_FIELDS)
