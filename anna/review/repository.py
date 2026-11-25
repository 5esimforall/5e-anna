#!/usr/bin/env python3
"""Read and write source-bounded reference annotations; no browser review surface."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys

from anna.catalog.references import bind_reference_anchor
from anna.review.context import Context
from anna.review.search import search_candidates
from anna.review.store import (
    DEFAULT_ANNOTATIONS,
    append_annotation_events,
    annotation_lifecycle,
    check_rebuild_gate,
    load_annotations,
    locked,
    reference_annotations,
    require_annotations_appendable,
)
from anna.review.workflow import (
    build_reference_event,
    inspect_reference,
    preview_annotations,
)
from anna.paths import BOOK, PROJECT


def _require_reviewer_and_reason(reviewer, reason):
    if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reason, str) or not reason.strip():
        raise ValueError('Reviewer and reason are required')
    return reviewer.strip(), reason.strip()


def _prepare_reference_event(db, path, subject_id, kind, reviewer='', reason='', *,
                             ruling=None, target=None, expected_content_sha256=None,
                             expected_context_sha256=None, search_query='', annotation_id=None,
                             supersedes=None, context=None):
    if kind != 'reference':
        raise ValueError('Only reference annotations are supported')
    reviewer, reason = _require_reviewer_and_reason(reviewer, reason)
    context = context or Context(db)
    require_annotations_appendable(db, path)
    events, log_hash = load_annotations(path)
    states, _ = annotation_lifecycle(events)
    by_id = {e['id']: e for e in events if e['operation'] == 'annotate'}
    pending, ref = build_reference_event(
        db, context, subject_id, ruling=ruling, reviewer=reviewer, reason=reason,
        target=target, path=path, expected_content_sha256=expected_content_sha256,
        expected_context_sha256=expected_context_sha256, search_query=search_query,
        annotation_id=annotation_id, supersedes=supersedes, events_by_id=by_id,
    )
    active = [
        e['id'] for e in events
        if e['operation'] == 'annotate'
        and e['kind'] == 'reference'
        and states[e['id']] in {'active', 'conflict'}
        and (
            e['annotation_id'] == pending['annotation_id']
            or (
                e['subject']['page_path'] == ref['path']
                and bind_reference_anchor(context, ref['path'], e['anchors'][0], [ref])[0] == 'bound'
            )
        )
    ]
    if sorted(active) != ([supersedes] if supersedes else []):
        raise ValueError('Supersede the active annotation revision or revoke competing revisions')
    return pending, ref, log_hash, context


def save_annotation(db, path, subject_id, kind, reviewer='', reason='', *,
                    ruling=None, target=None, expected_content_sha256=None,
                    expected_context_sha256=None, search_query='', annotation_id=None,
                    supersedes=None):
    """Append one reference decision; other knowledge kinds no longer exist."""
    context = Context(db)
    with locked(path):
        pending, _, log_hash, _ = _prepare_reference_event(
            db, path, subject_id, kind, reviewer=reviewer, reason=reason,
            ruling=ruling, target=target, expected_content_sha256=expected_content_sha256,
            expected_context_sha256=expected_context_sha256, search_query=search_query,
            annotation_id=annotation_id, supersedes=supersedes, context=context,
        )
        return append_annotation_events(path, [pending], log_hash)[0]


def preview_annotate(db, path, subject_id, kind, reviewer='', reason='', *,
                     ruling=None, target=None, expected_content_sha256=None,
                     expected_context_sha256=None, search_query='', annotation_id=None,
                     supersedes=None):
    """Dry-run one prospective reference annotation and preview its replay diff without writing."""
    pending, _, _, context = _prepare_reference_event(
        db, path, subject_id, kind, reviewer=reviewer, reason=reason,
        ruling=ruling, target=target, expected_content_sha256=expected_content_sha256,
        expected_context_sha256=expected_context_sha256, search_query=search_query,
        annotation_id=annotation_id, supersedes=supersedes,
    )
    return preview_annotations(db, path, pending_events=[pending], context=context)


def _validate_revoke(path, rev_id, reviewer, reason):
    reviewer, reason = _require_reviewer_and_reason(reviewer, reason)
    events, log_hash = load_annotations(path)
    by_id = {e['id']: e for e in events if e['operation'] == 'annotate'}
    target = by_id.get(rev_id)
    if target is None:
        raise ValueError('Only active or conflicting annotation revisions can be revoked')
    if target['kind'] != 'reference':
        raise ValueError('Only reference annotations can be revoked')
    ref_events = reference_annotations(events)
    states, _ = annotation_lifecycle(ref_events)
    if states.get(rev_id) not in {'active', 'conflict'}:
        raise ValueError('Only active or conflicting annotation revisions can be revoked')
    pending = {
        'operation': 'revoke',
        'target_revision_id': rev_id,
        'reviewer': reviewer,
        'reason': reason,
    }
    return pending, log_hash


def revoke_annotation(path, rev_id, reviewer, reason):
    _require_reviewer_and_reason(reviewer, reason)
    with locked(path):
        pending, log_hash = _validate_revoke(path, rev_id, reviewer, reason)
        return append_annotation_events(path, [pending], log_hash)[0]


def preview_revoke(db, path, rev_id, reviewer, reason):
    """Dry-run revoking one active/conflicting reference revision without writing."""
    pending, _ = _validate_revoke(path, rev_id, reviewer, reason)
    return preview_annotations(db, path, pending_events=[pending])


def query_log_annotations(path=DEFAULT_ANNOTATIONS, *, db=None, status='', annotation_id='',
                          page_path='', ruling='', limit=50, offset=0):
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError('Annotation query range is invalid')
    events, log_hash = load_annotations(path)
    ref_events = reference_annotations(events)
    states, _ = annotation_lifecycle(ref_events)
    revoked_by = {
        e['target_revision_id']: e['id']
        for e in ref_events
        if e['operation'] == 'revoke'
    }
    superseded_by = {}
    for e in ref_events:
        if e['operation'] == 'annotate' and e.get('supersedes'):
            superseded_by.setdefault(e['supersedes'], []).append(e['id'])
    db_ids = None
    if db is not None:
        has_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_annotations'").fetchone()
        db_ids = (
            {r[0] for r in db.execute("SELECT id FROM knowledge_annotations WHERE kind='reference'")}
            if has_table else set()
        )
    rows_all = []
    for e in ref_events:
        if e['operation'] != 'annotate':
            continue
        lstate = states[e['id']]
        rows_all.append({
            'id': e['id'],
            'annotation_id': e['annotation_id'],
            'kind': 'reference',
            'lifecycle_state': lstate,
            'ruling': e['statements']['ruling'],
            'path': e['subject']['page_path'],
            'text': e['anchors'][0]['quote'],
            'signal': e['anchors'][0].get('signal'),
            'reviewer': e['reviewer'],
            'reason': e['reason'],
            'created_at': e['created_at'],
            'supersedes': e.get('supersedes'),
            'superseded_by': superseded_by.get(e['id'], []),
            'revoked_by': revoked_by.get(e['id']),
            **({'in_db': e['id'] in db_ids} if db_ids is not None else {}),
            'event': e,
        })
    counts = dict(Counter(r['lifecycle_state'] for r in rows_all))
    filtered = [
        r for r in rows_all
        if (not status or r['lifecycle_state'] == status)
        and (not annotation_id or r['annotation_id'] == annotation_id)
        and (not page_path or r['path'] == page_path)
        and (not ruling or r['ruling'] == ruling)
    ]
    filtered.sort(key=lambda r: (r['annotation_id'], r['created_at'], r['id']))
    return {
        'source': 'log',
        'path': str(Path(path).resolve()),
        'log_sha256': log_hash,
        'counts': counts,
        'total': len(filtered),
        'offset': offset,
        'limit': limit,
        'rows': filtered[offset:offset + limit],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=PROJECT/'data/extracted/catalog.sqlite')
    parser.add_argument('--annotations', type=Path, default=DEFAULT_ANNOTATIONS)
    commands = parser.add_subparsers(dest='command', required=True)
    ann_list = commands.add_parser('annotations')
    ann_list.add_argument('--kind', choices=('reference',))
    ann_list.add_argument('--status', default='')
    ann_list.add_argument('--annotation-id', default='')
    ann_list.add_argument('--path', default='')
    ann_list.add_argument('--ruling', choices=('confirm', 'non_reference', 'missing_definition', ''), default='')
    ann_list.add_argument('--from-log', action='store_true')
    ann_list.add_argument('--limit', type=int, default=50)
    ann_list.add_argument('--offset', type=int, default=0)
    ann_cmd = commands.add_parser('annotate')
    ann_cmd.add_argument('id')
    ann_cmd.add_argument('--kind', required=True, choices=('reference',))
    ann_cmd.add_argument('--ruling', required=True, choices=('confirm', 'non_reference', 'missing_definition'))
    ann_cmd.add_argument('--target')
    ann_cmd.add_argument('--query', default='')
    ann_cmd.add_argument('--expected-content-sha256')
    ann_cmd.add_argument('--expected-context-sha256')
    ann_cmd.add_argument('--annotation-id')
    ann_cmd.add_argument('--reviewer', required=True)
    ann_cmd.add_argument('--reason', required=True)
    ann_cmd.add_argument('--supersedes')
    ann_cmd.add_argument('--preview', action='store_true')
    refs_cmd = commands.add_parser('references')
    refs_cmd.add_argument('--path', default='')
    refs_cmd.add_argument('--status', default='')
    refs_cmd.add_argument('--signal', default='')
    refs_cmd.add_argument('--review-status', default='')
    refs_cmd.add_argument('--reason', default='')
    refs_cmd.add_argument('--kind', default='')
    refs_cmd.add_argument('--limit', type=int, default=30)
    refs_cmd.add_argument('--offset', type=int, default=0)
    ref_inspect = commands.add_parser('ref-inspect')
    ref_inspect.add_argument('id')
    ref_inspect.add_argument('--target')
    ref_inspect.add_argument('--missing-definition', action='store_true')
    ref_inspect.add_argument('--query', default='')
    cand_cmd = commands.add_parser('candidates')
    cand_cmd.add_argument('id')
    cand_cmd.add_argument('--query', default='')
    cand_cmd.add_argument('--type', default='')
    cand_cmd.add_argument('--ruleset', default='')
    cand_cmd.add_argument('--source', default='')
    cand_cmd.add_argument('--locatable', action='store_true')
    cand_cmd.add_argument('--exclude-sections', action='store_true')
    cand_cmd.add_argument('--limit', type=int, default=20)
    cand_cmd.add_argument('--offset', type=int, default=0)
    miss_cmd = commands.add_parser('missing')
    miss_cmd.add_argument('--status', default='')
    miss_cmd.add_argument('--path', default='')
    miss_cmd.add_argument('--text', default='')
    miss_cmd.add_argument('--limit', type=int, default=30)
    miss_cmd.add_argument('--offset', type=int, default=0)
    mc_cmd = commands.add_parser('missing-candidates')
    mc_cmd.add_argument('--signal', default='')
    mc_cmd.add_argument('--kind', default='')
    mc_cmd.add_argument('--path', default='')
    mc_cmd.add_argument('--reason', default='')
    mc_cmd.add_argument('--text', default='')
    mc_cmd.add_argument('--limit', type=int, default=30)
    mc_cmd.add_argument('--offset', type=int, default=0)
    undo = commands.add_parser('revoke')
    undo.add_argument('id')
    undo.add_argument('--reviewer', required=True)
    undo.add_argument('--reason', required=True)
    undo.add_argument('--preview', action='store_true')
    commands.add_parser('preview')
    gate_cmd = commands.add_parser('gate')
    gate_cmd.add_argument('--book', type=Path, default=None)
    gate_cmd.add_argument('--no-book', action='store_true')
    gate_cmd.add_argument('--strict', action='store_true')
    args = parser.parse_args()
    exit_code = 0
    try:
        if args.command == 'revoke' and not args.preview:
            result = revoke_annotation(args.annotations, args.id, args.reviewer, args.reason)
        elif args.command == 'annotations' and args.from_log and not args.db.is_file():
            result = query_log_annotations(
                args.annotations, status=args.status, annotation_id=args.annotation_id,
                page_path=args.path, ruling=args.ruling, limit=args.limit, offset=args.offset,
            )
        else:
            with sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro', uri=True) as db:
                if args.command == 'annotations':
                    if args.from_log:
                        result = query_log_annotations(
                            args.annotations, db=db, status=args.status, annotation_id=args.annotation_id,
                            page_path=args.path, ruling=args.ruling, limit=args.limit, offset=args.offset,
                        )
                    else:
                        from anna.presentation.references import query_decisions
                        result = query_decisions(
                            db, status=args.status, annotation_id=args.annotation_id,
                            path=args.path, ruling=args.ruling, limit=args.limit, offset=args.offset,
                        )
                elif args.command == 'annotate':
                    fn = preview_annotate if args.preview else save_annotation
                    result = fn(
                        db, args.annotations, args.id, 'reference',
                        reviewer=args.reviewer, reason=args.reason, ruling=args.ruling,
                        target=args.target, expected_content_sha256=args.expected_content_sha256,
                        expected_context_sha256=args.expected_context_sha256, search_query=args.query,
                        annotation_id=args.annotation_id, supersedes=args.supersedes,
                    )
                elif args.command == 'revoke':
                    result = preview_revoke(db, args.annotations, args.id, args.reviewer, args.reason)
                elif args.command == 'preview':
                    result = preview_annotations(db, args.annotations)
                elif args.command == 'gate':
                    book = args.book
                    if not args.no_book and book is None and args.db.resolve() == (PROJECT / 'data/extracted/catalog.sqlite').resolve():
                        book = BOOK
                    result = check_rebuild_gate(db, args.annotations, db_path=args.db, book=book, strict=args.strict)
                    if not result['ok']:
                        exit_code = 1
                elif args.command == 'references':
                    from anna.presentation.references import query_references
                    result = query_references(db, path=args.path, status=args.status, signal=args.signal,
                                              review_status=args.review_status, reason=args.reason, kind=args.kind,
                                              limit=args.limit, offset=args.offset)
                elif args.command == 'ref-inspect':
                    result = inspect_reference(db, args.id, args.target, path=args.annotations,
                                               missing_definition=args.missing_definition, query=args.query)
                elif args.command == 'candidates':
                    result = search_candidates(db, args.id, query=args.query, entry_type=args.type,
                                               ruleset=args.ruleset, source_id=args.source,
                                               locatable_only=args.locatable, exclude_sections=args.exclude_sections,
                                               limit=args.limit, offset=args.offset)
                elif args.command == 'missing':
                    from anna.presentation.references import query_missing_definitions
                    result = query_missing_definitions(db, status=args.status, path=args.path,
                                                       text=args.text, limit=args.limit, offset=args.offset)
                elif args.command == 'missing-candidates':
                    from anna.presentation.references import query_missing_candidates
                    result = query_missing_candidates(db, signal=args.signal, kind=args.kind, path=args.path,
                                                      reason=args.reason, text=args.text,
                                                      limit=args.limit, offset=args.offset)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if exit_code:
            sys.exit(exit_code)
    except (ValueError, LookupError, sqlite3.Error, OSError) as exc:
        parser.exit(1, str(exc)+'\n')


if __name__ == '__main__':
    main()
