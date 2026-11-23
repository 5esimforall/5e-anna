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
from anna.publish.pages_data import CHUNK_SIZE, _project_db, _project_our_db, export_pages_data
from anna.server.pages import PublishedSite
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
                        'SELECT start, end, target_id FROM effective_references WHERE page_path=? AND target_id IS NOT NULL AND end > start ORDER BY start, id',
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
                        'SELECT start, end, target_id FROM effective_references WHERE page_path=? AND target_id IS NOT NULL AND end > start ORDER BY start, id',
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

                    # Query plan checks: never materialize VIEW or join entry_body unnecessarily
                    for sql, bind in (
                        ("EXPLAIN QUERY PLAN SELECT ef.facet_value, count(*) FROM entry_facets ef JOIN entry_core e ON e.id=ef.entry_id WHERE ef.facet_key='origin' GROUP BY ef.facet_value", ()),
                        ("EXPLAIN QUERY PLAN SELECT el.layout_html, em.provenance_json FROM entry_core e LEFT JOIN entry_meta em ON em.entry_rowid=e.rowid LEFT JOIN entry_layout el ON el.entry_rowid=e.rowid WHERE e.id=?", ('o5e_fixture',)),
                        ("EXPLAIN QUERY PLAN SELECT r.source_id, e.id FROM relations r JOIN entry_core e ON e.id=r.source_id WHERE r.target_id=? AND r.type='belongs_to_class' AND e.type='subclass'", ('cls_1',)),
                    ):
                        plan = [r[3] for r in conn.execute(sql, bind)]
                        joined = ' | '.join(plan)
                        self.assertNotIn('MATERIALIZE', joined, plan)
                        self.assertNotIn('entry_body', joined, plan)
                        self.assertNotIn('SCAN entry_core', joined, plan)

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
                    self.assertEqual(proj_cat.ranked({'type': ['spell']}), orig_cat.ranked({'type': ['spell']}))
                    self.assertEqual(
                        proj_cat.ranked({'type': ['spell']}, sort_field='spell_level'),
                        orig_cat.ranked({'type': ['spell']}, sort_field='spell_level'),
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
        entry_idx = detail_js.index("api('/api/entry?id='", open_idx)
        layout_idx = detail_js.index('layoutOf(id)', open_idx)
        self.assertLess(entry_idx, layout_idx)

        pages_js = (paths.PROJECT / 'web' / 'pages.js').read_text(encoding='utf-8')
        self.assertIn('export function warm()', pages_js)
        kit_js = (paths.PROJECT / 'web' / 'kit.js').read_text(encoding='utf-8')
        self.assertIn('request, warm', kit_js)
        worker_js = (paths.PROJECT / 'web' / 'pages-worker.js').read_text(encoding='utf-8')
        self.assertIn("data.type === 'warm'", worker_js)
        app_js = (paths.PROJECT / 'web' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('annaKit.warm()', app_js)


if __name__ == '__main__':
    unittest.main()
