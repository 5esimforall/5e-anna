"""Rule-chain grammars: quantities, scope, alternative costs and rejected prose."""
import json
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from casting_extract import parse_access, parse_spell, evidence, quantity_expression
from knowledge import Context, record_for, branches, casting_condition, casting_query


def feature(body):
    return {'id':'feature','name':'示例能力 Example','name_zh':'示例能力','name_en':'Example','type':'feat',
            'page_path':'test.htm','title_end':0,'start':0,'end':len(body),'ruleset':'5r','source_ids':['phb'],'fields':{}}


def mention(body,name='测试法术'):
    start=body.index(name)
    return {'kind':'entry','name':name,'resolution':{'targets':['spell'],'status':'resolved','reason':'same_source'},
            'evidence':evidence('test.htm',body,start,start+len(name))}


class AccessTests(unittest.TestCase):
    def test_known_prepared_and_mention_are_distinct(self):
        for verb,mode in [('习得','known'),('学会','known'),('始终准备着','prepared')]:
            body='你'+verb+'测试法术。'
            value=parse_access(feature(body),body,[mention(body)])
            self.assertEqual(value['grants'][0]['mode'],mode)
            self.assertFalse(value['unparsed'])
        for body in ['这本书提到测试法术。','你看见一名法师施展测试法术。','测试法术可以恢复生命值。']:
            self.assertIsNone(parse_access(feature(body),body,[mention(body)]))

    def test_negative_or_conditional_grants_never_become_supported_options(self):
        for body in ['你不能习得测试法术。','你不会习得测试法术。','你可以施展测试法术，但只能在夜晚使用。','如果你持有徽记，你习得测试法术。','你习得测试法术。仅限夜晚使用。']:
            value=parse_access(feature(body),body,[mention(body)])
            if value:
                self.assertTrue(value['unparsed'],body)

    def test_slot_alternative_does_not_inherit_daily_limit(self):
        body=('你习得测试法术。你可以在不消耗法术位的情况下施展这些法术各一次。'
              '当你以此法施展某个法术后，你不能再次以此法施展该法术，直至你完成一次长休。'
              '你也可以消耗合适的法术位来施展这些法术。')
        value=parse_access(feature(body),body,[mention(body)])
        free,slot=value['grants'][0]['branches']
        self.assertFalse(free['resource']['consumed']);self.assertEqual(free['uses']['quantity']['value'],1)
        self.assertEqual(free['recovery']['event'],'long_rest')
        self.assertTrue(slot['resource']['consumed']);self.assertNotIn('uses',slot);self.assertNotIn('recovery',slot)
        self.assertFalse(value['unparsed'])

    def test_unhandled_restriction_is_not_silently_consumed(self):
        body='你习得测试法术，但只能对龙使用。'
        value=parse_access(feature(body),body,[mention(body)])
        self.assertEqual(value['unparsed'][0]['reason'],'partially_parsed_clause')
        self.assertEqual(value['unparsed'][0]['evidence']['raw'],body)

    def test_missing_definition_retains_the_observation(self):
        body='你习得失落法术。'
        value=parse_access(feature(body),body,[])
        self.assertEqual(value['grants'],[]);self.assertEqual(value['unparsed'][0]['evidence']['raw'],body)

    def test_choice_count_does_not_use_the_list_selection_count(self):
        body='从牧师法术列表、德鲁伊法术列表、法师法术列表中选择一个列表，你从该法术列表中选择两道戏法并习得之。'
        value=parse_access(feature(body),body,[])
        self.assertEqual(value['grants'][0]['target']['count'],2)
        self.assertEqual(value['grants'][0]['target']['constraints']['levels'],[0])
        self.assertFalse(value['unparsed'])

    def test_ability_maps_keep_all_semicolon_branches(self):
        body='你习得测试法术。这些法术的施法属性取决于你指定的职业：吟游诗人、术士、魔契师用魅力；牧师、德鲁伊用感知；法师用智力。'
        value=parse_access(feature(body),body,[mention(body)])
        self.assertEqual(value['grants'][0]['ability']['values'],{'吟游诗人':'魅力','术士':'魅力','魔契师':'魅力','牧师':'感知','德鲁伊':'感知','法师':'智力'})

    def test_prerequisite_is_not_dropped(self):
        body='通用专长（先决：等级4+）\n你习得测试法术。'
        value=parse_access(feature(body),body,[mention(body)])
        self.assertEqual(value['prerequisites']['expression'],{'kind':'level','minimum':4})
        body=body.replace('等级4+','等级4+且具有施法能力')
        value=parse_access(feature(body),body,[mention(body)])
        self.assertEqual(value['prerequisites']['status'],'unparsed')


