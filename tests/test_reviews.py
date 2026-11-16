"""Unified reference annotation lifecycle contract; isolated synthetic sources, never the live log."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from anna.catalog.concepts import build_concepts
from anna.catalog.references import build_references, inspect_reference
from anna.review.repository import revoke_annotation, save_annotation
from anna.presentation.references import query_decisions, query_missing_definitions, query_references
from anna.review.search import search_candidates
from anna.review.store import (
    annotation_snapshot,
    append_annotation_events,
    extraction_hash,
    input_snapshot,
    load_annotations,
    locked,
    pipeline_hash,
    require_current_annotations,
)
from anna.serialization import digest
from anna.server.linked_book import MARKER, build_book, fingerprint
from anna.source.html import Document


class ReferenceAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        (self.source / 'topics').mkdir(parents=True)
        self.path = 'topics/example.htm'
        self.page = self.source / self.path
        self.page.write_text(
            '<h2 id="rule">共享规则 Shared Rule</h2><p>此处定义。</p>'
            '<h2>阅读上下文</h2><p>普通提及共享规则。</p>'
            '<p><a href="#rule">共享规则</a></p><p><a href="#missing">未知引用</a></p>'
        )
        self.log = self.root / 'annotations.jsonl'
        self.database = self.root / 'catalog.sqlite'
        self.db = None
        self.rebuild()

    def tearDown(self):
        if self.db:
            self.db.close()
        self.temp.cleanup()

    def rebuild(self, competitor=False, target_id='target', target_empty=False):
        if self.db:
            self.db.close()
        if self.database.exists():
            self.database.unlink()
        self.db = sqlite3.connect(self.database)
        self.db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE pages(path TEXT PRIMARY KEY,text TEXT,sha256 TEXT,source_ids TEXT);
            CREATE TABLE entries(id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE sources(id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE review_queue(id TEXT PRIMARY KEY,target TEXT,reason TEXT,priority TEXT,data TEXT);
        ''')
        doc = Document(self.path, self.page.read_text())
        headings = doc.body.find_all('h2')
        start, title_end = doc.span(headings[0])
        context_start, context_title = doc.span(headings[1])
        target_start, target_end = (len(doc.text), len(doc.text)) if target_empty else (start, context_start)
        target_title_end = len(doc.text) if target_empty else title_end
        target = dict(
            id=target_id, entity_id=target_id, name='共享规则 Shared Rule', name_zh='共享规则', name_en='Shared Rule',
            type='rule', page_path=self.path, start=target_start, title_end=target_title_end, end=target_end,
            anchors=['rule'], ruleset='5r', source_ids=[], is_primary=True,
        )
        context = dict(
            id='context', entity_id='context', name='阅读上下文', name_zh='阅读上下文', name_en='',
            type='section', page_path=self.path, start=context_start, title_end=context_title, end=len(doc.text),
            anchors=[], ruleset='5r', source_ids=[], is_primary=False,
        )
        entries = [target, context]
        if target_empty:
            entries.append({
                **target, 'id': 'heading_guard', 'entity_id': 'heading_guard',
                'type': 'section', 'start': start, 'title_end': title_end, 'end': context_start,
                'anchors': [], 'is_primary': False,
            })
        if competitor:
            entries.append({**target, 'id': 'competitor', 'entity_id': 'competitor', 'anchors': []})
        page = {'text': doc.text, 'sha256': fingerprint(self.page), 'source_ids': [], 'parse_status': 'ok'}
        self.db.execute('INSERT INTO pages VALUES(?,?,?,?)', (self.path, doc.text, page['sha256'], '[]'))
        self.db.executemany('INSERT INTO entries VALUES(?,?)', [(e['id'], json.dumps(e)) for e in entries])
        doc.close()
        build_concepts(self.db, self.source, self.root, entries, {self.path: page})
        build_references(self.db, self.source, self.root, self.log)
        self.db.execute(
            'INSERT INTO metadata VALUES(?,?)',
            ('summary', json.dumps({'full_scan': True, 'pipeline_sha256': pipeline_hash(), 'extraction_sha256': extraction_hash()})),
        )
        self.db.commit()

    def plain(self):
        return query_references(self.db, signal='plain')['rows'][0]

    def confirm(self, ref=None, target='target', supersedes=None):
        ref = ref or self.plain()
        evidence = inspect_reference(self.db, ref['id'], target, path=self.log)
        return save_annotation(
            self.db, self.log, ref['id'], 'reference', reviewer='fixture', reason='synthetic reference evidence',
            ruling='confirm', target=target, expected_content_sha256=evidence['content_sha256'],
            expected_context_sha256=evidence['context_sha256'], supersedes=supersedes,
        )

    def test_invalid_basis_schema_or_digest_rejected_without_rewriting(self):
        event = self.confirm()
        before = self.log.read_bytes()
        for schema in (None, 2, 99):
            basis = {**event['basis']}
            if schema is None:
                basis.pop('schema')
            else:
                basis['schema'] = schema
            pending = {k: v for k, v in event.items() if k not in {'id', 'schema', 'created_at'}}
            pending.update(basis=basis)
            with self.assertRaisesRegex(ValueError, 'Unsupported annotation basis schema'):
                append_annotation_events(self.log, [pending], load_annotations(self.log)[1])
            self.assertEqual(self.log.read_bytes(), before)

    def test_save_replay_book_and_revoke(self):
        original = self.plain()
        event = self.confirm(original)
        with self.assertRaisesRegex(ValueError, 'replayed'):
            require_current_annotations(self.db, self.log)
        self.rebuild()
        ref = query_references(self.db, reference_id=original['id'])['rows'][0]
        self.assertEqual((ref['target_id'], ref['review_status'], ref['can_link']), ('target', 'applied', True))
        raw = json.loads(self.db.execute('SELECT data FROM effective_references WHERE id=?', (original['id'],)).fetchone()[0])
        self.assertEqual((raw['automatic']['status'], raw['automatic']['target_id']), ('review', None))
        book = self.root / 'book'
        build_book(self.source, self.database, book)
        generated = (book / self.path).read_bytes()
        self.assertEqual(generated.split(MARKER)[0], self.page.read_bytes())
        payload = json.loads(generated.split(b'id="anna-bindings">')[1].split(b'</script>')[0])
        self.assertEqual(next(r['id'] for r in payload['references'] if r['reference_id'] == ref['id']), ref['target_id'])
        self.rebuild()
        self.assertEqual(self.plain()['review_status'], 'applied')
        revoke_annotation(self.log, event['id'], 'fixture', 'undo synthetic approval')
        self.rebuild()
        ref = self.plain()
        self.assertEqual((ref['status'], ref['review_status'], ref['can_link']), ('review', 'revoked', False))
        self.assertEqual(len(load_annotations(self.log)[0]), 2)

    def test_portable_transfer_across_offset_shifts_comments_and_regenerated_ids(self):
        self.confirm()
        self.page.write_text('<p>前插无关说明。</p>' + self.page.read_text() + '<!-- no visible change -->')
        self.rebuild(target_id='ver_regenerated_target')
        ref = self.plain()
        self.assertEqual(ref['review_status'], 'applied')
        self.assertEqual(ref['target_id'], 'ver_regenerated_target')
        self.assertTrue(ref['can_link'])

    def test_competitor_unlocatable_or_context_rewrite_becomes_stale(self):
        event = self.confirm()
        self.rebuild(competitor=True)
        self.assertEqual(self.plain()['review_status'], 'stale')
        self.assertFalse(self.plain()['can_link'])
        self.assertEqual(query_decisions(self.db, status='stale')['rows'][0]['event']['id'], event['id'])
        changes = query_decisions(self.db, status='stale')['rows'][0]['changes']
        self.assertTrue(any(c['path'][0] in {'name_competitors', 'target'} for c in changes))
        # Unlocatable target also invalidates confirmation cleanly.
        self.rebuild(target_empty=True)
        self.assertEqual(self.plain()['review_status'], 'stale')
        self.assertFalse(self.plain()['can_link'])
        # Editing the annotated sentence itself fails anchor binding.
        self.page.write_text(self.page.read_text().replace('普通提及共享规则。', '改写后提及共享规则。'))
        self.rebuild()
        self.assertEqual(self.plain()['review_status'], 'stale')
        self.assertIsNone(self.plain()['target_id'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM review_queue WHERE reason='review_decision_stale'").fetchone()[0], 1)

    def test_conflict_does_not_choose_last_line(self):
        event = self.confirm()
        with locked(self.log):
            duplicate = {k: v for k, v in event.items() if k not in {'schema', 'id', 'created_at'}}
            append_annotation_events(self.log, [duplicate], load_annotations(self.log)[1])
        self.rebuild()
        self.assertEqual(self.plain()['review_status'], 'conflict')
        self.assertIsNone(self.plain()['target_id'])
        self.assertEqual(query_decisions(self.db, status='conflict')['total'], 2)

    def test_non_reference_supersession_and_no_resurrection(self):
        event = self.confirm()
        self.rebuild()
        ref = self.plain()
        evidence = inspect_reference(self.db, ref['id'], path=self.log)
        replacement = save_annotation(
            self.db, self.log, ref['id'], 'reference', reviewer='fixture', reason='ordinary prose',
            ruling='non_reference', target=None, expected_content_sha256=evidence['content_sha256'],
            expected_context_sha256=evidence['context_sha256'], supersedes=event['id'],
        )
        self.rebuild()
        self.assertEqual(self.plain()['reason'], 'reviewed_non_reference')
        revoke_annotation(self.log, replacement['id'], 'fixture', 'undo')
        self.rebuild()
        self.assertEqual(self.plain()['status'], 'review')
        self.assertEqual(query_decisions(self.db, status='superseded')['total'], 1)

    def test_stale_save_and_pending_log_are_rejected(self):
        ref = self.plain()
        with self.assertRaisesRegex(ValueError, 'basis changed'):
            save_annotation(
                self.db, self.log, ref['id'], 'reference', reviewer='fixture', reason='reason',
                ruling='confirm', target='target', expected_content_sha256='old',
            )
        self.confirm()
        with self.assertRaisesRegex(ValueError, 'active annotation'):
            self.confirm()
        with self.assertRaisesRegex(ValueError, 'replayed'):
            build_book(self.source, self.database, self.root / 'book')

    def test_pending_events_allow_consecutive_saves_before_rebuild(self):
        plain_ref = self.plain()
        explicit_ref = next(r for r in query_references(self.db, signal='explicit')['rows'] if r['text'] == '未知引用')
        first = self.confirm(ref=plain_ref)
        second = self.confirm(ref=explicit_ref)
        with self.assertRaisesRegex(ValueError, 'replayed'):
            require_current_annotations(self.db, self.log)
        # Superseding a pending un-replayed revision succeeds before rebuilding.
        self.confirm(ref=plain_ref, supersedes=first['id'])
        self.rebuild()
        self.assertEqual(query_decisions(self.db, status='applied')['total'], 2)
        self.assertEqual(query_decisions(self.db, status='superseded')['total'], 1)
        self.assertEqual(len({first['id'], second['id']}), 2)

    def test_search_keeps_evidence_and_never_resolves(self):
        ref = self.plain()
        before = dict(ref)
        result = search_candidates(self.db, ref['id'])
        self.assertFalse(result['automatic_approval'])
        target = next(r for r in result['rows'] if r['id'] == 'target')
        self.assertIn('exact_name', [r['code'] for r in target['reasons']])
        self.assertEqual(query_references(self.db, reference_id=ref['id'])['rows'][0], before)
        self.assertEqual(search_candidates(self.db, ref['id'], query='absent label')['total'], 0)
        with self.assertRaises(LookupError):
            search_candidates(self.db, 'missing')

    def test_missing_definition_lifecycle_and_withdrawn_repair(self):
        ref = next(r for r in query_references(self.db, signal='explicit')['rows'] if r['text'] == '未知引用')
        view = inspect_reference(self.db, ref['id'], missing_definition=True, path=self.log)
        issue = save_annotation(
            self.db, self.log, ref['id'], 'reference', reviewer='fixture', reason='No suitable independent definition',
            ruling='missing_definition', target=None, expected_content_sha256=view['content_sha256'],
            expected_context_sha256=view['context_sha256'],
        )
        self.rebuild()
        self.assertEqual(query_missing_definitions(self.db, status='open')['total'], 1)
        result = query_references(self.db, reference_id=ref['id'])['rows'][0]
        self.assertEqual((result['status'], result['review_status'], result['can_link']), ('unresolved', 'applied', False))
        repair = self.confirm(ref=result, supersedes=issue['id'])
        self.rebuild()
        self.assertEqual(query_missing_definitions(self.db, status='resolved')['rows'][0]['target_id'], 'target')
        revoke_annotation(self.log, repair['id'], 'fixture', 'Withdraw repair')
        self.rebuild()
        self.assertEqual(query_missing_definitions(self.db, status='needs_review')['total'], 1)
        self.assertFalse(query_references(self.db, reference_id=ref['id'])['rows'][0]['can_link'])

    def test_removed_reference_remains_in_annotation_history(self):
        event = self.confirm()
        self.page.write_text('<h2 id="rule">共享规则 Shared Rule</h2><p>此处定义。</p><h2>阅读上下文</h2>')
        self.rebuild()
        rows = query_decisions(self.db, status='missing')['rows']
        self.assertEqual([r['event']['id'] for r in rows], [event['id']])

    def test_empty_original_links_have_distinct_ids_and_no_click_layer(self):
        self.page.write_text(self.page.read_text() + '<a href="#rule"></a><a href="#rule"></a>')
        self.rebuild()
        refs = query_references(self.db, signal='explicit')['rows']
        self.assertEqual(len(refs), len({r['id'] for r in refs}))
        empty = [r for r in refs if r['start'] == r['end']]
        self.assertEqual(len(empty), 2)
        self.assertTrue(all(not r['can_link'] and r['presentation'] == 'empty_range' for r in empty))

    def test_definition_titles_and_unlocatable_targets_cannot_be_approved(self):
        self.page.write_text(self.page.read_text().replace('阅读上下文', '<a href="#rule">阅读上下文</a>'))
        self.rebuild()
        title = next(r for r in query_references(self.db, signal='explicit')['rows'] if r['presentation'] == 'definition_title')
        with self.assertRaisesRegex(ValueError, 'definition entry'):
            self.confirm(title)
        with self.assertRaisesRegex(ValueError, 'Target entry not found'):
            inspect_reference(self.db, self.plain()['id'], 'missing', path=self.log)

    def test_invalid_log_is_rejected_without_rewriting_it(self):
        self.log.write_text('{"schema":99}\n')
        before = self.log.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Invalid annotation log'):
            load_annotations(self.log)
        self.assertEqual(self.log.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
