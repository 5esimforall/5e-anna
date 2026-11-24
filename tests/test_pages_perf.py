"""Tests for Pages export performance optimizations: query plans, parity, and cache invalidation."""
from contextlib import closing
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import anna.paths as paths
from anna.presentation import catalog
from anna.presentation.collections import (
    BookCatalog, Collection, CollectionRegistry, DEFAULT, Restriction, _clear_book_connections,
)
from anna.presentation.layout import entry_layout, span_layout
from anna.presentation.library import Library, Scope, name_fold
from anna.presentation.our5etools import (
    Our5etoolsCatalog, Our5etoolsTour, build_subclass_records, load_our5etools_collection,
)
from anna.presentation.tour import PublishedTour, Tour, load as load_curation
from anna.publish.pages_assets import stage_browser_runtime
from anna.publish.pages_build import build as build_pages
from anna.publish.pages_data import (
    CHUNK_SIZE, _collect_our_entry_records, _project_db, _project_our_db, _write_home_cache,
    choose_entry_buckets, entry_bucket_index, export_pages_data,
)
from anna.server.pages import PublishedSite
from anna.server.query import QueryRequest
from anna.source.html import Document


OUR5ETOOLS_DATA = paths.PROJECT / 'data' / 'our5etools'
BOOK_DIR = paths.BOOK


def _make_5echm_fixture(root: Path):
    source = root / 'source'
    (source / 'topics').mkdir(parents=True)
    html_bytes = (
        b'<html><body>'
        b'<h1>\xe6\xb3\x95\xe6\x9c\xaf\xe5\x88\x97\xe8\xa1\xa8</h1>'
        b'<p>\xe6\x9c\xac\xe9\xa1\xb5\xe4\xbb\x8b\xe7\xbb\x8d\xe3\x80\x82</p>'
        b'<h3>\xe7\x81\xab\xe7\x90\x83\xe6\x9c\xaf Fireball</h3>'
        b'<p><em>\xe5\xa1\x91\xe8\x83\xbd \xe4\xb8\x89\xe7\x8e\xaf</em><br>'
        b'<b>\xe6\x96\xbd\xe6\xb3\x95\xe6\x97\xb6\xe9\x97\xb4\xef\xbc\x9a</b>\xe5\x8a\xa8\xe4\xbd\x9c<br>'
        b'<b>\xe6\x96\xbd\xe6\xb3\x95\xe8\xb7\x9d\xe7\xa6\xbb\xef\xbc\x9a</b>150\xe5\xb0\xba<br>'
        b'\xe8\xa7\x81<em>\xe9\xad\x94\xe6\xb3\x95\xe9\xa3\x9e\xe5\xbc\xb9</em>\xe3\x80\x82</p>'
        b'<h3>\xe9\xad\x94\xe6\xb3\x95\xe9\xa3\x9e\xe5\xbc\xb9 Magic Missile</h3>'
        b'<p>\xe5\xbf\x85\xe4\xb8\xad\xe3\x80\x82</p>'
        b'</body></html>'
    )
    page = source / 'topics' / 'spells.htm'
    page.write_bytes(html_bytes)
    doc = Document('topics/spells.htm', html_bytes.decode('utf-8'))
    text = doc.text
    doc.close()

    f_start = text.index('火球术 Fireball')
    f_title_end = text.index('塑能 三环')
    f_end = text.index('魔法飞弹 Magic Missile')
    m_start = f_end
    m_title_end = text.index('必中')
    m_end = len(text)
    ref_start = text.index('魔法飞弹', f_title_end, f_end)
    ref_end = ref_start + 4
    fact_start = text.index('施法时间：')
    fact_end = text.index('动作') + 2

    orig = root / 'orig.sqlite'
    with closing(sqlite3.connect(orig)) as c:
        c.executescript('''
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE sources (id TEXT PRIMARY KEY, name TEXT NOT NULL, origin TEXT, ruleset_hint TEXT,
                                  release_status TEXT, source_type TEXT, archived INTEGER, coverage TEXT, data TEXT NOT NULL);
            CREATE TABLE pages (path TEXT PRIMARY KEY, title TEXT, role TEXT, parse_status TEXT, sha256 TEXT,
                                text TEXT, source_ids TEXT, data TEXT NOT NULL);
            CREATE TABLE entities (id TEXT PRIMARY KEY, identity_status TEXT);
            CREATE TABLE entries (id TEXT PRIMARY KEY, entity_id TEXT REFERENCES entities(id), name TEXT,
                                  name_zh TEXT, name_en TEXT, type TEXT, page_path TEXT REFERENCES pages(path),
                                  start INTEGER, end INTEGER, ruleset TEXT, legacy TEXT,
                                  classification_status TEXT, fields TEXT, data TEXT NOT NULL);
            CREATE TABLE entry_sources (entry_id TEXT REFERENCES entries(id), source_id TEXT REFERENCES sources(id),
                                        PRIMARY KEY(entry_id, source_id));
            CREATE TABLE facts (id TEXT PRIMARY KEY, entry_id TEXT REFERENCES entries(id), field TEXT,
                                value TEXT, evidence TEXT, rule TEXT, scope TEXT, status TEXT);
            CREATE TABLE relations (id TEXT PRIMARY KEY, subject_id TEXT REFERENCES entries(id), predicate TEXT,
                                    object_id TEXT REFERENCES entries(id), target_label TEXT, target_locator TEXT,
                                    evidence TEXT, status TEXT);
            CREATE TABLE identity_candidates (group_id TEXT PRIMARY KEY, type TEXT, status TEXT, data TEXT);
            CREATE TABLE book_search (url TEXT PRIMARY KEY, src TEXT NOT NULL, title TEXT NOT NULL,
                                      cat TEXT NOT NULL, text TEXT NOT NULL);
            CREATE TABLE effective_references (id TEXT PRIMARY KEY, page_path TEXT REFERENCES pages(path),
                                               start INTEGER, end INTEGER, signal TEXT, status TEXT,
                                               target_id TEXT REFERENCES entries(id), review_status TEXT,
                                               can_link INTEGER NOT NULL DEFAULT 0, presentation TEXT,
                                               is_effective INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
            CREATE INDEX entry_source_idx ON entry_sources(source_id, entry_id);
            CREATE INDEX relation_subject_idx ON relations(subject_id, predicate);
            CREATE INDEX effective_reference_page_idx ON effective_references(page_path, start, end);
        ''')
        sha = sha256(html_bytes).hexdigest()
        c.execute("INSERT INTO metadata VALUES ('summary', '{\"total\":3}')")
        c.execute("INSERT INTO sources VALUES ('src1','PHB','官方','5r','','',0,'','{\"id\":\"src1\",\"name\":\"PHB\",\"label\":\"PHB\",\"aliases\":[],\"coverage\":\"官方\"}')")
        c.execute("INSERT INTO sources VALUES ('src2','Empty','官方','5e','','',0,'','{\"id\":\"src2\",\"name\":\"Empty\",\"label\":\"Empty\",\"aliases\":[],\"coverage\":\"官方\"}')")
        c.execute("INSERT INTO pages VALUES ('topics/spells.htm','Spells','rule','ok',?,?, '[\"src1\"]','{}')", (sha, text))
        c.execute("INSERT INTO entities VALUES ('ent1','ok')")
        c.execute("INSERT INTO entities VALUES ('ent2','ok')")
        d1 = json.dumps({
            'id': 'ver_1', 'name': '火球术 Fireball', 'name_zh': '火球术', 'name_en': 'Fireball',
            'type': 'spell', 'page_path': 'topics/spells.htm', 'start': f_start, 'end': f_end,
            'title_end': f_title_end, 'ruleset': '5r', 'legacy': 'unmarked',
            'classification_status': '原文明示', 'source_ids': ['src1'], 'source_names': ['PHB'],
            'fields': {'spell_level': 3, 'casting_time': '动作'}, 'field_conflicts': [],
        })
        d2 = json.dumps({
            'id': 'ver_2', 'name': '火球术 (旧版) Fireball', 'name_zh': '火球术', 'name_en': 'Fireball',
            'type': 'spell', 'page_path': 'topics/spells.htm', 'start': f_start, 'end': f_end,
            'title_end': f_title_end, 'ruleset': '5e', 'legacy': 'entry_marked',
            'classification_status': '原文明示', 'source_ids': ['src1'], 'source_names': ['PHB'],
            'fields': {'spell_level': 3}, 'field_conflicts': [],
        })
        dm = json.dumps({
            'id': 'ver_3', 'name': '魔法飞弹 Magic Missile', 'name_zh': '魔法飞弹', 'name_en': 'Magic Missile',
            'type': 'spell', 'page_path': 'topics/spells.htm', 'start': m_start, 'end': m_end,
            'title_end': m_title_end, 'ruleset': '5r', 'legacy': 'unmarked',
            'classification_status': '原文明示', 'source_ids': ['src1'], 'source_names': ['PHB'],
            'fields': {'spell_level': 1}, 'field_conflicts': [],
        })
        c.execute("INSERT INTO entries VALUES ('ver_1','ent1','火球术 Fireball','火球术','Fireball','spell','topics/spells.htm',?,?, '5r','unmarked','原文明示','{\"spell_level\":3,\"casting_time\":\"动作\"}',?)", (f_start, f_end, d1))
        c.execute("INSERT INTO entries VALUES ('ver_2','ent1','火球术 (旧版) Fireball','火球术','Fireball','spell','topics/spells.htm',?,?, '5e','entry_marked','原文明示','{\"spell_level\":3}',?)", (f_start, f_end, d2))
        c.execute("INSERT INTO entries VALUES ('ver_3','ent2','魔法飞弹 Magic Missile','魔法飞弹','Magic Missile','spell','topics/spells.htm',?,?, '5r','unmarked','原文明示','{\"spell_level\":1}',?)", (m_start, m_end, dm))
        c.execute("INSERT INTO entry_sources VALUES ('ver_1','src1')")
        c.execute("INSERT INTO entry_sources VALUES ('ver_2','src1')")
        c.execute("INSERT INTO entry_sources VALUES ('ver_3','src1')")
        c.execute("INSERT INTO facts VALUES ('f1','ver_1','casting_time','\"动作\"',?,'fixture','local','原文明示')",
                  (json.dumps({'path': 'topics/spells.htm', 'span': [fact_start, fact_end]}),))
        c.execute("INSERT INTO facts VALUES ('f2','ver_1','spell_level','3',?,'fixture','remote','上下文推得')",
                  (json.dumps({'path': 'topics/list.htm', 'span': [0, 2]}),))
        c.execute("INSERT INTO effective_references VALUES ('ref1','topics/spells.htm',?,?,'plain','resolved','ver_3','applied',1,'link',1,?)",
                  (ref_start, ref_end, json.dumps({'target_id': 'ver_3', 'start': ref_start, 'end': ref_end})))
        c.execute("INSERT INTO identity_candidates VALUES ('grp_1','spell','same_name','{\"entry_ids\":[\"ver_1\",\"ver_2\"]}')")
        c.commit()
    return source, orig


