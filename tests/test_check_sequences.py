"""Target scopes, paragraph evidence and cumulative thresholds with rejection cases."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import unittest
from anna.knowledge.spells import parse_spell
from anna.knowledge.conditions import validate_conditions, local_references
from anna.knowledge.effects import effect_rows
from test_casting import feature

STONE=('你尝试将施法距离内一名你可见的生物变为石头。目标必须进行一次体质豁免。'
       '若豁免失败，则目标在法术持续时间内陷入束缚状态。若豁免成功，则目标的速度直至你的下个回合开始前变为0。'
       '构装生物进行此豁免时自动成功。\n'
       '因该法术陷入束缚的生物必须在其每回合结束时再进行一次体质豁免。'
       '在累计三次豁免成功后，该法术终止。'
       '在累计三次豁免失败后，该生物被转化为石头并在法术持续时间内陷入石化状态。'
       '成功或失败的检定不需要连续；只要记录各情况的次数直至其中一方累计三次为止。\n'
       '如果你为该法术维持的专注持续至法术最大持续时间，则该生物陷入的石化状态将持续至被一道高等复原术Greater Restoration或类似的魔法移除为止。')
AREAS=('处于15尺锥状区域内的生物必须进行一次敏捷豁免。\n'
       '处于10尺球状区域内的生物必须进行一次敏捷豁免。\n'
       '该锥状区域内的生物豁免失败时，受到2d6火焰伤害。\n'
       '该球状区域内的生物豁免失败时，受到3d6寒冷伤害。')


def parsed(body):
    value=parse_spell(feature(body),body)
    validate_conditions({'value':value})
    return value


class SequenceTests(unittest.TestCase):
    def test_cross_paragraph_target_identity_selects_the_check_not_just_its_ability(self):
        value=parsed(AREAS);self.assertEqual(value['unparsed'],[])
        effects=value['effects'];first,second=effects[1],effects[3]
        for effect,check in zip(effects[4:],[first,second]):
            self.assertEqual(effect['when']['effect_id'],check['id'])
            self.assertEqual(effect['target']['effect_id'],check['target']['effect_id'])
            self.assertNotEqual(effect['paragraph']['span'],check['paragraph']['span'])
        self.assertEqual(effects[4]['when']['candidate_effect_ids'],[first['id']])
        rows=list(effect_rows([{'id':'r','kind':'procedure','subject_id':'s','status':'resolved','review_status':'unreviewed','value':value}]))
        self.assertEqual([(r['save_ability'],r['target_scope']) for r in rows if r['effect']=='damage'],[('敏捷','area'),('敏捷','area')])
        bad=deepcopy(value);ref=bad['effects'][4]['when'];ref['effect_id']=second['id'];ref['candidate_effect_ids']=[second['id']]
        with self.assertRaisesRegex(ValueError,'scope'):validate_conditions({'value':bad})

    def test_competing_same_shape_missing_subject_and_unknown_intervening_clause_stay_gaps(self):
        for body in [AREAS.replace('10尺球状','10尺锥状'),AREAS.replace('该锥状区域','该立方区域'),
                     AREAS.replace('该锥状区域内的生物豁免失败时','豁免失败时'),
                     AREAS.replace('该锥状区域内的生物豁免失败时','未知限定。该锥状区域内的生物豁免失败时')]:
            with self.subTest(body=body):self.assertTrue(parsed(body)['unparsed'])
        value=parsed(AREAS.replace('10尺球状','10尺锥状'))
        damage=next(e for e in value['effects'] if e['kind']=='damage')
        self.assertIsNone(damage['target']['effect_id'])
        self.assertEqual(damage['target']['candidate_effect_ids'],['effect_1','effect_3'])
        self.assertIsNone(damage['when']['effect_id'])

    def test_paragraph_is_evidence_not_a_license_to_choose_the_last_check(self):
        competing=STONE.replace('目标必须进行一次体质豁免。','目标必须进行一次体质豁免。目标必须进行一次体质豁免。')
        value=parsed(competing);condition=next(e for e in value['effects'] if e['kind']=='condition')
        self.assertIsNone(condition['when']['effect_id'])
        self.assertEqual(condition['when']['candidate_effect_ids'],['effect_2','effect_3'])
        detached=parsed(STONE.replace('若豁免失败','\n若豁免失败',1))
        self.assertTrue(detached['unparsed'])
        for effect in parsed(STONE)['effects']:
            ev=effect['paragraph'];self.assertEqual(STONE[slice(*ev['span'])],ev['raw'])
        bad=parsed(STONE);bad['effects'][2]['paragraph']['span'][0]=9999
        with self.assertRaisesRegex(ValueError,'paragraph'):validate_conditions({'value':bad})

    def test_counts_reference_a_repeat_sequence_and_keep_initial_inclusion_unspecified(self):
        value=parsed(STONE);self.assertEqual(value['unparsed'],[])
        by_id={e['id']:e for e in value['effects']}
        sequence=next(e for e in by_id.values() if e['kind']=='check_sequence')
        repeat=by_id[sequence['check']['effect_id']]
        self.assertEqual(repeat['repeat_of']['effect_id'],'effect_2')
        self.assertEqual(sequence['counting_scope'],'per_target')
        self.assertEqual(sequence['initial_inclusion'],'not_stated')
        thresholds=[r for e in by_id.values() for _,r in local_references(e) if r['kind']=='check_total']
        self.assertEqual([(r['result'],r['count'],r['effect_id']) for r in thresholds],[('success',3,sequence['id']),('failure',3,sequence['id'])])
        policy=next(e for e in by_id.values() if e['kind']=='sequence_policy')
        self.assertFalse(policy['consecutive']);self.assertEqual(policy['stop'],{'success':3,'failure':3})
        lasting=by_id['effect_11'];self.assertEqual(lasting['applies_to']['effect_id'],'effect_9')
        self.assertEqual(lasting['ends'],{'kind':'magic_removal','spell_name':'高等复原术','similar_magic':True})
        override=by_id['effect_5'];self.assertEqual(override['applies_to']['effect_id'],'effect_2')
        self.assertEqual(override['when']['value'],'construct')

    def test_multiple_sequences_do_not_pool_different_targets(self):
        body=AREAS+'\n该锥状区域内的每个生物在其每回合结束时必须重复此豁免。\n该球状区域内的每个生物在其每回合结束时必须重复此豁免。\n该锥状区域内的每个生物累计三次豁免失败后，在持续时间内陷入石化状态。'
        value=parsed(body);self.assertFalse(value['unparsed'])
        sequences=[e for e in value['effects'] if e['kind']=='check_sequence'];condition=value['effects'][-1]
        self.assertEqual(condition['when']['effect_id'],sequences[0]['id'])
        self.assertEqual(condition['when']['target']['effect_id'],sequences[0]['target']['effect_id'])
        self.assertNotEqual(sequences[0]['target']['effect_id'],sequences[1]['target']['effect_id'])
        unknown=parsed(body+'在累计三次豁免成功后，该法术终止。')
        self.assertIsNone(unknown['effects'][-1]['when']['effect_id'])
        self.assertEqual(unknown['effects'][-1]['when']['candidate_effect_ids'],[s['id'] for s in sequences])

    def test_invalid_missing_and_conflicting_thresholds_are_not_effective(self):
        for old,new in [('累计三次豁免成功','累计零次豁免成功'),('累计三次豁免成功','累计四次豁免成功'),
                        ('成功或失败的检定不需要连续','成功或失败的检定必须连续'),
                        ('在累计三次豁免成功后，该法术终止。','在累计三次豁免成功后，该法术终止，但保留未知例外。')]:
            self.assertTrue(parsed(STONE.replace(old,new))['unparsed'])
        competition=parsed(STONE.replace('累计三次豁免成功','累计四次豁免成功'))
        self.assertIn('competing_sequence_threshold',{g['reason'] for g in competition['unparsed']})
        missing=parsed('指定施法距离内一个你能看见的生物。在累计三次豁免成功后，该法术终止。')
        self.assertTrue(missing['unparsed']);self.assertIsNone(missing['effects'][-1]['when']['effect_id'])
        for count in [0,-1,True,'3']:
            bad=parsed(STONE);bad['effects'][7]['when']['count']=count
            with self.assertRaisesRegex(ValueError,'threshold'):validate_conditions({'value':bad})


DB=Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite'
@unittest.skipUnless(DB.exists(),'Build the corpus first')
class CorpusSequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from anna.knowledge.repository import Context
        cls.db=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True);cls.ctx=Context(cls.db)
    @classmethod
    def tearDownClass(cls):cls.db.close()

    def test_two_generation_attack_and_area_identity_are_not_pooled(self):
        for generation,prefix in [('5e','topics/珊娜萨'),('5r','topics/玩家手册2024/')]:
            e=next(e for e in self.ctx.entries.values() if e.get('name_en')=='Ice Knife' and e['ruleset']==generation and e['page_path'].startswith(prefix))
            body=self.ctx.pages[e['page_path']];value=parse_spell(e,body);self.assertFalse(value['unparsed'])
            effects=value['effects'];self.assertEqual(effects[4]['selection']['origin']['effect_id'],effects[0]['id'])
            self.assertEqual(effects[2]['target']['effect_id'],effects[0]['id'])
            self.assertEqual(effects[6]['target']['effect_id'],effects[4]['id'])
            self.assertEqual(effects[6]['when']['effect_id'],effects[5]['id'])
            self.assertEqual(effects[7]['applies_to']['effect_id'],effects[6]['id'])
            self.assertEqual(effects[3]['initial_attack_result'],'irrelevant')
            self.assertEqual(parse_spell({**e,'name':'Renamed','name_en':'Renamed','name_zh':'改名'},body),value)

    def test_cumulative_spell_keeps_local_rules_and_unknown_legacy_clauses(self):
        from anna.knowledge.repository import record_for
        for generation in ('5e','5r'):
            e=next(e for e in self.ctx.entries.values() if e.get('name_en')=='Flesh to Stone' and e['ruleset']==generation)
            v=parse_spell(e,self.ctx.pages[e['page_path']]);r=record_for(e,'procedure',v,self.ctx)
            self.assertEqual(r['status'],'resolved' if generation=='5r' else 'partial')
            sequence=next(x for x in v['effects'] if x['kind']=='check_sequence')
            self.assertEqual(sequence['initial_inclusion'],'not_stated')
            cumulative=next(x for x in v['effects'] if x['kind']=='end_spell')
            link=next(x for x in v['rules'] if x['scope'].get('effect_id')==cumulative['id'] and x['relation']=='exception_to')
            self.assertEqual(link['when']['effect_id'],sequence['id'])
            self.assertEqual(link['change']['field'],'effect.duration')
            if generation=='5e':self.assertEqual(len(v['unparsed']),2)
            else:
                removal=next(x for x in v['rules'] if x['relation']=='removed_by')
                self.assertEqual(removal['target']['name'],'高等复原术')
                self.assertTrue(removal['target']['resolution']['targets'])


if __name__=='__main__':unittest.main()
