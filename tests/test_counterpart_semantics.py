"""Read-only semantic reports: scoped comparisons, evidence and conservative boundaries."""
from copy import deepcopy
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from anna.compare.semantics import (ReportContext, background_comparisons, build_report, compare_case, level_gate,
                                  normalize_external, pointer_get, render_markdown)
from anna.compare.catalog import load_records
from anna.source.html import key
from anna.paths import FIVETOOLS, PROJECT, WEB


def record(kind='feat', source='PHB', **kwargs):
    return {'id': 'external', 'kind': kind, 'source': source, 'name_zh': '魔法学徒',
            'name_en': 'Magic Initiate', 'locator': 'data/feats.json#/feat/0',
            'owner': {'class_zh': '牧师', 'class_source': source}, **kwargs}


def normalize(item, **kwargs):
    return normalize_external(record(**kwargs), item, {(key('光亮术'), 'PHB'): ['light'], (key('光亮术'), 'XPHB'): ['light24']})


def anna_grant(target=None, **kwargs):
    return {'target': target or {'kind': 'fixed', 'record_ids': ['light']}, 'mode': 'known',
            'level': None, 'ability': ['智力'], 'branches': [{'resource': {'kind': 'not_stated'}}],
            'evidence': [{'path': 'page.html', 'span': [0, 1], 'raw': '光'}],
            'path': [], 'parse_status': 'resolved', 'review_status': 'unreviewed',
            'choice_binding': None, **kwargs}


MAPPING = {'status': 'counterpart', 'root_id': 'entry', 'entry_ids': ['entry'], 'evidence': []}


class NormalizationTests(unittest.TestCase):
    def test_default_spell_source_is_not_owner_book_and_marker_is_preserved(self):
        result = normalize({'additionalSpells': [{'known': {'1': ['光亮术#c', '光亮术|xphb']}}]}, source='XPHB')
        self.assertEqual(result['grants'][0]['target']['record_ids'], ['light'])
        self.assertEqual(result['grants'][0]['target']['marker'], 'c')
        self.assertEqual(result['grants'][1]['target']['record_ids'], ['light24'])
        self.assertTrue(result['grants'][0]['locator'].endswith('/known/1/0'))

    def test_levels_and_selection_groups_remain_separate(self):
        result = normalize({'additionalSpells': [
            {'ability': 'wis', 'prepared': {'3': ['光亮术']}},
            {'ability': {'choose': ['cha', 'int']}, 'known': {'_': [{'choose': 'class=法师|level=0', 'count': 2}]}}]}, kind='subclass')
        a, b = result['grants']
        self.assertEqual(a['level'], {'kind': 'class_level', 'minimum': 3, 'class': '牧师'})
        self.assertIsNone(b['level'])
        self.assertNotEqual(a['group'], b['group'])
        self.assertEqual(b['target'], {'kind': 'choice', 'levels': [0], 'class_list': ['法师'], 'count': 2})
        self.assertEqual(b['ability'], ['智力', '魅力'])

    def test_nested_cantrip_and_daily_do_not_infer_cost_or_rest(self):
        result = normalize({'additionalSpells': [{'ability': 'cha', 'known': {'1': {'_': [{'choose': 'level=0|class=法师'}]}},
                                                'innate': {'3': {'daily': {'1': ['光亮术']}}}}]}, kind='subrace')
        self.assertFalse(result['gaps'])
        g = result['grants'][1]
        self.assertEqual(g['uses'], 1)
        self.assertEqual(g['recovery'], {'cadence': 'daily'})
        self.assertIsNone(g['resource'])
        self.assertEqual(g['mode'], 'innate')
        self.assertEqual(g['level'], {'kind': 'character_level', 'minimum': 3})

    def test_copy_and_mod_block_but_versions_do_not_replace_base(self):
        for field in ('_copy', '_mod'):
            with self.subTest(field=field):
                result = normalize({field: {'name': 'other'}, 'additionalSpells': [{'known': {'1': ['光亮术']}}]})
                self.assertTrue(result['blocked']); self.assertEqual(result['grants'], [])
                self.assertEqual(result['gaps'][0]['reason'], 'inheritance_unresolved')
        result = normalize({'_versions': [{'name': 'variant'}], 'additionalSpells': [{'known': {'1': ['光亮术']}}]})
        self.assertFalse(result['blocked']); self.assertEqual(len(result['grants']), 1)
        self.assertEqual(result['gaps'][0]['reason'], 'versions_not_expanded')

    def test_unknown_or_invalid_fields_do_not_disappear(self):
        samples = [({'additionalSpells': {}}, 'unsupported_additional_spells'),
                   ({'additionalSpells': [{'known': {'s3': ['光亮术']}}]}, 'unsupported_level_key'),
                   ({'additionalSpells': [{'known': {'1': ['光亮术#2']}}]}, 'unsupported_spell_target'),
                   ({'additionalSpells': [{'known': {'1': [{'choose': 'level=0|class=法师|school=A'}]}}]}, 'unsupported_spell_target'),
                   ({'additionalSpells': [{'known': {'1': [{'choose': 'level=0|class=法师', 'count': True}]}}]}, 'unsupported_spell_target'),
                   ({'additionalSpells': [{'innate': {'3': {'daily': {'1e': ['光亮术']}}}}]}, 'unsupported_daily_scope'),
                   ({'additionalSpells': [{'ability': {'choose': ['luck']}, 'newMode': []}]}, 'unsupported_ability'),
                   ({'charges': True}, 'unsupported_charges'), ({'recharge': 'sometimes'}, 'unsupported_recharge'),
                   ({'rechargeAmount': 'level + 1'}, 'unsupported_rechargeAmount')]
        for item, reason in samples:
            with self.subTest(item=item):
                result = normalize(item)
                self.assertIn(reason, [g['reason'] for g in result['gaps']])
                self.assertTrue(all(g['evidence']['locator'].startswith('data/feats.json#/feat/0/') for g in result['gaps']))

    def test_charges_keep_base_cost_but_will_is_not_free_casting(self):
        result = normalize({'charges': 7, 'recharge': 'dawn', 'rechargeAmount': '{@dice 1d6 + 1}',
                            'attachedSpells': {'charges': {'1': ['光亮术']}, 'will': ['光亮术|xphb']}}, kind='item')
        self.assertEqual(result['grants'][0]['resource'], {'kind': 'charges', 'base_cost': 1})
        self.assertIsNone(result['grants'][1]['resource'])
        self.assertEqual(result['fields']['charges']['value'], 7)
        self.assertEqual(result['fields']['recharge']['value'], 'dawn')
        self.assertEqual([g['reason'] for g in result['gaps']], ['will_does_not_verify_cost'])

    def test_json_pointer_escapes(self):
        self.assertEqual(pointer_get({'a/b': {'~': [3]}}, '/a~1b/~0/0'), 3)


