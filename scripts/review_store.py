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

from paths import PROJECT

DEFAULT_DECISIONS = PROJECT / 'reviews/decisions.jsonl'
KNOWLEDGE_OPERATIONS = {'knowledge_accept', 'knowledge_reject'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def pipeline_hash():
    return sha256(b''.join(p.read_bytes() for p in sorted((PROJECT/'scripts').glob('*.py')))).hexdigest()


# Bump when the meaning of stored review dependencies changes, not for UI edits.
REVIEW_SEMANTICS_VERSION = 1
EXTRACTION_MODULES = ('build.py', 'html_extract.py', 'entry_types.py', 'source_extract.py', 'table_extract.py',
                      'concepts.py', 'references.py', 'review_store.py', 'review_search.py', 'casting_extract.py', 'knowledge.py', 'rule_conditions.py', 'chain_reading.py', 'spell_effects.py', 'effect_queries.py')


def extraction_hash():
    return digest({name: sha256((PROJECT/'scripts'/name).read_bytes()).hexdigest()
                   for name in EXTRACTION_MODULES})


def input_snapshot(db):
    summary = json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    return digest({'summary': summary, 'reviews': snapshot(db)})


def require_appendable(db, path):
    """Pending events may extend, but never replace, the published log prefix."""
    saved = snapshot(db)
    raw = Path(path).read_bytes() if Path(path).exists() else b''
    events, _ = load_events(path)
    prefix = raw[:saved['bytes']] if 'bytes' in saved else b''.join(raw.splitlines(keepends=True)[:saved['events']])
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
                if not isinstance(event.get('basis'), dict) or event.get('basis_sha256') != digest(event['basis']):
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
                if not isinstance(event.get('basis'), dict) or event.get('basis_sha256') != digest(event['basis']):
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
