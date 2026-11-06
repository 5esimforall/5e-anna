"""Regression fixtures for catalog rows, roles, and over-broad context inference."""
import csv
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from build import Build,parse_one
from html_extract import split_name
from browse import schema
from audit import audit
from paths import WEB

class CatalogTests(unittest.TestCase):
    def parse(self,path):
        r=parse_one((str(WEB),'topics/'+path,[],path))
        self.assertEqual(r['page']['parse_status'],'ok',r['page']['error'])
        return r

    def synthetic(self,html,path='topics/装备/目录.htm'):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/path;f.parent.mkdir(parents=True);f.write_text(html)
            return parse_one((td,path,[],path))

    def test_mount_rows_both_editions(self):
        for book in ('玩家手册','玩家手册2024'):
            r=self.parse(book+'/装备/坐骑与载具.htm')
            animals=[e for e in r['candidates'] if e['type']=='animal_offer']
            self.assertEqual(len(animals),8)
            self.assertFalse(any(e['type']=='creature' for e in r['candidates']))
            for e in animals:
                self.assertIn('price',e['fields']);self.assertIn('carrying_capacity',e['fields'])
                self.assertNotIn('weight',e['fields'])
                for vs in e['fields'].values():
                    for value,a,b in vs:self.assertEqual(re.sub(r'\s+','',value),re.sub(r'\s+','',r['page']['text'][e['start']+a:e['start']+b]))
                self.assertTrue(any(f[0:2]==('role','坐骑') and f[-1]=='上下文推得' for f in e['extra_facts']))
            camel=next(e for e in animals if 'Camel' in e['name'])
            self.assertEqual(camel['fields']['carrying_capacity'][0][0], '480 磅' if book=='玩家手册' else '450磅')

    def test_animals_are_not_blanket_mounts(self):
        r=self.parse('被遗忘的国度/费伦英雄/第四章/坐骑及其他动物.htm')
        animals=[e for e in r['candidates'] if e['type']=='animal_offer'];self.assertEqual(len(animals),3)
        snake=next(e for e in animals if 'Flying Snake' in e['name'])
        self.assertIn(('role','宠物'),[f[:2] for f in snake['extra_facts']])
        self.assertNotIn(('role','坐骑'),[f[:2] for f in snake['extra_facts']])
        dog=next(e for e in animals if 'Sled Dog' in e['name'])
        self.assertIn(('statblock_name','狼Wolf'),[f[:2] for f in dog['extra_facts']])

    def test_vehicle_catalog_not_headings_or_saddles(self):
        for book,count in [('玩家手册',11),('玩家手册2024',12)]:
            r=self.parse(book+'/装备/坐骑与载具.htm')
            vs=[e for e in r['candidates'] if e['type']=='vehicle']
            self.assertEqual(len(vs),count,[e['name'] for e in vs])
            self.assertTrue(all(e['boundary_rule']=='table_row' for e in vs))
            self.assertFalse(any('Saddle' in e['name'] for e in vs))
        r=self.parse('被遗忘的国度/费伦英雄/第四章/载具.htm')
        self.assertEqual([e['name'] for e in r['candidates'] if e['type']=='vehicle'],['货篷车Covered Wagon'])

    def test_parallel_columns_do_not_overwrite(self):
        r=self.parse('玩家手册2024/装备/冒险装备.htm')
        items=[e for e in r['candidates'] if e['boundary_rule']=='table_row']
        acid=next(e for e in items if e['name']=='强酸')
        pen=next(e for e in items if e['name']=='墨水笔')
        self.assertEqual(acid['fields']['price'][0][0],'25GP')
        self.assertEqual(pen['fields']['price'][0][0],'2CP')
        self.assertLessEqual(acid['end'],pen['start'])
        self.assertNotIn('墨水笔',r['page']['text'][acid['start']:acid['end']])

    def test_armor_attributes_are_preserved(self):
        r=self.parse('玩家手册2024/装备/护甲.htm')
        padded=next(e for e in r['candidates'] if 'Padded Armor' in e['name'])
        self.assertEqual(padded['fields']['armor_class'][0][0],'11+敏捷调整值')
        self.assertEqual(padded['fields']['stealth'][0][0],'劣势')
        self.assertIn('strength_requirement',padded['fields'])

    def test_summary_and_merged_rows(self):
        r=self.synthetic('<table><tr><td colspan="3">冒险装备</td></tr><tr><th>物品</th><th>价格</th><th>重量</th></tr><tr><td>绳索Rope</td><td>1 GP</td><td>5磅</td></tr><tr><td rowspan="2">A</td><td>2 GP</td><td>1磅</td></tr><tr><td>3 GP</td><td>2磅</td></tr></table>')
        self.assertEqual([e['name'] for e in r['candidates'] if e['type']=='equipment'],['绳索Rope'])
        self.assertEqual(sum(i['reason']=='catalog_row_layout_unresolved' for i in r['issues']),2)

    def test_price_rules_not_catalog_entities(self):
        r=self.synthetic('<h2>施法服务</h2><table><tr><th>法术环阶</th><th>时间</th><th>价格</th></tr><tr><td>1</td><td>一天</td><td>25GP</td></tr></table>')
        self.assertFalse(any(e['type']=='equipment' for e in r['candidates']))

    def test_reverse_price_goods(self):
        r=self.parse('城主指南2024/7.宝藏/商业货物.htm')
        es=[e for e in r['candidates'] if e['type']=='equipment']
        self.assertGreater(len(es),5)
        self.assertTrue(all('price' in e['fields'] for e in es))
        self.assertFalse(any('GP' in e['name'] for e in es))

    def test_facilities_not_management_rules(self):
        r=self.parse('城主指南2024/8.据点/3.据点地图/基础设施.htm')
        self.assertEqual({e['name'] for e in r['candidates'] if e['type']=='facility'}, {'卧室','餐厅','客厅','庭院','厨房','储藏室'})
        r=self.parse('城主指南2024/8.据点/4.据点事件.htm')
        self.assertFalse(any(e['type']=='facility' for e in r['candidates']))
        r=self.parse('艾伯伦：奇械锻炉/第三章/特色设施.htm')
        self.assertEqual(sum(e['type']=='facility' for e in r['candidates']),10)
        self.assertNotEqual(r['candidates'][0]['type'],'facility')

    def test_ship_components_and_rules_not_vehicles(self):
        r=self.parse('模组/盐沼幽魂/船舶与海洋/船只数据卡/帆船和战舰.htm')
        self.assertEqual({split_name(e['name'])[0] for e in r['candidates'] if e['type']=='vehicle'},{'帆船','战舰'})
        for p in ['模组/盐沼幽魂/船舶与海洋/在海上旅行.htm','模组/盐沼幽魂/船舶与海洋/超级船只升级.htm','模组/坠入阿弗纳斯/地狱战争机器/厄运.htm']:
            self.assertFalse(any(e['type']=='vehicle' for e in self.parse(p)['candidates']),p)
        for p in ['星界冒险者指南/七鳃鳗舰.htm','模组/坠入阿弗纳斯/地狱战争机器/摧残者.htm']:
            self.assertEqual(sum(e['type']=='vehicle' for e in self.parse(p)['candidates']),1,p)

    def test_reward_overviews_and_class_boons(self):
        r=self.parse('城主指南2024/3.地下城主工具箱/超自然赠礼.htm')
        self.assertEqual(sum(e['type']=='reward' for e in r['candidates']),14)
        r=self.parse('第三方/邪狱使/禁令恩惠选项.htm')
        self.assertFalse(any(e['type']=='reward' for e in r['candidates']))
        self.assertEqual(sum(e['type']=='class_feature' for e in r['candidates']),19)

    def test_trap_descriptors_not_design_rules(self):
        for path,count in [('城主指南/冒险之主/环境/陷阱.html',8),('城主指南2024/3.地下城主工具箱/陷阱.htm',8),('珊娜萨的万事指南/地下城主工具/陷阱/简易陷阱范例.htm',9)]:
            traps=[e for e in self.parse(path)['candidates'] if e['type']=='trap']
            self.assertEqual(len(traps),count,[e['name'] for e in traps])
        r=self.parse('珊娜萨的万事指南/地下城主工具/陷阱/设计简易陷阱.htm')
        self.assertFalse(any(e['type']=='trap' for e in r['candidates']))

    def test_option_overviews_not_subclasses(self):
        for path in ['玩家手册/职业/邪术师/魔能祈唤.html','第三方/邪狱使/禁令恩惠选项.htm']:
            r=self.parse(path)
            self.assertNotEqual(r['candidates'][0]['type'],'subclass')

    def test_class_granted_invocations_not_option_overview(self):
        r=self.parse('玩家手册/职业/邪术师.html')
        e=next(e for e in r['candidates'] if e['name'].startswith('魔能祈唤'))
        self.assertEqual(e['type'],'class_feature')

    def test_animal_browse_columns(self):
        self.assertIn('carrying_capacity',schema('动物购置')['columns'])
        self.assertNotIn('weight',schema('动物购置')['columns'])

class AnimalLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=Build(WEB)
        ps=['玩家手册/装备/坐骑与载具.htm','玩家手册2024/装备/坐骑与载具.htm','被遗忘的国度/费伦英雄/第四章/坐骑及其他动物.htm',
            '怪物图鉴/野兽/CR0.5.html','怪物图鉴2025/附录A/战马.htm','怪物图鉴2025/附录A/驮用马.htm','怪物图鉴2025/附录A/乘用马.htm','怪物图鉴2025/附录A/怪兽/飞蛇.htm','怪物图鉴2025/附录A/狼.htm']
        cls.paths=['topics/'+p for p in ps]
        for p in cls.paths:
            cls.build.consume(parse_one((str(WEB),p,[],cls.build.registry.context(p))))
        cls.build.finish()

    def test_version_scoped_mount_roles(self):
        b=self.build
        horses=[e for e in b.entries if e['type']=='creature' and e['name_en']=='Warhorse']
        self.assertEqual({e['ruleset'] for e in horses},{'5e','5r'})
        for e in horses:
            self.assertEqual(e['fields']['role'],'坐骑')
            self.assertNotIn('price',e['fields'])
            links=[r for r in b.relations if r['predicate']=='uses_statblock' and r['object_id']==e['id']]
            self.assertEqual(len(links),1)
            self.assertEqual(b.entry_by_id[links[0]['subject_id']]['ruleset'],e['ruleset'])

    def test_inverted_english_names_without_spacing(self):
        horses=[e for e in self.build.entries if e['type']=='creature' and e['name_en'] in {'Draft Horse','Riding Horse'}]
        self.assertEqual(len(horses),2)
        self.assertTrue(all(e['fields'].get('role')=='坐骑' for e in horses))

    def test_explicit_rare_mount_example(self):
        e=next(e for e in self.build.entries if e['name_en']=='Giant Sea Horse')
        self.assertEqual(e['fields']['role'],'坐骑')
        fs=[f for f in self.build.facts if f['entry_id']==e['id'] and f['field']=='role']
        self.assertEqual(fs[0]['rule'],'explicit_mount_example')
        ev=fs[0]['evidence']
        self.assertIn('水栖坐骑',self.build.pages[ev['path']]['text'][slice(*ev['span'])])

    def test_ambiguous_and_unknown_edition_not_linked(self):
        b=Build.__new__(Build)
        offer={'id':'offer','name':'战马Warhorse','type':'animal_offer','ruleset':'5r','page_path':'test','start':0,'end':1}
        target={'id':'target1','name':'战马Warhorse','type':'creature','ruleset':'5r','source_names':['怪物图鉴2025']}
        b.entries=[offer,target,{**target,'id':'target2'}];b.facts=[];b.relations=[];b.reviews=[];b._review_ids=set()
        b.link_animal_catalogs()
        self.assertTrue(all(not r['object_id'] for r in b.relations))
        self.assertEqual(len(b.reviews),1)
        offer['ruleset']='待核';b.entries=[offer,target];b.relations=[]
        b.link_animal_catalogs()
        self.assertTrue(all(not r['object_id'] for r in b.relations))

    def test_dog_uses_wolf_snake_not_mount(self):
        b=self.build
        dog=next(e for e in b.entries if e['type']=='animal_offer' and 'Sled Dog' in e['name'])
        link=next(r for r in b.relations if r['subject_id']==dog['id'] and r['predicate']=='uses_statblock')
        self.assertEqual(b.entry_by_id[link['object_id']]['name_en'],'Wolf')
        snake=next(e for e in b.entries if e['type']=='creature' and e['name_en']=='Flying Snake')
        self.assertEqual(snake['fields']['role'],'宠物')

    def test_unresolved_not_guessed(self):
        b=self.build
        self.assertTrue(any(r['reason']=='animal_statblock_unresolved' for r in b.reviews))
        self.assertTrue(any(r['predicate']=='uses_statblock' and not r['object_id'] and r['status']=='待核' for r in b.relations))

    def test_sql_csv_views_and_evidence(self):
        b=self.build;validation=b.validate(self.paths)
        with tempfile.TemporaryDirectory() as td:
            b.export(Path(td),validation,len(self.paths),0)
            db=sqlite3.connect(Path(td)/'catalog.sqlite')
            self.assertEqual(db.execute('select count(*) from "坐骑"').fetchone()[0],5)
            self.assertEqual(db.execute('select count(*) from "动物购置"').fetchone()[0],19)
            with (Path(td)/'views/坐骑.csv').open(encoding='utf-8-sig') as f:
                self.assertEqual(len(list(csv.DictReader(f))),5)
            self.assertEqual(db.execute('pragma integrity_check').fetchone()[0],'ok')
            db.close()
            result,_=audit(Path(td)/'catalog.sqlite')
            self.assertEqual(result['csv_sql_mismatches'],[])
            self.assertEqual(result['foreign_key_errors'],0)

if __name__=='__main__':unittest.main()
