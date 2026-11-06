import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from html_extract import Document
from linked_book import text_slice,inject,build_book,fingerprint,MARKER

class DOMBindingTests(unittest.TestCase):
    def test_offsets_unicode_whitespace_and_markup(self):
        d=Document('topics/test.htm','<h2>甲 😀 <b>名字</b></h2><p> A\n  B&nbsp;C</p>')
        a=d.text.index('😀');b=d.text.index('名字')+2
        r=text_slice(d,a,b)
        self.assertEqual(r['text'],'😀 名字')
        self.assertEqual(r['a'][1],2)
        self.assertEqual(r['b'][1],2)
        d.close()
    def test_original_bytes_unchanged(self):
        for raw in [b'<body><p style="color:red">A &amp; B</p></body>', '<body>中文</body>'.encode('gb18030')]:
            result=inject(raw,{'definitions':[{'name':'</script>中文'}]})
            self.assertEqual(result.split(MARKER)[0],raw)
            self.assertNotIn(b'</script>\\u',result)
            self.assertIn(b'\\u003c/script>',result)

class LinkedBuildTests(unittest.TestCase):
    def fixture(self,root):
        source=root/'source';(source/'topics').mkdir(parents=True)
        page=source/'topics/test.htm'
        page.write_text('<h2 id="Spell">甲 Alpha</h2><p>定义</p><p><a href="#Spell">甲 Alpha</a></p><p><a href="#missing">未知</a></p>')
        (source/'style.css').write_text('h2 {color:red}')
        (source/'index.htm').write_text('<!doctype html><iframe id="content"></iframe>')
        d=Document('topics/test.htm',page.read_text());c=d.candidates()[0]
        e={'id':'ver_test','name':'甲 Alpha','name_zh':'甲','name_en':'Alpha','page_path':'topics/test.htm','anchors':['Spell'],'start':c.start,'title_end':c.title_end,'end':c.end,'type':'spell','is_primary':True}
        db=root/'catalog.sqlite';conn=sqlite3.connect(db)
        conn.executescript('CREATE TABLE metadata(key,value); CREATE TABLE pages(path,sha256,text); CREATE TABLE entries(data);')
        conn.execute('insert into metadata values(?,?)',('summary',json.dumps({'full_scan':True})))
        conn.execute('insert into pages values(?,?,?)',('topics/test.htm',fingerprint(page),d.text))
        conn.execute('insert into entries values(?)',(json.dumps(e),));conn.commit();conn.close();d.close()
        return source,db
    def test_copy_bindings_references_and_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root);out=root/'book'
            manifest,report=build_book(source,db,out)
            self.assertEqual(report['definitions'],1);self.assertEqual(report['linked_references'],1)
            self.assertEqual(report['unresolved'][0]['href'],'#missing')
            self.assertEqual((out/'style.css').read_bytes(),(source/'style.css').read_bytes())
            self.assertEqual((out/'index.htm').read_bytes().split(MARKER)[0],(source/'index.htm').read_bytes())
            self.assertIn(b'/anna-assets/reader.js',(out/'index.htm').read_bytes())
            self.assertEqual((out/'topics/test.htm').read_bytes().split(MARKER)[0],(source/'topics/test.htm').read_bytes())
            m2,_=build_book(source,db,out);self.assertEqual(manifest,m2)
            self.assertFalse((root/'book.previous').exists())
    def test_stale_source_rejected_and_previous_output_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root);out=root/'book'
            build_book(source,db,out);before=(out/'anna-manifest.json').read_bytes()
            (source/'topics/test.htm').write_text('changed')
            with self.assertRaisesRegex(ValueError,'快照'):build_book(source,db,out)
            self.assertEqual(before,(out/'anna-manifest.json').read_bytes())
    def test_whitespace_only_definition_is_unlocatable(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root);out=root/'book'
            page=source/'topics/test.htm';page.write_text('<body> \n </body>')
            doc=Document('topics/test.htm',page.read_text())
            conn=sqlite3.connect(db)
            e=json.loads(conn.execute('SELECT data FROM entries').fetchone()[0])
            e.update(start=0,end=len(doc.text),title_end=0,anchors=[])
            conn.execute('UPDATE entries SET data=?',(json.dumps(e),))
            conn.execute('UPDATE pages SET sha256=?,text=?',(fingerprint(page),doc.text));conn.commit();conn.close();doc.close()
            manifest,report=build_book(source,db,out)
            self.assertFalse(manifest['entries']['ver_test']['locatable'])
            self.assertEqual(report['unlocatable_definitions'],['ver_test'])
            self.assertEqual(report['clickable_definitions'],0)

    def test_source_output_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root)
            with self.assertRaises(ValueError):build_book(source,db,source/'nested')

    def test_formatted_reference_and_style_snapshot_guard(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root);out=root/'book'
            page=source/'topics/test.htm';page.write_text(page.read_text()+'<p><u>格式引用</u> 未核引用</p>')
            css=source/'topics/style.css';css.write_text('/* source convention */')
            doc=Document('topics/test.htm',page.read_text());a,b=doc.span(doc.soup.u)
            conn=sqlite3.connect(db)
            conn.execute('UPDATE pages SET sha256=?,text=?',(fingerprint(page),doc.text))
            conn.executescript('CREATE TABLE concept_observations(page_path,status,signal,data);')
            for status in ['resolved','review','ambiguous']:
                conn.execute('INSERT INTO concept_observations VALUES(?,?,?,?)',('topics/test.htm',status,'format',json.dumps({'start':a,'end':b,'target_id':'ver_test' if status=='resolved' else None})))
            conn.execute('INSERT INTO metadata VALUES(?,?)',('concept_coverage',json.dumps({'discovery_inputs':{'topics/style.css':fingerprint(css),'webhelpcontents.htm':None}})))
            conn.commit();conn.close();doc.close()
            _,report=build_book(source,db,out)
            self.assertEqual(report['linked_references'],2)  # one href + one resolved mark
            raw=(out/'topics/test.htm').read_bytes();self.assertEqual(raw.split(MARKER)[0],page.read_bytes())
            data=json.loads(raw.split(b'id="anna-bindings">')[1].split(b'</script>')[0])
            self.assertEqual(data['references'][-1]['range']['text'],'格式引用')
            css.write_text('changed convention')
            with self.assertRaisesRegex(ValueError,'概念发现依据'):build_book(source,db,out)
            self.assertEqual((out/'topics/test.htm').read_bytes(),raw)


class LinkRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node is needed for the link-layer fixture')
    def test_link_runtime(self):
        result=subprocess.run(['node',str(Path(__file__).with_name('link_runtime.cjs'))],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
