#!/usr/bin/env python3
"""Inspect, save and revoke scoped reference decisions; run make build to publish them."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

from anna.paths import PROJECT
from anna.review.search import search_candidates
from anna.catalog.references import ReviewContext, query_decisions, query_references, query_missing_definitions
from anna.serialization import digest
from anna.review.store import DEFAULT_DECISIONS, append_event, append_events, lifecycle, load_events, locked, require_appendable, input_snapshot


def read_reference(db, reference_id):
    row = db.execute('SELECT data FROM effective_references WHERE id=?', (reference_id,)).fetchone()
    if not row: raise ValueError('Reference not found; look up a valid reference ID')
    return json.loads(row[0])


def inspect(db, reference_id, target=None, *, context=None, path=DEFAULT_DECISIONS, missing_definition=False, query=''):
    ref = read_reference(db, reference_id)
    context = context or ReviewContext(db)
    if target and target not in context.entries: raise ValueError('Target entry not found')
    if missing_definition and target: raise ValueError('Missing-definition checks cannot specify a target')
    basis = context.basis(ref, target, search_query=query if missing_definition else None)
    page = context.pages[ref['path']]['text']
    candidates = [context.entries[i] for i in ref['automatic']['candidates'] if i in context.entries]
    selected = context.entries.get(target)
    target_text = context.pages[selected['page_path']]['text'][selected['start']:selected['end']] if selected else None
    return {'reference': ref, 'excerpt': page[max(0, ref['start']-160):ref['end']+160],
            'target': selected, 'target_text': target_text, 'candidates': candidates,
            **({'candidate_search':search_candidates(db,reference_id,query=query,context=context)} if missing_definition else {}),
            'basis': basis, 'basis_sha256': digest(basis),
            'snapshot_sha256': input_snapshot(db), 'log_sha256': load_events(path)[1]}


def save(db, path, *, operation, reference_id, target, expected_basis, reviewer, reason, supersedes=None, search_query=''):
    return save_batch(db, path, [{'operation': operation, 'reference_id': reference_id, 'target': target,
        'expected_basis': expected_basis, 'reviewer': reviewer, 'reason': reason, 'supersedes': supersedes, 'search_query': search_query}])[0]


def save_batch(db, path, items, *, expected_snapshot=None, expected_log=None):
    if not isinstance(items, list) or not items: raise ValueError('Batch must be a non-empty list of reviews')
    with locked(path):
        require_appendable(db, path)
        if expected_snapshot is not None and expected_snapshot != input_snapshot(db):
            raise ValueError('Build snapshot changed; inspect the whole batch again')
        events, log_hash = load_events(path)
        if expected_log is not None and expected_log != log_hash:
            raise ValueError('Review log changed; re-check the batch before saving')
        states = lifecycle(events)
        context = ReviewContext(db)
        pending = []; seen = set()
        for item in items:
            if not isinstance(item, dict): raise ValueError('Each review must be an object')
            operation = item.get('operation'); reference_id = item.get('reference_id'); target = item.get('target')
            reviewer = item.get('reviewer'); reason = item.get('reason'); supersedes = item.get('supersedes')
            if operation not in {'confirm', 'non_reference', 'missing_definition'}: raise ValueError('Unsupported review operation')
            if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reason, str) or not reason.strip():
                raise ValueError('Reviewer and reason cannot be empty')
            if not isinstance(reference_id, str) or reference_id in seen: raise ValueError('Batch reference is missing or duplicated')
            if target is not None and not isinstance(target, str): raise ValueError('Target ID must be a string')
            seen.add(reference_id)
            view = inspect(db, reference_id, target, context=context, path=path,
                           missing_definition=operation=='missing_definition', query=item.get('search_query',''))
            if view['basis_sha256'] != item.get('expected_basis'): raise ValueError('Review basis changed; inspect again: '+reference_id)
            ref = view['reference']
            if not view['basis']['binding']['text'].strip() or ref['presentation'] == 'definition_title':
                raise ValueError('This is an empty range or a definition entry; it cannot be reviewed as a body reference')
            if operation == 'confirm' and not view['basis']['target_locatable']:
                raise ValueError('Confirming a reference requires a locatable definition entry')
            if operation in {'non_reference','missing_definition'} and target is not None: raise ValueError('Non-reference or missing-definition decisions cannot have a target')
            active = [e['id'] for e in events if states.get(e['id']) == 'active' and e.get('binding', {}).get('reference_id') == reference_id]
            if active != ([supersedes] if supersedes else []):
                raise ValueError('This reference already has an active or pending decision; supersede the only decision, or revoke them one by one if they conflict')
            pending.append({'operation': operation, 'reviewer': reviewer.strip(), 'reason': reason.strip(),
                'binding': view['basis']['binding'], 'basis': view['basis'], 'basis_sha256': view['basis_sha256'],
                'reviewed_snapshot': {'automatic': ref['automatic'], 'evidence': ref['evidence'],
                                     'target': view['target'], 'target_text': view['target_text']},
                'target_id': target, 'supersedes': supersedes,
                **({'search_query':item.get('search_query',''), 'candidate_search':view['candidate_search']} if operation=='missing_definition' else {})})
        # A single durable replace: any invalid item leaves the log completely unchanged.
        return append_events(path, pending, log_hash)


def revoke(path, decision_id, reviewer, reason):
    if not reviewer.strip() or not reason.strip(): raise ValueError('Reviewer and reason cannot be empty')
    with locked(path):
        events, log_hash = load_events(path)
        if lifecycle(events).get(decision_id) != 'active': raise ValueError('Only decisions that are not revoked or superseded can be revoked')
        return append_event(path, {'operation': 'revoke', 'decision_id': decision_id,
                                  'reviewer': reviewer.strip(), 'reason': reason.strip()}, log_hash)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=PROJECT/'data/extracted/catalog.sqlite')
    parser.add_argument('--decisions', type=Path, default=DEFAULT_DECISIONS)
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='List the references valid in the current build')
    for flag in ('path', 'status', 'signal', 'review-status'): listing.add_argument('--'+flag, default='')
    listing.add_argument('--limit', type=int, default=30); listing.add_argument('--offset', type=int, default=0)
    decisions = commands.add_parser('decisions', help='List replay results, including stale, missing, conflicting and revoked history')
    decisions.add_argument('--status', default=''); decisions.add_argument('--limit', type=int, default=30)
    decisions.add_argument('--offset', type=int, default=0)
    viewing = commands.add_parser('inspect', help='Check the source text, target and basis fingerprint required for saving')
    viewing.add_argument('reference'); viewing.add_argument('--target')
    viewing.add_argument('--missing-definition', action='store_true'); viewing.add_argument('--query', default='')
    candidates = commands.add_parser('candidates', help='Search review targets read-only; ranked by matching evidence, never auto-approved')
    candidates.add_argument('reference'); candidates.add_argument('--query',default='')
    candidates.add_argument('--type',default=''); candidates.add_argument('--ruleset',default=''); candidates.add_argument('--source',default='')
    candidates.add_argument('--limit',type=int,default=20); candidates.add_argument('--offset',type=int,default=0)
    missing = commands.add_parser('missing', help='Show the traceable missing-definition queue and its history')
    missing.add_argument('--status',default=''); missing.add_argument('--path',default='')
    missing.add_argument('--limit',type=int,default=30); missing.add_argument('--offset',type=int,default=0)
    for name in ('confirm', 'non-reference', 'missing-definition'):
        cmd = commands.add_parser(name)
        cmd.add_argument('reference'); cmd.add_argument('--basis', required=True)
        if name == 'confirm': cmd.add_argument('--target', required=True)
        if name == 'missing-definition': cmd.add_argument('--query',default='')
        cmd.add_argument('--supersedes'); cmd.add_argument('--reviewer', required=True); cmd.add_argument('--reason', required=True)
    batch = commands.add_parser('batch', help='Atomically save a batch of reviews for one build snapshot and replay it')
    batch.add_argument('file', type=Path, help='JSON file with snapshot_sha256, log_sha256 and items')
    undo = commands.add_parser('revoke')
    undo.add_argument('decision'); undo.add_argument('--reviewer', required=True); undo.add_argument('--reason', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'revoke':
            result = revoke(args.decisions, args.decision, args.reviewer, args.reason)
        else:
            with closing(sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro', uri=True)) as db:
                if args.command == 'list':
                    result = query_references(db, path=args.path, status=args.status, signal=args.signal,
                                              review_status=args.review_status, limit=args.limit, offset=args.offset)
                elif args.command == 'decisions': result = query_decisions(db, status=args.status, limit=args.limit, offset=args.offset)
                elif args.command == 'inspect': result = inspect(db, args.reference, args.target, path=args.decisions,
                    missing_definition=args.missing_definition,query=args.query)
                elif args.command == 'candidates': result = search_candidates(db,args.reference,query=args.query,
                    entry_type=args.type,ruleset=args.ruleset,source_id=args.source,limit=args.limit,offset=args.offset)
                elif args.command == 'missing': result = query_missing_definitions(db,status=args.status,path=args.path,
                    limit=args.limit,offset=args.offset)
                elif args.command == 'batch':
                    batch = json.loads(args.file.read_text())
                    if not isinstance(batch, dict) or not all(isinstance(batch.get(k), str) and batch[k] for k in ('snapshot_sha256', 'log_sha256')):
                        raise ValueError('Batch is missing the build snapshot or log fingerprint')
                    result = save_batch(db, args.decisions, batch.get('items'),
                                        expected_snapshot=batch['snapshot_sha256'], expected_log=batch['log_sha256'])
                else:
                    result = save(db, args.decisions, operation=args.command.replace('-', '_'), reference_id=args.reference,
                                  target=getattr(args, 'target', None), expected_basis=args.basis, reviewer=args.reviewer,
                                  reason=args.reason, supersedes=args.supersedes, search_query=getattr(args,'query',''))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command in {'confirm', 'non-reference', 'missing-definition', 'revoke', 'batch'}:
            print('Saved review events; run make build to replay them and regenerate the linked book, then restart make up.',file=sys.stderr)
    except (OSError, ValueError, LookupError, sqlite3.Error) as exc: parser.exit(1, str(exc)+'\n')


if __name__ == '__main__': main()
