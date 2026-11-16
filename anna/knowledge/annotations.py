"""Content- and context-versioned structured annotations over structural extractions."""
from __future__ import annotations

from copy import deepcopy
import re

from anna.knowledge.conditions import CLASSES, condition_terms
from anna.knowledge.extract import (
    compact,
    entry_clauses,
    entry_header,
    named_table,
    resolve_lookup,
    table_exclusions,
)
from anna.knowledge.model import finish_effects, rule_value
from anna.knowledge.values import evidence
from anna.review.store import annotation_lifecycle
from anna.serialization import digest


def subject_selector(context, entry):
    """Portable subject identity without snapshot byte offsets or ver_ IDs."""
    return {
        'type': entry['type'],
        'ruleset': entry['ruleset'],
        'name_zh': entry['name_zh'],
        'name_en': entry.get('name_en', ''),
        'page_path': entry['page_path'],
    }


def resolve_subject(context, subject):
    """Resolve an annotation subject; page moves transfer only when unique."""
    matches = [
        e for e in context.entries.values()
        if e['end'] > e['start']
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


def _table_caption_count(entry, context, label):
    return sum(
        1 for e in context.entries.values()
        if e['page_path'] == entry['page_path']
        and entry['title_end'] <= e['start'] < e['end'] <= entry['end']
        and e.get('table_span')
        and e['name_zh'] == label
    )


def bind_anchors(context, entry, anchors, header=None, clauses=None):
    """Bind portable anchors against live structural text, failing closed on ambiguity."""
    path = entry['page_path']
    text = context.pages[path]
    if header is None:
        header = entry_header(entry, text, context)
    if clauses is None:
        exclusions = table_exclusions(entry, header['body_start'], context)
        clauses = entry_clauses(entry, text, header['body_start'], exclusions)
    bound = {}
    errors = []
    for anchor in anchors:
        aid = anchor['id']
        akind = anchor['kind']
        if akind == 'clause':
            target = compact(anchor['quote'])
            indices = [i for i, c in enumerate(clauses) if c['compact'] == target]
            if not indices:
                errors.append(('stale', aid, 'Clause quote not found in entry prose'))
            elif len(indices) > 1:
                errors.append(('ambiguous', aid, 'Clause quote matches multiple clauses'))
            else:
                idx = indices[0]
                sa, sb = clauses[idx]['span']
                bound[aid] = {
                    'id': aid, 'kind': 'clause', 'span': [sa, sb],
                    'evidence': evidence(path, text, sa, sb),
                    'clause_indices': {idx},
                }
        elif akind == 'span':
            wa, wb = entry['title_end'], entry['end']
            within = anchor.get('within')
            if within:
                wmatches = list(re.finditer(re.escape(within), text[wa:wb]))
                if not wmatches:
                    errors.append(('stale', aid, 'Span enclosing text not found'))
                    continue
                if len(wmatches) > 1:
                    errors.append(('ambiguous', aid, 'Span enclosing text matches multiple locations'))
                    continue
                wa, wb = wa + wmatches[0].start(), wa + wmatches[0].end()
            qmatches = list(re.finditer(re.escape(anchor['quote']), text[wa:wb]))
            if not qmatches:
                errors.append(('stale', aid, 'Span quote not found'))
            elif len(qmatches) > 1:
                errors.append(('ambiguous', aid, 'Span quote matches multiple occurrences'))
            else:
                sa, sb = wa + qmatches[0].start(), wa + qmatches[0].end()
                overlapping = {i for i, c in enumerate(clauses) if sa < c['span'][1] and c['span'][0] < sb}
                bound[aid] = {
                    'id': aid, 'kind': 'span', 'span': [sa, sb],
                    'evidence': evidence(path, text, sa, sb),
                    'clause_indices': overlapping,
                }
        elif akind == 'table':
            label = anchor['caption_zh']
            try:
                caption, table, grid = named_table(entry, context, label)
            except ValueError as exc:
                count = _table_caption_count(entry, context, label)
                if 'competing' in str(exc) and count > 1:
                    errors.append(('ambiguous', aid, 'Competing tables match caption'))
                else:
                    errors.append(('stale', aid, str(exc)))
            else:
                bound[aid] = {
                    'id': aid, 'kind': 'table',
                    'span': [min(caption['start'], table['start']), table['end']],
                    'caption': caption, 'table': table, 'grid': grid,
                    'evidence': evidence(path, text, table['start'], table['end']),
                    'clause_indices': set(),
                }
        else:
            errors.append(('stale', aid, 'Unsupported anchor kind: ' + str(akind)))
    return bound, errors, header, clauses


def resolve_evidence_ref(ref, bound, context, entry):
    """Resolve an anchor/table/range reference into an exact source evidence span."""
    path = entry['page_path']
    text = context.pages[path]
    if isinstance(ref, str):
        if ref not in bound:
            raise ValueError('Unknown anchor reference: ' + ref)
        return bound[ref]['evidence']
    if not isinstance(ref, dict):
        raise ValueError('Invalid evidence reference')
    if 'anchor' in ref:
        aid = ref['anchor']
        if aid not in bound:
            raise ValueError('Unknown anchor reference: ' + aid)
        sa, sb = bound[aid]['span']
        quote = ref['quote']
        matches = list(re.finditer(re.escape(quote), text[sa:sb]))
        if len(matches) != 1:
            raise ValueError('Sub-anchor quote must match uniquely')
        m = matches[0]
        return evidence(path, text, sa + m.start(), sa + m.end())
    if 'table' in ref:
        tid = ref['table']
        if tid not in bound or bound[tid]['kind'] != 'table':
            raise ValueError('Unknown table anchor: ' + str(tid))
        grid = bound[tid]['grid']
        cell = grid[ref['row']][ref['col']]
        ca, cb = cell['span']
        if 'slice' in ref:
            s0, s1 = ref['slice']
            return evidence(path, text, ca + s0, ca + s1)
        if 'quote' in ref:
            matches = list(re.finditer(re.escape(ref['quote']), text[ca:cb]))
            if len(matches) != 1:
                raise ValueError('Table cell quote must match uniquely')
            m = matches[0]
            return evidence(path, text, ca + m.start(), ca + m.end())
        return evidence(path, text, ca, cb)
    if 'start_of' in ref:
        ev1 = resolve_evidence_ref(ref['start_of'], bound, context, entry)
        if 'end_of' in ref:
            ev2 = resolve_evidence_ref(ref['end_of'], bound, context, entry)
            return evidence(path, text, ev1['span'][0], ev2['span'][1])
        if 'before_caption_of' in ref:
            tid = ref['before_caption_of']
            if tid not in bound or bound[tid]['kind'] != 'table':
                raise ValueError('Unknown table anchor for before_caption_of: ' + str(tid))
            end_pos = bound[tid]['caption']['start']
            return evidence(path, text, ev1['span'][0], end_pos)
    raise ValueError('Unsupported evidence reference structure')


def _target_identity(context, targets):
    return [
        {
            'type': context.entries[tid]['type'],
            'ruleset': context.entries[tid]['ruleset'],
            'name_zh': context.entries[tid]['name_zh'],
            'name_en': context.entries[tid].get('name_en', ''),
        }
        for tid in sorted(targets)
        if tid in context.entries
    ]


def materialize_statements(context, entry, header, bound, kind, statements):
    """Instantiate structured statements against bound anchors and source lookups."""
    targets_meta = []
    gaps = []
    title_ev = evidence(entry['page_path'], context.pages[entry['page_path']], entry['start'], entry['title_end'])

    def resolve_when_owner(value, fallback_ev):
        if not isinstance(value, dict):
            return value
        if value.get('kind') == 'owner_class_level':
            owner = context.class_owner(entry)
            if owner:
                return {'kind': 'class_level', 'class': owner['name_zh'], 'owner_id': owner['id'], 'minimum': value['minimum']}
            gap = {'reason': 'unresolved_level_owner', 'evidence': fallback_ev}
            if gap not in gaps:
                gaps.append(gap)
            return None
        out = {}
        for k, v in value.items():
            if k in ('all', 'any') and isinstance(v, list):
                items = [resolve_when_owner(item, fallback_ev) for item in v]
                if any(item is None for item in items):
                    return None
                out[k] = items
            elif k == 'not':
                inner = resolve_when_owner(v, fallback_ev)
                if inner is None:
                    return None
                out[k] = inner
            else:
                out[k] = v
        return out

    def transform(node, *, enclosing_entry_type=None, role=None, fallback_ev=title_ev):
        if isinstance(node, list):
            return [transform(item, enclosing_entry_type=enclosing_entry_type, role=role, fallback_ev=fallback_ev) for item in node]
        if not isinstance(node, dict):
            return node
        out = {}
        cur_ev = fallback_ev
        if 'evidence_ref' in node:
            cur_ev = resolve_evidence_ref(node['evidence_ref'], bound, context, entry)
            out['evidence'] = cur_ev
        elif 'evidence_refs' in node:
            evs = [resolve_evidence_ref(r, bound, context, entry) for r in node['evidence_refs']]
            if evs:
                cur_ev = evs[0]
            out['evidence'] = evs
        next_type = node.get('entry_type', enclosing_entry_type)
        if node.get('kind') == 'entry' and 'resolution' not in node and ('name' in node or 'lookup_names' in node):
            ev = out.get('evidence') or cur_ev
            lookup_names = list(node.get('lookup_names') or [node['name']])
            name = node.get('name', lookup_names[0])
            target_kind = node.get('entry_type') or enclosing_entry_type or 'spell'
            resolution = resolve_lookup(entry, context, lookup_names, target_kind, ev)
            targets_meta.append({
                'role': role or 'statement',
                'entry_type': target_kind,
                'lookup_names': lookup_names,
                'status': resolution['status'],
                'targets': _target_identity(context, resolution['targets']),
            })
            result = {'kind': 'entry', 'name': name, 'resolution': resolution, 'evidence': ev}
            if 'lookup_names' in node or len(lookup_names) > 1:
                result['lookup_names'] = lookup_names
            return result
        for k, v in node.items():
            if k in ('evidence_ref', 'evidence_refs'):
                continue
            if k == 'when':
                resolved_when = resolve_when_owner(
                    transform(v, enclosing_entry_type=next_type, role=role, fallback_ev=cur_ev),
                    cur_ev,
                )
                if resolved_when is not None:
                    out['when'] = resolved_when
                continue
            out[k] = transform(v, enclosing_entry_type=next_type, role=role, fallback_ev=cur_ev)
        return out

    pre_expr = header['prerequisites'].get('expression')
    for term in condition_terms(pre_expr):
        if term.get('kind') == 'requirement':
            res = context.resolve(entry, term['name'], term['entry_type'])
            targets_meta.append({
                'role': 'prerequisite',
                'entry_type': term['entry_type'],
                'lookup_names': [term['name']],
                'status': res['status'],
                'targets': _target_identity(context, res['targets']),
            })

    contexts = {k: transform(v, role='context') for k, v in statements.get('contexts', {}).items()}
    grants = [transform(g, role='grant') for g in statements.get('grants', [])]
    modifiers = [transform(m, role='modifier') for m in statements.get('modifiers', [])]
    effects = [transform(e, role='effect') for e in statements.get('effects', [])]
    descriptions = []
    for item in statements.get('descriptions', []):
        ref = item['evidence_ref'] if isinstance(item, dict) and 'evidence_ref' in item else item
        descriptions.append(resolve_evidence_ref(ref, bound, context, entry))
    explicit_gaps = [transform(g, role='gap') for g in statements.get('gaps', [])]
    extra = {}
    if 'evidence_refs' in statements:
        extra['evidence'] = [resolve_evidence_ref(r, bound, context, entry) for r in statements['evidence_refs']]
    materialized = {
        'contexts': contexts,
        'grants': grants,
        'modifiers': modifiers,
        'effects': effects,
        'descriptions': descriptions,
        'gaps': gaps + explicit_gaps,
        **extra,
    }
    return materialized, targets_meta


def _paragraph_bounds(text, start, end, a):
    pa = text.rfind('\n', start, a) + 1
    pb = text.find('\n', a, end)
    return max(start, pa), (pb if pb != -1 else end)


def _clause_topology(context, entry, header, clauses, bound):
    text = context.pages[entry['page_path']]
    ordered = sorted(bound.values(), key=lambda b: (b['span'][0], b['span'][1], b['id']))
    paragraph_groups = []
    group_keys = []
    for item in ordered:
        key = ('table', item['span'][0]) if item['kind'] == 'table' else ('prose',) + _paragraph_bounds(text, header['body_start'], entry['end'], item['span'][0])
        if group_keys and group_keys[-1] == key:
            paragraph_groups[-1].append(item['id'])
        else:
            group_keys.append(key)
            paragraph_groups.append([item['id']])
    covered_indices = {i for b in bound.values() for i in b['clause_indices']}
    intervening = []
    if len(ordered) >= 2:
        spans = [b['span'] for b in ordered]
        for idx in range(len(spans) - 1):
            gap_a, gap_b = spans[idx][1], spans[idx + 1][0]
            if gap_a >= gap_b:
                continue
            for c_idx, clause in enumerate(clauses):
                if c_idx in covered_indices:
                    continue
                if gap_a <= clause['span'][0] and clause['span'][1] <= gap_b:
                    intervening.append(digest(clause['compact']))
    return {'paragraph_groups': paragraph_groups, 'intervening_clauses': intervening}


def compute_annotation_basis(context, entry, header, clauses, bound, kind, statements, scope=None, targets_meta=()):
    """Compute separate content and governing-context hashes without volatile offsets/IDs."""
    content = []
    for item in sorted(bound.values(), key=lambda b: (b['span'][0], b['span'][1], b['id'])):
        if item['kind'] == 'table':
            content.append({
                'id': item['id'],
                'kind': 'table',
                'caption_zh': item['caption']['name_zh'],
                'headers': [compact(h) for h in item['table']['headers']],
                'rows': [[compact(cell['text']) for cell in row] for row in item['grid']],
            })
        else:
            content.append({
                'id': item['id'],
                'kind': item['kind'],
                'compact': compact(item['evidence']['raw']),
            })
    content.append({
        'rule_kind': kind,
        'scope': scope,
        'statements': statements,
    })
    owner = context.class_owner(entry)
    class_owner = {'name_zh': owner['name_zh'], 'name_en': owner.get('name_en', ''), 'ruleset': owner['ruleset']} if owner else None
    pre_ev = header['prerequisites'].get('evidence')
    act_ev = header['activation'].get('evidence') if header.get('activation') else None
    ctx = {
        'subject_type': entry['type'],
        'ruleset': entry['ruleset'],
        'source_roles': sorted({
            context.source_by_id.get(sid, {}).get('reference_role', 'supplement')
            for sid in entry['source_ids']
        }),
        'class_owner': class_owner,
        'heading_level': header['heading_level'],
        'prerequisites_raw': compact(pre_ev['raw']) if pre_ev else None,
        'activation_raw': compact(act_ev['raw']) if act_ev else None,
        'parameters_raw': {k: compact(v['evidence']['raw']) for k, v in sorted(header['parameters'].items())},
        'clause_topology': _clause_topology(context, entry, header, clauses, bound),
        'targets': sorted(targets_meta, key=lambda t: (t['role'], t['entry_type'], tuple(t['lookup_names']))),
    }
    return {
        'schema': 1,
        'content': content,
        'content_sha256': digest(content),
        'context': ctx,
        'context_sha256': digest(ctx),
    }


def _spans_overlap(bound_a, bound_b):
    clauses_a = {i for b in bound_a.values() for i in b['clause_indices']}
    clauses_b = {i for b in bound_b.values() for i in b['clause_indices']}
    if clauses_a & clauses_b:
        return True
    for ba in bound_a.values():
        a0, a1 = ba['span']
        for bb in bound_b.values():
            b0, b1 = bb['span']
            if a0 < b1 and b0 < a1:
                return True
    return False


def evaluate_annotations(context, events):
    """Replay lifecycle state, subject/anchor binding, basis verification, and overlap conflicts."""
    states, _ = annotation_lifecycle(events)
    entry_cache = {}
    results = []
    by_entry_kind = {}

    for event in events:
        if event['operation'] != 'annotate':
            continue
        rev_id = event['id']
        lstate = states[rev_id]
        row = {
            'event': event,
            'lifecycle_state': lstate,
            'status': lstate,
            'subject_match': None,
            'subject_id': None,
            'bound': {},
            'materialized': None,
            'current_basis': None,
            'detail': None,
        }
        if lstate in {'revoked', 'superseded'}:
            results.append(row)
            continue

        subject_match, entry = resolve_subject(context, event['subject'])
        row['subject_match'] = subject_match
        row['subject_id'] = entry['id'] if entry else None
        if lstate == 'conflict':
            row['status'] = 'conflict'
            row['detail'] = 'Multiple active revisions exist for the same annotation_id'
            results.append(row)
            if entry:
                by_entry_kind.setdefault((entry['id'], event['kind']), []).append(row)
            continue

        if subject_match in {'missing', 'ambiguous'}:
            row['status'] = subject_match
            row['detail'] = 'Subject ' + subject_match
            results.append(row)
            continue

        if entry['id'] not in entry_cache:
            text = context.pages[entry['page_path']]
            hdr = entry_header(entry, text, context)
            excl = table_exclusions(entry, hdr['body_start'], context)
            cls = entry_clauses(entry, text, hdr['body_start'], excl)
            entry_cache[entry['id']] = (hdr, cls)
        header, clauses = entry_cache[entry['id']]

        bound, errors, header, clauses = bind_anchors(context, entry, event['anchors'], header, clauses)
        row['bound'] = bound
        if errors:
            row['status'] = 'ambiguous' if any(kind == 'ambiguous' for kind, _, _ in errors) else 'stale'
            row['detail'] = '; '.join(f'{aid}: {msg}' for _, aid, msg in errors)
            results.append(row)
            by_entry_kind.setdefault((entry['id'], event['kind']), []).append(row)
            continue

        try:
            materialized, targets_meta = materialize_statements(
                context, entry, header, bound, event['kind'], event['statements']
            )
        except ValueError as exc:
            row['status'] = 'stale'
            row['detail'] = str(exc)
            results.append(row)
            by_entry_kind.setdefault((entry['id'], event['kind']), []).append(row)
            continue

        current_basis = compute_annotation_basis(
            context, entry, header, clauses, bound, event['kind'], event['statements'],
            scope=event.get('scope'), targets_meta=targets_meta,
        )
        row['materialized'] = materialized
        row['current_basis'] = current_basis
        saved_basis = event['basis']
        content_ok = current_basis['content_sha256'] == saved_basis['content_sha256']
        context_ok = current_basis['context_sha256'] == saved_basis['context_sha256']
        if content_ok and context_ok:
            row['status'] = 'applied'
        else:
            row['status'] = 'stale'
            parts = []
            if not content_ok:
                parts.append('content_sha256 mismatch')
            if not context_ok:
                parts.append('context_sha256 mismatch')
            row['detail'] = ', '.join(parts)
        results.append(row)
        by_entry_kind.setdefault((entry['id'], event['kind']), []).append(row)

    for (_eid, _kind), group in by_entry_kind.items():
        if len(group) < 2:
            continue
        conflicted = set()
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                ri, rj = group[i], group[j]
                same_id = ri['event']['annotation_id'] == rj['event']['annotation_id']
                overlap = _spans_overlap(ri['bound'], rj['bound'])
                ids_i = {g['id'] for g in ri['event']['statements'].get('grants', [])}
                ids_j = {g['id'] for g in rj['event']['statements'].get('grants', [])}
                ctx_i = set(ri['event']['statements'].get('contexts', {}))
                ctx_j = set(rj['event']['statements'].get('contexts', {}))
                if same_id or overlap or (ids_i & ids_j) or (ctx_i & ctx_j):
                    conflicted.add(i)
                    conflicted.add(j)
        for idx in conflicted:
            group[idx]['status'] = 'conflict'
            group[idx]['detail'] = group[idx]['detail'] or 'Overlapping or colliding active annotations'

    return results


def _dedupe_gaps(gaps):
    seen = []
    for gap in gaps:
        if gap not in seen:
            seen.append(gap)
    return seen


def compose_records(context, evaluated, structural_keys=()):
    """Compose clause-level grant/procedure record payloads from evaluated annotations.

    Yields `(entry, kind, value, annotation_review_status)` for every annotated `(subject_id, kind)`.
    """
    grouped = {}
    for row in evaluated:
        sid = row.get('subject_id')
        if not sid or row['lifecycle_state'] in {'revoked', 'superseded'}:
            continue
        grouped.setdefault((sid, row['event']['kind']), []).append(row)

    structural = set(structural_keys)
    for (sid, kind), group in sorted(grouped.items()):
        entry = context.entries[sid]
        path = entry['page_path']
        text = context.pages[path]
        header = entry_header(entry, text, context)
        exclusions = table_exclusions(entry, header['body_start'], context)
        clauses = entry_clauses(entry, text, header['body_start'], exclusions)
        title_ev = evidence(path, text, entry['start'], entry['title_end'])

        applied = [r for r in group if r['status'] == 'applied']
        failing = [r for r in group if r['status'] in {'stale', 'conflict', 'ambiguous'}]
        applied.sort(key=lambda r: (
            min((b['span'][0] for b in r['bound'].values()), default=entry['end']),
            r['event']['annotation_id'],
        ))

        if (sid, kind) in structural:
            # Structural extractor (e.g. background feat line or subclass features) already owns this (entry, kind);
            # fail closed rather than merging uncoordinated claims.
            for r in group:
                r['status'] = 'conflict'
                r['detail'] = 'Conflicts with structural extraction for this entry and kind'
            failing = group
            applied = []

        gaps = list(header['gaps'])
        covered_clauses = set()
        scoped_intervals = []
        table_scopes = []
        all_table_grants = bool(applied) and not failing

        value = rule_value(
            prerequisites=header['prerequisites'],
            activation=header['activation'],
            parameters=header['parameters'],
        )
        top_evidence = []
        for r in applied:
            mat = r['materialized']
            value['contexts'].update(deepcopy(mat['contexts']))
            value['grants'].extend(deepcopy(mat['grants']))
            value['modifiers'].extend(deepcopy(mat['modifiers']))
            value['effects'].extend(deepcopy(mat['effects']))
            value['descriptions'].extend(deepcopy(mat['descriptions']))
            top_evidence.extend(deepcopy(mat.get('evidence', [])))
            gaps.extend(deepcopy(mat['gaps']))
            covered_clauses.update(i for b in r['bound'].values() for i in b['clause_indices'])
            scope = r['event'].get('scope')
            if isinstance(scope, dict) and scope.get('kind') == 'table_grants':
                sa = r['bound'][scope['start_anchor']]['span'][0]
                tb = r['bound'][scope['table_anchor']]
                sb = tb['span'][1]
                scoped_intervals.append((sa, sb))
                table_scopes.append({
                    'kind': 'table_grants',
                    'table_id': tb['table']['id'],
                    'evidence': evidence(path, text, sa, tb['table']['end']),
                })
            else:
                all_table_grants = False

        if all_table_grants and len(table_scopes) == 1:
            value['scope'] = table_scopes[0]
        if top_evidence:
            value['evidence'] = top_evidence

        for r in failing:
            covered_clauses.update(i for b in r['bound'].values() for i in b['clause_indices'])
            reason = 'conflicting_annotation' if r['status'] == 'conflict' else 'stale_annotation'
            first_ev = next((b['evidence'] for b in sorted(r['bound'].values(), key=lambda x: x['span'][0])), title_ev)
            gaps.append({'reason': reason, 'annotation_id': r['event']['annotation_id'], 'evidence': first_ev})

        for idx, clause in enumerate(clauses):
            if idx in covered_clauses:
                continue
            if all_table_grants and scoped_intervals:
                ca, cb = clause['span']
                if not any(sa < cb and ca < sb for sa, sb in scoped_intervals):
                    continue
            gaps.append({
                'reason': 'unannotated_clause',
                'evidence': evidence(path, text, clause['span'][0], clause['span'][1]),
            })

        gaps = _dedupe_gaps(gaps)
        if kind == 'procedure':
            binding_gaps = finish_effects(value['effects'], gaps, path, text, header['body_start'], entry['end'])
            gaps = _dedupe_gaps(gaps + binding_gaps)
        value['unparsed'] = gaps

        if any(r['status'] == 'conflict' for r in group):
            ann_status = 'conflict'
        elif any(r['status'] in {'stale', 'ambiguous'} for r in group):
            ann_status = 'stale'
        else:
            ann_status = 'applied'
        yield entry, kind, value, ann_status


def inspect_clauses(context, entry_id):
    """Return structural headers, table anchors, and clause spans for annotation authoring."""
    entry = context.entries[entry_id]
    path = entry['page_path']
    text = context.pages[path]
    header = entry_header(entry, text, context)
    exclusions = table_exclusions(entry, header['body_start'], context)
    clauses = entry_clauses(entry, text, header['body_start'], exclusions)
    tables = [
        {'caption': cap['name_zh'], 'start': t['start'], 'end': t['end'], 'headers': t['headers']}
        for t in context.tables
        if t['page_path'] == path and entry['title_end'] <= t['start'] < t['end'] <= entry['end']
        for cap in [
            next(
                (e for e in context.entries.values()
                 if e['page_path'] == path and e.get('table_span') == [t['start'], t['end']]),
                {'name_zh': ''},
            )
        ]
    ]
    return {
        'subject': subject_selector(context, entry),
        'subject_id': entry['id'],
        'header': header,
        'clauses': clauses,
        'tables': tables,
    }


def build_annotation_event(context, entry_id, kind, anchors, statements, reviewer, reason, *,
                           annotation_id=None, scope=None, supersedes=None):
    """Validate and package a pending annotation event ready for append_annotation_events."""
    if kind not in {'grant', 'procedure'}:
        raise ValueError('Annotation kind must be grant or procedure')
    entry = context.entries[entry_id]
    subject = subject_selector(context, entry)
    bound, errors, header, clauses = bind_anchors(context, entry, anchors)
    if errors:
        raise ValueError('Failed to bind anchors: ' + '; '.join(f'{aid}: {msg}' for _, aid, msg in errors))
    _, targets_meta = materialize_statements(context, entry, header, bound, kind, statements)
    basis = compute_annotation_basis(
        context, entry, header, clauses, bound, kind, statements,
        scope=scope, targets_meta=targets_meta,
    )
    if annotation_id is None:
        annotation_id = 'ann_' + digest([subject['type'], subject['ruleset'], subject['name_zh'], subject['name_en'], kind, [a['id'] for a in anchors]])[:20]
    event = {
        'operation': 'annotate',
        'annotation_id': annotation_id,
        'kind': kind,
        'subject': subject,
        'anchors': anchors,
        'statements': statements,
        'basis': basis,
        'reviewer': reviewer,
        'reason': reason,
    }
    if scope is not None:
        event['scope'] = scope
    if supersedes is not None:
        event['supersedes'] = supersedes
    return event
