"""Evidence transport, strict contracts and actual Soufflé acceptance (no mock evaluator)."""
import copy
import csv
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import datalog as d
from datalog_sources import DatalogError, bind_sources, locate, read_json, within
from html_extract import Document
from datalog_report import render_report

LIBRARY = Path(__file__).resolve().parents[1] / 'rules/core'


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.library = d.load_library(LIBRARY)

    def error(self, code, fn, *args, **kwargs):
        with self.assertRaises(DatalogError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_source_exact_relocation_ambiguity_and_change(self):
        selector = {'quote': '规则', 'context': '甲规则乙', 'span': [1, 3]}
        self.assertEqual(locate('甲规则乙', selector), (1, 3, 'same_range'))
        self.assertEqual(locate('新甲规则乙', selector), (2, 4, 'relocated'))
        self.error('source_ambiguous', locate, '甲规则乙甲规则乙', selector)
        self.error('source_changed', locate, '甲新规则乙', selector)
        self.error('invalid_selector', locate, 'abc', {'quote': '', 'context': ''})

    def test_json_and_paths_reject_conflicts_and_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path in ['../outside', '/etc/passwd', 'a/../b', 'a\\b', 'a//b']:
                self.error('invalid_path', within, root, path)
            (root / 'bad.json').write_text('{"a":1,"a":2}')
            self.error('duplicate_key', read_json, root / 'bad.json')
            (root / 'bad.json').write_text('{"a":NaN}')
            self.error('invalid_number', read_json, root / 'bad.json')

    def test_input_types_arity_bounds_editions_and_derived_injection(self):
        schema = self.library['schema']
        valid = ['5r', 'actor', 'str', 14]
        d.validate_tuple(schema, 'AbilityScore', valid, input_kind='scenario')
        for values in [valid[:-1], ['5r','actor','str',True], ['5r','actor','str',31], ['5r','actor','luck',10]]:
            with self.assertRaises(DatalogError):d.validate_tuple(schema, 'AbilityScore', values)
        self.error('invalid_relation', d.validate_tuple, schema, 'Outcome', ['5r','test','success'], input_kind='scenario')
        self.error('unsupported_relation_edition', d.validate_tuple, schema, 'Rank', ['5e','actor',1])
        for bad in ['x\nquit', 'quote"', 'backslash\\', '']:
            self.error('invalid_value', d.validate_tuple, schema, 'Roll', ['5r',bad,1,0])

    def test_conflicting_inputs_and_claim_review(self):
        c = {'id':'conflict','facts':{'AbilityScore':[['5r','a','str',10],['5r','a','str',11]]}}
        self.error('conflicting_fact', d.collect_facts, self.library, [c])
        c['facts']['AbilityScore'][1][-1] = 10
        rows, origins = d.collect_facts(self.library, [c])
        self.assertEqual(len(rows['AbilityScore']), 1)
        self.assertEqual(len(origins[d.fact_key('AbilityScore',['5r','a','str',10])]), 2)
        library = copy.deepcopy(self.library)
        library['facts'][0]['review_status'] = 'reviewed'
        self.error('review_evidence_required', d.collect_facts, library, [])

    def test_malformed_scenarios_fail_with_structured_errors(self):
        for value,code in [(None,'invalid_case_id'),({'id':3},'invalid_case_id'),
                           ({'id':'x','facts':[]},'invalid_facts'),
                           ({'id':'x','expect':{}},'invalid_queries'),
                           ({'id':'x','explain':[{}]},'invalid_query'),
                           ({'id':'x','fact':{}},'unknown_case_field')]:
            self.error(code,d.collect_facts,self.library,[value])

    def test_rule_ids_and_edition_origins_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'lib';shutil.copytree(LIBRARY, root)
            rules = read_json(root / 'rules.json')
            rules['ability.modifier']['sources']['5e'] = ['ability']
            d.write_json(root / 'rules.json', rules)
            self.error('invalid_source_reference', d.load_library, root)
        schema = self.library['schema']
        self.error('invalid_clause', d.instrument, 'unsafe', '.input Surprise.\nX(E) :- Y(E).', schema)
        self.error('invalid_clause_head', d.instrument, 'unsafe', 'AbilityScore(E,A,B,10) :- Rank(E,A,1).', schema)
        self.error('invalid_clause', d.instrument, 'unsafe', 'ProficiencyBonus(E,A,2) :- Rank(E,A,1). ProficiencyBonus(E,A,9) :- Rank(E,A,30).', schema)
        self.error('cross_edition_clause', d.instrument, 'unsafe', 'ProficiencyBonus(E,A,2) :- Rank("5e",A,1).', schema)

    def test_output_is_transactional_and_owned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);target = root / 'bundle';target.mkdir();(target / 'keep').write_text('user')
            with self.assertRaises(DatalogError):
                with d.output_directory(target):pass
            d.write_json(target / 'manifest.json', {'producer':'anna-datalog'})
            with self.assertRaises(RuntimeError):
                with d.output_directory(target) as stage:
                    (stage / 'replacement').write_text('new')
                    raise RuntimeError('engine failed')
            self.assertEqual((target / 'keep').read_text(), 'user')
            with self.assertRaises(DatalogError):
                with d.output_directory(root, (target,)):pass

    def test_report_escapes_content_and_has_localized_navigation(self):
        labels=read_json(d.PROJECT/'scripts/datalog_report_labels.json')
        mapping={'rules':{},'sources':{},'fact_origins':{}}
        for lang in ('zh','en'):
            report=render_report({'counts':{},'snapshot':'<script>alert(1)</script>'},mapping,{},[],{},labels[lang],lang)
            self.assertNotIn('<script>',report)
            self.assertIn('&lt;script&gt;',report)
            self.assertIn(labels[lang]['title'],report)
            self.assertIn('href="#sources"',report)

    def test_fixture_binding_uses_canonical_offsets_and_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.mkdir();file=source/'page.htm'
            raw='<html><body><h1>规则</h1><p>甲规则乙</p></body></html>'
            file.write_text(raw);doc=Document('page.htm',raw);text=doc.text;doc.close()
            db=root/'catalog.sqlite'
            with closing(sqlite3.connect(db)) as conn:
                conn.executescript('CREATE TABLE pages(path TEXT,text TEXT,sha256 TEXT); CREATE TABLE entries(page_path TEXT,data TEXT);')
                conn.execute('INSERT INTO pages VALUES(?,?,?)',('page.htm',text,sha256(file.read_bytes()).hexdigest()))
                entry={'id':'entry','start':0,'end':len(text),'ruleset':'5r','snapshot':'source-commit'}
                conn.execute('INSERT INTO entries VALUES(?,?)',('page.htm',json.dumps(entry)))
                conn.commit()
            selectors={'s':{'edition':'5r','path':'page.htm','quote':'甲规则乙','context':'甲规则乙','span':[0,4]}}
            bound,_=bind_sources(selectors,db,source,'http://127.0.0.1:8000')
            b=bound['s'];self.assertEqual(text[slice(*b['span'])],'甲规则乙');self.assertEqual(b['location_status'],'relocated');self.assertEqual(b['entry_ids'],['entry'])
            self.assertIn('sha256=', b['links']['evidence']);self.assertIn('/read?page=',b['links']['reader'])
            selectors['s']['edition']='5e'
            self.error('source_edition_mismatch',bind_sources,selectors,db,source,'http://localhost:8000')
            selectors['s']['edition']='5r';file.write_text(raw+'<!--changed-->')
            self.error('catalog_source_mismatch',bind_sources,selectors,db,source,'http://localhost:8000')
            # Simulate a rebuild after a source insertion: the quote is rebound,
            # not forced onto its old position, and the old page hash expires.
            updated=raw.replace('<h1>', '<p>新前言</p><h1>');file.write_text(updated)
            doc=Document('page.htm',updated);updated_text=doc.text;doc.close()
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('UPDATE pages SET text=?,sha256=?',(updated_text,sha256(file.read_bytes()).hexdigest()))
                conn.commit()
            rebound,_=bind_sources(selectors,db,source,'http://localhost:8000')
            self.assertNotEqual(b['page_sha256'],rebound['s']['page_sha256'])
            self.assertGreater(rebound['s']['span'][0],b['span'][0])
            self.assertEqual(rebound['s']['raw'],b['raw'])


