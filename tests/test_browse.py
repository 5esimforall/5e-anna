"""Quick-reference filter and semantic-order regressions."""
import json
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import browse
from serve import DB,connect,query,csv_export

class NormalizeTests(unittest.TestCase):
    def test_level_alias(self):
        self.assertEqual(browse.normalized({'spell_level':['一环','1环']},'spell_level'),['1'])
        self.assertEqual(browse.sort_value('{"spell_level":"戏法"}','spell_level'),0)
    def test_class_tokens(self):
        self.assertEqual(browse.normalized({'spell_classes':'术 法 锁 械'},'spell_classes'),['术士','法师','魔契师','奇械师'])
        self.assertEqual(browse.normalized({},'spell_classes'),[browse.UNKNOWN])
    def test_missing_is_not_false(self):
        self.assertEqual(browse.normalized({},'concentration'),[browse.UNKNOWN])
        self.assertEqual(browse.normalized({'concentration':False},'concentration'),['false'])
        self.assertEqual(browse.normalized({'concentration':[True,False]},'concentration'),[browse.CONFLICT])
    def test_material_semantics(self):
        self.assertEqual(browse.normalized({'material':'×'},'special_material'),['__na__'])
        self.assertEqual(browse.normalized({'material':'M'},'special_material'),['false'])
        self.assertEqual(browse.normalized({'material':'M*'},'special_material'),['true'])
        self.assertEqual(browse.normalized({'material':'M*'},'material'),['true'])
    def test_cr_and_rarity(self):
        self.assertEqual(browse.number('1/8'),.125)
        self.assertIsNone(browse.number('1/0'));self.assertIsNone(browse.number('nan'))
        self.assertIsNone(browse.sort_value('{"cr":["3","4"]}','cr'))
        self.assertLess(browse.sort_value('{"rarity":"普通"}','rarity'),browse.sort_value('{"rarity":"神器"}','rarity'))
    def test_numeric_order_both_directions_unknown_last(self):
        db=sqlite3.connect(':memory:');browse.register(db)
        db.execute('create table entries(id,name,name_en,type,ruleset,page_path,start,fields)')
        vals=['10','2','1/2','1/4','0',None,['1','2']]
        for i,v in enumerate(vals):db.execute('insert into entries values(?,?,?,?,?,?,?,?)',(str(i),str(i),'','creature','5r','',0,json.dumps({'cr':v})))
        for direction,expected in [('asc',[0,.25,.5,2,10]),('desc',[10,2,.5,.25,0])]:
            result=[r[0] for r in db.execute("select browse_rank(e.fields,'cr') from entries e order by "+browse.order_sql({'sort':['cr'],'dir':[direction]}))]
            self.assertEqual(result,expected+[None,None])
        db.close()

