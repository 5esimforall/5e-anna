"""Mechanism regressions: checks, outcomes, recurring uses and scoped exceptions."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from spell_effects import parse_spell
from rule_conditions import condition_terms, validate_conditions

TARGET='一道蓝白色的冰冷光束朝施法距离内的一名生物射去。'
ATTACK='对目标进行一次远程法术攻击。'
HIT='命中时，目标将受到1d8点寒冷伤害，且其速度将被减少10尺，直到你的下一回合开始为止。'
WITCH=('一支缠绕着碎裂魔力的蓝色能量长矛射向施法距离内的一个生物，并在你与目标之间生成一道持久的闪电弧。'
       '对目标进行一次远程法术攻击。若命中，则目标受到2d12闪电伤害。'
       '无论首次攻击是否命中，在你接下来的每个回合中，你都可以用附赠动作直接对目标造成1d12闪电伤害。'
       '此外，如果目标与你之间的距离超出了法术的施法距离，或者目标相对你而言处于全身掩护状态，法术将提前终止。'
       '升环施法。使用的法术位每比一环高一环，此法术的初始伤害就提高1d12。')
LAUGHTER=('施法距离内你可见的一名生物进行一次感知豁免。豁免失败，目标在持续时间内陷入失能和倒地状态。'
          '在此期间，目标会不受控制地狂笑（只要它能够发笑），且无法结束自身的倒地状态。'
          '目标在其每回合结束或受到伤害时，可以再进行一次感知豁免。'
          '如果豁免是因受到伤害所致，则该次豁免具有优势。豁免成功时，法术终止。')


def parse(body):
    entry={'page_path':'fixture.htm','start':0,'title_end':0,'end':len(body),'fields':{}}
    return parse_spell(entry,body)


class EffectGrammarTests(unittest.TestCase):
    def test_damage_and_timed_modifier_share_the_same_hit(self):
        value=parse(TARGET+ATTACK+HIT);self.assertFalse(value['unparsed'])
        target,attack,damage,modifier=value['effects']
        self.assertEqual(attack['target']['effect_id'],target['id'])
        self.assertEqual(damage['when']['effect_id'],attack['id'])
        self.assertEqual(modifier['when'],damage['when'])
        self.assertEqual(modifier['ends'],{'kind':'turn','subject':'caster','phase':'start','relative':'next'})
        self.assertEqual(modifier['quantity'],{'kind':'constant','value':10,'unit':'foot'})
        changed=parse(TARGET+ATTACK+HIT.replace('直到你的下一回合开始为止','直到目标逃离水面'))
        self.assertTrue(changed['unparsed']);self.assertFalse(any(e['kind']=='damage' for e in changed['effects']))

    def test_save_outcomes_share_a_check_and_half_refers_to_base_damage(self):
        body='处于20尺锥状区域内的生物必须进行一次体质豁免。豁免失败者受到4d10点雷鸣伤害，豁免成功者受到一半伤害。'
        value=parse(body);self.assertFalse(value['unparsed']);target,check,failed,succeeded=value['effects']
        self.assertEqual(target['selection']['size'],20)
        self.assertEqual(check['ability'],'体质')
        self.assertEqual([e['when']['effect_id'] for e in (failed,succeeded)],[check['id']]*2)
        self.assertEqual([e['when']['result'] for e in (failed,succeeded)],['failure','success'])
        self.assertEqual(succeeded['quantity']['effect_id'],failed['id'])
        self.assertEqual(succeeded['quantity']['factor'],{'numerator':1,'denominator':2})
        self.assertEqual(failed['damage_type'],'thunder')
        self.assertTrue(parse(body.replace('一半伤害','两倍伤害'))['unparsed'])

    def test_ignition_keeps_nested_negative_target_conditions(self):
        value=parse('你对施法距离内一名生物或物件掷出一把火焰，对目标进行一次远程法术攻击。未被着装或携带的可燃物件被该法术命中时将开始燃烧。')
        self.assertFalse(value['unparsed']);ignite=value['effects'][-1]
        terms=ignite['when']['all'];self.assertEqual(terms[0]['result'],'hit')
        self.assertEqual(terms[0]['effect_id'],'effect_2')
        self.assertEqual(terms[-1],{'not':{'any':[{'kind':'target_property','property':'worn'},{'kind':'target_property','property':'carried'}]}})
        wrong=parse(TARGET+'在此锥状区域内的未被着装或携带的易燃物件会开始燃烧。')
        self.assertTrue(wrong['unparsed']);self.assertIsNone(wrong['effects'][-1]['when']['all'][0]['area']['effect_id'])

    def test_repeat_save_and_damage_advantage_do_not_replace_the_initial_check(self):
        value=parse(LAUGHTER);self.assertFalse(value['unparsed'])
        initial,repeat=[e for e in value['effects'] if e['kind']=='saving_throw']
        statuses=[e for e in value['effects'] if e['kind']=='condition']
        self.assertTrue(all(e['when']['effect_id']==initial['id'] for e in statuses))
        self.assertEqual(repeat['repeat_of']['effect_id'],initial['id'])
        self.assertEqual(repeat['when']['all'][0]['effect_id'],initial['id'])
        events=repeat['when']['all'][-1]['any'];self.assertEqual([e['kind'] for e in events],['turn','damage_received'])
        advantage=next(e for e in value['effects'] if e['kind']=='check_modifier')
        self.assertEqual(advantage['check']['effect_id'],repeat['id'])
        self.assertEqual(advantage['when'],{'kind':'damage_received','subject':'target'})
        end=value['effects'][-1];self.assertEqual(end['when']['effect_id'],repeat['id']);self.assertEqual(end['when']['result'],'success')
        laugh=next(e for e in value['effects'] if e['kind']=='behavior')
        self.assertEqual(laugh['when']['all'][0]['effect_id'],initial['id'])
        self.assertEqual(laugh['when']['all'][0]['result'],'failure')

    def test_ongoing_damage_is_independent_of_the_initial_hit_and_upcast(self):
        value=parse(WITCH);self.assertFalse(value['unparsed'])
        initial,ongoing=[e for e in value['effects'] if e['kind']=='damage']
        self.assertEqual(initial['when']['result'],'hit');self.assertEqual(initial['quantity']['count'],2)
        self.assertEqual(ongoing['quantity']['count'],1);self.assertEqual(ongoing['delivery'],'automatic')
        self.assertEqual(ongoing['initial_attack_result'],'irrelevant')
        self.assertEqual(ongoing['use'],{'action':'bonus_action','frequency':'once_per_turn'})
        self.assertFalse(any(c.get('kind')=='attack_result' for c in condition_terms(ongoing['when'])))
        self.assertEqual(value['effects'][-1]['applies_to']['effect_id'],initial['id'])
        end=next(e for e in value['effects'] if e['kind']=='end_spell')
        self.assertEqual([c['kind'] for c in end['when']['any']],['outside_spell_range','cover'])
        changed=parse(WITCH.replace('无论首次攻击是否命中','只有首次攻击命中'))
        self.assertTrue(changed['unparsed']);self.assertFalse(any(e.get('timing')=='ongoing' for e in changed['effects']))

    def test_implicit_ongoing_hit_requirement_stays_a_gap(self):
        value=parse(TARGET+ATTACK+'若命中，则目标受到1d12点闪电伤害。在法术持续时间内，你在每个自己的回合中可以用一个动作直接对目标造成1d12点闪电伤害。')
        self.assertIn('unresolved_ongoing_precondition',[g['reason'] for g in value['unparsed']])
        self.assertEqual(value['effects'][-1]['initial_attack_result'],'not_stated')

    def test_competing_or_interrupted_checks_do_not_bind_by_order(self):
        for prefix in (TARGET+ATTACK+ATTACK,TARGET+ATTACK+'但这一检定仅对巨人生效。'):
            value=parse(prefix+HIT);damage=next(e for e in value['effects'] if e['kind']=='damage')
            self.assertIsNone(damage['when']['effect_id']);self.assertTrue(value['unparsed'])
        missing=parse(ATTACK+HIT);self.assertTrue(missing['unparsed'])
        self.assertIsNone(missing['effects'][0]['target']['effect_id'])

    def test_local_links_and_target_reference_types_are_validated(self):
        value=parse(TARGET+ATTACK+HIT);validate_conditions({'value':value})
        validate_conditions({'value':json.loads(json.dumps(value,sort_keys=True))})
        bad=deepcopy(value);next(link for link in bad['effects'][2]['links'] if link['field']=='when')['target_effect_id']='effect_1'
        with self.assertRaisesRegex(ValueError,'links'):validate_conditions({'value':bad})
        bad=deepcopy(value);bad['effects'][2]['when']['effect_id']='effect_1'
        with self.assertRaisesRegex(ValueError,'type'):validate_conditions({'value':bad})

    def test_shared_attribute_threshold_needs_an_explicit_or(self):
        from rule_conditions import prerequisite_expression
        self.assertEqual([c['ability'] for c in prerequisite_expression('智力、感知或魅力13+')['any']],['智力','感知','魅力'])
        self.assertIsNone(prerequisite_expression('智力、感知13+'))

    def test_repeated_check_outcomes_keep_repeat_timing_in_query_projection(self):
        from effect_queries import effect_rows
        record={'id':'record','subject_id':'spell','kind':'spell_effect','status':'resolved',
                'review_status':'unreviewed','value':parse(LAUGHTER)}
        rows=list(effect_rows([record]))
        repeat=[r for r in rows if r['timing']=='repeat']
        self.assertEqual([r['effect'] for r in repeat],['saving_throw','check_modifier','end_spell'])
        self.assertTrue(all(r['delivery']=='saving_throw' for r in repeat))
        self.assertEqual([r['timing'] for r in rows if r['effect']=='condition'],['immediate','immediate'])


DB=Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite'
@unittest.skipUnless(DB.exists(),'Build the corpus first')
class CorpusEffectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from knowledge import Context
        cls.db=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True);cls.context=Context(cls.db)
    @classmethod
    def tearDownClass(cls):cls.db.close()

    def test_shared_grammar_in_both_generations_without_name_gating(self):
        expected={'Fire Bolt':{'attack_roll','damage','ignite','level_scale'},'Ray of Frost':{'attack_roll','damage','modifier','level_scale'}}
        for generation in ('5e','5r'):
            for name,kinds in expected.items():
                e=next(e for e in self.context.entries.values() if e['type']=='spell' and e.get('name_en')==name and e['ruleset']==generation and e['page_path'].startswith(('topics/玩家手册/','topics/玩家手册2024/')))
                value=parse_spell(e,self.context.pages[e['page_path']]);self.assertFalse(value['unparsed']);self.assertTrue(kinds<={x['kind'] for x in value['effects']})
                renamed={**e,'name':'Unknown','name_zh':'未命名','name_en':'Unknown'}
                self.assertEqual(parse_spell(renamed,self.context.pages[e['page_path']]),value)

    def test_acquisition_reaches_ongoing_use_and_scoped_exception(self):
        from knowledge import chain,casting_condition
        root=next(e for e in self.context.entries.values() if e['name_zh']=='智者' and e['ruleset']=='5r')
        witch=next(e for e in self.context.entries.values() if e.get('name_en')=='Witch Bolt' and e['ruleset']=='5r' and e['type']=='spell')
        result=chain(self.db,root['id'],witch['id']);record=next(r for r in result['records'] if r['subject_id']==witch['id'] and r['kind']=='spell_effect')
        self.assertEqual(record['status'],'resolved')
        query,values=casting_condition({'effect':'damage','damage_type':'lightning','delivery':'automatic','timing':'ongoing'})
        self.assertTrue(self.db.execute('SELECT 1 FROM entries e WHERE e.id=? AND '+query,[root['id'],*values]).fetchone())
        self.assertTrue(result['bindings']);self.assertEqual(result['reading']['path'][0],root['id'])
        ray=next(e for e in self.context.entries.values() if e.get('name_en')=='Ray of Frost' and e['ruleset']=='5r' and e['type']=='spell')
        r=next(r for r in chain(self.db,root['id'],ray['id'])['records'] if r['subject_id']==ray['id'])
        exception=next(link for link in r['value']['rules'] if link['relation']=='exception_to')
        self.assertEqual(exception['change']['field'],'speed');self.assertEqual(exception['when']['result'],'hit')
        self.assertEqual(exception['ends']['phase'],'start')
        scope=exception['scope']['effect_id']
        focused=chain(self.db,root['id'],ray['id'],effect_id=scope)
        self.assertEqual(focused['reading']['effect_id'],scope)


if __name__=='__main__':unittest.main()
