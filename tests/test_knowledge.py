"""Knowledge composition, review replay, structural extraction, and clause-level projections."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from anna.knowledge.annotations import build_annotation_event
from anna.knowledge.context import Context
from anna.knowledge.extract import extract_definition_roles, extract_feat_grant, extract_subclass_features
from anna.knowledge.inspection import inspect
from anna.knowledge.repository import build_knowledge, branches, save, validate_record
from anna.presentation.knowledge import casting_condition, chain
from anna.review.commands import revoke
from anna.review.store import append_event, extraction_hash, load_events, locked
from anna.serialization import canonical


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = self.root / 'reviews.jsonl'
        self.annotations = self.root / 'annotations.jsonl'
        self.db = None
        self.feature = (
            '示例 Example\n'
            '你习得测试法术。\n'
            '你可以在不消耗法术位的情况下施展这些法术各一次。\n'
            '当你以此法施展某个法术后，你不能再次以此法施展该法术，直至你完成一次长休。\n'
            '你也可以消耗合适的法术位来施展这些法术。'
        )
        self.spell = (
            '测试法术 Test Spell\n施法时间：附赠动作\n施法距离：自身\n法术成分：V\n持续时间：立即\n'
            '你短暂地被银白的雾气所笼罩，传送到至多30尺内一个你能看见且未占据空间。'
        )
        self.other = '无关文字'
        self.competitor = False
        self.rules = []
        self.feat_annotation = self._default_feat_annotation
        self.spell_annotation = None
        self.rebuild()

    def tearDown(self):
        if self.db:
            self.db.close()
        self.tmp.cleanup()

    def _default_feat_annotation(self):
        anchors = [
            {'id': 'a1', 'kind': 'clause', 'quote': '你习得测试法术。'},
            {'id': 'a2', 'kind': 'clause', 'quote': '你可以在不消耗法术位的情况下施展这些法术各一次。'},
            {'id': 'a3', 'kind': 'clause', 'quote': '当你以此法施展某个法术后，你不能再次以此法施展该法术，直至你完成一次长休。'},
            {'id': 'a4', 'kind': 'clause', 'quote': '你也可以消耗合适的法术位来施展这些法术。'},
        ]
        statements = {
            'grants': [{
                'id': 'grant_1',
                'entry_type': 'spell',
                'mode': 'known',
                'target': {'kind': 'entry', 'name': '测试法术', 'evidence_ref': {'anchor': 'a1', 'quote': '测试法术'}},
                'constraints': {},
                'branches': [
                    {
                        'id': 'branch_1',
                        'resource': {'kind': 'spell_slot', 'consumed': False},
                        'uses': {'per': 'spell', 'quantity': {'kind': 'constant', 'value': 1}},
                        'recovery': {'event': 'long_rest', 'amount': 'all'},
                        'evidence_refs': ['a2', 'a3'],
                    },
                    {
                        'id': 'branch_2',
                        'resource': {'kind': 'spell_slot', 'consumed': True, 'count': 1},
                        'evidence_refs': ['a4'],
                    },
                ],
                'evidence_refs': ['a1', 'a2', 'a3', 'a4'],
            }],
        }
        return ('grant', anchors, statements)

    def _init_schema_and_entries(self):
        if self.db:
            self.db.close()
        self.db = sqlite3.connect(':memory:')
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
        source = {'id': 'phb', 'name': '核心', 'ruleset_hint': '5r', 'reference_role': 'core_rules', 'evidence': []}
        self.db.execute('INSERT INTO sources VALUES(?,?)', ('phb', canonical(source)))
        entries = []
        for eid, body, name, english, kind in [
            ('feat', self.feature, '示例', 'Example', getattr(self, 'feature_type', 'feat')),
            ('spell', self.spell, '测试法术', 'Test Spell', 'spell'),
            ('other', self.other, '无关', 'Other', 'section'),
            *self.rules,
        ]:
            entry = {
                'id': eid, 'entity_id': eid, 'name': name + ' ' + english, 'name_zh': name, 'name_en': english,
                'type': kind, 'ruleset': '5r', 'page_path': eid + '.htm',
                'start': 0, 'title_end': body.find('\n') + 1, 'end': len(body),
                'source_ids': ['phb'], 'fields': {}, 'anchors': [], 'is_primary': True,
            }
            entries.append(entry)
            self.db.execute('INSERT INTO pages VALUES(?,?)', (entry['page_path'], body))
        if self.competitor:
            entries.append({**entries[1], 'id': 'competitor', 'entity_id': 'competitor'})
        for e in entries:
            self.db.execute(
                'INSERT INTO entries VALUES(?,?,?,?,?,?,?,?)',
                (e['id'], e['name'], e['type'], e['ruleset'], e['page_path'], e['start'], e['end'], canonical(e)),
            )

    def _seed_annotations(self):
        ctx = Context(self.db)
        rows = []
        for idx, (eid, builder) in enumerate([('feat', self.feat_annotation), ('spell', self.spell_annotation)], 1):
            if not builder:
                continue
            spec = builder() if callable(builder) else builder
            kind, anchors, statements = spec
            ev = build_annotation_event(
                ctx, eid, kind, anchors, statements, 'fixture', 'test fixture',
                annotation_id=f'ann_{eid}_{kind}',
            )
            rows.append({
                'schema': 1,
                'id': f'ann_rev_fixture_{idx}',
                'created_at': '2026-11-16T00:00:00+00:00',
                **ev,
            })
        self.annotations.write_text(''.join(canonical(r) + '\n' for r in rows))

    def rebuild(self, *, reseed_annotations=True):
        self._init_schema_and_entries()
        if reseed_annotations:
            self._seed_annotations()
        self.coverage = build_knowledge(self.db, self.root, self.log, annotations_path=self.annotations)
        events, log_hash = load_events(self.log)
        self.db.execute('INSERT INTO metadata VALUES(?,?)', ('summary', canonical({'extraction_sha256': extraction_hash()})))
        self.db.execute(
            'INSERT INTO metadata VALUES(?,?)',
            ('review_snapshot', canonical({'path': str(self.log), 'sha256': log_hash, 'events': len(events), 'bytes': self.log.stat().st_size if self.log.exists() else 0})),
        )
        self.db.commit()
        row = self.db.execute("SELECT id FROM rule_knowledge WHERE subject_id='feat' AND kind='grant'").fetchone()
        self.rid = row[0] if row else None

    def accept(self, operation='knowledge_accept', supersedes=None):
        view = inspect(self.db, self.rid)
        return save(self.db, self.log, self.rid, operation, view['basis_sha256'], 'fixture', 'source checked', supersedes)

    def effective(self):
        return json.loads(self.db.execute('SELECT data FROM effective_knowledge WHERE id=?', (self.rid,)).fetchone()[0])

    def test_accept_reject_revoke_replay_without_mutating_observations(self):
        original = self.db.execute('SELECT data FROM rule_knowledge WHERE id=?', (self.rid,)).fetchone()[0]
        event = self.accept()
        self.rebuild()
        self.assertEqual(self.effective()['review_status'], 'applied')
        self.assertEqual(len(chain(self.db, 'feat')['options']), 2)
        rejected = self.accept('knowledge_reject', event['id'])
        self.rebuild()
        self.assertEqual(self.effective()['status'], 'rejected')
        self.assertFalse(chain(self.db, 'feat')['options'])
        self.assertEqual(self.db.execute('SELECT data FROM rule_knowledge WHERE id=?', (self.rid,)).fetchone()[0], original)
        revoke(self.log, rejected['id'], 'fixture', 'undo rejection')
        self.rebuild()
        self.assertEqual(self.effective()['review_status'], 'revoked')
        self.assertEqual(len(chain(self.db, 'feat')['options']), 2)
        self.assertEqual(len(load_events(self.log)[0]), 3)

    def test_relevant_changes_reopen_and_unrelated_changes_do_not(self):
        self.accept()
        self.other += ' unrelated update'
        self.rebuild()
        self.assertEqual(self.effective()['review_status'], 'applied')
        self.spell = self.spell.replace('30尺', '60尺')
        self.rebuild()
        self.assertEqual(self.effective()['review_status'], 'stale')
        self.assertFalse(chain(self.db, 'feat')['options'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM review_queue WHERE reason='knowledge_decision_stale'").fetchone()[0], 1)

    def test_new_competing_identity_reopens_approval(self):
        self.accept()
        self.competitor = True
        self.rebuild()
        self.assertEqual(self.effective()['review_status'], 'stale')
        target = self.effective()['value']['grants'][0]['target']['resolution']
        self.assertEqual(target['status'], 'ambiguous')
        self.assertEqual(set(target['targets']), {'spell', 'competitor'})
        self.assertFalse(chain(self.db, 'feat')['options'])

    def test_competing_reviews_block_consumption(self):
        first = self.accept()
        with locked(self.log):
            _, version = load_events(self.log)
            append_event(self.log, {k: v for k, v in first.items() if k not in {'id', 'schema', 'created_at'}}, version)
        self.rebuild()
        self.assertEqual(self.effective()['review_status'], 'conflict')
        self.assertFalse(chain(self.db, 'feat')['options'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM knowledge_decisions WHERE status='conflict'").fetchone()[0], 2)

    def test_inspection_basis_is_required_for_writes(self):
        with self.assertRaisesRegex(ValueError, 'basis'):
            save(self.db, self.log, self.rid, 'knowledge_accept', 'wrong', 'fixture', 'source')
        self.assertFalse(self.log.exists())
        self.accept()
        with self.assertRaisesRegex(ValueError, 'Supersede'):
            self.accept()

    def test_same_branch_query_does_not_mix_free_recovery_with_slots(self):
        def count(q):
            where, values = casting_condition(q)
            return self.db.execute('SELECT count(*) FROM entries e WHERE ' + where, values).fetchone()[0]
        self.assertEqual(count({'resource': 'slot_free', 'recovery': 'long_rest', 'spell': '测试法术'}), 1)
        self.assertEqual(count({'resource': 'spell_slot', 'recovery': 'long_rest'}), 0)
        self.assertEqual(count({'resource': 'spell_slot', 'recovery': 'not_stated'}), 1)
        self.assertEqual(chain(self.db, 'spell')['incoming'][0]['entry_id'], 'feat')

    def test_partial_grant_with_unannotated_clause_projects_resolved_branch(self):
        self.feature = self.feature + '\n但只能对龙使用。'
        self.rebuild()
        self.assertEqual(self.effective()['status'], 'partial')
        self.assertEqual([g['reason'] for g in self.effective()['value']['unparsed']], ['unannotated_clause'])
        self.assertEqual(len(chain(self.db, 'feat')['options']), 2)
        # Entry-wide mode (projection_v1 compatibility) still excludes partial records.
        self.assertEqual(list(branches(Context(self.db), [self.effective()], entry_wide=True)), [])

    def test_selection_grant_remains_partial_and_unprojected(self):
        self.feature = '示例 Example\n从以下法术中选择一个，但尚未指明获取方式。'
        self.feat_annotation = (
            'grant',
            [{'id': 'a1', 'kind': 'clause', 'quote': '从以下法术中选择一个，但尚未指明获取方式。'}],
            {'grants': [{
                'id': 'grant_1', 'entry_type': 'spell', 'mode': 'selection',
                'target': {'kind': 'entry', 'name': '测试法术', 'evidence_ref': 'a1'},
                'constraints': {}, 'branches': [], 'evidence_refs': ['a1'],
            }]},
        )
        self.rebuild()
        self.assertEqual(self.effective()['status'], 'partial')
        self.assertFalse(chain(self.db, 'feat')['options'])

    def test_audit_detects_export_and_projection_corruption(self):
        from anna.knowledge.repository import audit_knowledge, knowledge_diff
        self.assertEqual(audit_knowledge(self.db, self.root)['errors'], [])
        self.assertEqual(knowledge_diff(self.db, self.db), [])
        self.db.execute("UPDATE knowledge_rule_links SET relation='wrong'")
        self.assertIn('knowledge_rule_links: projection mismatch', audit_knowledge(self.db, self.root)['errors'])
        (self.root / 'effective_knowledge.jsonl').write_text('')
        (self.root / 'knowledge_annotations.jsonl').write_text('')
        self.db.execute('DELETE FROM casting_options')
        self.db.execute('DELETE FROM knowledge_rule_links')
        errors = audit_knowledge(self.db, self.root)['errors']
        self.assertIn('effective_knowledge: export mismatch', errors)
        self.assertIn('knowledge_annotations: export mismatch', errors)
        self.assertIn('casting_options: projection mismatch', errors)
        self.assertIn('knowledge_rule_links: projection mismatch', errors)

    def test_path_preserves_root_and_validates_every_hop(self):
        result = chain(self.db, 'feat', 'spell')
        self.assertEqual(result['reading']['path'], ['feat', 'spell'])
        self.assertEqual(result['reading']['root_id'], 'feat')
        self.assertIn('传送', result['reading']['text'])
        self.assertEqual(result['reading']['steps'][0]['relations'][0]['relation'], 'grants_spell')
        self.assertEqual(chain(self.db, 'feat', 'spell', ['feat'])['reading']['focus_id'], 'feat')
        for bad in [['spell'], ['feat', 'other'], ['feat', 'spell', 'feat'], {}, ['feat', 1], ['x'] * 17, 'invalid JSON']:
            with self.subTest(path=bad), self.assertRaises(ValueError):
                chain(self.db, 'feat', reading_path=bad)
        self.competitor = True
        self.rebuild()
        candidate = chain(self.db, 'feat', reading_path=['feat', 'competitor'])
        self.assertFalse(candidate['options'])
        self.assertEqual(candidate['reading']['steps'][0]['relations'][0]['target_status'], 'ambiguous')
        with self.assertRaises(ValueError):
            chain(self.db, 'feat', 'competitor')

    def test_exception_and_reverse_lookup_keep_the_specific_branch(self):
        self.rules = [('slots', '法术位 Spell Slots\n施法通常需要法术位。', '法术位', 'Spell Slots', 'rule')]
        self.rebuild()
        result = chain(self.db, 'feat', reading_path=['feat', 'slots'])
        relations = result['reading']['steps'][0]['relations']
        exception = next(e for e in relations if e['relation'] == 'exception_to')
        ordinary = next(e for e in relations if e['relation'] == 'uses_rule')
        self.assertEqual(exception['change'], {'field': 'spell_slot.consumed', 'value': False})
        self.assertNotEqual(exception['scope']['branch_id'], ordinary['scope']['branch_id'])
        self.assertIsNone(ordinary['change'])
        self.assertEqual(exception['when']['branch_id'], exception['scope']['branch_id'])
        reverse = chain(self.db, 'slots', reading_path=['slots', 'feat'])['reading']['steps'][0]['relations']
        self.assertEqual([e['relation'] for e in reverse], ['modified_by'])
        self.assertEqual(reverse[0]['scope'], exception['scope'])
        extended = chain(self.db, 'slots', reading_path=['slots', 'feat', 'spell'])
        self.assertEqual(extended['reading']['focus_id'], 'spell')
        self.assertEqual(extended['bindings'][0]['subject_id'], 'feat')
        self.accept('knowledge_reject')
        self.rebuild()
        with self.assertRaises(ValueError):
            chain(self.db, 'slots', reading_path=['slots', 'feat'])

    def test_local_annotation_and_governing_target_status_are_independent(self):
        self.rules = [(eid, '法术位 Spell Slots\n定义。', '法术位', 'Spell Slots', 'rule') for eid in ('slots_a', 'slots_b')]
        self.rebuild()
        result = chain(self.db, 'feat', reading_path=['feat', 'slots_a'])
        segment = next(s for s in result['segments'] if s['subject_id'] == 'feat')
        self.assertEqual(segment['content_status'], 'resolved')
        self.assertEqual(segment['review_status'], 'applied')
        slots = [r for r in segment['rule_targets'] if r['name'] == '法术位']
        self.assertTrue(slots)
        self.assertTrue(all(r['status'] == 'ambiguous' and set(r['candidates']) == {'slots_a', 'slots_b'} for r in slots))
        self.assertEqual(result['reading']['steps'][0]['relations'][0]['status'], 'ambiguous')
        self.assertEqual(result['bindings'][0]['status'], 'not_stated')

    def test_effect_conditions_match_one_effect_of_the_same_spell_and_branch(self):
        self.spell = (
            '测试法术 Test Spell\n施法时间：动作\n'
            '对目标进行一次远程法术攻击。若命中，则目标受到2d12闪电伤害。\n'
            '无论首次攻击是否命中，在你接下来的每个回合中，你都可以用附赠动作直接对目标造成1d12闪电伤害。'
        )
        self.spell_annotation = (
            'procedure',
            [
                {'id': 's1', 'kind': 'clause', 'quote': '对目标进行一次远程法术攻击。'},
                {'id': 's2', 'kind': 'clause', 'quote': '若命中，则目标受到2d12闪电伤害。'},
                {'id': 's3', 'kind': 'clause', 'quote': '无论首次攻击是否命中，在你接下来的每个回合中，你都可以用附赠动作直接对目标造成1d12闪电伤害。'},
            ],
            {'effects': [
                {'kind': 'attack_roll', 'attack_type': 'ranged_spell', 'target': {'kind': 'self'}, 'evidence_ref': 's1'},
                {'kind': 'damage', 'damage_type': 'lightning', 'timing': 'immediate', 'delivery': 'attack_roll',
                 'when': {'kind': 'attack_result', 'result': 'hit', 'scope': 'initial', 'selector': 'same_paragraph'},
                 'quantity': {'kind': 'dice', 'count': 2, 'sides': 12}, 'evidence_ref': 's2'},
                {'kind': 'damage', 'damage_type': 'lightning', 'timing': 'ongoing', 'delivery': 'automatic',
                 'initial_attack_result': 'hit_or_miss',
                 'use': {'action': 'bonus_action', 'evidence_refs': ['s3']},
                 'quantity': {'kind': 'dice', 'count': 1, 'sides': 12}, 'evidence_ref': 's3'},
            ]},
        )
        self.rebuild()
        def roots(query):
            sql, values = casting_condition(query)
            return {r[0] for r in self.db.execute('SELECT e.id FROM entries e WHERE ' + sql, values)}
        self.assertEqual(roots({'effect': 'damage', 'delivery': 'automatic', 'timing': 'ongoing', 'resource': 'slot_free', 'recovery': 'long_rest'}), {'feat'})
        self.assertEqual(roots({'effect': 'damage', 'delivery': 'attack_roll', 'timing': 'ongoing'}), set())
        self.assertEqual(roots({'effect': 'damage', 'damage_type': 'lightning', 'delivery': 'attack_roll', 'timing': 'immediate'}), {'feat'})
        self.assertEqual(roots({'effect': 'damage', 'timing': 'ongoing', 'resource': 'spell_slot', 'recovery': 'long_rest'}), set())
        focused = chain(self.db, 'feat', 'spell', effect_id='effect_3')
        self.assertEqual(focused['reading']['effect_id'], 'effect_3')

        # Modifying annotated spell clause makes spell annotation stale without invalidating feat grant options.
        self.spell = self.spell.replace('2d12', '3d12')
        self.rebuild(reseed_annotations=False)
        self.assertFalse(roots({'effect': 'damage', 'timing': 'ongoing'}))
        self.assertTrue(chain(self.db, 'feat')['options'])
        state = json.loads(self.db.execute("SELECT data FROM effective_knowledge WHERE subject_id='spell' AND kind='procedure'").fetchone()[0])
        self.assertEqual(state['review_status'], 'stale')

    def test_effect_index_audit_checks_scalar_fields_and_payload(self):
        from anna.knowledge.effects import audit_effect_index
        self.spell_annotation = (
            'procedure',
            [{'id': 's1', 'kind': 'clause', 'quote': '你短暂地被银白的雾气所笼罩，传送到至多30尺内一个你能看见且未占据空间。'}],
            {'effects': [{'kind': 'teleport', 'destination': {'distance': {'quantity': 30, 'unit': 'foot'}}, 'evidence_ref': 's1'}]},
        )
        self.rebuild()
        from anna.knowledge.repository import audit_knowledge
        self.assertEqual(audit_knowledge(self.db, self.root)['errors'], [])
        self.db.execute("UPDATE rule_effect_index SET delivery='automatic'")
        self.assertIn('rule_effect_index: projection mismatch', audit_knowledge(self.db, self.root)['errors'])

    def test_nested_acquisition_and_unknown_prerequisite_gate(self):
        self.feature = (
            '示例 Example\n先决：魔契师等级2+且（智力或感知13+或必需专长）\n'
            '你习得测试法术。\n'
            '你可以在不消耗法术位的情况下施展这些法术各一次。\n'
            '当你以此法施展某个法术后，你不能再次以此法施展该法术，直至你完成一次长休。\n'
            '你也可以消耗合适的法术位来施展这些法术。'
        )
        self.rules = [('required', '必需 Required\n规定。', '必需', 'Required', 'feat')]
        self.rebuild()
        self.assertEqual(self.effective()['status'], 'resolved')
        option = chain(self.db, 'feat')['options'][0]
        self.assertEqual(option['condition_kinds'], ['ability_score', 'class_level', 'requirement'])
        expression = option['conditions'][0]['expression']
        self.assertEqual(expression['all'][1]['any'][1]['target']['resolution']['targets'], ['required'])
        viewed = chain(self.db, 'feat', reading_path=['feat', 'required'])
        relation = viewed['reading']['steps'][0]['relations'][0]
        self.assertEqual(relation['relation'], 'requires_option')

        # Unparsed prerequisite gates the entire entry even with applied grant annotations.
        self.feature = '示例 Example\n先决：神秘的未解析前提\n你习得测试法术。'
        self.feat_annotation = (
            'grant',
            [{'id': 'a1', 'kind': 'clause', 'quote': '你习得测试法术。'}],
            {'grants': [{
                'id': 'grant_1', 'entry_type': 'spell', 'mode': 'known',
                'target': {'kind': 'entry', 'name': '测试法术', 'evidence_ref': 'a1'},
                'constraints': {},
                'branches': [{'id': 'branch_1', 'resource': {'kind': 'not_stated'}, 'evidence_refs': ['a1']}],
                'evidence_refs': ['a1'],
            }]},
        )
        self.rebuild()
        self.assertEqual(self.effective()['status'], 'partial')
        self.assertFalse(chain(self.db, 'feat')['options'])

    def test_source_activation_branch_condition_and_target_do_not_leak(self):
        self.feature_type = 'magic_item'
        self.feature = (
            '示例 Example\n戒指，非普通（需同调）\n'
            '当你持握此物品时，你可以从中施展测试法术，但只能以自身为目标。\n'
            '你也可以使用你拥有的任意法术位来施展该法术。'
        )
        self.feat_annotation = (
            'grant',
            [
                {'id': 'a1', 'kind': 'clause', 'quote': '当你持握此物品时，你可以从中施展测试法术，但只能以自身为目标。'},
                {'id': 'a2', 'kind': 'clause', 'quote': '你也可以使用你拥有的任意法术位来施展该法术。'},
            ],
            {'grants': [{
                'id': 'grant_1', 'entry_type': 'spell', 'mode': 'cast',
                'target': {'kind': 'entry', 'name': '测试法术', 'evidence_ref': {'anchor': 'a1', 'quote': '测试法术'}},
                'constraints': {},
                'branches': [
                    {
                        'id': 'branch_1',
                        'resource': {'kind': 'not_stated'},
                        'target': {'kind': 'self'},
                        'when': {'kind': 'item_state', 'state': 'held', 'subject': 'source_item'},
                        'evidence_refs': ['a1'],
                    },
                    {
                        'id': 'branch_2',
                        'resource': {'kind': 'spell_slot', 'consumed': True, 'count': 1},
                        'evidence_refs': ['a2'],
                    },
                ],
                'evidence_refs': ['a1', 'a2'],
            }]},
        )
        self.rules = [('range', '目标 Targets\n定义。', '目标', 'Targets', 'rule')]
        self.rebuild()
        result = chain(self.db, 'feat', 'spell')
        self.assertEqual(self.effective()['status'], 'resolved')
        options = {o['resource']: o for o in result['options']}
        self.assertEqual([c['scope']['kind'] for c in options['not_stated']['conditions']], ['provider', 'usage_branch'])
        self.assertEqual([c['scope']['kind'] for c in options['spell_slot']['conditions']], ['provider'])
        exception = chain(self.db, 'feat', reading_path=['feat', 'range'])['reading']['steps'][0]['relations'][0]
        self.assertEqual(exception['change'], {'field': 'casting.target', 'value': 'self'})
        self.assertEqual([c.get('state') for c in exception['when']['all']], [None, 'attuned', 'held'])

    def test_partial_procedure_projects_resolved_effect_and_blocks_downstream_dependency(self):
        self.spell = (
            '测试法术 Test Spell\n施法时间：动作\n'
            '选定施法距离内一名生物。\n'
            '目标必须进行一次体质豁免。\n'
            '在累计三次豁免失败后，该生物陷入石化状态。\n'
            '如果该生物在石化期间受破坏，则保持破坏。'
        )
        self.spell_annotation = (
            'procedure',
            [
                {'id': 's1', 'kind': 'clause', 'quote': '选定施法距离内一名生物。'},
                {'id': 's2', 'kind': 'clause', 'quote': '目标必须进行一次体质豁免。'},
                {'id': 's3', 'kind': 'clause', 'quote': '在累计三次豁免失败后，该生物陷入石化状态。'},
            ],
            {'effects': [
                {'kind': 'targets', 'selection': {'kind': 'creature', 'count': 1}, 'evidence_ref': 's1'},
                {'kind': 'saving_throw', 'ability': '体质', 'check_role': 'initial',
                 'target': {'kind': 'target_ref'}, 'evidence_ref': 's2'},
                {'kind': 'saving_throw', 'check_role': 'repeat', 'ability_source': 'repeat_of',
                 'repeat_of': {'kind': 'save_result', 'scope': 'initial', 'result': 'any'},
                 'target': {'kind': 'target_ref'}, 'evidence_ref': 's2'},
                {'kind': 'check_sequence', 'counting_scope': 'per_target', 'initial_inclusion': 'not_stated',
                 'check': {'kind': 'check_ref', 'scope': 'repeat'}, 'target': {'kind': 'target_ref'}, 'evidence_ref': 's2'},
                {'kind': 'condition', 'condition': '石化',
                 'when': {'kind': 'check_total', 'result': 'failure', 'count': 3},
                 'target': {'kind': 'target_ref'}, 'evidence_ref': 's3'},
            ]},
        )
        self.rebuild()
        record = json.loads(self.db.execute("SELECT data FROM effective_knowledge WHERE subject_id='spell'").fetchone()[0])
        self.assertEqual(record['status'], 'partial')
        self.assertEqual([g['reason'] for g in record['value']['unparsed']], ['unannotated_clause'])
        sql, args = casting_condition({
            'query_subject': 'spell', 'effect': 'condition', 'cumulative_result': 'failure',
            'cumulative_count': 3, 'save_ability': '体质', 'timing': 'cumulative', 'target_scope': 'single',
        })
        self.assertEqual({r[0] for r in self.db.execute('SELECT e.id FROM entries e WHERE ' + sql, args)}, {'spell'})

        # An intervening unannotated clause blocks downstream sequence binding cleanly.
        self.spell_annotation = (
            'procedure',
            [
                {'id': 's1', 'kind': 'clause', 'quote': '选定施法距离内一名生物。'},
                {'id': 's3', 'kind': 'clause', 'quote': '在累计三次豁免失败后，该生物陷入石化状态。'},
            ],
            {'effects': [
                {'kind': 'targets', 'selection': {'kind': 'creature', 'count': 1}, 'evidence_ref': 's1'},
                {'kind': 'condition', 'condition': '石化',
                 'when': {'kind': 'check_total', 'result': 'failure', 'count': 3},
                 'target': {'kind': 'target_ref'}, 'evidence_ref': 's3'},
            ]},
        )
        self.rebuild()
        projected = [r[0] for r in self.db.execute("SELECT effect FROM rule_effect_index WHERE subject_id='spell'")]
        self.assertEqual(projected, ['targets'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM rule_effect_index WHERE subject_id='spell' AND effect='condition'").fetchone()[0], 0)

    def test_structural_extractors_for_background_subclass_and_definitions(self):
        self._init_schema_and_entries()
        feat_target = '示例 Example\n专长：警觉（见第五章）\n其余背景故事文本。'
        sub_body = '圣武士子职 Oath\n'
        child_body = '3级：誓言光环 Oath Aura\n光环文本。'
        role_body = '精通动作 Attack\n规则解释。'
        self.db.execute('INSERT INTO pages VALUES(?,?)', ('bg.htm', '智者 Sage\n专长：示例（法师）\n背景。'))
        self.db.execute('INSERT INTO pages VALUES(?,?)', ('sub.htm', sub_body + child_body))
        self.db.execute('INSERT INTO pages VALUES(?,?)', ('cls.htm', '法师 Wizard\n职业说明。'))
        self.db.execute('INSERT INTO pages VALUES(?,?)', ('glossary.htm', role_body))
        bg = {'id': 'bg_1', 'entity_id': 'bg_1', 'name': '智者 Sage', 'name_zh': '智者', 'name_en': 'Sage', 'type': 'background', 'ruleset': '5r', 'page_path': 'bg.htm', 'start': 0, 'title_end': len('智者 Sage\n'), 'end': len('智者 Sage\n专长：示例（法师）\n背景。'), 'source_ids': ['phb'], 'fields': {}, 'anchors': [], 'is_primary': True}
        cls = {'id': 'cls_1', 'entity_id': 'cls_1', 'name': '法师 Wizard', 'name_zh': '法师', 'name_en': 'Wizard', 'type': 'class', 'ruleset': '5r', 'page_path': 'cls.htm', 'start': 0, 'title_end': len('法师 Wizard\n'), 'end': len('法师 Wizard\n职业说明。'), 'source_ids': ['phb'], 'fields': {}, 'anchors': [], 'is_primary': True}
        sub = {'id': 'sub_1', 'entity_id': 'sub_1', 'name': '子职 Oath', 'name_zh': '子职', 'name_en': 'Oath', 'type': 'subclass', 'ruleset': '5r', 'page_path': 'sub.htm', 'start': 0, 'title_end': len(sub_body), 'end': len(sub_body + child_body), 'parent_id': 'cls_1', 'source_ids': ['phb'], 'fields': {}, 'anchors': [], 'is_primary': True}
        cf = {'id': 'cf_1', 'entity_id': 'cf_1', 'name': '3级：誓言光环 Oath Aura', 'name_zh': '誓言光环', 'name_en': 'Oath Aura', 'type': 'class_feature', 'ruleset': '5r', 'page_path': 'sub.htm', 'start': len(sub_body), 'title_end': len(sub_body) + len('3级：誓言光环 Oath Aura\n'), 'end': len(sub_body + child_body), 'parent_id': 'sub_1', 'source_ids': ['phb'], 'fields': {}, 'anchors': [], 'is_primary': True}
        action_e = {'id': 'act_1', 'entity_id': 'act_1', 'name': '精通动作 Attack', 'name_zh': '精通动作', 'name_en': 'Attack', 'type': 'action', 'ruleset': '5r', 'page_path': 'glossary.htm', 'start': 0, 'title_end': len('精通动作 Attack\n'), 'end': len(role_body), 'source_ids': ['phb'], 'fields': {}, 'anchors': [], 'is_primary': True}
        for e in (bg, cls, sub, cf, action_e):
            self.db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?)', (e['id'], e['name'], e['type'], e['ruleset'], e['page_path'], e['start'], e['end'], canonical(e)))
        ctx = Context(self.db)
        fg = extract_feat_grant(bg, ctx)
        self.assertEqual(fg['grants'][0]['constraints'], {'class_list': ['法师']})
        self.assertEqual(fg['grants'][0]['target']['resolution']['targets'], ['feat'])
        sf = extract_subclass_features(sub, ctx)
        self.assertEqual(sf['grants'][0]['target']['resolution']['targets'], ['cf_1'])
        self.assertEqual(sf['grants'][0]['when']['minimum'], 3)
        roles = {e['id']: v['role'] for e, v in extract_definition_roles(ctx)}
        self.assertEqual(roles['act_1'], 'explanation')

    def test_exports_and_evidence_are_exact(self):
        exported = [json.loads(s) for s in (self.root / 'effective_knowledge.jsonl').read_text().splitlines()]
        self.assertEqual({r['id'] for r in exported}, {r[0] for r in self.db.execute('SELECT id FROM effective_knowledge')})
        context = Context(self.db)
        for r in exported:
            validate_record(r, context)
        changed = deepcopy(exported[0])
        changed['evidence']['raw'] = 'invented'
        with self.assertRaisesRegex(ValueError, 'evidence'):
            validate_record(changed, context)


if __name__ == '__main__':
    unittest.main()
