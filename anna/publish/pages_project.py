"""SQLite database projections, layout pre-rendering, and Tour/rank index builders for static Pages exports."""
from collections import defaultdict
from contextlib import closing
import gzip
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3

from anna.presentation.layout import (
    _span_ref_layers, render_entry_layout, render_span_layout, text_start, tour_span_key,
)
from anna.presentation.library import name_fold
from anna.presentation.tour import LEAD_LIMIT, RULESETS, Tour, clip, first_sentence, load as load_curation
from anna.source.html import Document
from anna.source.pages import decode_page
import anna.catalog.views as browse
import anna.our5etools.types as otypes


HTML_SUFFIXES = {'.htm', '.html'}
LOCAL_URL = re.compile(
    rb'''(?P<quote>["'])file:(?://|\\\\)[^"'<>\r\n]*(?P=quote)|file:(?://|\\\\)[^\s"'<>`)]*''',
    re.IGNORECASE,
)

DEFAULT_TABLES = (
    'metadata', 'sources', 'pages', 'entities', 'entries', 'entry_sources',
    'relations', 'identity_candidates', 'book_search',
)
OUR5ETOOLS_TABLES = (
    'metadata', 'entries', 'entry_sources', 'entry_facets', 'sources', 'packages',
    'identity_groups', 'identity_members', 'relations', 'book_pages', 'book_search',
)


