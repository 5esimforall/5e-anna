"""Difference disposition is a prerequisite for baseline promotion, not rule approval."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile

from anna.snapshots.baseline import (SCHEMA, active, atomic_json, connect, decisions_path,
                                materialize, object_refs, sync_directory, verify)
from anna.serialization import canonical, digest

POLICY = 'whole_snapshot_disposition_v1'


@contextmanager
def locked(path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def read_journal(path):
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise ValueError('Incomplete append-only journal')
    events = []; previous = None
    for line in raw.splitlines():
        event = json.loads(line)
        payload = {k: v for k, v in event.items() if k != 'id'}
        if event.get('previous') != previous or event.get('id') != 'event_' + digest(payload):
            raise ValueError('Changed or conflicting append-only journal')
        events.append(event); previous = event['id']
    return events


def append_event(path, payload):
    path = Path(path)
    previous = read_journal(path)
    event = {**payload, 'previous': previous[-1]['id'] if previous else None,
             'created_at': datetime.now(timezone.utc).isoformat()}
    event['id'] = 'event_' + digest(event)
    with path.open('ab') as stream:
        stream.write((canonical(event) + '\n').encode()); stream.flush(); os.fsync(stream.fileno())
    sync_directory(path.parent)
    return event


def parent_path(root, manifest):
    return (Path(root).parent / manifest['parent']['path']).resolve() if manifest['parent'] else None


def assess(root, output=None):
    from anna.snapshots.workflow import report
    root = Path(root).resolve()
    manifest = verify(root)
    parent = parent_path(root, manifest)
    old = verify(parent) if parent else None
    if old and old['id'] != manifest['parent']['id']:
        raise ValueError('Candidate parent identity changed')
    issues = {}
    def add(category, detail):
        value = {'category': category, 'detail': detail}
        value['id'] = 'issue_' + digest(value)
        issues[value['id']] = value
    # Always reconstruct/check the candidate, including for an initial bootstrap.
    database = materialize(root)
    if parent:
        with tempfile.TemporaryDirectory(prefix='anna-assess-') as tmp:
            result = report(database, parent, Path(tmp) / 'report', decisions_path(root))
            report_dir = Path(tmp) / 'report'
            accepted = {'asset_changes': {'provenance_only'}, 'source_pages': {'unchanged', 'relocated'},
                        'source_entries': {'unchanged', 'relocated'}, 'knowledge_dependencies': {'unchanged', 'relocated'},
                        'review_dependencies': {'eligible', 'history_retained'}, 'evidence_ranges': {'unchanged', 'relocated'}}
            for name, statuses in accepted.items():
                for line in (report_dir / (name + '.jsonl')).read_text().splitlines():
                    row = json.loads(line)
                    if row.get('status') not in statuses:
                        add(name, row)
            for name, stats in result['assets'].items():
                if stats['status'] != 'compared':
                    add('table_schema', {'table': name, 'before': old['tables'].get(name, {}).get('sql'),
                                         'after': manifest['tables'].get(name, {}).get('sql')})
            for row in result['view_changes']:
                add('view_schema', row)
        with closing(connect(materialize(parent))) as db:
            for kind, group in [('index', 'indexes'), ('trigger', 'triggers')]:
                previous = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL", (kind,)))
                current = manifest[group]
                for name in sorted(previous.keys() | current.keys()):
                    if previous.get(name) != current.get(name):
                        add(group, {'name': name, 'before': previous.get(name), 'after': current.get(name)})
        for group in ('source_files', 'counterpart_files'):
            previous = {n: r['sha256'] for n, r in old[group].items()}
            current = {n: r['sha256'] for n, r in manifest[group].items()}
            for name in sorted(previous.keys() | current.keys()):
                if previous.get(name) != current.get(name):
                    add(group, {'path': name, 'before': previous.get(name), 'after': current.get(name)})
        # Export duplication can disappear only with a proven all-values recipe.
        before_exports = {n: v['original_sha256'] for n, v in old['exports'].items()}
        after_exports = {n: v['original_sha256'] for n, v in manifest['exports'].items()}
        for name in sorted(before_exports.keys() | after_exports.keys()):
            if before_exports.get(name) != after_exports.get(name):
                add('export_artifact', {'path': name, 'before': before_exports.get(name), 'after': after_exports.get(name)})
        # Saved pending events and non-database history cannot hide behind projection equality.
        before_log = old['review_log']['sha256']
        if before_log != manifest['review_log']['sha256']:
            add('review_log', {'before': before_log, 'after': manifest['review_log']['sha256']})
    result = {'schema': 1, 'policy': POLICY, 'parent_id': old['id'] if old else None,
              'candidate_id': manifest['id'], 'issues': [issues[k] for k in sorted(issues)]}
    result['id'] = 'assessment_' + digest(result)
    if output:
        atomic_json(output, result)
    return result


def evidence_ids(root):
    manifest = verify(root)
    result = {manifest['id']}
    result.update(r['sha256'] for r in object_refs(manifest))
    return result


def dispositions(root, assessment):
    manifest = verify(root)
    parent = parent_path(root, manifest)
    evidence = evidence_ids(root) | (evidence_ids(parent) if parent else set())
    required = {i['id'] for i in assessment['issues']}
    covered = set()
    for event in read_journal(Path(root) / 'dispositions.jsonl'):
        if event.get('kind') != 'disposition' or event.get('assessment_id') != assessment['id']:
            raise ValueError('Stale or invalid disposition assessment')
        if event.get('action') not in {'accept_change', 'retain_unresolved'}:
            raise ValueError('Invalid disposition action')
        if not isinstance(event.get('reviewer'), str) or not event['reviewer'].strip() or not isinstance(event.get('reason'), str) or not event['reason'].strip():
            raise ValueError('Disposition requires a reviewer and a reason')
        refs = event.get('evidence', [])
        if not refs or not isinstance(refs, list) or any(r not in evidence for r in refs):
            raise ValueError('Disposition evidence is missing or outside these snapshots')
        ids = event.get('issues', [])
        if not ids or not isinstance(ids, list) or len(ids) != len(set(ids)) or not set(ids) <= required or set(ids) & covered:
            raise ValueError('Unknown, duplicate or conflicting disposition issues')
        covered.update(ids)
    return required - covered


def dispose(root, issue_ids, action, reviewer, reason, evidence):
    root = Path(root).resolve()
    with locked(root / '.dispositions.lock'):
        assessment = assess(root)
        missing = dispositions(root, assessment)
        if not issue_ids or len(set(issue_ids)) != len(issue_ids) or not set(issue_ids) <= missing:
            raise ValueError('Unknown, duplicate or conflicting disposition issues')
        if action not in {'accept_change', 'retain_unresolved'} or not reviewer.strip() or not reason.strip():
            raise ValueError('Disposition requires an action, reviewer and reason')
        manifest = verify(root); parent = parent_path(root, manifest)
        allowed = evidence_ids(root) | (evidence_ids(parent) if parent else set())
        if not evidence or any(r not in allowed for r in evidence):
            raise ValueError('Disposition evidence is missing or outside these snapshots')
        return append_event(root / 'dispositions.jsonl', {'kind': 'disposition', 'assessment_id': assessment['id'],
                            'issues': sorted(issue_ids), 'action': action, 'reviewer': reviewer.strip(), 'reason': reason.strip(),
                            'evidence': sorted(set(evidence))})


def promote(root, pointer, *, reviewer, reason, bootstrap=False):
    root, pointer = Path(root).resolve(), Path(pointer).resolve()
    if not reviewer.strip() or not reason.strip():
        raise ValueError('Activation requires a reviewer and reason')
    # Keep the compare-and-swap and the disposition check in one locked interval.
    with locked(pointer.parent / '.promotion.lock'), locked(root / '.dispositions.lock'):
        manifest = verify(root)
        current = active(pointer)
        journal = pointer.parent / 'promotions.jsonl'
        history = read_journal(journal)
        current_id = verify(current)['id'] if current else None
        if history and (not current or history[-1]['candidate_id'] != current_id or
                        json.loads(pointer.read_text()).get('promotion_id') != history[-1]['id']):
            raise ValueError('Active pointer and promotion history disagree; recover before proceeding')
        if current_id == manifest['id']:
            return json.loads(pointer.read_text())
        expected = manifest['parent']['id'] if manifest['parent'] else None
        if expected != current_id:
            raise ValueError('Candidate parent is no longer the active baseline')
        if current is None and (not bootstrap or history):
            raise ValueError('Initial activation requires explicit bootstrap and no promotion history')
        if bootstrap and current is not None:
            raise ValueError('Bootstrap cannot bypass an existing baseline')
        assessment = assess(root)
        missing = dispositions(root, assessment)
        if missing:
            raise ValueError(f'Unresolved baseline differences: {len(missing)}; record dispositions before activation')
        event = append_event(journal, {'kind': 'promotion', 'parent_id': current_id, 'candidate_id': manifest['id'],
                             'assessment_id': assessment['id'], 'dispositions_sha256': digest(read_journal(root / 'dispositions.jsonl')),
                             'retained_unresolved': [e['id'] for e in read_journal(root / 'dispositions.jsonl') if e['action'] == 'retain_unresolved'],
                             'reviewer': reviewer.strip(), 'reason': reason.strip()})
        value = {'schema': SCHEMA, 'path': os.path.relpath(root, pointer.parent), 'id': manifest['id'], 'promotion_id': event['id']}
        atomic_json(pointer, value)
        return value
