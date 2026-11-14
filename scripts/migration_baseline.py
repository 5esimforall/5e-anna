"""Immutable local evidence bundles, separate from replaceable build output."""
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from snapshot_store import Objects, export_bytes, export_recipe, restore_tables, save_tables

from paths import PROJECT
from review_store import canonical, digest, load_events, snapshot

SCHEMA = 2
ACTIVE = PROJECT / 'baselines/active.json'


def fingerprint(path):
    h = sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def connect(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def metadata(db, name):
    row = db.execute('SELECT value FROM metadata WHERE key=?', (name,)).fetchone()
    return json.loads(row[0]) if row else None


def tables(db):
    result = {}
    for name, sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        info = db.execute('PRAGMA table_info(' + quote(name) + ')').fetchall()
        result[name] = {'columns': [r[1] for r in info],
                        'key': [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]],
                        'rows': db.execute('SELECT count(*) FROM ' + quote(name)).fetchone()[0], 'sql': sql}
    return result


def safe_file(root, relative):
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Unsafe bundle path: ' + str(relative))
    return path


def objects(root):
    return Objects(Path(root).parent / 'objects')


def object_refs(manifest):
    for group in ('source_files', 'counterpart_files'):
        yield from manifest[group].values()
    yield manifest['review_log']
    for info in manifest['tables'].values():
        yield from info.get('chunks', [])
        if 'encoding_overrides' in info:
            yield info['encoding_overrides']
    for info in manifest['exports'].values():
        if 'object' in info:
            yield info['object']


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('schema') not in {1, SCHEMA} or manifest.get('id') != 'baseline_' + digest({k: v for k, v in manifest.items() if k != 'id'}):
        raise ValueError('Baseline manifest is invalid or changed')
    if manifest['schema'] == 1:
        # Read-only compatibility for the first full archive; never write or promote it.
        required = {'catalog.sqlite', 'decisions.jsonl', 'source.zip', 'exports.zip'}
        if not required <= manifest['files'].keys():
            raise ValueError('Legacy baseline is missing required artifacts')
        for name, info in manifest['files'].items():
            p = safe_file(root, name)
            if not p.is_file() or p.stat().st_size != info['bytes'] or fingerprint(p) != info['sha256']:
                raise ValueError('Baseline artifact is missing or changed: ' + name)
    else:
        store = objects(root)
        seen = set()
        for ref in object_refs(manifest):
            if (ref['sha256'], ref['bytes']) not in seen:
                store.get(ref)
                seen.add((ref['sha256'], ref['bytes']))
    return manifest


