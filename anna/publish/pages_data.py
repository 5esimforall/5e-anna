"""Stage the read-only query data and book assets for a static Pages export."""
from contextlib import closing
import gzip
from hashlib import sha256
from ipaddress import ip_address
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from anna.paths import PROJECT
from anna.presentation.catalog import TYPES
from anna.presentation.collections import BookCatalog, CollectionRegistry, DEFAULT, _clear_book_connections
from anna.presentation.our5etools import (
    COLLECTION_KEY as OUR5ETOOLS_KEY, FALLBACK_BOOK_ASSETS,
    build_subclass_records, load_our5etools_collection,
)
from anna.presentation.tour import load as load_curation
from anna.publish.pages_buckets import (
    ENTRY_BUCKETS_MAX, ENTRY_BUCKET_TARGET,
    _collect_default_entry_records, _collect_our_entry_records,
    _export_entry_buckets, choose_entry_buckets, entry_bucket_index,
)
from anna.publish.pages_project import (
    DEFAULT_TABLES, HTML_SUFFIXES, LOCAL_URL, OUR5ETOOLS_TABLES,
    _file_hash, _input, _json, _project_db, _project_our_db, _read_json, _scrub_local_urls,
)
from anna.review.store import load_annotations, require_current_annotations
from anna.server import home, tour_cache
from anna.server.pages import PublishedSite
from anna.server.pages_publications import PATH as PUBLICATIONS, build_publications


CHUNK_SIZE = 256 * 1024
MAX_CHUNKS = 4096
PAGES_LIMIT = 1_000_000_000
DEFAULT_DB = 'data/extracted/catalog.sqlite'
DEFAULT_BOOK = 'data/5echm-anna'
OUR5ETOOLS_DATA = 'data/our5etools'
OUR5ETOOLS_READER = 'reader/anna-reader.json'
CURATION = 'tour/tour.json'
WEB_SUFFIXES = {'.css', '.js', '.gif', '.ico', '.jpg', '.jpeg', '.png', '.svg', '.webp', '.avif',
                '.mp3', '.ogg', '.opus', '.wav', '.mp4', '.webm', '.woff', '.woff2', '.otf', '.ttf'}
FONT_NOTICE_ROOTS = ('fonts/', '__ours/fonts/', '__brewrepo/_font/')
FONT_NOTICE_NAMES = ('license', 'ofl', 'copying', 'notice', 'copyright', 'readme')


def _copy_asset(stage, file, target, rewrite_html, *, expected=None):
    raw = file.read_bytes()
    if expected is not None and sha256(raw).hexdigest() != expected:
        raise ValueError(f'Stale generated book asset in Pages export: {target}')
    if file.suffix.lower() in HTML_SUFFIXES | {'.js', '.css', '.svg'}:
        raw = _scrub_local_urls(raw)
        if rewrite_html is not None and file.suffix.lower() != '.svg':
            raw = rewrite_html(raw, target)
    dest = stage / target
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)


def _validate_default_annotations(db, manifest):
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
        db_ann = require_current_annotations(conn)
    manifest_ann = manifest.get('annotation_snapshot')
    if db_ann is not None and manifest_ann != db_ann:
        raise ValueError('Default database and linked book annotation snapshots do not match; run make build')
    if manifest_ann is not None and load_annotations(manifest_ann['path'])[1] != manifest_ann['sha256']:
        raise ValueError('Annotation log does not match the published snapshot; run make build')
    return db_ann or manifest_ann