@unittest.skipUnless(shutil.which('souffle'), 'Install Soufflé 2.5 to run actual engine acceptance')
class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = d.load_library(LIBRARY)
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def evaluate(self, cases, name):
        directory=self.root/name;directory.mkdir();(directory/'facts').mkdir();(directory/'results').mkdir()
        (directory/'program.dl').write_text(self.library['program'])
        rows, origins=d.collect_facts(self.library,cases)
        for relation,spec in self.library['schema']['relations'].items():
            if spec.get('input'):
                with (directory/'facts'/(relation+'.facts')).open('w',newline='') as file:
                    csv.writer(file,lineterminator='\n').writerows(rows[relation].values())
        d.invoke_engine('souffle',directory)
        return directory,d.load_results(directory,self.library['schema']),origins

    def test_all_authored_cases_and_actual_proof_links(self):
        cases=d.selected_cases(self.library)
        directory,results,origins=self.evaluate(cases,'acceptance')
        self.assertEqual(results['Violation'],[])
        queries=d.check_expectations(cases,results,self.library['schema'])
        proofs=d.explain('souffle',directory,queries,self.library['schema'],origins,self.library['rule_map'])
        self.assertGreaterEqual(len(cases),60);self.assertGreaterEqual(len(proofs),60)
        for proof in proofs:
            self.assertTrue(proof['rule_ids']);self.assertTrue(proof['source_ids']);self.assertTrue(proof['input_origins'])
            self.assertEqual(proof['review_statuses'],['unreviewed'])
        negative=next(p for p in proofs if p['case_ids']==['ability.table'])
        self.assertEqual(negative['values'][-1],-5)
        self.assertEqual(negative['rule_ids'],['ability.modifier'])
        critical=next(p for p in proofs if p['relation']=='DamageDiceRequired')
        self.assertIn('critical.dice',critical['rule_ids']);self.assertIn('condition.paralyzed',critical['source_ids'])
        self.assertIn('condition.close.critical',critical['rule_ids'])

    def test_missing_and_inconsistent_scenarios(self):
        original=next(c for c in self.library['cases']['cases'] if c['id']=='training.proficient')
        variants=[]
        for relation,code in [('AbilityScore','missing_ability'),('Roll','missing_roll'),('Rank','missing_rank')]:
            c=copy.deepcopy(original);c['facts'].pop(relation)
            variants.append((c,code))
        attack=copy.deepcopy(next(c for c in self.library['cases']['cases'] if c['id']=='attack.twenty'))
        attack['facts'].pop('Visibility');variants.append((attack,'missing_visibility'))
        second=copy.deepcopy(next(c for c in self.library['cases']['cases'] if c['id']=='advantage'))
        second['facts']['Roll'][0][-1]=0;variants.append((second,'missing_second_die'))
        orphan={'id':'orphan','facts':{'Roll':[['5r','missing-test',1,0]]}};variants.append((orphan,'orphan_Roll'))
        wrong_kind=copy.deepcopy(next(c for c in self.library['cases']['cases'] if c['id']=='attack.twenty'))
        wrong_kind['facts']['Test'][0][3]='check';variants.append((wrong_kind,'invalid_attack_test'))
        variants.append(({'id':'healing.missing','facts':{'Healing':[['5r','heal',3]]}},'missing_hit_points'))
        variants.append(({'id':'dodge.missing','facts':{'DodgeActive':[['5r','actor']]}},'missing_speed'))
        variants.append(({'id':'charm.missing','facts':{'Condition':[['5r','actor','charmed']]}},'missing_condition_source'))
        for i,(case,code) in enumerate(variants):
            with self.subTest(code=code):
                _,results,_=self.evaluate([case],f'invalid-{i}')
                self.assertIn(code,[v[2] for v in results['Violation']])

    def test_unicode_and_comma_symbols_round_trip_through_provenance(self):
        actor='逗号, 带空格的标识'
        c={'id':'quoted','facts':{'AbilityScore':[['5r',actor,'str',1]]},'explain':[{'relation':'AbilityModifier','values':['5r',actor,'str',-5]}]}
        directory,results,origins=self.evaluate([c],'quoted')
        queries=d.check_expectations([c],results,self.library['schema'])
        proof=d.explain('souffle',directory,queries,self.library['schema'],origins,self.library['rule_map'])[0]
        self.assertEqual(proof['values'],['5r',actor,'str',-5])

    @unittest.skipUnless((d.PROJECT/'data/extracted/catalog.sqlite').exists() and d.WEB.exists(), 'Build the source catalog first')
    def test_real_corpus_bundle_and_tamper_detection(self):
        output=self.root/'bound'
        manifest=d.build(output=output)
        self.assertEqual(manifest['acceptance']['status'],'passed')
        self.assertEqual(manifest['review_status'],'unreviewed')
        self.assertEqual(d.verify_bundle(output)['snapshot'],manifest['snapshot'])
        mapping=read_json(output/'source-map.json')
        for source in mapping['sources'].values():
            self.assertEqual(source['quote'],source['raw'])
            self.assertIn('sha256=',source['links']['evidence'])
        proof=next(p for p in read_json(output/'explanations.json')['explanations'] if p['relation']=='DamageDiceRequired')
        self.assertIn('condition.paralyzed',proof['source_ids'])
        report=(output/'report.html').read_text()
        self.assertIn('id="source-condition.paralyzed"',report)
        self.assertIn('href="#rule-critical.dice"',report)
        (output/'results.json').write_text('{}')
        with self.assertRaises(DatalogError) as caught:d.verify_bundle(output)
        self.assertEqual(caught.exception.code,'artifact_mismatch')


if __name__=='__main__':unittest.main()
