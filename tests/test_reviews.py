"""Review lifecycle contract; isolated synthetic sources, never the real review log."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from concepts import build_concepts
from html_extract import Document
from linked_book import build_book, fingerprint, MARKER
from references import build_references, query_references, query_decisions
from review import inspect, save, revoke
from review_store import append_event, load_events, locked, pipeline_hash, require_current


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        (self.source/'topics').mkdir(parents=True)
        self.path = 'topics/example.htm'
        self.page = self.source/self.path
        self.page.write_text('<h2 id="rule">共享规则 Shared Rule</h2><p>此处定义。</p>'
                             '<h2>阅读上下文</h2><p>普通提及共享规则。</p>'
                             '<p><a href="#rule">共享规则</a></p><p><a href="#missing">未知引用</a></p>')
        self.log = self.root/'decisions.jsonl'
        self.database = self.root/'catalog.sqlite'
        self.db = None
        self.rebuild()

    def tearDown(self):
        if self.db: self.db.close()
        self.temp.cleanup()

    def rebuild(self, competitor=False):
        if self.db: self.db.close()
        if self.database.exists(): self.database.unlink()
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
        start, title_end = doc.span(headings[0]); context_start, context_title = doc.span(headings[1])
        target = dict(id='target',entity_id='target',name='共享规则 Shared Rule',name_zh='共享规则',name_en='Shared Rule',
                      type='rule',page_path=self.path,start=start,title_end=title_end,end=context_start,
                      anchors=['rule'],ruleset='5r',source_ids=[],is_primary=True)
        context = dict(id='context',entity_id='context',name='阅读上下文',name_zh='阅读上下文',name_en='',
                       type='section',page_path=self.path,start=context_start,title_end=context_title,end=len(doc.text),
                       anchors=[],ruleset='5r',source_ids=[],is_primary=False)
        entries = [target,context]
        if competitor: entries.append({**target,'id':'competitor','entity_id':'competitor','anchors':[]})
        page = {'text':doc.text,'sha256':fingerprint(self.page),'source_ids':[],'parse_status':'ok'}
        self.db.execute('INSERT INTO pages VALUES(?,?,?,?)',(self.path,doc.text,page['sha256'],'[]'))
        self.db.executemany('INSERT INTO entries VALUES(?,?)',[(e['id'],json.dumps(e)) for e in entries])
        doc.close()
        build_concepts(self.db,self.source,self.root,entries,{self.path:page})
        build_references(self.db,self.source,self.root,self.log)
        self.db.execute('INSERT INTO metadata VALUES(?,?)',('summary',json.dumps({'full_scan':True,'pipeline_sha256':pipeline_hash()})))
        self.db.commit()

    def plain(self):
        return query_references(self.db,signal='plain')['rows'][0]

    def confirm(self, ref=None, target='target', supersedes=None):
        ref = ref or self.plain()
        evidence = inspect(self.db,ref['id'],target)
        return save(self.db,self.log,operation='confirm',reference_id=ref['id'],target=target,
                    expected_basis=evidence['basis_sha256'],reviewer='fixture',reason='synthetic reference evidence',supersedes=supersedes)

    def test_save_replay_book_and_revoke(self):
        original = self.plain(); event = self.confirm(original)
        with self.assertRaisesRegex(ValueError,'重放'): require_current(self.db,self.log)
        self.rebuild()
        ref = query_references(self.db,reference_id=original['id'])['rows'][0]
        self.assertEqual((ref['target_id'],ref['review_status'],ref['can_link']),('target','applied',True))
        raw = self.db.execute('SELECT status,target_id FROM concept_observations WHERE id=?',(original['id'],)).fetchone()
        self.assertEqual(raw,('review',None))
        book = self.root/'book'; build_book(self.source,self.database,book)
        generated = (book/self.path).read_bytes()
        self.assertEqual(generated.split(MARKER)[0],self.page.read_bytes())
        payload = json.loads(generated.split(b'id="anna-bindings">')[1].split(b'</script>')[0])
        self.assertEqual(next(r['id'] for r in payload['references'] if r['reference_id']==ref['id']),ref['target_id'])
        self.rebuild(); self.assertEqual(self.plain()['review_status'],'applied')
        revoke(self.log,event['id'],'fixture','undo synthetic approval')
        self.rebuild()
        ref = self.plain()
        self.assertEqual((ref['status'],ref['review_status'],ref['can_link']),('review','revoked',False))
        self.assertEqual(len(load_events(self.log)[0]),2)

    def test_page_or_candidate_change_requires_review(self):
        event = self.confirm(); self.rebuild(competitor=True)
        self.assertEqual(self.plain()['review_status'],'stale')
        self.assertFalse(self.plain()['can_link'])
        self.assertEqual(query_decisions(self.db,status='stale')['rows'][0]['event']['id'],event['id'])
        self.rebuild()
        self.page.write_text(self.page.read_text()+'<p>变更的依据。</p>')
        self.rebuild()
        self.assertEqual(self.plain()['review_status'],'stale')
        self.assertIsNone(self.plain()['target_id'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM review_queue WHERE reason='review_decision_stale'").fetchone()[0],1)

    def test_shifted_range_is_not_silently_migrated(self):
        self.confirm(); self.page.write_text('<p>前插内容。</p>'+self.page.read_text()); self.rebuild()
        self.assertEqual(self.plain()['review_status'],'stale')
        self.assertFalse(self.plain()['can_link'])

    def test_conflict_does_not_choose_last_line(self):
        event = self.confirm()
        with locked(self.log):
            duplicate = {k:v for k,v in event.items() if k not in {'schema','id','created_at'}}
            append_event(self.log,duplicate,load_events(self.log)[1])
        self.rebuild()
        self.assertEqual(self.plain()['review_status'],'conflict')
        self.assertIsNone(self.plain()['target_id'])
        self.assertEqual(query_decisions(self.db,status='conflict')['total'],2)

    def test_non_reference_supersession_and_no_resurrection(self):
        event = self.confirm(); self.rebuild()
        ref = self.plain(); evidence = inspect(self.db,ref['id'])
        replacement = save(self.db,self.log,operation='non_reference',reference_id=ref['id'],target=None,
                           expected_basis=evidence['basis_sha256'],reviewer='fixture',reason='ordinary prose',supersedes=event['id'])
        self.rebuild(); self.assertEqual(self.plain()['reason'],'reviewed_non_reference')
        revoke(self.log,replacement['id'],'fixture','undo'); self.rebuild()
        self.assertEqual(self.plain()['status'],'review')
        self.assertEqual(query_decisions(self.db,status='superseded')['total'],1)

    def test_stale_save_and_pending_log_are_rejected(self):
        ref = self.plain()
        with self.assertRaisesRegex(ValueError,'依据已变化'):
            save(self.db,self.log,operation='confirm',reference_id=ref['id'],target='target',expected_basis='old',reviewer='fixture',reason='reason')
        self.confirm()
        with self.assertRaisesRegex(ValueError,'重放'): self.confirm()
        with self.assertRaisesRegex(ValueError,'重放'): build_book(self.source,self.database,self.root/'book')

    def test_removed_reference_remains_in_review_history(self):
        event = self.confirm()
        self.page.write_text('<h2 id="rule">共享规则 Shared Rule</h2><p>此处定义。</p><h2>阅读上下文</h2>')
        self.rebuild()
        rows = query_decisions(self.db,status='missing')['rows']
        self.assertEqual([r['event']['id'] for r in rows],[event['id']])

    def test_empty_original_links_have_distinct_ids_and_no_click_layer(self):
        self.page.write_text(self.page.read_text()+'<a href="#rule"></a><a href="#rule"></a>')
        self.rebuild()
        refs = query_references(self.db,signal='explicit')['rows']
        self.assertEqual(len(refs),len({r['id'] for r in refs}))
        empty = [r for r in refs if r['start']==r['end']]
        self.assertEqual(len(empty),2)
        self.assertTrue(all(not r['can_link'] and r['presentation']=='empty_range' for r in empty))

    def test_definition_titles_and_unlocatable_targets_cannot_be_approved(self):
        self.page.write_text(self.page.read_text().replace('阅读上下文','<a href="#rule">阅读上下文</a>'))
        self.rebuild()
        title = next(r for r in query_references(self.db,signal='explicit')['rows'] if r['presentation']=='definition_title')
        with self.assertRaisesRegex(ValueError,'定义入口'): self.confirm(title)
        with self.assertRaisesRegex(ValueError,'目标条目不存在'): inspect(self.db,self.plain()['id'],'missing')

    def test_invalid_log_is_rejected_without_rewriting_it(self):
        self.log.write_text('{"schema":99}\n')
        before = self.log.read_bytes()
        with self.assertRaisesRegex(ValueError,'审核日志'): load_events(self.log)
        self.assertEqual(self.log.read_bytes(),before)


if __name__ == '__main__': unittest.main()