def _default_book(stage, db, book, rewrite_html):
    manifest = _read_json(_input(book, 'anna-manifest.json'))
    reader_file = _input(book, 'anna-reader.json')
    reader = _read_json(reader_file)
    if manifest['reader_schema'] != reader['schema'] or sha256(reader_file.read_bytes()).hexdigest() != manifest['outputs']['anna-reader.json']:
        raise ValueError('Default reader does not match its manifest; rebuild the linked book')
    if _file_hash(db) != manifest['database_sha256']:
        raise ValueError('Default database does not match the linked book; rebuild the data')
    ann_snapshot = _validate_default_annotations(db, manifest)
    pages = reader['pages']
    if any(not name.startswith('topics/') or PurePosixPath(name).suffix.lower() not in HTML_SUFFIXES
           or name not in manifest['outputs'] for name in pages):
        raise ValueError('Default reader is missing linked pages. Rebuild the linked book before exporting.')
    for name, expected in sorted(manifest['outputs'].items()):
        path = PurePosixPath(name)
        if (name in pages or (path.parts[0] in {'topics', 'assets', 'anna-assets'}
                             and path.suffix.lower() in WEB_SUFFIXES) or name == 'topics/LICENSE'):
            _copy_asset(stage, _input(book, name), 'book/' + name, rewrite_html, expected=expected)
    entries = {entry_id: {key: entry[key] for key in ('path', 'fragment', 'locatable')}
               for entry_id, entry in manifest['entries'].items()}
    return {'reader_schema': manifest['reader_schema'], 'entries': entries}, reader_file, reader, ann_snapshot


def _our_attribution(attribution):
    attribution.pop('source_root', None)
    if 'non_redistributable' in attribution:
        notes = attribution.pop('non_redistributable')
        attribution.setdefault('resource_notes', notes)
    for note in attribution.get('resource_notes', ()):
        if note['kind'] == 'local_fonts' and 'remain in the source mirror' in note.get('reason', ''):
            note['reason'] = 'Bundled fonts retain their upstream licenses and notices.'
        if note['kind'] == 'official_and_partnered_rules' and 'for personal reference only' in note.get('reason', ''):
            note['reason'] = 'Official and partner publications are credited to Wizards of the Coast and their respective partners.'
    return attribution


def _our_media(stage, root, source, inventory, rewrite_html):
    media_root = root / 'media'
    media = {item['path']: item['sha256'] for item in inventory['files'] if item['disposition'] == 'local_media'}
    cached = set()
    for subdir in ('img', 'ext'):
        for file in (media_root / subdir).rglob('*'):
            path = file.relative_to(media_root)
            if (file.is_file() and file.suffix.lower() in (WEB_SUFFIXES | {'.m4a'}) - {'.css', '.js'}
                    and not any(part.startswith('.') for part in path.parts)):
                cached.add(path.as_posix())
    cached.update(name for name in media if name.startswith(('img/', 'ext/')))
    for name in sorted(media.keys() | cached):
        _copy_asset(stage, _input(media_root, name), 'our5etools-media/' + name, rewrite_html,
                    expected=media.get(name))
    for item in inventory['files']:
        name = item['path']
        path = PurePosixPath(name)
        if (name.startswith(FONT_NOTICE_ROOTS) and path.name.casefold().startswith(FONT_NOTICE_NAMES)
                and path.suffix.lower() in {'', '.txt', '.md'}):
            _copy_asset(stage, _input(source, name), 'our5etools-media/' + name, None, expected=item['sha256'])
    return sorted(cached)


def _public_remote_url(value):
    if not isinstance(value, str) or any(ord(char) < 33 or char.isspace() or char == '\\' for char in value):
        return False
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.username is not None or url.password is not None or not url.hostname
                or url.port == 0):
            return False
        host = url.hostname.rstrip('.')
        if host.endswith(('.localhost', '.local', '.localdomain', '.internal', '.test', '.invalid')):
            return False
        try:
            return ip_address(host).is_global
        except ValueError:
            domain = host.encode('idna').decode('ascii')
            return bool(re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]*', domain))
    except (UnicodeError, ValueError):
        return False


