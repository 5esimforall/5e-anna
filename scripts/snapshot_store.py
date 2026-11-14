"""Version 2 lossless row codec and shared, content-addressed snapshot objects.

Only projection recipes are executable; source parsing and review replay never run
while restoring a snapshot. Every reconstructed table must match its saved digest.
"""
from base64 import b64decode, b64encode
from collections import Counter
from contextlib import closing
import csv
import gzip
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from review_store import canonical

SHARDS = 64
PROJECTIONS = {'casting_options', 'knowledge_rule_links', 'rule_effect_index'}
BASE_TABLES = {'effective_knowledge': 'rule_knowledge', 'effective_references': 'concept_observations'}
CODECS = [(sort, compact, ascii_) for ascii_ in (False, True) for sort in (True, False) for compact in (True, False)]


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def json_text(value, codec):
    sort, compact, ascii_ = CODECS[codec]
    return json.dumps(value, ensure_ascii=ascii_, sort_keys=sort, separators=(',', ':') if compact else None)


def codec_for(raw, value):
    return next((i for i in range(len(CODECS)) if json_text(value, i) == raw), None)


def cell(value):
    return {'blob': b64encode(value).decode()} if isinstance(value, bytes) else value


def uncell(value):
    return b64decode(value['blob'], validate=True) if isinstance(value, dict) else value


def row_bytes(row):
    return (canonical([cell(v) for v in row]) + '\n').encode()


def ordered_rows(db, name, info):
    cols = ','.join(map(quote, info['columns']))
    order = ','.join(map(quote, info['key'] or info['columns']))
    return db.execute(f'SELECT {cols} FROM {quote(name)} ORDER BY {order}')


def table_digest(db, name, info):
    h = sha256(); count = 0
    for row in ordered_rows(db, name, info):
        h.update(row_bytes(row)); count += 1
    return {'sha256': h.hexdigest(), 'rows': count}


