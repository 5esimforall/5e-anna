import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from html_extract import Document,resolve_link,split_name,key,printed_type,hazard_member,NO_TOC_PLACE
from build import parse_one,normalize_value,observation_fields
from source_extract import SourceRegistry

from paths import WEB

class StructureTests(unittest.TestCase):
    def test_omitted_td(self):
        d=Document('topics/test.htm','<table><tr><td>A<td>B<tr item="剑Sword"><td><a href="x.htm#Sword">剑Sword</a><td>珍稀</table>')
        obs=d.quickrefs();self.assertEqual(len(obs),1);self.assertEqual(obs[0]['cells'],['剑Sword','珍稀']);d.close()

    def test_links(self):
        self.assertEqual(resolve_link('topics/a/b.htm','../%E4%B9%A6.htm#A%20B'),('topics/书.htm','A B'))
        self.assertEqual(resolve_link('topics/a.htm','#A'),('topics/a.htm','A'))
        self.assertIsNone(resolve_link('topics/a.htm','https://example.com/'))
        self.assertIsNone(resolve_link('topics/a.htm','../../secret'))
        self.assertEqual(resolve_link('topics/a.htm','b.htm#A/B'),('topics/b.htm','A/B'))

    def test_legacy_scope(self):
        raw='<h2>专长 Feats</h2><p>标注Legacy的是旧版。</p><p><font color="#800000"><b>甲 Alpha</b><sup>Legacy</sup></font><br>正文</p><p><font color="#800000"><b>乙 Beta</b></font><br>正文</p>'
        d=Document('topics/test.htm',raw);cs=d.candidates()
        self.assertTrue(next(c for c in cs if 'Alpha' in c.name).local_legacy)
        self.assertFalse(next(c for c in cs if 'Beta' in c.name).local_legacy)
        self.assertFalse(cs[0].local_legacy);d.close()

    def test_bold_descriptor(self):
        d=Document('topics/test.htm','<h2>专长</h2><p><b>甲 Alpha</b><br><i>起源专长</i><br>正文</p><ul><li><b>能力 Ability。</b>正文</li></ul>')
        cs=d.candidates();self.assertEqual(len(cs),2);self.assertEqual(cs[1].name,'甲 Alpha');d.close()

    def test_unknown_anchor_not_guessed(self):
        d=Document('topics/test.htm','<h4 id="A">甲 Alpha</h4><p>正文</p><h4 id="B">乙 Beta</h4><p>正文</p>')
        ob={'id':'o','target_anchor':'missing','name':'丙 Gamma','kind':'spell'}
        self.assertFalse(any(c.observations for c in d.candidates([ob])));d.close()

    def test_anchor_exact_name_recovery(self):
        d=Document('topics/test.htm','<h4 id="A">甲｜Alpha</h4><p>正文</p>')
        ob={'id':'o','target_anchor':'stale','name':'甲Alpha','kind':'spell'}
        cs=d.candidates([ob]);self.assertEqual(cs[0].observations,[ob]);d.close()

    def test_ambiguous_anchor(self):
        d=Document('topics/test.htm','<h4 id="A">甲 Alpha</h4><h4 id="A">甲 Alpha</h4>')
        cs=d.candidates([{'id':'o','target_anchor':'A','name':'甲Alpha','kind':'spell'}])
        self.assertFalse(any(c.observations for c in cs));d.close()

    def test_normalization(self):
        self.assertEqual(split_name('粉碎者 CrusherLegacy'),('粉碎者','Crusher'))
        self.assertEqual(normalize_value('cr','1/4（XP50）'),'1/4')
        self.assertEqual(normalize_value('ritual','×'),False)
        self.assertEqual(normalize_value('casting_time','其他'),'其他')

    def test_printed_type_label(self):
        self.assertEqual(printed_type('目盲 Blinded【状态】'),'condition')
        self.assertEqual(printed_type('燃烧 Burning【危害】'),'hazard')
        self.assertEqual(printed_type('【状态】Condition'),'')
        self.assertEqual(printed_type('护甲等级 17（天生护甲）'),'')

    def test_hazard_member_needs_a_defined_rule(self):
        for body in ['必须成功通过一次DC 10的体质豁免','承重为3d10×10磅','D6 钥匙','在所有感知（察觉）检定中处于劣势','视为轻度遮蔽','将提升一级力竭状态']:
            self.assertTrue(hazard_member('甲 Alpha',body),body)
        for body in ['世界也是一个危险的所在','其效果和反魔法场法术一样','D&D 的世界充满灾害','每1个小时都被算作2个小时']:
            self.assertFalse(hazard_member('甲 Alpha',body),body)
        self.assertFalse(hazard_member('如果你掷出 DC 10。',"DC 10"))

class CorpusRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=SourceRegistry(WEB)

    def parse(self,rel,toc=True):
        path='topics/'+rel
        return parse_one((str(WEB),path,[],rel,self.registry.toc_place(path) if toc else NO_TOC_PLACE,False))

    def test_2014_feats(self):
        r=self.parse('玩家手册/自定义选项/专长.html')
        self.assertEqual(sum(c['type']=='feat' for c in r['candidates']),42)
        self.assertTrue(any(split_name(c['name'])[1]=='Alert' for c in r['candidates']))

    def test_feat_descriptions_not_feat(self):
        paths=list((WEB/'topics/玩家手册2024/专长').glob('*.htm'))
        candidates=[c for p in paths for c in self.parse(p.relative_to(WEB/'topics').as_posix())['candidates']]
        headers=[c for c in candidates if c['name'].startswith('专长描述')]
        self.assertTrue(headers)
        self.assertTrue(all(c['type']=='section' for c in headers))
        self.assertEqual(sum(c['type']=='feat' for c in candidates),75)

    def test_2024_origin_feats(self):
        r=self.parse('玩家手册2024/专长/起源专长.htm')
        feats=[c for c in r['candidates'] if c['type']=='feat'];self.assertEqual(len(feats),10)
        for c in feats:
            v,a,b=c['fields']['feat_category'][0]
            self.assertIn(v,r['page']['text'][c['start']+a:c['start']+b])
        self.assertTrue(any('Magic Initiate' in c['name'] for c in feats))

    def test_tce_mixed_legacy(self):
        r=self.parse('塔莎的万事坩埚/玩家选项/专长.html')
        fs=[c for c in r['candidates'] if c['type']=='feat'];self.assertEqual(len(fs),15)
        flags={split_name(c['name'])[1]:c['legacy_direct'] for c in fs}
        self.assertTrue(flags['Chef']);self.assertTrue(flags['Crusher']);self.assertFalse(flags['Artificer Initiate']);self.assertFalse(flags['Gunner'])

    def test_partner_feats(self):
        r=self.parse('第三方/歪曲之月/第五章/起源专长.htm')
        self.assertEqual(sum(c['type']=='feat' for c in r['candidates']),12)

    def test_subclass_features(self):
        r=self.parse('玩家手册2024/角色职业/野蛮人/狂战士道途.htm')
        self.assertEqual(r['candidates'][0]['type'],'subclass')
        self.assertEqual(sum(c['type']=='class_feature' for c in r['candidates']),4)

    def test_class_structure_names_the_class_outside_a_class_path(self):
        for path,features in [('第三方/吸血鬼：避世潜藏/血族职业/血族.htm',13),('其他/新UA/灵能/灵能使.htm',11)]:
            r=self.parse(path)
            self.assertEqual(r['candidates'][0]['type'],'class',path)
            self.assertEqual(sum(c['type']=='class_feature' for c in r['candidates']),features,path)

    def test_general_hit_die_rules_are_not_classes(self):
        r=self.parse('玩家手册2024/创建角色/等级提升.htm')
        self.assertNotEqual(r['candidates'][0]['type'],'class')
        self.assertFalse(any(c['type']=='class_feature' for c in r['candidates']))

    def test_subclass_features_begin_at_third_level(self):
        for path,features in [('第三方/吸血鬼：避世潜藏/血族职业/冈格罗.htm',7),
                              ('其他/新UA/灵能/传心师.htm',7),
                              ('其他/新UA/xge子职业更新/晓骑士.htm',7),
                              ('第三方/鬼魅幽谷/玩家指南/第二章/捕猎会.htm',6)]:
            r=self.parse(path)
            self.assertEqual(r['candidates'][0]['type'],'subclass',path)
            self.assertEqual(sum(c['type']=='class_feature' for c in r['candidates']),features,path)

    def test_condition_topic_types_its_children(self):
        r=self.parse('玩家手册/状态.html')
        self.assertEqual(r['candidates'][0]['type'],'rule')
        self.assertEqual([c['name'] for c in r['candidates'] if c['type']=='condition'],
                         ['目盲Blinded','魅惑Charmed','耳聋Deafened','恐慌Frightened','受擒Grappled','失能Incapacitated','隐形Invisible','麻痹Paralyzed','石化Petrified','力竭Exhaustion','中毒Poisoned','倒地Prone','束缚Restrained','震慑Stunned','昏迷Unconscious'])
        r=self.parse('第三方/胧忆岛/状态.htm')
        self.assertEqual([c['name'] for c in r['candidates'] if c['type']=='condition'],['镇定 Pacify'])

    def test_condition_topic_keeps_rules_as_sections(self):
        r=self.parse('玩家手册2024/进行游戏/状态.htm')
        self.assertFalse(any(c['type']=='condition' for c in r['candidates']))
        self.assertEqual(sum(c['type']=='section' for c in r['candidates']),2)

    def hazards(self,rel,rule=None,toc=True):
        return [c['name'] for c in self.parse(rel,toc)['candidates'] if c['type']=='hazard' and rule in {None,c['classification_rule']}]

    def test_printed_type_labels(self):
        self.assertEqual(self.hazards('玩家手册2024/术语汇编/危害.htm','name_label'),
                         ['燃烧 Burning【危害】','脱水 Dehydration【危害】','坠落 Falling【危害】','饥饿 Malnutrition【危害】','窒息 Suffocation【危害】'])
        self.assertEqual([c['name'] for c in self.parse('第三方/德城怪物/附录C/污染.htm')['candidates'] if c['type']=='condition'],['污染 Contamination 【状态】'])
        # A heading that is only the label defines the label itself, whichever label it is.
        labels={c['name']:c['type'] for c in self.parse('玩家手册2024/术语汇编/术语速查.htm')['candidates']}
        self.assertEqual({labels['【动作】Action'],labels['【状态】Condition'],labels['【危害】 Hazard']},{'rule'})

    def test_hazard_descriptor_is_a_signature(self):
        names=self.hazards('城主指南2024/3.地下城主工具箱/危害.htm','hazard_signature')
        self.assertEqual(len(names),12);self.assertIn('蛛网Webs',names)
        # The section's own rule headings print no severity band.
        self.assertEqual(self.hazards('城主指南2024/3.地下城主工具箱/危害.htm'),names)

    def test_hazard_catalog_heading_types_the_run_that_follows(self):
        # Weather before the heading and foraging after the run are not hazards.
        self.assertEqual(self.hazards('城主指南/冒险之主/环境/野外求生.htm','topic_context'),
                         ['亵渎之地Desecrated Ground','冰水Frigid Water','流沙Quicksand','刃藤Razorvine','光滑冰面Slippery Ice','薄冰Thin Ice'])
        self.assertEqual(self.hazards('城主指南/冒险之主/环境/地下城.html'),['棕霉菌Brown Mold','绿软泥Green Slime','黄霉菌Yellow Mold'])

    def test_environment_page_types_effects_that_apply_rules(self):
        r=self.parse('城主指南2024/3.地下城主工具箱/环境影响.htm')
        self.assertEqual((r['candidates'][0]['type'],r['candidates'][0]['classification_rule']),('rule','topic_context'))
        names=self.hazards('城主指南2024/3.地下城主工具箱/环境影响.htm','topic_context')
        self.assertEqual(len(names),10);self.assertIn('薄冰Thin Ice',names);self.assertIn('重度雨雪Heavy Precipitation',names)
        # An effect that applies no defined rule stays untyped rather than borrowing the page's category.
        self.assertNotIn('死魔法区域Dead Magic Zone',names);self.assertNotIn('高海拔High Altitude',names)

    def test_toc_section_types_one_hazard_per_page(self):
        tce='塔莎的万事坩埚/城主工具/环境灾害/'
        for rel in ['雪崩.htm','魔法泉.htm','奇异道路.htm']:
            self.assertEqual(len(self.hazards(tce+rel,'topic_context')),1,rel)
            # The page body alone carries no category; the TOC section supplies it.
            self.assertEqual(self.hazards(tce+rel,toc=False),[],rel)
        self.assertEqual(len(self.hazards(tce+'奥法风暴.htm')),5)
        # Subsection pages list further pages; a lookup table of spells applies no rule.
        self.assertTrue(self.registry.toc_place('topics/'+tce+'超自然区域.htm')['lists_pages'])
        self.assertFalse(self.registry.toc_place('topics/'+tce+'雪崩.htm')['lists_pages'])
        for rel in ['自然灾害.htm','魔法现象.htm','超自然区域.htm','自然灾害的法术替代品.htm']:
            self.assertEqual(self.hazards(tce+rel),[],rel)
        self.assertEqual(self.parse('塔莎的万事坩埚/环境灾害.htm')['candidates'][0]['type'],'rule')
        # A stat block owns its page identity, and its 动作 heading belongs to the stat block.
        r=self.parse(tce+'拟身怪群落.htm')
        self.assertFalse(any(c['type']=='hazard' for c in r['candidates']))
        self.assertEqual(sum(c['type']=='creature' for c in r['candidates']),1)

    def test_hazard_rules_sections_are_not_catalogs(self):
        # Bare 危害 heads a rules section; its 团体检定 applies a rule but defines no hazard.
        self.assertEqual(self.hazards('模组/盐沼幽魂/船舶与海洋/在海上旅行.htm'),[])
        self.assertEqual(self.hazards('城主指南/冒险之主/环境/陷阱.html'),[])

    def test_background_grants(self):
        r=self.parse('玩家手册2024/角色起源/背景/侍僧.htm')
        c=r['candidates'][0];self.assertEqual(c['type'],'background')
        self.assertIn('魔法学徒',c['fields']['granted_feat'][0][0])

    def test_statblock_not_actions(self):
        r=self.parse('怪物图鉴2025/元素/鸟羽人/鸟羽人风舞术士.htm')
        cs=[c for c in r['candidates'] if c['type']=='creature'];self.assertEqual(len(cs),1)
        self.assertIn('Aarakocra',cs[0]['name']);self.assertIn('风之法杖',r['page']['text'][cs[0]['start']:cs[0]['end']])

    def test_spell_evidence(self):
        r=self.parse('玩家手册2024/法术详述/1环.htm');cs=[c for c in r['candidates'] if c['type']=='spell']
        self.assertEqual(len(cs),64)
        for c in cs:
            for field in ['spell_level','school']:
                v,a,b=c['fields'][field][0]
                self.assertIn(v,r['page']['text'][c['start']+a:c['start']+b])

    def test_old_class_and_flavor(self):
        r=self.parse('玩家手册/职业/战士.html')
        self.assertEqual(r['candidates'][0]['type'],'class')
        self.assertEqual(next(c for c in r['candidates'] if 'Trained for Danger' in c['name'])['type'],'section')

    def test_unspaced_ac(self):
        r=self.parse('怪物图鉴/构装生物/三元冢.htm')
        self.assertEqual(sum(c['type']=='creature' for c in r['candidates']),1)

    def test_unstyled_magic_items(self):
        r=self.parse('第三方/德拉肯海姆/宝藏/魔法物品.htm')
        self.assertTrue(any('Aqua Delerium' in c['name'] and c['type']=='magic_item' for c in r['candidates']))

    def test_old_poisons(self):
        r=self.parse('城主指南/规则之主/运作游戏/毒药.html')
        self.assertEqual(sum(c['type']=='poison' for c in r['candidates']),14)

    def test_comments_excluded(self):
        d=Document('topics/test.htm','<!--EndFragment--><h2>标题 Title</h2><p>正文</p>')
        self.assertNotIn('EndFragment',d.text);d.close()

    def test_source_scope_and_cross_directory(self):
        registry=SourceRegistry(WEB)
        source=next(s for s in registry.sources.values() if s['name']=='城主指南2014')
        self.assertEqual(source['ruleset_hint'],'5e');self.assertTrue(source['archived'])
        source=next(s for s in registry.sources.values() if s['name']=='塔莎的万事坩埚')
        self.assertFalse(source['archived']);self.assertEqual(source['ruleset_hint'],'5e')
        owners,_=registry.owners('topics/第三方/德拉肯海姆/生物/点灯人.htm')
        self.assertTrue(any('探寻走私者之秘' in registry.sources[s]['name'] for s in owners))
        self.assertFalse(any(s['ruleset_hint']=='冲突' for s in registry.sources.values()))

    def test_catalog_abbreviations_name_what_they_identify(self):
        registry=SourceRegistry(WEB);by_name={s['name']:s for s in registry.sources.values()}
        # Mixed-case and punctuated codes are the catalog's own; no alias is promoted by its spelling.
        self.assertEqual(by_name['被遗忘的国度：费伦英雄']['abbreviations'],['FRHoF'])
        self.assertEqual(by_name['瓦尔达的秘密尖塔 / 玩家包Ⅰ']['abbreviations'],['VSS:PP'])
        # A collection's code is shared by its publications, so it is recorded apart from their own codes.
        ua=by_name['破解奥秘（UA） / 灵能I']
        self.assertEqual((ua['abbreviations'],ua['collection_abbreviations']),([],['UA']))
        adventure=by_name['模组相关 / 纸质出版物 / 黄金宝库之钥']
        self.assertEqual((adventure['abbreviations'],adventure['collection_abbreviations']),([],[]))
        codes=[c for s in registry.sources.values() for c in s['abbreviations']]
        self.assertEqual(len(codes),len(set(codes)))

    def test_folder_sources_read_the_catalog_like_toc_labels(self):
        from build import page_role
        registry=SourceRegistry(WEB)
        paths=sorted(p.relative_to(WEB).as_posix() for p in (WEB/'topics').rglob('*') if p.suffix.lower() in {'.htm','.html'})
        registry.register_orphans([p for p in paths if page_role(p)=='document'])
        by_name={s['name']:s for s in registry.sources.values()}
        # A catalog code in the folder name (新UA, beyond drops) names the collection; the folder joins
        # the group of the TOC sources the same catalog row describes.
        ua=by_name['其他/新UA/奥术子职']
        self.assertEqual((ua['coverage'],ua['abbreviations'],ua['collection_abbreviations']),('filesystem_only',[],['UA']))
        self.assertEqual((ua['group'],ua['source_type'],ua['release_status']),('破解奥秘UA','测试材料','测试'))
        self.assertEqual(by_name['其他/beyond drops']['collection_abbreviations'],['Drops'])
        # A folder named after a collection is that collection; its subfolders are its members.
        vss=by_name['第三方/瓦尔达的秘密尖塔']
        self.assertEqual((vss['abbreviations'],vss['collection_abbreviations'],vss['ruleset_hint']),(['VSS'],[],'5r'))
        member=by_name['第三方/瓦尔达的秘密尖塔/子职']
        self.assertEqual((member['abbreviations'],member['collection_abbreviations'],member['ruleset_hint']),([],['VSS'],'5r'))
        # Without a catalog row the folder keeps only what its path says, and says it is unverified.
        bestiary=by_name['第三方/万兽图志']
        self.assertEqual((bestiary['catalog_ids'],bestiary['origin'],bestiary['release_status'],bestiary['metadata_status']),([],'合作','待核','待核'))
        # A folder that is a catalog-only publication fills that publication instead of duplicating it.
        self.assertEqual((by_name['憎恶魔窟']['coverage'],by_name['憎恶魔窟']['abbreviations']),('filesystem_only',['AV']))
        self.assertEqual(sum(s['name']=='憎恶魔窟' for s in registry.sources.values()),1)

    def test_source_labels_are_the_shortest_unique_evidence(self):
        from source_extract import source_labels
        def src(name,own=(),collection=()):return {'name':name,'abbreviations':list(own),'collection_abbreviations':list(collection)}
        labels=source_labels({'a':src('甲书',['AB','ABC'],['X']),'b':src('合集 / 第二卷',[],['X']),'c':src('其他/新X',[],['X']),
            'd':src('模组 / 冰谷：少女雾凇'),'e':src('模组 / 深城：龙金之劫'),'f':src('模组 / 深城：疯法师地城'),
            'g':src('纲要 / 卷四：艾卓生物'),'h':src('模组 / CR：溟渊的呼唤'),'i':src('旧库'),'j':src('错位怪物：卷一')})
        # Codes first; a collection code is not repeated in a title that already carries it.
        self.assertEqual([labels[k] for k in 'abc'],['AB','X 第二卷','新X'])
        # A main title names the book when no other source shares it; otherwise the subtitle does.
        # Volume numbers and Latin series marks never stand alone.
        self.assertEqual([labels[k] for k in 'defghij'],['冰谷','龙金之劫','疯法师地城','艾卓生物','溟渊的呼唤','旧库','错位怪物'])
        self.assertEqual(len(set(labels.values())),len(labels))

    def test_printed_layout_marks_one_rule_generation(self):
        from html_extract import printed_generation
        old='僵尸 中型亡灵 护甲等级 8 生命值 22 力量 敏捷 体质 智力 感知 魅力 13 (+1) 6 (-2) 16 (+3) 3 (-4) 6 (-2) 5 (-3)'
        new='僵尸 中型亡灵 AC 8 先攻 -2 (8) HP 15 调整 豁免 力量 13 +1 +1'
        self.assertEqual(printed_generation('creature',old)[0],'5e')
        self.assertEqual(printed_generation('creature',new)[0],'5r')
        # One parenthesized score in prose is an adjustment note, not an ability table.
        self.assertIsNone(printed_generation('creature','其具有以下调整：智力18（+4）'))
        self.assertIsNone(printed_generation('creature',old+' '+new))
        self.assertEqual(printed_generation('spell','一环 塑能 施法时间：1 动作 施法距离')[0],'5e')
        self.assertEqual(printed_generation('spell','一环 塑能 施法时间：附赠动作 施法距离')[0],'5r')
        self.assertIsNone(printed_generation('spell','施法时间：1 分钟'))
        self.assertIsNone(printed_generation('magic_item','施法时间：动作'))

    def test_sources_without_a_catalog_generation_take_their_printed_layout(self):
        from build import Build
        build=Build(WEB)
        pages=['topics/模组/湮灭之墓/生物/三花株.htm','topics/模组/湮灭之墓/生物/僵尸.htm','topics/玩家手册2024/法术详述/6环.htm']
        for path in pages:build.consume(parse_one(build.parse_job(path)))
        tomb=next(s for s in build.sources.values() if s['name'].endswith('湮灭之墓'))
        phb=next(s for s in build.sources.values() if s['name']=='玩家手册2024')
        self.assertEqual(tomb['ruleset_hint'],'待核')
        creatures=[e for e in build.entries if tomb['id'] in e['source_ids'] and e['type']=='creature']
        self.assertTrue(creatures);self.assertTrue(all(e['ruleset']=='待核' for e in creatures))
        build.infer_source_rulesets()
        # The adventure's 2014 stat blocks set its default; entries and their facts follow with the votes as evidence.
        self.assertEqual(tomb['ruleset_hint'],'5e')
        self.assertEqual(tomb['evidence'][-1]['rule'],'printed_generation')
        self.assertTrue(all(e['ruleset']=='5e' and e['ruleset_status']=='上下文推得' for e in creatures))
        facts={(f['entry_id'],f['rule']) for f in build.facts if f['field']=='ruleset'}
        self.assertTrue(all((e['id'],'source_default') in facts for e in creatures))
        # A catalogued generation is never overridden; an entry printed the other way is queued instead.
        self.assertEqual(phb['ruleset_hint'],'5r')
        heal=next(e for e in build.entries if e['name_en']=='Heal')
        self.assertEqual(heal['ruleset'],'5r')
        mismatch=[r for r in build.reviews if r['reason']=='printed_generation_mismatch']
        self.assertEqual([(r['target'],r['detail']) for r in mismatch],[(heal['id'],{'ruleset':'5r','printed':'5e'})])

if __name__=='__main__':unittest.main()