class EffectTests(unittest.TestCase):
    def test_teleport_preserves_destination_predicates(self):
        body='施法时间：附赠动作\n施法距离：自身\n法术成分：V\n持续时间：立即\n你短暂地被银白的雾气所笼罩，传送到至多30尺内一个你能看见且未占据空间。'
        result=parse_spell(feature(body),body)
        self.assertEqual(result['casting']['action']['kind'],'bonus_action')
        self.assertEqual(result['effects'][0]['destination'],{'distance':{'maximum':30,'unit':'foot'},'visible':True,'unoccupied':True})
        self.assertFalse(result['unparsed'])
        changed=body.replace('未占据空间','仅有一名友军占据的空间')
        result=parse_spell(feature(changed),changed)
        self.assertEqual(result['effects'],[]);self.assertEqual(len(result['unparsed']),1)

    def test_healing_dice_and_upcast_remain_expressions(self):
        body='你触碰的一名生物恢复等同于2d8+你施法属性调整值点生命值。升环施法。使用的法术位每比一环高一环，此法术的治疗量就增加2d8点。'
        result=parse_spell(feature(body),body)
        self.assertEqual(result['effects'][0]['quantity']['kind'],'sum')
        self.assertEqual(result['effects'][1]['threshold'],1)
        self.assertEqual(result['effects'][1]['effect']['quantity'],{'kind':'dice','count':2,'sides':8})
        self.assertIsNone(quantity_expression('2d8+some expression'))

    def test_condition_requires_failure_and_keeps_both_end_events(self):
        body='如果它豁免失败，它陷入魅惑状态直到法术结束，或者直到你或你的同伴对它造成伤害。'
        result=parse_spell(feature(body),body)['effects'][0]
        self.assertEqual(result['when'],{'kind':'save_result','result':'failure'})
        self.assertEqual(result['ends']['any'][1],{'kind':'damage_from','actors':['caster','caster_allies']})
        changed=body.replace('豁免失败','豁免成功')
        self.assertTrue(parse_spell(feature(changed),changed)['unparsed'])

    def test_casting_query_validates_fields_and_correlates_one_branch(self):
        sql,values=casting_condition({'mode':'prepared','resource':'spell_slot','recovery':'long_rest'})
        self.assertEqual(sql.count('EXISTS'),1)
        self.assertEqual(values,['prepared','spell_slot','long_rest'])
        for value in [{},{'mode':'invented'},{'sql':'drop table'},[],{'spell':''}]:
            with self.assertRaises(ValueError):casting_query(value)


DB=Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite'
@unittest.skipUnless(DB.exists(),'Build the corpus first')
class CorpusCastingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True);cls.context=Context(cls.db)
    @classmethod
    def tearDownClass(cls):cls.db.close()

    def test_independent_core_and_expansion_acceptance_list(self):
        cases=json.loads((Path(__file__).parent/'fixtures/casting-chains.json').read_text())
        for case in cases['access']:
            with self.subTest(case=case['name'],generation=case['ruleset']):
                matches=[e for e in self.context.entries.values() if e['name_zh']==case['name'] and e['page_path']==case['path'] and e['ruleset']==case['ruleset']]
                self.assertEqual(len(matches),1)
                e=matches[0];value=parse_access(e,self.context.pages[e['page_path']],self.context.mentions(e))
                self.assertEqual(len(value['grants']),case['grants']);self.assertEqual(value['unparsed'],[])
                self.assertEqual([g['mode'] for g in value['grants']],case['modes'])
                for g in value['grants']:
                    if g['target']['kind']=='entry':
                        self.assertEqual(g['target']['resolution']['status'],'resolved')
                        self.assertEqual(self.context.entries[g['target']['resolution']['targets'][0]]['ruleset'],case['ruleset'])
                    else:self.assertTrue(self.context.choice_options(e,g['target']))
        for case in cases['effects']:
            e=next(e for e in self.context.entries.values() if e['name_zh']==case['name'] and e['page_path']==case['path'] and e['type']=='spell')
            value=parse_spell(e,self.context.pages[e['page_path']])
            self.assertEqual([x['kind'] for x in value['effects']],case['kinds']);self.assertFalse(value['unparsed'])

    def test_background_constraints_survive_the_complete_chain(self):
        from knowledge import chain
        cases=json.loads((Path(__file__).parent/'fixtures/casting-chains.json').read_text())
        for case in cases['acquisition']:
            root=next(e for e in self.context.entries.values() if e['name_zh']==case['name'] and e['page_path']==case['path'])
            result=chain(self.db,root['id']);self.assertTrue(result['options'])
            ids={i for o in result['options'] for i in o['spell_ids']}
            names={self.context.entries[i]['name_zh'] for i in ids}
            self.assertIn(case['allowed_spell'],names);self.assertNotIn(case['excluded_spell'],names)
            for o in result['options']:
                self.assertEqual(o['constraints']['class_list'],[case['class_list']])
                self.assertEqual(len(o['acquisition']),1)
            spell=next(i for i in ids if self.context.entries[i]['name_zh']==case['allowed_spell'])
            selected=chain(self.db,root['id'],spell)
            self.assertTrue(any(r['subject_id']==spell and r['kind']=='spell_effect' for r in selected['records']))
            with self.assertRaises(ValueError):chain(self.db,root['id'],'nonexistent')


if __name__=='__main__':unittest.main()
