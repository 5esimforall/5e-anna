"""Storage/correspondence contracts only; rule facts are accepted from saved data."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from anna.snapshots.workflow import knowledge_dependencies, report, review_dependencies
from anna.snapshots.baseline import activate, active, capture, connect, verify, materialize, decisions_path, objects
from anna.snapshots.correspondence import Correspondence, UnmappedEvidence
from anna.serialization import canonical, digest


class Catalog:
    def __init__(self, root, name, text='Heading\ncontent', path='topics/source.htm', offset=0, suffix=''):
        self.root = root / name
        self.root.mkdir()
        self.source = self.root / 'source'
        (self.source / 'topics').mkdir(parents=True)
        self.path = path
        self.text = text
        (self.source / path).write_text(text)
        (self.source / 'webhelpcontents.htm').write_text('directory')
        self.database = self.root / 'catalog.sqlite'
        self.log = root / 'decisions.jsonl'
        if not self.log.exists():
            self.log.write_bytes(b'')
        self.db = sqlite3.connect(self.database)
        self.db.executescript('''
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE pages(path TEXT PRIMARY KEY,text TEXT,sha256 TEXT);
            CREATE TABLE entries(id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE sources(id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE relations(id TEXT PRIMARY KEY,subject_id TEXT,predicate TEXT,object_id TEXT,evidence TEXT);
            CREATE TABLE raw_tables(id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE rule_knowledge(id TEXT PRIMARY KEY,subject_id TEXT,kind TEXT,status TEXT,data TEXT);
            CREATE TABLE facts(id TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE knowledge_decisions(id TEXT PRIMARY KEY,data TEXT);
        ''')
        self.db.execute('INSERT INTO pages VALUES(?,?,?)', (path, text, sha256(text.encode()).hexdigest()))
        summary = {'full_scan': True, 'counterparts': {'status': 'skipped'}, 'runtime_seconds': 1}
        inputs = {'webhelpcontents.htm': sha256(b'directory').hexdigest()}
        snap = {'sha256': sha256(b'').hexdigest(), 'events': 0, 'bytes': 0, 'path': str(self.log)}
        for k, v in [('summary', summary), ('concept_coverage', {'discovery_inputs': inputs}), ('review_snapshot', snap)]:
            self.db.execute('INSERT INTO metadata VALUES(?,?)', (k, canonical(v)))
        self.entry = dict(id='entry' + suffix, entity_id='entity' + suffix, name='Heading', name_zh='Heading', name_en='',
                          type='section', page_path=path, start=offset, end=len(text), title_end=offset + len('Heading'),
                          anchors=[], parent_id=None, source_ids=[], ruleset='unknown', fields={})
        self.db.execute('INSERT INTO entries VALUES(?,?)', (self.entry['id'], canonical(self.entry)))
        self.record = dict(id='knowledge' + suffix, subject_id=self.entry['id'], kind='definition_role', schema=2,
                           evidence={'path': path, 'span': [offset, len(text)], 'raw': text[offset:]},
                           value={'role': 'explanation', 'unparsed': []}, dependencies=[], status='resolved', ruleset='unknown')
        self.db.execute('INSERT INTO rule_knowledge VALUES(?,?,?,?,?)',
                        (self.record['id'], self.entry['id'], self.record['kind'], self.record['status'], canonical(self.record)))
        self.db.execute("INSERT INTO facts VALUES('one','stored observation')")
        self.db.commit()

    def close(self):
        self.db.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalogs = []

    def tearDown(self):
        for c in self.catalogs:
            c.close()
        self.temp.cleanup()

    def catalog(self, *args, **kwargs):
        c = Catalog(self.root, *args, **kwargs)
        self.catalogs.append(c)
        return c

    def capture(self, c):
        bundle = self.root / 'baseline'
        capture(c.database, bundle, c.source, c.log, pointer=self.root / 'active.json')
        return bundle

    def test_capture_is_independent_verified_and_never_overwritten(self):
        c = self.catalog('old')
        bundle = self.capture(c)
        self.assertEqual(verify(bundle)['tables']['facts']['rows'], 1)
        pointer = self.root / 'active.json'
        activate(bundle, pointer, reviewer='tester', reason='initial fixture', bootstrap=True)
        self.assertEqual(active(pointer), bundle.resolve())
        c.db.execute("DELETE FROM facts"); c.db.commit()
        with closing(connect(materialize(bundle, self.root / 'cache'))) as db:
            self.assertEqual(db.execute('SELECT value FROM facts').fetchall(), [('stored observation',)])
        with self.assertRaisesRegex(ValueError, 'already exists'):
            capture(c.database, bundle, c.source, c.log, pointer=self.root / 'active.json')
        objects(bundle).path(verify(bundle)['review_log']).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'object'):
            verify(bundle)
        with self.assertRaises(ValueError):
            active(pointer)

    def test_retired_snapshot_and_pointer_versions_are_rejected(self):
        bundle=self.capture(self.catalog('source'))
        original=verify(bundle)
        pointer=self.root/'active.json'
        pointer.write_text(canonical({'schema':1,'path':'baseline','id':original['id']}))
        with self.assertRaisesRegex(ValueError,'pointer schema'):active(pointer)
        manifest={**original,'schema':1}
        manifest['id']='baseline_'+digest({k:v for k,v in manifest.items() if k!='id'})
        (bundle/'manifest.json').write_text(canonical(manifest))
        for action in (verify,materialize,decisions_path):
            with self.subTest(action=action.__name__), self.assertRaisesRegex(ValueError,'manifest'):
                action(bundle)

    def test_changed_source_and_partial_dataset_cannot_be_frozen(self):
        c = self.catalog('old')
        (c.source / c.path).write_text('different')
        with self.assertRaisesRegex(ValueError, 'no longer matches'):
            self.capture(c)
        self.assertFalse((self.root / 'baseline').exists())
        c.db.execute("UPDATE metadata SET value=? WHERE key='summary'", (canonical({'full_scan': False}),))
        c.db.commit()
        with self.assertRaisesRegex(ValueError, 'full dataset'):
            self.capture(c)

    def test_all_rows_not_counts_are_the_acceptance_basis(self):
        c = self.catalog('old')
        bundle = self.capture(c)
        out = self.root / 'report'
        result = report(c.database, bundle, out, c.log)
        self.assertEqual(result['status'], 'preserved')
        self.assertEqual(result['knowledge_dependencies'], {'unchanged': 1})
        self.assertEqual(result['assets']['facts']['unchanged'], 1)
        c.db.execute("DELETE FROM facts")
        c.db.execute("INSERT INTO facts VALUES('two','stored observation')"); c.db.commit()
        result = report(c.database, bundle, out, c.log)
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual((result['assets']['facts']['missing'], result['assets']['facts']['added']), (1, 1))
        self.assertFalse(result['approvals_applied'])
        with self.assertRaisesRegex(ValueError, 'separate'):
            report(c.database, bundle, c.database.parent, c.log)

    def test_content_change_does_not_hide_behind_stable_ids(self):
        old = self.catalog('old')
        new = self.catalog('new', text='Heading\nchanged')
        alignment = Correspondence(old.db, new.db)
        self.assertIsNone(alignment.entry_results[0]['after'])
        self.assertEqual(alignment.entry_results[0]['locator_candidates'], ['entry'])
        self.assertIsNone(alignment.span(old.path, 0, len(old.text))['after'])

    def test_page_move_and_prefix_insertion_transport_dependencies(self):
        old = self.catalog('old')
        moved = self.catalog('moved', path='topics/moved.htm', suffix='-new')
        alignment = Correspondence(old.db, moved.db)
        self.assertEqual(alignment.page_results[0]['status'], 'relocated')
        self.assertEqual(list(knowledge_dependencies(old.db, moved.db, alignment))[0]['status'], 'relocated')
        prefixed = self.catalog('prefixed', text='prefix\n' + old.text, offset=7)
        alignment = Correspondence(old.db, prefixed.db)
        self.assertEqual(alignment.span(old.path, 8, len(old.text))['after']['span'], [15, len(prefixed.text)])
        self.assertEqual(list(knowledge_dependencies(old.db, prefixed.db, alignment))[0]['status'], 'relocated')

    def test_duplicate_destinations_are_ambiguous_not_first_match(self):
        old = self.catalog('old')
        new = self.catalog('new', path='topics/a.htm', suffix='-new')
        new.db.execute('INSERT INTO pages SELECT ?,text,sha256 FROM pages', ('topics/b.htm',)); new.db.commit()
        alignment = Correspondence(old.db, new.db)
        self.assertEqual(alignment.page_results[0]['status'], 'ambiguous')
        self.assertEqual(alignment.page_results[0]['candidates'], ['topics/a.htm', 'topics/b.htm'])
        self.assertIsNone(alignment.entry_results[0]['after'])
        with self.assertRaises(UnmappedEvidence):
            alignment.transport(old.record)

    def test_equal_quote_with_changed_enclosing_condition_requires_review(self):
        old = self.catalog('old', text='context A\nHeading\ncontent', offset=10)
        new = self.catalog('new', text='context B\nHeading\ncontent', offset=10)
        for c in (old, new):
            parent = {**c.entry, 'id': 'parent', 'entity_id': 'parent', 'start': 0, 'title_end': 9, 'anchors': ['parent']}
            c.db.execute('INSERT INTO entries VALUES(?,?)', ('parent', canonical(parent))); c.db.commit()
        alignment = Correspondence(old.db, new.db)
        check = list(knowledge_dependencies(old.db, new.db, alignment))[0]
        self.assertEqual(check['status'], 'needs_review')
        self.assertTrue(any(c['path'][0] == 'enclosing_sources' for c in check['changes']))

    def test_table_structure_is_checked_even_when_text_is_unchanged(self):
        old, new = self.catalog('old'), self.catalog('new')
        for c, size in [(old, '1'), (new, '2')]:
            table = {'id': 'table', 'page_path': c.path, 'start': 0, 'end': len(c.text), 'rowspan': size}
            c.db.execute('INSERT INTO raw_tables VALUES(?,?)', ('table', canonical(table))); c.db.commit()
        check = list(knowledge_dependencies(old.db, new.db, Correspondence(old.db, new.db)))[0]
        self.assertEqual(check['status'], 'needs_review')
        self.assertTrue(any(c['path'][0] == 'tables' for c in check['changes']))

    def test_competing_records_and_tables_are_not_chosen_by_load_order(self):
        old, new = self.catalog('old'), self.catalog('new')
        duplicate = {**new.record, 'id': 'competing'}
        new.db.execute('INSERT INTO rule_knowledge VALUES(?,?,?,?,?)',
                       (duplicate['id'], duplicate['subject_id'], duplicate['kind'], duplicate['status'], canonical(duplicate)))
        for c, ids in [(old, ['old-table']), (new, ['new-table', 'competing-table'])]:
            for tid in ids:
                t = {'id': tid, 'page_path': c.path, 'start': 0, 'end': len(c.text)}
                c.db.execute('INSERT INTO raw_tables VALUES(?,?)', (tid, canonical(t)))
            c.db.commit()
        alignment = Correspondence(old.db, new.db)
        check = list(knowledge_dependencies(old.db, new.db, alignment))[0]
        self.assertEqual(check['status'], 'missing')
        self.assertEqual(check['reason'], 'subject_or_record_missing_or_ambiguous')
        self.assertNotIn('old-table', alignment.ids)

    def test_valid_review_is_previewed_but_stale_review_is_not_revived(self):
        from anna.knowledge.repository import Context, binding
        old = self.catalog('old')
        new = self.catalog('new', path='topics/moved.htm', suffix='-new')
        basis = Context(old.db).basis(old.record)
        event = {'schema': 1, 'id': 'review', 'created_at': 'now', 'operation': 'knowledge_accept',
                 'reviewer': 'tester', 'reason': 'generic lifecycle', 'binding': binding(old.record),
                 'basis': basis, 'basis_sha256': digest(basis)}
        old.log.write_text(canonical(event) + '\n')
        old.db.execute('INSERT INTO knowledge_decisions VALUES(?,?)', ('review', canonical({'event': event, 'status': 'applied'})))
        old.db.commit()
        alignment = Correspondence(old.db, new.db)
        list(knowledge_dependencies(old.db, new.db, alignment))
        result = list(review_dependencies(old.db, new.db, alignment, old.log, new.log))[0]
        self.assertEqual((result['status'], result['after_status']), ('eligible', 'pending'))
        blocked = [{'before': old.record['id'], 'status': 'needs_review'}]
        result = list(review_dependencies(old.db, new.db, alignment, old.log, new.log, blocked))[0]
        self.assertEqual(result['reason'], 'knowledge_dependencies_require_review')
        old.db.execute('UPDATE knowledge_decisions SET data=?', (canonical({'event': event, 'status': 'stale'}),)); old.db.commit()
        result = list(review_dependencies(old.db, new.db, alignment, old.log, new.log))[0]
        self.assertEqual(result['reason'], 'no_valid_baseline_approval')
        self.assertNotEqual(result['status'], 'eligible')
        self.assertEqual(json.loads(old.log.read_text()), event)


class CompactSnapshotTests(unittest.TestCase):
    setUp = MigrationTests.setUp
    tearDown = MigrationTests.tearDown
    catalog = MigrationTests.catalog
    capture = MigrationTests.capture

    def bootstrap(self):
        c = self.catalog('old')
        root = self.capture(c)
        pointer = self.root / 'active.json'
        activate(root, pointer, reviewer='tester', reason='fixture root', bootstrap=True)
        return c, root, pointer

    def candidate(self, c, name='candidate'):
        root = self.root / name
        capture(c.database, root, c.source, c.log, pointer=self.root / 'active.json')
        return root

    def test_compact_roundtrip_deduplicates_and_recovers_corrupt_cache(self):
        c, baseline, pointer = self.bootstrap()
        first = verify(baseline)
        second = self.candidate(c)
        self.assertEqual(first['tables']['entries']['chunks'], verify(second)['tables']['entries']['chunks'])
        self.assertFalse((baseline / 'catalog.sqlite').exists())
        cache = self.root / 'cache'
        database = materialize(baseline, cache)
        database.write_bytes(b'corrupt cache')
        with closing(connect(materialize(baseline, cache))) as db:
            self.assertEqual(db.execute('SELECT * FROM facts').fetchall(), [('one', 'stored observation')])
        (database.parent / 'catalog.json').write_text('broken receipt')
        self.assertEqual(materialize(baseline, cache), database)
        self.assertEqual(decisions_path(baseline, cache).read_bytes(), b'')
        activate(second, pointer, reviewer='tester', reason='unchanged data')
        self.assertEqual(active(pointer), second.resolve())

    def test_gate_rejects_uncovered_and_accepts_evidenced_disposition(self):
        from anna.snapshots.gate import assess, dispose, dispositions, read_journal
        c, baseline, pointer = self.bootstrap()
        c.db.execute("UPDATE facts SET value='changed observation'"); c.db.commit()
        candidate = self.candidate(c)
        assessment = assess(candidate)
        self.assertEqual(len(assessment['issues']), 1)
        issue = assessment['issues'][0]
        self.assertEqual(issue['category'], 'asset_changes')
        with self.assertRaisesRegex(ValueError, 'Unresolved'):
            activate(candidate, pointer, reviewer='tester', reason='not yet disposed')
        self.assertEqual(active(pointer), baseline.resolve())
        with self.assertRaisesRegex(ValueError, 'evidence'):
            dispose(candidate, [issue['id']], 'accept_change', 'tester', 'fixed', ['not-evidence'])
        dispose(candidate, [issue['id']], 'retain_unresolved', 'tester', 'retained for later semantic review', [verify(candidate)['id']])
        self.assertEqual(dispositions(candidate, assessment), set())
        with self.assertRaisesRegex(ValueError, 'duplicate or conflicting'):
            dispose(candidate, [issue['id']], 'accept_change', 'tester', 'competing', [verify(candidate)['id']])
        activate(candidate, pointer, reviewer='tester', reason='all differences explained')
        self.assertTrue(read_journal(pointer.parent / 'promotions.jsonl')[-1]['retained_unresolved'])
        self.assertEqual(c.log.read_bytes(), b'')

    def test_stale_assessment_parent_race_and_bootstrap_bypass_are_rejected(self):
        from anna.snapshots.gate import assess, append_event
        c, baseline, pointer = self.bootstrap()
        a = self.candidate(c, 'a'); b = self.candidate(c, 'b')
        with self.assertRaisesRegex(ValueError, 'Bootstrap'):
            activate(a, pointer, reviewer='tester', reason='bypass', bootstrap=True)
        append_event(a / 'dispositions.jsonl', {'kind': 'disposition', 'assessment_id': 'stale'})
        with self.assertRaisesRegex(ValueError, 'Stale'):
            activate(a, pointer, reviewer='tester', reason='stale assessment')
        activate(b, pointer, reviewer='tester', reason='current successor')
        with self.assertRaisesRegex(ValueError, 'parent'):
            activate(a, pointer, reviewer='tester', reason='old parent')
        pointer.unlink()
        with self.assertRaisesRegex(ValueError, 'history disagree'):
            activate(baseline, pointer, reviewer='tester', reason='lost pointer', bootstrap=True)

    def test_json_column_fallback_blob_and_effective_delta_are_lossless(self):
        c = self.catalog('old')
        c.db.executescript('CREATE TABLE unusual(id TEXT PRIMARY KEY, data TEXT, independent BLOB);'
                           'CREATE TABLE effective_knowledge(id TEXT PRIMARY KEY,subject_id TEXT,kind TEXT,status TEXT,review_status TEXT,data TEXT);')
        c.db.execute('INSERT INTO unusual VALUES(?,?,?)', ('x', '{ "x" : 1 }', b'\x00\xff'))
        effective = {**c.record, 'review_status': 'stale'}
        c.db.execute('INSERT INTO effective_knowledge VALUES(?,?,?,?,?,?)',
                     (effective['id'], effective['subject_id'], effective['kind'], effective['status'], 'stale', canonical(effective)))
        c.db.commit()
        root = self.capture(c)
        self.assertEqual(verify(root)['tables']['effective_knowledge']['base'], 'rule_knowledge')
        with closing(connect(materialize(root, self.root / 'cache'))) as db:
            for name in ('unusual', 'effective_knowledge'):
                self.assertEqual(db.execute('SELECT * FROM ' + name).fetchall(), c.db.execute('SELECT * FROM ' + name).fetchall())

    def test_parent_review_history_cannot_be_dropped_before_capture(self):
        from anna.knowledge.repository import Context, binding
        c = self.catalog('old')
        basis = Context(c.db).basis(c.record)
        event = {'schema': 1, 'id': 'historical', 'created_at': 'now', 'operation': 'knowledge_accept',
                 'reviewer': 'tester', 'reason': 'pending historical event', 'binding': binding(c.record),
                 'basis': basis, 'basis_sha256': digest(basis)}
        c.log.write_text(canonical(event) + '\n')
        root = self.capture(c)
        activate(root, self.root / 'active.json', reviewer='tester', reason='history', bootstrap=True)
        c.log.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'append-only extension of the parent'):
            self.candidate(c)
        self.assertFalse((self.root / 'candidate').exists())

    def test_database_insertion_order_does_not_change_dependency_evidence(self):
        old, new = self.catalog('old'), self.catalog('new')
        for c, ids in [(old, ['first', 'second']), (new, ['second', 'first'])]:
            for tid in ids:
                table = {'id': tid, 'page_path': c.path, 'start': 0 if tid == 'first' else 1, 'end': len(c.text)}
                c.db.execute('INSERT INTO raw_tables VALUES(?,?)', (tid, canonical(table)))
            c.db.commit()
        checks = list(knowledge_dependencies(old.db, new.db, Correspondence(old.db, new.db)))
        self.assertEqual(checks[0]['status'], 'unchanged')

    def test_projection_encoding_is_retained_and_changed_recipe_fails_closed(self):
        from anna.knowledge.reading import build_rule_index
        from unittest.mock import patch
        c = self.catalog('old')
        c.db.execute('CREATE TABLE effective_knowledge(id TEXT PRIMARY KEY,subject_id TEXT,kind TEXT,status TEXT,review_status TEXT,data TEXT)')
        record = {**c.record, 'review_status': 'stale', 'value': {'rules': [{
            'id': 'link', 'target': {'resolution': {'status': 'resolved', 'targets': ['entry']}, 'evidence': c.record['evidence']},
            'relation': 'applies', 'scope': {'z': 1, 'a': 2}, 'when': None, 'change': {}, 'ends': None}]}}
        c.db.execute('INSERT INTO effective_knowledge VALUES(?,?,?,?,?,?)',
                     (record['id'], record['subject_id'], record['kind'], record['status'], 'stale', canonical(record)))
        build_rule_index(c.db, [record]); c.db.commit()
        root = self.capture(c)
        self.assertIn('encoding_overrides', verify(root)['tables']['knowledge_rule_links'])
        with closing(connect(materialize(root, self.root / 'cache'))) as db:
            self.assertEqual(db.execute('SELECT * FROM knowledge_rule_links').fetchall(), c.db.execute('SELECT * FROM knowledge_rule_links').fetchall())
        with patch('anna.snapshots.storage.projection_rows', return_value=iter(())):
            with self.assertRaisesRegex(ValueError, 'Restored table mismatch'):
                materialize(root, self.root / 'changed-recipe-cache')

    def test_exports_only_discard_proven_duplicates(self):
        from anna.snapshots.baseline import export_snapshot
        c = self.catalog('old')
        (c.database.parent / 'entries.jsonl').write_text(canonical(c.entry) + '\n')
        (c.database.parent / 'retired.jsonl').write_text('{"historical":"unresolved"}\n')
        root = self.capture(c)
        m = verify(root)
        self.assertEqual(m['exports']['entries.jsonl']['recipe']['kind'], 'jsonl')
        self.assertIn('object', m['exports']['retired.jsonl'])
        out = self.root / 'exports'
        export_snapshot(root, out)
        self.assertEqual(json.loads((out / 'entries.jsonl').read_text()), c.entry)
        self.assertEqual((out / 'retired.jsonl').read_bytes(), (c.database.parent / 'retired.jsonl').read_bytes())
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            export_snapshot(root, out)


if __name__ == '__main__':
    unittest.main()
