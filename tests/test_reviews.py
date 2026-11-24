"""Unified reference annotation lifecycle contract; isolated synthetic sources, never the live log."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from anna.catalog.concepts import build_concepts
from anna.catalog.references import build_references, inspect_reference, preview_annotations
from anna.presentation.collections import _clear_book_connections
from anna.presentation.layout import entry_layout, span_layout
from anna.presentation.references import (
    query_decisions,
    query_missing_candidates,
    query_missing_definitions,
    query_references,
)
from anna.publish.pages_data import export_pages_data
from anna.review.repository import (
    preview_annotate,
    preview_revoke,
    query_log_annotations,
    revoke_annotation,
    save_annotation,
)
from anna.review.search import search_candidates
from anna.review.store import (
    annotation_snapshot,
    append_annotation_events,
    check_rebuild_gate,
    extraction_hash,
    input_snapshot,
    load_annotations,
    locked,
    pipeline_hash,
    require_current_annotations,
)
from anna.server.pages import PublishedSite
from anna.serialization import digest
from anna.presentation.reader import fingerprint
from anna.publish.linked_book import MARKER, build_book
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

    def test_missing_candidates_groups_unregistered_unresolved_references(self):
        self.page.write_text(self.page.read_text() + '<p>' + ('补充语境说明文字。' * 8) + '第二处：<a href="#missing">未知引用</a>，<a href="#missing2">另一缺失</a>。<a href="#missing"></a></p>')
        self.rebuild()
        unfiltered = query_references(self.db, status='unresolved', reason='explicit_locator', kind='entry')
        self.assertEqual(unfiltered['total'], 4)
        by_text = query_missing_candidates(self.db)
        self.assertEqual([(r['text'], r['count'], r['page_count']) for r in by_text['rows']], [('未知引用', 2, 1), ('另一缺失', 1, 1)])
        ref = next(r for r in query_references(self.db, signal='explicit', path=self.path)['rows'] if r['text'] == '未知引用')
        view = inspect_reference(self.db, ref['id'], missing_definition=True, path=self.log)
        save_annotation(
            self.db, self.log, ref['id'], 'reference', reviewer='fixture', reason='Track missing definition',
            ruling='missing_definition', target=None, expected_content_sha256=view['content_sha256'],
            expected_context_sha256=view['context_sha256'],
        )
        self.rebuild()
        after = query_missing_candidates(self.db)
        self.assertEqual([(r['text'], r['count']) for r in after['rows']], [('另一缺失', 1), ('未知引用', 1)])
        filtered = query_missing_candidates(self.db, path=self.path, signal='explicit', kind='entry')
        self.assertEqual(filtered['total'], 2)

    def _run_cli(self, *args):
        project = Path(__file__).resolve().parents[1]
        return subprocess.run(
            [sys.executable, '-m', 'anna.review.repository',
             '--db', str(self.database), '--annotations', str(self.log), *args],
            cwd=project, text=True, capture_output=True, timeout=30,
        )

    def test_preview_annotate_pending_batch_and_revoke(self):
        clean = preview_annotations(self.db, self.log)
        self.assertEqual(clean['log_state'], 'current')
        self.assertEqual(clean['summary']['references_changed'], 0)
        self.assertEqual(clean['summary']['clickable_links_delta'], 0)
        self.assertEqual((clean['decision_changes'], clean['missing_definition_changes']), ([], []))

        plain_ref = self.plain()
        explicit_ref = next(r for r in query_references(self.db, signal='explicit')['rows'] if r['text'] == '未知引用')
        ev = inspect_reference(self.db, plain_ref['id'], 'target', path=self.log)
        before_bytes = self.log.read_bytes() if self.log.exists() else b''

        dry = preview_annotate(
            self.db, self.log, plain_ref['id'], 'reference', reviewer='fixture', reason='dry-run confirm',
            ruling='confirm', target='target',
            expected_content_sha256=ev['content_sha256'], expected_context_sha256=ev['context_sha256'],
        )
        self.assertEqual(self.log.read_bytes() if self.log.exists() else b'', before_bytes)
        self.assertIsNotNone(dry['hypothetical'])
        self.assertEqual(dry['summary']['references_changed'], 1)
        self.assertEqual(dry['summary']['clickable_links_delta'], 1)
        self.assertEqual(dry['reference_changes'][0]['before']['can_link'], False)
        self.assertEqual(dry['reference_changes'][0]['after']['can_link'], True)
        self.assertEqual(dry['reference_changes'][0]['after']['target_id'], 'target')
        self.assertEqual(dry['decision_changes'][0]['change'], 'added')
        self.assertEqual(dry['decision_changes'][0]['after_status'], 'applied')

        # Invalid basis hash is rejected identically in dry-run without touching log
        with self.assertRaisesRegex(ValueError, 'basis changed'):
            preview_annotate(
                self.db, self.log, plain_ref['id'], 'reference', reviewer='fixture', reason='bad hash',
                ruling='confirm', target='target', expected_content_sha256='stale',
            )
        self.assertEqual(self.log.read_bytes() if self.log.exists() else b'', before_bytes)

        # Save plain confirm + missing_definition without rebuilding yet -> cumulative preview
        confirmed = self.confirm(ref=plain_ref)
        miss_view = inspect_reference(self.db, explicit_ref['id'], missing_definition=True, path=self.log)
        issue = save_annotation(
            self.db, self.log, explicit_ref['id'], 'reference', reviewer='fixture', reason='missing entry',
            ruling='missing_definition', target=None,
            expected_content_sha256=miss_view['content_sha256'], expected_context_sha256=miss_view['context_sha256'],
        )
        batch = preview_annotations(self.db, self.log)
        self.assertEqual(batch['log_state'], 'pending_replay')
        self.assertEqual(batch['summary']['references_changed'], 2)
        self.assertEqual(batch['summary']['clickable_links_delta'], 1)
        self.assertEqual(batch['summary']['decisions_after'], {'applied': 2})
        self.assertEqual(batch['summary']['missing_definitions_after'], {'open': 1})
        self.assertEqual([(m['change'], m['after_status']) for m in batch['missing_definition_changes']], [('added', 'open')])

        # Dry-run superseding the pending missing_definition with confirm before rebuild
        repair_ev = inspect_reference(self.db, explicit_ref['id'], 'target', path=self.log)
        dry_repair = preview_annotate(
            self.db, self.log, explicit_ref['id'], 'reference', reviewer='fixture', reason='repair preview',
            ruling='confirm', target='target',
            expected_content_sha256=repair_ev['content_sha256'], expected_context_sha256=repair_ev['context_sha256'],
            supersedes=issue['id'],
        )
        self.assertEqual(dry_repair['summary']['clickable_links_delta'], 2)
        self.assertEqual([(m['id'], m['after_status'], m['after_target_id']) for m in dry_repair['missing_definition_changes']],
                         [(issue['id'], 'resolved', 'target')])

        # Rebuild to materialize confirmed + missing_definition, verify exact match with batch prediction
        self.rebuild()
        self.assertEqual(query_decisions(self.db, status='applied')['total'], 2)
        self.assertEqual(query_missing_definitions(self.db, status='open')['total'], 1)

        # Supersede missing_definition -> resolved, rebuild, then preview_revoke -> predicts needs_review
        repair = save_annotation(
            self.db, self.log, explicit_ref['id'], 'reference', reviewer='fixture', reason='repair committed',
            ruling='confirm', target='target',
            expected_content_sha256=repair_ev['content_sha256'], expected_context_sha256=repair_ev['context_sha256'],
            supersedes=issue['id'],
        )
        self.rebuild()
        before_revoke_bytes = self.log.read_bytes()
        dry_revoke = preview_revoke(self.db, self.log, repair['id'], 'fixture', 'dry-run withdraw')
        self.assertEqual(self.log.read_bytes(), before_revoke_bytes)
        self.assertEqual(dry_revoke['summary']['clickable_links_delta'], -1)
        self.assertEqual(dry_revoke['missing_definition_changes'][0]['before_status'], 'resolved')
        self.assertEqual(dry_revoke['missing_definition_changes'][0]['after_status'], 'needs_review')

        # Dry-run superseding confirmed plain reference with non_reference
        plain_after = self.plain()
        nr_ev = inspect_reference(self.db, plain_after['id'], path=self.log)
        dry_nr = preview_annotate(
            self.db, self.log, plain_after['id'], 'reference', reviewer='fixture', reason='not a rule ref',
            ruling='non_reference', target=None,
            expected_content_sha256=nr_ev['content_sha256'], expected_context_sha256=nr_ev['context_sha256'],
            supersedes=confirmed['id'],
        )
        self.assertEqual(dry_nr['summary']['clickable_links_delta'], -1)
        self.assertEqual(next(r for r in dry_nr['reference_changes'] if r['id'] == plain_after['id'])['after']['reason'],
                         'reviewed_non_reference')

        # Revoking non-reference (grant/procedure) annotation is rejected in both revoke & preview_revoke
        seed_log = self.root / 'seed.jsonl'
        seed_event = {
            'schema': 1, 'id': 'ann_rev_seed_fixture', 'created_at': '2026-11-16T00:00:00+00:00',
            'operation': 'annotate', 'annotation_id': 'ann_seed', 'kind': 'grant',
            'subject': {'type': 'feat', 'ruleset': '5r', 'page_path': self.path, 'name_zh': '示例', 'name_en': 'Sample'},
            'anchors': [{'id': 'c0', 'kind': 'clause', 'quote': '示例条款'}],
            'statements': {},
            'basis': {'schema': 1, 'content': [], 'content_sha256': digest([]), 'context': {}, 'context_sha256': digest({})},
            'reviewer': 'seed', 'reason': 'seed',
        }
        seed_log.write_text(json.dumps(seed_event) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Only reference annotations'):
            revoke_annotation(seed_log, 'ann_rev_seed_fixture', 'fixture', 'cannot revoke grant')
        with self.assertRaisesRegex(ValueError, 'Only reference annotations'):
            preview_revoke(self.db, seed_log, 'ann_rev_seed_fixture', 'fixture', 'cannot revoke grant')

    def test_candidate_search_normalization_multi_token_and_filters(self):
        self.page.write_text(
            '<h2 id="armor">护甲 Armor</h2><p>护甲条目。</p>'
            '<h2 id="scroll">法术卷轴 Spell Scroll</h2><p>卷轴条目。</p>'
            '<h2 id="empty_h">空条目 Empty Entry</h2>'
            '<p>阅读上下文：<a href="#m1">+2护甲*</a>、<a href="#m2">Spell Scrolls</a>、<a href="#m3">护甲空词</a>。</p>'
        )
        if self.db:
            self.db.close()
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
        h_armor, h_scroll, h_empty = doc.body.find_all('h2')
        sa, ta = doc.span(h_armor)
        ss, ts = doc.span(h_scroll)
        entries = [
            {'id': 'armor_id', 'entity_id': 'armor_id', 'name': '护甲 Armor', 'name_zh': '护甲', 'name_en': 'Armor',
             'type': 'equipment', 'page_path': self.path, 'start': sa, 'title_end': ta, 'end': ss,
             'anchors': ['armor'], 'ruleset': '5r', 'source_ids': [], 'is_primary': True},
            {'id': 'scroll_id', 'entity_id': 'scroll_id', 'name': '法术卷轴 Spell Scroll', 'name_zh': '法术卷轴',
             'name_en': 'Spell Scroll', 'type': 'magic_item', 'page_path': self.path, 'start': ss, 'title_end': ts,
             'end': doc.span(h_empty)[0], 'anchors': ['scroll'], 'ruleset': '5r', 'source_ids': [], 'is_primary': True},
            {'id': 'empty_sec', 'entity_id': 'empty_sec', 'name': '空条目 Empty Entry', 'name_zh': '空条目',
             'name_en': 'Empty Entry', 'type': 'section', 'page_path': self.path, 'start': len(doc.text),
             'title_end': len(doc.text), 'end': len(doc.text), 'anchors': [], 'ruleset': '5r', 'source_ids': [],
             'is_primary': False},
        ]
        page = {'text': doc.text, 'sha256': fingerprint(self.page), 'source_ids': [], 'parse_status': 'ok'}
        self.db.execute('INSERT INTO pages VALUES(?,?,?,?)', (self.path, doc.text, page['sha256'], '[]'))
        self.db.executemany('INSERT INTO entries VALUES(?,?)', [(e['id'], json.dumps(e)) for e in entries])
        doc.close()
        build_concepts(self.db, self.source, self.root, entries, {self.path: page})
        build_references(self.db, self.source, self.root, self.log)
        self.db.execute('INSERT INTO metadata VALUES(?,?)',
                        ('summary', json.dumps({'full_scan': True, 'pipeline_sha256': pipeline_hash(), 'extraction_sha256': extraction_hash()})))
        self.db.commit()

        refs = {r['text']: r for r in query_references(self.db, signal='explicit')['rows']}
        res_armor = search_candidates(self.db, refs['+2护甲*']['id'])
        hit_armor = next(r for r in res_armor['rows'] if r['id'] == 'armor_id')
        self.assertIn('normalized_name', [r['code'] for r in hit_armor['reasons']])

        res_scroll = search_candidates(self.db, refs['Spell Scrolls']['id'])
        hit_scroll = next(r for r in res_scroll['rows'] if r['id'] == 'scroll_id')
        self.assertIn('normalized_name', [r['code'] for r in hit_scroll['reasons']])

        multi = search_candidates(self.db, refs['+2护甲*']['id'], query='example armor')
        self.assertEqual([r['id'] for r in multi['rows']], ['armor_id'])
        no_sec = search_candidates(self.db, refs['+2护甲*']['id'], query='empty', exclude_sections=True)
        self.assertEqual(no_sec['total'], 0)
        loc_only = search_candidates(self.db, refs['+2护甲*']['id'], query='empty', locatable_only=True)
        self.assertEqual(loc_only['total'], 0)

    def test_decision_log_missing_queries_and_cli_gate(self):
        plain_ref = self.plain()
        explicit_ref = next(r for r in query_references(self.db, signal='explicit')['rows'] if r['text'] == '未知引用')

        # Unfiltered missing-candidates carries reasons
        mc = query_missing_candidates(self.db, reason='explicit_locator', text='未知')
        self.assertEqual(mc['total'], 1)
        self.assertEqual(mc['rows'][0]['reasons'], ['explicit_locator'])

        # Clean gate passes (strict & non-strict)
        gate_clean = check_rebuild_gate(self.db, self.log, db_path=self.database, strict=True)
        self.assertTrue(gate_clean['ok'])
        self.assertEqual(gate_clean['issues'], [])
        proc = self._run_cli('gate', '--no-book', '--strict')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)['ok'])

        # Save confirm + missing_definition without rebuilding -> gate fails, log query marks in_db=False
        c1 = self.confirm(ref=plain_ref)
        mv = inspect_reference(self.db, explicit_ref['id'], missing_definition=True, path=self.log)
        m1 = save_annotation(
            self.db, self.log, explicit_ref['id'], 'reference', reviewer='fixture', reason='missing def',
            ruling='missing_definition', target=None,
            expected_content_sha256=mv['content_sha256'], expected_context_sha256=mv['context_sha256'],
        )
        gate_pending = check_rebuild_gate(self.db, self.log, db_path=self.database)
        self.assertFalse(gate_pending['ok'])
        self.assertEqual(gate_pending['database']['pending_events'], 2)
        self.assertEqual(gate_pending['issues'][0]['code'], 'annotations_not_replayed')
        proc_pending = self._run_cli('gate', '--no-book')
        self.assertEqual(proc_pending.returncode, 1)
        self.assertEqual(json.loads(proc_pending.stdout)['issues'][0]['code'], 'annotations_not_replayed')

        log_rows = query_log_annotations(self.log, db=self.db, ruling='missing_definition')
        self.assertEqual(log_rows['total'], 1)
        self.assertFalse(log_rows['rows'][0]['in_db'])

        # Preview & annotations --from-log CLI subcommands work cleanly
        proc_prev = self._run_cli('preview')
        self.assertEqual(proc_prev.returncode, 0, proc_prev.stderr)
        self.assertEqual(json.loads(proc_prev.stdout)['summary']['decisions_after'], {'applied': 2})
        proc_log = self._run_cli('annotations', '--from-log', '--ruling', 'confirm')
        self.assertEqual(proc_log.returncode, 0, proc_log.stderr)
        self.assertEqual(json.loads(proc_log.stdout)['total'], 1)

        # Rebuild, then verify decisions filters, missing --text, and revoke/supersede linkage
        self.rebuild()
        dec = query_decisions(self.db, annotation_id=c1['annotation_id'], path=self.path, ruling='confirm')
        self.assertEqual(dec['total'], 1)
        self.assertEqual(dec['counts'], {'applied': 2})
        miss = query_missing_definitions(self.db, text='未知')
        self.assertEqual(miss['total'], 1)
        self.assertEqual(miss['counts'], {'open': 1})

        # CLI dry-run annotate --preview & revoke --preview keep log untouched
        before_bytes = self.log.read_bytes()
        rep_ev = inspect_reference(self.db, explicit_ref['id'], 'target', path=self.log)
        proc_ap = self._run_cli(
            'annotate', explicit_ref['id'], '--kind', 'reference', '--ruling', 'confirm',
            '--target', 'target', '--expected-content-sha256', rep_ev['content_sha256'],
            '--expected-context-sha256', rep_ev['context_sha256'],
            '--reviewer', 'fixture', '--reason', 'cli preview', '--supersedes', m1['id'], '--preview',
        )
        self.assertEqual(proc_ap.returncode, 0, proc_ap.stderr)
        self.assertEqual(json.loads(proc_ap.stdout)['missing_definition_changes'][0]['after_status'], 'resolved')
        proc_rp = self._run_cli('revoke', c1['id'], '--reviewer', 'fixture', '--reason', 'cli revoke preview', '--preview')
        self.assertEqual(proc_rp.returncode, 0, proc_rp.stderr)
        self.assertEqual(json.loads(proc_rp.stdout)['summary']['clickable_links_delta'], -1)
        self.assertEqual(self.log.read_bytes(), before_bytes)

        # Linked book snapshot mismatch and diverged log detection
        book = self.root / 'book'
        build_book(self.source, self.database, book)
        self.assertTrue(check_rebuild_gate(self.db, self.log, db_path=self.database, book=book)['ok'])
        manifest = json.loads((book / 'anna-manifest.json').read_text(encoding='utf-8'))
        manifest['database_sha256'] = 'stale-db-hash'
        manifest['annotation_snapshot']['sha256'] = 'stale-ann-hash'
        (book / 'anna-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        book_gate = check_rebuild_gate(self.db, self.log, db_path=self.database, book=book)
        self.assertFalse(book_gate['ok'])
        self.assertEqual({i['code'] for i in book_gate['issues']},
                         {'linked_book_db_mismatch', 'linked_book_annotations_mismatch'})

        # Strict gate flags stale decision as error while non-strict treats it as warning
        self.rebuild(competitor=True)
        warn_gate = check_rebuild_gate(self.db, self.log, db_path=self.database, strict=False)
        self.assertTrue(warn_gate['ok'])
        self.assertEqual(warn_gate['issues'][0]['severity'], 'warning')
        strict_gate = check_rebuild_gate(self.db, self.log, db_path=self.database, strict=True)
        self.assertFalse(strict_gate['ok'])
        self.assertEqual(strict_gate['issues'][0]['severity'], 'error')

    def test_ruling_changes_stay_consistent_across_query_layout_book_and_pages(self):
        curation = self.root / 'tour.json'
        curation.write_text(json.dumps({
            'schema': 2,
            'sections': [{
                'key': 'rules',
                'label': {'zh': '规则', 'en': 'Rules'},
                'topics': [{'key': 'ex', 'label': {'zh': '示例', 'en': 'Example'}, 'contents': {'5echm': '阅读上下文'}}],
            }],
            'choices': {'5echm': {}},
        }), encoding='utf-8')

        def export_and_collect():
            book = self.root / 'book'
            pages_out = self.root / 'pages'
            site_root = self.root / 'site'
            for d in (pages_out, site_root):
                if d.exists():
                    shutil.rmtree(d)
            # Add the column-complete schema needed by pages_data projection onto the test SQLite copy
            proj_src = self.root / 'proj_src.sqlite'
            shutil.copyfile(self.database, proj_src)
            with closing(sqlite3.connect(proj_src)) as conn, conn:
                conn.executescript('''
                    CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, identity_status TEXT);
                    CREATE TABLE IF NOT EXISTS entry_sources(entry_id TEXT, source_id TEXT, PRIMARY KEY(entry_id, source_id));
                    CREATE TABLE IF NOT EXISTS facts(id TEXT PRIMARY KEY, entry_id TEXT, field TEXT, value TEXT, evidence TEXT, rule TEXT, scope TEXT, status TEXT);
                    CREATE TABLE IF NOT EXISTS relations(id TEXT PRIMARY KEY, subject_id TEXT, predicate TEXT, object_id TEXT, target_label TEXT, target_locator TEXT, evidence TEXT, status TEXT);
                    CREATE TABLE IF NOT EXISTS identity_candidates(group_id TEXT PRIMARY KEY, type TEXT, status TEXT, data TEXT);
                    CREATE TABLE IF NOT EXISTS book_search(url TEXT PRIMARY KEY, src TEXT NOT NULL, title TEXT NOT NULL, cat TEXT NOT NULL, text TEXT NOT NULL);
                ''')
                conn.execute('DROP TABLE sources')
                conn.execute('CREATE TABLE sources(id TEXT PRIMARY KEY, name TEXT NOT NULL, origin TEXT, ruleset_hint TEXT, release_status TEXT, source_type TEXT, archived INTEGER, coverage TEXT, data TEXT NOT NULL)')
                conn.execute("INSERT OR IGNORE INTO entities SELECT id, 'ok' FROM entries")
                conn.execute('DROP TABLE entries')
                conn.execute('''CREATE TABLE entries(
                    id TEXT PRIMARY KEY, entity_id TEXT, name TEXT, name_zh TEXT, name_en TEXT, type TEXT,
                    page_path TEXT, start INTEGER, end INTEGER, ruleset TEXT, legacy TEXT,
                    classification_status TEXT, fields TEXT, data TEXT NOT NULL
                )''')
                for row in self.db.execute('SELECT id, data FROM entries'):
                    e = json.loads(row[1])
                    e.setdefault('fields', {})
                    e.setdefault('legacy', 'unmarked')
                    e.setdefault('classification_status', '原文明示')
                    e.setdefault('source_names', [])
                    conn.execute(
                        'INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (e['id'], e['entity_id'], e['name'], e['name_zh'], e['name_en'], e['type'],
                         e['page_path'], e['start'], e['end'], e['ruleset'], e['legacy'],
                         e['classification_status'], '{}', json.dumps(e)),
                    )
                pcols = {r[1] for r in conn.execute('PRAGMA table_info(pages)')}
                if 'role' not in pcols:
                    prows = conn.execute('SELECT path, text, sha256, source_ids FROM pages').fetchall()
                    conn.execute('DROP TABLE pages')
                    conn.execute('CREATE TABLE pages(path TEXT PRIMARY KEY, title TEXT, role TEXT, parse_status TEXT, sha256 TEXT, text TEXT, source_ids TEXT, data TEXT)')
                    for p_path, p_text, p_sha, p_sids in prows:
                        conn.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?)', (p_path, 'Example', 'document', 'ok', p_sha, p_text, p_sids, '{}'))
                # Add one non-effective index row that must never leak into layout, book, or pages export
                conn.execute(
                    "INSERT OR REPLACE INTO effective_references VALUES('idx_non_eff',?,0,4,'index','resolved','target','unreviewed',0,'not_resolved',0,?)",
                    (self.path, json.dumps({'id': 'idx_non_eff', 'path': self.path, 'start': 0, 'end': 4, 'signal': 'index', 'status': 'resolved', 'target_id': 'target', 'can_link': False})),
                )
            _, book_report = build_book(self.source, proj_src, book)
            export_pages_data(pages_out, db=proj_src, book=book, source=self.source, curation=curation, gzip_shards=False)
            idx = json.loads((pages_out / 'pages-data' / 'index.json').read_text(encoding='utf-8'))
            for rel, meta in idx.items():
                target_file = site_root / rel
                target_file.parent.mkdir(parents=True, exist_ok=True)
                cnt = (meta['size'] + meta['chunkSize'] - 1) // meta['chunkSize']
                with target_file.open('wb') as dst:
                    for i in range(cnt):
                        dst.write((pages_out / 'pages-data' / rel / f'{i}.bin').read_bytes())
            query_links = sorted(
                (r['start'], r['end'], r['target_id'])
                for r in query_references(self.db, path=self.path)['rows']
                if r['can_link']
            )
            with closing(sqlite3.connect(proj_src)) as pconn:
                ctx_data = json.loads(pconn.execute("SELECT data FROM entries WHERE id='context'").fetchone()[0])
                l_layout = entry_layout(pconn, 'context', self.source)
                s_layout = span_layout(pconn, self.path, ctx_data['title_end'], ctx_data['end'], self.source, owner='context')
                self.assertEqual(s_layout['html'], l_layout['html'])
            book_bindings = json.loads((book / self.path).read_bytes().split(b'id="anna-bindings">')[1].split(b'</script>')[0])
            book_refs = sorted((r['range']['text'], r['id']) for r in book_bindings['references'])
            pages_bindings = json.loads((pages_out / 'book' / self.path).read_bytes().split(b'id="anna-bindings">')[1].split(b'</script>')[0])
            pages_book_refs = sorted((r['range']['text'], r['id']) for r in pages_bindings['references'])
            try:
                site = PublishedSite(site_root)
                st, _, body = site.handle('/api/entry-layout?id=context')
                self.assertEqual(st, 200)
                pages_layout = json.loads(body)
                self.assertEqual(l_layout['html'], pages_layout['html'])
            finally:
                _clear_book_connections()
            self.assertEqual(book_report['linked_references'], len(query_links))
            self.assertEqual(book_refs, pages_book_refs)
            return query_links, book_refs, pages_layout['html'], book, proj_src

        # 1. Baseline: only the explicit <a href="#rule"> links
        q0, b0, h0, _, _ = export_and_collect()
        self.assertEqual(len(q0), 1)
        self.assertEqual(b0, [('共享规则', 'target')])
        self.assertEqual(h0.count('class="ref"'), 1)

        # 2. Confirm plain_ref -> all consumers gain the second link identically
        ev = self.confirm()
        # Un-replayed log must block export_pages_data cleanly before staging output
        with self.assertRaisesRegex(ValueError, 'replayed'):
            export_pages_data(self.root / 'blocked-pages', db=self.root / 'proj_src.sqlite',
                              book=self.root / 'book', source=self.source, curation=curation, gzip_shards=False)
        self.rebuild()
        q1, b1, h1, _, _ = export_and_collect()
        self.assertEqual(len(q1), 2)
        self.assertEqual(b1, [('共享规则', 'target'), ('共享规则', 'target')])
        self.assertEqual(h1.count('class="ref"'), 2)

        # 3. Supersede with non_reference and revoke -> back to single link across all consumers
        plain_after = self.plain()
        nr_ev = inspect_reference(self.db, plain_after['id'], path=self.log)
        nr = save_annotation(
            self.db, self.log, plain_after['id'], 'reference', reviewer='fixture', reason='ordinary mention',
            ruling='non_reference', target=None,
            expected_content_sha256=nr_ev['content_sha256'], expected_context_sha256=nr_ev['context_sha256'],
            supersedes=ev['id'],
        )
        self.rebuild()
        q2, b2, h2, _, _ = export_and_collect()
        self.assertEqual((len(q2), b2, h2.count('class="ref"')), (1, [('共享规则', 'target')], 1))
        revoke_annotation(self.log, nr['id'], 'fixture', 'undo')
        self.rebuild()
        q3, b3, h3, _, _ = export_and_collect()
        self.assertEqual((len(q3), b3, h3.count('class="ref"')), (1, [('共享规则', 'target')], 1))


if __name__ == '__main__':
    unittest.main()
