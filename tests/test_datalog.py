"""Evidence transport, strict contracts and actual Soufflé acceptance (no mock evaluator)."""
import copy
import csv
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

import anna.datalog.runtime as d
from anna.datalog.sources import DatalogError, bind_entries, bind_sources, locate, read_json, within, knowledge_value
from anna.source.html import Document
from anna.datalog.report import render_report

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
        self.error('unsupported_relation_edition', d.validate_tuple, schema, 'Exhaustion', ['5e','actor',1])
        for relation, values in [('Condition',['5e','actor','blinded']),
                                 ('Turn',['5e','actor',0,0,0]), ('Damage',['5e','event','actor','fire',1,0])]:
            self.error('unsupported_relation_edition',d.validate_tuple,schema,relation,values)
        self.error('invalid_value',d.validate_tuple,schema,'DamageDiceInput',['5r','attack','weapon',0,4])
        self.error('invalid_relation',d.validate_tuple,schema,'Test',
                   ['5r','test','actor','check','str',10,0,'none'],input_kind='scenario')
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
        labels=read_json(d.PROJECT/'anna/datalog/labels.json')
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

    def test_entry_binding_identity_revision_and_knowledge_values(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp)/'catalog.sqlite'
            entry = {'id':'entry', 'ruleset':'5r', 'type':'spell', 'body_hash':'body',
                     'fields':{'duration':'8 hours'}, 'name':'Mage Armor', 'identity_status':'provisional'}
            record = {'id':'knowledge', 'subject_id':'entry', 'ruleset':'5r', 'status':'partial',
                      'review_status':'unreviewed', 'value':{'parameters':{'duration':{
                          'quantity':8, 'kind':'timed', 'unit':'hour', 'concentration':False,
                          'evidence':{'path':'page', 'raw':'8 hours', 'span':[0,7]}}}}}
            raw = json.dumps(record)
            selector = {'edition':'5r','type':'spell','body_hash':'body','fields':entry['fields'],
                        'sources':['source'],'knowledge':{'knowledge':sha256(raw.encode()).hexdigest()}}
            sources = {'source':{'edition':'5r','entry_ids':['entry']}}
            with closing(sqlite3.connect(db)) as conn:
                conn.executescript('CREATE TABLE entries(id TEXT,data TEXT); CREATE TABLE effective_knowledge(id TEXT,data TEXT);')
                conn.execute('INSERT INTO entries VALUES(?,?)',('entry',json.dumps(entry)))
                conn.execute('INSERT INTO effective_knowledge VALUES(?,?)',('knowledge',raw));conn.commit()
            bound = bind_entries({'entry':selector},sources,db)
            self.assertEqual(bound['entry']['knowledge'][0]['value']['parameters']['duration']['quantity'],8)
            self.assertEqual(bound['entry']['identity_status'],'provisional')
            library = copy.deepcopy(self.library)
            library['sources']['source'] = {'edition':'5r'}
            library['entries'] = {'entry':selector}
            library['facts'] = [{'id':'test.spell','relation':'ArmorSpellProfile',
                'values':['5r','entry',13,8], 'sources':['source'], 'entry_id':'entry',
                'review_status':'unreviewed', 'knowledge_bindings':[{'knowledge_id':'knowledge',
                    'path':['parameters','duration','quantity'],'value_index':3},
                    {'knowledge_id':'knowledge','path':['parameters','duration','unit'],'expected':'hour'}]}]
            _, origins = d.collect_facts(library,[],bound)
            refs = origins[d.fact_key('ArmorSpellProfile',['5r','entry',13,8])]
            self.assertEqual({x['kind'] for x in refs},{'claim','entry','knowledge_value'})
            library['facts'][0]['values'][3] = 9
            self.error('knowledge_value_mismatch',d.collect_facts,library,[],bound)
            for field,value,code in [('edition','5e','entry_edition_mismatch'),
                                      ('body_hash','changed','entry_changed'),
                                      ('fields',{},'entry_changed')]:
                wrong = {**selector,field:value}
                self.error(code,bind_entries,{'entry':wrong},sources,db)
            self.error('entry_missing',bind_entries,{'unknown':selector},sources,db)
            self.error('entry_evidence_mismatch',bind_entries,{'entry':selector},
                       {'source':{'edition':'5r','entry_ids':['different-same-name']}},db)
            for status,review in [('partial','applied'),('rejected','applied'),('review','stale'),('review','conflict')]:
                changed = {**record,'status':status,'review_status':review,'decision_ids':['decision']}
                with closing(sqlite3.connect(db)) as conn:
                    conn.execute('UPDATE effective_knowledge SET data=?',(json.dumps(changed),));conn.commit()
                self.error('entry_knowledge_changed',bind_entries,{'entry':selector},sources,db)
                inspected={**selector,'knowledge':{'knowledge':sha256(json.dumps(changed).encode()).hexdigest()}}
                if status=='partial':
                    accepted=bind_entries({'entry':inspected},sources,db)['entry']['knowledge'][0]
                    self.assertEqual(accepted['decision_ids'],['decision'])
                    self.assertEqual(accepted['review_status'],'applied')
                else:self.error('entry_knowledge_changed',bind_entries,{'entry':inspected},sources,db)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('DELETE FROM effective_knowledge');conn.commit()
            self.error('entry_knowledge_missing',bind_entries,{'entry':selector},sources,db)

    def test_local_statement_binding_is_id_scoped_and_retains_evidence(self):
        evidence={'path':'page','span':[1,4],'raw':'规则文'}
        branch={'id':'branch_1','target':{'kind':'self'},'evidence':[evidence]}
        record={'id':'knowledge','value':{'grants':[{'id':'grant_1','branches':[branch]}]},'evidence':evidence}
        path=['grants',{'id':'grant_1'},'branches',{'id':'branch_1'},'target','kind']
        self.assertEqual(knowledge_value(record,path),('self',[evidence]))
        record['value']['grants'][0]['branches'].append(copy.deepcopy(branch))
        self.error('knowledge_path_unresolved',knowledge_value,record,path)
        record['value']['grants'][0]['branches']=[]
        self.error('knowledge_path_unresolved',knowledge_value,record,path)
        library=copy.deepcopy(self.library)
        claim=next(c for c in library['facts'] if c.get('knowledge_bindings'))
        for change in [{'path':[True]},{'path':[{'name':'effect_1'}]},{'path':[]},
                       {'value_index':True},{'value_index':10000}]:
            binding=copy.deepcopy(claim['knowledge_bindings'][0])
            claim['knowledge_bindings'][0].update(change)
            self.error('invalid_knowledge_binding',d.collect_facts,library,[])
            claim['knowledge_bindings'][0]=binding


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

    def test_cast_contract_rejects_incomplete_conflicting_and_cross_edition_runs(self):
        base=next(c for c in d.selected_cases(self.library) if c['id']=='cast.5r.duration')
        cases=[];expected=[]
        def changed(name,code,edit):
            run='invalid.cast.'+name
            case=json.loads(json.dumps(base).replace(base['id'],run))
            for field in ('expect','absent','explain'):case[field]=[]
            edit(case['facts']);cases.append(case);expected.append(['5r',run,code])
        changed('resources','missing_cast_resources',lambda f:f.pop('CastResources'))
        changed('readiness','missing_cast_readiness',lambda f:f.pop('CastReadiness'))
        changed('ability','missing_cast_ability',lambda f:f.pop('AbilityScore'))
        changed('branch','unsupported_cast_branch',lambda f:f['CastRequest'][0].__setitem__(5,'missing_branch'))
        changed('edition','unsupported_cast_branch',lambda f:f['CastRequest'][0].__setitem__(3,'ver_205593d20d0c37015ecd0ab1'))
        changed('gap','missing_cast_step',lambda f:f['CastFollowup'].pop(0))
        changed('time','cast_time_reversed',lambda f:f['CastFollowup'][1].__setitem__(3,2))
        changed('turn','cast_turn_reversed',lambda f:f['CastFollowup'][1].__setitem__(4,0))
        changed('orphan','orphan_cast_followup',lambda f:f.pop('CastRequest'))
        changed('snapshot','mixed_cast_snapshot',lambda f:f.update(ArmorSpell=[['5r','old','ver_992b31e3033fc968022d586c',f['CastRequest'][0][2],0,0]]))
        _,results,_=self.evaluate(cases,'cast-invalid')
        for row in expected:self.assertIn(row,results['Violation'])
        for name in ('branch','edition','resources','readiness'):
            self.assertNotIn(['5r','invalid.cast.'+name],results['CastAllowed'])
        conflict=copy.deepcopy(base)
        conflict['facts']['CastReadiness'].append(copy.deepcopy(conflict['facts']['CastReadiness'][0]))
        conflict['facts']['CastReadiness'][-1][6]=1
        with self.assertRaises(DatalogError) as caught:d.collect_facts(self.library,[conflict])
        self.assertEqual(caught.exception.code,'conflicting_fact')
        for relation,values in [('CastState',['5r','injected',0,1,0,1]),('CastAllowed',['5r','injected'])]:
            with self.assertRaises(DatalogError):d.validate_tuple(self.library['schema'],relation,values,input_kind='scenario')

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
        wrong_kind['facts']['TestInput'][0][3]='check';variants.append((wrong_kind,'invalid_attack_test'))
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

    def test_input_dependencies_reject_missing_conflicting_and_cross_edition_values(self):
        def case(name):
            return copy.deepcopy(next(c for c in self.library['cases']['cases'] if c['id']==name))
        variants=[]
        for relation,code in [('WeaponReady','missing_weapon_readiness'),('AbilityScore','missing_ability'),
                              ('ArmorLoadout','missing_target_ac'),('Rank','missing_rank')]:
            value=case('inputs.5r.dagger')
            if relation=='AbilityScore':value['facts'][relation].pop(0)
            else:value['facts'].pop(relation)
            variants.append((value,code))
        value=case('inputs.5r.dagger');value['facts']['WeaponUse'][0][3]='ver_ba7662556535b8923728ab01'
        variants.append((value,'unsupported_weapon'))
        value=case('inputs.5r.dagger');value['facts']['WeaponUse'][0][6]='default'
        variants.append((value,'missing_or_invalid_weapon_ability'))
        value=case('inputs.5r.dagger');value['facts']['TestInput']=[['5r',value['facts']['WeaponUse'][0][1],
            value['facts']['WeaponUse'][0][2],'attack','dex',1,0,'none']]
        variants.append((value,'conflicting_test_request'))
        value=case('inputs.5r.dagger');value['facts']['WeaponProficiency'][0][2]='ver_ba7662556535b8923728ab01'
        variants.append((value,'unsupported_weapon_proficiency'))
        value=case('inputs.5r.skill_tool');value['facts']['SkillCheck'][0][3]='unknown'
        variants.append((value,'unsupported_skill'))
        value=case('inputs.5e.small_strong');value['facts'].pop('ActorSize');variants.append((value,'missing_actor_size'))
        value=case('inputs.5r.spell_dc');value['facts']['CastingSource'][0][2]='ver_f68f2241cc9fd6a17aefead9'
        variants.append((value,'unsupported_casting_source'))
        value=case('inputs.5r.spell_dc');value['facts'].pop('CastingSource');variants.append((value,'missing_casting_source'))
        value=case('inputs.5r.mage_active');value['facts']['ArmorSpell'][0][-1]=1
        variants.append((value,'unsupported_spell_dismissal'))
        value=case('inputs.5r.mage_active');value['facts'].pop('ArmorLoadout');variants.append((value,'missing_armor_loadout'))
        value=case('inputs.5r.mage_active');other=value['facts']['ArmorSpell'][0].copy();other[1]+='-other'
        value['facts']['ArmorSpell'].append(other);variants.append((value,'multiple_armor_spells'))
        value=case('inputs.5e.armor_leather');value['facts']['ArmorLoadout'][0][3]=0
        variants.append((value,'unresolved_2014_armor_training'))
        value=case('inputs.5e.armor_leather');value['facts']['ArmorLoadout'][0][-1]=0
        variants.append((value,'unresolved_2014_shield_training'))
        for i,(value,code) in enumerate(variants):
            with self.subTest(code=code):
                _,result,_=self.evaluate([value],f'input-reject-{i}')
                self.assertIn(code,[v[2] for v in result['Violation']])

    def test_annotation_change_propagates_and_missing_inputs_do_not_borrow_other_editions(self):
        case=copy.deepcopy(next(c for c in self.library['cases']['cases'] if c['id']=='inputs.5r.mage_target'))
        _,before,_=self.evaluate([case],'before-annotation')
        original=self.library
        try:
            self.library=copy.deepcopy(original)
            claim=next(f for f in self.library['facts'] if f['id']=='5r.entry.mage_armor')
            claim['values'][2]=14
            _,after,_=self.evaluate([case],'after-annotation')
            self.assertEqual(before['ArmorClass'],[['5r','5r-mage-defender',15]])
            self.assertEqual(after['ArmorClass'],[['5r','5r-mage-defender',16]])
            self.assertEqual(before['Outcome'],[['5r','5r-mage-attacker-attack','success']])
            self.assertEqual(after['Outcome'],[['5r','5r-mage-attacker-attack','failure']])
            self.assertEqual(before['TestTotal'],after['TestTotal'])
            self.library['facts'].remove(claim)
            _,missing,_=self.evaluate([case],'without-annotation')
            self.assertIn('unsupported_armor_spell',[v[2] for v in missing['Violation']])
            self.assertEqual(missing['ArmorSpellActive'],[])
        finally:self.library=original

    @unittest.skipUnless((d.PROJECT/'data/extracted/catalog.sqlite').exists() and d.WEB.exists(), 'Build the source catalog first')
    def test_real_corpus_bundle_and_tamper_detection(self):
        output=self.root/'bound'
        manifest=d.build(output=output)
        self.assertEqual(manifest['acceptance']['status'],'passed')
        self.assertEqual(manifest['review_status'],'unreviewed')
        self.assertEqual(d.verify_bundle(output)['snapshot'],manifest['snapshot'])
        mapping=read_json(output/'source-map.json')
        self.assertEqual(len(mapping['entries']),34)
        for source in mapping['sources'].values():
            self.assertEqual(source['quote'],source['raw'])
            self.assertIn('sha256=',source['links']['evidence'])
        proof=next(p for p in read_json(output/'explanations.json')['explanations'] if p['relation']=='DamageDiceRequired')
        self.assertIn('condition.paralyzed',proof['source_ids'])
        mage=next(p for p in read_json(output/'explanations.json')['explanations'] if p['case_ids']==['inputs.5r.mage_active'])
        self.assertEqual(mage['entry_origins'][0]['entry_id'],'ver_992b31e3033fc968022d586c')
        self.assertIn('knowledge_value',json.dumps(mage['proof']))
        attack=next(p for p in read_json(output/'explanations.json')['explanations'] if p['case_ids']==['inputs.5r.mage_target'])
        self.assertEqual({e['entry_id'] for e in attack['entry_origins']},
                         {'ver_96fa837d8dcbbd58d610d900','ver_992b31e3033fc968022d586c'})
        self.assertTrue(all(mapping['sources'][s]['edition']=='5r' for s in attack['source_ids']))
        cast=next(p for p in read_json(output/'explanations.json')['explanations']
                  if p['case_ids']==['cast.5r.duration'] and p['relation']=='CastBaseAC')
        self.assertEqual({e['entry_id'] for e in cast['entry_origins']},
                         {'ver_3d28cd45188a9be58b8bece0','ver_992b31e3033fc968022d586c'})
        self.assertTrue(all(mapping['sources'][s]['edition']=='5r' for s in cast['source_ids']))
        self.assertIn('knowledge_value',json.dumps(cast['proof']))
        report=(output/'report.html').read_text()
        self.assertIn('id="source-condition.paralyzed"',report)
        self.assertIn('href="#rule-critical.dice"',report)
        (output/'results.json').write_text('{}')
        with self.assertRaises(DatalogError) as caught:d.verify_bundle(output)
        self.assertEqual(caught.exception.code,'artifact_mismatch')


if __name__=='__main__':unittest.main()
