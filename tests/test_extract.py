import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from html_extract import Document,resolve_link,split_name,key
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

class CorpusRegressionTests(unittest.TestCase):
    def parse(self,rel):return parse_one((str(WEB),'topics/'+rel,[],rel))

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

if __name__=='__main__':unittest.main()