def active(pointer=ACTIVE):
    pointer = Path(pointer)
    if not pointer.exists():
        return None
    value = json.loads(pointer.read_text())
    if value.get('schema') not in {1, SCHEMA}:
        raise ValueError('Unknown active baseline pointer schema')
    root = (pointer.parent / value['path']).resolve()
    if verify(root)['id'] != value['id']:
        raise ValueError('Active baseline identity does not match its manifest')
    return root


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.snapshot-', delete=False) as stream:
        temp = Path(stream.name)
        stream.write(canonical(value) + '\n'); stream.flush(); os.fsync(stream.fileno())
    try:
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def save_snapshot(db, output, source_files, counterpart_files, review_log, exports, parent, provenance):
    """Publish a logical snapshot; caches and duplicate export files are not authority."""
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Baseline already exists; never overwrite it: ' + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    if parent and not review_log.startswith(decisions_path(parent).read_bytes()):
        raise ValueError('Review history must remain an append-only extension of the parent baseline')
    store = objects(output)
    with tempfile.TemporaryDirectory(prefix='.baseline-', dir=output.parent) as tmp:
        stage = Path(tmp) / 'snapshot'; stage.mkdir()
        summary = metadata(db, 'summary')
        if not summary or not summary.get('full_scan'):
            raise ValueError('Baseline requires a full dataset')
        descriptors = tables(db)
        built = snapshot(db)
        events = [json.loads(line) for line in review_log.splitlines() if line.strip()]
        prefix = review_log[:built['bytes']] if 'bytes' in built else b''.join(review_log.splitlines(keepends=True)[:built['events']])
        if sha256(prefix).hexdigest() != built['sha256'] or len(events) < built['events']:
            raise ValueError('Decision log is not an append-only extension of the dataset snapshot')
        manifest = {'schema': SCHEMA, 'created_at': datetime.now(timezone.utc).isoformat(),
                    'parent': {'id': verify(parent)['id'], 'path': os.path.relpath(Path(parent).resolve(), output.parent)} if parent else None,
                    'summary': summary, 'provenance': provenance,
                    'tables': save_tables(db, descriptors, store, tmp),
                    'review_snapshot': built, 'review_events': len(events), 'pending_review_events': len(events) - built['events'],
                    'review_log': store.put(review_log), 'source_files': {}, 'counterpart_files': {}, 'exports': {}}
        for kind in ('index', 'view', 'trigger'):
            manifest[{'index': 'indexes', 'view': 'views', 'trigger': 'triggers'}[kind]] = dict(db.execute(
                "SELECT name,sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL ORDER BY name", (kind,)))
        for group, inputs in [('source_files', source_files), ('counterpart_files', counterpart_files)]:
            for name, raw in inputs:
                safe_file(output, name)
                manifest[group][name] = store.put(raw)
        for name, raw in exports:
            recipe = export_recipe(db, name, raw, descriptors.keys() | manifest['views'].keys())
            info = {'original_sha256': sha256(raw).hexdigest(), 'original_bytes': len(raw)}
            if recipe:
                info.update(recipe=recipe, equivalence='all_values_multiset')
            else:
                info.update(object=store.put(raw), reason='historical_artifact_not_proven_reconstructible')
            manifest['exports'][name] = info
        manifest['id'] = 'baseline_' + digest(manifest)
        (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        with (stage / 'manifest.json').open('rb') as stream:
            os.fsync(stream.fileno())
        sync_directory(stage)
        # Validate all reconstructed rows before publishing, including projection recipes.
        with closing(sqlite3.connect(Path(tmp) / 'check.sqlite')) as restored:
            restore_tables(restored, manifest, store)
        os.rename(stage, output)
        sync_directory(output.parent)
        cache = PROJECT / '.cache/baselines' / manifest['id']
        cache.mkdir(parents=True, exist_ok=True)
        restored_path = Path(tmp) / 'check.sqlite'
        h = fingerprint(restored_path)
        os.replace(restored_path, cache / 'catalog.sqlite')
        atomic_json(cache / 'catalog.json', {'baseline_id': manifest['id'], 'sha256': h})
    return manifest


def capture(database, output, source, decisions, counterparts=None, *, pointer=ACTIVE):
    database, output, source, decisions = map(lambda p: Path(p).resolve(), (database, output, source, decisions))
    if output.exists():
        raise ValueError('Baseline already exists; never overwrite it: ' + str(output))
    if output.is_relative_to(database.parent) or output.is_relative_to(source):
        raise ValueError('Baseline must be separate from generated output and source')
    parent = active(pointer)
    with tempfile.TemporaryDirectory(prefix='anna-capture-') as tmp:
        frozen = Path(tmp) / 'catalog.sqlite'
        with closing(connect(database)) as original, closing(sqlite3.connect(frozen)) as saved:
            original.backup(saved)
        with closing(connect(frozen)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Baseline database failed integrity check')
            summary = metadata(db, 'summary')
            if not summary or not summary.get('full_scan'):
                raise ValueError('Baseline requires a full dataset')
            expected = dict(db.execute('SELECT path,sha256 FROM pages'))
            expected.update((metadata(db, 'concept_coverage') or {}).get('discovery_inputs', {}))
            if 'webhelpcontents.htm' not in expected:
                raise ValueError('Source directory evidence is missing from the dataset')
            actual_html = {p.relative_to(source).as_posix() for p in (source / 'topics').rglob('*')
                           if p.suffix.lower() in {'.htm', '.html'} and p.is_file()}
            if actual_html != {p for p, in db.execute('SELECT path FROM pages')}:
                raise ValueError('Source HTML inventory changed; baseline was not saved')
            def source_inputs():
                for name, h in sorted(expected.items()):
                    raw = safe_file(source, name).read_bytes()
                    if sha256(raw).hexdigest() != h:
                        raise ValueError('Input no longer matches the saved dataset: ' + name)
                    yield name, raw
            _, log_hash = load_events(decisions)
            raw_log = decisions.read_bytes() if decisions.exists() else b''
            if sha256(raw_log).hexdigest() != log_hash:
                raise ValueError('Decision log changed during capture')
            counterpart = summary.get('counterparts', {})
            counterpart_files = []
            if counterpart.get('status') != 'skipped':
                if not counterparts:
                    raise ValueError('Dataset includes external evidence; supply its mirror')
                from counterparts import load_records, SKIPPED_DIRS, SKIPPED_PREFIXES
                _, current = load_records(counterparts)
                if current['input_sha256'] != counterpart.get('input_sha256'):
                    raise ValueError('External evidence no longer matches the dataset')
                root = Path(counterparts).resolve()
                names = sorted(p.relative_to(root).as_posix() for p in (root / 'data').rglob('*.json')
                               if p.relative_to(root / 'data').parts[0] not in SKIPPED_DIRS and not p.name.startswith(SKIPPED_PREFIXES))
                def counterpart_inputs():
                    h = sha256()
                    for name in names:
                        raw = safe_file(root, name).read_bytes()
                        h.update(f'{name}:{sha256(raw).hexdigest()}\n'.encode())
                        yield name, raw
                    if h.hexdigest() != counterpart['input_sha256']:
                        raise ValueError('External evidence changed during capture')
                counterpart_files = counterpart_inputs()
            exports = ((p.relative_to(database.parent).as_posix(), p.read_bytes()) for p in sorted(database.parent.rglob('*'))
                       if p.is_file() and not any(x.startswith('.') for x in p.relative_to(database.parent).parts)
                       and p.suffix in {'.csv', '.json', '.jsonl', '.md'})
            return save_snapshot(db, output, source_inputs(), counterpart_files, raw_log, exports, parent,
                                 {'kind': 'capture', 'database_sha256': fingerprint(frozen), 'storage_codec': 1, 'projection_recipe': 1})


def convert(legacy, output):
    legacy, output = Path(legacy).resolve(), Path(output).resolve()
    manifest = verify(legacy)
    if manifest['schema'] != 1:
        raise ValueError('Conversion accepts only the read-only legacy archive')
    with closing(connect(legacy / 'catalog.sqlite')) as db, zipfile.ZipFile(legacy / 'source.zip') as source, zipfile.ZipFile(legacy / 'exports.zip') as exports:
        def archive_inputs(archive, hashes):
            for name, h in sorted(hashes.items()):
                raw = archive.read(name)
                if sha256(raw).hexdigest() != h:
                    raise ValueError('Legacy input manifest mismatch: ' + name)
                yield name, raw
        def external_inputs():
            if manifest['counterpart_files']:
                with zipfile.ZipFile(legacy / 'counterparts.zip') as external:
                    yield from archive_inputs(external, manifest['counterpart_files'])
        return save_snapshot(db, output, archive_inputs(source, manifest['source_files']), external_inputs(),
                             (legacy / 'decisions.jsonl').read_bytes(), archive_inputs(exports, manifest['exports']), legacy,
                             {'kind': 'legacy_conversion', 'legacy_id': manifest['id'], 'storage_codec': 1, 'projection_recipe': 1})


def materialize(root, cache=None):
    root = Path(root).resolve()
    manifest = verify(root)
    if manifest['schema'] == 1:
        return root / 'catalog.sqlite'
    cache = Path(cache) if cache else PROJECT / '.cache/baselines'
    folder = cache / manifest['id']; folder.mkdir(parents=True, exist_ok=True)
    database, receipt = folder / 'catalog.sqlite', folder / 'catalog.json'
    if database.exists() and receipt.exists():
        try:
            info = json.loads(receipt.read_text())
        except (ValueError, OSError):
            info = {}
        if info.get('baseline_id') == manifest['id'] and fingerprint(database) == info.get('sha256'):
            return database
    with tempfile.TemporaryDirectory(prefix='.restore-', dir=folder) as tmp:
        path = Path(tmp) / 'catalog.sqlite'
        with closing(sqlite3.connect(path)) as db:
            restore_tables(db, manifest, objects(root))
        h = fingerprint(path)
        os.replace(path, database)
        atomic_json(receipt, {'baseline_id': manifest['id'], 'sha256': h})
    return database


def decisions_path(root, cache=None):
    root = Path(root).resolve()
    manifest = verify(root)
    if manifest['schema'] == 1:
        return root / 'decisions.jsonl'
    cache = Path(cache) if cache else PROJECT / '.cache/baselines'
    folder = cache / manifest['id']; folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'decisions.jsonl'
    raw = objects(root).get(manifest['review_log'])
    if not path.exists() or path.read_bytes() != raw:
        path.write_bytes(raw)
    return path


def export_snapshot(root, output, group='exports'):
    root, output = Path(root).resolve(), Path(output).resolve()
    manifest = verify(root)
    if manifest['schema'] != SCHEMA or group not in {'exports', 'source_files', 'counterpart_files'}:
        raise ValueError('Unsupported snapshot export')
    if output.exists():
        raise ValueError('Export directory already exists; refusing to overwrite')
    output.parent.mkdir(parents=True, exist_ok=True)
    store = objects(root)
    with tempfile.TemporaryDirectory(prefix='.export-', dir=output.parent) as tmp:
        stage = Path(tmp) / 'files'; stage.mkdir()
        with closing(connect(materialize(root))) as db:
            for name, info in manifest[group].items():
                raw = export_bytes(db, info['recipe']) if 'recipe' in info else store.get(info.get('object', info))
                path = safe_file(stage, name); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        os.rename(stage, output)
    return {'files': len(manifest[group]), 'path': str(output)}


def activate(root, pointer=ACTIVE, *, reviewer='', reason='', bootstrap=False):
    from migration_gate import promote
    return promote(root, pointer, reviewer=reviewer, reason=reason, bootstrap=bootstrap)
