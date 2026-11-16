"""Versioned, append-only review events. Generated databases are never the authority."""
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

DEFAULT_DECISIONS = PROJECT / 'reviews/decisions.jsonl'
DEFAULT_ANNOTATIONS = PROJECT / 'annotations/annotations.jsonl'
KNOWLEDGE_OPERATIONS = {'knowledge_accept', 'knowledge_reject'}
ANNOTATION_OPERATIONS = {'annotate', 'revoke'}


def pipeline_hash():
    return sha256(b''.join(p.read_bytes() for p in sorted((PROJECT/'anna').rglob('*.py')))).hexdigest()


# Bump when the meaning of stored review dependencies changes, not for UI edits.
REVIEW_SEMANTICS_VERSION = 1
EXTRACTION_MODULES = ('serialization.py', 'source/pages.py', 'catalog/build.py', 'source/html.py', 'catalog/types.py', 'source/registry.py', 'source/tables.py',
                      'catalog/concepts.py', 'catalog/references.py', 'review/store.py', 'review/search.py', 'knowledge/extract.py', 'knowledge/annotations.py', 'knowledge/repository.py', 'knowledge/conditions.py', 'knowledge/rule_index.py', 'knowledge/context.py', 'knowledge/inspection.py', 'knowledge/query_schema.py', 'knowledge/effects.py', 'knowledge/values.py', 'knowledge/model.py')


def extraction_hash():
    return digest({name: sha256((PROJECT/'anna'/name).read_bytes()).hexdigest()
                   for name in EXTRACTION_MODULES})


def input_snapshot(db):
    summary = json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    return digest({'summary': summary, 'reviews': snapshot(db)})


def require_appendable(db, path):
    """Pending events may extend, but never replace, the published log prefix."""
    saved = snapshot(db)
    raw = Path(path).read_bytes() if Path(path).exists() else b''
    events, _ = load_events(path)
    prefix = raw[:saved['bytes']]
    if len(events) < saved['events'] or sha256(prefix).hexdigest() != saved['sha256']:
        raise ValueError('Review log is not an append-only extension of the build snapshot; rebuild')
    summary = json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    if summary.get('extraction_sha256') != extraction_hash():
        raise ValueError('Extraction implementation changed or the snapshot is stale; run make build')


def load_events(path=DEFAULT_DECISIONS):
    path = Path(path)
    raw = path.read_bytes() if path.exists() else b''
    events = []
    known = {}
    for number, line in enumerate(raw.decode('utf-8').splitlines(), 1):
        if not line.strip(): continue
        try:
            event = json.loads(line)
            if event.get('schema') != 1 or event.get('operation') not in {'confirm', 'non_reference', 'missing_definition', 'revoke'} | KNOWLEDGE_OPERATIONS:
                raise ValueError('未知审核事件版本或操作')
            for field in ('id', 'reviewer', 'reason', 'created_at'):
                if not isinstance(event.get(field), str) or not event[field].strip():
                    raise ValueError('缺少字段：'+field)
            if event['id'] in known: raise ValueError('重复事件 ID')
            if event['operation'] == 'revoke':
                previous = known.get(event.get('decision_id'))
                if not previous or previous['operation'] == 'revoke': raise ValueError('撤销对象不存在')
            elif event['operation'] in KNOWLEDGE_OPERATIONS:
                binding = event['binding']
                for field in ('knowledge_id', 'subject_id', 'kind', 'path', 'raw'):
                    if not isinstance(binding.get(field), str) or not binding[field].strip():
                        raise ValueError('Invalid knowledge binding: '+field)
                span = binding.get('span')
                if not isinstance(span, list) or len(span)!=2 or not all(type(n) is int for n in span) or not 0<=span[0]<span[1]:
                    raise ValueError('Invalid knowledge evidence range')
                if not isinstance(event.get('basis'), dict) or event['basis'].get('schema') != 2:
                    raise ValueError('Unsupported review basis schema')
                if event.get('basis_sha256') != digest(event['basis']):
                    raise ValueError('Knowledge basis fingerprint mismatch')
                if event.get('supersedes'):
                    previous = known.get(event['supersedes'])
                    if not previous or previous['operation'] not in KNOWLEDGE_OPERATIONS or previous['binding']['knowledge_id'] != binding['knowledge_id']:
                        raise ValueError('Superseded decision must concern the same knowledge record')
            else:
                binding = event['binding']
                for field in ('reference_id', 'path', 'text', 'signal', 'kind', 'role'):
                    if not isinstance(binding.get(field), str): raise ValueError('无效绑定：'+field)
                if not (isinstance(binding['start'], int) and isinstance(binding['end'], int)
                        and 0 <= binding['start'] < binding['end'] and binding['text'].strip()):
                    raise ValueError('无效审核范围')
                if not isinstance(event.get('basis'), dict) or event['basis'].get('schema') != 2:
                    raise ValueError('Unsupported review basis schema')
                if event.get('basis_sha256') != digest(event['basis']):
                    raise ValueError('审核依据指纹不符')
                target = event.get('target_id')
                if event['operation'] == 'confirm' and (not isinstance(target, str) or not target):
                    raise ValueError('确认引用需要目标')
                if event['operation'] in {'non_reference', 'missing_definition'} and target is not None:
                    raise ValueError('非引用或缺失定义决策不能有目标')
                if event['operation'] == 'missing_definition' and not isinstance(event.get('search_query'), str):
                    raise ValueError('缺失定义决策须保存候选搜索条件')
                if event.get('supersedes'):
                    previous = known.get(event['supersedes'])
                    if (not previous or previous['operation'] == 'revoke'
                            or previous['binding'].get('reference_id') != binding['reference_id']):
                        raise ValueError('替代决策必须属于同一引用')
            known[event['id']] = event
            events.append(event)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'审核日志第 {number} 行无效：{exc}') from exc
    return events, sha256(raw).hexdigest()


