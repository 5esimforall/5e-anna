"""Portable entry and span selectors shared by reference review."""
from __future__ import annotations

import re


def entry_selector(entry):
    """Portable entry identity without snapshot byte offsets or ver_ IDs."""
    return {
        'type': entry['type'],
        'ruleset': entry['ruleset'],
        'name_zh': entry['name_zh'],
        'name_en': entry.get('name_en', ''),
        'page_path': entry['page_path'],
    }


def resolve_subject(context, subject, *, non_empty=True):
    """Resolve an entry selector; page moves transfer only when unique."""
    matches = [
        e for e in context.entries.values()
        if (not non_empty or e['end'] > e['start'])
        and e['type'] == subject['type']
        and e['ruleset'] == subject['ruleset']
        and e['name_zh'] == subject['name_zh']
        and e.get('name_en', '') == subject.get('name_en', '')
    ]
    if not matches:
        return 'missing', None
    exact = [e for e in matches if e['page_path'] == subject['page_path']]
    if len(exact) == 1:
        return 'exact', exact[0]
    if len(exact) > 1:
        return 'ambiguous', None
    if len(matches) == 1:
        return 'transferred', matches[0]
    return 'ambiguous', None


def bind_span_anchor(text, anchor, start=0, end=None):
    """Bind a portable span anchor (`quote` + optional `within`) within `text[start:end]`."""
    wa, wb = start, len(text) if end is None else end
    quote = anchor['quote']
    within = anchor.get('within')
    if within:
        wmatches = list(re.finditer(re.escape(within), text[wa:wb]))
        if not wmatches:
            qspans = [(wa + m.start(), wa + m.end()) for m in re.finditer(re.escape(quote), text[wa:wb])]
            return 'stale', None, qspans, 'Span enclosing text not found'
        if len(wmatches) > 1:
            wspans = [(wa + m.start(), wa + m.end()) for m in wmatches]
            return 'ambiguous', None, wspans, 'Span enclosing text matches multiple locations'
        wa, wb = wa + wmatches[0].start(), wa + wmatches[0].end()
    qmatches = list(re.finditer(re.escape(anchor['quote']), text[wa:wb]))
    if not qmatches:
        return 'missing', None, [], 'Span quote not found'
    if len(qmatches) > 1:
        qspans = [(wa + m.start(), wa + m.end()) for m in qmatches]
        return 'ambiguous', None, qspans, 'Span quote matches multiple occurrences'
    span = (wa + qmatches[0].start(), wa + qmatches[0].end())
    return 'bound', span, [span], 'Bound uniquely'
