import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from concepts import Resolver,format_kind,build_concepts,style_profile
from html_extract import Document
from build import parse_one

def entry(eid,name,path='topics/book/rules.htm',ruleset='5r',kind='rule',source='book'):
    return dict(id=eid,entity_id=eid,name=name,name_zh=name,name_en='',type=kind,page_path=path,ruleset=ruleset,source_ids=[source],start=0,title_end=0,end=100,anchors=[eid],is_primary=True)

class ConceptTests(unittest.TestCase):
    def test_format_order_and_conflicts(self):
        for html,kind in [('<u>优势</u>','concept'),('<u><em>法术</em></u>','spell'),('<em><u>物品</u></em>','magic_item'),('<b><u>怪物</u></b>','creature'),('<em><u><i>冲突</i></u></em>','ambiguous'),('<u>部分<em>斜体</em></u>','ambiguous'),('<u><em>全</em><i>斜体</i></u>','spell')]:
            d=Document('topics/a.htm',html);self.assertEqual(format_kind(d.soup.u),kind);d.close()
    def test_version_and_ambiguity(self):
        a=entry('a','专注');b=entry('b','专注',ruleset='5e');context=entry('c','正文','topics/book/text.htm')
        r=Resolver([a,b,context]);self.assertEqual(r.resolve('专注','concept',context['page_path'])['targets'],['a'])
        r=Resolver([a,entry('d','专注'),context]);self.assertEqual(r.resolve('专注','concept',context['page_path'])['targets'],['a','d'])
        context['ruleset']='待核';r=Resolver([a,context]);self.assertEqual(r.resolve('专注','concept',context['page_path'])['status'],'review')
    def test_no_arbitrary_registry_or_cross_source_preference(self):
        a=entry('a','专注');b=entry('b','专注');context=entry('c','正文','topics/book/text.htm')
        r=Resolver([a,b,context]);r.indexed.add('a')
        self.assertEqual(r.resolve('专注','concept',context['page_path'])['status'],'ambiguous')
        context['source_ids']=['other'];r=Resolver([a,context])
        result=r.resolve('专注','concept',context['page_path'])
        self.assertEqual(result,{'targets':['a'],'status':'review','reason':'cross_source_unconfirmed'})
        result=r.resolve('专注','spell',context['page_path'])
        self.assertEqual(result['reason'],'type_mismatch');self.assertEqual(result['status'],'review')
        a['page_path']='topics/玩家手册2024/术语汇编/专注.htm'
        r=Resolver([a,context]);r.indexed.add('a')
        self.assertEqual(r.resolve('专注','concept',context['page_path'])['reason'],'core_registry')
    def test_style_profile_requires_complete_documented_selectors(self):
        from paths import WEB
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'topics').mkdir()
            self.assertIsNone(style_profile(root))
            css=root/'topics/style.css';raw=(WEB/'topics/style.css').read_text();css.write_text(raw)
            profile=style_profile(root);self.assertTrue(profile['supported'])
            css.write_text(raw.replace('U EM,U I','EM U,I U'))
            changed=style_profile(root);self.assertFalse(changed['supported'])
            self.assertNotEqual(profile['sha256'],changed['sha256'])
    def test_registry_outcomes_and_plain_mentions(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'topics/book').mkdir(parents=True)
            from paths import WEB
            (root/'topics/style.css').write_bytes((WEB/'topics/style.css').read_bytes())
            raws={'topics/book/rules.htm':'<h3 id="a">专注 Concentration</h3><p>定义</p>',
                  'topics/book/text.htm':'<link rel="stylesheet" href="../style.css"><body><p><u>专注</u>与未标记的专注；中文Concentration 与 XConcentration 和 ConcentrationX；<u>未知</u></p></body>',
                  'topics/book/术语速查.htm':'<a href="rules.htm#a">专注</a><a href="rules.htm#missing">失效</a>'}
            pages={}
            for path,raw in raws.items():
                (root/path).write_text(raw);d=Document(path,raw);pages[path]={'text':d.text,'parse_status':'ok'};d.close()
            e=entry('a','专注 Concentration');e['name_zh']='专注';e['name_en']='Concentration'
            es=[e,entry('context','正文','topics/book/text.htm',kind='section')]
            db=sqlite3.connect(':memory:');db.executescript('CREATE TABLE entries(id PRIMARY KEY);CREATE TABLE pages(path PRIMARY KEY,text);CREATE TABLE metadata(key PRIMARY KEY,value);')
            db.executemany('INSERT INTO entries VALUES(?)',[(e['id'],) for e in es]);db.executemany('INSERT INTO pages VALUES(?,?)',[(p,pages[p]['text']) for p in pages])
            report=build_concepts(db,root,root,es,pages)
            self.assertEqual(report['outcomes']['index'],{'resolved':1,'unresolved':1})
            rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM concept_observations')]
            marked=next(r for r in rows if r['signal']=='format' and r['text']=='专注');self.assertEqual(marked['target_id'],'a')
            plain=next(r for r in rows if r['signal']=='plain' and r['text']=='专注');self.assertEqual(plain['status'],'review');self.assertIsNone(plain['target_id'])
            self.assertEqual([r['text'] for r in rows if r['signal']=='plain' and r['text'].lower()=='concentration'],['Concentration']);self.assertEqual(plain['candidates'],['a']);self.assertEqual(pages[plain['path']]['text'][plain['start']:plain['end']],'专注')
            from audit import audit_concepts
            self.assertEqual(audit_concepts(db,root)['errors'],[])
            db.execute("UPDATE concept_observations SET target_id='a' WHERE status='review'")
            self.assertTrue(any('unresolved target' in e for e in audit_concepts(db,root)['errors']))
            (root/'concept_observations.jsonl').write_text('')
            self.assertIn('concept_observations.jsonl: IDs mismatch',audit_concepts(db,root)['errors'])
            db.close()
    def test_roles_evidence_unknown_format_and_navigation_outside_quick_directory(self):
        from paths import WEB
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'topics/book').mkdir(parents=True)
            (root/'topics/style.css').write_bytes((WEB/'topics/style.css').read_bytes())
            (root/'webhelpcontents.htm').write_text('<div><a href="#">本书速查</a><div><a href="topics/book/registry.htm">规则</a></div></div>')
            raws={'topics/book/rules.htm':'<h3 id="a"><u>专注</u></h3><p>定义</p>',
                  'topics/book/registry.htm':'<a href="rules.htm#a">专注</a>',
                  'topics/book/text.htm':'<link rel="stylesheet" href="../style.css"><body><u>专注</u><u><em>专注</em></u><u>专<em>注</em></u><a href="rules.htm#a"><u>专注</u></a><div class="example"><u>专注</u></div>普通专注</body>',
                  'topics/book/unknown.htm':'<u>专注</u>',
                  'topics/empty.htm':'<p>没有标记</p>'}
            pages={};es=[entry('a','专注'),entry('context','正文','topics/book/text.htm',kind='section')]
            for path,raw in raws.items():
                (root/path).write_text(raw);d=Document(path,raw);pages[path]={'text':d.text,'parse_status':'ok'}
                if path.endswith('/rules.htm'):
                    es[0]['start'],es[0]['title_end']=d.span(d.soup.h3)
                d.close()
            db=sqlite3.connect(':memory:');db.executescript('PRAGMA foreign_keys=ON;CREATE TABLE entries(id PRIMARY KEY);CREATE TABLE pages(path PRIMARY KEY,text);CREATE TABLE metadata(key PRIMARY KEY,value);')
            db.executemany('INSERT INTO entries VALUES(?)',[(e['id'],) for e in es]);db.executemany('INSERT INTO pages VALUES(?,?)',[(p,pages[p]['text']) for p in pages])
            report=build_concepts(db,root,root,es,pages)
            rows=[json.loads(r[0]) for r in db.execute("SELECT data FROM concept_observations WHERE signal='format'")]
            by_reason={r['reason']:r for r in rows}
            self.assertEqual(by_reason['definition_title']['role'],'definition')
            self.assertEqual(by_reason['explicit_link']['role'],'explicit_reference')
            self.assertEqual(by_reason['example_context']['role'],'example')
            self.assertEqual(by_reason['unknown_style']['status'],'review')
            self.assertEqual(by_reason['mixed_format']['evidence']['tag_paths'],[['u'],['u','em']])
            self.assertEqual(by_reason['type_mismatch']['candidates'],['a'])
            self.assertEqual(by_reason['same_source']['target_id'],'a')
            self.assertEqual(len(by_reason['same_source']['evidence']['stylesheet_sha256']),64)
            self.assertEqual(report['registry_paths'],['topics/book/registry.htm'])
            self.assertEqual(report['outcomes']['index'],{'resolved':1})
            self.assertEqual(report['by_book']['empty.htm']['marked_pages'],0)
            self.assertGreater(len(report['gaps']['marked_without_target']),0)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall());db.close()
    def test_mastery_is_rule_not_unclassified(self):
        from paths import WEB
        path='topics/玩家手册2024/装备/精通词条.htm'
        result=parse_one((str(WEB),path,[],path))
        self.assertEqual(next(c for c in result['candidates'] if c['name'].startswith('横扫'))['type'],'rule')
