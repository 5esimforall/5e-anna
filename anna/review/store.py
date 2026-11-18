"""Versioned, append-only structured annotations. Generated databases are never the authority."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from anna.paths import PROJECT
from anna.serialization import canonical, digest

DEFAULT_ANNOTATIONS = PROJECT / 'annotations/annotations.jsonl'
ANNOTATION_OPERATIONS = {'annotate', 'revoke'}
ANNOTATION_KINDS = {'grant', 'procedure', 'reference'}
REFERENCE_RULINGS = {'confirm', 'non_reference', 'missing_definition'}
REFERENCE_SIGNALS = {'explicit', 'index', 'format', 'plain', 'table', 'field'}


def pipeline_hash():
    return sha256(b''.join(p.read_bytes() for p in sorted((PROJECT / 'anna').rglob('*.py')))).hexdigest()


EXTRACTION_MODULES = (
    'serialization.py', 'source/pages.py', 'catalog/build.py', 'source/html.py',
    'catalog/signatures.py', 'catalog/frames.py', 'catalog/types.py', 'source/registry.py',
    'catalog/reference_slots.py', 'catalog/typed_mentions.py', 'catalog/concepts.py', 'catalog/references.py', 'review/store.py', 'review/search.py',
    'review/anchors.py', 'review/context.py',
)


def extraction_hash():
    return digest({name: sha256((PROJECT / 'anna' / name).read_bytes()).hexdigest()
                   for name in EXTRACTION_MODULES})


def input_snapshot(db):
    summary = json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    return digest({'summary': summary, 'annotations': annotation_snapshot(db)})


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _validate_target_selector(target):
    if not isinstance(target, dict):
        raise ValueError('Confirm ruling requires target selector')
    for field in ('type', 'ruleset', 'page_path'):
        if not isinstance(target.get(field), str) or not target[field].strip():
            raise ValueError('Invalid target selector: ' + field)
    zh = target.get('name_zh', '')
    en = target.get('name_en', '')
    if not isinstance(zh, str) or not isinstance(en, str) or not (zh.strip() or en.strip()):
        raise ValueError('Invalid target selector name')


def load_annotations(path=DEFAULT_ANNOTATIONS):
    path = Path(path)
    raw = path.read_bytes() if path.exists() else b''
    events = []
    known = {}
    for number, line in enumerate(raw.decode('utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if event.get('schema') != 1 or event.get('operation') not in ANNOTATION_OPERATIONS:
                raise ValueError('Unsupported annotation schema or operation')
            for field in ('id', 'reviewer', 'reason', 'created_at'):
                if not isinstance(event.get(field), str) or not event[field].strip():
                    raise ValueError('Missing field: ' + field)
            if event['id'] in known:
                raise ValueError('Duplicate annotation event ID')
            if event['operation'] == 'revoke':
                previous = known.get(event.get('target_revision_id'))
                if not previous or previous['operation'] != 'annotate':
                    raise ValueError('Revoked annotation revision does not exist')
            else:
                if not isinstance(event.get('annotation_id'), str) or not event['annotation_id'].strip():
                    raise ValueError('Missing annotation_id')
                kind = event.get('kind')
                if kind not in ANNOTATION_KINDS:
                    raise ValueError('Invalid annotation kind')
                subject = event.get('subject')
                if not isinstance(subject, dict):
                    raise ValueError('Missing subject selector')
                if kind == 'reference':
                    if subject.get('kind') != 'page' or not isinstance(subject.get('page_path'), str) or not subject['page_path'].strip():
                        raise ValueError('Invalid reference annotation subject')
                else:
                    for field in ('type', 'ruleset', 'page_path'):
                        if not isinstance(subject.get(field), str) or not subject[field].strip():
                            raise ValueError('Invalid annotation subject: ' + field)
                    if not isinstance(subject.get('name_zh'), str) or not isinstance(subject.get('name_en', ''), str) or not (subject['name_zh'].strip() or subject.get('name_en', '').strip()):
                        raise ValueError('Invalid annotation subject name')
                anchors = event.get('anchors')
                if not isinstance(anchors, list) or not anchors:
                    raise ValueError('Annotation requires at least one anchor')
                anchor_ids = set()
                for anchor in anchors:
                    if not isinstance(anchor, dict) or not isinstance(anchor.get('id'), str) or not anchor['id'].strip():
                        raise ValueError('Invalid annotation anchor')
                    if anchor['id'] in anchor_ids:
                        raise ValueError('Duplicate anchor id: ' + anchor['id'])
                    anchor_ids.add(anchor['id'])
                    akind = anchor.get('kind')
                    if kind == 'reference' and akind != 'span':
                        raise ValueError('Reference annotation requires span anchor')
                    if akind in {'clause', 'span'}:
                        if not isinstance(anchor.get('quote'), str) or not anchor['quote'] or (kind == 'reference' and not anchor['quote'].strip()):
                            raise ValueError('Clause/span anchor requires quote')
                        if 'within' in anchor and anchor['within'] is not None and not isinstance(anchor['within'], str):
                            raise ValueError('Anchor within must be a string')
                        if 'signal' in anchor and anchor['signal'] is not None and anchor['signal'] not in REFERENCE_SIGNALS:
                            raise ValueError('Invalid anchor signal')
                    elif akind == 'table':
                        if not isinstance(anchor.get('caption_zh'), str) or not anchor['caption_zh'].strip():
                            raise ValueError('Table anchor requires caption_zh')
                    else:
                        raise ValueError('Unsupported anchor kind')
                statements = event.get('statements')
                if not isinstance(statements, dict):
                    raise ValueError('Annotation statements must be an object')
                if kind == 'reference':
                    if len(anchors) != 1:
                        raise ValueError('Reference annotation requires exactly one anchor')
                    ruling = statements.get('ruling')
                    if ruling not in REFERENCE_RULINGS:
                        raise ValueError('Invalid reference ruling')
                    if ruling == 'confirm':
                        _validate_target_selector(statements.get('target'))
                    else:
                        if statements.get('target') is not None:
                            raise ValueError('Non-reference or missing-definition ruling cannot have a target')
                        if ruling == 'missing_definition' and not isinstance(statements.get('search_query'), str):
                            raise ValueError('Missing-definition ruling requires search_query')
                basis = event.get('basis')
                if not isinstance(basis, dict) or basis.get('schema') != 1:
                    raise ValueError('Unsupported annotation basis schema')
                if not isinstance(basis.get('content'), list) or not isinstance(basis.get('context'), dict):
                    raise ValueError('Invalid annotation basis payload')
                if basis.get('content_sha256') != digest(basis['content']) or basis.get('context_sha256') != digest(basis['context']):
                    raise ValueError('Annotation basis digest mismatch')
                if event.get('supersedes'):
                    previous = known.get(event['supersedes'])
                    if not previous or previous['operation'] != 'annotate' or previous['annotation_id'] != event['annotation_id']:
                        raise ValueError('Superseded revision must belong to the same annotation')
            known[event['id']] = event
            events.append(event)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'Invalid annotation log line {number}: {exc}') from exc
    return events, sha256(raw).hexdigest()


def partition_annotations(events):
    """Split unified events into rule-knowledge events and reference-ruling events, pairing revocations."""
    kinds = {e['id']: e['kind'] for e in events if e['operation'] == 'annotate'}
    rule_events, ref_events = [], []
    for event in events:
        kind = event['kind'] if event['operation'] == 'annotate' else kinds[event['target_revision_id']]
        (ref_events if kind == 'reference' else rule_events).append(event)
    return rule_events, ref_events


def annotation_lifecycle(events):
    """Superseding/revoking never resurrects an older revision; duplicate actives conflict."""
    states = {e['id']: 'active' for e in events if e['operation'] == 'annotate'}
    for event in events:
        if event['operation'] == 'revoke':
            states[event['target_revision_id']] = 'revoked'
        elif event.get('supersedes'):
            states[event['supersedes']] = 'superseded'
    by_annotation = {}
    for event in events:
        if event['operation'] == 'annotate' and states[event['id']] == 'active':
            by_annotation.setdefault(event['annotation_id'], []).append(event['id'])
    active = {}
    for aid, ids in by_annotation.items():
        if len(ids) == 1:
            active[aid] = ids[0]
        else:
            for rev_id in ids:
                states[rev_id] = 'conflict'
    return states, active


def append_annotation_events(path, pending, expected_hash):
    """Caller holds locked(path); compare-and-swap protects concurrent saves."""
    path = Path(path)
    _, current = load_annotations(path)
    if current != expected_hash:
        raise ValueError('Annotation log changed; reload before saving')
    events = [{'schema': 1, 'id': 'ann_rev_' + uuid4().hex,
               'created_at': datetime.now(timezone.utc).isoformat(), **event} for event in pending]
    raw = path.read_bytes() if path.exists() else b''
    if raw and not raw.endswith(b'\n'):
        raw += b'\n'
    raw += ''.join(canonical(event) + '\n' for event in events).encode()
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.annotation-', delete=False) as stream:
            temp = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        load_annotations(temp)
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temp and temp.exists():
            temp.unlink()
    return events


def annotation_snapshot(db):
    row = db.execute("SELECT value FROM metadata WHERE key='annotation_snapshot'").fetchone()
    return json.loads(row[0]) if row else None


def require_annotations_appendable(db, path):
    saved = annotation_snapshot(db)
    if not saved:
        raise ValueError('Database is missing annotation snapshot; run make build')
    raw = Path(path).read_bytes() if Path(path).exists() else b''
    events, _ = load_annotations(path)
    prefix = raw[:saved['bytes']]
    if len(events) < saved['events'] or sha256(prefix).hexdigest() != saved['sha256']:
        raise ValueError('Annotation log is not an append-only extension of the build snapshot; rebuild')
    summary = json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    if summary.get('extraction_sha256') != extraction_hash():
        raise ValueError('Extraction implementation changed or the snapshot is stale; run make build')


def require_current_annotations(db, path=None):
    saved = annotation_snapshot(db)
    if not saved:
        return None
    _, current = load_annotations(path or saved['path'])
    if current != saved['sha256']:
        raise ValueError('Annotation log has not been replayed; run make build')
    return saved