def _our_remotes(stage, document, snapshot, cached):
    if document['snapshot'] != snapshot:
        raise ValueError('our5etools remote media metadata does not match its manifest')
    entries = {}
    for name, candidates in document['entries'].items():
        path = PurePosixPath(name)
        if (not name.startswith(('img/', 'ext/')) or name != path.as_posix() or '\\' in name
                or any(part.startswith('.') or '\x00' in part for part in path.parts)):
            continue
        urls = [url for url in candidates if _public_remote_url(url)]
        if urls:
            entries[name] = urls
    (stage / 'our5etools-media-remotes.json').write_bytes(_json({'entries': entries, 'cachedMedia': cached}))


def _our_bundle(stage, work, root, source, rewrite_html):
    manifest = _read_json(_input(root, 'manifest.json'))
    reader_path = _input(root, OUR5ETOOLS_READER)
    db = _input(root, 'catalog.sqlite')
    summary_path = _input(root, 'summary.json')
    attribution_path = _input(root, 'attribution.json')
    inventory_path = _input(root, 'inventory.json')
    remotes_path = _input(root, 'media-remotes.json')
    for file in (reader_path, db, summary_path, attribution_path, inventory_path, remotes_path):
        name = file.relative_to(root).as_posix()
        if _file_hash(file) != manifest['files'].get(name):
            raise ValueError(f'our5etools {name} does not match its manifest. Rebuild the collection before exporting.')
    reader = _read_json(reader_path)
    attribution = _read_json(attribution_path)
    for document in (reader, attribution, _read_json(summary_path)):
        if document['collection'] != 'our5etools' or document['snapshot'] != manifest['snapshot']:
            raise ValueError('our5etools collection metadata does not match its manifest')
    if manifest['collection'] != 'our5etools' or manifest['reader']['schema'] != reader['schema']:
        raise ValueError('our5etools reader does not match its manifest')
    projection = work / 'our5etools.sqlite'
    rank_index_file = work / 'our5etools-rank-index.json.gz'
    _project_our_db(db, projection, rank_index_path=rank_index_file)
    with closing(sqlite3.connect(projection.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        pages = {row[0] for row in conn.execute('SELECT page_path FROM book_pages')}
        metadata = dict(conn.execute('SELECT key,value FROM metadata'))
        located_in_db = {
            row[0] for row in conn.execute(
                "SELECT e.id, json_extract(m.provenance_json, '$.reader_location') "
                'FROM entry_core e JOIN entry_meta m ON m.entry_rowid = e.rowid '
                "WHERE json_extract(m.provenance_json, '$.reader_location') IS NOT NULL"
            ) if json.loads(row[1])
        }
        held_types = sorted(row[0] for row in conn.execute('SELECT DISTINCT type FROM entry_core') if row[0])
        sources_by_id = {
            row['id']: {
                'id': row['id'], 'package_id': row['package_id'], 'origin': row['origin'],
                'ruleset': row['ruleset'], 'short_name': row['short_name'], 'name': row['name'],
                'data': json.loads(row['data_json'] or '{}'),
            }
            for row in conn.execute('SELECT id, package_id, origin, ruleset, short_name, name, data_json FROM sources')
        }
        subclass_records = build_subclass_records(conn, sources_by_id, 'entry_core')
    if pages != reader['pages'].keys() or reader['default_page'] not in pages or any(
        PurePosixPath(name).suffix.lower() not in HTML_SUFFIXES for name in pages
    ):
        raise ValueError('our5etools chapter index does not match the reader model')
    if metadata['snapshot'] != manifest['snapshot'] or metadata['collection'] != 'our5etools':
        raise ValueError('our5etools database metadata does not match its manifest')
    book_root = root / 'reader'
    assets = {path.relative_to(book_root).as_posix() for path in (book_root / 'anna-assets').rglob('*')
              if path.is_file() and path.suffix.lower() in WEB_SUFFIXES}
    assets.update(resource['path'] for resource in reader['presentation_resources'])
    for name in sorted(pages | assets):
        _copy_asset(stage, _input(book_root, name), 'our5etools-book/' + name, rewrite_html)
    for name in sorted(FALLBACK_BOOK_ASSETS):
        asset = f'anna-assets/{name}'
        if asset not in assets:
            fallback = PROJECT / ('web' if name == 'link-labels.js' else 'web/book')
            _copy_asset(stage, _input(fallback, name), 'our5etools-book/' + asset, rewrite_html)
    if source is None:
        source = Path(manifest['source_root']).resolve()
    cached = _our_media(stage, root, source, _read_json(inventory_path), rewrite_html)
    _our_remotes(stage, _read_json(remotes_path), manifest['snapshot'], cached)
    cached_file = work / 'cached-media.json'
    cached_file.write_bytes(_json(cached))
    (stage / 'our5etools-attribution.json').write_bytes(_json(_our_attribution(attribution)))
    roots = {}
    for root_item in reader.get('roots') or ():
        if isinstance(root_item, dict) and root_item.get('source_id') and root_item.get('id'):
            roots.setdefault(root_item['source_id'], root_item['id'])
    extra_locations = {
        eid: loc for eid, loc in (reader.get('entry_locations') or {}).items()
        if eid not in located_in_db
    }
    reader_index_file = work / 'our5etools-reader-index.json'
    reader_index_file.write_bytes(_json({'roots': roots, 'entry_locations': extra_locations}))
    nav_payload = {
        key: reader.get(key)
        for key in ('schema', 'snapshot', 'collection', 'roots', 'nodes', 'default_page',
                    'page_base', 'media_base', 'presentation_resources', 'source_release')
    }
    nav_file = work / 'our5etools-nav-nodes.json.gz'
    nav_file.write_bytes(gzip.compress(_json(nav_payload), compresslevel=6, mtime=0))
    subclasses_file = work / 'our5etools-tour-subclasses.json.gz'
    subclasses_file.write_bytes(gzip.compress(_json(subclass_records), compresslevel=6, mtime=0))
    files = {
        'catalog.sqlite': projection,
        OUR5ETOOLS_READER: reader_path,
        'reader-index.json': reader_index_file,
        'nav-nodes.json.gz': nav_file,
        'tour-subclasses.json.gz': subclasses_file,
        'rank-index.json.gz': rank_index_file,
        'summary.json': summary_path,
        'cached-media.json': cached_file,
    }
    public_manifest = {key: manifest[key] for key in ('schema', 'collection', 'snapshot', 'git_commit',
                                                       'input_sha256', 'built_at', 'counts', 'reader')}
    public_manifest['types'] = held_types
    public_manifest['files'] = {name: _file_hash(file) for name, file in files.items()}
    manifest_file = work / 'our5etools-manifest.json'
    manifest_file.write_bytes(_json(public_manifest))
    files['manifest.json'] = manifest_file
    return {f'{OUR5ETOOLS_DATA}/{name}': file for name, file in files.items()}


def _public_curation(source, target):
    curation = _read_json(source)
    included = set()
    for section in curation['sections']:
        topics = []
        for topic in section['topics']:
            if 'type' in topic.get('query', {}):
                kinds = [kind for kind in topic['query']['type'] if kind in TYPES]
                if kinds:
                    topic['query']['type'] = kinds
                else:
                    topic.pop('query')
                    topic.pop('facet', None)
            for field in ('contents', 'dynamic'):
                value = topic.pop(field, {}).get(DEFAULT)
                if value:
                    topic[field] = {DEFAULT: value}
            if any(field in topic for field in ('contents', 'dynamic', 'query', 'browse')):
                topics.append(topic)
                included.add(topic['key'])
        section['topics'] = topics
    curation['sections'] = [section for section in curation['sections'] if section['topics']]
    choices = curation.get('choices', {}).get(DEFAULT, {})
    curation['choices'] = {DEFAULT: {place: [key for key in keys if key in included]
                                    for place, keys in choices.items()}}
    target.write_bytes(_json(curation))
    load_curation(target)


def _shard(root, files, chunk_size, gzip_shards):
    count = sum((file.stat().st_size + chunk_size - 1) // chunk_size for file in files.values())
    if count > MAX_CHUNKS:
        raise ValueError(f'Pages export requires {count:,} chunks. Increase the chunk size to stay within {MAX_CHUNKS:,}.')
    index = {}
    for name, file in sorted(files.items()):
        total = 0
        compress = gzip_shards and not name.endswith('.gz')
        folder = root / name
        folder.mkdir(parents=True)
        with file.open('rb') as source:
            for part, chunk in enumerate(iter(lambda: source.read(chunk_size), b'')):
                total += len(chunk)
                stored = gzip.compress(chunk, compresslevel=3, mtime=0) if compress else chunk
                (folder / f'{part}.bin').write_bytes(stored)
        index[name] = {'size': total, 'chunkSize': chunk_size}
        if compress:
            index[name]['encoding'] = 'gzip'
    (root / 'index.json').write_bytes(_json(index))


def _write_home_cache(stage, work, files):
    # Give the regular Pages worker adapter the same file layout it will see after publication.
    root = work / 'site-data'
    for name, file in files.items():
        mounted = root / name
        mounted.parent.mkdir(parents=True, exist_ok=True)
        mounted.symlink_to(file)
    site = PublishedSite(root)
    curation_path = getattr(site, 'curation', PROJECT / CURATION)
    payload = home.encode_home(site.handle)
    default_name = home.filename(payload)
    (stage / default_name).write_bytes(payload)
    scopes = {}
    tour_payload = tour_cache.encode_tour(site.handle, curation_path=curation_path)
    tour_default_name = tour_cache.filename(tour_payload)
    (stage / tour_default_name).write_bytes(tour_payload)
    tour_scopes = {}
    for libs in home.extra_scopes(site.registry.ready()):
        scoped = home.encode_home(site.handle, libs)
        name = home.filename(scoped)
        (stage / name).write_bytes(scoped)
        scopes[','.join(libs)] = name
        tour_scoped = tour_cache.encode_tour(site.handle, libs, curation_path=curation_path)
        tour_name = tour_cache.filename(tour_scoped)
        (stage / tour_name).write_bytes(tour_scoped)
        tour_scopes[','.join(libs)] = tour_name
    (stage / 'home-index.json').write_bytes(_json({'default': default_name, 'scopes': scopes}))
    (stage / 'tour-index.json').write_bytes(_json({'default': tour_default_name, 'scopes': tour_scopes}))


def export_pages_data(output, *, db, book, source, our5etools_data=None, our5etools_source=None,
                      rewrite_html=None, curation=PROJECT / CURATION, chunk_size=CHUNK_SIZE, gzip_shards=True,
                      size_limit=PAGES_LIMIT, home_cache=False):
    """Write static assets and a virtual file index into the caller's output directory.

    ``rewrite_html`` receives (bytes, published POSIX path) for each copied HTML, CSS and JS file.
    ``our5etools_data`` adds the full companion collection. Font notices use its build's source mirror
    unless ``our5etools_source`` points to a relocated copy.
    The index records uncompressed lengths and labels gzip shards explicitly.
    ``size_limit`` includes any files the caller has already staged. Returns the resulting byte count.
    """
    output, db, book, source, curation = (Path(path).resolve() for path in (output, db, book, source, curation))
    if not isinstance(chunk_size, int) or not 0 < chunk_size <= 32 * 1024 * 1024:
        raise ValueError('Pages chunk size must be between 1 byte and 32 MiB')
    output.mkdir(parents=True, exist_ok=True)
    existing_bytes = sum(file.stat().st_size for file in output.rglob('*') if file.is_file())
    with TemporaryDirectory(prefix='.pages-data-', dir=output.parent) as temp:
        work = Path(temp)
        stage = work / 'stage'
        stage.mkdir()
        default_manifest, default_reader, default_reader_data, ann_snapshot = _default_book(stage, db, book, rewrite_html)
        if our5etools_data is None:
            public_curation = work / 'tour.json'
            _public_curation(curation, public_curation)
        else:
            public_curation = curation
            load_curation(public_curation)
        locatable = {eid for eid, entry in default_manifest['entries'].items() if entry['locatable']}
        default_projection = work / 'default.sqlite'
        tour_index_path = work / 'tour-index.json.gz'
        default_rank_path = work / 'default-rank-index.json.gz'
        _project_db(
            db, default_projection, source,
            align_works=our5etools_data is not None,
            reader_nodes=default_reader_data['nodes'],
            locatable=locatable,
            curation=public_curation,
            tour_index_path=tour_index_path,
            rank_index_path=default_rank_path,
        )
        manifest_path = work / 'default-manifest.json'
        manifest_path.write_bytes(_json(default_manifest))
        meta_path = work / 'default-meta.json'
        meta_path.write_bytes(_json({
            'reader_schema': default_manifest['reader_schema'],
            'snapshot': default_reader_data['snapshot'],
            'default_page': default_reader_data.get('default_page', ''),
            'source_release': default_reader_data.get('source_release'),
        }))
        files = {DEFAULT_DB: default_projection,
                 'data/extracted/rank-index.json.gz': default_rank_path,
                 f'{DEFAULT_BOOK}/anna-manifest.json': manifest_path,
                 f'{DEFAULT_BOOK}/anna-meta.json': meta_path, f'{DEFAULT_BOOK}/tour-index.json.gz': tour_index_path,
                 f'{DEFAULT_BOOK}/anna-reader.json': default_reader}
        collection = None
        if our5etools_data is not None:
            companion = Path(our5etools_data).resolve()
            companion_source = Path(our5etools_source).resolve() if our5etools_source is not None else None
            files.update(_our_bundle(stage, work, companion, companion_source, rewrite_html))
            collection = load_our5etools_collection(companion, curation_path=public_curation)
            if collection is None:
                raise ValueError('Could not read the validated our5etools collection for work alignment')
            registry = CollectionRegistry(BookCatalog(default_projection, source), (collection,),
                                          book_snapshot=default_reader_data['snapshot'])
            relationships = work / 'publications.json'
            build_publications(relationships, registry)
            files[PUBLICATIONS] = relationships
        files[CURATION] = public_curation
        try:
            if home_cache:
                _write_home_cache(stage, work, files)
            our_projection = files.get(f'{OUR5ETOOLS_DATA}/catalog.sqlite')
            our_reader_index = files.get(f'{OUR5ETOOLS_DATA}/reader-index.json')
            our_extra_locations = (
                _read_json(our_reader_index).get('entry_locations') or {}
                if our_reader_index is not None else {}
            )
            _export_entry_buckets(
                stage,
                default_projection,
                our_projection=our_projection,
                our_media_dir=stage / 'our5etools-media' if our_projection is not None else None,
                our_extra_locations=our_extra_locations,
                gzip_shards=gzip_shards,
            )
            _shard(stage / 'pages-data', files, chunk_size, gzip_shards)
        finally:
            _clear_book_connections()
            if collection is not None and collection.provider is not None:
                collection.provider.close()
        bytes_written = sum(file.stat().st_size for file in stage.rglob('*') if file.is_file())
        if bytes_written + existing_bytes > size_limit:
            raise ValueError(f'Pages output is {bytes_written + existing_bytes:,} bytes, over the {size_limit:,}-byte limit')
        if ann_snapshot is not None and load_annotations(ann_snapshot['path'])[1] != ann_snapshot['sha256']:
            raise ValueError('Annotation log changed during Pages export; run make build')
        staged = sorted(stage.iterdir())
        for file in staged:
            if (output / file.name).exists() or (output / file.name).is_symlink():
                raise FileExistsError(f'Pages output already contains {file.name}')
        for file in staged:
            shutil.move(file, output / file.name)
    return bytes_written + existing_bytes
