"""Read-only review workflow, reference inspection, and live annotation diff preview."""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from anna.catalog.references import (
    _derive_missing_definitions,
    _init_decision_results,
    presentation,
    reference_basis,
    replay_annotations,
)
from anna.review.anchors import entry_selector
from anna.review.context import Context
from anna.review.search import search_candidates
from anna.review.store import (
    DEFAULT_ANNOTATIONS,
    annotation_lifecycle,
    annotation_snapshot,
    input_snapshot,
    load_annotations,
    read_effective_reference,
    reference_annotations,
)
from anna.serialization import digest


_PREVIEW_REF_FIELDS = (
    'status', 'reason', 'target_id', 'review_status', 'can_link', 'presentation', 'decision_ids',
)
_SENTENCE_DELIMITER_RE = re.compile(r'[。！？；]')


def _suggest_within(text, a, b):
    """Build the tightest unique surrounding sentence/line snippet for portable span anchors."""
    la = text.rfind('\n', 0, a) + 1
    lb = text.find('\n', b)
    if lb == -1:
        lb = len(text)
    sa = la
    for m in _SENTENCE_DELIMITER_RE.finditer(text[la:a]):
        sa = la + m.end()
    while sa < a and text[sa].isspace():
        sa += 1
    sb_match = _SENTENCE_DELIMITER_RE.search(text[b:lb])
    sb = b + sb_match.end() if sb_match else lb
    snippet = text[sa:sb].strip()
    if snippet and text.count(snippet) == 1:
        return snippet
    line = text[la:lb].strip()
    if line and text.count(line) == 1:
        return line
    pad = 40
    while pad <= 240:
        wa = max(0, a - pad)
        wb = min(len(text), b + pad)
        chunk = text[wa:wb].strip()
        if chunk and text.count(chunk) == 1:
            return chunk
        pad += 40
    return text[max(0, a - 160):min(len(text), b + 160)]


def read_reference(db, reference_id):
    """Load one effective reference row by ID, raising ValueError if missing."""
    ref = read_effective_reference(db, reference_id)
    if ref is None:
        raise ValueError('Reference not found; look up a valid reference ID')
    return ref