class ComparisonTests(unittest.TestCase):
    def rows(self, ext, anna, mapping=MAPPING):
        return compare_case(ext, anna, mapping, {})

    def test_agreement_conflict_missing_and_external_insufficiency(self):
        ext = normalize({'additionalSpells': [{'ability': 'int', 'known': {'3': ['光亮术']}}]})
        a = anna_grant(ability=['魅力'])
        rows = self.rows(ext, [a]); by_dimension = {r['dimension']: r for r in rows}
        self.assertEqual(by_dimension['spell']['outcome'], 'agree')
        self.assertEqual(by_dimension['ability']['outcome'], 'conflict')
        self.assertEqual(by_dimension['ability']['anna']['value'], ['魅力'])
        self.assertEqual(by_dimension['ability']['external']['value'], ['智力'])
        self.assertEqual(by_dimension['level']['outcome'], 'anna_missing')
        self.assertEqual(by_dimension['recovery']['outcome'], 'external_insufficient')
        self.assertEqual(by_dimension['ability']['review_status'], 'unreviewed')
        self.assertEqual(by_dimension['ability']['anna']['evidence'], a['evidence'])

    def test_daily_and_innate_never_equal_long_rest_and_prepared(self):
        ext = normalize({'additionalSpells': [{'innate': {'_': {'daily': {'1': ['光亮术']}}}}]})
        a = anna_grant(mode='prepared', branches=[{'resource': {'kind': 'spell_slot', 'consumed': False},
                      'uses': {'quantity': {'kind': 'constant', 'value': 1}}, 'recovery': {'event': 'long_rest'}}])
        rows = {r['dimension']: r for r in self.rows(ext, [a])}
        self.assertEqual(rows['uses']['outcome'], 'agree')
        for dim in ('mode', 'resource', 'recovery'):
            self.assertEqual(rows[dim]['outcome'], 'external_insufficient')

    def test_missing_choice_domain_can_be_reported_without_borrowing_another_choice(self):
        ext = normalize({'additionalSpells': [{'ability': 'int', 'known': {'1': [{'choose': 'level=0|class=法师'}]}}]})
        a = anna_grant(target={'kind': 'choice', 'levels': [0], 'count': 1, 'class_list': None}, ability=['魅力'], parse_status='partial')
        rows = {r['dimension']: r for r in self.rows(ext, [a])}
        self.assertEqual(rows['spell']['outcome'], 'anna_missing')
        self.assertEqual(rows['ability']['outcome'], 'conflict')
        self.assertEqual(rows['ability']['anna_status'], 'partial')
        ext['grants'].append({**deepcopy(ext['grants'][0]), 'target': {'kind': 'choice', 'levels': [0], 'count': 1, 'class_list': ['牧师']}})
        rows = self.rows(ext, [a])
        self.assertFalse(any(r['outcome'] == 'agree' for r in rows))

    def test_ambiguous_scope_and_spell_identity_do_not_select_first(self):
        ext = normalize({'additionalSpells': [{'known': {'1': ['光亮术']}}]})
        rows = self.rows(ext, [anna_grant()], {**MAPPING, 'status': 'ambiguous', 'entry_ids': ['a', 'b']})
        self.assertEqual([r['outcome'] for r in rows], ['identity_unresolved'])
        ext['grants'][0]['target']['record_ids'] = ['light', 'another']
        rows = self.rows(ext, [anna_grant()])
        self.assertEqual(rows[0]['outcome'], 'identity_unresolved')
        self.assertFalse(any(r['outcome'] == 'agree' for r in rows))

    def test_duplicate_grants_and_unknown_external_remainder_are_not_confident_conflicts(self):
        ext = normalize({'additionalSpells': [{'known': {'1': ['光亮术']}}]})
        rows = self.rows(ext, [anna_grant(), anna_grant()])
        self.assertEqual(rows[0]['reason'], 'multiple_anna_grants_in_scope')
        self.assertFalse(any(r['outcome'] == 'agree' for r in rows))
        ext['grants'].append(deepcopy(ext['grants'][0]))
        rows = self.rows(ext, [anna_grant()])
        self.assertEqual(rows[0]['reason'], 'multiple_external_grants_in_scope')
        ext = normalize({'additionalSpells': [{'known': {'1': ['光亮术', {'unknown': 1}]}}]})
        rows = self.rows(ext, [anna_grant(target={'kind': 'fixed', 'record_ids': ['other']})])
        extra = next(r for r in rows if r['reason'] == 'anna_target_not_in_external_machine_set')
        self.assertEqual(extra['outcome'], 'external_insufficient')

    def test_empty_machine_fields_do_not_prove_absence(self):
        rows = self.rows(normalize({}), [])
        self.assertEqual(rows[0]['outcome'], 'external_insufficient')
        self.assertEqual(rows[0]['reason'], 'no_machine_spells_does_not_prove_no_grant')

    def test_choice_dependency_uses_the_shared_context_not_just_equal_class_lists(self):
        ext = normalize({'additionalSpells': [{'known': {'_': [{'choose': 'level=0|class=法师', 'count': 2},
                                                                              {'choose': 'level=1|class=法师'}]}}]})
        choices = [anna_grant(target=g['target'], choice_binding='r:class_list') for g in ext['grants']]
        rows = self.rows(ext, choices)
        self.assertEqual(next(r for r in rows if r['dimension'] == 'choice_dependency')['outcome'], 'agree')
        choices[1]['choice_binding'] = 'different_context'
        rows = self.rows(ext, choices)
        self.assertEqual(next(r for r in rows if r['dimension'] == 'choice_dependency')['outcome'], 'anna_missing')

    def test_level_conjunction_does_not_flatten_disjunction_negation_or_other_owner(self):
        a = {'kind': 'class_level', 'class': '牧师', 'minimum': 3, 'owner_id': 'cleric'}
        b = {**a, 'minimum': 7}
        self.assertEqual(level_gate([{'all': [a, b]}]), {'kind': 'class_level', 'class': '牧师', 'minimum': 7})
        for expressions in [[{'any': [a,b]}], [{'not': a}], [a, {**b, 'owner_id': 'other'}], [a, {'kind': 'character_level', 'minimum': 7}]]:
            self.assertIsNone(level_gate(expressions))


class BackgroundComparisonTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE casting_options(id TEXT,root_id TEXT,data TEXT)')
        self.grant = {'entry_type': 'feat', 'constraints': {'class_list': ['牧师']}, 'evidence': [],
                      'target': {'resolution': {'status': 'resolved', 'targets': ['feat']}}}
        self.bg = {'subject_id': 'bg', 'status': 'resolved', 'review_status': 'unreviewed', 'value': {'grants': [self.grant]}}
        self.ctx = SimpleNamespace(db=self.db, knowledge={'bg': self.bg},
                                   locate=lambda *_: {'status': 'counterpart', 'root_id': 'feat'}, verify_evidence=lambda _: None)
        self.case = {'mapping': {'root_id': 'bg', 'status': 'counterpart'},
                     'external': {'fields': {'feats': {'value': [{'魔法学徒；牧师|xphb': True}], 'evidence': {}}}}}
        self.variant = {'name': '魔法学徒；牧师', 'source': 'XPHB', '_mod': {'entries': []},
                        'additionalSpells': [{'known': {'_': [{'choose': 'level=0|class=牧师', 'count': 2}]}}]}
        self.variants = {(key(self.variant['name']), 'XPHB'): [(record(source='XPHB'), 0, self.variant)]}
        self.option = {'id': 'option', 'constraints': {'class_list': ['牧师']},
                       'acquisition_steps': [{'subject_id': 'bg', 'target_id': 'feat'}]}
        self.db.execute('INSERT INTO casting_options VALUES(?,?,?)', ('option','bg',json.dumps(self.option)))

    def tearDown(self):
        self.db.close()

    def rows(self):
        return background_comparisons(self.ctx, self.case, self.variants, {})

    def test_valid_reference_selector_and_each_projected_path(self):
        self.assertEqual([r['outcome'] for r in self.rows()], ['agree', 'agree'])
        self.option['id'] = 'leak'; self.option['constraints'] = {}
        self.db.execute('INSERT INTO casting_options VALUES(?,?,?)', ('leak','bg',json.dumps(self.option)))
        rows = self.rows()
        self.assertEqual(rows[1]['outcome'], 'conflict')
        self.assertEqual(len(rows[1]['anna']['value']), 2)
        self.assertEqual(rows[0]['reason'], 'declared_variant_selector_only')

    def test_missing_conflicting_rejected_and_ambiguous_dependencies(self):
        self.grant['constraints'] = {}
        self.assertEqual(self.rows()[0]['outcome'], 'anna_missing')
        self.grant['constraints'] = {'class_list': ['法师']}
        self.assertEqual(self.rows()[0]['outcome'], 'conflict')
        self.bg['status'] = 'rejected'; self.assertEqual(self.rows(), [])
        self.bg['status'] = 'resolved'
        candidates = next(iter(self.variants.values())); candidates.append(deepcopy(candidates[0]))
        self.assertEqual(self.rows(), [])

    def test_unknown_selector_or_inherited_variant_is_not_interpreted(self):
        self.variant['additionalSpells'][0]['known']['_'][0]['choose'] += '|school=A'
        self.assertEqual(self.rows(), [])
        self.variant['additionalSpells'][0]['known']['_'][0]['choose'] = 'level=0|class=牧师'
        self.variant['_copy'] = {'name': 'other'}
        self.assertEqual(self.rows(), [])


class ReportFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.external = self.root/'external'; (self.external/'data').mkdir(parents=True)
        self.source = self.root/'source'; self.source.mkdir()
        self.text = '魔法学徒 Magic Initiate\n你习得光亮术。'
        (self.source/'page.html').write_text(self.text)
        self.catalog = {'feat': [{'name': '魔法学徒', 'ENG_name': 'Magic Initiate', 'source': 'PHB',
                                 'additionalSpells': [{'ability': 'int', 'known': {'1': ['光亮术']}}]}],
                        'spell': [{'name': '光亮术', 'ENG_name': 'Light', 'source': 'PHB'}]}
        self.external_file = self.external/'data/feats.json'; self.external_file.write_text(json.dumps(self.catalog, ensure_ascii=False))
        self.dbfile = self.root/'catalog.sqlite'; self.db = sqlite3.connect(self.dbfile)
        self.db.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE sources(id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE counterpart_sources(source_id TEXT,code TEXT,status TEXT);
            CREATE TABLE entries(id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE counterparts(entry_id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE effective_knowledge(subject_id TEXT,kind TEXT,data TEXT);
            CREATE TABLE pages(path TEXT PRIMARY KEY,text TEXT,sha256 TEXT);
            CREATE TABLE casting_options(id TEXT,root_id TEXT,data TEXT);''')
        records, snapshot = load_records(self.external); self.records = records
        self.db.executemany('INSERT INTO metadata VALUES(?,?)', [('summary', json.dumps({'snapshot_commit': 'fixture', 'input_manifest_sha256': 'manifest'})),
                                                                ('counterpart_coverage', json.dumps(snapshot)), ('review_snapshot', '{}')])
        self.db.execute('INSERT INTO sources VALUES(?,?)', ('phb', '玩家手册2014'))
        self.db.execute('INSERT INTO counterpart_sources VALUES(?,?,?)', ('phb', 'PHB', '上下文推得'))
        self.db.execute('INSERT INTO pages VALUES(?,?,?)', ('page.html', self.text, sha256((self.source/'page.html').read_bytes()).hexdigest()))
        self.entry = {'id': 'entry', 'name': '魔法学徒', 'name_en': 'Magic Initiate', 'source_ids': ['phb'], 'page_path': 'page.html',
                      'start': 0, 'end': len(self.text), 'ruleset': '5e', 'type': 'feat', 'parent_id': ''}
        self.db.execute('INSERT INTO entries VALUES(?,?)', ('entry', json.dumps(self.entry)))
        feat = next(r for r in records if r['kind'] == 'feat')
        self.db.execute('INSERT INTO counterparts VALUES(?,?)', ('entry', json.dumps({'entry_id': 'entry', 'outcome': 'resolved', 'rule': 'english_name', 'record_id': feat['id']})))
        self.db.commit()

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def test_report_is_deterministic_and_does_not_mutate_input(self):
        before = self.dbfile.read_bytes(); changes = self.db.total_changes
        a = build_report(self.db, self.external, self.source)
        b = build_report(self.db, self.external, self.source)
        self.assertEqual(a, b); self.assertEqual(self.db.total_changes, changes)
        self.assertEqual(self.dbfile.read_bytes(), before)
        self.assertEqual(a['cases'][0]['comparisons'][0]['outcome'], 'anna_missing')
        self.assertFalse(a['policy']['writes_effective_rules']); self.assertFalse(a['policy']['approves_reviews'])
        self.assertIn('Unicode', render_markdown(a))
        self.assertEqual(a['snapshots']['source_evidence_files']['page.html'], sha256((self.source/'page.html').read_bytes()).hexdigest())

    def test_external_update_requires_rebuild_and_relocates_pointer_after_rebuild(self):
        self.catalog['feat'].insert(0, {'name': '另一项', 'ENG_name': 'Other', 'source': 'PHB'})
        self.external_file.write_text(json.dumps(self.catalog, ensure_ascii=False))
        with self.assertRaisesRegex(ValueError, 'snapshot differs'): build_report(self.db, self.external, self.source)
        records, snapshot = load_records(self.external)
        self.db.execute("UPDATE metadata SET value=? WHERE key='counterpart_coverage'", (json.dumps(snapshot),))
        feat = next(r for r in records if r['name_en'] == 'Magic Initiate')
        self.db.execute("UPDATE counterparts SET data=?", (json.dumps({'entry_id': 'entry', 'outcome': 'resolved', 'rule': 'english_name', 'record_id': feat['id']}),))
        report = build_report(self.db, self.external, self.source)
        self.assertEqual(report['cases'][0]['external_record']['locator'], 'data/feats.json#/feat/1')
        self.assertEqual(report['cases'][0]['mapping']['status'], 'counterpart')

    def test_source_update_and_bad_evidence_rejected(self):
        ctx = ReportContext(self.db, self.source, self.records)
        with self.assertRaisesRegex(ValueError, 'no longer matches'):
            ctx.verify_evidence({'path': 'page.html', 'span': [0, 1], 'raw': '错'})
        with self.assertRaisesRegex(ValueError, 'range'):
            ctx.evidence('page.html', [0, len(self.text)+1])
        (self.source/'page.html').write_text(self.text+'changed')
        with self.assertRaisesRegex(ValueError, 'Source snapshot changed'): build_report(self.db, self.external, self.source)

    def test_source_and_generation_do_not_borrow_another_entry(self):
        entry = {**self.entry, 'ruleset': '5r'}
        self.db.execute('UPDATE entries SET data=?', (json.dumps(entry),))
        report = build_report(self.db, self.external, self.source)
        self.assertEqual(report['cases'][0]['mapping']['status'], 'unresolved')
        self.assertEqual(report['cases'][0]['comparisons'][0]['outcome'], 'identity_unresolved')

    def test_duplicate_matching_entries_preserve_ambiguity(self):
        other = {**self.entry, 'id': 'other'}
        self.db.execute('INSERT INTO entries VALUES(?,?)', ('other', json.dumps(other)))
        match = json.loads(self.db.execute('SELECT data FROM counterparts').fetchone()[0])
        self.db.execute('INSERT INTO counterparts VALUES(?,?)', ('other', json.dumps({**match, 'entry_id': 'other'})))
        report = build_report(self.db, self.external, self.source)
        self.assertEqual(report['cases'][0]['mapping']['status'], 'ambiguous')
        self.assertEqual(report['cases'][0]['mapping']['entry_ids'], ['entry', 'other'])

    def test_rejected_and_unsupported_knowledge_schema_cannot_supply_agreement(self):
        r = {'id': 'k', 'subject_id': 'entry', 'schema': 2, 'status': 'rejected', 'review_status': 'accepted',
             'decision_ids': ['rejection'], 'value': {'unparsed': [], 'grants': []}}
        self.db.execute('INSERT INTO effective_knowledge VALUES(?,?,?)', ('entry','grant',json.dumps(r)))
        report = build_report(self.db, self.external, self.source)
        case = report['cases'][0]
        self.assertEqual(case['anna']['gaps'][0]['reason'], 'rejected_knowledge')
        self.assertEqual(case['anna']['records'][0]['status'], 'rejected')
        self.assertFalse(any(x['outcome']=='agree' for x in case['comparisons']))
        r['schema'] = 1
        self.db.execute('UPDATE effective_knowledge SET data=?', (json.dumps(r),))
        with self.assertRaisesRegex(ValueError, 'knowledge schema'):
            build_report(self.db, self.external, self.source)

    def test_cli_reads_existing_db_only_and_rejects_output_in_mirrors(self):
        cmd = [sys.executable, '-m', 'anna.compare.semantics', '--db', str(self.dbfile),
               '--source', str(self.source), '--5etools', str(self.external), '--output', str(self.root/'report')]
        before = self.dbfile.read_bytes()
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.dbfile.read_bytes(), before)
        self.assertTrue((self.root/'report.md').is_file())
        run = subprocess.run(cmd[:-1]+[str(self.external/'data/feats')], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0); self.assertIn('must not overwrite', run.stderr)
        cmd[cmd.index('--db')+1] = str(self.root/'missing.sqlite')
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0); self.assertFalse((self.root/'missing.sqlite').exists())


@unittest.skipUnless((PROJECT/'data/extracted/catalog.sqlite').exists() and (FIVETOOLS/'data').exists() and WEB.exists(), 'requires local snapshots')
class LocalSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with closing(sqlite3.connect((PROJECT/'data/extracted/catalog.sqlite').as_uri()+'?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            cls.report = build_report(db, FIVETOOLS, WEB)

    def case(self, name, source='PHB', owner=None):
        return next(c for c in self.report['cases'] if c['external_record']['name_en'] == name and c['external_record']['source'] == source
                    and (owner is None or c['external_record']['owner']['class_source'] == owner))

    def test_four_families_and_2014_elf_conflicts_are_evidenced_not_promoted(self):
        self.assertEqual(set(self.report['summary']['families']), {'magic_initiate','domain_spells','elf_2014','charged_items'})
        high = self.case('High'); drow = self.case('Drow')
        self.assertEqual(high['mapping']['status'], 'source_slice')
        ability = next(r for r in high['comparisons'] if r['dimension']=='ability')
        self.assertEqual(ability['outcome'], 'conflict'); self.assertEqual(ability['anna_status'], 'partial')
        self.assertEqual(ability['anna']['value'], ['魅力']); self.assertEqual(ability['external']['value'], ['智力'])
        self.assertNotIn('木精灵Wood Elf', high['mapping']['evidence'][0]['raw'])
        self.assertEqual(len(drow['external']['grants']), 3)
        extra = [r for r in drow['comparisons'] if r['reason']=='anna_target_not_in_external_machine_set']
        self.assertEqual(len(extra), 1); self.assertEqual(extra[0]['outcome'], 'conflict')

    def test_domain_spell_scope_does_not_borrow_same_spell_from_later_feature(self):
        life = self.case('Life Domain','XPHB'); war = self.case('War Domain','XPHB')
        self.assertEqual(len(life['external']['grants']), 10)
        self.assertEqual(len(life['anna']['grants']), 10)
        self.assertEqual([r['outcome'] for r in life['comparisons'] if r['dimension']=='level'], ['agree']*10)
        self.assertEqual(len(war['anna']['grants']), 1)
        self.assertFalse(any(r['outcome']=='conflict' for r in war['comparisons']))
        self.assertEqual(sum(r['outcome']=='anna_missing' for r in war['comparisons']), 9)
        self.assertEqual(self.case('Life Domain','PHB','PHB')['mapping']['status'], 'unresolved')
        self.assertTrue(self.case('Life Domain','PHB','XPHB')['external']['blocked'])

    def test_magic_initiate_variants_are_not_expanded_but_selectors_survive_paths(self):
        feat = self.case('Magic Initiate','XPHB')
        self.assertEqual(feat['mapping']['status'], 'counterpart')
        self.assertTrue(feat['mapping']['excluded_matches'])
        self.assertEqual(len(feat['external']['grants']), 6)
        self.assertIn('versions_not_expanded', [g['reason'] for g in feat['external']['gaps']])
        for name, cls in [('Acolyte','牧师'),('Guide','德鲁伊'),('Sage','法师')]:
            bg = self.case(name,'XPHB')
            for dim in ('variant_class_selector','projected_class_selector'):
                row = next(r for r in bg['comparisons'] if r['dimension']==dim)
                self.assertEqual(row['outcome'], 'agree'); self.assertEqual(row['external']['value'], [cls])
            self.assertIn('variant_uid_not_expanded', [r['reason'] for r in bg['comparisons']])

    def test_charged_item_generations_and_external_cost_gap_are_separate(self):
        for source in ('DMG','XDMG'):
            wand = self.case('Wand of Magic Missiles',source)
            self.assertEqual(wand['external']['fields']['charges']['value'], 7)
            self.assertEqual(wand['external']['fields']['recharge']['value'], 'dawn')
            self.assertEqual(next(r for r in wand['comparisons'] if r['dimension']=='resource')['outcome'], 'anna_missing')
            self.assertEqual(wand['anna']['records'][0]['status'], 'partial')
        staff = self.case('Staff of Healing','XDMG')
        self.assertIn('will_does_not_verify_cost', [g['reason'] for g in staff['external']['gaps']])
        self.assertIn('每法术等级1次充能', json.dumps(staff['external']['text_evidence'],ensure_ascii=False))

    def test_all_external_evidence_pointers_reproduce_raw_values(self):
        docs = {}
        def check(value):
            if isinstance(value, dict):
                if {'locator','raw'} <= value.keys():
                    file, pointer = value['locator'].split('#',1)
                    if file not in docs: docs[file] = json.loads((FIVETOOLS/file).read_text())
                    self.assertEqual(pointer_get(docs[file], pointer), value['raw'], value['locator'])
                for v in value.values(): check(v)
            elif isinstance(value,list):
                for v in value: check(v)
        check(self.report)


if __name__ == '__main__':
    unittest.main()