class Phase1QueryPlanAndParityTests(unittest.TestCase):
    def test_5echm_projected_query_plans_and_parity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            source, orig = _make_5echm_fixture(root)
            proj = root / 'proj.sqlite'
            _project_db(orig, proj, source)

            with closing(catalog.connect(orig)) as c_orig, closing(catalog.connect(proj)) as c_proj:
                self.assertEqual(catalog.works(c_proj), catalog.works(c_orig))
                self.assertEqual(catalog.meta(c_proj), catalog.meta(c_orig))
                self.assertEqual(catalog.facets(c_proj, {}), catalog.facets(c_orig, {}))
                self.assertEqual(catalog.detail(c_proj, 'ver_1'), catalog.detail(c_orig, 'ver_1'))
                self.assertTrue(catalog.has(c_proj, 'ver_1'))
                self.assertFalse(catalog.has(c_proj, 'ver_missing'))
                self.assertEqual(
                    {r['id'] for r in catalog.same_name(c_proj, 'spell', name_fold('Fireball'))},
                    {'ver_1', 'ver_2'},
                )
                for qparams in (
                    {},
                    {'type': ['spell']},
                    {'type': ['spell'], 'ruleset': ['5r']},
                    {'type': ['spell'], 'ruleset': ['5r', '5e']},
                    {'type': ['spell'], 'f_spell_level': ['3']},
                    {'not_type': ['spell']},
                ):
                    self.assertEqual(
                        catalog.count(c_proj, qparams),
                        catalog.query(c_proj, {**qparams, 'limit': ['1']})['versions_total'],
                        qparams,
                    )
                for rest in (
                    Restriction(rulesets=frozenset({'5r'})),
                    Restriction(sources=frozenset({'src1'})),
                    Restriction(sources=frozenset()),
                ):
                    self.assertEqual(
                        catalog.count(c_proj, {'type': ['spell']}, restrict=rest),
                        catalog.query(c_proj, {'type': ['spell'], 'limit': ['1']}, restrict=rest)['versions_total'],
                        rest,
                    )
                self.assertEqual(
                    catalog.query(c_proj, {'body': ['动作']}, export=True)['rows'],
                    catalog.query(c_orig, {'body': ['动作']}, export=True)['rows'],
                )
                # Verify works/meta count query avoids MATERIALIZE entries
                plans = [
                    r[3] for r in c_proj.execute(
                        "EXPLAIN QUERY PLAN SELECT es.source_id, count(*) FROM entry_sources es "
                        "JOIN entry_core e ON e.id=es.entry_id WHERE e.type!='section' GROUP BY es.source_id"
                    )
                ]
                self.assertFalse(any('MATERIALIZE' in p for p in plans), plans)
                # Verify version_compare hits identity_candidate_members + entry_core without MATERIALIZE or SCAN
                vc_join_plans = [
                    r[3] for r in c_proj.execute(
                        'EXPLAIN QUERY PLAN SELECT d.data FROM identity_candidate_members m '
                        'JOIN entry_core e ON e.id=m.entry_id LEFT JOIN entry_data d ON d.entry_rowid=e.rowid '
                        'WHERE m.group_id=? '
                        "ORDER BY CASE e.ruleset WHEN '5r' THEN 0 WHEN '5e' THEN 1 ELSE 2 END, e.page_path, e.start, e.id",
                        ('grp_1',),
                    )
                ]
                self.assertFalse(any('MATERIALIZE' in p or 'SCAN' in p for p in vc_join_plans), vc_join_plans)
                vc_plans = [
                    r[3] for r in c_proj.execute(
                        'EXPLAIN QUERY PLAN SELECT group_id, status FROM identity_candidate_members WHERE entry_id=?',
                        ('ver_1',),
                    )
                ]
                self.assertTrue(any('identity_candidate_members' in p for p in vc_plans), vc_plans)

            # Connection cache reuse and invalidation on signature change
            cat = BookCatalog(proj, source)
            try:
                self.assertTrue(cat.has('ver_1'))
                c1 = cat._read(lambda db: db)
                c2 = cat._read(lambda db: db)
                self.assertIs(c1, c2)
                # Rewrite proj copy to change mtime/inode
                proj2 = root / 'proj2.sqlite'
                shutil.copyfile(proj, proj2)
                proj2.replace(proj)
                c3 = cat._read(lambda db: db)
                self.assertIsNot(c1, c3)
            finally:
                _clear_book_connections()

    @unittest.skipUnless((OUR5ETOOLS_DATA / 'manifest.json').is_file(), 'Build our5etools collection first')
    def test_our5etools_projected_plans_relations_and_library_stacking(self):
        orig_cat = Our5etoolsCatalog(OUR5ETOOLS_DATA)
        try:
            with tempfile.TemporaryDirectory() as td:
                proj_dir = Path(td) / 'o5e'
                proj_dir.mkdir()
                for name in ('manifest.json', 'summary.json'):
                    shutil.copyfile(OUR5ETOOLS_DATA / name, proj_dir / name)
                (proj_dir / 'reader').mkdir()
                shutil.copyfile(OUR5ETOOLS_DATA / 'reader' / 'anna-reader.json', proj_dir / 'reader' / 'anna-reader.json')
                _project_our_db(OUR5ETOOLS_DATA / 'catalog.sqlite', proj_dir / 'catalog.sqlite')
                proj_cat = Our5etoolsCatalog(proj_dir)
                try:
                    with closing(sqlite3.connect((proj_dir / 'catalog.sqlite').as_uri() + '?mode=ro', uri=True)) as conn:
                        rel_eid = conn.execute('SELECT source_id FROM relations LIMIT 1').fetchone()[0]
                        copy_eid = conn.execute("SELECT source_id FROM relations WHERE type='copied_from' LIMIT 1").fetchone()[0]
                        fireball_eid = conn.execute("SELECT id FROM entry_core WHERE type='spell' AND name_en='Fireball' LIMIT 1").fetchone()[0]
                        # Verify EXPLAIN QUERY PLAN for relation targets never materializes entries view
                        plan = [r[3] for r in conn.execute(
                            'EXPLAIN QUERY PLAN SELECT id, name, type, source_code FROM entry_core WHERE id IN (?, ?)',
                            (rel_eid, copy_eid),
                        )]
                        self.assertFalse(any('MATERIALIZE' in p or 'SCAN' in p for p in plan), plan)

                    # Parity check for detail() with relations and copied_from
                    for eid in (rel_eid, copy_eid, fireball_eid):
                        d_orig = orig_cat.detail(eid)
                        d_proj = proj_cat.detail(eid)
                        self.assertEqual(d_proj['relations'], d_orig['relations'])
                        self.assertEqual(d_proj['versions'], d_orig['versions'])
                        self.assertEqual(d_proj['body'], d_orig['body'])
                        self.assertTrue(proj_cat.has(eid))
                    self.assertFalse(proj_cat.has('o5e_0000000000000000'))
                    self.assertEqual(orig_cat.works(), proj_cat.works())

                    # Verify Library._library_versions uses same_name() and NEVER calls ranked()
                    book = BookCatalog(catalog.DB, paths.WEB) if catalog.DB.is_file() else None
                    if book is not None:
                        from anna.presentation.alignment import Alignment
                        from anna.presentation.library import Publications
                        pubs = Publications(Alignment(), {})
                        reg = CollectionRegistry(
                            book,
                            (Collection('our5etools', 'our5etools', 'our5etools', provider=proj_cat),),
                            published_publications=pubs,
                        )
                        lib = Library(reg, Scope(('5echm', 'our5etools')))
                        with patch.object(proj_cat, 'ranked', side_effect=AssertionError('should not scan whole type')), \
                             patch.object(BookCatalog, 'ranked', side_effect=AssertionError('should not scan whole type')):
                            d = lib.detail(fireball_eid)
                        self.assertGreater(len(d['versions']['members']), 1)
                        # Verify identical result to fallback ranked() path
                        reg_orig = CollectionRegistry(
                            book,
                            (Collection('our5etools', 'our5etools', 'our5etools', provider=orig_cat),),
                            published_publications=pubs,
                        )
                        lib_orig = Library(reg_orig, Scope(('5echm', 'our5etools')))
                        # Verify parity against the unoptimized ranked() path and also exercise the unprojected same_name() cache
                        with patch.object(Our5etoolsCatalog, 'same_name', None), \
                             patch.object(BookCatalog, 'same_name', None):
                            d_ranked = lib_orig.detail(fireball_eid)
                        self.assertEqual(
                            [(m['id'], m.get('relation')) for m in d['versions']['members']],
                            [(m['id'], m.get('relation')) for m in d_ranked['versions']['members']],
                        )
                        with patch.object(orig_cat, 'ranked', side_effect=AssertionError('fallback same_name should not call ranked')), \
                             patch.object(BookCatalog, 'ranked', side_effect=AssertionError('fallback same_name should not call ranked')):
                            d_unproj = lib_orig.detail(fireball_eid)
                        self.assertEqual(
                            [(m['id'], m.get('relation')) for m in d_unproj['versions']['members']],
                            [(m['id'], m.get('relation')) for m in d['versions']['members']],
                        )
                        # Verify Our5etoolsCatalog connection cache reuses and invalidates on signature change
                        c1 = proj_cat._connect()
                        c2 = proj_cat._connect()
                        self.assertIs(c1, c2)
                        cat_copy = Path(td) / 'o5e' / 'copy.sqlite'
                        shutil.copyfile(proj_dir / 'catalog.sqlite', cat_copy)
                        cat_copy.replace(proj_dir / 'catalog.sqlite')
                        c3 = proj_cat._connect()
                        self.assertIsNot(c1, c3)
                finally:
                    proj_cat.close()
                    _clear_book_connections()
        finally:
            orig_cat.close()
            _clear_book_connections()


