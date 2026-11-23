"""Tests for Pages export performance optimizations: query plans, parity, and cache invalidation."""
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import anna.paths as paths
from anna.presentation import catalog
from anna.presentation.collections import BookCatalog, Collection, CollectionRegistry, DEFAULT, _clear_book_connections
from anna.presentation.library import Library, Scope, name_fold
from anna.presentation.our5etools import Our5etoolsCatalog
from anna.publish.pages_data import _project_our_db


OUR5ETOOLS_DATA = paths.PROJECT / 'data' / 'our5etools'


class Phase1QueryPlanAndParityTests(unittest.TestCase):
    def test_5echm_projected_query_plans_and_parity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            source = root / 'source'
            (source / 'topics').mkdir(parents=True)
            page = source / 'topics' / 'spells.htm'
            page.write_bytes(b'<html><body><h3>Fireball</h3><p>Boom</p></body></html>')
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
                from hashlib import sha256
                sha = sha256(page.read_bytes()).hexdigest()
                c.execute("INSERT INTO metadata VALUES ('summary', '{\"total\":2}')")
                c.execute("INSERT INTO sources VALUES ('src1','PHB','官方','5r','','',0,'','{\"id\":\"src1\",\"name\":\"PHB\",\"label\":\"PHB\",\"aliases\":[],\"coverage\":\"官方\"}')")
                c.execute("INSERT INTO sources VALUES ('src2','Empty','官方','5e','','',0,'','{\"id\":\"src2\",\"name\":\"Empty\",\"label\":\"Empty\",\"aliases\":[],\"coverage\":\"官方\"}')")
                c.execute("INSERT INTO pages VALUES ('topics/spells.htm','Spells','rule','ok',?,'Fireball\\nBoom','[\"src1\"]','{}')", (sha,))
                c.execute("INSERT INTO entities VALUES ('ent1','ok')")
                d1 = '{"id":"ver_1","name":"火球术 Fireball","name_zh":"火球术","name_en":"Fireball","type":"spell","page_path":"topics/spells.htm","start":0,"end":13,"title_end":8,"ruleset":"5r","legacy":"unmarked","classification_status":"原文明示","source_ids":["src1"],"source_names":["PHB"],"fields":{"spell_level":3},"field_conflicts":[]}'
                d2 = '{"id":"ver_2","name":"火球术 (旧版) Fireball","name_zh":"火球术","name_en":"Fireball","type":"spell","page_path":"topics/spells.htm","start":0,"end":13,"title_end":8,"ruleset":"5e","legacy":"entry_marked","classification_status":"原文明示","source_ids":["src1"],"source_names":["PHB"],"fields":{"spell_level":3},"field_conflicts":[]}'
                c.execute("INSERT INTO entries VALUES ('ver_1','ent1','火球术 Fireball','火球术','Fireball','spell','topics/spells.htm',0,13,'5r','unmarked','原文明示','{\"spell_level\":3}',?)", (d1,))
                c.execute("INSERT INTO entries VALUES ('ver_2','ent1','火球术 (旧版) Fireball','火球术','Fireball','spell','topics/spells.htm',0,13,'5e','entry_marked','原文明示','{\"spell_level\":3}',?)", (d2,))
                c.execute("INSERT INTO entry_sources VALUES ('ver_1','src1')")
                c.execute("INSERT INTO entry_sources VALUES ('ver_2','src1')")
                c.execute("INSERT INTO identity_candidates VALUES ('grp_1','spell','same_name','{\"entry_ids\":[\"ver_1\",\"ver_2\"]}')")
                c.commit()

            proj = root / 'proj.sqlite'
            from anna.publish.pages_data import _project_db
            _project_db(orig, proj, source)

            with closing(catalog.connect(orig)) as c_orig, closing(catalog.connect(proj)) as c_proj:
                self.assertEqual(catalog.works(c_proj), catalog.works(c_orig))
                self.assertEqual(catalog.meta(c_proj), catalog.meta(c_orig))
                self.assertEqual(catalog.detail(c_proj, 'ver_1'), catalog.detail(c_orig, 'ver_1'))
                self.assertTrue(catalog.has(c_proj, 'ver_1'))
                self.assertFalse(catalog.has(c_proj, 'ver_missing'))
                self.assertEqual(
                    {r['id'] for r in catalog.same_name(c_proj, 'spell', name_fold('Fireball'))},
                    {'ver_1', 'ver_2'},
                )
                # Verify works/meta count query avoids MATERIALIZE entries
                plans = [
                    r[3] for r in c_proj.execute(
                        "EXPLAIN QUERY PLAN SELECT es.source_id, count(*) FROM entry_sources es "
                        "JOIN entry_core e ON e.id=es.entry_id WHERE e.type!='section' GROUP BY es.source_id"
                    )
                ]
                self.assertFalse(any('MATERIALIZE' in p for p in plans), plans)
                # Verify version_compare hits identity_candidate_members without scanning identity_candidates
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


if __name__ == '__main__':
    unittest.main()
