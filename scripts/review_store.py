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


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def pipeline_hash():
    return sha256(b''.join(p.read_bytes() for p in sorted((PROJECT/'scripts').glob('*.py')))).hexdigest()


def load_events(path=DEFAULT_DECISIONS):
    path = Path(path)
    raw = path.read_bytes() if path.exists() else b''
    events = []
    known = {}
    for number, line in enumerate(raw.decode('utf-8').splitlines(), 1):
        if not line.strip(): continue
        try:
            event = json.loads(line)
            if event.get('schema') != 1 or event.get('operation') not in {'confirm', 'non_reference', 'revoke'}:
                raise ValueError('未知审核事件版本或操作')
            for field in ('id', 'reviewer', 'reason', 'created_at'):
                if not isinstance(event.get(field), str) or not event[field].strip():
                    raise ValueError('缺少字段：'+field)
            if event['id'] in known: raise ValueError('重复事件 ID')
            if event['operation'] == 'revoke':
                previous = known.get(event.get('decision_id'))
                if not previous or previous['operation'] == 'revoke': raise ValueError('撤销对象不存在')
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
                if event['operation'] == 'non_reference' and target is not None:
                    raise ValueError('非引用决策不能有目标')
                if event.get('supersedes'):
                    previous = known.get(event['supersedes'])
                    if (not previous or previous['operation'] == 'revoke'
                            or previous['binding']['reference_id'] != binding['reference_id']):
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
    path = Path(path)
    events, current = load_events(path)
    if current != expected_hash: raise ValueError('审核日志已变化，请重新读取后保存')
    event = {'schema': 1, 'id': 'review_'+uuid4().hex,
             'created_at': datetime.now(timezone.utc).isoformat(), **event}
    raw = path.read_bytes() if path.exists() else b''
    if raw and not raw.endswith(b'\n'): raw += b'\n'
    raw += (canonical(event)+'\n').encode()
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
    return event


def snapshot(db):
    row = db.execute("SELECT value FROM metadata WHERE key='review_snapshot'").fetchone()
    if not row: raise ValueError('数据库缺少审核结果，请运行 make build')
    return json.loads(row[0])


def require_current(db, path=None):
    saved = snapshot(db)
    _, current = load_events(path or saved['path'])
    if current != saved['sha256']:
        raise ValueError('审核日志尚未重放，请运行 make build')
    return saved