def preview_annotations(db, annotations_path=DEFAULT_ANNOTATIONS, *, pending_events=(), context=None):
    """Read-only diff preview of un-replayed annotations or prospective events without rebuilding the DB."""
    ann_file = Path(annotations_path).resolve()
    raw_bytes = ann_file.read_bytes() if ann_file.exists() else b''
    all_events, current_log_hash = load_annotations(ann_file)
    saved = annotation_snapshot(db)
    if not saved:
        log_state = 'missing_snapshot'
    elif saved['sha256'] == current_log_hash:
        log_state = 'current'
    elif len(all_events) >= saved['events'] and sha256(raw_bytes[:saved['bytes']]).hexdigest() == saved['sha256']:
        log_state = 'pending_replay'
    else:
        log_state = 'diverged'

    known_ids = {e['id'] for e in all_events}
    hypothetical = []
    for idx, item in enumerate(pending_events, 1):
        ev = deepcopy(dict(item))
        if not ev.get('id') or ev['id'] in known_ids:
            candidate = 'ann_rev_preview' if idx == 1 else f'ann_rev_preview_{idx}'
            while candidate in known_ids:
                idx += 1
                candidate = f'ann_rev_preview_{idx}'
            ev['id'] = candidate
        known_ids.add(ev['id'])
        ev.setdefault('schema', 1)
        ev.setdefault('created_at', '')
        hypothetical.append(ev)

    combined_events = list(all_events) + hypothetical
    ref_events = reference_annotations(combined_events)
    states, _ = annotation_lifecycle(ref_events)

    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    existing_decisions = (
        {r[0]: json.loads(r[1]) for r in db.execute("SELECT id, data FROM knowledge_annotations WHERE kind='reference'")}
        if 'knowledge_annotations' in tables else {}
    )
    existing_missing = (
        {r[0]: json.loads(r[1]) for r in db.execute('SELECT id, data FROM missing_definitions')}
        if 'missing_definitions' in tables else {}
    )
    ref_cols = {r[1] for r in db.execute('PRAGMA table_info(effective_references)')} if 'effective_references' in tables else set()
    eff_clause = 'is_effective=1' if 'is_effective' in ref_cols else '1=1'
    clickable_links_before = (
        db.execute(f'SELECT count(*) FROM effective_references WHERE {eff_clause} AND can_link=1').fetchone()[0]
        if 'effective_references' in tables else 0
    )

    affected_pages = {
        e['subject']['page_path']
        for e in ref_events
        if e['operation'] == 'annotate'
    } | {
        row['subject']['page_path']
        for row in existing_decisions.values()
        if isinstance(row.get('subject'), dict) and row['subject'].get('page_path')
    }

    if not affected_pages and not ref_events and not existing_decisions and not existing_missing:
        results = {}
        after_missing_list = []
        after_missing = {}
        reference_changes = []
        clickable_links_delta = 0
    else:
        before_refs_by_page = {p: [] for p in sorted(affected_pages)}
        after_refs_by_page = {p: [] for p in sorted(affected_pages)}
        if 'effective_references' in tables:
            for p in sorted(affected_pages):
                for raw, in db.execute(
                    f'SELECT data FROM effective_references WHERE {eff_clause} AND page_path=? ORDER BY start, id',
                    (p,),
                ):
                    ref = json.loads(raw)
                    if 'automatic' not in ref:
                        ref['automatic'] = {
                            'status': ref.get('status'),
                            'reason': ref.get('reason'),
                            'target_id': ref.get('target_id'),
                            'candidates': list(ref.get('candidates', ())),
                        }
                    before_refs_by_page[p].append(ref)
                    after_refs_by_page[p].append(deepcopy(ref))
        context = context or Context(db)
        results = _init_decision_results(ref_events, states)
        replay_annotations(after_refs_by_page, ref_events, context, results, states)
        for p in sorted(after_refs_by_page):
            presentation(after_refs_by_page[p], context, p)
        after_missing_list = _derive_missing_definitions(ref_events, results)
        after_missing = {r['id']: r for r in after_missing_list}

        reference_changes = []
        before_clicks = 0
        after_clicks = 0
        for p in sorted(affected_pages):
            before_by_id = {r['id']: r for r in before_refs_by_page[p]}
            before_clicks += sum(int(bool(r.get('can_link'))) for r in before_refs_by_page[p])
            after_clicks += sum(int(bool(r.get('can_link'))) for r in after_refs_by_page[p])
            for after in after_refs_by_page[p]:
                before = before_by_id.get(after['id'])
                if before is None:
                    continue
                before_view = {
                    k: ([] if k == 'decision_ids' and k not in before else before.get(k))
                    for k in _PREVIEW_REF_FIELDS
                }
                after_view = {k: after.get(k) for k in _PREVIEW_REF_FIELDS}
                if before_view != after_view:
                    reference_changes.append({
                        'id': after['id'],
                        'path': p,
                        'start': after['start'],
                        'end': after['end'],
                        'text': after['text'],
                        'signal': after['signal'],
                        'before': before_view,
                        'after': after_view,
                    })
        clickable_links_delta = after_clicks - before_clicks

    decision_changes = []
    for rev_id in sorted(existing_decisions.keys() | results.keys()):
        old = existing_decisions.get(rev_id)
        new = results.get(rev_id)
        old_sig = (old['status'], old.get('resolved_target_id'), old.get('detail'), old.get('changes', [])) if old else None
        new_sig = (new['status'], new.get('resolved_target_id'), new.get('detail'), new.get('changes', [])) if new else None
        if old_sig == new_sig:
            continue
        source = new or old
        decision_changes.append({
            'id': rev_id,
            'annotation_id': source['annotation_id'],
            'change': 'added' if old is None else 'removed' if new is None else 'changed',
            'ruling': source['event']['statements']['ruling'],
            'path': source['subject']['page_path'],
            'text': source['event']['anchors'][0]['quote'],
            'before_status': old['status'] if old else None,
            'after_status': new['status'] if new else None,
            'before_target_id': old.get('resolved_target_id') if old else None,
            'after_target_id': new.get('resolved_target_id') if new else None,
            'detail': source.get('detail'),
            'changes': source.get('changes', []),
        })

    missing_definition_changes = []
    for mid in sorted(existing_missing.keys() | after_missing.keys()):
        old = existing_missing.get(mid)
        new = after_missing.get(mid)
        old_sig = (old['status'], old.get('target_id'), old.get('latest_decision_id'), old.get('latest_status')) if old else None
        new_sig = (new['status'], new.get('target_id'), new.get('latest_decision_id'), new.get('latest_status')) if new else None
        if old_sig == new_sig:
            continue
        source = new or old
        missing_definition_changes.append({
            'id': mid,
            'reference_id': source.get('reference_id'),
            'path': source['path'],
            'text': source['text'],
            'change': 'added' if old is None else 'removed' if new is None else 'changed',
            'before_status': old['status'] if old else None,
            'after_status': new['status'] if new else None,
            'before_target_id': old.get('target_id') if old else None,
            'after_target_id': new.get('target_id') if new else None,
            'latest_decision_id': source.get('latest_decision_id'),
            'latest_status': source.get('latest_status'),
        })

    decisions_before = dict(Counter(r['status'] for r in existing_decisions.values()))
    decisions_after = dict(Counter(r['status'] for r in results.values()))
    missing_before = dict(Counter(r['status'] for r in existing_missing.values()))
    missing_after = dict(Counter(r['status'] for r in after_missing_list))
    blocking_decisions = sum(1 for r in results.values() if r['status'] in {'stale', 'missing', 'ambiguous', 'conflict'})

    return {
        'annotations_path': str(ann_file),
        'snapshot_sha256': saved['sha256'] if saved else None,
        'current_log_sha256': current_log_hash,
        'log_state': log_state,
        'hypothetical': hypothetical if pending_events else None,
        'summary': {
            'references_changed': len(reference_changes),
            'clickable_links_before': clickable_links_before,
            'clickable_links_after': clickable_links_before + clickable_links_delta,
            'clickable_links_delta': clickable_links_delta,
            'decisions_before': decisions_before,
            'decisions_after': decisions_after,
            'missing_definitions_before': missing_before,
            'missing_definitions_after': missing_after,
            'blocking_decisions': blocking_decisions,
        },
        'reference_changes': reference_changes,
        'decision_changes': decision_changes,
        'missing_definition_changes': missing_definition_changes,
    }