def _json(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _read_json(file):
    return json.loads(file.read_text(encoding='utf-8'))


def _file_hash(file):
    digest = sha256()
    with Path(file).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _db_schema(conn, original):
    conn.execute('ATTACH DATABASE ? AS original', (original.as_uri() + '?mode=ro',))
    return conn.execute(
        "SELECT type, tbl_name, name, sql FROM original.sqlite_schema "
        "WHERE sql IS NOT NULL AND type IN ('table', 'index') ORDER BY rowid"
    ).fetchall()


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


def _make_interner():
    arr = []
    idx = {}

    def intern(value):
        if value is None or value == '':
            return -1
        hit = idx.get(value)
        if hit is None:
            hit = len(arr)
            arr.append(value)
            idx[value] = hit
        return hit

    return arr, intern


def _prerender_layouts(conn, source, *, tour=None):
    conn.execute('CREATE TABLE pages_entry_layout (entry_rowid INTEGER PRIMARY KEY, payload_gzip BLOB NOT NULL)')
    conn.execute('CREATE TABLE pages_tour_layout (span_key TEXT PRIMARY KEY, payload_gzip BLOB NOT NULL) WITHOUT ROWID')

    entries_by_page = defaultdict(list)
    for rowid, eid, page_path, data_json in conn.execute(
        'SELECT e.rowid, e.id, e.page_path, d.data FROM entry_core e JOIN entry_data d ON d.entry_rowid = e.rowid ORDER BY e.rowid'
    ):
        entries_by_page[page_path].append((rowid, eid, data_json))

    facts_by_entry = defaultdict(list)
    for eid, field, value, evidence, rule, status in conn.execute(
        'SELECT entry_id, field, value, evidence, rule, status FROM original.facts ORDER BY id'
    ):
        if field not in browse.METADATA_FIELDS:
            facts_by_entry[eid].append((field, value, evidence, rule, status))

    refs_by_page = defaultdict(list)
    if conn.execute("SELECT 1 FROM original.sqlite_master WHERE type='table' AND name='effective_references'").fetchone():
        ref_cols = {r[1] for r in conn.execute('PRAGMA original.table_info(effective_references)')}
        clauses = ['target_id IS NOT NULL', 'end > start']
        if 'can_link' in ref_cols:
            clauses.append('can_link=1')
        if 'is_effective' in ref_cols:
            clauses.append('is_effective=1')
        for page_path, start, end, target_id in conn.execute(
            'SELECT page_path, start, end, target_id FROM original.effective_references '
            f"WHERE {' AND '.join(clauses)} ORDER BY page_path, start, id"
        ):
            refs_by_page[page_path].append((start, end, target_id))

    tour_spans_by_page = defaultdict(list)
    if tour is not None:
        seen_spans = set()
        for node in tour.nodes.values():
            if node['status'] == 'present':
                path, start, stop, owner = tour.page_span(node['path'])
                key = (path, start, stop, owner)
                if key not in seen_spans:
                    seen_spans.add(key)
                    tour_spans_by_page[path].append((start, stop, owner))
        for entry in tour.entries.values():
            path, start, stop, owner = tour.entry_span(entry)
            key = (path, start, stop, owner)
            if key not in seen_spans:
                seen_spans.add(key)
                tour_spans_by_page[path].append((start, stop, owner))

    entry_rows = []
    tour_rows = []
    for name, digest, text, parse_status in conn.execute('SELECT path, sha256, text, parse_status FROM original.pages ORDER BY path'):
        file = _input(source, name)
        raw = file.read_bytes()
        if sha256(raw).hexdigest() != digest:
            raise ValueError(f'Stale source page in Pages export: {name}')
        if parse_status == 'excluded' and not text and not entries_by_page.get(name):
            continue
        public = _scrub_local_urls(raw)
        html_text, _, _ = decode_page(public)
        doc = Document(name, html_text)
        try:
            if doc.text != text:
                if public != raw:
                    raise ValueError(f'Local URL is part of indexed source text: {name}')
                raise ValueError(f'Stale source page in Pages export: {name}')
            page_refs = refs_by_page.get(name, ())
            for rowid, eid, data_json in entries_by_page.get(name, ()):
                entry = json.loads(data_json)
                ref_layers = _span_ref_layers(page_refs, text_start(entry), entry['end'], eid)
                payload = render_entry_layout(doc, entry, facts_by_entry.get(eid, ()), ref_layers)
                entry_rows.append((rowid, gzip.compress(_json(payload), compresslevel=3, mtime=0)))
            for start, stop, owner in tour_spans_by_page.get(name, ()):
                ref_layers = _span_ref_layers(page_refs, start, stop, owner)
                payload = render_span_layout(doc, name, start, stop, ref_layers)
                if payload['status'] == 'ok':
                    tour_rows.append((tour_span_key(name, start, stop, owner),
                                      gzip.compress(_json(payload), compresslevel=3, mtime=0)))
        finally:
            doc.close()
    entry_rows.sort(key=lambda row: row[0])
    tour_rows.sort(key=lambda row: row[0])
    conn.executemany('INSERT INTO pages_entry_layout VALUES (?, ?)', entry_rows)
    conn.executemany('INSERT INTO pages_tour_layout VALUES (?, ?)', tour_rows)


def _project_entries(conn):
    # Cluster non-section rows in UI display order before sections and split field JSON from entry_core.
    conn.execute('''CREATE TABLE entry_core (
        id TEXT NOT NULL, entity_id TEXT REFERENCES entities(id), name TEXT, name_zh TEXT, name_en TEXT,
        type TEXT, page_path TEXT REFERENCES pages(path), start INTEGER, end INTEGER, ruleset TEXT,
        legacy TEXT, classification_status TEXT, primary_source_id TEXT REFERENCES sources(id)
    )''')
    conn.execute('CREATE TABLE entry_fields (entry_rowid INTEGER PRIMARY KEY, fields TEXT)')
    conn.execute('CREATE TABLE entry_data (entry_rowid INTEGER PRIMARY KEY, data TEXT NOT NULL)')

    sources_by_entry = defaultdict(list)
    for eid, sid in conn.execute('SELECT entry_id, source_id FROM original.entry_sources ORDER BY entry_id, source_id'):
        sources_by_entry[eid].append(sid)
    single_source = all(len(v) <= 1 for v in sources_by_entry.values())

    section_rank = len(browse.TYPE_ORDER)
    type_rank_map = {t: i for i, t in enumerate(browse.TYPE_ORDER) if t != 'section'}
    rows = []
    for row in conn.execute(
        'SELECT id, entity_id, name, name_zh, name_en, type, page_path, start, end, ruleset, '
        'legacy, classification_status, fields, data FROM original.entries ORDER BY rowid'
    ):
        eid, _, name, name_zh, _, typ = row[:6]
        t_rank = section_rank if typ == 'section' else type_rank_map.get(typ, section_rank - 1)
        sort_key = (t_rank, browse.name_key(name_zh or name) or '', name or '', eid)
        primary_src = sources_by_entry[eid][0] if sources_by_entry.get(eid) else None
        rows.append((sort_key, row[:12] + (primary_src,), row[12], row[13]))
    rows.sort(key=lambda item: item[0])

    core_rows = []
    field_rows = []
    data_rows = []
    non_section_max = 0
    for idx, (_, core_vals, fields_json, data_json) in enumerate(rows, start=1):
        if core_vals[5] != 'section':
            non_section_max = idx
        core_rows.append((idx, *core_vals))
        field_rows.append((idx, fields_json))
        data_rows.append((idx, data_json))

    core_cols = ('id,entity_id,name,name_zh,name_en,type,page_path,start,end,ruleset,'
                 'legacy,classification_status,primary_source_id')
    conn.executemany(f'INSERT INTO entry_core (rowid,{core_cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', core_rows)
    conn.executemany('INSERT INTO entry_fields VALUES (?, ?)', field_rows)
    conn.executemany('INSERT INTO entry_data VALUES (?, ?)', data_rows)
    conn.execute(
        f'CREATE VIEW entries AS SELECT {", ".join("e." + c.strip() for c in core_cols.split(","))}, '
        'f.fields, d.data FROM entry_core e '
        'LEFT JOIN entry_fields f ON f.entry_rowid=e.rowid '
        'LEFT JOIN entry_data d ON d.entry_rowid=e.rowid'
    )
    return non_section_max, single_source


def _write_default_rank_index(conn, rank_index_path, origin_of, sources_by_entry):
    types_lut, it = _make_interner()
    paths_lut, ip = _make_interner()
    sources_lut, isrc = _make_interner()
    statuses_lut, istat = _make_interner()
    rows = []
    for eid, typ, name, name_zh, name_en, ruleset, page_path, start, gid, status in conn.execute(
        "SELECT e.id, e.type, e.name, e.name_zh, e.name_en, e.ruleset, e.page_path, e.start, "
        "m.group_id, m.status FROM entry_core e "
        "LEFT JOIN identity_candidate_members m ON m.entry_id = e.id "
        "WHERE e.type != 'section' ORDER BY e.rowid"
    ):
        sids = sources_by_entry.get(eid) or []
        if not sids:
            src_val = -1
        elif len(sids) == 1:
            src_val = isrc(sids[0])
        else:
            src_val = [isrc(s) for s in sids]
        nz = None if name_zh == name else name_zh
        rows.append([
            eid,
            it(typ),
            name or '',
            nz,
            name_en or '',
            ruleset or '',
            gid,
            istat(status),
            ip(page_path),
            start,
            src_val,
        ])
    origins = {sid: (origin_of.get(sid) or '待核') for sid in sources_lut}
    payload = {
        'schema': 1,
        'types': types_lut,
        'paths': paths_lut,
        'sources': sources_lut,
        'statuses': statuses_lut,
        'origins': origins,
        'rows': rows,
    }
    rank_index_path.write_bytes(gzip.compress(_json(payload), compresslevel=6, mtime=0))


def _write_default_metadata_and_tour(conn, tour, reader_nodes, tour_index_path, *,
                                     non_section_max=None, single_source=False, rank_index_path=None):
    """Store precomputed catalog counts/facets and lazy per-page Tour tables in the projected DB."""
    source_counts = dict(conn.execute(
        "SELECT es.source_id, count(*) FROM entry_sources es JOIN entry_core e ON e.id=es.entry_id "
        "WHERE e.type!='section' GROUP BY es.source_id"
    ))
    type_ruleset_counts = [
        [r[0], r[1], r[2]]
        for r in conn.execute(
            "SELECT type, COALESCE(ruleset, ''), count(*) FROM entry_core "
            "GROUP BY type, COALESCE(ruleset, '') ORDER BY type, COALESCE(ruleset, '')"
        )
    ]
    origin_of = dict(conn.execute('SELECT id, origin FROM sources'))
    unfiltered_type = {}
    unfiltered_ruleset = {}
    unfiltered_origin = {}
    unfiltered_total = 0
    for typ, rs in conn.execute("SELECT type, ruleset FROM entry_core WHERE type!='section'"):
        unfiltered_total += 1
        unfiltered_type[typ] = unfiltered_type.get(typ, 0) + 1
        unfiltered_ruleset[rs] = unfiltered_ruleset.get(rs, 0) + 1
    sources_by_entry = defaultdict(list)
    for eid, sid in conn.execute(
        "SELECT es.entry_id, es.source_id FROM entry_sources es "
        "JOIN entry_core e ON e.id=es.entry_id WHERE e.type!='section' ORDER BY es.entry_id, es.source_id"
    ):
        sources_by_entry[eid].append(sid)
    for sids in sources_by_entry.values():
        for orig in dict.fromkeys(origin_of.get(s) for s in sids if origin_of.get(s)):
            unfiltered_origin[orig] = unfiltered_origin.get(orig, 0) + 1
    unfiltered_facets = {
        'total': unfiltered_total,
        'type': unfiltered_type,
        'ruleset': unfiltered_ruleset,
        'origin': unfiltered_origin,
        'source': source_counts,
    }
    meta_rows = [
        ('pages_source_counts', _json(source_counts).decode('utf-8')),
        ('pages_type_ruleset_counts', _json(type_ruleset_counts).decode('utf-8')),
        ('pages_unfiltered_facets', _json(unfiltered_facets).decode('utf-8')),
        ('pages_single_source', '1' if single_source else '0'),
    ]
    if non_section_max is not None:
        meta_rows.append(('pages_non_section_max_rowid', str(non_section_max)))
    conn.executemany('INSERT INTO metadata VALUES (?, ?)', meta_rows)

    if rank_index_path is not None:
        _write_default_rank_index(conn, rank_index_path, origin_of, sources_by_entry)
    if tour is None:
        return

    conn.execute('''CREATE TABLE pages_tour_entries (
        page_path TEXT NOT NULL,
        start INTEGER NOT NULL,
        end INTEGER NOT NULL,
        id TEXT NOT NULL,
        name TEXT NOT NULL,
        name_zh TEXT NOT NULL,
        name_en TEXT NOT NULL,
        type TEXT NOT NULL,
        ruleset TEXT NOT NULL,
        title_end INTEGER,
        parent_id TEXT NOT NULL,
        is_primary INTEGER NOT NULL,
        has_table INTEGER NOT NULL,
        parts_count INTEGER NOT NULL,
        PRIMARY KEY (page_path, start, id)
    ) WITHOUT ROWID''')
    tour_entry_rows = [
        (
            e.path, e.start, e.end, e.id, e.name, e.name_zh, e.name_en,
            e.type, e.ruleset, e.title_end, e.parent, int(e.primary), int(e.table),
            len(tour.parts.get(e.id, ())),
        )
        for e in sorted(tour.entries.values(), key=lambda item: (item.path, item.start, item.id))
    ]
    conn.executemany('INSERT INTO pages_tour_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', tour_entry_rows)
    conn.execute('CREATE UNIQUE INDEX pages_tour_entries_id_idx ON pages_tour_entries(id)')

    conn.execute('''CREATE TABLE pages_tour_page (
        path TEXT PRIMARY KEY,
        source_ids TEXT NOT NULL,
        hint TEXT NOT NULL,
        lead_text TEXT NOT NULL,
        lead_truncated INTEGER NOT NULL,
        span_start INTEGER NOT NULL,
        span_stop INTEGER NOT NULL,
        span_owner TEXT NOT NULL
    ) WITHOUT ROWID''')
    page_rows = []
    for path, source_ids in conn.execute("SELECT path, COALESCE(source_ids, '[]') FROM original.pages ORDER BY path"):
        _, span_start, span_stop, span_owner = tour.page_span(path)
        full_lead = tour.span_text((path, span_start, span_stop, span_owner))
        hint = first_sentence(full_lead)
        lead_text, lead_truncated = clip(full_lead, LEAD_LIMIT) if full_lead else ('', False)
        page_rows.append((path, source_ids, hint, lead_text, int(lead_truncated), span_start, span_stop, span_owner or ''))
    conn.executemany('INSERT INTO pages_tour_page VALUES (?,?,?,?,?,?,?,?)', page_rows)

    if tour_index_path is None:
        return
    class_spell_counts = {}
    for e in tour.entries.values():
        if e.type == 'class' and e.name_zh:
            rs = e.ruleset if e.ruleset in RULESETS else ''
            key = f'{e.name_zh}\0{rs}'
            if key not in class_spell_counts:
                q = {'type': ['spell'], 'f_spell_classes': [e.name_zh], **({'ruleset': [rs]} if rs else {})}
                class_spell_counts[key] = tour.query_count(q)
    index_payload = {
        'schema': 1,
        'nodes': [[n['id'], n['parent_id'], n['label'], n['path'], n['fragment'], n['status']] for n in reader_nodes],
        'unlocatable': sorted(eid for eid in tour.entries if eid not in tour.locatable),
        'sources': tour.source_names,
        'below': {nid: cnt for nid in tour.nodes if (cnt := tour.below(nid)) is not None},
        'parent_class': tour.parent_class,
        'subclasses': dict(tour.subclasses),
        'feats': dict(tour.feats),
        'generic_subclass_titles': tour._generic_subclass_titles,
        'subclass_records': tour.subclass_records(),
        'class_spell_counts': class_spell_counts,
    }
    tour_index_path.write_bytes(gzip.compress(_json(index_payload), compresslevel=6, mtime=0))


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
    rows.sort()
    conn.executemany('INSERT OR IGNORE INTO name_fold_index VALUES (?, ?, ?)', rows)


def _project_db(original, target, source, *, align_works=False, reader_nodes=None, locatable=None, curation=None,
                tour_index_path=None, rank_index_path=None):
    """Project the default `catalog.sqlite` into a clustered, pre-rendered read-only Pages database."""
    with closing(sqlite3.connect(target, uri=True)) as conn, conn:
        schema = _db_schema(conn, original)
        definitions = {table: sql for kind, table, _, sql in schema if kind == 'table'}
        non_section_max = None
        single_source = False
        for table in DEFAULT_TABLES:
            if table not in definitions:
                raise ValueError(f'Missing table {table} in {original.name}. Rebuild the data before exporting.')
            if table == 'entries':
                non_section_max, single_source = _project_entries(conn)
                continue
            if table == 'pages':
                conn.execute(re.sub(r'\bpath\s+TEXT\s+PRIMARY\s+KEY\b', 'path TEXT NOT NULL', definitions['pages'], flags=re.I))
                conn.execute('INSERT INTO pages SELECT * FROM original.pages ORDER BY path')
                continue
            if table == 'entry_sources':
                conn.execute('''CREATE TABLE entry_sources (
                    entry_id TEXT NOT NULL REFERENCES entry_core(id),
                    source_id TEXT NOT NULL REFERENCES sources(id),
                    PRIMARY KEY (entry_id, source_id)
                ) WITHOUT ROWID''')
                conn.execute('INSERT INTO entry_sources SELECT entry_id, source_id FROM original.entry_sources ORDER BY entry_id, source_id')
                continue
            if table == 'relations':
                conn.execute('''CREATE TABLE relations (
                    id TEXT NOT NULL,
                    subject_id TEXT NOT NULL REFERENCES entry_core(id),
                    predicate TEXT NOT NULL,
                    object_id TEXT REFERENCES entry_core(id),
                    target_label TEXT NOT NULL,
                    target_locator TEXT,
                    evidence TEXT,
                    status TEXT,
                    PRIMARY KEY (subject_id, predicate, target_label, id)
                ) WITHOUT ROWID''')
                conn.execute(
                    "INSERT INTO relations (id, subject_id, predicate, object_id, target_label, target_locator, evidence, status) "
                    "SELECT id, subject_id, COALESCE(predicate, ''), object_id, COALESCE(target_label, ''), target_locator, evidence, status "
                    'FROM original.relations ORDER BY subject_id, predicate, target_label, id'
                )
                continue
            conn.execute(definitions[table].replace('REFERENCES entries(', 'REFERENCES entry_core('))
            if table == 'metadata':
                value, = conn.execute("SELECT value FROM original.metadata WHERE key='summary'").fetchone()
                summary = json.loads(value)
                summary.pop('counterparts', None)
                conn.execute('INSERT INTO metadata VALUES (?, ?)', ('summary', _json(summary).decode('utf-8')))
            else:
                conn.execute(f'INSERT INTO "{table}" SELECT * FROM original."{table}" ORDER BY rowid')
        conn.execute('CREATE UNIQUE INDEX pages_path_idx ON pages(path)')
        conn.execute('CREATE UNIQUE INDEX entry_core_id_idx ON entry_core(id)')
        for kind, table, name, sql in schema:
            if kind != 'index' or table not in DEFAULT_TABLES or name == 'relation_subject_idx':
                continue
            if table == 'entries':
                sql = sql.replace(' ON entries(', ' ON entry_core(')
            conn.execute(sql)
        if align_works and 'counterpart_sources' in definitions:
            conn.execute('CREATE TABLE counterpart_sources (source_id TEXT, code TEXT, status TEXT, '
                         'PRIMARY KEY(source_id, code)) WITHOUT ROWID')
            conn.execute("INSERT INTO counterpart_sources SELECT source_id, code, status "
                         "FROM original.counterpart_sources WHERE status = '上下文推得' AND code != ''")
        tour = (Tour(original, reader_nodes, locatable or set(), load_curation(curation), source)
                if reader_nodes is not None and curation is not None else None)
        _prerender_layouts(conn, source, tour=tour)
        _build_name_fold_index(conn, browse.STACKABLE_TYPES)
        conn.execute('CREATE TABLE identity_candidate_members ('
                     'entry_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, status TEXT NOT NULL) WITHOUT ROWID')
        kinds = ','.join("'" + k + "'" for k in browse.STACKABLE_TYPES)
        conn.execute(
            'INSERT OR IGNORE INTO identity_candidate_members (entry_id, group_id, status) '
            'SELECT j.value, g.group_id, g.status FROM identity_candidates g, '
            f"json_each(g.data, '$.entry_ids') j WHERE g.type IN ({kinds}) ORDER BY j.value"
        )
        conn.execute('CREATE INDEX identity_candidate_group_idx ON identity_candidate_members(group_id)')
        _write_default_metadata_and_tour(
            conn, tour, reader_nodes, tour_index_path,
            non_section_max=non_section_max, single_source=single_source, rank_index_path=rank_index_path,
        )
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError(f'Invalid foreign keys in projected {original.name}')
        conn.execute('ANALYZE main')


def _project_our_entries(conn):
    # Cluster rows in UI default display order (TYPE_ORDER, sort_name, id) and compress cold layout/body blobs.
    columns = conn.execute('PRAGMA original.table_info(entries)').fetchall()
    payload_tables = {
        'fields_json': 'entry_fields',
        'quickref_json': 'entry_fields',
        'provenance_json': 'entry_meta',
        'body_text': 'entry_body_text',
        'search_body': 'entry_search_body',
        'layout_html': 'entry_layout',
    }
    core = [(name, kind, required, pk) for _, name, kind, required, _, pk in columns if name not in payload_tables]
    declarations = [f'{name} {kind}' + (' NOT NULL' if required or pk else '')
                    for name, kind, required, pk in core]
    declarations.append('build_order INTEGER NOT NULL')
    conn.execute('CREATE TABLE entry_core (' + ', '.join(declarations) + ')')
    conn.execute('CREATE TABLE entry_fields (entry_rowid INTEGER PRIMARY KEY, fields_json TEXT NOT NULL, quickref_json TEXT NOT NULL)')
    conn.execute('CREATE TABLE entry_meta (entry_rowid INTEGER PRIMARY KEY, provenance_json TEXT NOT NULL)')
    conn.execute('CREATE TABLE entry_body_text (entry_rowid INTEGER PRIMARY KEY, body_gzip BLOB NOT NULL)')
    conn.execute('CREATE TABLE entry_search_body (entry_rowid INTEGER PRIMARY KEY, search_body TEXT NOT NULL)')
    conn.execute('CREATE TABLE entry_layout (entry_rowid INTEGER PRIMARY KEY, layout_gzip BLOB NOT NULL)')

    o_type_rank = {t: i for i, t in enumerate(otypes.TYPE_ORDER)}
    fallback_rank = len(otypes.TYPE_ORDER)
    core_names = [name for name, *_ in core]
    core_select = ', '.join(core_names)
    rows_raw = conn.execute(
        f'SELECT rowid, {core_select}, fields_json, quickref_json, provenance_json, body_text, search_body, layout_html '
        'FROM original.entries ORDER BY rowid'
    ).fetchall()
    n_core = len(core_names)
    id_pos = core_names.index('id') + 1
    type_pos = core_names.index('type') + 1
    sort_pos = core_names.index('sort_name') + 1

    rows_raw.sort(key=lambda r: (o_type_rank.get(r[type_pos], fallback_rank), r[sort_pos] or '', r[id_pos]))

    core_rows = []
    field_rows = []
    meta_rows = []
    body_rows = []
    search_rows = []
    layout_rows = []
    for new_rowid, row in enumerate(rows_raw, start=1):
        orig_rowid = row[0]
        core_vals = row[1:1 + n_core]
        fields_json, quickref_json, provenance_json, body_text, search_body, layout_html = row[1 + n_core:]
        core_rows.append((new_rowid, *core_vals, orig_rowid))
        field_rows.append((new_rowid, fields_json, quickref_json))
        meta_rows.append((new_rowid, provenance_json))
        body_rows.append((new_rowid, gzip.compress((body_text or '').encode('utf-8'), compresslevel=3, mtime=0)))
        search_rows.append((new_rowid, search_body or ''))
        layout_rows.append((new_rowid, gzip.compress((layout_html or '').encode('utf-8'), compresslevel=3, mtime=0)))

    all_core_cols = ', '.join(core_names) + ', build_order'
    placeholders = ','.join('?' for _ in range(n_core + 2))
    conn.executemany(f'INSERT INTO entry_core (rowid, {all_core_cols}) VALUES ({placeholders})', core_rows)
    conn.executemany('INSERT INTO entry_fields VALUES (?, ?, ?)', field_rows)
    conn.executemany('INSERT INTO entry_meta VALUES (?, ?)', meta_rows)
    conn.executemany('INSERT INTO entry_body_text VALUES (?, ?)', body_rows)
    conn.executemany('INSERT INTO entry_search_body VALUES (?, ?)', search_rows)
    conn.executemany('INSERT INTO entry_layout VALUES (?, ?)', layout_rows)

    view_expr = {
        'fields_json': 'entry_fields.fields_json',
        'quickref_json': 'entry_fields.quickref_json',
        'provenance_json': 'entry_meta.provenance_json',
        'body_text': 'CAST(entry_body_text.body_gzip AS TEXT) AS body_text',
        'search_body': 'entry_search_body.search_body',
        'layout_html': 'CAST(entry_layout.layout_gzip AS TEXT) AS layout_html',
    }
    fields = ', '.join(view_expr.get(name, f'entry_core.{name}') for _, name, *_ in columns)
    conn.execute(f'''CREATE VIEW entries AS SELECT {fields}, entry_core.rowid AS rowid FROM entry_core
                    LEFT JOIN entry_fields ON entry_fields.entry_rowid=entry_core.rowid
                    LEFT JOIN entry_meta ON entry_meta.entry_rowid=entry_core.rowid
                    LEFT JOIN entry_body_text ON entry_body_text.entry_rowid=entry_core.rowid
                    LEFT JOIN entry_search_body ON entry_search_body.entry_rowid=entry_core.rowid
                    LEFT JOIN entry_layout ON entry_layout.entry_rowid=entry_core.rowid''')


def _write_our_rank_index(conn, rank_index_path):
    types_lut, it = _make_interner()
    sources_lut, isrc = _make_interner()
    codes_lut, icd = _make_interner()
    origin_of = {r[0]: (r[1] or '社区') for r in conn.execute('SELECT id, origin FROM sources')}
    bases = {
        r[0]: [r[1], r[2], r[3]]
        for r in conn.execute('SELECT source_id, target_id, base_source_id, base_name_en FROM pages_copied_bases ORDER BY source_id, target_id')
    }
    rows = []
    for eid, typ, name, name_zh, name_en, ruleset, sid, code, gid in conn.execute(
        'SELECT e.id, e.type, e.name, e.name_zh, e.name_en, e.ruleset, e.source_id, e.source_code, im.group_id '
        'FROM entry_core e LEFT JOIN identity_members im ON im.entry_id = e.id '
        'ORDER BY e.rowid'
    ):
        nz = None if name_zh == name else name_zh
        rows.append([
            eid,
            it(typ),
            name or '',
            nz,
            name_en or '',
            ruleset or '待核',
            isrc(sid),
            icd(code),
            gid,
        ])
    origins = {sid: origin_of.get(sid, '社区') for sid in sources_lut}
    payload = {
        'schema': 1,
        'types': types_lut,
        'sources': sources_lut,
        'codes': codes_lut,
        'origins': origins,
        'bases': bases,
        'rows': rows,
    }
    rank_index_path.write_bytes(gzip.compress(_json(payload), compresslevel=6, mtime=0))


def _project_our_db(original, target, *, rank_index_path=None):
    """Project `our5etools` catalog.sqlite into a clustered read-only Pages database."""
    with closing(sqlite3.connect(target, uri=True)) as conn, conn:
        schema = _db_schema(conn, original)
        definitions = {table: sql for kind, table, _, sql in schema if kind == 'table'}
        without_rowid_order = {
            'entry_facets': 'entry_id, facet_key, facet_value',
            'entry_sources': 'entry_id, source_id',
            'identity_members': 'entry_id',
        }
        for table in OUR5ETOOLS_TABLES:
            if table not in definitions:
                raise ValueError(f'Missing our5etools table {table}. Rebuild the data before exporting.')
            if table == 'entries':
                _project_our_entries(conn)
                continue
            if table in without_rowid_order:
                sql = definitions[table].rstrip().rstrip(';')
                if not sql.upper().endswith('WITHOUT ROWID'):
                    sql += ' WITHOUT ROWID'
                conn.execute(sql)
                conn.execute(f'INSERT INTO "{table}" SELECT * FROM original."{table}" ORDER BY {without_rowid_order[table]}')
                continue
            conn.execute(definitions[table])
            if table == 'relations':
                conn.execute('INSERT INTO relations SELECT * FROM original.relations ORDER BY source_id, type, target_id')
                continue
            where = (" WHERE key IN ('schema','reader_schema','snapshot','built_at','collection','summary')"
                     if table == 'metadata' else '')
            conn.execute(f'INSERT INTO "{table}" SELECT * FROM original."{table}"{where} ORDER BY rowid')
        conn.execute('CREATE UNIQUE INDEX idx_entry_core_id ON entry_core(id)')
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
        total = int(conn.execute('SELECT count(*) FROM entry_core').fetchone()[0])
        type_counts = dict(conn.execute('SELECT type, count(*) FROM entry_core GROUP BY type'))
        rs_counts = dict(conn.execute('SELECT ruleset, count(*) FROM entry_core GROUP BY ruleset'))
        src_counts = dict(conn.execute('SELECT source_id, count(*) FROM entry_core GROUP BY source_id'))
        pkg_counts = dict(conn.execute('SELECT package_id, count(*) FROM entry_core GROUP BY package_id'))
        or_counts = dict(conn.execute(
            "SELECT facet_value, count(*) FROM entry_facets WHERE facet_key='origin' GROUP BY facet_value"
        ))
        type_facets = {}
        for typ, fk, fv, cnt in conn.execute(
            'SELECT e.type, ef.facet_key, ef.facet_value, count(*) '
            'FROM entry_core e JOIN entry_facets ef ON ef.entry_id = e.id '
            'GROUP BY e.type, ef.facet_key, ef.facet_value'
        ):
            type_facets.setdefault(typ, {}).setdefault(fk, {})[fv] = int(cnt)
        type_ruleset_counts = [
            [r[0], r[1], r[2]]
            for r in conn.execute(
                "SELECT type, COALESCE(ruleset, ''), count(*) FROM entry_core "
                "GROUP BY type, COALESCE(ruleset, '') ORDER BY type, COALESCE(ruleset, '')"
            )
        ]
        conn.executemany('INSERT INTO metadata VALUES (?, ?)', [
            ('pages_source_counts', _json(src_counts).decode('utf-8')),
            ('pages_type_ruleset_counts', _json(type_ruleset_counts).decode('utf-8')),
            ('pages_type_facets', _json(type_facets).decode('utf-8')),
            ('pages_unfiltered_facets', _json({
                'total': total,
                'type': type_counts,
                'ruleset': rs_counts,
                'source': src_counts,
                'package': pkg_counts,
                'origin': or_counts,
            }).decode('utf-8')),
        ])
        if rank_index_path is not None:
            _write_our_rank_index(conn, rank_index_path)
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Invalid foreign keys in the projected our5etools catalog')
        conn.execute('ANALYZE main')
