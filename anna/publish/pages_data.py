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
from anna.presentation import catalog
from anna.presentation.catalog import TYPES
from anna.presentation.collections import BookCatalog, CollectionRegistry, DEFAULT
from anna.presentation.library import name_fold
from anna.presentation.our5etools import FALLBACK_BOOK_ASSETS, load_our5etools_collection
from anna.presentation.tour import load as load_curation
from anna.server import home
from anna.server.pages import PublishedSite
from anna.server.pages_publications import PATH as PUBLICATIONS, build_publications
import anna.catalog.views as browse
import anna.our5etools.types as otypes


CHUNK_SIZE = 1024 * 1024
MAX_CHUNKS = 4096
PAGES_LIMIT = 1_000_000_000
DEFAULT_DB = 'data/extracted/catalog.sqlite'
DEFAULT_BOOK = 'data/5echm-anna'
OUR5ETOOLS_DATA = 'data/our5etools'
OUR5ETOOLS_READER = 'reader/anna-reader.json'
CURATION = 'tour/tour.json'
HTML_SUFFIXES = {'.htm', '.html'}
WEB_SUFFIXES = {'.css', '.js', '.gif', '.ico', '.jpg', '.jpeg', '.png', '.svg', '.webp', '.avif',
                '.mp3', '.ogg', '.opus', '.wav', '.mp4', '.webm', '.woff', '.woff2', '.otf', '.ttf'}
LOCAL_URL = re.compile(rb'''(?P<quote>["'])file:(?://|\\\\)[^"'<>\r\n]*(?P=quote)|file:(?://|\\\\)[^\s"'<>`)]*''', re.IGNORECASE)

DEFAULT_TABLES = ('metadata', 'sources', 'pages', 'entities', 'entries', 'entry_sources', 'facts',
                  'relations', 'identity_candidates', 'book_search', 'effective_references')
OUR5ETOOLS_TABLES = ('metadata', 'entries', 'entry_sources', 'entry_facets', 'sources', 'packages',
                     'identity_groups', 'identity_members', 'relations', 'book_pages', 'book_search')
FONT_NOTICE_ROOTS = ('fonts/', '__ours/fonts/', '__brewrepo/_font/')
FONT_NOTICE_NAMES = ('license', 'ofl', 'copying', 'notice', 'copyright', 'readme')


