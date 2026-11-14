"""B1-09/10: the same model and consumers work without a spell carrier."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import unittest
from anna.knowledge.features import parse_feature
from anna.knowledge.model import SCHEMA, rule_value, validate_structure
from anna.knowledge.conditions import validate_conditions
from anna.knowledge.values import quantity_expression
from anna.knowledge.repository import Context, record_for, chain, casting_condition, inspect, save, audit_knowledge, validate_record
from anna.serialization import canonical, digest
from anna.review.store import append_event, load_events, review_record_id
from anna.review.commands import revoke
import test_knowledge

HEAL = '你可以在自己回合内用一个附赠动作恢复生命值，其总值为1d10＋你的战士职业等级。'
REST = '你必须完成一次短休或长休才能再次使用该特性。'
DODGE = '当一个你能看见的攻击者用一次攻击检定命中你时，你可以用你的反应将这次攻击对你造成的伤害减半（向下取整）。'


def feature(body, name='任意名称', **fields):
    text = name+'\n'+body
    entry = dict(id='probe', name=name, name_zh=name, type='class_feature', ruleset='5r',
                 page_path='probe.htm', source_ids=['core'], start=0, title_end=len(name)+1, end=len(text), fields={}, **fields)
    return entry, text


class SkeletonSyntaxTests(unittest.TestCase):
    def test_b1_10_healing_uses_shared_quantity_conditions_and_usage(self):
        entry, text = feature(HEAL+REST)
        value = parse_feature(entry, text)
        self.assertEqual(value['unparsed'], [])
        heal = value['effects'][0]
        self.assertEqual(heal['target'], {'kind':'self'})
        self.assertEqual(heal['when'], {'kind':'turn','subject':'self','phase':'during','relative':'each'})
        self.assertEqual(heal['quantity'], {'kind':'sum','terms':[
            {'kind':'dice','count':1,'sides':10}, {'kind':'class_level','class':'战士'}]})
        self.assertEqual(heal['use']['action'], 'bonus_action')
        self.assertEqual(heal['use']['resource'], {'kind':'not_stated'})
        self.assertEqual(heal['use']['uses'], {'quantity':{'kind':'constant','value':1},'per':'feature'})
        self.assertEqual(heal['use']['recovery'], {'event':'short_or_long_rest','amount':'all'})
        self.assertEqual(heal['use']['evidence'][1]['raw'], REST)
        self.assertEqual(value['parameters'], {})
        self.assertNotIn('casting', value)
        self.assertEqual(quantity_expression('1d8+你的施法属性调整值')['terms'][1]['ability'], 'spellcasting')
        self.assertEqual(quantity_expression('1d10＋你的战士职业等级'), heal['quantity'])

    def test_unsupported_qualifiers_and_competing_uses_remain_gaps(self):
        for body in [HEAL.replace('。','，但只能在水下使用。')+REST,
                     HEAL+REST.replace('短休或长休','神秘休息'), HEAL+HEAL+REST]:
            with self.subTest(body=body):
                entry,text=feature(body);value=parse_feature(entry,text)
                self.assertTrue(value['unparsed'])
                self.assertTrue(all('recovery' not in e.get('use',{}) for e in value['effects']))
        entry,text=feature('旁人传闻这种能力能够恢复生命值。')
        self.assertIsNone(parse_feature(entry,text))

    def test_triggering_attack_is_local_and_cannot_be_replaced_by_another(self):
        entry,text=feature(DODGE+DODGE);value=parse_feature(entry,text)
        self.assertFalse(value['unparsed'])
        self.assertEqual(value['effects'][1]['applies_to']['effect_id'],'effect_1')
        self.assertEqual(value['effects'][3]['applies_to']['effect_id'],'effect_3')
        self.assertEqual(value['effects'][1]['rounding'],'floor')
        self.assertEqual(value['effects'][1]['use']['action'],'reaction')
        self.assertEqual(value['effects'][0]['attacker'],{'visible':True})
        record={'schema':SCHEMA,'kind':'procedure','value':value}
        validate_structure(record);validate_conditions(record)
        for mutate in [lambda v:v['effects'][3]['applies_to'].update(effect_id='effect_1'),
                       lambda v:v['effects'][1]['applies_to'].update(effect_id='missing'),
                       lambda v:v['effects'][1]['use'].update(action='invented'),
                       lambda v:v['effects'][1]['factor'].update(denominator=0),
                       lambda v:v['effects'][1]['factor'].update(numerator=True)]:
            broken=deepcopy(record);mutate(broken['value'])
            with self.assertRaises(ValueError):
                validate_structure(broken);validate_conditions(broken)
        entry,text=feature(DODGE.replace('（向下取整）','（仅限龙类）'))
        self.assertTrue(parse_feature(entry,text)['unparsed'])

    def test_class_level_requires_its_owner_and_evidence(self):
        entry,text=feature(DODGE,'5级：任意标题',parent_id='rogue')
        self.assertEqual(parse_feature(entry,text)['unparsed'][0]['reason'],'unresolved_level_owner')
        value=parse_feature(entry,text,{'id':'rogue','name_zh':'游荡者'})
        self.assertEqual(value['prerequisites']['expression'],{'kind':'class_level','class':'游荡者','owner_id':'rogue','minimum':5})
        self.assertEqual(value['prerequisites']['evidence']['raw'],'5级：任意标题\n')
        broken={'schema':1,'kind':'procedure','value':value}
        with self.assertRaisesRegex(ValueError,'schema'):validate_structure(broken)
        broken={'schema':SCHEMA,'kind':'procedure','value':{**value,'casting':{}}}
        with self.assertRaisesRegex(ValueError,'common rule'):validate_structure(broken)

    def test_legacy_migration_only_maps_verified_schema_one_identities(self):
        for previous,current in [('spell_access','grant'),('feat_access','grant'),('spell_effect','procedure')]:
            with self.subTest(previous=previous):
                old_id='knowledge_'+digest(['owner',previous])[:24]
                event={'basis':{'schema':1},'binding':{'subject_id':'owner','kind':previous,'knowledge_id':old_id}}
                self.assertEqual(review_record_id(event),'knowledge_'+digest(['owner',current])[:24])
                wrong_subject=deepcopy(event);wrong_subject['binding']['subject_id']='other'
                self.assertEqual(review_record_id(wrong_subject),old_id)
                wrong_id=deepcopy(event);wrong_id['binding']['knowledge_id']='unrelated'
                self.assertEqual(review_record_id(wrong_id),'unrelated')
                wrong_schema=deepcopy(event);wrong_schema['basis']['schema']=2
                self.assertEqual(review_record_id(wrong_schema),old_id)


class SkeletonConsumerTests(unittest.TestCase):
    def setUp(self):
        self.fx=test_knowledge.KnowledgeTests();self.fx.setUp()
        self.fx.spell=('测试法术 Test Spell\n施法时间：附赠动作\n施法距离：触碰\n法术成分：V\n持续时间：立即\n你触碰的一名生物恢复等同于1d8+你的施法属性调整值点生命值。')
        self.fx.rules=[('wind','自愈 Self Heal\n'+HEAL+REST,'自愈','Self Heal','class_feature'),
                       ('dodge','减伤 Mitigation\n'+DODGE,'减伤','Mitigation','class_feature'),
                       ('reaction','反应 Reaction\n使用反应的通则。','反应','Reaction','action'),
                       ('damage','伤害 Damage\n伤害的通则。','伤害','Damage','rule'),
                       ('background','背景 Background\n专长：示例','背景','Background','background')]
        self.fx.rebuild()
    def tearDown(self):self.fx.tearDown()
    def ids(self,query):
        sql,values=casting_condition(query)
        return {r[0] for r in self.fx.db.execute('SELECT e.id FROM entries e WHERE '+sql,values)}
    def procedure(self,subject):
        return next(r for r in chain(self.fx.db,subject)['records'] if r['subject_id']==subject and r['kind']=='procedure')

    def test_b1_01_and_10_share_canonical_records_without_parallel_indexes(self):
        root=chain(self.fx.db,'background','spell')
        self.assertEqual(root['schema'],2)
        self.assertEqual(root['reading']['path'],['background','feat','spell'])
        self.assertEqual({r['kind'] for r in root['records']},{'grant','procedure'})
        background=next(r for r in root['records'] if r['subject_id']=='background')['value']
        self.assertEqual(background['grants'][0]['entry_type'],'feat')
        self.assertNotIn('target',background)
        for record in root['records']:
            self.assertFalse(set(rule_value())-record['value'].keys())
        self.assertFalse(self.fx.db.execute("SELECT name FROM sqlite_master WHERE name='spell_effect_index'").fetchone())
        self.assertEqual(self.ids({'query_subject':'entry','effect':'healing','use_action':'bonus_action'}),{'spell','wind'})
        self.assertEqual(self.ids({'query_subject':'spell','effect':'healing','use_action':'bonus_action'}),{'spell'})
        self.assertEqual(self.ids({'effect':'healing','use_action':'bonus_action'}),{'feat','background'})
        self.assertEqual(self.ids({'query_subject':'entry','effect':'modifier','use_action':'reaction'}),{'dodge'})
        self.assertFalse(self.ids({'query_subject':'entry','effect':'healing','use_action':'reaction'}))
        for subject in ('entry','spell'):
            with self.assertRaises(ValueError):self.ids({'query_subject':subject,'resource':'slot_free'})
        self.assertEqual(chain(self.fx.db,'wind')['options'],[])
        self.assertEqual(audit_knowledge(self.fx.db,self.fx.root)['errors'],[])

    def test_same_local_graph_reading_rule_modifications_and_export(self):
        record=self.procedure('dodge');effect=record['value']['effects'][1]
        selected=chain(self.fx.db,'dodge',effect_id=effect['id'])
        self.assertEqual(selected['reading']['effect_id'],'effect_2')
        exception=next(r for r in record['value']['rules'] if r['relation']=='exception_to')
        self.assertEqual(exception['scope'],{'kind':'effect','effect_id':'effect_2'})
        self.assertEqual(exception['change']['factor'],{'numerator':1,'denominator':2})
        self.assertEqual(exception['change']['applies_to']['effect_id'],'effect_1')
        forward=chain(self.fx.db,'dodge',reading_path=['dodge','damage'])
        backward=chain(self.fx.db,'damage',reading_path=['damage','dodge'],effect_id='effect_2')
        self.assertEqual(forward['reading']['steps'][0]['relations'][0]['relation'],'exception_to')
        self.assertEqual(backward['reading']['steps'][0]['relations'][0]['relation'],'modified_by')
        with self.assertRaises(ValueError):chain(self.fx.db,'dodge',effect_id='effect_99')
        with self.assertRaises(ValueError):chain(self.fx.db,'dodge',reading_path=['dodge','spell'])
        # Independent consumer: JSON only, no query projection or rendering helper.
        rows=[json.loads(line) for line in (self.fx.root/'effective_knowledge.jsonl').read_text().splitlines()]
        exported=set()
        for row in rows:
            if row['kind']!='procedure' or row['status']!='resolved':continue
            for node in row['value']['effects']:
                action=node.get('use',{}).get('action') or row['value']['parameters'].get('action',{}).get('kind')
                if node['kind']=='healing' and action=='bonus_action':exported.add(row['subject_id'])
        self.assertEqual(exported,{'spell','wind'})
        self.assertEqual(exported,self.ids({'query_subject':'entry','effect':'healing','use_action':'bonus_action'}))
        self.fx.db.execute("UPDATE rule_effect_index SET use_action='reaction' WHERE subject_id='wind'")
        self.assertIn('rule_effect_index: projection mismatch',audit_knowledge(self.fx.db,self.fx.root)['errors'])

    def test_initial_action_is_not_charged_again_to_a_repeated_check_or_ending(self):
        from test_rule_scopes import TARGET, SAVE, REPEAT
        self.fx.spell='测试法术 Test Spell\n施法时间：动作\n施法距离：60尺\n法术成分：V\n持续时间：1分钟\n'+TARGET+SAVE+REPEAT
        self.fx.rebuild()
        base={'query_subject':'spell','effect':'saving_throw','use_action':'action'}
        self.assertEqual(self.ids({**base,'timing':'immediate'}),{'spell'})
        self.assertFalse(self.ids({**base,'timing':'repeat'}))
        self.assertEqual(self.ids({**base,'timing':'repeat','use_action':'not_stated'}),{'spell'})
        self.assertFalse(self.ids({'query_subject':'spell','effect':'end_spell','use_action':'action'}))

    def test_generic_procedure_review_and_changed_source_gate_every_consumer(self):
        row=self.procedure('dodge');rid=row['id'];view=inspect(self.fx.db,rid)
        event=save(self.fx.db,self.fx.log,rid,'knowledge_accept',view['basis_sha256'],'fixture','checked')
        self.fx.rebuild();self.assertEqual(self.procedure('dodge')['review_status'],'applied')
        self.fx.rules=[(i,b.replace('向下取整','未知限定') if i=='dodge' else b,n,en,k) for i,b,n,en,k in self.fx.rules]
        self.fx.rebuild()
        self.assertEqual(self.procedure('dodge')['review_status'],'stale')
        self.assertFalse(self.ids({'query_subject':'entry','effect':'modifier','use_action':'reaction'}))
        self.assertFalse(any(e['target_id']=='dodge' and e['status']=='resolved' for e in chain(self.fx.db,'damage')['edges']))
        self.assertTrue(chain(self.fx.db,'feat')['options'])
        revoke(self.fx.log,event['id'],'fixture','reopen');self.fx.rebuild()
        self.assertEqual(self.procedure('dodge')['status'],'partial')
        self.assertFalse(self.ids({'query_subject':'entry','effect':'modifier'}))

    def test_legacy_review_is_reopened_not_dropped_or_silently_reapproved(self):
        row=self.procedure('spell');rid=row['id']
        old_id='knowledge_'+digest(['spell','spell_effect'])[:24]
        basis={'schema':1,'record':{'id':old_id,'kind':'spell_effect','schema':1},'definitions':{},'sources':{}}
        _,version=load_events(self.fx.log)
        old=append_event(self.fx.log,{'operation':'knowledge_accept','binding':{'knowledge_id':old_id,
            'subject_id':'spell','kind':'spell_effect',**row['evidence']},'basis':basis,'basis_sha256':digest(basis),
            'reviewer':'fixture','reason':'v1 approval','supersedes':None},version)
        self.fx.rebuild()
        self.assertEqual(self.procedure('spell')['review_status'],'stale')
        decision=json.loads(self.fx.db.execute('SELECT data FROM knowledge_decisions WHERE id=?',(old['id'],)).fetchone()[0])
        self.assertEqual(decision['knowledge_id'],rid)
        self.assertEqual(decision['reason'],'knowledge_schema_changed')
        self.assertEqual(decision['event']['binding']['knowledge_id'],old_id)
        self.assertFalse(self.ids({'query_subject':'spell','effect':'healing'}))
        current=inspect(self.fx.db,rid)
        replacement=save(self.fx.db,self.fx.log,rid,'knowledge_accept',current['basis_sha256'],'fixture','v2 rechecked',old['id'])
        self.fx.rebuild()
        self.assertEqual(self.procedure('spell')['review_status'],'applied')
        self.assertEqual(self.ids({'query_subject':'spell','effect':'healing'}),{'spell'})
        self.assertEqual(len(load_events(self.fx.log)[0]),2)
        revoke(self.fx.log,replacement['id'],'fixture','undo');self.fx.rebuild()
        self.assertEqual(self.procedure('spell')['review_status'],'revoked')


DB=Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite'
@unittest.skipUnless(DB.exists(),'Build the corpus first')
class SkeletonCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True);cls.context=Context(cls.db)
    @classmethod
    def tearDownClass(cls):cls.db.close()
    def test_b1_10_independent_original_cases_and_renaming(self):
        cases=[('ver_0fd1de793ebd67111e8e6e30','5e','healing','resolved'),
               ('ver_958337258cbdf07fb3fe6c4f','5r','healing','partial'),
               ('ver_9642042f9561eb7e3d936d91','5e','modifier','resolved'),
               ('ver_e03ee891694944041e9b1434','5r','modifier','resolved')]
        for eid,generation,kind,status in cases:
            with self.subTest(eid=eid):
                entry=self.context.entries[eid];self.assertEqual(entry['ruleset'],generation)
                text=self.context.pages[entry['page_path']]
                owner=self.context.entries[entry['parent_id']]
                while owner['type']!='class':owner=self.context.entries[owner['parent_id']]
                value=parse_feature(entry,text,owner)
                renamed=parse_feature({**entry,'name':'改名','name_zh':'改名','name_en':'Different Name'},text,owner)
                self.assertEqual(renamed,value)
                record=record_for(entry,'procedure',value,self.context)
                self.assertEqual(record['status'],status)
                published=next(r for r in chain(self.db,eid)['records'] if r['kind']=='procedure')
                self.assertEqual(published['value'],record['value'])
                if value['prerequisites']['status']=='parsed':
                    for field,bad_value in [('owner_id','missing'),('class','不匹配的职业'),('minimum',True),('minimum',0)]:
                        bad=deepcopy(record);bad['value']['prerequisites']['expression'][field]=bad_value
                        with self.assertRaises(ValueError):validate_record(bad,self.context)
                effect=next(e for e in value['effects'] if e['kind']==kind)
                if kind=='modifier':
                    self.assertEqual(effect['use']['action'],'reaction')
                    self.assertEqual(effect['factor'],{'numerator':1,'denominator':2})
                    self.assertEqual(effect['rounding'],'floor' if generation=='5r' else 'not_stated')
                    self.assertEqual(value['prerequisites']['expression']['minimum'],5)
                else:
                    self.assertEqual(effect['quantity']['terms'][1],{'kind':'class_level','class':'战士'})
                    self.assertEqual(effect['use'].get('recovery'),{'event':'short_or_long_rest','amount':'all'} if generation=='5e' else None)
                self.assertEqual(value['parameters'],{})
                self.assertNotIn('spell',canonical(effect))


if __name__=='__main__':unittest.main()
