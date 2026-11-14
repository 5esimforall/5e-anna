"""Acquisition expressions and local check scope, including deliberate ambiguities."""
from copy import deepcopy
from pathlib import Path
import sys
import sqlite3
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from casting_extract import parse_access
from rule_conditions import prerequisite_expression, bind_effects, validate_conditions
from spell_effects import parse_spell
from test_casting import feature, mention

TARGET='指定施法距离内一个你能看见的类人生物。'
SAVE='该目标必须进行一次感知豁免，豁免失败则其在法术持续时间内陷入麻痹状态。'
REPEAT='目标在其每回合结束时可以重新进行这次豁免，豁免成功则终止其身上该法术的效应。'
SLEEP=('以施法距离内一点为源点，5尺半径球状区域内每个你选择的生物都必须成功通过一次感知豁免否则直到其下个回合结束陷入失能状态。'
       '在其下个回合结束时，它必须重复此豁免。如果目标第二次豁免失败，目标在持续时间内陷入昏迷状态。'
       '若目标受到伤害或其5尺内有人用一个动作将其摇醒，目标身上的法术结束。'
       '不需要睡眠的生物（例如精灵）或免疫力竭状态的生物自动成功通过对抗此法术的豁免。')


def spell(body):return parse_spell(feature(body),body)


class AcquisitionScopeTests(unittest.TestCase):
    def test_nested_alternatives_are_not_flattened(self):
        raw='等级4+，魔契师等级2+且（智力或感知13+或测试（选项甲）专长）'
        expression=prerequisite_expression(raw)
        self.assertEqual(expression['all'][1]['kind'],'class_level')
        alternatives=expression['all'][2]['any']
        self.assertEqual(alternatives[0]['any'][1]['ability'],'感知')
        self.assertEqual(alternatives[1],{'kind':'requirement','name':'测试','entry_type':'feat','qualifier':'选项甲'})
        body='通用专长（先决：'+raw+'）\n你习得测试法术。'
        value=parse_access(feature(body),body,[mention(body)])
        self.assertFalse(value['unparsed']);self.assertEqual(value['prerequisites']['expression'],expression)
        self.assertEqual(value['prerequisites']['evidence']['raw'],'先决：'+raw)
        grouped=prerequisite_expression('等级4+且（智力、感知或魅力13+或测试（选项甲、选项乙）专长）')
        self.assertEqual([t['ability'] for t in grouped['all'][1]['any'][0]['any']],['智力','感知','魅力'])
        self.assertEqual(grouped['all'][1]['any'][1]['qualifier'],'选项甲、选项乙')
        for raw in ['等级4+且（智力或感知13+','等级4+或神秘力量','智力、感知13+','（等级4+）且','等级4+且（未知前提或魔契师等级2+）','等级4+或魔契师等级2+且智力13+']:
            self.assertIsNone(prerequisite_expression(raw),raw)

    def test_prerequisite_header_cannot_hide_an_earlier_rule(self):
        body='你不能施展测试法术。\n先决：等级4+\n你习得测试法术。'
        value=parse_access(feature(body),body,[mention(body)])
        self.assertTrue(value['unparsed'])
        self.assertIn('你不能施展',value['unparsed'][0]['evidence']['raw'])

    def test_item_state_and_self_target_stay_on_the_usage_branch(self):
        body=('戒指，非普通（需同调）\n当你持握此物品且与其同调时，你可以从中施展测试法术，但只能以自身为目标。'
              '你也可以使用你拥有的任意法术位来施展该法术。')
        entry={**feature(body),'type':'magic_item'}
        value=parse_access(entry,body,[mention(body)]);self.assertFalse(value['unparsed'])
        self.assertEqual(value['activation']['expression']['state'],'attuned')
        first,second=value['grants'][0]['branches']
        self.assertEqual([c['state'] for c in first['when']['all']],['held','attuned'])
        self.assertEqual(first['target'],{'kind':'self'})
        self.assertNotIn('when',second);self.assertNotIn('target',second)
        self.assertTrue(second['resource']['consumed'])
        for mutation in ['在夜晚','持握别人的物品','与其同调','持握此物品且拥有未知力量']:
            bad=body.replace('你持握此物品且与其同调',mutation)
            self.assertTrue(parse_access({**entry,'end':len(bad)},bad,[mention(bad)])['unparsed'])
        bad=body.replace('以自身为目标','以龙为目标')
        self.assertTrue(parse_access({**entry,'end':len(bad)},bad,[mention(bad)])['unparsed'])

    def test_material_exception_is_not_reused_by_a_later_slot_branch(self):
        body='你可以随意施展测试法术，且无需消耗法术位和材料成分。你也可以消耗合适的法术位来施展这些法术。'
        value=parse_access(feature(body),body,[mention(body)]);self.assertFalse(value['unparsed'])
        free,slot=value['grants'][0]['branches']
        self.assertEqual(free['components'],{'waived':['M']});self.assertEqual(free['frequency'],'at_will')
        self.assertNotIn('components',slot);self.assertNotIn('frequency',slot)
        bad=body.replace('材料成分','价值不超过100GP的材料成分')
        self.assertTrue(parse_access(feature(bad),bad,[mention(bad)])['unparsed'])


