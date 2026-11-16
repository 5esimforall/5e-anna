"""Tests for content- and context-versioned structured rule annotations."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from anna.knowledge.annotations import inspect_clauses
from anna.knowledge.context import Context
from anna.knowledge.repository import build_knowledge, save_annotation, revoke_annotation
from anna.paths import PROJECT
from anna.review.store import (
    load_annotations,
    require_annotations_appendable,
    require_current_annotations,
    extraction_hash,
)
from anna.serialization import canonical


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ann = self.root / 'annotations.jsonl'
        self.db_path = self.root / 'catalog.sqlite'
        self.db = None

    def tearDown(self):
        if self.db:
            self.db.close()
        self.tmp.cleanup()

    def build_db(self, entries_spec, extra_tables=()):
        if self.db:
            self.db.close()
        self.db_path.unlink(missing_ok=True)
        self.db = sqlite3.connect(self.db_path)
        self.db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE entries(id TEXT PRIMARY KEY,name TEXT,type TEXT,ruleset TEXT,page_path TEXT,start INTEGER,end INTEGER,data TEXT);
            CREATE TABLE pages(path TEXT PRIMARY KEY,text TEXT);
            CREATE TABLE sources(id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE raw_tables(id TEXT PRIMARY KEY,page_path TEXT REFERENCES pages(path),start INTEGER,end INTEGER,data TEXT);
            CREATE TABLE relations(id TEXT PRIMARY KEY,subject_id TEXT REFERENCES entries(id),predicate TEXT,object_id TEXT REFERENCES entries(id),target_label TEXT,target_locator TEXT,evidence TEXT,status TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE review_queue(id TEXT PRIMARY KEY,target TEXT,reason TEXT,priority TEXT,data TEXT);
        ''')
        self.db.execute(
            'INSERT INTO sources VALUES(?,?)',
            ('phb', canonical({'id': 'phb', 'name': '核心', 'ruleset_hint': '5r', 'reference_role': 'core_rules', 'evidence': []})),
        )
        pages = {}
        for spec in entries_spec:
            pages[spec['page_path']] = pages.get(spec['page_path'], '') + spec['page_text']
        for path, text in pages.items():
            self.db.execute('INSERT INTO pages VALUES(?,?)', (path, text))
        for spec in entries_spec:
            entry = {
                'id': spec['id'],
                'entity_id': spec['id'],
                'name': spec['name_zh'] + (' ' + spec['name_en'] if spec.get('name_en') else ''),
                'name_zh': spec['name_zh'],
                'name_en': spec.get('name_en', ''),
                'type': spec['type'],
                'ruleset': spec.get('ruleset', '5r'),
                'page_path': spec['page_path'],
                'start': spec['start'],
                'title_end': spec['title_end'],
                'end': spec['end'],
                'source_ids': spec.get('source_ids', ['phb']),
                'fields': spec.get('fields', {}),
                'anchors': [],
                'is_primary': True,
                **({'table_span': spec['table_span']} if 'table_span' in spec else {}),
            }
            self.db.execute(
                'INSERT INTO entries VALUES(?,?,?,?,?,?,?,?)',
                (entry['id'], entry['name'], entry['type'], entry['ruleset'], entry['page_path'], entry['start'], entry['end'], canonical(entry)),
            )
        for tbl in extra_tables:
            self.db.execute(
                'INSERT INTO raw_tables VALUES(?,?,?,?,?)',
                (tbl['id'], tbl['page_path'], tbl['start'], tbl['end'], canonical(tbl)),
            )
        self.coverage = build_knowledge(self.db, self.root, annotations_path=self.ann)
        self.db.execute('INSERT INTO metadata VALUES(?,?)', ('summary', canonical({'extraction_sha256': extraction_hash()})))
        self.db.commit()

    def base_specs(self, *, prefix='', extra_feat_tail='', spell_body=None, feat_page='feat.htm', feat_id='ver_feat_1', spell_id='ver_spell_1', second_spell=False):
        feat_head = '示例专长 Sample Feat\n'
        feat_c1 = '你习得迷踪步，并可每日无消耗施展一次。'
        feat_tail = '\n' + extra_feat_tail if extra_feat_tail else ''
        feat_text = prefix + feat_head + feat_c1 + feat_tail
        f_start = len(prefix)

        s_body = spell_body or '你传送至30尺内空地。'
        s_text = '迷踪步 Misty Step\n施法时间：附赠动作\n施法距离：自身\n法术成分：V\n持续时间：立即\n' + s_body
        specs = [
            {
                'id': feat_id, 'name_zh': '示例专长', 'name_en': 'Sample Feat', 'type': 'feat',
                'page_path': feat_page, 'page_text': feat_text,
                'start': f_start, 'title_end': f_start + len(feat_head), 'end': len(feat_text),
            },
            {
                'id': spell_id, 'name_zh': '迷踪步', 'name_en': 'Misty Step', 'type': 'spell',
                'page_path': 'spell.htm', 'page_text': s_text,
                'start': 0, 'title_end': len('迷踪步 Misty Step\n'), 'end': len(s_text),
            },
        ]
        if second_spell:
            dup_text = '迷踪步 Misty Step\n施法时间：动作\n你换个方式传送。'
            specs.append({
                'id': 'ver_spell_dup', 'name_zh': '迷踪步', 'name_en': 'Misty Step', 'type': 'spell',
                'page_path': 'spell2.htm', 'page_text': dup_text,
                'start': 0, 'title_end': len('迷踪步 Misty Step\n'), 'end': len(dup_text),
            })
        return specs

    def grant_payload(self):
        anchors = [{'id': 'a1', 'kind': 'clause', 'quote': '你习得迷踪步，并可每日无消耗施展一次。'}]
        statements = {
            'grants': [{
                'id': 'grant_1',
                'entry_type': 'spell',
                'mode': 'prepared',
                'target': {'kind': 'entry', 'name': '迷踪步', 'evidence_ref': {'anchor': 'a1', 'quote': '迷踪步'}},
                'constraints': {},
                'branches': [{
                    'id': 'branch_1',
                    'resource': {'kind': 'spell_slot', 'consumed': False},
                    'uses': {'per': 'grant', 'quantity': {'kind': 'constant', 'value': 1}},
                    'recovery': {'event': 'long_rest', 'amount': 'all'},
                    'evidence_refs': ['a1'],
                }],
                'evidence_refs': ['a1'],
            }],
        }
        return anchors, statements

    def test_authoring_transferability_across_offsets_ids_pages_siblings_and_targets(self):
        self.build_db(self.base_specs(extra_feat_tail='这是一句尚未标注的补充说明。'))
        clauses_info = inspect_clauses(Context(self.db), 'ver_feat_1')
        self.assertEqual(len(clauses_info['clauses']), 2)

        anchors, statements = self.grant_payload()
        ev = save_annotation(self.db, self.ann, 'ver_feat_1', 'grant', anchors, statements, 'author', 'initial grant')
        self.assertIn('ann_rev_', ev['id'])
        with self.assertRaisesRegex(ValueError, 'not been replayed'):
            require_current_annotations(self.db, self.ann)

        # Rebuild replays cleanly; unannotated sibling yields partial with locally projected casting_options.
        self.build_db(self.base_specs(extra_feat_tail='这是一句尚未标注的补充说明。'))
        require_current_annotations(self.db, self.ann)
        require_annotations_appendable(self.db, self.ann)
        row = self.db.execute("SELECT status, json_extract(data, '$.subject_match') FROM knowledge_annotations WHERE id=?", (ev['id'],)).fetchone()
        self.assertEqual(row, ('applied', 'exact'))
        self.assertEqual(self.db.execute("SELECT count(*) FROM casting_options WHERE root_id='ver_feat_1'").fetchone()[0], 1)

        # 1) Byte offset shift + ver_ ID churn + unannotated trailing sibling edit + target spell body edit -> still exact & applied!
        self.build_db(self.base_specs(
            prefix='前导文本占位。' * 5,
            extra_feat_tail='未标注的尾部补充条款已被改写。',
            spell_body='你伴随银色星光传送至60尺内。',
            feat_id='ver_feat_new_id',
            spell_id='ver_spell_new_id',
        ))
        status, match, subj = self.db.execute(
            "SELECT status, json_extract(data, '$.subject_match'), subject_id FROM knowledge_annotations WHERE id=?",
            (ev['id'],),
        ).fetchone()
        self.assertEqual((status, match, subj), ('applied', 'exact', 'ver_feat_new_id'))
        self.assertEqual(self.db.execute("SELECT count(*) FROM casting_options WHERE root_id='ver_feat_new_id'").fetchone()[0], 1)

        # 2) Unique page relocation -> subject_match == 'transferred', status still 'applied'!
        self.build_db(self.base_specs(feat_page='moved/feats.htm', feat_id='ver_feat_moved', extra_feat_tail='尚未标注的说明。'))
        status, match, subj = self.db.execute(
            "SELECT status, json_extract(data, '$.subject_match'), subject_id FROM knowledge_annotations WHERE id=?",
            (ev['id'],),
        ).fetchone()
        self.assertEqual((status, match, subj), ('applied', 'transferred', 'ver_feat_moved'))

    def test_staleness_triggers_for_clause_header_topology_and_target_ambiguity(self):
        self.build_db(self.base_specs())
        anchors, statements = self.grant_payload()
        ev = save_annotation(self.db, self.ann, 'ver_feat_1', 'grant', anchors, statements, 'author', 'seed')

        # Annotated clause text edited -> stale (content mismatch)
        specs = self.base_specs()
        specs[0]['page_text'] = specs[0]['page_text'].replace('每日无消耗施展一次', '每短休无消耗施展一次')
        specs[0]['end'] = len(specs[0]['page_text'])
        self.build_db(specs)
        self.assertEqual(self.db.execute('SELECT status FROM knowledge_annotations WHERE id=?', (ev['id'],)).fetchone()[0], 'stale')
        self.assertEqual(self.db.execute("SELECT count(*) FROM review_queue WHERE reason='knowledge_annotation_stale'").fetchone()[0], 1)

        # Header prerequisite edited -> stale (context mismatch)
        specs = self.base_specs()
        specs[0]['page_text'] = '示例专长 Sample Feat\n先决条件：等级4+\n你习得迷踪步，并可每日无消耗施展一次。'
        specs[0]['end'] = len(specs[0]['page_text'])
        self.build_db(specs)
        self.assertEqual(self.db.execute('SELECT status FROM knowledge_annotations WHERE id=?', (ev['id'],)).fetchone()[0], 'stale')

        # Duplicate target spell -> target lookup becomes ambiguous -> stale (context mismatch)
        self.build_db(self.base_specs(second_spell=True))
        self.assertEqual(self.db.execute('SELECT status FROM knowledge_annotations WHERE id=?', (ev['id'],)).fetchone()[0], 'stale')

        # Multi-anchor topology: intervening clause inserted OR paragraph split -> stale!
        base_multi = '测试法术 Test Spell\n施法时间：动作\n第一句造成伤害。第二句陷入倒地。'
        specs2 = [{
            'id': 'ver_sp', 'name_zh': '测试法术', 'name_en': 'Test Spell', 'type': 'spell',
            'page_path': 'sp.htm', 'page_text': base_multi,
            'start': 0, 'title_end': len('测试法术 Test Spell\n'), 'end': len(base_multi),
        }]
        self.ann.write_text('')
        self.build_db(specs2)
        ev2 = save_annotation(
            self.db, self.ann, 'ver_sp', 'procedure',
            [{'id': 'a1', 'kind': 'clause', 'quote': '第一句造成伤害。'}, {'id': 'a2', 'kind': 'clause', 'quote': '第二句陷入倒地。'}],
            {'effects': [
                {'kind': 'damage', 'damage_type': 'fire', 'quantity': {'kind': 'dice', 'count': 1, 'sides': 6}, 'evidence_ref': 'a1'},
                {'kind': 'condition', 'condition': '倒地', 'evidence_ref': 'a2'},
            ]},
            'author', 'two clauses',
        )
        # Insert intervening clause between a1 and a2
        specs2[0]['page_text'] = '测试法术 Test Spell\n施法时间：动作\n第一句造成伤害。插入一条规则。第二句陷入倒地。'
        specs2[0]['end'] = len(specs2[0]['page_text'])
        self.build_db(specs2)
        self.assertEqual(self.db.execute('SELECT status FROM knowledge_annotations WHERE id=?', (ev2['id'],)).fetchone()[0], 'stale')
        # Split paragraph between a1 and a2 without inserting a clause
        specs2[0]['page_text'] = '测试法术 Test Spell\n施法时间：动作\n第一句造成伤害。\n第二句陷入倒地。'
        specs2[0]['end'] = len(specs2[0]['page_text'])
        self.build_db(specs2)
        self.assertEqual(self.db.execute('SELECT status FROM knowledge_annotations WHERE id=?', (ev2['id'],)).fetchone()[0], 'stale')

    def test_duplicate_quote_ambiguity_overlap_conflict_and_lifecycle(self):
        # Duplicate quote -> ambiguous
        specs = self.base_specs(extra_feat_tail='你习得迷踪步，并可每日无消耗施展一次。')
        self.build_db(self.base_specs())
        anchors, statements = self.grant_payload()
        ev = save_annotation(self.db, self.ann, 'ver_feat_1', 'grant', anchors, statements, 'author', 'grant')
        self.build_db(specs)
        self.assertEqual(self.db.execute('SELECT status FROM knowledge_annotations WHERE id=?', (ev['id'],)).fetchone()[0], 'ambiguous')
        self.assertEqual(self.db.execute("SELECT count(*) FROM review_queue WHERE reason='knowledge_annotation_ambiguous'").fetchone()[0], 1)

        # Supersede and revoke lifecycle
        self.ann.write_text('')
        self.build_db(self.base_specs())
        ev1 = save_annotation(self.db, self.ann, 'ver_feat_1', 'grant', anchors, statements, 'author', 'v1')
        self.build_db(self.base_specs())
        with self.assertRaisesRegex(ValueError, 'Supersede'):
            save_annotation(self.db, self.ann, 'ver_feat_1', 'grant', anchors, statements, 'author', 'dup', annotation_id=ev1['annotation_id'])
        ev2 = save_annotation(
            self.db, self.ann, 'ver_feat_1', 'grant', anchors, statements, 'author', 'v2',
            annotation_id=ev1['annotation_id'], supersedes=ev1['id'],
        )
        # Overlapping different annotation_id on same entry+kind -> both conflict!
        ev_other = save_annotation(
            self.db, self.ann, 'ver_feat_1', 'grant',
            [{'id': 'b1', 'kind': 'clause', 'quote': '你习得迷踪步，并可每日无消耗施展一次。'}],
            {'descriptions': ['b1']},
            'author', 'competing overlap', annotation_id='ann_competing',
        )
        self.build_db(self.base_specs())
        statuses = dict(self.db.execute('SELECT id, status FROM knowledge_annotations').fetchall())
        self.assertEqual(statuses[ev1['id']], 'superseded')
        self.assertEqual(statuses[ev2['id']], 'conflict')
        self.assertEqual(statuses[ev_other['id']], 'conflict')

        # Revoke competing annotation -> ev2 becomes applied cleanly!
        revoke_annotation(self.ann, ev_other['id'], 'author', 'remove overlap')
        self.build_db(self.base_specs())
        self.assertEqual(self.db.execute('SELECT status FROM knowledge_annotations WHERE id=?', (ev2['id'],)).fetchone()[0], 'applied')

    def test_table_grants_scope_ignores_unrelated_clauses_and_cli_round_trip(self):
        prefix = '黑暗视觉Darkvision。\n你拥有60尺黑暗视觉。\n'
        trait = '特殊血系表格中选择其一。\n'
        cap_title = '血系表\n'
        row0_k = '血系'
        row0_v = '法术'
        row1_k = '高阶'
        row1_v = '迷踪步'
        suffix = '\n出神Trance。\n你仅需4小时完成长休。'
        text = prefix + trait + cap_title + row0_k + row0_v + row1_k + row1_v + suffix
        cap_start = len(prefix) + len(trait)
        t_start = cap_start + len(cap_title)
        c_pos = t_start
        cells = []
        for s in (row0_k, row0_v, row1_k, row1_v):
            cells.append((c_pos, c_pos + len(s), s))
            c_pos += len(s)
        t_end = c_pos
        table = {
            'id': 'tbl_1', 'page_path': 'race.htm', 'start': t_start, 'end': t_end,
            'headers': [row0_k, row0_v],
            'rows': [
                {'cells': [row0_k, row0_v], 'cell_spans': [list(cells[0][:2]), list(cells[1][:2])], 'span': [cells[0][0], cells[1][1]], 'rowspan': ['1', '1'], 'colspan': ['1', '1']},
                {'cells': [row1_k, row1_v], 'cell_spans': [list(cells[2][:2]), list(cells[3][:2])], 'span': [cells[2][0], cells[3][1]], 'rowspan': ['1', '1'], 'colspan': ['1', '1']},
            ],
        }
        spell_text = '迷踪步 Misty Step\n施法时间：附赠动作\n你传送。'
        specs = [
            {'id': 'ver_race', 'name_zh': '精灵', 'name_en': 'Elf', 'type': 'species', 'page_path': 'race.htm', 'page_text': text, 'start': 0, 'title_end': 0, 'end': len(text)},
            {'id': 'ver_cap', 'name_zh': '血系表', 'name_en': '', 'type': 'section', 'page_path': 'race.htm', 'page_text': '', 'start': cap_start, 'title_end': t_start, 'end': t_end, 'table_span': [t_start, t_end]},
            {'id': 'ver_ms', 'name_zh': '迷踪步', 'name_en': 'Misty Step', 'type': 'spell', 'page_path': 'sp.htm', 'page_text': spell_text, 'start': 0, 'title_end': len('迷踪步 Misty Step\n'), 'end': len(spell_text)},
        ]
        self.build_db(specs, extra_tables=(table,))

        # CLI `clauses` and `annotate` and `annotations` and `revoke`
        payload_file = self.root / 'payload.json'
        payload_file.write_text(json.dumps({
            'anchors': [
                {'id': 'c_policy', 'kind': 'clause', 'quote': trait.strip()},
                {'id': 't1', 'kind': 'table', 'caption_zh': '血系表'},
            ],
            'scope': {'kind': 'table_grants', 'start_anchor': 'c_policy', 'table_anchor': 't1'},
            'statements': {
                'grants': [{
                    'id': 'grant_1', 'entry_type': 'spell', 'mode': 'prepared',
                    'target': {'kind': 'entry', 'name': '迷踪步', 'evidence_ref': {'table': 't1', 'row': 1, 'col': 1}},
                    'constraints': {},
                    'branches': [{'id': 'branch_1', 'resource': {'kind': 'spell_slot', 'consumed': False}, 'evidence_refs': ['c_policy']}],
                    'evidence_refs': [{'table': 't1', 'row': 1, 'col': 1}, 'c_policy'],
                }],
            },
        }, ensure_ascii=False))
        res_clauses = subprocess.run(
            [sys.executable, '-m', 'anna.knowledge.repository', '--db', str(self.db_path), 'clauses', 'ver_race'],
            cwd=PROJECT, capture_output=True, text=True, check=True,
        )
        self.assertGreaterEqual(len(json.loads(res_clauses.stdout)['clauses']), 3)
        res_ann = subprocess.run(
            [sys.executable, '-m', 'anna.knowledge.repository', '--db', str(self.db_path), '--annotations', str(self.ann),
             'annotate', 'ver_race', '--kind', 'grant', '--file', str(payload_file), '--reviewer', 'cli', '--reason', 'table grant'],
            cwd=PROJECT, capture_output=True, text=True, check=True,
        )
        rev_id = json.loads(res_ann.stdout)['id']

        self.build_db(specs, extra_tables=(table,))
        rec = json.loads(self.db.execute("SELECT data FROM effective_knowledge WHERE subject_id='ver_race'").fetchone()[0])
        self.assertEqual(rec['status'], 'resolved')
        self.assertEqual(rec['value']['unparsed'], [])
        self.assertEqual(rec['value']['scope']['kind'], 'table_grants')
        self.assertEqual(rec['value']['scope']['table_id'], 'tbl_1')

        # Editing unrelated lore OUTSIDE table_grants scope does NOT stale the annotation!
        specs[0]['page_text'] = text.replace('你拥有60尺黑暗视觉', '你拥有120尺黑暗视觉').replace('4小时完成长休', '2小时完成冥想')
        specs[0]['end'] = len(specs[0]['page_text'])
        self.build_db(specs, extra_tables=(table,))
        self.assertEqual(self.db.execute('SELECT status FROM knowledge_annotations WHERE id=?', (rev_id,)).fetchone()[0], 'applied')

        # CLI `annotations` & `revoke`
        res_list = subprocess.run(
            [sys.executable, '-m', 'anna.knowledge.repository', '--db', str(self.db_path), 'annotations', '--status', 'applied'],
            cwd=PROJECT, capture_output=True, text=True, check=True,
        )
        self.assertEqual(len(json.loads(res_list.stdout)), 1)
        subprocess.run(
            [sys.executable, '-m', 'anna.knowledge.repository', '--db', str(self.db_path), '--annotations', str(self.ann),
             'revoke', rev_id, '--reviewer', 'cli', '--reason', 'undo'],
            cwd=PROJECT, capture_output=True, text=True, check=True,
        )
        events, _ = load_annotations(self.ann)
        self.assertEqual([e['operation'] for e in events], ['annotate', 'revoke'])


if __name__ == '__main__':
    unittest.main()
