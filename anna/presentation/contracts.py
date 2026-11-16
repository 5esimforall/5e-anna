"""Explicit wire projections, independent of extractor classes and storage-only metadata.

These are the existing consumer contracts, not a new wire version. Optional keys
stay absent; nulls, ordering, identities, evidence and review states are retained.
Conditions, quantities, evidence, field maps and other semantic JSON values are
intentional parts of the contract, copied without evaluating or normalizing them.
A new storage schema needs an explicit adapter here, not a frontend rewrite.
"""
from copy import deepcopy

ENTRY_FIELDS = frozenset('''anchors body_hash boundary_rule classification_rule classification_status
    end entity_id field_conflicts fields id identity_status is_primary legacy locator name name_en
    name_parse name_zh observation_ids page_path parent_id ruleset ruleset_status snapshot source_ids
    source_names start table_span title_end type type_conflicts type_label'''.split())
REFERENCE_FIELDS = frozenset('''automatic can_link candidates decision_ids end evidence id kind
    observation_ids path presentation reason review_status role signal start status target_id text'''.split())


def select(value, fields):
    """Copy only named public fields, retaining presence and source order."""
    return {key: deepcopy(item) for key, item in value.items() if key in fields}


def entry(value):
    return select(value, ENTRY_FIELDS)


def reference(value):
    return select(value, REFERENCE_FIELDS)