class CheckScopeTests(unittest.TestCase):
    def test_a_hit_triggers_a_save_without_changing_the_save_mechanism(self):
        from effect_queries import effect_rows
        body=('一道恶心的浅绿射线朝施法距离内一个生物射去。你对目标进行一次远程法术攻击。'
              '若命中，则目标受到2d8的毒素伤害且必须进行一次体质豁免，豁免失败则目标陷入中毒，该中毒效应持续至你的下一回合结束。')
        value=spell(body);self.assertFalse(value['unparsed'])
        rows=list(effect_rows([{'id':'r','subject_id':'s','kind':'spell_effect','status':'resolved','review_status':'unreviewed','value':value}]))
        save=next(r for r in rows if r['effect']=='saving_throw');poison=next(r for r in rows if r['effect']=='condition')
        self.assertEqual(save['delivery'],'saving_throw');self.assertEqual(save['triggers'],['attack_result'])
        self.assertEqual(poison['delivery'],'saving_throw');self.assertEqual(poison['triggers'],['save_result'])
        self.assertEqual(poison['save_ability'],'体质')
        bad=body.replace('该中毒效应','该麻痹效应')
        self.assertTrue(spell(bad)['unparsed']);self.assertFalse(any(e['kind']=='condition' for e in spell(bad)['effects']))

    def test_each_same_ability_check_keeps_its_own_outcome(self):
        value=spell(TARGET+SAVE+SAVE.replace('麻痹','束缚')+'该次豁免具有劣势。')
        self.assertFalse(value['unparsed']);target,first,paralyzed,second,restrained,modifier=value['effects']
        self.assertEqual(paralyzed['when']['effect_id'],first['id'])
        self.assertEqual(restrained['when']['effect_id'],second['id'])
        self.assertEqual(modifier['check']['effect_id'],second['id'])
        self.assertEqual(modifier['modifier'],'disadvantage')
        self.assertEqual(paralyzed['when']['selector'],'same_clause')
        for effect in value['effects']:self.assertEqual(effect['evidence']['raw'],(TARGET+SAVE+SAVE.replace('麻痹','束缚')+'该次豁免具有劣势。')[slice(*effect['evidence']['span'])])
        validate_conditions({'value':value})
        bad=deepcopy(value);bad['effects'][4]['when']['effect_id']=first['id'];bad['effects'][4].pop('links')
        bad['effects'][4]['when']['candidate_effect_ids']=[first['id']]
        with self.assertRaisesRegex(ValueError,'escapes its clause'):validate_conditions({'value':bad})

    def test_repeated_this_save_inherits_only_the_named_current_check(self):
        value=spell(TARGET+SAVE+SAVE.replace('感知','体质')+REPEAT)
        self.assertFalse(value['unparsed']);repeat=value['effects'][-2]
        self.assertEqual(repeat['ability'],'体质');self.assertEqual(repeat['ability_source'],'repeat_of')
        self.assertEqual(repeat['repeat_of']['effect_id'],'effect_4')
        self.assertEqual(value['effects'][-1]['when']['effect_id'],repeat['id'])
        explicit=spell(TARGET+SAVE+SAVE+REPEAT.replace('重新进行这次豁免','再进行一次感知豁免'))
        self.assertTrue(explicit['unparsed'])
        self.assertIsNone(explicit['effects'][-2]['repeat_of']['effect_id'])
        self.assertEqual(explicit['effects'][-2]['repeat_of']['candidate_effect_ids'],['effect_2','effect_4'])
        for barrier in ['另一个生物先进行一次未知检定。','对目标进行一次远程法术攻击。']:
            broken=spell(TARGET+SAVE+barrier+REPEAT)
            self.assertIsNone(broken['effects'][-2]['ability']);self.assertTrue(broken['unparsed'])
        bad=deepcopy(value);bad['effects'][-2]['ability']='魅力'
        with self.assertRaisesRegex(ValueError,'Inherited'):validate_conditions({'value':bad})

    def test_ordinal_followup_and_save_exception_preserve_negation(self):
        value=spell(SLEEP);self.assertFalse(value['unparsed'])
        first,repeat=[e for e in value['effects'] if e['kind']=='saving_throw']
        self.assertEqual([first['occurrence'],repeat['occurrence']],[1,2])
        self.assertEqual(repeat['ability'],'感知');self.assertEqual(repeat['repeat_of']['effect_id'],first['id'])
        asleep=value['effects'][4]
        self.assertEqual(asleep['when']['effect_id'],repeat['id'])
        self.assertEqual(asleep['when']['occurrence'],2)
        self.assertEqual(value['effects'][-2]['scope'],'target')
        self.assertEqual(value['effects'][-1]['when']['any'][0],{'not':{'kind':'requires_sleep','subject':'target'}})
        self.assertEqual(value['effects'][-1]['when']['any'][1]['condition'],'力竭')
        for bad in [SLEEP.replace('重复此豁免','进行新的体质豁免'),SLEEP.replace('第二次','第三次'),SLEEP.replace('第二次','第0次'),SLEEP.replace('5尺内','10尺以外')]:
            self.assertTrue(spell(bad)['unparsed'])

    def test_same_clause_ambiguity_is_not_resolved_by_order(self):
        ev={'path':'x','span':[0,5],'raw':'同句两豁免'}
        effects=[{'kind':'saving_throw','ability':'体质','evidence':ev},
                 {'kind':'saving_throw','ability':'体质','evidence':ev},
                 {'kind':'condition','condition':'中毒','when':{'kind':'save_result','selector':'same_clause'},'evidence':ev}]
        gaps=bind_effects(effects)
        self.assertTrue(gaps);self.assertIsNone(effects[-1]['when']['effect_id'])
        self.assertEqual(effects[-1]['when']['candidate_effect_ids'],['effect_1','effect_2'])



