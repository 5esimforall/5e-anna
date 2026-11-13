"""Entry typing: declared evidence, recognizer precedence, and the printed evidence a decision overrules."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from html_extract import Candidate
from entry_types import RECOGNIZERS, RULE_EVIDENCE, PRINTED, INFERRED, UNTYPED, classify, evidence_status, refine_page_types, type_conflicts

SPELL_FIELDS='\n施法时间：1 动作\n施法距离：60 尺\n法术成分：V、S\n持续时间：立即\n'
CLASS_LAYOUT='\n核心特质\n生命骰：每战士等级 1d10\n'
REAL_DB=Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite'


def typed(name,body='',path='topics/测试书/规则.htm',primary=False,full=None,method='heading',kinds=(),**fields):
    c=Candidate(0,len(name),name,4,method,kinds=list(kinds),fields=dict(fields))
    own=name+body
    return classify(c,own,full if full is not None else own,path,primary)


class EvidenceTests(unittest.TestCase):
    def test_every_recognizer_declares_its_evidence(self):
        self.assertTrue(RECOGNIZERS)
        self.assertTrue(all(r.rule in RULE_EVIDENCE for r in RECOGNIZERS))
        self.assertEqual(set(RULE_EVIDENCE.values()),{PRINTED,INFERRED,UNTYPED})

    def test_status_follows_the_declared_evidence(self):
        self.assertEqual(evidence_status('name_label','condition'),PRINTED)
        self.assertEqual(evidence_status('equipment_table','equipment'),PRINTED)
        self.assertEqual(evidence_status('topic_context','hazard'),INFERRED)
        self.assertEqual(evidence_status('document_structure','section'),UNTYPED)

    def test_undeclared_or_inconsistent_rules_are_rejected(self):
        with self.assertRaises(ValueError):evidence_status('guessed_from_title','spell')
        # Only the structure decision leaves an entry untyped, and it never types one.
        with self.assertRaises(ValueError):evidence_status('document_structure','spell')
        with self.assertRaises(ValueError):evidence_status('name_label','section')


class ClassifyTests(unittest.TestCase):
    def test_first_recognizer_decides_and_later_printed_evidence_is_kept(self):
        t=typed('甲 Alpha【状态】',SPELL_FIELDS)
        self.assertEqual((t.type,t.rule),('spell','spell_fields'))
        self.assertEqual(t.printed_types,(('spell','spell_fields'),('condition','name_label')))
        self.assertEqual(type_conflicts(t.type,t.printed_types),[{'type':'condition','rule':'name_label'}])

    def test_agreeing_or_inferred_evidence_is_no_conflict(self):
        t=typed('甲 Alpha【状态】')
        self.assertEqual((t.type,t.rule,t.printed_types),('condition','name_label',(('condition','name_label'),)))
        self.assertEqual(type_conflicts(t.type,t.printed_types),[])
        # A glossary heading would be a rule by context; after a printed decision, context is not evidence.
        t=typed('乙 Beta',SPELL_FIELDS,path='topics/测试书/术语汇编/乙.htm')
        self.assertEqual((t.type,t.printed_types),('spell',(('spell','spell_fields'),)))

    def test_every_index_kind_is_printed_evidence(self):
        t=typed('丙 Gamma',method='index_anchor',kinds=['magic_item','spell'])
        self.assertEqual((t.type,t.rule),('magic_item','index_or_statblock'))
        self.assertEqual(type_conflicts(t.type,t.printed_types),[{'type':'spell','rule':'index_or_statblock'}])
        t=typed('丙 Gamma',method='spell_signature',kinds=['spell'])
        self.assertEqual((t.type,t.rule),('spell','spell_signature'))

    def test_a_class_page_primary_reads_the_whole_page(self):
        own,full='战士 Fighter','战士 Fighter'+CLASS_LAYOUT
        # Under a class folder, or wherever the page prints a class's hit die and core traits.
        for path in ['topics/测试书/职业/战士/战士.htm','topics/测试书/第三方/新职业.htm']:
            t=classify(Candidate(0,len(own),own,1,'heading'),own,full,path,True)
            self.assertEqual((t.type,t.rule),('class','class_structure'),path)
        # Any other entry reads its own text, and a page without the layout is no class page.
        t=classify(Candidate(0,len(own),own,1,'heading'),own,full,'topics/测试书/第三方/新职业.htm',False)
        self.assertEqual(t.type,'section')
        t=classify(Candidate(0,len(own),own,1,'heading'),own,own,'topics/测试书/第三方/新职业.htm',True)
        self.assertEqual(t.type,'section')

    def test_stat_and_background_fields_open_their_lines(self):
        stats=['\n护甲等级：类人形态下11\n生命值：58（9d8 +18）\n力量15（+2） 敏捷13（+1）\n',
               '\nAC18 (天生护甲) HP 65 (10d8 + 20)\n力量 16 (+3) 敏捷 16 (+3)\n']
        prose=['\n你对抗此次攻击的护甲等级会获得+2加值。将你盟友的生命值降为0。你的力量或敏捷提升1。\n',
               '\n1. 力量值\n2. 敏捷值\n4. 护甲等级\n5. 当前生命值\n',
               '\n每颗石头拥有AC 24，生命值为10。它失去力量与敏捷。\n']
        for body in stats:self.assertEqual(typed('怪 Monster',body).type,'creature',body)
        for body in prose:self.assertEqual(typed('怪 Monster',body).type,'section',body)
        fields='\n技能熟练项：欺瞒、隐匿\n工具熟练项：制毒工具\n语言：任选一门语言\n初始装备：普通服装\n'
        self.assertEqual(typed('甲 Alpha',fields,primary=True).rule,'background_fields')
        self.assertEqual(typed('甲 Alpha','\n技能熟练。运动\n工具熟练。一种游戏装置\n装备。一个拼图盒\n',primary=True).type,'background')
        self.assertEqual(typed('甲 Alpha','\n你的技能熟练项和工具熟练项由总部提供，熟练和起始装备见下表。\n',primary=True).type,'section')

    def test_a_page_introduction_with_a_field_layout_is_the_page_definition(self):
        fields='\n技能熟练：欺瞒，威吓\n工具熟练：易容工具\n装备：一套易容工具\n'
        self.assertEqual(typed('背景-假面人',fields,primary=True,method='page_intro').type,'background')
        self.assertEqual(typed('假面人','\n本章介绍假面人。\n',primary=True,method='page_intro').rule,'document_structure')

    def test_skill_leads_are_printed_skills(self):
        t=typed('运动Athletics',method='skill_lead')
        self.assertEqual((t.type,t.rule,t.printed_types),('skill','skill_notation',(('skill','skill_notation'),)))
        self.assertEqual(evidence_status(t.rule,t.type),PRINTED)
        # The same name as an ordinary heading has no notation behind it.
        self.assertEqual(typed('运动Athletics').type,'section')

    def test_structure_and_named_tables(self):
        self.assertEqual(typed('目录').rule,'document_structure')
        self.assertEqual(typed('动作Actions',path='topics/测试书/生物/怪.htm').type,'section')
        self.assertEqual(typed('动作Actions',path='topics/测试书/进行游戏/战斗.htm').type,'rule')
        t=typed('随机遭遇',table_span=[0,10])
        self.assertEqual((t.type,t.rule),('lore','named_table_structure'))


def page_entries(*names,parents=None,types=None):
    """A rules page: its primary rule and the given headings, each a child of the primary unless `parents` says."""
    out=[{'name':'各属性应用场景Using Each Ability','type':'rule','classification_rule':'context_heading','parent_index':None,
          'start':0,'title_end':10,'end':1000,'boundary_rule':'heading','is_primary':True}]
    for i,name in enumerate(names,1):
        out.append({'name':name,'type':(types or {}).get(name,'section'),'classification_rule':'document_structure',
                    'parent_index':(parents or {}).get(name,0),'start':i*10,'title_end':i*10+5,'end':i*10+10,
                    'boundary_rule':'heading','is_primary':False})
    return out


class CheckedAbilityTests(unittest.TestCase):
    def typed_page(self,*names,**kw):
        entries=page_entries(*names,**kw);refine_page_types(entries,' '*1000)
        return {e['name']:(e['type'],e['classification_rule']) for e in entries[1:]}

    def test_an_ability_heading_owns_the_check_heading_that_follows_it(self):
        types=self.typed_page('力量Strength','力量检定Strength Checks','攻击检定与伤害Attack Rolls and Damage','敏捷Dexterity','敏捷检定 Dexterity Check')
        self.assertEqual(types['力量Strength'],('ability','checked_ability'))
        self.assertEqual(types['力量检定Strength Checks'],('rule','ability_check_heading'))
        self.assertEqual(types['敏捷Dexterity'],('ability','checked_ability'))
        self.assertEqual(types['敏捷检定 Dexterity Check'],('rule','ability_check_heading'))
        # The ability's other uses stay where they were.
        self.assertEqual(types['攻击检定与伤害Attack Rolls and Damage'][0],'section')
        self.assertEqual(evidence_status('checked_ability','ability'),INFERRED)

    def test_a_check_heading_needs_its_own_ability_right_before_it(self):
        cases=[('力量检定Strength Checks',),                                      # no ability before it
               ('力量Strength','攻击检定与伤害Attack Rolls and Damage','力量检定Strength Checks'),  # not adjacent
               ('力量Strength','力量检定Might Checks'),                           # another English name
               ('力量','力量检定'),                                              # no printed English names
               ('团体Group','团体检定Group Checks Explained')]                    # a title that only starts the same
        for names in cases:
            self.assertTrue(all(t=='section' for t,_ in self.typed_page(*names).values()),names)
        # Headings at different levels are not a pair, and a typed entry keeps its type.
        nested=self.typed_page('力量Strength','力量检定Strength Checks',parents={'力量检定Strength Checks':1})
        self.assertEqual(nested['力量Strength'][0],'section')
        typed_feature=self.typed_page('力量Strength','力量检定Strength Checks',types={'力量Strength':'class_feature'})
        self.assertEqual(typed_feature,{'力量Strength':('class_feature','document_structure'),'力量检定Strength Checks':('section','document_structure')})


@unittest.skipUnless(REAL_DB.exists(),'Build the dataset first')
class CorpusEvidenceTests(unittest.TestCase):
    def test_built_entries_carry_declared_evidence_and_real_conflicts(self):
        db=sqlite3.connect(REAL_DB.resolve().as_uri()+'?mode=ro',uri=True)
        for typ,status,raw in db.execute("SELECT type,classification_status,data FROM entries"):
            e=json.loads(raw)
            self.assertEqual(evidence_status(e['classification_rule'],typ),status,e['id'])
            self.assertTrue(all(c['type']!=typ and RULE_EVIDENCE[c['rule']]==PRINTED for c in e['type_conflicts']),e['id'])
        db.close()


if __name__=='__main__':unittest.main()