def inspect_reference(db, reference_id, target=None, *, context=None, path=DEFAULT_ANNOTATIONS, missing_definition=False, query=''):
    """Inspect a candidate reference, its basis, target text, and missing-definition candidate matches."""
    ref = read_reference(db, reference_id)
    context = context or Context(db)
    if target and target not in context.entries:
        raise ValueError('Target entry not found')
    if missing_definition and target:
        raise ValueError('Missing-definition checks cannot specify a target')
    ruling = 'missing_definition' if missing_definition else 'confirm' if target else 'non_reference'
    selected = context.entries.get(target)
    if ruling == 'confirm':
        statements = {'ruling': 'confirm', 'target': entry_selector(selected)}
    elif ruling == 'missing_definition':
        statements = {'ruling': 'missing_definition', 'search_query': query}
    else:
        statements = {'ruling': 'non_reference'}
    page_text = context.pages[ref['path']]
    anchor_id = 'r1'
    anchor = {
        'id': anchor_id,
        'kind': 'span',
        'quote': ref['text'],
        'within': _suggest_within(page_text, ref['start'], ref['end']),
        'signal': ref['signal'],
    }
    subject = {'kind': 'page', 'page_path': ref['path']}
    owner = context.enclosing_entry(ref['path'], ref['start'], ref['end'])
    if owner and owner['type'] != 'section':
        subject['entry'] = {
            'type': owner['type'],
            'ruleset': owner['ruleset'],
            'name_zh': owner['name_zh'],
            'name_en': owner.get('name_en', ''),
        }
    basis = reference_basis(context, ref, anchor_id, statements)
    candidates = [context.entries[i] for i in ref['automatic']['candidates'] if i in context.entries]
    target_text = context.pages[selected['page_path']][selected['start']:selected['end']] if selected else None
    return {
        'reference': ref,
        'excerpt': page_text[max(0, ref['start'] - 160):ref['end'] + 160],
        'subject': subject,
        'anchors': [anchor],
        'statements': statements,
        'target': selected,
        'target_text': target_text,
        'candidates': candidates,
        **({'candidate_search': search_candidates(db, reference_id, query=query, context=context)} if missing_definition else {}),
        'basis': basis,
        'content_sha256': basis['content_sha256'],
        'context_sha256': basis['context_sha256'],
        'snapshot_sha256': input_snapshot(db),
        'log_sha256': load_annotations(path)[1],
    }


def build_reference_event(
    db, context, reference_id, *, ruling, reviewer, reason, target=None, path=DEFAULT_ANNOTATIONS,
    expected_content_sha256=None, expected_context_sha256=None, search_query='',
    annotation_id=None, supersedes=None, events_by_id=None,
):
    """Build a validated reference annotation event ready for atomic log append."""
    if ruling not in {'confirm', 'non_reference', 'missing_definition'}:
        raise ValueError('Unsupported reference ruling')
    if target is not None and not isinstance(target, str):
        raise ValueError('Target ID must be a string')
    view = inspect_reference(
        db, reference_id, target, context=context, path=path,
        missing_definition=ruling == 'missing_definition', query=search_query,
    )
    if expected_content_sha256 is not None and expected_content_sha256 != view['content_sha256']:
        raise ValueError('Reference content basis changed; inspect again: ' + reference_id)
    if expected_context_sha256 is not None and expected_context_sha256 != view['context_sha256']:
        raise ValueError('Reference context basis changed; inspect again: ' + reference_id)
    ref = view['reference']
    if not ref['text'].strip() or ref.get('presentation') == 'definition_title':
        raise ValueError('This is an empty range or a definition entry; it cannot be reviewed as a body reference')
    if ruling == 'confirm' and (not target or not context.locatable(target)):
        raise ValueError('Confirming a reference requires a locatable definition entry')
    if ruling != 'confirm' and target is not None:
        raise ValueError('Non-reference or missing-definition rulings cannot have a target')
    anchor = view['anchors'][0]
    aid = annotation_id or (events_by_id or {}).get(supersedes, {}).get('annotation_id')
    if aid is None:
        aid = 'ann_ref_' + digest([ref['path'], anchor.get('within', ''), anchor['quote'], ref['signal']])[:20]
    event = {
        'operation': 'annotate',
        'annotation_id': aid,
        'kind': 'reference',
        'subject': view['subject'],
        'anchors': view['anchors'],
        'statements': view['statements'],
        'basis': view['basis'],
        'reviewer': reviewer,
        'reason': reason,
    }
    if supersedes:
        event['supersedes'] = supersedes
    return event, ref