class Phase2PrerenderTests(unittest.TestCase):
    def test_prerendered_entry_and_tour_layout_parity_and_no_runtime_bs4(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            source, orig = _make_5echm_fixture(root)
            book = root / 'book'
            (book / 'topics').mkdir(parents=True)
            shutil.copyfile(source / 'topics' / 'spells.htm', book / 'topics' / 'spells.htm')
            db_sha = sha256(orig.read_bytes()).hexdigest()
            page_sha = sha256((book / 'topics' / 'spells.htm').read_bytes()).hexdigest()
            reader = {
                'schema': 6,
                'snapshot': 'snap-fixture',
                'nodes': [{'id': '1', 'parent_id': '', 'label': 'Spells', 'path': 'topics/spells.htm', 'fragment': '', 'status': 'present'}],
                'default_page': 'topics/spells.htm',
                'pages': {'topics/spells.htm': {'title': 'Spells'}},
                'presentation_resources': [],
                'source_release': None,
            }
            reader_bytes = json.dumps(reader).encode('utf-8')
            (book / 'anna-reader.json').write_bytes(reader_bytes)
            manifest = {
                'reader_schema': 6,
                'database_sha256': db_sha,
                'outputs': {
                    'anna-reader.json': sha256(reader_bytes).hexdigest(),
                    'topics/spells.htm': page_sha,
                },
                'entries': {
                    'ver_1': {'path': 'topics/spells.htm', 'fragment': '', 'locatable': True},
                    'ver_2': {'path': 'topics/spells.htm', 'fragment': '', 'locatable': True},
                    'ver_3': {'path': 'topics/spells.htm', 'fragment': '', 'locatable': True},
                },
            }
            (book / 'anna-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

            curation = root / 'tour.json'
            curation.write_text(json.dumps({
                'schema': 2,
                'sections': [{
                    'key': 'rules',
                    'label': {'zh': '规则', 'en': 'Rules'},
                    'topics': [{
                        'key': 'spells',
                        'label': {'zh': '法术', 'en': 'Spells'},
                        'contents': {'5echm': 'Spells'},
                    }],
                }],
                'choices': {'5echm': {}},
            }), encoding='utf-8')

            out = root / 'pages-out'
            export_pages_data(out, db=orig, book=book, source=source, curation=curation, gzip_shards=False)

            proj = out / 'pages-data' / 'data' / 'extracted' / 'catalog.sqlite' / '0.bin'
            # Assemble uncompressed projected sqlite from shards
            assembled = root / 'assembled.sqlite'
            manifest_idx = json.loads((out / 'pages-data' / 'index.json').read_text())
            info = manifest_idx['data/extracted/catalog.sqlite']
            n_chunks = (info['size'] + info['chunkSize'] - 1) // info['chunkSize']
            with assembled.open('wb') as dst:
                for i in range(n_chunks):
                    dst.write((out / 'pages-data' / 'data' / 'extracted' / 'catalog.sqlite' / f'{i}.bin').read_bytes())

            with closing(catalog.connect(orig)) as c_orig, closing(catalog.connect(assembled)) as c_proj:
                tables = {r[0] for r in c_proj.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertNotIn('facts', tables)
                self.assertNotIn('effective_references', tables)
                self.assertNotIn('pages_source', tables)
                self.assertIn('pages_entry_layout', tables)
                self.assertIn('pages_tour_layout', tables)

                for eid in ('ver_1', 'ver_2', 'ver_3'):
                    self.assertEqual(entry_layout(c_proj, eid, root / 'absent-source'), entry_layout(c_orig, eid, source))
                with self.assertRaises(LookupError):
                    entry_layout(c_proj, 'ver_missing', root / 'absent-source')

                # Tour span_layout parity for both page node and entry
                self.assertEqual(
                    span_layout(c_proj, 'topics/spells.htm', 0, 10, root / 'absent-source', owner=None),
                    {'status': 'empty', 'path': 'topics/spells.htm'},
                )
                # Entry ver_1 span matches Tour.entry_span
                ver1_row = c_orig.execute("SELECT start, end, json_extract(data, '$.title_end') FROM entries WHERE id='ver_1'").fetchone()
                self.assertEqual(
                    span_layout(c_proj, 'topics/spells.htm', ver1_row[2], ver1_row[1], root / 'absent-source', 'ver_1'),
                    span_layout(c_orig, 'topics/spells.htm', ver1_row[2], ver1_row[1], source, 'ver_1'),
                )

            # Verify PublishedSite handles entry, entry-layout, and tour-layout with bs4 & html5lib blocked
            site_root = root / 'site-data'
            for rel, meta in manifest_idx.items():
                target_file = site_root / rel
                target_file.parent.mkdir(parents=True, exist_ok=True)
                cnt = (meta['size'] + meta['chunkSize'] - 1) // meta['chunkSize']
                with target_file.open('wb') as dst:
                    for i in range(cnt):
                        dst.write((out / 'pages-data' / rel / f'{i}.bin').read_bytes())

            code = (
                "import sys, json; "
                "sys.modules['bs4'] = None; sys.modules['html5lib'] = None; "
                "from pathlib import Path; "
                "import anna.server.pages as p; "
                "site = p.PublishedSite(Path(" + repr(str(site_root)) + ")); "
                "s1, _, b1 = site.handle('/api/entry?id=ver_1'); assert s1 == 200, b1; "
                "s2, _, b2 = site.handle('/api/entry-layout?id=ver_1'); assert s2 == 200 and json.loads(b2)['status'] == 'ok', b2; "
                "s3, _, b3 = site.handle('/api/tour-layout?at=5echm:ver_1'); assert s3 == 200 and json.loads(b3)['status'] == 'ok', b3; "
                "s3b, _, b3b = site.handle('/api/tour-layout?at=5echm:n1'); assert s3b == 200 and json.loads(b3b)['status'] == 'ok', b3b; "
                "s4, _, _ = site.handle('/api/entry-layout?id=ver_missing'); assert s4 == 404; "
            )
            res = subprocess.run([sys.executable, '-c', code], cwd=paths.PROJECT, capture_output=True, text=True, timeout=20)
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

        # Verify worker and asset stager no longer require bs4 / html5lib
        worker_js = (paths.PROJECT / 'web' / 'pages-worker.js').read_text(encoding='utf-8')
        self.assertNotIn('loadPackage', worker_js)
        assets_py = (paths.PROJECT / 'anna' / 'publish' / 'pages_assets.py').read_text(encoding='utf-8')
        self.assertNotIn('html5lib', assets_py)
        self.assertNotIn('beautifulsoup4', assets_py)

    @unittest.skipUnless(
        catalog.DB.is_file() and (BOOK_DIR / 'anna-reader.json').is_file() and (paths.WEB / 'topics').is_dir(),
        'Requires built 5echm library and source mirror',
    )
    def test_real_5echm_prerender_parity_sample(self):
        from anna.presentation.layout import _span_ref_layers, render_entry_layout, render_span_layout, text_start
        from anna.presentation.tour import Tour, load as load_curation
        from anna.source.pages import decode_page

        reader_data = json.loads((BOOK_DIR / 'anna-reader.json').read_text(encoding='utf-8'))
        manifest = json.loads((BOOK_DIR / 'anna-manifest.json').read_text(encoding='utf-8'))
        locatable = {eid for eid, target in manifest['entries'].items() if target['locatable']}
        tour = Tour(catalog.DB, reader_data['nodes'], locatable, load_curation(), paths.WEB)

        with closing(catalog.connect(catalog.DB)) as db:
            # Sample entries of diverse types (spell, creature, class, feat, magic_item)
            sample_ids = []
            for typ in ('spell', 'creature', 'class', 'feat', 'magic_item'):
                sample_ids.extend(r[0] for r in db.execute('SELECT id FROM entries WHERE type=? LIMIT 3', (typ,)))
            for eid in sample_ids:
                e = json.loads(db.execute('SELECT data FROM entries WHERE id=?', (eid,)).fetchone()[0])
                path = e['page_path']
                raw = (paths.WEB / path).read_bytes()
                html_text, _, _ = decode_page(raw)
                doc = Document(path, html_text)
                try:
                    facts_rows = db.execute('SELECT field,value,evidence,rule,status FROM facts WHERE entry_id=? ORDER BY id', (eid,))
                    page_refs = db.execute(
                        'SELECT start, end, target_id FROM effective_references '
                        'WHERE page_path=? AND can_link=1 AND is_effective=1 AND target_id IS NOT NULL AND end > start ORDER BY start, id',
                        (path,),
                    ).fetchall()
                    ref_layers = _span_ref_layers(page_refs, text_start(e), e['end'], eid)
                    prerendered = render_entry_layout(doc, e, facts_rows, ref_layers)
                finally:
                    doc.close()
                self.assertEqual(prerendered, entry_layout(db, eid, paths.WEB))

            # Sample tour page nodes & entries
            excluded = {r[0] for r in db.execute("SELECT path FROM pages WHERE parse_status='excluded'")}
            checked = 0
            for node in reader_data['nodes']:
                if node['status'] != 'present' or node['path'] in excluded:
                    continue
                path, start, stop, owner = tour.page_span(node['path'])
                raw = (paths.WEB / path).read_bytes()
                html_text, _, _ = decode_page(raw)
                doc = Document(path, html_text)
                try:
                    page_refs = db.execute(
                        'SELECT start, end, target_id FROM effective_references '
                        'WHERE page_path=? AND can_link=1 AND is_effective=1 AND target_id IS NOT NULL AND end > start ORDER BY start, id',
                        (path,),
                    ).fetchall()
                    prerendered = render_span_layout(doc, path, start, stop, _span_ref_layers(page_refs, start, stop, owner))
                finally:
                    doc.close()
                self.assertEqual(prerendered, span_layout(db, path, start, stop, paths.WEB, owner))
                checked += 1
                if checked >= 8:
                    break


class Phase3ClusteringAndShardingTests(unittest.TestCase):
    def test_shard_and_worker_cache_constants(self):
        self.assertEqual(CHUNK_SIZE, 256 * 1024)
        worker_js = (paths.PROJECT / 'web' / 'pages-worker.js').read_text(encoding='utf-8')
        match = re.search(r'const\s+CACHE_ENTRIES\s*=\s*(\d+)\s*;', worker_js)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), 2048)

    def test_5echm_clustered_tables_fk_and_query_plans(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            source, orig = _make_5echm_fixture(root)
            proj = root / 'proj.sqlite'
            _project_db(orig, proj, source)

            with closing(catalog.connect(proj)) as c:
                self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
                indexes = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
                self.assertIn('pages_path_idx', indexes)
                self.assertIn('entry_core_id_idx', indexes)
                self.assertNotIn('relation_subject_idx', indexes)
                for tbl in ('entry_sources', 'relations'):
                    with self.assertRaises(sqlite3.OperationalError):
                        c.execute(f'SELECT rowid FROM {tbl}')

                plan_data = [r[3] for r in c.execute("EXPLAIN QUERY PLAN SELECT data FROM entries WHERE id=?", ('ver_1',))]
                joined = ' | '.join(plan_data)
                self.assertIn('COVERING INDEX entry_core_id_idx', joined)
                self.assertIn('INTEGER PRIMARY KEY', joined)

                plan_has = [r[3] for r in c.execute("EXPLAIN QUERY PLAN SELECT 1 FROM entries WHERE id=?", ('ver_1',))]
                self.assertFalse(any('entry_data' in step for step in plan_has), plan_has)

                plan_rel = [r[3] for r in c.execute(
                    'EXPLAIN QUERY PLAN SELECT predicate, object_id, target_label, status '
                    'FROM relations WHERE subject_id=? ORDER BY predicate, target_label',
                    ('ver_1',),
                )]
                joined_rel = ' | '.join(plan_rel)
                self.assertIn('PRIMARY KEY', joined_rel)
                self.assertFalse(any('TEMP B-TREE' in step for step in plan_rel), plan_rel)

                plan_layout = [r[3] for r in c.execute(
                    'EXPLAIN QUERY PLAN SELECT l.payload_gzip FROM entry_core e '
                    'LEFT JOIN pages_entry_layout l ON l.entry_rowid=e.rowid WHERE e.id=?',
                    ('ver_1',),
                )]
                joined_layout = ' | '.join(plan_layout)
                self.assertIn('COVERING INDEX entry_core_id_idx', joined_layout)
                self.assertIn('INTEGER PRIMARY KEY', joined_layout)

    @unittest.skipUnless((OUR5ETOOLS_DATA / 'manifest.json').is_file(), 'Build our5etools collection first')
    def test_our5etools_clustered_tables_plans_and_packed_index(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td).resolve() / 'o5e.sqlite'
            _project_our_db(OUR5ETOOLS_DATA / 'catalog.sqlite', proj)

            with closing(sqlite3.connect(proj.as_uri() + '?mode=ro', uri=True)) as c:
                self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
                for tbl in ('entry_facets', 'entry_sources', 'identity_members'):
                    with self.assertRaises(sqlite3.OperationalError):
                        c.execute(f'SELECT rowid FROM {tbl}')

                plan = [r[3] for r in c.execute(
                    'EXPLAIN QUERY PLAN SELECT layout_html, provenance_json FROM entries WHERE id=?',
                    ('o5e_fixture',),
                )]
                joined = ' | '.join(plan)
                self.assertIn('idx_entry_core_id', joined)
                self.assertIn('INTEGER PRIMARY KEY', joined)
                self.assertFalse(any('entry_body' in step for step in plan), plan)

                try:
                    stats = c.execute(
                        "SELECT min(pageno), max(pageno), count(*) FROM dbstat WHERE name='idx_entry_core_id'"
                    ).fetchone()
                except sqlite3.OperationalError:
                    stats = None
                if stats and stats[2]:
                    span_ratio = (stats[1] - stats[0] + 1) / stats[2]
                    self.assertLessEqual(span_ratio, 1.2, f'idx_entry_core_id span ratio {span_ratio:.2f}')


class Phase4LazyReaderAndRequestOrderTests(unittest.TestCase):
    def test_published_site_lazy_loads_5echm_reader_and_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            source, orig = _make_5echm_fixture(root)
            book = root / 'book'
            (book / 'topics').mkdir(parents=True)
            shutil.copyfile(source / 'topics' / 'spells.htm', book / 'topics' / 'spells.htm')
            db_sha = sha256(orig.read_bytes()).hexdigest()
            page_sha = sha256((book / 'topics' / 'spells.htm').read_bytes()).hexdigest()
            reader = {
                'schema': 6,
                'snapshot': 'snap-fixture',
                'nodes': [{'id': '1', 'parent_id': '', 'label': 'Spells', 'path': 'topics/spells.htm', 'fragment': '', 'status': 'present'}],
                'default_page': 'topics/spells.htm',
                'pages': {'topics/spells.htm': {'title': 'Spells'}},
                'presentation_resources': [],
                'source_release': None,
            }
            reader_bytes = json.dumps(reader).encode('utf-8')
            (book / 'anna-reader.json').write_bytes(reader_bytes)
            manifest = {
                'reader_schema': 6,
                'database_sha256': db_sha,
                'outputs': {
                    'anna-reader.json': sha256(reader_bytes).hexdigest(),
                    'topics/spells.htm': page_sha,
                },
                'entries': {
                    'ver_1': {'path': 'topics/spells.htm', 'fragment': '', 'locatable': True},
                    'ver_2': {'path': 'topics/spells.htm', 'fragment': '', 'locatable': True},
                    'ver_3': {'path': 'topics/spells.htm', 'fragment': '', 'locatable': True},
                },
            }
            (book / 'anna-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            curation = root / 'tour.json'
            curation.write_text(json.dumps({
                'schema': 2,
                'sections': [{
                    'key': 'rules',
                    'label': {'zh': '规则', 'en': 'Rules'},
                    'topics': [{'key': 'spells', 'label': {'zh': '法术', 'en': 'Spells'}, 'contents': {'5echm': 'Spells'}}],
                }],
                'choices': {'5echm': {}},
            }), encoding='utf-8')

            out = root / 'pages-out'
            export_pages_data(out, db=orig, book=book, source=source, curation=curation, gzip_shards=False)
            manifest_idx = json.loads((out / 'pages-data' / 'index.json').read_text(encoding='utf-8'))
            self.assertIn('data/5echm-anna/anna-meta.json', manifest_idx)
            self.assertIn('data/5echm-anna/tour-index.json.gz', manifest_idx)

            site_root = root / 'site-data'
            for rel, meta in manifest_idx.items():
                target_file = site_root / rel
                target_file.parent.mkdir(parents=True, exist_ok=True)
                cnt = (meta['size'] + meta['chunkSize'] - 1) // meta['chunkSize']
                with target_file.open('wb') as dst:
                    for i in range(cnt):
                        dst.write((out / 'pages-data' / rel / f'{i}.bin').read_bytes())

            opened = []
            orig_open = Path.open

            def spy_open(path_self, *args, **kwargs):
                opened.append(path_self.as_posix())
                return orig_open(path_self, *args, **kwargs)

            try:
                with patch.object(Path, 'open', spy_open):
                    site = PublishedSite(site_root)
                    for route in ('/api/meta', '/api/entries?type=spell', '/api/entry?id=ver_1',
                                  '/api/entry-layout?id=ver_1', '/api/library?works=1',
                                  '/api/reader', '/api/tour', '/api/tour?at=5echm:n1',
                                  '/api/tour?at=5echm:ver_1', '/api/tour-layout?at=5echm:ver_1',
                                  '/api/tour-layout?at=5echm:n1'):
                        status, _, _ = site.handle(route)
                        self.assertEqual(status, 200, route)
                    self.assertFalse(any(p.endswith('anna-reader.json') for p in opened), opened)
                    self.assertFalse(any(p.endswith('anna-manifest.json') for p in opened), opened)
                    self.assertTrue(any(p.endswith('tour-index.json.gz') for p in opened), opened)

                    # Reading an actual Stella page loads anna-reader.json lazily on demand
                    s_page, _, _ = site.handle('/api/reader-page?path=5echm:topics/spells.htm')
                    self.assertEqual(s_page, 200)
                    self.assertTrue(any(p.endswith('anna-reader.json') for p in opened), opened)

                # Fresh site: opening by entry in Stella loads anna-manifest.json lazily on demand
                opened.clear()
                _clear_book_connections()
                with patch.object(Path, 'open', spy_open):
                    site2 = PublishedSite(site_root)
                    self.assertEqual(site2.handle('/api/entry?id=ver_1')[0], 200)
                    self.assertEqual(site2.handle('/api/tour?at=5echm:n1')[0], 200)
                    self.assertFalse(any(p.endswith('anna-reader.json') or p.endswith('anna-manifest.json') for p in opened), opened)
                    s_epage, _, _ = site2.handle('/api/reader-page?entry=5echm:ver_1')
                    self.assertEqual(s_epage, 200)
                    self.assertTrue(any(p.endswith('anna-reader.json') for p in opened), opened)
                    self.assertTrue(any(p.endswith('anna-manifest.json') for p in opened), opened)
            finally:
                _clear_book_connections()

    @unittest.skipUnless(
        catalog.DB.is_file() and (BOOK_DIR / 'anna-reader.json').is_file() and (paths.WEB / 'topics').is_dir(),
        'Requires built 5echm library and source mirror',
    )
    def test_published_tour_parity_and_cross_library_isolation(self):
        reader_data = json.loads((BOOK_DIR / 'anna-reader.json').read_text(encoding='utf-8'))
        manifest = json.loads((BOOK_DIR / 'anna-manifest.json').read_text(encoding='utf-8'))
        curation_data = load_curation()
        locatable = {eid for eid, target in manifest['entries'].items() if target['locatable']}
        orig_tour = Tour(catalog.DB, reader_data['nodes'], locatable, curation_data, paths.WEB)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            site_root = root / 'site-data'
            db_dir = site_root / 'data' / 'extracted'
            book_dir = site_root / 'data' / '5echm-anna'
            tour_dir = site_root / 'tour'
            db_dir.mkdir(parents=True)
            book_dir.mkdir(parents=True)
            tour_dir.mkdir(parents=True)
            shutil.copyfile(paths.PROJECT / 'tour' / 'tour.json', tour_dir / 'tour.json')
            proj_db = db_dir / 'catalog.sqlite'
            tour_idx_file = book_dir / 'tour-index.json.gz'
            _project_db(
                catalog.DB, proj_db, paths.WEB,
                reader_nodes=reader_data['nodes'], locatable=locatable,
                curation=tour_dir / 'tour.json', tour_index_path=tour_idx_file,
            )
            (book_dir / 'anna-meta.json').write_text(json.dumps({
                'reader_schema': manifest['reader_schema'],
                'snapshot': reader_data['snapshot'],
                'default_page': reader_data.get('default_page', ''),
                'source_release': reader_data.get('source_release'),
            }), encoding='utf-8')
            (book_dir / 'anna-reader.json').symlink_to(BOOK_DIR / 'anna-reader.json')
            (book_dir / 'anna-manifest.json').symlink_to(BOOK_DIR / 'anna-manifest.json')

            tour_idx = json.loads(gzip.decompress(tour_idx_file.read_bytes()).decode('utf-8'))
            pub_tour = PublishedTour(proj_db, tour_idx, curation_data, paths.WEB)
            wizard_id = next(e.id for e in orig_tour.entries.values() if e.type == 'class' and e.ruleset == '5r' and e.name_en == 'Wizard')
            sub_id = 'ver_f7736c3a6aed1bf13627368f'

            for seg in ('n284', 'n291', sub_id, wizard_id):
                self.assertEqual(pub_tour.view(seg), orig_tour.view(seg), seg)
            for seg in ('n284', sub_id):
                self.assertEqual(pub_tour.layout(seg), orig_tour.layout(seg), seg)
            for nid in ('284', '291'):
                self.assertEqual(pub_tour.below(nid), orig_tour.below(nid), nid)
            self.assertEqual(pub_tour.resolve('本书速查 / 职业速查'), orig_tour.resolve('本书速查 / 职业速查'))
            self.assertEqual(pub_tour.subclass_records(), orig_tour.subclass_records())

            # Optional dual-collection isolation check when our5etools is present
            if (OUR5ETOOLS_DATA / 'manifest.json').is_file():
                o5e_dir = site_root / 'data' / 'our5etools'
                (o5e_dir / 'reader').mkdir(parents=True)
                for name in ('manifest.json', 'summary.json', 'cached-media.json'):
                    if (OUR5ETOOLS_DATA / name).is_file():
                        shutil.copyfile(OUR5ETOOLS_DATA / name, o5e_dir / name)
                (o5e_dir / 'reader' / 'anna-reader.json').symlink_to(OUR5ETOOLS_DATA / 'reader' / 'anna-reader.json')
                _project_our_db(OUR5ETOOLS_DATA / 'catalog.sqlite', o5e_dir / 'catalog.sqlite')
                o5e_reader = json.loads((OUR5ETOOLS_DATA / 'reader' / 'anna-reader.json').read_text(encoding='utf-8'))
                o5e_nav = {
                    k: o5e_reader.get(k)
                    for k in ('schema', 'snapshot', 'collection', 'roots', 'nodes', 'default_page',
                                'page_base', 'media_base', 'presentation_resources', 'source_release')
                }
                (o5e_dir / 'nav-nodes.json.gz').write_bytes(
                    gzip.compress(json.dumps(o5e_nav, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), mtime=0)
                )
                with closing(sqlite3.connect((o5e_dir / 'catalog.sqlite').as_uri() + '?mode=ro', uri=True)) as oconn:
                    oconn.row_factory = sqlite3.Row
                    o_types = sorted(r[0] for r in oconn.execute('SELECT DISTINCT type FROM entry_core') if r[0])
                    o_sources = {
                        r['id']: {
                            'id': r['id'], 'package_id': r['package_id'], 'origin': r['origin'],
                            'ruleset': r['ruleset'], 'short_name': r['short_name'], 'name': r['name'],
                            'data': json.loads(r['data_json'] or '{}'),
                        }
                        for r in oconn.execute('SELECT id, package_id, origin, ruleset, short_name, name, data_json FROM sources')
                    }
                    o_subs = build_subclass_records(oconn, o_sources, 'entry_core')
                (o5e_dir / 'tour-subclasses.json.gz').write_bytes(
                    gzip.compress(json.dumps(o_subs, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), mtime=0)
                )
                man = json.loads((o5e_dir / 'manifest.json').read_text(encoding='utf-8'))
                man['types'] = o_types
                (o5e_dir / 'manifest.json').write_text(json.dumps(man, ensure_ascii=False), encoding='utf-8')
                (site_root / 'data' / 'publications.json').write_text(json.dumps({
                    'schema': 1,
                    'snapshots': {'5echm': reader_data['snapshot'], 'our5etools': o5e_nav['snapshot']},
                    'members': {}, 'links': [], 'ranks': {},
                }), encoding='utf-8')

            opened = []
            orig_open = Path.open
            _clear_book_connections()
            try:
                with patch.object(Path, 'open', lambda s, *a, **kw: (opened.append(s.as_posix()), orig_open(s, *a, **kw))[1]):
                    site = PublishedSite(site_root)
                    for route in ('/api/tour', '/api/tour?at=classes',
                                  '/api/tour?at=5echm:n284', '/api/tour?at=5echm:n291',
                                  f'/api/tour?at=5echm:{sub_id}',
                                  f'/api/entry?id={sub_id}'):
                        self.assertEqual(site.handle(route)[0], 200, route)
                    self.assertFalse(any(p.endswith('anna-reader.json') or p.endswith('anna-manifest.json') for p in opened), opened)
                    self.assertFalse(any('our5etools/catalog.sqlite' in p for p in opened), opened)

                    if (OUR5ETOOLS_DATA / 'manifest.json').is_file():
                        for route in ('/api/reader?libraries=5echm,our5etools', '/api/tour?libraries=5echm,our5etools'):
                            self.assertEqual(site.handle(route)[0], 200, route)
                        self.assertFalse(any(p.endswith('anna-reader.json') for p in opened), opened)
            finally:
                _clear_book_connections()

    @unittest.skipUnless((OUR5ETOOLS_DATA / 'manifest.json').is_file(), 'Build our5etools collection first')
    def test_our5etools_lazy_reader_and_compact_reader_index(self):
        with tempfile.TemporaryDirectory() as td:
            o5e_dir = Path(td).resolve() / 'our5etools'
            (o5e_dir / 'reader').mkdir(parents=True)
            for name in ('manifest.json', 'summary.json', 'catalog.sqlite'):
                (o5e_dir / name).symlink_to(OUR5ETOOLS_DATA / name)
            (o5e_dir / 'reader' / 'anna-reader.json').symlink_to(OUR5ETOOLS_DATA / 'reader' / 'anna-reader.json')

            reader_raw = json.loads((OUR5ETOOLS_DATA / 'reader' / 'anna-reader.json').read_text(encoding='utf-8'))
            roots = {}
            for item in reader_raw.get('roots') or ():
                if isinstance(item, dict) and item.get('source_id') and item.get('id'):
                    roots.setdefault(item['source_id'], item['id'])
            with closing(sqlite3.connect((OUR5ETOOLS_DATA / 'catalog.sqlite').as_uri() + '?mode=ro', uri=True)) as conn:
                unlocated_eid = conn.execute(
                    "SELECT id FROM entries WHERE json_extract(provenance_json, '$.reader_location') IS NULL LIMIT 1"
                ).fetchone()[0]
                located_eid = conn.execute(
                    "SELECT id FROM entries WHERE json_extract(provenance_json, '$.reader_location') IS NOT NULL LIMIT 1"
                ).fetchone()[0]
            (o5e_dir / 'reader-index.json').write_text(
                json.dumps({'roots': roots, 'entry_locations': {}}, ensure_ascii=False, separators=(',', ':')),
                encoding='utf-8',
            )

            opened = []
            orig_open = Path.open

            def spy_open(path_self, *args, **kwargs):
                opened.append(path_self.as_posix())
                return orig_open(path_self, *args, **kwargs)

            collection = None
            try:
                with patch.object(Path, 'open', spy_open):
                    collection = load_our5etools_collection(o5e_dir, lazy_reader=True)
                    self.assertIsNotNone(collection)
                    works = collection.provider.works()
                    self.assertTrue(any(w.get('reader_root') for w in works))
                    d1 = collection.provider.detail(located_eid)
                    self.assertIsNotNone(d1['reader_location'])
                    d2 = collection.provider.detail(unlocated_eid)
                    self.assertIsNone(d2['reader_location'])
                    self.assertFalse(any(p.endswith('anna-reader.json') for p in opened), opened)
                    self.assertTrue(any(p.endswith('reader-index.json') for p in opened), opened)

                    # Opening Stella or Tour lazily reads anna-reader.json and works
                    nav = collection.reader.navigation()
                    self.assertTrue(nav['nodes'])
                    tour_view = collection.tour.view('n' + nav['roots'][0]['id'])
                    self.assertTrue(tour_view)
                    self.assertTrue(any(p.endswith('anna-reader.json') for p in opened), opened)
            finally:
                if collection is not None and collection.provider is not None:
                    collection.provider.close()

    @unittest.skipUnless((OUR5ETOOLS_DATA / 'manifest.json').is_file(), 'Build our5etools collection first')
    def test_our5etools_entry_fields_meta_plans_and_full_parity(self):
        orig_cat = Our5etoolsCatalog(OUR5ETOOLS_DATA)
        orig_tour = Our5etoolsTour(OUR5ETOOLS_DATA)
        try:
            with tempfile.TemporaryDirectory() as td:
                proj_dir = Path(td).resolve() / 'o5e'
                (proj_dir / 'reader').mkdir(parents=True)
                for name in ('manifest.json', 'summary.json'):
                    shutil.copyfile(OUR5ETOOLS_DATA / name, proj_dir / name)
                (proj_dir / 'reader' / 'anna-reader.json').symlink_to(OUR5ETOOLS_DATA / 'reader' / 'anna-reader.json')
                _project_our_db(OUR5ETOOLS_DATA / 'catalog.sqlite', proj_dir / 'catalog.sqlite')

                reader_raw = json.loads((OUR5ETOOLS_DATA / 'reader' / 'anna-reader.json').read_text(encoding='utf-8'))
                nav_payload = {
                    k: reader_raw.get(k)
                    for k in ('schema', 'snapshot', 'collection', 'roots', 'nodes', 'default_page',
                                'page_base', 'media_base', 'presentation_resources', 'source_release')
                }
                (proj_dir / 'nav-nodes.json.gz').write_bytes(
                    gzip.compress(json.dumps(nav_payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), mtime=0)
                )
                r_roots = {
                    item['source_id']: item['id']
                    for item in (reader_raw.get('roots') or ())
                    if isinstance(item, dict) and item.get('source_id') and item.get('id')
                }
                (proj_dir / 'reader-index.json').write_text(
                    json.dumps({'roots': r_roots, 'entry_locations': reader_raw.get('entry_locations') or {}}, ensure_ascii=False, separators=(',', ':')),
                    encoding='utf-8',
                )

                with closing(sqlite3.connect((proj_dir / 'catalog.sqlite').as_uri() + '?mode=ro', uri=True)) as conn:
                    conn.row_factory = sqlite3.Row
                    held_types = sorted(r[0] for r in conn.execute('SELECT DISTINCT type FROM entry_core') if r[0])
                    sources_by_id = {
                        r['id']: {
                            'id': r['id'], 'package_id': r['package_id'], 'origin': r['origin'],
                            'ruleset': r['ruleset'], 'short_name': r['short_name'], 'name': r['name'],
                            'data': json.loads(r['data_json'] or '{}'),
                        }
                        for r in conn.execute('SELECT id, package_id, origin, ruleset, short_name, name, data_json FROM sources')
                    }
                    sub_records = build_subclass_records(conn, sources_by_id, 'entry_core')
                    (proj_dir / 'tour-subclasses.json.gz').write_bytes(
                        gzip.compress(json.dumps(sub_records, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), mtime=0)
                    )
                    manifest = json.loads((proj_dir / 'manifest.json').read_text(encoding='utf-8'))
                    manifest['types'] = held_types
                    (proj_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

                    # Query plan checks: never materialize VIEW or join entry_body_text / entry_layout unnecessarily
                    for sql, bind in (
                        ("EXPLAIN QUERY PLAN SELECT ef.facet_value, count(*) FROM entry_facets ef JOIN entry_core e ON e.id=ef.entry_id WHERE ef.facet_key='origin' GROUP BY ef.facet_value", ()),
                        ("EXPLAIN QUERY PLAN SELECT efld.fields_json, efld.quickref_json FROM entry_core e LEFT JOIN entry_fields efld ON efld.entry_rowid=e.rowid WHERE e.id=?", ('o5e_fixture',)),
                        ("EXPLAIN QUERY PLAN SELECT el.layout_gzip, em.provenance_json FROM entry_core e LEFT JOIN entry_meta em ON em.entry_rowid=e.rowid LEFT JOIN entry_layout el ON el.entry_rowid=e.rowid WHERE e.id=?", ('o5e_fixture',)),
                        ("EXPLAIN QUERY PLAN SELECT r.source_id, e.id FROM relations r JOIN entry_core e ON e.id=r.source_id WHERE r.target_id=? AND r.type='belongs_to_class' AND e.type='subclass'", ('cls_1',)),
                    ):
                        plan = [r[3] for r in conn.execute(sql, bind)]
                        joined = ' | '.join(plan)
                        self.assertNotIn('MATERIALIZE', joined, plan)
                        self.assertNotIn('entry_body', joined, plan)
                        self.assertNotIn('SCAN entry_core', joined, plan)
                        if 'entry_fields' in sql:
                            self.assertNotIn('entry_meta', joined, plan)
                            self.assertNotIn('entry_layout', joined, plan)

                    spell_eid = conn.execute("SELECT id FROM entry_core WHERE type='spell' LIMIT 1").fetchone()[0]
                    sub_eid = conn.execute("SELECT id FROM entry_core WHERE type='subclass' LIMIT 1").fetchone()[0]
                    cls_eid = conn.execute("SELECT id FROM entry_core WHERE type='class' LIMIT 1").fetchone()[0]

                proj_cat = Our5etoolsCatalog(proj_dir)
                proj_tour = Our5etoolsTour(proj_dir)
                try:
                    self.assertEqual(proj_cat.known_types(), orig_cat.capabilities().types)
                    self.assertEqual(proj_cat.capabilities(), orig_cat.capabilities())
                    self.assertEqual(proj_cat.meta(), orig_cat.meta())
                    self.assertEqual(proj_cat.works(), orig_cat.works())

                    for qparams in (
                        {},
                        {'type': ['spell']},
                        {'origin': ['官方']},
                        {'origin': ['官方'], 'body': ['火球']},
                    ):
                        self.assertEqual(proj_cat.facets(qparams), orig_cat.facets(qparams), qparams)
                    self.assertEqual(
                        proj_cat.facets({'type': ['spell']}, restrict=Restriction(rulesets=frozenset({'5r'}))),
                        orig_cat.facets({'type': ['spell']}, restrict=Restriction(rulesets=frozenset({'5r'}))),
                    )
                    for qparams in (
                        {},
                        {'type': ['spell']},
                        {'type': ['spell'], 'ruleset': ['5r']},
                        {'type': ['spell'], 'f_spell_level': ['3']},
                        {'not_type': ['spell']},
                    ):
                        self.assertEqual(
                            proj_cat.count(qparams),
                            orig_cat.entries({**qparams, 'limit': ['1']})['versions_total'],
                            qparams,
                        )
                    for qparams in (
                        {'type': ['spell'], 'limit': ['10']},
                        {'type': ['spell'], 'versions': ['each'], 'limit': ['10']},
                        {'type': ['spell'], 'sort': ['spell_level'], 'limit': ['5']},
                    ):
                        self.assertEqual(proj_cat.entries(qparams), orig_cat.entries(qparams), qparams)
                    self.assertEqual(
                        sorted(proj_cat.ranked({'type': ['spell']}), key=lambda r: r['id']),
                        sorted(orig_cat.ranked({'type': ['spell']}), key=lambda r: r['id']),
                    )
                    self.assertEqual(
                        sorted(proj_cat.ranked({'type': ['spell']}, sort_field='spell_level'), key=lambda r: r['id']),
                        sorted(orig_cat.ranked({'type': ['spell']}, sort_field='spell_level'), key=lambda r: r['id']),
                    )
                    self.assertEqual(proj_cat.layout(spell_eid), orig_cat.layout(spell_eid))
                    self.assertEqual(proj_cat.layout(sub_eid), orig_cat.layout(sub_eid))

                    # Our5etoolsTour reads nav-nodes.json.gz + tour-subclasses.json.gz without anna-reader.json
                    expected_subs = orig_tour.subclass_records()
                    expected_sub_view = orig_tour.view(sub_eid)
                    expected_cls_view = orig_tour.view(cls_eid)
                    expected_sub_layout = orig_tour.layout(sub_eid)
                    opened = []
                    orig_open = Path.open
                    with patch.object(Path, 'open', lambda s, *a, **kw: (opened.append(s.as_posix()), orig_open(s, *a, **kw))[1]):
                        self.assertEqual(proj_tour.subclass_records(), expected_subs)
                        self.assertEqual(proj_tour.view(sub_eid), expected_sub_view)
                        self.assertEqual(proj_tour.view(cls_eid), expected_cls_view)
                        self.assertEqual(proj_tour.layout(sub_eid), expected_sub_layout)
                    self.assertFalse(any(p.endswith('anna-reader.json') for p in opened), opened)

                    # Library.count parity across single, dual, and restricted scopes
                    collections = (Collection('our5etools', 'our5etools', 'our5etools', provider=proj_cat),)
                    if catalog.DB.is_file() and paths.WEB.is_dir():
                        book = BookCatalog(catalog.DB, paths.WEB)
                        from anna.presentation.alignment import Alignment
                        from anna.presentation.library import Publications
                        reg_dual = CollectionRegistry(book, collections, published_publications=Publications(Alignment(), {}))
                        scopes = (Scope(('5echm',)), Scope(('our5etools',)), Scope(('5echm', 'our5etools')),
                                  Scope(('5echm', 'our5etools'), rulesets=frozenset({'5r'})))
                    else:
                        dummy_book = BookCatalog(proj_dir / 'catalog.sqlite', paths.PROJECT)
                        reg_dual = CollectionRegistry(dummy_book, collections)
                        scopes = (Scope(('our5etools',)), Scope(('our5etools',), rulesets=frozenset({'5r'})))
                    for scope in scopes:
                        lib = Library(reg_dual, scope)
                        for qparams in ({}, {'type': ['spell']}, {'type': ['spell'], 'ruleset': ['5r']}, {'type': ['psionic']}, {'type': ['spell'], 'f_spell_level': ['3']}):
                            self.assertEqual(
                                lib.count(qparams),
                                lib.entries({**qparams, 'limit': ['1']})['versions_total'],
                                (scope, qparams),
                            )
                finally:
                    proj_tour.catalog.close()
                    proj_cat.close()
                    _clear_book_connections()
        finally:
            orig_tour.catalog.close()
            orig_cat.close()
            _clear_book_connections()

    def test_frontend_request_order_and_idle_warmup_wiring(self):
        detail_js = (paths.PROJECT / 'web' / 'detail.js').read_text(encoding='utf-8')
        open_idx = detail_js.index('async function openEntry(')
        entry_idx = detail_js.index("apiRequest('/api/entry?id='", open_idx)
        layout_idx = detail_js.index('layoutOf(id', open_idx)
        self.assertLess(entry_idx, layout_idx)
        self.assertIn('X-Anna-Entry-Versions-Pending', detail_js)
        self.assertIn('/api/entry-versions?id=', detail_js)

        pages_js = (paths.PROJECT / 'web' / 'pages.js').read_text(encoding='utf-8')
        self.assertIn('export function warm()', pages_js)
        self.assertIn('pages-entries.js', pages_js)
        kit_js = (paths.PROJECT / 'web' / 'kit.js').read_text(encoding='utf-8')
        self.assertIn('request, warm', kit_js)
        worker_js = (paths.PROJECT / 'web' / 'pages-worker.js').read_text(encoding='utf-8')
        self.assertIn("data.type === 'warm'", worker_js)
        app_js = (paths.PROJECT / 'web' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('annaKit.warm()', app_js)


class Phase5StaticEntryBucketsAndVersionsTests(unittest.TestCase):
    def test_entry_versions_endpoint_and_lightweight_query_plans(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            source, orig = _make_5echm_fixture(root)
            proj = root / 'proj.sqlite'
            _project_db(orig, proj, source)

            book = BookCatalog(proj, source)
            additional = ()
            o5e_cat = None
            o5e_proj_dir = root / 'o5e'
            if (OUR5ETOOLS_DATA / 'manifest.json').is_file():
                o5e_proj_dir.mkdir()
                for name in ('manifest.json', 'summary.json'):
                    shutil.copyfile(OUR5ETOOLS_DATA / name, o5e_proj_dir / name)
                (o5e_proj_dir / 'reader').mkdir()
                shutil.copyfile(OUR5ETOOLS_DATA / 'reader' / 'anna-reader.json', o5e_proj_dir / 'reader' / 'anna-reader.json')
                _project_our_db(OUR5ETOOLS_DATA / 'catalog.sqlite', o5e_proj_dir / 'catalog.sqlite')
                o5e_cat = Our5etoolsCatalog(o5e_proj_dir)
                from anna.presentation.collections import Collection
                additional = (Collection('our5etools', 'our5etools', 'our5etools', provider=o5e_cat),)

            from anna.presentation.alignment import Alignment
            from anna.presentation.library import Publications
            reg = CollectionRegistry(book, additional, published_publications=Publications(Alignment(), {}))
            try:
                scopes = ['libraries=5echm', 'libraries=5echm&versions=each', 'libraries=5echm&prefer=5e']
                test_ids = ['ver_1', 'ver_3']
                if o5e_cat is not None:
                    with closing(sqlite3.connect((o5e_proj_dir / 'catalog.sqlite').as_uri() + '?mode=ro', uri=True)) as conn:
                        fireball_o5e = conn.execute(
                            "SELECT id FROM entry_core WHERE type='spell' AND name_en='Fireball' LIMIT 1"
                        ).fetchone()[0]
                    test_ids.append(fireball_o5e)
                    scopes.extend([
                        'libraries=our5etools',
                        'libraries=5echm,our5etools',
                        'libraries=5echm,our5etools&prefer=5e',
                        'libraries=5echm,our5etools&rulesets=5r',
                        'libraries=5echm,our5etools&versions=each',
                    ])

                for eid in test_ids:
                    for qs in scopes:
                        if eid.startswith('o5e_') and 'our5etools' not in qs:
                            continue
                        s1, _, b1 = QueryRequest(reg).handle_path(f'/api/entry?id={eid}&{qs}')
                        s2, _, b2 = QueryRequest(reg).handle_path(f'/api/entry-versions?id={eid}&{qs}')
                        self.assertEqual((s1, s2), (200, 200), (eid, qs))
                        d1, d2 = json.loads(b1), json.loads(b2)
                        self.assertEqual(d2['id'], d1['id'])
                        self.assertEqual(d2['collection'], d1['collection'])
                        self.assertEqual(d2['versions'], d1['versions'], (eid, qs))

                self.assertEqual(QueryRequest(reg).handle_path('/api/entry-versions?id=ver_missing')[0], 404)
                self.assertEqual(QueryRequest(reg).handle_path('/api/entry-versions?id=')[0], 404)
                self.assertEqual(QueryRequest(reg).handle_path('/api/entry-versions?id=ver_1&versions=bogus')[0], 400)

                # Verify catalog.versions() never touches pages, entry_data, relations, or pages_entry_layout
                traced_5e = []
                db5 = book._read(lambda db: db)
                db5.set_trace_callback(traced_5e.append)
                try:
                    self.assertEqual(book.versions('ver_1')['versions'], book.detail('ver_1')['versions'])
                finally:
                    db5.set_trace_callback(None)
                sql_head_5e = traced_5e[0]
                self.assertNotIn('pages', sql_head_5e)
                self.assertNotIn('entry_data', sql_head_5e)
                self.assertNotIn('relations', sql_head_5e)

                if o5e_cat is not None:
                    traced_o5e = []
                    dbo = o5e_cat._connect()
                    dbo.set_trace_callback(traced_o5e.append)
                    try:
                        v_o5e = o5e_cat.versions(fireball_o5e)
                    finally:
                        dbo.set_trace_callback(None)
                    self.assertEqual(v_o5e['versions'], o5e_cat.detail(fireball_o5e)['versions'])
                    joined_sql = '\n'.join(traced_o5e)
                    for forbidden in ('entry_body', 'entry_layout', 'relations', 'entry_sources'):
                        self.assertNotIn(forbidden, joined_sql, traced_o5e)
            finally:
                if o5e_cat is not None:
                    o5e_cat.close()
                _clear_book_connections()

    def test_static_entry_buckets_export_and_parity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            source, orig = _make_5echm_fixture(root)
            # Add a second Magic Missile entry in src2 outside identity_candidates so has_extra_versions=True is exercised
            with closing(sqlite3.connect(orig)) as c:
                row_mm = c.execute("SELECT start, end, data FROM entries WHERE id='ver_3'").fetchone()
                d4 = json.loads(row_mm[2])
                d4['id'] = 'ver_4'
                d4['source_ids'] = ['src2']
                d4['source_names'] = ['Empty']
                d4['ruleset'] = '5e'
                c.execute(
                    "INSERT INTO entries VALUES ('ver_4','ent2','魔法飞弹 Magic Missile','魔法飞弹','Magic Missile',"
                    "'spell','topics/spells.htm',?,?, '5e','unmarked','原文明示','{\"spell_level\":1}',?)",
                    (row_mm[0], row_mm[1], json.dumps(d4)),
                )
                c.execute("INSERT INTO entry_sources VALUES ('ver_4','src2')")
                c.commit()

            book = root / 'book'
            (book / 'topics').mkdir(parents=True)
            shutil.copyfile(source / 'topics' / 'spells.htm', book / 'topics' / 'spells.htm')
            db_sha = sha256(orig.read_bytes()).hexdigest()
            page_sha = sha256((book / 'topics' / 'spells.htm').read_bytes()).hexdigest()
            reader = {
                'schema': 6,
                'snapshot': 'snap-fixture',
                'nodes': [{'id': '1', 'parent_id': '', 'label': 'Spells', 'path': 'topics/spells.htm', 'fragment': '', 'status': 'present'}],
                'default_page': 'topics/spells.htm',
                'pages': {'topics/spells.htm': {'title': 'Spells'}},
                'presentation_resources': [],
                'source_release': None,
            }
            reader_bytes = json.dumps(reader).encode('utf-8')
            (book / 'anna-reader.json').write_bytes(reader_bytes)
            manifest = {
                'reader_schema': 6,
                'database_sha256': db_sha,
                'outputs': {'anna-reader.json': sha256(reader_bytes).hexdigest(), 'topics/spells.htm': page_sha},
                'entries': {eid: {'path': 'topics/spells.htm', 'fragment': '', 'locatable': True} for eid in ('ver_1', 'ver_2', 'ver_3', 'ver_4')},
            }
            (book / 'anna-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            curation = root / 'tour.json'
            curation.write_text(json.dumps({
                'schema': 2,
                'sections': [{'key': 'rules', 'label': {'zh': '规则', 'en': 'Rules'},
                              'topics': [{'key': 'spells', 'label': {'zh': '法术', 'en': 'Spells'}, 'contents': {'5echm': 'Spells'}}]}],
                'choices': {'5echm': {}},
            }), encoding='utf-8')

            for gzip_shards in (False, True):
                out = root / f'out-{gzip_shards}'
                export_pages_data(out, db=orig, book=book, source=source, curation=curation, gzip_shards=gzip_shards)
                entries_dir = out / 'pages-entries'
                idx = json.loads((entries_dir / 'index.json').read_text(encoding='utf-8'))
                self.assertEqual(idx['schema'], 1)
                self.assertEqual(idx['buckets'], choose_entry_buckets(4))
                self.assertEqual(idx['encoding'], 'gzip' if gzip_shards else 'identity')
                self.assertEqual(idx['collections'], [DEFAULT])

                # Reconstruct site-data for PublishedSite parity verification
                manifest_idx = json.loads((out / 'pages-data' / 'index.json').read_text(encoding='utf-8'))
                site_root = root / f'site-{gzip_shards}'
                for rel, meta in manifest_idx.items():
                    target_file = site_root / rel
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    cnt = (meta['size'] + meta['chunkSize'] - 1) // meta['chunkSize']
                    with target_file.open('wb') as dst:
                        for i in range(cnt):
                            raw_chunk = (out / 'pages-data' / rel / f'{i}.bin').read_bytes()
                            dst.write(gzip.decompress(raw_chunk) if meta.get('encoding') == 'gzip' else raw_chunk)

                bucket_file = entries_dir / f'{entry_bucket_index("ver_1", idx["buckets"]):03x}.bin'
                raw_bucket = bucket_file.read_bytes()
                bucket = json.loads((gzip.decompress(raw_bucket) if gzip_shards else raw_bucket).decode('utf-8'))
                try:
                    site = PublishedSite(site_root)
                    for eid in ('ver_1', 'ver_2', 'ver_3', 'ver_4'):
                        b_idx = entry_bucket_index(eid, idx['buckets'])
                        b_raw = (entries_dir / f'{b_idx:03x}.bin').read_bytes()
                        b_map = json.loads((gzip.decompress(b_raw) if gzip_shards else b_raw).decode('utf-8'))
                        self.assertIn(eid, b_map)
                        s_e, _, b_e = site.handle(f'/api/entry?id={eid}')
                        s_l, _, b_l = site.handle(f'/api/entry-layout?id={eid}')
                        self.assertEqual((s_e, s_l), (200, 200))
                        self.assertEqual(b_map[eid]['detail'], json.loads(b_e))
                        self.assertEqual(b_map[eid]['layout'], json.loads(b_l))
                    self.assertFalse(bucket['ver_1'].get('has_extra_versions', False))
                    self.assertFalse(bucket['ver_2'].get('has_extra_versions', False))
                    self.assertTrue(bucket['ver_3'].get('has_extra_versions'))
                    self.assertTrue(bucket['ver_4'].get('has_extra_versions'))
                finally:
                    _clear_book_connections()

            # Verify pages_build.build stamps data-anna-entries on all HTML documents
            built = root / 'built-pages'
            with patch('anna.publish.pages_build.stage_browser_runtime', lambda target: target.mkdir(parents=True, exist_ok=True)), \
                 patch('anna.publish.pages_build.export_pages_data', wraps=lambda *a, **kw: export_pages_data(*a, curation=curation, **kw)):
                build_pages(built, source, orig, book, '/5e-anna/')
            for rel_doc in ('index.html', 'read/index.html', 'tour/index.html', 'source/index.html'):
                html_doc = (built / rel_doc).read_text(encoding='utf-8')
                self.assertIn('data-anna-pages', html_doc)
                self.assertIn('data-anna-entries="1"', html_doc)

            # Verify real our5etools collection streaming parity when available
            if (OUR5ETOOLS_DATA / 'manifest.json').is_file():
                o5e_dir = root / 'o5e-real'
                o5e_dir.mkdir()
                for name in ('manifest.json', 'summary.json'):
                    shutil.copyfile(OUR5ETOOLS_DATA / name, o5e_dir / name)
                proj_o5e = o5e_dir / 'catalog.sqlite'
                _project_our_db(OUR5ETOOLS_DATA / 'catalog.sqlite', proj_o5e)
                extra_locs = {}
                (o5e_dir / 'reader-index.json').write_text(json.dumps({'roots': {}, 'entry_locations': extra_locs}))
                media_dir = o5e_dir / 'media'
                media_dir.mkdir()
                o5e_cat = Our5etoolsCatalog(o5e_dir)
                try:
                    with closing(sqlite3.connect(proj_o5e.as_uri() + '?mode=ro', uri=True)) as conn:
                        wanted = set()
                        for typ in ('spell', 'subclass', 'class', 'magic_item', 'creature'):
                            row = conn.execute('SELECT id FROM entry_core WHERE type=? LIMIT 1', (typ,)).fetchone()
                            if row:
                                wanted.add(row[0])
                        rel_row = conn.execute('SELECT source_id FROM relations LIMIT 1').fetchone()
                        if rel_row:
                            wanted.add(rel_row[0])
                        grp_row = conn.execute('SELECT entry_id FROM identity_members LIMIT 1').fetchone()
                        if grp_row:
                            wanted.add(grp_row[0])
                    checked = 0
                    for eid, detail_rec, layout_bytes in _collect_our_entry_records(proj_o5e, media_dir, extra_locs):
                        if eid in wanted:
                            expected_d = o5e_cat.detail(eid)
                            expected_d['collection'] = 'our5etools'
                            self.assertEqual(detail_rec, expected_d, eid)
                            self.assertEqual(json.loads(layout_bytes), o5e_cat.layout(eid), eid)
                            checked += 1
                    self.assertEqual(checked, len(wanted))
                finally:
                    o5e_cat.close()

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required to test web/pages-entries.js')
    def test_pages_entries_js_module_behavior(self):
        sample_ids = [
            'ver_1', 'ver_missing', 'ver_7e43c12bf8016dfa65efd6bd', 'o5e_ae4364085b943d7c',
            'entry-中文-火球术', 'spell_αβγ',
        ]
        expected_hashes = {
            bcount: {eid: entry_bucket_index(eid, bcount) for eid in sample_ids}
            for bcount in (1, 2048, 4096)
        }
        gz_payload = gzip.compress(json.dumps({
            'ver_extra': {
                'detail': {'id': 'ver_extra', 'name': 'Extra', 'collection': '5echm'},
                'layout': {'status': 'ok', 'html': '<p>Extra</p>'},
                'has_extra_versions': True,
            },
            'ver_plain': {
                'detail': {'id': 'ver_plain', 'name': 'Plain', 'collection': '5echm'},
                'layout': {'status': 'ok', 'html': '<p>Plain</p>'},
            },
        }, separators=(',', ':')).encode('utf-8'), mtime=0)
        id_payload = json.dumps({
            'o5e_one': {
                'detail': {'id': 'o5e_one', 'name': 'One', 'collection': 'our5etools'},
                'layout': {'status': 'ok', 'html': '<p>One</p>'},
                'has_extra_versions': True,
            },
        }).encode('utf-8')
        module_url = (paths.PROJECT / 'web' / 'pages-entries.js').resolve().as_uri()
        script = f"""
import assert from 'node:assert/strict';
import {{ entryBucket, request, resetCache }} from {json.dumps(module_url)};

const expectedHashes = {json.dumps(expected_hashes)};
for (const [bcount, mapping] of Object.entries(expectedHashes)) {{
  const n = Number(bcount);
  for (const [eid, expected] of Object.entries(mapping)) {{
    assert.equal(entryBucket(eid, n), expected, `hash mismatch for ${{eid}} @ ${{n}}`);
  }}
}}

let fetchCalls = [];
let attrValue = '2:gzip';
let mode = 'normal';
let failOnce = true;

globalThis.document = {{
  documentElement: {{
    getAttribute(name) {{
      return name === 'data-anna-entries' ? attrValue : null;
    }}
  }}
}};

const gzBytes = Uint8Array.from({json.dumps(list(gz_payload))});
const idBytes = Uint8Array.from({json.dumps(list(id_payload))});

globalThis.fetch = async (url) => {{
  const href = String(url);
  fetchCalls.push(href);
  if (mode === 'http500') {{
    return new Response('boom', {{status: 500}});
  }}
  if (mode === 'flaky' && failOnce) {{
    failOnce = false;
    throw new Error('network hiccup');
  }}
  if (href.endsWith('/pages-entries/index.json')) {{
    return Response.json({{schema: 1, buckets: 2, encoding: 'identity', collections: ['5echm', 'our5etools']}});
  }}
  if (attrValue === '2:gzip') {{
    return new Response(gzBytes, {{status: 200}});
  }}
  return new Response(idBytes, {{status: 200}});
}};

// 1. Concurrent /api/entry + /api/entry-layout hits fetch once (deduplicated & LRU cached)
resetCache();
fetchCalls = [];
const [rEntry, rLayout] = await Promise.all([
  request('/api/entry?id=ver_extra'),
  request('/api/entry-layout?id=ver_extra'),
]);
assert.equal(fetchCalls.length, 1);
assert.equal(rEntry.status, 200);
assert.equal(rLayout.status, 200);
assert.equal(rEntry.headers.get('X-Anna-Entry-Bundle'), '1');
assert.equal(rEntry.headers.get('X-Anna-Entry-Versions-Pending'), null);
assert.deepEqual(await rEntry.json(), {{id: 'ver_extra', name: 'Extra', collection: '5echm'}});
assert.deepEqual(await rLayout.json(), {{status: 'ok', html: '<p>Extra</p>'}});

// 2. X-Anna-Entry-Versions-Pending header matrix
const matrix = [
  ['/api/entry?id=ver_extra&libraries=5echm', null],
  ['/api/entry?id=ver_extra&libraries=5echm,our5etools&versions=each', null],
  ['/api/entry?id=ver_plain&libraries=5echm,our5etools', null],
  ['/api/entry?id=ver_extra&libraries=5echm,our5etools', '1'],
  ['/api/entry?id=ver_extra&libraries=our5etools', '1'],
];
for (const [path, expectedPending] of matrix) {{
  const resp = await request(path);
  assert.equal(resp.status, 200, path);
  assert.equal(resp.headers.get('X-Anna-Entry-Versions-Pending'), expectedPending, path);
}}
assert.equal(fetchCalls.length, 1);

// 3. Instant 404 for missing or empty ID without fallback
const rMissing = await request('/api/entry?id=ver_not_here');
assert.equal(rMissing.status, 404);
assert.equal(rMissing.headers.get('X-Anna-Entry-Bundle'), '1');
const rEmpty = await request('/api/entry?id=');
assert.equal(rEmpty.status, 404);

// 4. Fallback to index.json when data-anna-entries attribute is absent + identity encoding
resetCache();
attrValue = null;
fetchCalls = [];
const rId = await request('/api/entry?id=o5e_one&libraries=our5etools');
assert.equal(rId.status, 200);
assert.equal(rId.headers.get('X-Anna-Entry-Versions-Pending'), '1');
assert.deepEqual(await rId.json(), {{id: 'o5e_one', name: 'One', collection: 'our5etools'}});
assert.equal(fetchCalls.length, 2);

// 5. AbortSignal isolation on concurrent shared bucket fetch
resetCache();
attrValue = '2:gzip';
const ac = new AbortController();
const p1 = request('/api/entry?id=ver_extra', {{signal: ac.signal}});
const p2 = request('/api/entry-layout?id=ver_extra');
ac.abort(new DOMException('aborted', 'AbortError'));
await assert.rejects(p1, {{name: 'AbortError'}});
const r2 = await p2;
assert.equal(r2.status, 200);
assert.deepEqual(await r2.json(), {{status: 'ok', html: '<p>Extra</p>'}});

// 6. Worker fallback (null) on HTTP 500, network failure + retry recovery, and invalid scope
resetCache();
mode = 'http500';
assert.equal(await request('/api/entry?id=ver_extra'), null);

mode = 'flaky';
failOnce = true;
assert.equal(await request('/api/entry?id=ver_extra'), null);
const recovered = await request('/api/entry?id=ver_extra');
assert.equal(recovered.status, 200);

assert.equal(await request('/api/entry?id=ver_extra&versions=bogus'), null);
assert.equal(await request('/api/entry?id=ver_extra&libraries=bad!'), null);
"""
        proc = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)


class Phase6ListQueryAndMultiScopeHomeTests(unittest.TestCase):
    @staticmethod
    def _make_rich_5echm_fixture(root: Path):
        source, orig = _make_5echm_fixture(root)
        with closing(sqlite3.connect(orig)) as c:
            r1 = c.execute("SELECT start, end, data FROM entries WHERE id='ver_1'").fetchone()
            base_d = json.loads(r1[2])
            # Missing name_en spell (exercises missing-key sort ordering vs SQLite NULLS LAST)
            d_miss = {**base_d, 'id': 'ver_miss', 'name': '无英法术', 'name_zh': '无英法术', 'name_en': '',
                      'ruleset': '5e', 'fields': {'spell_level': 2}, 'field_conflicts': []}
            c.execute(
                "INSERT INTO entries VALUES ('ver_miss','ent_miss','无英法术','无英法术','','spell','topics/spells.htm',?,?, '5e','unmarked','原文明示','{\"spell_level\":2}',?)",
                (r1[0], r1[1], json.dumps(d_miss)),
            )
            c.execute("INSERT INTO entry_sources VALUES ('ver_miss','src2')")
            # Conflicted spell (exercises conflict=1 fallback to SQLite entries view)
            d_conf = {**base_d, 'id': 'ver_conf', 'name': '冲突法术 Conflict', 'name_zh': '冲突法术', 'name_en': 'Conflict',
                      'ruleset': '5r', 'fields': {'spell_level': 4}, 'field_conflicts': ['spell_level']}
            c.execute(
                "INSERT INTO entries VALUES ('ver_conf','ent_conf','冲突法术 Conflict','冲突法术','Conflict','spell','topics/spells.htm',?,?, '5r','unmarked','原文明示','{\"spell_level\":4}',?)",
                (r1[0], r1[1], json.dumps(d_conf)),
            )
            c.execute("INSERT INTO entry_sources VALUES ('ver_conf','src1')")
            # Magic item (exercises type ordering & multi-type facets)
            d_item = {**base_d, 'id': 'item_1', 'name': '魔杖 Wand', 'name_zh': '魔杖', 'name_en': 'Wand',
                      'type': 'magic_item', 'ruleset': '5r', 'fields': {'rarity': '非普通'}, 'field_conflicts': []}
            c.execute(
                "INSERT INTO entries VALUES ('item_1','ent_item','魔杖 Wand','魔杖','Wand','magic_item','topics/spells.htm',?,?, '5r','unmarked','原文明示','{\"rarity\":\"非普通\"}',?)",
                (r1[0], r1[1], json.dumps(d_item)),
            )
            c.execute("INSERT INTO entry_sources VALUES ('item_1','src1')")
            # Untyped section (exercises non_section_max_rowid boundary, sections=1, and unclassified_matches)
            d_sec = {**base_d, 'id': 'sec_1', 'name': '法术章节导言', 'name_zh': '法术章节导言', 'name_en': '',
                     'type': 'section', 'ruleset': '5r', 'classification_status': '未分类段落',
                     'fields': {'spell_level': 3}, 'field_conflicts': []}
            c.execute(
                "INSERT INTO entries VALUES ('sec_1','ent_sec','法术章节导言','法术章节导言','','section','topics/spells.htm',?,?, '5r','unmarked','未分类段落','{\"spell_level\":3}',?)",
                (0, 10, json.dumps(d_sec)),
            )
            c.execute("INSERT INTO entry_sources VALUES ('sec_1','src1')")
            c.execute("INSERT INTO entities VALUES ('ent_miss','ok'), ('ent_conf','ok'), ('ent_item','ok'), ('ent_sec','ok')")
            c.commit()
        return source, orig

    def test_5echm_rank_index_cache_and_exhaustive_query_parity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            source, orig = self._make_rich_5echm_fixture(root)
            proj = root / 'proj.sqlite'
            rank_path = root / 'rank-index.json.gz'
            _project_db(orig, proj, source, rank_index_path=rank_path)
            self.assertTrue(rank_path.is_file())

            with closing(catalog.connect(orig)) as c_orig, closing(catalog.connect(proj)) as c_proj:
                max_row = catalog._meta_int(c_proj, 'pages_non_section_max_rowid')
                self.assertIsNotNone(max_row)
                self.assertEqual(catalog._meta_int(c_proj, 'pages_single_source'), 1)
                self.assertEqual(
                    c_proj.execute('SELECT count(*) FROM entry_core WHERE rowid <= ? AND type=\'section\'', (max_row,)).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    c_proj.execute('SELECT count(*) FROM entry_core WHERE rowid > ? AND type!=\'section\'', (max_row,)).fetchone()[0],
                    0,
                )

                # Exhaustive query parity across sorts, directions, versions, search MATCH, export, restrictions
                query_cases = [
                    {},
                    {'type': ['spell']},
                    {'type': ['spell'], 'sort': ['spell_level']},
                    {'type': ['spell'], 'sort': ['spell_level'], 'dir': ['desc']},
                    {'type': ['spell'], 'q': ['Fireball']},
                    {'type': ['spell'], 'q': ['Fireball'], 'dir': ['desc']},
                    {'q': ['火球']},
                    {'q': ['火球'], 'dir': ['desc']},
                    {'typing': ['火']},
                    {'conflict': ['1']},
                    {'body': ['动作']},
                    {'sections': ['1']},
                    {'type': ['spell'], 'field': ['spell_level'], 'value': ['3'], 'q': ['法术章节导言']},
                    {'field': ['spell_level'], 'value': ['3'], 'q': ['不存在']},
                    {'prefer': ['5e']},
                ]
                for base_p in query_cases:
                    self.assertEqual(catalog.query(c_proj, base_p), catalog.query(c_orig, base_p), base_p)
                    each_p = {**base_p, 'versions': ['each']}
                    self.assertEqual(catalog.query(c_proj, each_p), catalog.query(c_orig, each_p), each_p)

                for sort_name in ('type', 'name', 'name_en', 'ruleset', 'source'):
                    for direction in ('asc', 'desc'):
                        for ver_mode in ('stacked', 'each'):
                            p = {'sort': [sort_name], 'dir': [direction], 'versions': [ver_mode]}
                            self.assertEqual(catalog.query(c_proj, p), catalog.query(c_orig, p), p)

                for export_p in (
                    {},
                    {'type': ['spell'], 'f_spell_level': ['3'], 'sort': ['spell_level']},
                    {'conflict': ['1']},
                    {'body': ['动作']},
                ):
                    self.assertEqual(catalog.query(c_proj, export_p, export=True), catalog.query(c_orig, export_p, export=True), export_p)

                for rest in (
                    None,
                    Restriction(rulesets=frozenset({'5r'})),
                    Restriction(sources=frozenset({'src1'})),
                    Restriction(sources=frozenset({'src2'})),
                    Restriction(sources=frozenset()),
                ):
                    for fp in ({}, {'type': ['spell']}, {'type': ['spell'], 'q': ['火']}, {'q': ['火']}):
                        self.assertEqual(catalog.query(c_proj, fp, restrict=rest), catalog.query(c_orig, fp, restrict=rest), (fp, rest))
                        self.assertEqual(catalog.facets(c_proj, fp, restrict=rest), catalog.facets(c_orig, fp, restrict=rest), (fp, rest))
                    self.assertEqual(
                        sorted(catalog.ranked(c_proj, {'type': ['spell']}, restrict=rest), key=lambda r: r['id']),
                        sorted(catalog.ranked(c_orig, {'type': ['spell']}, restrict=rest), key=lambda r: r['id']),
                        rest,
                    )
                    self.assertEqual(
                        sorted(catalog.ranked(c_proj, {'type': ['spell']}, restrict=rest, sort_field='spell_level'), key=lambda r: r['id']),
                        sorted(catalog.ranked(c_orig, {'type': ['spell']}, restrict=rest, sort_field='spell_level'), key=lambda r: r['id']),
                        rest,
                    )

                with self.assertRaises(ValueError):
                    catalog.ranked(c_proj, {'q': ['x' * 201]})

                # Trace checks: rank-index fast path avoids scanning entry_core; facets avoids entry_sources
                catalog.load_rank_index(c_proj)
                traced = []
                c_proj.set_trace_callback(traced.append)
                try:
                    _ = catalog.ranked(c_proj, {'type': ['spell']}, restrict=Restriction(sources=frozenset({'src1'})))
                    self.assertFalse(any('FROM entry_core' in s or 'FROM entries' in s for s in traced), traced)

                    traced.clear()
                    _ = catalog.query(c_proj, {'offset': ['0'], 'limit': ['2']})
                    self.assertFalse(
                        any(('FROM entry_core' in s or 'FROM entries' in s) and 'WHERE e.id IN' not in s for s in traced),
                        traced,
                    )

                    traced.clear()
                    _ = catalog.facets(c_proj, {'type': ['spell']})
                    facets_sql = '\n'.join(traced)
                    self.assertNotIn('entry_sources', facets_sql, traced)
                    self.assertIn('e.primary_source_id', facets_sql)
                    self.assertIn(f'e.rowid<={max_row}', facets_sql)

                    # Stacked ranking LRU cache avoids re-running CTE across offset pages
                    catalog._STACKED_RANKINGS.clear()
                    traced.clear()
                    _ = catalog.query(c_proj, {'type': ['spell'], 'q': ['火'], 'limit': ['1']})
                    self.assertTrue(any('WITH matched AS' in s for s in traced), traced)
                    traced.clear()
                    _ = catalog.query(c_proj, {'type': ['spell'], 'q': ['火'], 'offset': ['1'], 'limit': ['1']})
                    self.assertFalse(any('WITH matched AS' in s for s in traced), traced)
                finally:
                    c_proj.set_trace_callback(None)

                # Rewriting proj invalidates _STACKED_RANKINGS cleanly
                proj2 = root / 'proj2.sqlite'
                shutil.copyfile(proj, proj2)
                proj2.replace(proj)
                with closing(catalog.connect(proj)) as c_proj_new:
                    traced2 = []
                    c_proj_new.set_trace_callback(traced2.append)
                    try:
                        res_rebuilt = catalog.query(c_proj_new, {'type': ['spell'], 'q': ['火'], 'offset': ['1'], 'limit': ['1']})
                    finally:
                        c_proj_new.set_trace_callback(None)
                    self.assertTrue(any('WITH matched AS' in s for s in traced2), traced2)
                    self.assertEqual(res_rebuilt, catalog.query(c_orig, {'type': ['spell'], 'q': ['火'], 'offset': ['1'], 'limit': ['1']}))

    @unittest.skipUnless((OUR5ETOOLS_DATA / 'manifest.json').is_file(), 'Build our5etools collection first')
    def test_our5etools_rank_index_and_type_facets_optimization(self):
        orig_cat = Our5etoolsCatalog(OUR5ETOOLS_DATA)
        try:
            with tempfile.TemporaryDirectory() as td:
                proj_dir = Path(td).resolve() / 'o5e'
                (proj_dir / 'reader').mkdir(parents=True)
                for name in ('manifest.json', 'summary.json'):
                    shutil.copyfile(OUR5ETOOLS_DATA / name, proj_dir / name)
                shutil.copyfile(OUR5ETOOLS_DATA / 'reader' / 'anna-reader.json', proj_dir / 'reader' / 'anna-reader.json')
                _project_our_db(
                    OUR5ETOOLS_DATA / 'catalog.sqlite',
                    proj_dir / 'catalog.sqlite',
                    rank_index_path=proj_dir / 'rank-index.json.gz',
                )
                proj_cat = Our5etoolsCatalog(proj_dir)
                try:
                    with closing(sqlite3.connect((proj_dir / 'catalog.sqlite').as_uri() + '?mode=ro', uri=True)) as c:
                        spell_id = c.execute("SELECT id FROM entry_core WHERE type='spell' LIMIT 1").fetchone()[0]
                        src_id = c.execute("SELECT source_id FROM entry_core WHERE type='spell' LIMIT 1").fetchone()[0]
                        fireball_id = c.execute("SELECT id FROM entry_core WHERE type='spell' AND name_en='Fireball' LIMIT 1").fetchone()[0]

                    for qp, rest in (
                        ({}, None),
                        ({'type': ['spell']}, None),
                        ({'q': ['Fireball']}, None),
                        ({'ruleset': ['5r']}, None),
                        ({'type': ['spell']}, Restriction(rulesets=frozenset({'5r'}))),
                        ({'type': ['spell']}, Restriction(sources=frozenset({src_id}))),
                        ({'type': ['spell']}, Restriction(sources=frozenset())),
                    ):
                        self.assertEqual(
                            sorted(proj_cat.ranked(qp, restrict=rest), key=lambda r: r['id']),
                            sorted(orig_cat.ranked(qp, restrict=rest), key=lambda r: r['id']),
                            (qp, rest),
                        )
                    for bad_qp in ({'type': ['invalid']}, {'ruleset': ['bogus']}):
                        with self.assertRaises(ValueError):
                            proj_cat.ranked(bad_qp)

                    # List rows never carry provenance on either original or projected
                    e_proj = proj_cat.entries({'type': ['spell'], 'q': ['Fireball'], 'limit': ['5']})
                    e_orig = orig_cat.entries({'type': ['spell'], 'q': ['Fireball'], 'limit': ['5']})
                    self.assertEqual(e_proj, e_orig)
                    self.assertTrue(e_proj['rows'])
                    self.assertNotIn('provenance', e_proj['rows'][0])

                    # Trace queries to verify entry_meta and entry_facets skipping
                    proj_cat.meta()
                    proj_cat.facets({'type': ['spell']})
                    traced = []
                    db_p = proj_cat._connect()
                    db_p.set_trace_callback(traced.append)
                    try:
                        _ = proj_cat.rows([spell_id])
                        _ = proj_cat.versions(fireball_id)
                        _ = proj_cat.facets({'type': ['spell']})
                        _ = proj_cat.ranked({'type': ['spell'], 'q': ['Fireball']}, restrict=Restriction(sources=frozenset({src_id})))
                    finally:
                        db_p.set_trace_callback(None)
                    sql_log = '\n'.join(traced)
                    self.assertNotIn('entry_meta', sql_log, traced)
                    self.assertNotIn('entry_facets', sql_log, traced)
                finally:
                    proj_cat.close()
                    _clear_book_connections()
        finally:
            orig_cat.close()
            _clear_book_connections()

    def test_pages_build_multi_scope_home_and_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            source, orig = _make_5echm_fixture(root)
            book = root / 'book'
            (book / 'topics').mkdir(parents=True)
            shutil.copyfile(source / 'topics' / 'spells.htm', book / 'topics' / 'spells.htm')
            db_sha = sha256(orig.read_bytes()).hexdigest()
            page_sha = sha256((book / 'topics' / 'spells.htm').read_bytes()).hexdigest()
            reader = {
                'schema': 6,
                'snapshot': 'snap-fixture',
                'nodes': [{'id': '1', 'parent_id': '', 'label': 'Spells', 'path': 'topics/spells.htm', 'fragment': '', 'status': 'present'}],
                'default_page': 'topics/spells.htm',
                'pages': {'topics/spells.htm': {'title': 'Spells'}},
                'presentation_resources': [],
                'source_release': None,
            }
            reader_bytes = json.dumps(reader).encode('utf-8')
            (book / 'anna-reader.json').write_bytes(reader_bytes)
            manifest = {
                'reader_schema': 6,
                'database_sha256': db_sha,
                'outputs': {'anna-reader.json': sha256(reader_bytes).hexdigest(), 'topics/spells.htm': page_sha},
                'entries': {eid: {'path': 'topics/spells.htm', 'fragment': '', 'locatable': True} for eid in ('ver_1', 'ver_2', 'ver_3')},
            }
            (book / 'anna-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            curation = root / 'tour.json'
            curation.write_text(json.dumps({
                'schema': 2,
                'sections': [{'key': 'rules', 'label': {'zh': '规则', 'en': 'Rules'},
                              'topics': [{'key': 'spells', 'label': {'zh': '法术', 'en': 'Spells'}, 'contents': {'5echm': 'Spells'}}]}],
                'choices': {'5echm': {}},
            }), encoding='utf-8')

            # 1. _write_home_cache generates scoped home.*.json + home-index.json when two libraries are ready
            fake_stage = root / 'fake-stage'
            fake_work = root / 'fake-work'
            fake_stage.mkdir()
            fake_work.mkdir()

            class FakeSite:
                def __init__(self, _root):
                    self.registry = type('Reg', (), {'ready': staticmethod(lambda: ('5echm', 'our5etools'))})()

                @staticmethod
                def handle(route):
                    return 200, {}, json.dumps({'route': route}).encode('utf-8')

            with patch('anna.publish.pages_data.PublishedSite', FakeSite):
                _write_home_cache(fake_stage, fake_work, {})
            home_idx = json.loads((fake_stage / 'home-index.json').read_text(encoding='utf-8'))
            self.assertEqual(set(home_idx['scopes']), {'5echm,our5etools', 'our5etools', 'our5etools,5echm'})
            self.assertTrue((fake_stage / home_idx['default']).is_file())
            for fname in home_idx['scopes'].values():
                self.assertTrue((fake_stage / fname).is_file())

            # 2. pages_build.build(home_cache=True) stamps data-anna-home + data-anna-home-scopes and removes home-index.json
            def fake_export(stage, *args, **kwargs):
                written = export_pages_data(stage, *args, curation=curation, **{k: v for k, v in kwargs.items() if k != 'home_cache'}, home_cache=True)
                scoped_payload = b'{"ok":true}'
                scoped_name = f'home.{sha256(scoped_payload).hexdigest()[:16]}.json'
                (stage / scoped_name).write_bytes(scoped_payload)
                idx_file = stage / 'home-index.json'
                idx_data = json.loads(idx_file.read_text(encoding='utf-8'))
                idx_data['scopes'] = {'5echm,our5etools': scoped_name, 'our5etools': scoped_name}
                old_size = idx_file.stat().st_size
                idx_file.write_bytes(json.dumps(idx_data).encode('utf-8'))
                return written + len(scoped_payload) + (idx_file.stat().st_size - old_size)

            built = root / 'built-pages'
            with patch('anna.publish.pages_build.stage_browser_runtime', lambda target: target.mkdir(parents=True, exist_ok=True)), \
                 patch('anna.publish.pages_build.export_pages_data', fake_export):
                build_pages(built, source, orig, book, '/5e-anna/', home_cache=True)
            self.assertFalse((built / 'home-index.json').exists())
            index_html = (built / 'index.html').read_text(encoding='utf-8')
            self.assertIn('data-anna-home="home.', index_html)
            self.assertIn('data-anna-home-scopes="5echm,our5etools=home.', index_html)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required to test web/home.js and web/pages.js')
    def test_home_js_and_pages_js_node_unit(self):
        home_url = (paths.PROJECT / 'web' / 'home.js').resolve().as_uri()
        pages_url = (paths.PROJECT / 'web' / 'pages.js').resolve().as_uri()
        script = f"""
import assert from 'node:assert/strict';
import {{ request as homeRequest }} from {json.dumps(home_url)};
import {{ request as pagesRequest, warm as pagesWarm, resetState }} from {json.dumps(pages_url)};

// --- Part 1: web/home.js multi-scope path canonicalization, validation & abort ---
const scopeFile = 'home.0123456789abcdef.json';
const validPayload = {{
  '/api/library': {{collections: [{{id: '5echm'}}, {{id: 'our5etools'}}]}},
  '/api/meta?libraries=5echm,our5etools': {{fields: {{}}, types: {{}}, collections: []}},
  '/api/facets?libraries=5echm,our5etools': {{schema: {{columns: [], flags: [], sorts: []}}, groups: [{{options: []}}], scope_total: 10}},
  '/api/entries?offset=0&limit=150&libraries=5echm,our5etools': {{rows: [], total: 10}},
}};
let homeFetchCalls = 0;
let releaseDelayed;
const delayedGate = new Promise(resolve => {{ releaseDelayed = resolve; }});
globalThis.fetch = async (url) => {{
  homeFetchCalls++;
  if (String(url).includes('deadbeef')) {{
    await delayedGate;
    return Response.json(validPayload);
  }}
  return Response.json(validPayload);
}};

// Encoded comma %2C and reversed query param order hit canonical cached route once
const h1 = await homeRequest('/api/library', scopeFile);
const h2 = await homeRequest('/api/meta?libraries=5echm%2Cour5etools', scopeFile);
const h3 = await homeRequest('/api/facets?libraries=5echm,our5etools', scopeFile);
const h4 = await homeRequest('/api/entries?libraries=5echm%2Cour5etools&limit=150&offset=0', scopeFile);
assert.equal(homeFetchCalls, 1);
for (const r of [h1, h2, h3, h4]) {{
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('X-Anna-Home-Cache'), '1');
}}

// Reject invalid routes, extra/duplicate params, bad filename, bad libraries
for (const badPath of [
  '/api/library?libraries=5echm',
  '/api/meta?libraries=5echm,our5etools&libraries=5echm',
  '/api/meta?libraries=5echm&extra=1',
  '/api/meta?libraries=bad!',
  '/api/entries?offset=150&limit=150&libraries=5echm,our5etools',
  '/api/entries?offset=0&limit=50&libraries=5echm,our5etools',
  '/api/entries?offset=0&limit=150&libraries=5echm,our5etools&type=spell',
  'https://evil.invalid/api/meta',
]) {{
  assert.equal(await homeRequest(badPath, scopeFile), undefined, badPath);
}}
assert.equal(await homeRequest('/api/library', 'not-a-home.json'), undefined);

// AbortSignal rejects waiting caller cleanly without breaking a second caller on the same delayed fetch
const acHome = new AbortController();
const delayedFile = 'home.deadbeefdeadbeef.json';
const pAbort = homeRequest('/api/library', delayedFile, acHome.signal);
const pKeep = homeRequest('/api/library', delayedFile);
acHome.abort(new DOMException('cancel home', 'AbortError'));
await assert.rejects(pAbort, {{name: 'AbortError'}});
releaseDelayed();
const kept = await pKeep;
assert.equal(kept.status, 200);

// --- Part 2: web/pages.js priority queue, abort cancellation, deferred warm, error cleanup ---
const workers = [];
class MockWorker {{
  constructor() {{
    this.listeners = new Map();
    this.posted = [];
    this.terminated = false;
    workers.push(this);
  }}
  addEventListener(type, fn) {{
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn);
  }}
  postMessage(msg) {{
    this.posted.push(msg);
  }}
  terminate() {{
    this.terminated = true;
  }}
  emit(type, payload) {{
    for (const fn of this.listeners.get(type) || []) fn(payload);
  }}
}}
globalThis.Worker = MockWorker;
resetState();
const flush = () => new Promise(r => setTimeout(r, 0));

// 1. Priority ordering: active blocks queue, then P0 (entry-versions) preempts P1 (entries) preempts P2 (facets)
const pFirst = pagesRequest('/api/entries?offset=0&limit=20');
await flush();
const w = workers[0];
assert.equal(w.posted.length, 2); // init + first request
assert.equal(w.posted[1].path, '/api/entries?offset=0&limit=20');

const pFacets = pagesRequest('/api/facets?type=spell');
const pEntriesQueued = pagesRequest('/api/entries?offset=20&limit=20');
const pVersions = pagesRequest('/api/entry-versions?id=ver_1');
const acQueued = new AbortController();
const pCancelledBeforeSend = pagesRequest('/api/facets?type=feat', {{signal: acQueued.signal}});
await flush();
pagesWarm(); // Must defer while queue & active are non-empty
assert.equal(w.posted.length, 2); // Still waiting on active pFirst!

// Abort queued job before dispatch
acQueued.abort(new DOMException('stale facet', 'AbortError'));
await assert.rejects(pCancelledBeforeSend, {{name: 'AbortError'}});

const completeActive = (bodyObj) => {{
  const reqMsg = w.posted[w.posted.length - 1];
  const bytes = new TextEncoder().encode(JSON.stringify(bodyObj));
  w.emit('message', {{data: {{id: reqMsg.id, status: 200, headers: {{'Content-Type': 'application/json'}}, body: bytes}}}});
  return reqMsg;
}};

// Finish pFirst -> worker must receive entry-versions (Priority 0) BEFORE entries (Priority 1)!
completeActive({{step: 1}});
assert.equal(await pFirst.then(r => r.json()).then(d => d.step), 1);
assert.equal(w.posted.length, 3);
assert.equal(w.posted[2].path, '/api/entry-versions?id=ver_1');

completeActive({{step: 2}});
assert.equal(await pVersions.then(r => r.json()).then(d => d.step), 2);
assert.equal(w.posted.length, 4);
assert.equal(w.posted[3].path, '/api/entries?offset=20&limit=20');

completeActive({{step: 3}});
assert.equal(await pEntriesQueued.then(r => r.json()).then(d => d.step), 3);
assert.equal(w.posted.length, 5);
assert.equal(w.posted[4].path, '/api/facets?type=spell');

// Aborted queued facet job must NEVER have been posted to worker
assert.equal(w.posted.some(m => m.path === '/api/facets?type=feat'), false);

// Finish last job -> deferred warm() now fires
completeActive({{step: 4}});
assert.equal(await pFacets.then(r => r.json()).then(d => d.step), 4);
assert.equal(w.posted.length, 6);
assert.deepEqual(w.posted[5], {{type: 'warm'}});

// 2. Active abort drops late reply and pumps next queued job; worker error rejects pending & removes abort listeners
let removedAbort = 0;
const acActive = new AbortController();
const origRemove = acActive.signal.removeEventListener.bind(acActive.signal);
acActive.signal.removeEventListener = (...args) => {{ removedAbort++; return origRemove(...args); }};
const pActiveAborted = pagesRequest('/api/entries?type=spell', {{signal: acActive.signal}});
await flush();
const pFollow = pagesRequest('/api/meta');
await flush();
const abortedId = w.posted[6].id;
acActive.abort(new DOMException('abort active', 'AbortError'));
await assert.rejects(pActiveAborted, {{name: 'AbortError'}});
assert.equal(removedAbort, 1);

// Worker replies late to abortedId -> response ignored, next queued job (pFollow) pumped!
w.emit('message', {{data: {{id: abortedId, status: 200, headers: {{}}, body: new Uint8Array()}}}});
assert.equal(w.posted.length, 8);
assert.equal(w.posted[7].path, '/api/meta');

// Worker error while pFollow (with signal) + pTail (with signal) are active/queued -> cleans listeners & rejects both
const acErr = new AbortController();
let removedErr = 0;
const origErrRemove = acErr.signal.removeEventListener.bind(acErr.signal);
acErr.signal.removeEventListener = (...args) => {{ removedErr++; return origErrRemove(...args); }};
const pTailErr = pagesRequest('/api/facets?type=creature', {{signal: acErr.signal}});
await flush();
w.emit('error', {{message: 'worker crashed'}});
assert.equal(w.terminated, true);
await assert.rejects(pFollow, /worker crashed/);
await assert.rejects(pTailErr, /worker crashed/);
assert.equal(removedErr, 1);
resetState();
"""
        proc = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)


if __name__ == '__main__':
    unittest.main()