@unittest.skipUnless(DB.exists(),'Build corpus first')
class BrowseCorpusTests(unittest.TestCase):
    def setUp(self):self.db=connect()
    def tearDown(self):self.db.close()
    def ids(self,params):return {e['id'] for e in query(self.db,params,export=True)['rows']}
    def test_facets_differ_by_type(self):
        spells=browse.facets(self.db,'法术');monsters=browse.facets(self.db,'生物数据卡');items=browse.facets(self.db,'魔法物品')
        self.assertIn('spell_classes',spells['schema']['facets']);self.assertNotIn('rarity',spells['schema']['facets'])
        self.assertEqual(monsters['schema']['range'],'cr');self.assertIn('rarity',items['schema']['facets'])
        self.assertEqual(spells['total'],1385)
    def test_multiselect_union_and_intersection(self):
        base={'view':['法术']};a=self.ids(base|{'f_school':['塑能']});b=self.ids(base|{'f_school':['防护']})
        self.assertEqual(self.ids(base|{'f_school':['塑能','防护']}),a|b)
        wizard=self.ids(base|{'f_spell_classes':['法师']})
        self.assertEqual(self.ids(base|{'f_school':['塑能','防护'],'f_spell_classes':['法师']}),(a|b)&wizard)
        self.assertEqual(self.ids(base|{'f_school':[browse.NONE]}),set())
    def test_source_union_and_versions(self):
        sources=[r[0] for r in self.db.execute("select id from sources where name in ('玩家手册2014','玩家手册2024')")]
        base={'view':['法术']}
        a=self.ids(base|{'source':sources[:1]});b=self.ids(base|{'source':sources[1:]})
        self.assertEqual(self.ids(base|{'source':sources}),a|b)
        self.assertEqual(self.ids(base|{'ruleset':['5e','5r']}),self.ids(base|{'ruleset':['5e']})|self.ids(base|{'ruleset':['5r']}))
    def test_origin_multiselect(self):
        base={'view':['魔法物品']}
        self.assertEqual(self.ids(base|{'origin':['官方','合作']}),self.ids(base|{'origin':['官方']})|self.ids(base|{'origin':['合作']}))
        self.assertEqual(self.ids(base|{'origin':[browse.NONE]}),set())
    def test_boolean_conflict_disjoint(self):
        base={'view':['法术']};states=[self.ids(base|{'f_concentration':[v]}) for v in ['true','false',browse.UNKNOWN,browse.CONFLICT]]
        for a in range(len(states)):
            for b in range(a):self.assertFalse(states[a]&states[b])
        self.assertEqual(set.union(*states),self.ids(base))
        self.assertEqual(len(states[-1]),13)
    def test_cr_inclusive_range_and_order(self):
        rows=query(self.db,{'view':['生物数据卡'],'cr_min':['1/8'],'cr_max':['1/2'],'sort':['cr']},export=True)['rows']
        ranks=[browse.sort_value(json.dumps(e['fields']),'cr') for e in rows]
        self.assertTrue(ranks);self.assertEqual(ranks,sorted(ranks));self.assertTrue(all(.125<=x<=.5 for x in ranks))
        for p in [{'cr_min':['2'],'cr_max':['1']},{'cr_min':['nan']},{'cr_max':['1/0']},{'dir':['DROP']},{'size':['-1']}]:
            with self.assertRaises(ValueError):query(self.db,p)
    def test_special_material_false_requires_material(self):
        rows=query(self.db,{'view':['法术'],'f_special_material':['false']})['rows']
        self.assertTrue(rows);self.assertTrue(all(r['fields']['material']=='M' for r in rows))
    def test_export_matches_sorted_rows_and_columns(self):
        p={'view':['魔法物品'],'f_rarity':['普通','珍稀'],'sort':['rarity'],'dir':['desc'],'size':['50']}
        allrows=query(self.db,p,export=True)['rows'];first=query(self.db,p)['rows']
        self.assertEqual([r['id'] for r in allrows[:50]],[r['id'] for r in first])
        csv=csv_export(allrows,'魔法物品').decode('utf-8-sig')
        self.assertIn('稀有度',csv.splitlines()[0]);self.assertIn('同调条件',csv.splitlines()[0])
    def test_composite_spell_columns_keep_exported_parts(self):
        config=browse.schema('法术')
        self.assertEqual(config['composites'],{'components':['verbal','somatic','material'],'spell_markers':['ritual','concentration']})
        self.assertFalse({'verbal','somatic','material','ritual','concentration'}&set(config['columns']))
        rows=query(self.db,{'view':['法术'],'f_verbal':['false'],'size':['50']})['rows']
        self.assertTrue(rows);self.assertTrue(all(r['quickref']['verbal']==['false'] for r in rows))
        header=csv_export(rows,'法术').decode('utf-8-sig').splitlines()[0].split(',')
        for label in ['言语','姿势','材料','仪式','专注']:self.assertIn(label,header)
        self.assertNotIn('成分',header)
        # Views without composites keep identical table and export columns.
        self.assertEqual(browse.schema('魔法物品')['composites'],{})
        self.assertEqual(browse.schema('魔法物品')['export_columns'],browse.schema('魔法物品')['columns'])

if __name__=='__main__':unittest.main()