def lifecycle(events):
    """Superseding/revoking never resurrects an older decision implicitly."""
    states = {e['id']: 'active' for e in events if e['operation'] != 'revoke'}
    for event in events:
        if event['operation'] == 'revoke': states[event['decision_id']] = 'revoked'
        elif event.get('supersedes'): states[event['supersedes']] = 'superseded'
    return states


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix+'.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def append_event(path, event, expected_hash):
    """Caller holds locked(path); compare-and-swap protects concurrent saves."""
    return append_events(path, [event], expected_hash)[0]


def append_events(path, pending, expected_hash):
    """Atomically publish the entire validated batch under the caller's lock."""
    path = Path(path)
    _, current = load_events(path)
    if current != expected_hash: raise ValueError('Review log changed; reload before saving')
    events = [{'schema': 1, 'id': 'review_'+uuid4().hex,
               'created_at': datetime.now(timezone.utc).isoformat(), **event} for event in pending]
    raw = path.read_bytes() if path.exists() else b''
    if raw and not raw.endswith(b'\n'): raw += b'\n'
    raw += ''.join(canonical(event)+'\n' for event in events).encode()
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.review-', delete=False) as stream:
            temp = Path(stream.name)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        load_events(temp)  # Validate before replacing the durable input.
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if temp and temp.exists(): temp.unlink()
    return events


def snapshot(db):
    row = db.execute("SELECT value FROM metadata WHERE key='review_snapshot'").fetchone()
    if not row: raise ValueError('Database is missing review results; run make build')
    return json.loads(row[0])


def require_current(db, path=None):
    saved = snapshot(db)
    _, current = load_events(path or saved['path'])
    if current != saved['sha256']:
        raise ValueError('Review log has not been replayed; run make build')
    return saved


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
                if event.get('kind') not in {'grant', 'procedure'}:
                    raise ValueError('Invalid annotation kind')
                subject = event.get('subject')
                if not isinstance(subject, dict):
                    raise ValueError('Missing subject selector')
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
                    if akind in {'clause', 'span'}:
                        if not isinstance(anchor.get('quote'), str) or not anchor['quote']:
                            raise ValueError('Clause/span anchor requires quote')
                    elif akind == 'table':
                        if not isinstance(anchor.get('caption_zh'), str) or not anchor['caption_zh'].strip():
                            raise ValueError('Table anchor requires caption_zh')
                    else:
                        raise ValueError('Unsupported anchor kind')
                if not isinstance(event.get('statements'), dict):
                    raise ValueError('Annotation statements must be an object')
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
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
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
