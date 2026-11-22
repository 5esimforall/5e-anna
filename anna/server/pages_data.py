"""Stage the read-only query data and book assets for a static Pages export."""
from contextlib import closing
import gzip
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory

from anna.paths import PROJECT
from anna.presentation.catalog import TYPES
from anna.presentation.collections import DEFAULT
from anna.presentation.tour import load as load_curation


CHUNK_SIZE = 1024 * 1024
MAX_CHUNKS = 4096
PAGES_LIMIT = 1024 ** 3
DEFAULT_DB = 'data/extracted/catalog.sqlite'
DEFAULT_BOOK = 'data/5echm-anna'
CURATION = 'tour/tour.json'
HTML_SUFFIXES = {'.htm', '.html'}
WEB_SUFFIXES = {'.css', '.js', '.gif', '.ico', '.jpg', '.jpeg', '.png', '.svg', '.webp', '.avif',
                '.mp3', '.ogg', '.opus', '.wav', '.mp4', '.webm', '.woff', '.woff2', '.otf', '.ttf'}
LOCAL_URL = re.compile(rb'''(?P<quote>["'])file:(?://|\\\\)[^"'<>\r\n]*(?P=quote)|file:(?://|\\\\)[^\s"'<>`)]*''', re.IGNORECASE)

DEFAULT_TABLES = ('metadata', 'sources', 'pages', 'entities', 'entries', 'entry_sources', 'facts',
                  'relations', 'identity_candidates', 'book_search', 'effective_references')


def _json(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _read_json(file):
    return json.loads(file.read_text(encoding='utf-8'))


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


def _project_db(original, target, source):
    with closing(sqlite3.connect(target, uri=True)) as conn, conn:
        conn.execute('ATTACH DATABASE ? AS original', (original.as_uri() + '?mode=ro',))
        schema = conn.execute("SELECT type, tbl_name, name, sql FROM original.sqlite_schema "
                              "WHERE sql IS NOT NULL AND type IN ('table', 'index') ORDER BY rowid").fetchall()
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
        conn.execute('CREATE TABLE pages_source (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, '
                     'html_gzip BLOB NOT NULL)')
        conn.executemany('INSERT INTO pages_source VALUES (?, ?, ?)', _source_rows(source, conn))
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError(f'Invalid foreign keys in projected {original.name}')
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
    with db.open('rb') as f:
        digest = sha256()
        for block in iter(lambda: f.read(CHUNK_SIZE), b''):
            digest.update(block)
    if digest.hexdigest() != manifest['database_sha256']:
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


def export_pages_data(output, *, db, book, source, rewrite_html=None,
                      curation=PROJECT / CURATION, chunk_size=CHUNK_SIZE, gzip_shards=True, size_limit=PAGES_LIMIT):
    """Write static assets and a virtual file index into the caller's output directory.

    ``rewrite_html`` receives (bytes, published POSIX path) for each copied HTML, CSS and JS file.
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
        _project_db(db, default_projection, source)
        manifest_path = work / 'default-manifest.json'
        manifest_path.write_bytes(_json(default_manifest))
        public_curation = work / 'tour.json'
        _public_curation(curation, public_curation)
        files = {DEFAULT_DB: default_projection, f'{DEFAULT_BOOK}/anna-manifest.json': manifest_path,
                 f'{DEFAULT_BOOK}/anna-reader.json': default_reader, CURATION: public_curation}
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
