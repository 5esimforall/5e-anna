"""Knowledge observations, review replay and branch-scoped public consumers."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from knowledge import (build_knowledge, chain, Context, inspect, save, casting_condition,
                       validate_record, record_for, parse_access, branches)
from review import revoke
from review_store import load_events, append_event, locked, extraction_hash, canonical


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.log=self.root/'reviews.jsonl';self.db=None
        self.feature=('示例 Example\n你习得测试法术。你可以在不消耗法术位的情况下施展这些法术各一次。'
                      '当你以此法施展某个法术后，你不能再次以此法施展该法术，直至你完成一次长休。'
                      '你也可以消耗合适的法术位来施展这些法术。')
        self.spell=('测试法术 Test Spell\n施法时间：附赠动作\n施法距离：自身\n法术成分：V\n持续时间：立即\n'
                    '你短暂地被银白的雾气所笼罩，传送到至多30尺内一个你能看见且未占据空间。')
        self.other='无关文字';self.competitor=False;self.rules=[];self.rebuild()

    def tearDown(self):
        if self.db:self.db.close()
        self.tmp.cleanup()

    def rebuild(self):
        if self.db:self.db.close()
        self.db=sqlite3.connect(':memory:')
        self.db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE entries(id TEXT PRIMARY KEY,name TEXT,type TEXT,ruleset TEXT,page_path TEXT,start INTEGER,end INTEGER,data TEXT);
            CREATE TABLE pages(path TEXT PRIMARY KEY,text TEXT);
            CREATE TABLE sources(id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE review_queue(id TEXT PRIMARY KEY,target TEXT,reason TEXT,priority TEXT,data TEXT);
        ''')
        source={'id':'phb','name':'核心','ruleset_hint':'5r','reference_role':'core_rules','evidence':[]}
        self.db.execute('INSERT INTO sources VALUES(?,?)',('phb',canonical(source)))
        entries=[]
        for eid,body,name,english,kind in [('feat',self.feature,'示例','Example','feat'),('spell',self.spell,'测试法术','Test Spell','spell'),('other',self.other,'无关','Other','section'),*self.rules]:
            entry={'id':eid,'entity_id':eid,'name':name+' '+english,'name_zh':name,'name_en':english,'type':kind,'ruleset':'5r',
                   'page_path':eid+'.htm','start':0,'title_end':body.find('\n')+1,'end':len(body),'source_ids':['phb'],
                   'fields':{},'anchors':[],'is_primary':True}
            entries.append(entry);self.db.execute('INSERT INTO pages VALUES(?,?)',(entry['page_path'],body))
        if self.competitor:entries.append({**entries[1],'id':'competitor','entity_id':'competitor'})
        for e in entries:self.db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?)',(e['id'],e['name'],e['type'],e['ruleset'],e['page_path'],e['start'],e['end'],canonical(e)))
        self.coverage=build_knowledge(self.db,self.root,self.log)
        events,log_hash=load_events(self.log)
        self.db.execute('INSERT INTO metadata VALUES(?,?)',('summary',canonical({'extraction_sha256':extraction_hash()})))
        self.db.execute('INSERT INTO metadata VALUES(?,?)',('review_snapshot',canonical({'path':str(self.log),'sha256':log_hash,'events':len(events),'bytes':self.log.stat().st_size if self.log.exists() else 0})))
        self.db.commit()
        self.rid=self.db.execute("SELECT id FROM rule_knowledge WHERE subject_id='feat' AND kind='spell_access'").fetchone()[0]

    def accept(self,operation='knowledge_accept',supersedes=None):
        view=inspect(self.db,self.rid)
        return save(self.db,self.log,self.rid,operation,view['basis_sha256'],'fixture','source checked',supersedes)

    def effective(self):return json.loads(self.db.execute('SELECT data FROM effective_knowledge WHERE id=?',(self.rid,)).fetchone()[0])

    def test_accept_reject_revoke_replay_without_mutating_observations(self):
        original=self.db.execute('SELECT data FROM rule_knowledge WHERE id=?',(self.rid,)).fetchone()[0]
        event=self.accept();self.rebuild()
        self.assertEqual(self.effective()['review_status'],'applied');self.assertEqual(len(chain(self.db,'feat')['options']),2)
        rejected=self.accept('knowledge_reject',event['id']);self.rebuild()
        self.assertEqual(self.effective()['status'],'rejected');self.assertFalse(chain(self.db,'feat')['options'])
        self.assertEqual(self.db.execute('SELECT data FROM rule_knowledge WHERE id=?',(self.rid,)).fetchone()[0],original)
        revoke(self.log,rejected['id'],'fixture','undo rejection');self.rebuild()
        self.assertEqual(self.effective()['review_status'],'revoked');self.assertEqual(len(chain(self.db,'feat')['options']),2)
        self.assertEqual(len(load_events(self.log)[0]),3)

    def test_relevant_changes_reopen_and_unrelated_changes_do_not(self):
        self.accept();self.other+=' unrelated update';self.rebuild()
        self.assertEqual(self.effective()['review_status'],'applied')
        self.spell=self.spell.replace('30尺','60尺');self.rebuild()
        self.assertEqual(self.effective()['review_status'],'stale');self.assertFalse(chain(self.db,'feat')['options'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM review_queue WHERE reason='knowledge_decision_stale'").fetchone()[0],1)

    def test_new_competing_identity_reopens_approval(self):
        self.accept();self.competitor=True;self.rebuild()
        self.assertEqual(self.effective()['review_status'],'stale')
        target=self.effective()['value']['grants'][0]['target']['resolution']
        self.assertEqual(target['status'],'ambiguous');self.assertEqual(set(target['targets']),{'spell','competitor'})
        self.assertFalse(chain(self.db,'feat')['options'])

    def test_competing_reviews_block_consumption(self):
        first=self.accept()
        with locked(self.log):
            _,version=load_events(self.log)
            append_event(self.log,{k:v for k,v in first.items() if k not in {'id','schema','created_at'}},version)
        self.rebuild()
        self.assertEqual(self.effective()['review_status'],'conflict');self.assertFalse(chain(self.db,'feat')['options'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM knowledge_decisions WHERE status='conflict'").fetchone()[0],2)

    def test_inspection_basis_is_required_for_writes(self):
        with self.assertRaisesRegex(ValueError,'basis'):
            save(self.db,self.log,self.rid,'knowledge_accept','wrong','fixture','source')
        self.assertFalse(self.log.exists())
        self.accept()
        with self.assertRaisesRegex(ValueError,'Supersede'):self.accept()

    def test_same_branch_query_does_not_mix_free_recovery_with_slots(self):
        def count(q):
            where,values=casting_condition(q)
            return self.db.execute('SELECT count(*) FROM entries e WHERE '+where,values).fetchone()[0]
        self.assertEqual(count({'resource':'slot_free','recovery':'long_rest','spell':'测试法术'}),1)
        self.assertEqual(count({'resource':'spell_slot','recovery':'long_rest'}),0)
        self.assertEqual(count({'resource':'spell_slot','recovery':'not_stated'}),1)
        self.assertEqual(chain(self.db,'spell')['incoming'][0]['entry_id'],'feat')

    def test_unknown_qualifier_remains_partial_and_not_a_false_positive(self):
        self.feature=self.feature.replace('你习得测试法术。','你习得测试法术，但只能对龙使用。');self.rebuild()
        self.assertEqual(self.effective()['status'],'partial');self.assertFalse(chain(self.db,'feat')['options'])
        self.assertTrue(self.effective()['value']['unparsed'])
        self.accept();self.rebuild()
        self.assertEqual(self.effective()['review_status'],'applied')
        self.assertEqual(self.effective()['status'],'partial')
        self.assertFalse(chain(self.db,'feat')['options'])

    def test_audit_detects_export_and_projection_corruption(self):
        from knowledge import audit_knowledge, knowledge_diff
        self.assertEqual(audit_knowledge(self.db,self.root)['errors'],[])
        self.assertEqual(knowledge_diff(self.db,self.db),[])
        self.db.execute("UPDATE knowledge_rule_links SET relation='wrong'")
        self.assertIn('knowledge_rule_links: projection mismatch',audit_knowledge(self.db,self.root)['errors'])
        (self.root/'effective_knowledge.jsonl').write_text('')
        self.db.execute('DELETE FROM casting_options')
        self.db.execute('DELETE FROM knowledge_rule_links')
        errors=audit_knowledge(self.db,self.root)['errors']
        self.assertIn('effective_knowledge: export mismatch',errors)
        self.assertIn('casting_options: projection mismatch',errors)
        self.assertIn('knowledge_rule_links: projection mismatch',errors)

    def test_path_preserves_root_and_validates_every_hop(self):
        result=chain(self.db,'feat','spell')
        self.assertEqual(result['reading']['path'],['feat','spell'])
        self.assertEqual(result['reading']['root_id'],'feat')
        self.assertIn('传送',result['reading']['text'])
        self.assertEqual(result['reading']['steps'][0]['relations'][0]['relation'],'grants_spell')
        self.assertEqual(chain(self.db,'feat','spell',['feat'])['reading']['focus_id'],'feat')
        for bad in [['spell'],['feat','other'],['feat','spell','feat'],{},['feat',1],['x']*17,'invalid JSON']:
            with self.subTest(path=bad),self.assertRaises(ValueError):chain(self.db,'feat',reading_path=bad)
        self.competitor=True;self.rebuild()
        candidate=chain(self.db,'feat',reading_path=['feat','competitor'])
        self.assertFalse(candidate['options'])
        self.assertEqual(candidate['reading']['steps'][0]['relations'][0]['target_status'],'ambiguous')
        with self.assertRaises(ValueError):chain(self.db,'feat','competitor')

    def test_exception_and_reverse_lookup_keep_the_specific_branch(self):
        self.rules=[('slots','法术位 Spell Slots\n施法通常需要法术位。','法术位','Spell Slots','rule')];self.rebuild()
        result=chain(self.db,'feat',reading_path=['feat','slots'])
        relations=result['reading']['steps'][0]['relations']
        exception=next(e for e in relations if e['relation']=='exception_to')
        ordinary=next(e for e in relations if e['relation']=='uses_rule')
        self.assertEqual(exception['change'],{'field':'spell_slot.consumed','value':False})
        self.assertNotEqual(exception['scope']['branch_id'],ordinary['scope']['branch_id'])
        self.assertIsNone(ordinary['change'])
        self.assertEqual(exception['when']['branch_id'],exception['scope']['branch_id'])
        reverse=chain(self.db,'slots',reading_path=['slots','feat'])['reading']['steps'][0]['relations']
        self.assertEqual([e['relation'] for e in reverse],['modified_by'])
        self.assertEqual(reverse[0]['scope'],exception['scope'])
        extended=chain(self.db,'slots',reading_path=['slots','feat','spell'])
        self.assertEqual(extended['reading']['focus_id'],'spell')
        self.assertTrue(any(r['kind']=='spell_effect' for r in extended['records']))
        self.assertEqual(extended['bindings'][0]['subject_id'],'feat')
        self.accept('knowledge_reject');self.rebuild()
        with self.assertRaises(ValueError):chain(self.db,'slots',reading_path=['slots','feat'])

    def test_local_parse_and_governing_target_status_are_independent(self):
        self.rules=[(eid,'法术位 Spell Slots\n定义。','法术位','Spell Slots','rule') for eid in ('slots_a','slots_b')];self.rebuild()
        result=chain(self.db,'feat',reading_path=['feat','slots_a'])
        segment=next(s for s in result['segments'] if s['subject_id']=='feat')
        self.assertEqual(segment['content_status'],'resolved')
        self.assertEqual(segment['review_status'],'unreviewed')
        slots=[r for r in segment['rule_targets'] if r['name']=='法术位']
        self.assertTrue(slots)
        self.assertTrue(all(r['status']=='ambiguous' and set(r['candidates'])=={'slots_a','slots_b'} for r in slots))
        self.assertEqual(result['reading']['steps'][0]['relations'][0]['status'],'ambiguous')
        self.assertEqual(result['bindings'][0]['status'],'not_stated')

    def test_effect_conditions_match_one_effect_of_the_same_spell_and_branch(self):
        from test_spell_effects import WITCH
        self.spell='测试法术 Test Spell\n'+WITCH;self.rebuild()
        def roots(query):
            sql,values=casting_condition(query)
            return {r[0] for r in self.db.execute('SELECT e.id FROM entries e WHERE '+sql,values)}
        self.assertEqual(roots({'effect':'damage','delivery':'automatic','timing':'ongoing','resource':'slot_free','recovery':'long_rest'}),{'feat'})
        self.assertEqual(roots({'effect':'damage','delivery':'attack_roll','timing':'ongoing'}),set())
        self.assertEqual(roots({'effect':'damage','damage_type':'lightning','delivery':'attack_roll','timing':'immediate'}),{'feat'})
        self.assertEqual(roots({'effect':'damage','timing':'ongoing','resource':'spell_slot','recovery':'long_rest'}),set())
        focused=chain(self.db,'feat','spell',effect_id='effect_4')
        self.assertEqual(focused['reading']['effect_id'],'effect_4')
        for path,effect in [(['feat'],'effect_4'),(['feat','spell'],'effect_missing')]:
            with self.assertRaises(ValueError):chain(self.db,'feat','spell',path,effect)
        rid=self.db.execute("SELECT id FROM rule_knowledge WHERE subject_id='spell' AND kind='spell_effect'").fetchone()[0]
        view=inspect(self.db,rid);save(self.db,self.log,rid,'knowledge_accept',view['basis_sha256'],'fixture','checked ongoing mechanism')
        self.spell=self.spell.replace('2d12','3d12');self.rebuild()
        self.assertFalse(roots({'effect':'damage','timing':'ongoing'}))
        self.assertTrue(chain(self.db,'feat')['options'])
        state=json.loads(self.db.execute('SELECT data FROM effective_knowledge WHERE id=?',(rid,)).fetchone()[0])
        self.assertEqual(state['review_status'],'stale')

    def test_effect_index_audit_checks_scalar_fields_and_payload(self):
        from knowledge import audit_knowledge
        self.assertEqual(audit_knowledge(self.db,self.root)['errors'],[])
        self.db.execute("UPDATE spell_effect_index SET delivery='automatic'")
        self.assertIn('spell_effect_index: projection mismatch',audit_knowledge(self.db,self.root)['errors'])
        self.rebuild()
        self.db.execute("UPDATE spell_effect_index SET data=json_set(data,'$.statement.kind','damage')")
        self.assertIn('spell_effect_index: projection mismatch',audit_knowledge(self.db,self.root)['errors'])

    def test_exports_and_evidence_are_exact(self):
        exported=[json.loads(s) for s in (self.root/'effective_knowledge.jsonl').read_text().splitlines()]
        self.assertEqual({r['id'] for r in exported},{r[0] for r in self.db.execute('SELECT id FROM effective_knowledge')})
        context=Context(self.db)
        for r in exported:validate_record(r,context)
        changed=deepcopy(exported[0]);changed['evidence']['raw']='invented'
        with self.assertRaisesRegex(ValueError,'evidence'):validate_record(changed,context)


if __name__=='__main__':unittest.main()