DB=Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite'
@unittest.skipUnless(DB.exists(),'Build the corpus first')
class CorpusScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from knowledge import Context
        cls.db=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True);cls.context=Context(cls.db)
    @classmethod
    def tearDownClass(cls):cls.db.close()

    def entry(self,name,generation,prefix):
        entries=[e for e in self.context.entries.values() if e.get('name_en')==name and e['ruleset']==generation and e['page_path'].startswith(prefix)]
        self.assertEqual(len(entries),1,(name,generation,prefix));return entries[0]

    def test_equipment_and_class_acquisition_remain_distinct_along_the_path(self):
        from knowledge import chain,record_for
        cases=[('Boots of Levitation','5e','topics/城主指南/',True,'worn'),
               ('Ring of Jumping','5r','topics/城主指南2024/',True,'worn'),
               ('Ring of Water Walking','5r','topics/城主指南2024/',False,'worn'),
               ('Otherworldly Leap','5r','topics/玩家手册2024/',False,None)]
        for name,generation,prefix,attuned,state in cases:
            with self.subTest(name=name,generation=generation):
                entry=self.entry(name,generation,prefix)
                value=parse_access(entry,self.context.pages[entry['page_path']],self.context.mentions(entry))
                self.assertEqual(record_for(entry,'spell_access',value,self.context)['status'],'resolved')
                self.assertEqual(bool(value['activation']),attuned)
                branch=value['grants'][0]['branches'][0];self.assertEqual(branch['target'],{'kind':'self'})
                self.assertEqual(branch.get('when',{}).get('state'),state)
                if not state:self.assertEqual(value['prerequisites']['expression'],{'kind':'class_level','class':'魔契师','minimum':2})
                result=chain(self.db,entry['id']);self.assertTrue(result['options'])
                option=result['options'][0];self.assertEqual(option['casting_target'],'self')
                result=chain(self.db,entry['id'],option['spell_ids'][0])
                destination=result['reading']['steps'][0]['relations'][0]
                self.assertEqual(destination['scope']['usage'][0]['conditions'],option['conditions'])
                # The definition identity and generation are retained, not rewritten by a self-only grant.
                self.assertEqual(self.context.entries[option['spell_ids'][0]]['ruleset'],generation)

    def test_material_waiver_is_evidenced_in_one_generation_only(self):
        values=[]
        for gen in ('5e','5r'):
            entry=self.entry('Misty Visions',gen,'topics/玩家手册')
            value=parse_access(entry,self.context.pages[entry['page_path']],self.context.mentions(entry))
            self.assertFalse(value['unparsed']);values.append(value['grants'][0]['branches'][0])
        self.assertEqual(values[0]['components'],{'waived':['M']});self.assertNotIn('components',values[1])

    def test_expansion_named_prerequisite_retains_its_parenthetical_choice(self):
        from knowledge import record_for
        entry=self.entry('Adept of the Black Robes','5e','topics/龙枪：龙后之影/')
        value=parse_access(entry,self.context.pages[entry['page_path']],self.context.mentions(entry))
        record=record_for(entry,'spell_access',value,self.context)
        pre=record['value']['prerequisites'];self.assertEqual(pre['status'],'parsed')
        requirement=pre['expression']['all'][1]
        self.assertEqual(requirement['name'],'高等术法入门');self.assertEqual(requirement['qualifier'],'努塔瑞')
        self.assertEqual(requirement['target']['resolution']['status'],'resolved')
        self.assertIn('（努塔瑞）专长',pre['evidence']['raw'])
        # Its other, unsupported clauses do not become unconditional query results.
        self.assertEqual(record['status'],'partial')

    def test_two_generations_keep_different_poison_delivery(self):
        for generation,delivery in [('5e','save_result'),('5r','attack_result')]:
            entry=self.entry('Ray of Sickness',generation,'topics/玩家手册')
            value=parse_spell(entry,self.context.pages[entry['page_path']]);self.assertFalse(value['unparsed'])
            poisoned=next(e for e in value['effects'] if e['kind']=='condition')
            self.assertEqual(poisoned['when']['kind'],delivery)
            check=next(e for e in value['effects'] if e['id']==poisoned['when']['effect_id'])
            if generation=='5e':self.assertEqual(check['ability'],'体质');self.assertEqual(check['when']['kind'],'attack_result')
            renamed={**entry,'name':'Renamed','name_en':'Renamed','name_zh':'改名'}
            self.assertEqual(parse_spell(renamed,self.context.pages[entry['page_path']]),value)

    def test_background_reaches_ordinal_save_and_explicit_exception(self):
        from knowledge import chain,casting_condition
        entry=self.entry('Sleep','5r','topics/玩家手册2024/')
        root=next(e for e in self.context.entries.values() if e['name_zh']=='智者' and e['ruleset']=='5r')
        result=chain(self.db,root['id'],entry['id'],effect_id='effect_5')
        record=next(r for r in result['records'] if r['subject_id']==entry['id'] and r['kind']=='spell_effect')
        self.assertEqual(record['status'],'resolved');effects=record['value']['effects']
        self.assertEqual(effects[4]['when']['effect_id'],effects[3]['id'])
        self.assertEqual(effects[3]['repeat_of']['effect_id'],effects[1]['id'])
        self.assertEqual(result['reading']['path'][0],root['id']);self.assertEqual(len(result['reading']['path']),3)
        self.assertEqual(result['reading']['effect_id'],'effect_5')
        exception=next(r for r in record['value']['rules'] if r['change'] and r['change']['field']=='saving_throw.result')
        self.assertEqual(exception['scope']['effect_id'],'effect_7')
        self.assertEqual(exception['change']['applies_to'],{'kind':'spell_saves'})
        for ability,wanted in [('感知',True),('体质',False)]:
            sql,params=casting_condition({'spell':'睡眠术','effect':'condition','save_ability':ability,'timing':'repeat','trigger':'save_result'})
            self.assertEqual(bool(self.db.execute('SELECT 1 FROM entries e WHERE e.id=? AND '+sql,[root['id'],*params]).fetchone()),wanted)
        old=self.entry('Sleep','5e','topics/玩家手册/')
        self.assertTrue(parse_spell(old,self.context.pages[old['page_path']])['unparsed'])

if __name__=='__main__':unittest.main()
