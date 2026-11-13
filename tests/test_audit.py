"""Classification audit: untyped pages, mechanic signals in untyped sections, title labels and type conflicts."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit import classification_gaps, gap_report, type_conflict_report, conflict_report

REAL_DB=Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite'


class PlainProvenanceTests(unittest.TestCase):
    """A resolved plain reference keeps the cue that made it a rules use, located against the name."""
    def errors(self,syntax,a=10,b=12):
        from audit import audit_concepts
        db=sqlite3.connect(':memory:')
        db.executescript('''CREATE TABLE pages(path TEXT,text TEXT);CREATE TABLE entries(id TEXT);
            CREATE TABLE concepts(definition_id TEXT,data TEXT);CREATE TABLE metadata(key TEXT,value TEXT);
            CREATE TABLE concept_observations(id TEXT,page_path TEXT,start INTEGER,end INTEGER,signal TEXT,status TEXT,target_id TEXT,data TEXT);''')
        db.execute("INSERT INTO pages VALUES('p',?)",('x'*40,));db.executemany('INSERT INTO entries VALUES(?)',[('owner',),('wis',)])
        db.execute('INSERT INTO concepts VALUES(?,?)',('wis',json.dumps({'ruleset':'5r','type':'ability'})))
        o={'id':'o','path':'p','start':a,'end':b,'signal':'plain','status':'resolved','target_id':'wis','candidates':['wis'],'role':'mention',
           'evidence':{'syntax':syntax,'scope':{'ruleset':'5r','source_ids':['s'],'owner_ids':['owner']}}}
        db.execute('INSERT INTO concept_observations VALUES(?,?,?,?,?,?,?,?)',('o','p',a,b,'plain','resolved','wis',json.dumps(o)))
        db.execute('INSERT INTO metadata VALUES(?,?)',('concept_coverage',json.dumps({'observations':1,'outcomes':{'plain':{'resolved':1}},'gaps':{}})))
        return [e for e in audit_concepts(db,'/nonexistent')['errors'] if 'provenance' in e]

    def test_clause_cues_precede_and_notation_cues_contain_the_name(self):
        self.assertEqual(self.errors({'rule':'rule_use_clause','slot':'rule','cue_span':[7,10]}),[])
        self.assertEqual(self.errors({'rule':'check_notation','slot':'rule','role':'ability','cue_span':[10,18]}),[])
        self.assertEqual(self.errors({'rule':'check_notation','slot':'rule','role':'skill','cue_span':[9,13]}),[])
        # A notation cue that misses the name, or a clause cue that does not end at it, is no evidence.
        self.assertTrue(self.errors({'rule':'check_notation','slot':'rule','role':'ability','cue_span':[11,18]}))
        self.assertTrue(self.errors({'rule':'rule_use_clause','slot':'rule','cue_span':[10,18]}))


class Corpus:
    """A minimal catalog: pages are assembled from named passages so spans follow the text."""
    def __init__(self):
        self.db=sqlite3.connect(':memory:')
        self.db.executescript('''
            CREATE TABLE sources(id TEXT,name TEXT);
            CREATE TABLE pages(path TEXT,title TEXT,role TEXT,parse_status TEXT,text TEXT,source_ids TEXT);
            CREATE TABLE entries(id TEXT,name TEXT,type TEXT,page_path TEXT,start INTEGER,end INTEGER);
            CREATE TABLE raw_tables(id TEXT,page_path TEXT,start INTEGER,end INTEGER,data TEXT);''')
        self.db.execute("INSERT INTO sources VALUES('src','测试书')")
        self.spans={}

    def page(self,path,passages,role='document'):
        text='';spans={}
        for name,body in passages:
            spans[name]=(len(text),len(text)+len(name+'\n'+body));text+=name+'\n'+body+'\n'
        self.db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?)',(path,path,role,'ok',text,'["src"]'))
        self.spans[path]=spans;return spans

    def entry(self,path,name,typ,start=None,end=None):
        a,b=self.spans[path][name]
        self.db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?)',(f'{path}::{name}',name,typ,path,a if start is None else start,b if end is None else end))

    def table(self,path,name,header):
        a,b=self.spans[path][name]
        self.db.execute('INSERT INTO raw_tables VALUES(?,?,?,?,?)',(f'{path}::{name}::table',path,a+len(name)+1,b,json.dumps({'headers':[header,'效应']})))


class ClassificationGapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Corpus()
        hazards='topics/测试书/危害/危害.htm'
        c.page(hazards,[('危害 Hazards','本节介绍危害。'),('流沙 Quicksand','进入流沙的生物须进行一次DC 10的力量（运动）检定。'),
                        ('难度说明','DC 10 简单，DC 15 中等。'),('燎原烈火 Inferno','受到5（2d4）点火焰伤害。'),
                        ('魔法区域','掷骰决定效应。'),('仅有表格','另一张表。')])
        for name in ['危害 Hazards','流沙 Quicksand','难度说明','燎原烈火 Inferno','魔法区域','仅有表格']:c.entry(hazards,name,'section')
        c.table(hazards,'魔法区域','d6');c.table(hazards,'仅有表格','名称')
        # A stat block's 动作 heading begins inside the creature and runs two characters past its end.
        monster='topics/测试书/生物/怪.htm'
        spans=c.page(monster,[('怪 Monster','一种怪物。'),('数据卡','护甲等级 12'),('动作Actions','目标须进行DC 12的敏捷豁免，失败受到7（2d6）点伤害。')])
        c.entry(monster,'怪 Monster','section',end=spans['动作Actions'][1])
        c.entry(monster,'数据卡','creature',start=spans['数据卡'][0],end=spans['动作Actions'][1]-2)
        c.entry(monster,'动作Actions','section',start=spans['动作Actions'][0]-1)
        # 资料索引 points at its material without owning it.
        lore='topics/测试书/资料/地理.htm'
        spans=c.page(lore,[('地理 Geography','概述。'),('寒冷水域','进行一次DC 15的体质豁免。')])
        c.entry(lore,'地理 Geography','lore',end=spans['寒冷水域'][1])
        c.entry(lore,'寒冷水域','section')
        index='topics/测试书/索引.htm'
        c.page(index,[('索引','DC 15的体质豁免')],role='index');c.entry(index,'索引','section')
        glossary='topics/测试书/术语.htm'
        c.page(glossary,[('燃烧 Burning【危害】','着火。'),('目盲 Blinded【状态】','看不见。'),('【状态 】 Condition','状态规则。')])
        c.entry(glossary,'燃烧 Burning【危害】','rule');c.entry(glossary,'目盲 Blinded【状态】','condition');c.entry(glossary,'【状态 】 Condition','rule')
        cls.db=c.db;cls.texts=dict(c.db.execute('SELECT path,text FROM pages'))
        cls.summary,cls.pages,cls.sections=classification_gaps(c.db)
        cls.by_name={r['name']:r for r in cls.sections}

    def test_untyped_pages_have_no_typed_entry(self):
        self.assertEqual([r['path'] for r in self.pages],['topics/测试书/危害/危害.htm'])
        page=self.pages[0]
        self.assertEqual((page['source'],page['directory'],page['sections'],page['flagged_sections']),('测试书','测试书/危害',6,3))
        self.assertEqual(self.summary['untyped_pages'],1)

    def test_mechanics_flag_untyped_sections_with_their_evidence(self):
        self.assertEqual({n:r['signals'] for n,r in self.by_name.items()},
                         {'流沙 Quicksand':'save_dc','燎原烈火 Inferno':'dice_damage','魔法区域':'dice_table','寒冷水域':'save_dc'})
        self.assertEqual(self.by_name['流沙 Quicksand']['evidence'][0]['text'],'DC 10的力量（运动）检定')
        self.assertEqual(self.by_name['魔法区域']['evidence'][0]['text'],'d6')
        for r in self.sections:
            for e in r['evidence']:
                if e['signal']!='dice_table':self.assertEqual(self.texts[r['page_path']][slice(*e['span'])],e['text'])
        self.assertEqual(self.summary['signals'],{'save_dc':2,'dice_damage':1,'dice_table':1})

    def test_a_dc_without_a_save_or_check_is_not_a_mechanic(self):
        self.assertNotIn('难度说明',self.by_name)
        self.assertNotIn('仅有表格',self.by_name)

    def test_sections_inside_typed_entries_belong_to_them(self):
        self.assertNotIn('动作Actions',self.by_name)
        self.assertNotIn('topics/测试书/生物/怪.htm',[r['path'] for r in self.pages])
        # Six hazard sections, the monster's primary section and the lore child; never the 动作 heading.
        self.assertEqual(self.summary['outside_sections'],8)

    def test_lore_does_not_own_its_sections_but_types_its_page(self):
        self.assertFalse(self.by_name['寒冷水域']['page_untyped'])
        self.assertTrue(self.by_name['流沙 Quicksand']['page_untyped'])

    def test_index_pages_are_outside_the_audit(self):
        self.assertFalse(any(r['page_path'].endswith('索引.htm') for r in self.sections))

    def test_sources_and_labels_are_tallied(self):
        self.assertEqual(self.summary['by_source'],[{'source':'测试书','untyped_pages':1,'flagged_sections':4}])
        self.assertEqual(self.summary['labels'],{'【状态】':{'condition':1,'rule':1},'【危害】':{'rule':1}})

    def test_report_compares_with_the_baseline(self):
        result={'classification_gaps':self.summary,
                'classification_gaps_before':{'untyped_pages':3,'outside_sections':9,'flagged_sections':6,'signals':{}}}
        lines='\n'.join(gap_report(result,{'pages':self.pages,'sections':self.sections}))
        self.assertIn('无类型正文页：1（修改前 3）',lines)
        self.assertIn('| `测试书/危害` | 1 | 3 |',lines)
        self.assertIn('| 【状态】 | 状态 1，规则 1 |',lines)
        without=gap_report({'classification_gaps':self.summary,'classification_gaps_before':None},{'pages':[],'sections':[]})
        self.assertIn('- 无类型正文页：1。',without)


class TypeConflictTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE entries(id TEXT,name TEXT,type TEXT,page_path TEXT,start INTEGER,data TEXT)')

    def entry(self,eid,typ,rule,conflicts,start=0):
        data={'classification_rule':rule}
        if conflicts is not None:data['type_conflicts']=conflicts
        self.db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?)',(eid,eid,typ,'topics/测试书/专长.htm',start,json.dumps(data)))

    def test_conflicts_are_grouped_by_decision_and_printed_evidence(self):
        feat=[{'type':'feat','rule':'feat_descriptor'}]
        self.entry('铁血英雄','creature','stat_fields',feat);self.entry('钢铁意志','creature','stat_fields',feat,start=9)
        self.entry('火球术','spell','spell_fields',[])
        summary,rows=type_conflict_report(self.db)
        self.assertEqual(summary,{'entries':2,'pairs':[{'type':'creature','rule':'stat_fields','printed_type':'feat','printed_rule':'feat_descriptor','count':2}]})
        self.assertEqual([(r['id'],r['printed']) for r in rows],[('铁血英雄','feat/feat_descriptor'),('钢铁意志','feat/feat_descriptor')])
        lines='\n'.join(conflict_report({'type_conflicts':summary,'type_conflicts_before':5}))
        self.assertIn('带冲突的条目：2（修改前 5）',lines)
        self.assertIn('| 生物数据卡（`stat_fields`） | 专长（`feat_descriptor`） | 2 |',lines)

    def test_a_catalog_built_before_the_field_reports_nothing(self):
        self.entry('火球术','spell','spell_fields',None)
        self.assertEqual(type_conflict_report(self.db),(None,[]))
        self.assertEqual(conflict_report({'type_conflicts':None,'type_conflicts_before':None}),[])
        # A newer catalog compared with such a baseline states its own count only.
        self.assertIn('- 带冲突的条目：0。',conflict_report({'type_conflicts':{'entries':0,'pairs':[]},'type_conflicts_before':None}))


@unittest.skipUnless(REAL_DB.exists(),'Build the dataset first')
class CorpusGapTests(unittest.TestCase):
    def test_evidence_points_at_the_page_text_and_counts_agree(self):
        db=sqlite3.connect(REAL_DB.resolve().as_uri()+'?mode=ro',uri=True)
        summary,pages,sections=classification_gaps(db)
        texts=dict(db.execute("SELECT path,text FROM pages WHERE role='document'"))
        for r in sections:
            for e in r['evidence']:
                if e['signal']!='dice_table':self.assertEqual(texts[r['page_path']][slice(*e['span'])],e['text'],r['id'])
        self.assertEqual(sum(s['untyped_pages'] for s in summary['by_source']),summary['untyped_pages'])
        self.assertEqual(sum(s['flagged_sections'] for s in summary['by_source']),summary['flagged_sections'])
        self.assertEqual(sum(r['flagged_sections'] for r in pages),sum(r['page_untyped'] for r in sections))
        # Stat block parts begin inside their creature and never surface as gaps.
        typed=db.execute("SELECT page_path,start,end FROM entries WHERE type NOT IN ('section','lore')").fetchall()
        owners={}
        for path,a,b in typed:owners.setdefault(path,[]).append((a,b))
        starts=dict(db.execute('SELECT id,start FROM entries'))
        self.assertFalse(any(x<=starts[r['id']]<y for r in sections for x,y in owners.get(r['page_path'],[])))
        db.close()


if __name__=='__main__':unittest.main()


class TableProvenanceTests(unittest.TestCase):
    """A table reference keeps the cell that listed it and the enclosing table range."""
    def errors(self,table,role='mention'):
        from audit import audit_concepts
        db=sqlite3.connect(':memory:')
        db.executescript('''CREATE TABLE pages(path TEXT,text TEXT);CREATE TABLE entries(id TEXT);
            CREATE TABLE concepts(definition_id TEXT,data TEXT);CREATE TABLE metadata(key TEXT,value TEXT);
            CREATE TABLE concept_observations(id TEXT,page_path TEXT,start INTEGER,end INTEGER,signal TEXT,status TEXT,target_id TEXT,data TEXT);''')
        db.execute("INSERT INTO pages VALUES('p',?)",('x'*40,));db.execute("INSERT INTO entries VALUES('feature')")
        o={'id':'o','path':'p','start':10,'end':12,'signal':'table','status':'resolved','target_id':'feature',
           'candidates':['feature'],'role':role,'evidence':{'table':table}}
        db.execute('INSERT INTO concept_observations VALUES(?,?,?,?,?,?,?,?)',('o','p',10,12,'table','resolved','feature',json.dumps(o)))
        db.execute('INSERT INTO metadata VALUES(?,?)',('concept_coverage',json.dumps({'observations':1,'outcomes':{'table':{'resolved':1}},'gaps':{}})))
        return [e for e in audit_concepts(db,'/nonexistent')['errors'] if 'provenance' in e]

    def test_cell_and_enclosing_table_bounds_are_required(self):
        table={'id':'tbl_x','span':[0,20],'cell_span':[8,16],'row':1,'column':0,'headers':['等级','职业特性'],'cell_text':'专精'}
        self.assertEqual(self.errors(table),[])
        self.assertTrue(self.errors({**table,'cell_span':[11,16]}))
        self.assertTrue(self.errors({**table,'span':[10,20]}))
        self.assertTrue(self.errors({**table,'headers':'职业特性'}))
        self.assertTrue(self.errors({**table,'id':None}))
        self.assertTrue(self.errors(table,role='definition'))