def _json(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _read_json(file):
    return json.loads(file.read_text(encoding='utf-8'))


def _file_hash(file):
    digest = sha256()
    with file.open('rb') as f:
        for block in iter(lambda: f.read(CHUNK_SIZE), b''):
            digest.update(block)
    return digest.hexdigest()


def _db_schema(conn, original):
    conn.execute('ATTACH DATABASE ? AS original', (original.as_uri() + '?mode=ro',))
    return conn.execute("SELECT type, tbl_name, name, sql FROM original.sqlite_schema "
                        "WHERE sql IS NOT NULL AND type IN ('table', 'index') ORDER BY rowid").fetchall()


def _input(root, name):
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or name != path.as_posix() or '\\' in name or any(
        part.startswith('.') or part == '__MACOSX' for part in path.parts
    ):
        raise ValueError(f'Invalid Pages input path: {name}')
    file = root / path
    if file.resolve() != file or not file.is_file():
        raise ValueError(f'Missing or symlinked Pages input: {name}')
    return file


def _scrub_local_urls(raw):
    # Preserve source offsets and encodings while disabling links to local machine files.
    def replacement(match):
        quote = match['quote'] or b''
        return quote + b'#' + b'-' * (len(match[0]) - 2 * len(quote) - 1) + quote

    return LOCAL_URL.sub(replacement, raw)


def _source_rows(source, conn):
    for name, digest, text in conn.execute('SELECT path, sha256, text FROM pages ORDER BY path'):
        file = _input(source, name)
        raw = file.read_bytes()
        if sha256(raw).hexdigest() != digest:
            raise ValueError(f'Stale source page in Pages export: {name}')
        public = _scrub_local_urls(raw)
        if public != raw:
            from anna.source.html import Document
            doc = Document(name, public)
            try:
                if text and doc.text != text:
                    raise ValueError(f'Local URL is part of indexed source text: {name}')
            finally:
                doc.close()
        yield name, sha256(public).hexdigest(), gzip.compress(public, compresslevel=3, mtime=0)


def _project_entries(conn):
    # Keeping detail JSON out of the main table lets SQLite skip it for rankings, facets and counts.
    conn.execute('''CREATE TABLE entry_core (
        id TEXT PRIMARY KEY, entity_id TEXT REFERENCES entities(id), name TEXT, name_zh TEXT, name_en TEXT,
        type TEXT, page_path TEXT REFERENCES pages(path), start INTEGER, end INTEGER, ruleset TEXT,
        legacy TEXT, classification_status TEXT, fields TEXT
    )''')
    fields = 'id,entity_id,name,name_zh,name_en,type,page_path,start,end,ruleset,legacy,classification_status,fields'
    conn.execute(f'INSERT INTO entry_core SELECT {fields} FROM original.entries ORDER BY rowid')
    conn.execute('CREATE TABLE entry_data (id TEXT PRIMARY KEY REFERENCES entry_core(id), data TEXT NOT NULL)')
    conn.execute('INSERT INTO entry_data SELECT id,data FROM original.entries ORDER BY rowid')
    conn.execute('CREATE VIEW entries AS SELECT e.*, d.data FROM entry_core e LEFT JOIN entry_data d ON d.id=e.id')


def _project_db(original, target, source, *, align_works=False):
    with closing(sqlite3.connect(target, uri=True)) as conn, conn:
        schema = _db_schema(conn, original)
        definitions = {table: sql for kind, table, _, sql in schema if kind == 'table'}
        for table in DEFAULT_TABLES:
            if table not in definitions:
                raise ValueError(f'Missing table {table} in {original.name}. Rebuild the data before exporting.')
            if table == 'entries':
                _project_entries(conn)
                continue
            conn.execute(definitions[table].replace('REFERENCES entries(', 'REFERENCES entry_core('))
            if table == 'metadata':
                value, = conn.execute("SELECT value FROM original.metadata WHERE key='summary'").fetchone()
                summary = json.loads(value)
                summary.pop('counterparts', None)
                conn.execute('INSERT INTO metadata VALUES (?, ?)', ('summary', _json(summary).decode('utf-8')))
            elif table == 'effective_references':
                # Layout uses resolved targets and their offsets. Reader bindings are already in the generated HTML.
                conn.execute('''INSERT INTO effective_references (id,page_path,start,end,target_id,data)
                                SELECT id,page_path,start,end,target_id,
                                       json_object('target_id',target_id,'start',start,'end',end)
                                FROM original.effective_references WHERE target_id IS NOT NULL ORDER BY rowid''')
            else:
                conn.execute(f'INSERT INTO "{table}" SELECT * FROM original."{table}" ORDER BY rowid')
        for kind, table, name, sql in schema:
            if kind != 'index' or table not in DEFAULT_TABLES:
                continue
            if table == 'entries':
                sql = sql.replace(' ON entries(', ' ON entry_core(')
            if table != 'effective_references' or name == 'effective_reference_page_idx':
                conn.execute(sql)
        if align_works and 'counterpart_sources' in definitions:
            # The library uses reconciled source codes to align the two translations of a work.
            conn.execute('CREATE TABLE counterpart_sources (source_id TEXT, code TEXT, status TEXT, '
                         'PRIMARY KEY(source_id, code)) WITHOUT ROWID')
            conn.execute("INSERT INTO counterpart_sources SELECT source_id, code, status "
                         "FROM original.counterpart_sources WHERE status = '上下文推得' AND code != ''")
        conn.execute('CREATE TABLE pages_source (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, '
                     'html_gzip BLOB NOT NULL)')
        conn.executemany('INSERT INTO pages_source VALUES (?, ?, ?)', _source_rows(source, conn))
        _build_name_fold_index(conn, browse.STACKABLE_TYPES)
        conn.execute('CREATE TABLE identity_candidate_members ('
                     'entry_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, status TEXT NOT NULL) WITHOUT ROWID')
        kinds = ','.join("'" + k + "'" for k in browse.STACKABLE_TYPES)
        conn.execute(
            'INSERT OR IGNORE INTO identity_candidate_members (entry_id, group_id, status) '
            'SELECT j.value, g.group_id, g.status FROM identity_candidates g, '
            f"json_each(g.data, '$.entry_ids') j WHERE g.type IN ({kinds})"
        )
        conn.execute('CREATE INDEX identity_candidate_group_idx ON identity_candidate_members(group_id)')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError(f'Invalid foreign keys in projected {original.name}')
        conn.execute('ANALYZE main')


def _build_name_fold_index(conn, stackable_types):
    conn.execute('CREATE TABLE name_fold_index ('
                 'type TEXT NOT NULL, folded TEXT NOT NULL, entry_id TEXT NOT NULL, '
                 'PRIMARY KEY (type, folded, entry_id)) WITHOUT ROWID')
    kinds = ','.join("'" + k + "'" for k in stackable_types)
    rows = []
    for eid, typ, name_en in conn.execute(
        f"SELECT id, type, name_en FROM entry_core WHERE type IN ({kinds}) AND name_en IS NOT NULL AND name_en != ''"
    ):
        folded = name_fold(name_en)
        if folded:
            rows.append((typ, folded, eid))
    conn.executemany('INSERT OR IGNORE INTO name_fold_index VALUES (?, ?, ?)', rows)


def _project_our_entries(conn):
    # List queries can avoid reading body and layout data, and body searches can skip rendered HTML.
    columns = conn.execute('PRAGMA original.table_info(entries)').fetchall()
    payloads = {'body_text': 'entry_body', 'search_body': 'entry_body', 'layout_html': 'entry_layout'}
    core = [(name, kind, required, pk) for _, name, kind, required, _, pk in columns if name not in payloads]
    declarations = [f'{name} {kind}' + (' PRIMARY KEY' if pk else '') + (' NOT NULL' if required else '')
                    for name, kind, required, pk in core]
    conn.execute('CREATE TABLE entry_core (' + ', '.join(declarations) + ')')
    names = 'rowid, ' + ', '.join(name for name, *_ in core)
    conn.execute(f'INSERT INTO entry_core ({names}) SELECT {names} FROM original.entries ORDER BY rowid')
    conn.execute('CREATE TABLE entry_body (entry_rowid INTEGER PRIMARY KEY, body_text TEXT NOT NULL, search_body TEXT NOT NULL)')
    conn.execute('CREATE TABLE entry_layout (entry_rowid INTEGER PRIMARY KEY, layout_html TEXT NOT NULL)')
    conn.execute('INSERT INTO entry_body SELECT rowid, body_text, search_body FROM original.entries ORDER BY rowid')
    conn.execute('INSERT INTO entry_layout SELECT rowid, layout_html FROM original.entries ORDER BY rowid')
    fields = ', '.join(f'{payloads.get(name, "entry_core")}.{name}' for _, name, *_ in columns)
    conn.execute(f'''CREATE VIEW entries AS SELECT {fields}, entry_core.rowid AS rowid FROM entry_core
                    LEFT JOIN entry_body ON entry_body.entry_rowid=entry_core.rowid
                    LEFT JOIN entry_layout ON entry_layout.entry_rowid=entry_core.rowid''')


def _project_our_db(original, target):
    with closing(sqlite3.connect(target, uri=True)) as conn, conn:
        schema = _db_schema(conn, original)
        definitions = {table: sql for kind, table, _, sql in schema if kind == 'table'}
        for table in OUR5ETOOLS_TABLES:
            if table not in definitions:
                raise ValueError(f'Missing our5etools table {table}. Rebuild the data before exporting.')
            if table == 'entries':
                _project_our_entries(conn)
                continue
            conn.execute(definitions[table])
            where = (" WHERE key IN ('schema','reader_schema','snapshot','built_at','collection','summary')"
                     if table == 'metadata' else '')
            conn.execute(f'INSERT INTO "{table}" SELECT * FROM original."{table}"{where} ORDER BY rowid')
        for kind, table, _, sql in schema:
            if kind == 'index' and table in OUR5ETOOLS_TABLES:
                conn.execute(re.sub(r'\bON\s+entries\s*\(', 'ON entry_core(', sql, flags=re.I) if table == 'entries' else sql)
        _build_name_fold_index(conn, otypes.STACKABLE_TYPES)
        conn.execute('CREATE TABLE pages_copied_bases ('
                     'source_id TEXT PRIMARY KEY, target_id TEXT NOT NULL, '
                     'base_source_id TEXT NOT NULL, base_name_en TEXT NOT NULL) WITHOUT ROWID')
        conn.execute(
            'INSERT OR IGNORE INTO pages_copied_bases (source_id, target_id, base_source_id, base_name_en) '
            'SELECT r.source_id, r.target_id, b.source_id, b.name_en '
            "FROM relations r JOIN entry_core b ON b.id = r.target_id "
            "WHERE r.type = 'copied_from' ORDER BY r.source_id, r.target_id"
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_identity_members_group ON identity_members(group_id)')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Invalid foreign keys in the projected our5etools catalog')
        conn.execute('ANALYZE main')


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


def _default_book(stage, db, book, rewrite_html):
    manifest = _read_json(_input(book, 'anna-manifest.json'))
    reader_file = _input(book, 'anna-reader.json')
    reader = _read_json(reader_file)
    if manifest['reader_schema'] != reader['schema'] or sha256(reader_file.read_bytes()).hexdigest() != manifest['outputs']['anna-reader.json']:
        raise ValueError('Default reader does not match its manifest; rebuild the linked book')
    if _file_hash(db) != manifest['database_sha256']:
        raise ValueError('Default database does not match the linked book; rebuild the data')
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
    return {'reader_schema': manifest['reader_schema'], 'entries': entries}, reader_file


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
            # The collection build keeps notice hashes in inventory but stages the fonts alone.
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
    _project_our_db(db, projection)
    with closing(sqlite3.connect(projection.as_uri() + '?mode=ro', uri=True)) as conn:
        pages = {row[0] for row in conn.execute('SELECT page_path FROM book_pages')}
        metadata = dict(conn.execute('SELECT key,value FROM metadata'))
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
    files = {'catalog.sqlite': projection, OUR5ETOOLS_READER: reader_path,
             'summary.json': summary_path, 'cached-media.json': cached_file}
    public_manifest = {key: manifest[key] for key in ('schema', 'collection', 'snapshot', 'git_commit',
                                                       'input_sha256', 'built_at', 'counts', 'reader')}
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
        folder = root / name
        folder.mkdir(parents=True)
        with file.open('rb') as source:
            for part, chunk in enumerate(iter(lambda: source.read(chunk_size), b'')):
                total += len(chunk)
                stored = gzip.compress(chunk, compresslevel=3, mtime=0) if gzip_shards else chunk
                (folder / f'{part}.bin').write_bytes(stored)
        index[name] = {'size': total, 'chunkSize': chunk_size}
        if gzip_shards:
            index[name]['encoding'] = 'gzip'
    (root / 'index.json').write_bytes(_json(index))


def _write_home_cache(stage, work, files):
    # Give the regular Pages worker adapter the same file layout it will see after publication.
    root = work / 'site-data'
    for name, file in files.items():
        mounted = root / name
        mounted.parent.mkdir(parents=True, exist_ok=True)
        mounted.symlink_to(file)
    payload = home.encode_home(PublishedSite(root).handle)
    (stage / home.filename(payload)).write_bytes(payload)


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
        default_manifest, default_reader = _default_book(stage, db, book, rewrite_html)
        default_projection = work / 'default.sqlite'
        _project_db(db, default_projection, source, align_works=our5etools_data is not None)
        manifest_path = work / 'default-manifest.json'
        manifest_path.write_bytes(_json(default_manifest))
        files = {DEFAULT_DB: default_projection, f'{DEFAULT_BOOK}/anna-manifest.json': manifest_path,
                 f'{DEFAULT_BOOK}/anna-reader.json': default_reader}
        if our5etools_data is None:
            public_curation = work / 'tour.json'
            _public_curation(curation, public_curation)
        else:
            public_curation = curation
            load_curation(public_curation)
            companion = Path(our5etools_data).resolve()
            companion_source = Path(our5etools_source).resolve() if our5etools_source is not None else None
            files.update(_our_bundle(stage, work, companion, companion_source, rewrite_html))
            collection = load_our5etools_collection(companion, curation_path=public_curation)
            if collection is None:
                raise ValueError('Could not read the validated our5etools collection for work alignment')
            registry = CollectionRegistry(BookCatalog(default_projection, source), (collection,),
                                          book_snapshot=_read_json(default_reader)['snapshot'])
            relationships = work / 'publications.json'
            build_publications(relationships, registry)
            files[PUBLICATIONS] = relationships
        files[CURATION] = public_curation
        if home_cache:
            _write_home_cache(stage, work, files)
        _shard(stage / 'pages-data', files, chunk_size, gzip_shards)
        bytes_written = sum(file.stat().st_size for file in stage.rglob('*') if file.is_file())
        if bytes_written + existing_bytes > size_limit:
            raise ValueError(f'Pages output is {bytes_written + existing_bytes:,} bytes, over the {size_limit:,}-byte limit')
        staged = sorted(stage.iterdir())
        for file in staged:
            if (output / file.name).exists() or (output / file.name).is_symlink():
                raise FileExistsError(f'Pages output already contains {file.name}')
        for file in staged:
            shutil.move(file, output / file.name)
    return bytes_written + existing_bytes
