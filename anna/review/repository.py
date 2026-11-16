#!/usr/bin/env python3
"""Read and write source-bounded reference annotations; no browser review surface."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from anna.review.context import Context
from anna.review.store import (
    DEFAULT_ANNOTATIONS,
    append_annotation_events,
    annotation_lifecycle,
    load_annotations,
    locked,
    require_annotations_appendable,
)
from anna.paths import PROJECT


def save_annotation(db, path, subject_id, kind, reviewer='', reason='', *,
                    ruling=None, target=None, expected_content_sha256=None,
                    expected_context_sha256=None, search_query='', annotation_id=None,
                    supersedes=None):
    """Append one reference decision; other knowledge kinds no longer exist."""
    if kind != 'reference':
        raise ValueError('Only reference annotations are supported')
    if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reason, str) or not reason.strip():
        raise ValueError('Reviewer and reason are required')
    context = Context(db)
    with locked(path):
        require_annotations_appendable(db, path)
        events, log_hash = load_annotations(path)
        states, _ = annotation_lifecycle(events)
        from anna.catalog.references import bind_reference_anchor, build_reference_event
        by_id = {e['id']: e for e in events if e['operation'] == 'annotate'}
        pending, ref = build_reference_event(
            db, context, subject_id, ruling=ruling, reviewer=reviewer.strip(), reason=reason.strip(),
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
        return append_annotation_events(path, [pending], log_hash)[0]


def revoke_annotation(path, rev_id, reviewer, reason):
    if not reviewer.strip() or not reason.strip():
        raise ValueError('Reviewer and reason are required')
    with locked(path):
        events, log_hash = load_annotations(path)
        states, _ = annotation_lifecycle(events)
        if states.get(rev_id) not in {'active', 'conflict'}:
            raise ValueError('Only active or conflicting annotation revisions can be revoked')
        return append_annotation_events(
            path,
            [{'operation': 'revoke', 'target_revision_id': rev_id, 'reviewer': reviewer.strip(), 'reason': reason.strip()}],
            log_hash,
        )[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=PROJECT/'data/extracted/catalog.sqlite')
    parser.add_argument('--annotations', type=Path, default=DEFAULT_ANNOTATIONS)
    commands = parser.add_subparsers(dest='command', required=True)
    ann_list = commands.add_parser('annotations')
    ann_list.add_argument('--kind', choices=('reference',))
    ann_list.add_argument('--status')
    ann_list.add_argument('--limit', type=int, default=50)
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
    refs_cmd = commands.add_parser('references')
    refs_cmd.add_argument('--path', default='')
    refs_cmd.add_argument('--status', default='')
    refs_cmd.add_argument('--signal', default='')
    refs_cmd.add_argument('--review-status', default='')
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
    cand_cmd.add_argument('--limit', type=int, default=20)
    cand_cmd.add_argument('--offset', type=int, default=0)
    miss_cmd = commands.add_parser('missing')
    miss_cmd.add_argument('--status', default='')
    miss_cmd.add_argument('--path', default='')
    miss_cmd.add_argument('--limit', type=int, default=30)
    miss_cmd.add_argument('--offset', type=int, default=0)
    undo = commands.add_parser('revoke')
    undo.add_argument('id')
    undo.add_argument('--reviewer', required=True)
    undo.add_argument('--reason', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'revoke':
            result = revoke_annotation(args.annotations, args.id, args.reviewer, args.reason)
        else:
            with sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro', uri=True) as db:
                if args.command == 'annotations':
                    if not 1 <= args.limit <= 500:
                        raise ValueError('Limit must be between 1 and 500')
                    filters = ['kind=?']
                    values = ['reference']
                    if args.status:
                        filters.append('status=?')
                        values.append(args.status)
                    result = [json.loads(r[0]) for r in db.execute(
                        'SELECT data FROM knowledge_annotations WHERE '+' AND '.join(filters)+
                        ' ORDER BY annotation_id,id LIMIT ?', values+[args.limit])]
                elif args.command == 'annotate':
                    result = save_annotation(
                        db, args.annotations, args.id, 'reference',
                        reviewer=args.reviewer, reason=args.reason, ruling=args.ruling,
                        target=args.target, expected_content_sha256=args.expected_content_sha256,
                        expected_context_sha256=args.expected_context_sha256, search_query=args.query,
                        annotation_id=args.annotation_id, supersedes=args.supersedes,
                    )
                elif args.command == 'references':
                    from anna.presentation.references import query_references
                    result = query_references(db, path=args.path, status=args.status, signal=args.signal,
                                              review_status=args.review_status, limit=args.limit, offset=args.offset)
                elif args.command == 'ref-inspect':
                    from anna.catalog.references import inspect_reference
                    result = inspect_reference(db, args.id, args.target, path=args.annotations,
                                               missing_definition=args.missing_definition, query=args.query)
                elif args.command == 'candidates':
                    from anna.review.search import search_candidates
                    result = search_candidates(db, args.id, query=args.query, entry_type=args.type,
                                               ruleset=args.ruleset, source_id=args.source,
                                               limit=args.limit, offset=args.offset)
                elif args.command == 'missing':
                    from anna.presentation.references import query_missing_definitions
                    result = query_missing_definitions(db, status=args.status, path=args.path,
                                                       limit=args.limit, offset=args.offset)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, LookupError, sqlite3.Error, OSError) as exc:
        parser.exit(1, str(exc)+'\n')


if __name__ == '__main__':
    main()
