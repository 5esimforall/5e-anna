import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from anna.source.html import Document
from anna.review.store import load_events
from anna.server.linked_book import text_slice,inject,build_book,fingerprint,MARKER
from anna.server.reader import build_navigation,page_data,ReaderError

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
        (source/'topics/style.css').write_text('h2 {color:red}')
        (source/'index.htm').write_text('<!doctype html><iframe id="content"></iframe>')
        d=Document('topics/test.htm',page.read_text());c=d.candidates()[0]
        e={'id':'ver_test','name':'甲 Alpha','name_zh':'甲','name_en':'Alpha','page_path':'topics/test.htm','anchors':['Spell'],'start':c.start,'title_end':c.title_end,'end':c.end,'type':'spell','is_primary':True}
        db=root/'catalog.sqlite';conn=sqlite3.connect(db)
        conn.executescript('CREATE TABLE metadata(key,value); CREATE TABLE pages(path,sha256,text); CREATE TABLE entries(data);')
        conn.execute('insert into metadata values(?,?)',('summary',json.dumps({'full_scan':True})))
        conn.execute('insert into pages values(?,?,?)',('topics/test.htm',fingerprint(page),d.text))
        conn.execute('insert into entries values(?)',(json.dumps(e),))
        conn.execute('CREATE TABLE effective_references(id,page_path,start,signal,status,can_link,data)')
        for i,tag in enumerate(d.body.find_all('a',href=True)):
            a,b=d.span(tag);resolved=tag['href']=='#Spell'
            ref={'id':f'reference_{i}','path':'topics/test.htm','start':a,'end':b,'text':tag.get_text(),
                 'signal':'explicit','status':'resolved' if resolved else 'unresolved','can_link':resolved,
                 'target_id':'ver_test' if resolved else None,'reason':'explicit_locator','evidence':{'href':tag['href']}}
            conn.execute('INSERT INTO effective_references VALUES(?,?,?,?,?,?,?)',
                         (ref['id'],ref['path'],a,'explicit',ref['status'],int(resolved),json.dumps(ref)))
        log=root/'decisions.jsonl'
        conn.execute('INSERT INTO metadata VALUES(?,?)',('review_snapshot',json.dumps({'path':str(log),'sha256':load_events(log)[1],'events':0})))
        conn.execute('INSERT INTO metadata VALUES(?,?)',('reference_coverage','{}'))
        conn.commit();conn.close();d.close()
        return source,db
    def test_copy_bindings_references_and_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root);out=root/'book'
            manifest,report=build_book(source,db,out)
            self.assertEqual(report['definitions'],1);self.assertEqual(report['linked_references'],1)
            self.assertEqual(report['unresolved'][0]['href'],'#missing')
            self.assertEqual((out/'topics/style.css').read_bytes(),(source/'topics/style.css').read_bytes())
            self.assertFalse((out/'index.htm').exists())
            self.assertFalse((out/'anna-assets/reader.js').exists())
            self.assertTrue((out/'anna-assets/topic.js').exists())
            reader=json.loads((out/'anna-reader.json').read_text())
            self.assertEqual(reader['default_page'],'topics/test.htm')
            self.assertEqual(reader['pages']['topics/test.htm']['definitions'][0]['id'],'ver_test')
            self.assertEqual((out/'topics/test.htm').read_bytes().split(MARKER)[0],(source/'topics/test.htm').read_bytes())
            m2,_=build_book(source,db,out);self.assertEqual(manifest,m2)
            self.assertFalse((root/'book.previous').exists())
    def test_stale_source_rejected_and_previous_output_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root);out=root/'book'
            build_book(source,db,out);before=(out/'anna-manifest.json').read_bytes()
            (source/'topics/test.htm').write_text('changed')
            with self.assertRaisesRegex(ValueError,'snapshot'):build_book(source,db,out)
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
            conn.execute('UPDATE pages SET sha256=?,text=?',(fingerprint(page),doc.text))
            conn.execute('DELETE FROM effective_references')
            conn.commit();conn.close();doc.close()
            manifest,report=build_book(source,db,out)
            self.assertFalse(manifest['entries']['ver_test']['locatable'])
            self.assertEqual(report['unlocatable_definitions'],['ver_test'])
            self.assertEqual(report['clickable_definitions'],0)

    def test_source_output_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root)
            with self.assertRaises(ValueError):build_book(source,db,source/'nested')

    def test_no_upstream_application_is_published(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root);out=root/'book'
            for name in ['index.html','indexh.htm','webhelpframe.htm','webhelpleft.htm','webhelpsearch.htm','webhelpindex.htm','webhelpbookmark.htm']:
                (source/name).write_text('<h1>old reader</h1>')
            (source/'assets').mkdir()
            for name in ['webhelp-shell.js','webhelp-prepaint.js','webhelp-topic.css']:
                (source/'assets'/name).write_text('fixture')
            (source/'webhelp-content-bridge.js').write_text('old protocol')
            before={str(p):p.read_bytes() for p in source.rglob('*') if p.is_file()}
            manifest,_=build_book(source,db,out)
            self.assertEqual(before,{str(p):p.read_bytes() for p in source.rglob('*') if p.is_file()})
            self.assertFalse(list(out.glob('*.htm*')))
            self.assertIn('index.htm',manifest['inputs'])
            self.assertNotIn('index.htm',manifest['outputs'])
            for name in ['assets/webhelp-shell.js','assets/webhelp-prepaint.js','webhelp-content-bridge.js']:
                self.assertNotIn(name,manifest['outputs'])
                self.assertIn(name,manifest['inputs'])
            self.assertIn('assets/webhelp-topic.css',manifest['outputs'])

    def test_page_without_entries_and_snapshot_change(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root);out=root/'book'
            conn=sqlite3.connect(db);conn.execute('DELETE FROM entries');conn.execute('DELETE FROM effective_references')
            conn.execute("UPDATE pages SET text=''")  # Editorial pages are not extracted, but remain readable.
            conn.commit();conn.close()
            m,_=build_book(source,db,out)
            reader=json.loads((out/'anna-reader.json').read_text())
            self.assertEqual(reader['pages']['topics/test.htm']['definitions'],[])
            self.assertIn(b'/anna-assets/topic.js',(out/'topics/test.htm').read_bytes())
            self.assertEqual(page_data(reader,m,path='topics/test.htm')['title'],'test')
            for kw,code in [({},'invalid_location'),({'path':'../style.css'},'page_missing'),({'entry':'unknown'},'entry_missing')]:
                with self.assertRaises(ReaderError) as raised:page_data(reader,m,**kw)
                self.assertEqual(raised.exception.code,code)
            (source/'topics/style.css').write_text('h2 {color:blue}')
            build_book(source,db,out)
            self.assertNotEqual(reader['snapshot'],json.loads((out/'anna-reader.json').read_text())['snapshot'])

    def test_navigation_keeps_duplicate_pages_anchors_and_missing_nodes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root)
            (source/'webhelpcontents.htm').write_text('''<a target="content" href="#" onclick="LinkClick(0)">分组</a><div id="d0">
              <a target="content" href="topics/test.htm#Spell" onclick="LinkClick(1)">入口一</a>
              <a target="content" href="topics/test.htm#other" onclick="LinkClick(2)">入口二</a>
              <a target="content" href="topics/missing.htm" onclick="LinkClick(3)">缺失</a></div>''')
            data=build_navigation(source,{'topics/test.htm':{}})
            self.assertEqual([n['status'] for n in data['nodes']],['placeholder','present','present','missing'])
            self.assertEqual([n['parent_id'] for n in data['nodes']],['','0','0','0'])
            self.assertEqual([n['fragment'] for n in data['nodes'][1:3]],['Spell','other'])
            self.assertEqual(data['default_page'],'topics/test.htm')
            (source/'webhelpcontents.htm').write_text('<a target="content" href="topics/test.htm">no id</a>')
            with self.assertRaisesRegex(ValueError,'目录'):build_navigation(source,{'topics/test.htm':{}})

    def test_presentation_uses_only_available_source_content_assets(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root)
            self.assertEqual(build_navigation(source,{'topics/test.htm':{}})['presentation_resources'],[])
            (source/'assets').mkdir()
            for file in ['webhelp-topic.css','webhelp-contrast.js','webhelp-shell.js']:
                (source/'assets'/file).write_text('fixture')
            assets=build_navigation(source,{'topics/test.htm':{}})['presentation_resources']
            self.assertEqual([asset['id'] for asset in assets],['webhelpTopicStyles','webhelpContrastScript'])
            self.assertEqual([asset['kind'] for asset in assets],['style','script'])

    def test_updated_source_and_database_publish_new_targets_without_name_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,db=self.fixture(root);out=root/'book'
            build_book(source,db,out)
            before=json.loads((out/'anna-reader.json').read_text())['snapshot']
            page=source/'topics/test.htm'
            page.write_text('<h2 id="New">甲 Alpha</h2><p>更新后的规则。</p>')
            with self.assertRaisesRegex(ValueError,'snapshot'):build_book(source,db,out)
            doc=Document('topics/test.htm',page.read_text());candidate=doc.candidates()[0]
            conn=sqlite3.connect(db)
            entry=json.loads(conn.execute('SELECT data FROM entries').fetchone()[0])
            entry.update(id='ver_updated',start=candidate.start,end=candidate.end,title_end=candidate.title_end,anchors=['New'])
            conn.execute('UPDATE entries SET data=?',(json.dumps(entry),))
            conn.execute('UPDATE pages SET text=?,sha256=?',(doc.text,fingerprint(page)))
            conn.execute('DELETE FROM effective_references');conn.commit();conn.close();doc.close()
            manifest,_=build_book(source,db,out)
            reader=json.loads((out/'anna-reader.json').read_text())
            self.assertNotEqual(before,reader['snapshot'])
            self.assertEqual(page_data(reader,manifest,entry='ver_updated')['fragment'],'anna-ver_updated')
            with self.assertRaises(ReaderError) as raised:page_data(reader,manifest,entry='ver_test')
            self.assertEqual(raised.exception.code,'entry_missing')
            self.assertEqual((out/'topics/test.htm').read_bytes().split(MARKER)[0],page.read_bytes())

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
            ref={'id':'format_reference','path':'topics/test.htm','start':a,'end':b,'text':'格式引用','signal':'format',
                 'status':'resolved','target_id':'ver_test','can_link':True}
            conn.execute('INSERT INTO effective_references VALUES(?,?,?,?,?,?,?)',('format_reference','topics/test.htm',a,'format','resolved',1,json.dumps(ref)))
            conn.commit();conn.close();doc.close()
            _,report=build_book(source,db,out)
            self.assertEqual(report['linked_references'],2)  # one href + one resolved mark
            raw=(out/'topics/test.htm').read_bytes();self.assertEqual(raw.split(MARKER)[0],page.read_bytes())
            data=json.loads(raw.split(b'id="anna-bindings">')[1].split(b'</script>')[0])
            self.assertEqual(data['references'][-1]['range']['text'],'格式引用')
            css.write_text('changed convention')
            with self.assertRaisesRegex(ValueError,'Concept discovery'):build_book(source,db,out)
            self.assertEqual((out/'topics/test.htm').read_bytes(),raw)


class LinkRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node is needed for the link-layer fixture')
    def test_link_runtime(self):
        result=subprocess.run(['node',str(Path(__file__).with_name('link_runtime.cjs'))],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