class Objects:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, ref):
        h = ref['sha256']
        if not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h):
            raise ValueError('Invalid object identity')
        return self.root / h[:2] / (h + '.gz')

    def put(self, raw):
        ref = {'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)}
        path = self.path(ref)
        if path.exists():
            if self.get(ref) != raw:
                raise ValueError('Object identity collision')
            return ref
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.object-', delete=False) as stream:
            temp = Path(stream.name)
            stream.write(gzip.compress(raw, compresslevel=6, mtime=0))
            stream.flush(); os.fsync(stream.fileno())
        try:
            # Never overwrite a published content object, even during concurrent capture.
            try:
                os.link(temp, path)
                descriptor = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except FileExistsError:
                self.get(ref)
        finally:
            temp.unlink(missing_ok=True)
        return ref

    def get(self, ref):
        try:
            raw = gzip.decompress(self.path(ref).read_bytes())
        except (OSError, EOFError) as exc:
            raise ValueError('Snapshot object is missing or damaged: ' + ref['sha256']) from exc
        if len(raw) != ref['bytes'] or sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('Snapshot object changed: ' + ref['sha256'])
        return raw


def infer_layout(columns, row):
    if 'data' not in columns or not row:
        return None
    raw = row[columns.index('data')]
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or (codec := codec_for(raw, data)) is None:
        return None
    fields = {}
    for column, value in zip(columns, row):
        key = column if column in data else 'path' if column == 'page_path' else column
        if column == 'data' or key not in data:
            continue
        v = data[key]
        if type(value) is type(v) and value == v:
            fields[column] = [key, 'value']
        elif isinstance(v, bool) and type(value) is int and value == int(v):
            fields[column] = [key, 'bool']
        elif isinstance(value, str) and (c := codec_for(value, v)) is not None:
            fields[column] = [key, c]
    return {'codec': codec, 'fields': fields}


def projected(data, spec):
    key, codec = spec
    if key not in data:
        raise KeyError(key)
    value = data[key]
    if codec == 'value':
        return value
    if codec == 'bool':
        return int(value) if isinstance(value, bool) else None
    return json_text(value, codec)


def unpack_data(record, layout, columns, data=None):
    data = record[1] if data is None else data
    extra = record[-1]
    values = []
    for col in columns:
        if col in extra:
            values.append(uncell(extra[col]))
        elif col == 'data':
            values.append(json_text(data, layout['codec']))
        else:
            values.append(projected(data, layout['fields'][col]))
    return values


def pack_row(row, info, base=None):
    columns, layout = info['columns'], info['layout']
    raw = ['row', [cell(v) for v in row]]
    if not layout:
        return raw
    try:
        data = json.loads(row[columns.index('data')])
        if not isinstance(data, dict) or json_text(data, layout['codec']) != row[columns.index('data')]:
            return raw
        extra = {}
        for col, value in zip(columns, row):
            if col == 'data':
                continue
            try:
                p = projected(data, layout['fields'][col])
                same = type(p) is type(value) and p == value
            except (KeyError, TypeError):
                same = False
            if not same:
                extra[col] = cell(value)
        record = ['data', data, extra]
        if base is not None and CODECS[layout['codec']][0]:
            before = json.loads(base)
            patch = {k: v for k, v in data.items() if k not in before or canonical(v) != canonical(before[k])}
            removed = [k for k in before if k not in data]
            candidate = ['patch', [row[columns.index(k)] for k in info['key']], patch, removed, extra]
            if len(canonical(candidate)) < len(canonical(record)):
                record = candidate
        # Check each value, not just the inferred first-row layout.
        restored = unpack_data(record, layout, columns, data)
        if row_bytes(row) != row_bytes(restored):
            raise ValueError('Row codec did not round-trip')
        return record
    except (ValueError, TypeError):
        return raw


def save_tables(db, descriptors, objects, tmp):
    result = {}
    for name, original in descriptors.items():
        info = dict(original)
        info.update(table_digest(db, name, info))
        if name in PROJECTIONS and 'effective_knowledge' in descriptors:
            info['storage'] = 'projection_v1'
            generated = {row[0]: row for row in projection_rows(db, name)}
            corrections = {}
            for row in ordered_rows(db, name, info):
                expected = generated.pop(row[0], None)
                if expected is None or len(expected) != len(row):
                    raise ValueError('Projection recipe has different records: ' + name)
                changes = {}
                for i, (a, b) in enumerate(zip(row, expected)):
                    if row_bytes([a]) == row_bytes([b]):
                        continue
                    if info['columns'][i] != 'data' or canonical(json.loads(a)) != canonical(json.loads(b)):
                        raise ValueError('Projection recipe has different values: ' + name)
                    changes[str(i)] = cell(a)
                if changes:
                    corrections[row[0]] = changes
            if generated:
                raise ValueError('Projection recipe added records: ' + name)
            # Historical JSON key order is not a rule fact, but is preserved exactly.
            if corrections:
                info['encoding_overrides'] = objects.put(canonical(corrections).encode())
            result[name] = info
            continue
        first = next(iter(ordered_rows(db, name, info)), None)
        info.update(storage='rows_v1', layout=infer_layout(info['columns'], first), chunks=[])
        base = BASE_TABLES.get(name)
        if base in descriptors and info['key'] == ['id'] and info['layout']:
            info['base'] = base
        paths = [Path(tmp) / f'{name}-{i}.jsonl' for i in range(SHARDS)]
        streams = [p.open('wb') for p in paths]
        try:
            for row in ordered_rows(db, name, info):
                keys = [row[info['columns'].index(k)] for k in info['key']]
                bucket = int(sha256(row_bytes(keys or row)).hexdigest()[:8], 16) % SHARDS
                before = db.execute(f'SELECT data FROM {quote(base)} WHERE id=?', keys).fetchone() if 'base' in info else None
                record = pack_row(row, info, before[0] if before else None)
                # Preserve object key order: a non-sorted original JSON is also evidence.
                streams[bucket].write((json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
        finally:
            for stream in streams:
                stream.close()
        for i, path in enumerate(paths):
            if path.stat().st_size:
                info['chunks'].append({'bucket': i, **objects.put(path.read_bytes())})
            path.unlink()
        result[name] = info
    return result


def projection_rows(db, name):
    records = [json.loads(r[0]) for r in db.execute('SELECT data FROM effective_knowledge ORDER BY id')]
    if name == 'casting_options':
        from knowledge import Context, branches
        for r in branches(Context(db), records):
            yield tuple(r[k] for k in ('id', 'root_id', 'subject_id', 'spell_id', 'mode', 'resource', 'recovery', 'selection')) + (canonical(r),)
    elif name == 'knowledge_rule_links':
        from chain_reading import rule_rows
        for r in rule_rows(records):
            yield tuple(r[k] for k in ('id', 'source_id', 'target_id', 'relation', 'status')) + (json.dumps(r, ensure_ascii=False),)
    elif name == 'rule_effect_index':
        from effect_queries import effect_rows, COLUMNS
        for r in effect_rows(records):
            yield tuple(r[k] for k in COLUMNS) + (json.dumps(r, ensure_ascii=False),)
    else:
        raise ValueError('Unknown projection recipe: ' + name)


def restore_tables(db, manifest, objects):
    descriptors = manifest['tables']
    for info in descriptors.values():
        db.execute(info['sql'])
    pending = set(descriptors)
    while pending:
        ready = [n for n in sorted(pending) if descriptors[n].get('base') not in pending and
                 (descriptors[n]['storage'] != 'projection_v1' or not (pending - PROJECTIONS))]
        if not ready:
            raise ValueError('Cyclic snapshot dependencies')
        for name in ready:
            info = descriptors[name]
            def rows():
                if info['storage'] == 'projection_v1':
                    overrides = json.loads(objects.get(info['encoding_overrides'])) if 'encoding_overrides' in info else {}
                    for row in projection_rows(db, name):
                        values = list(row)
                        for i, value in overrides.get(row[0], {}).items():
                            values[int(i)] = uncell(value)
                        yield values
                    return
                if info['storage'] != 'rows_v1':
                    raise ValueError('Unknown row storage codec')
                for chunk in info['chunks']:
                    for line in objects.get(chunk).splitlines():
                        record = json.loads(line)
                        if record[0] == 'row':
                            yield [uncell(v) for v in record[1]]
                        elif record[0] == 'data':
                            yield unpack_data(record, info['layout'], info['columns'])
                        elif record[0] == 'patch':
                            found = db.execute(f'SELECT data FROM {quote(info["base"])} WHERE id=?', record[1]).fetchone()
                            if not found:
                                raise ValueError('Missing base record for patch')
                            data = json.loads(found[0])
                            for key in record[3]:
                                del data[key]
                            data.update(record[2])
                            yield unpack_data(record, info['layout'], info['columns'], data)
                        else:
                            raise ValueError('Unknown row record kind')
            db.executemany(f'INSERT INTO {quote(name)} VALUES(' + ','.join('?' for _ in info['columns']) + ')', rows())
            if table_digest(db, name, info) != {k: info[k] for k in ('sha256', 'rows')}:
                raise ValueError('Restored table mismatch (projection recipe may need historical support): ' + name)
            pending.remove(name)
    for group in ('indexes', 'views', 'triggers'):
        for sql in manifest.get(group, {}).values():
            db.execute(sql)
    db.commit()
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Restored database failed integrity check')


def logical_rows(db, name):
    """Exports consume frozen records, not new extraction or current review replay."""
    cols = [r[1] for r in db.execute('PRAGMA table_info(' + quote(name) + ')')]
    for values in db.execute('SELECT * FROM ' + quote(name)):
        row = dict(zip(cols, values))
        if 'data' in row:
            yield json.loads(row['data'])
        else:
            for k in ('value', 'evidence'):
                if k in row and isinstance(row[k], str):
                    try:
                        row[k] = json.loads(row[k])
                    except ValueError:
                        pass
            yield row


def csv_value(value):
    if isinstance(value, (dict, list)):
        return canonical(value)
    return '' if value is None else str(value)


def export_recipe(db, name, raw, table_names):
    """Discard a duplicate only after multiset equality of EVERY exported value.

    Export order/whitespace are presentation. Unmatched historical files are kept
    verbatim as attachments; unknown future exports never disappear silently.
    """
    path = Path(name)
    table = path.stem
    if path.suffix == '.json':
        found = db.execute('SELECT value FROM metadata WHERE key=?', (table,)).fetchone()
        if found and canonical(json.loads(raw)) == canonical(json.loads(found[0])):
            return {'kind': 'metadata', 'key': table}
    if path.suffix not in {'.csv', '.jsonl'} or table not in table_names:
        return None
    if path.suffix == '.jsonl':
        saved = Counter(sha256(canonical(json.loads(line)).encode()).hexdigest() for line in raw.splitlines() if line.strip())
        current = Counter(sha256(canonical(r).encode()).hexdigest() for r in logical_rows(db, table))
        return {'kind': 'jsonl', 'table': table} if saved == current else None
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''))
    fields = reader.fieldnames
    saved = Counter(canonical(r) for r in reader)
    current = Counter(canonical({k: csv_value(r.get(k)) for k in fields}) for r in logical_rows(db, table))
    return {'kind': 'csv', 'table': table, 'fields': fields} if saved == current else None


def export_bytes(db, recipe):
    kind = recipe['kind']
    if kind == 'metadata':
        value = json.loads(db.execute('SELECT value FROM metadata WHERE key=?', (recipe['key'],)).fetchone()[0])
        return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    rows = logical_rows(db, recipe['table'])
    if kind == 'jsonl':
        return ''.join(canonical(r) + '\n' for r in rows).encode()
    if kind != 'csv':
        raise ValueError('Unknown export recipe')
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=recipe['fields'])
    writer.writeheader()
    for row in rows:
        writer.writerow({k: csv_value(row.get(k)) for k in recipe['fields']})
    return stream.getvalue().encode('utf-8-sig')
