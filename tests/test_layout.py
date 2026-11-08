"""Original-layout reader: sanitized source structure, evidence marks and snapshot checks."""
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from html_extract import Document
from entry_layout import entry_layout, render
import paths
from serve import DB,connect

ALLOWED_CLASSES={'note','inferred','short','ref'}


def tags(markup):return set(re.findall(r'<([a-z0-9]+)',markup))


class RenderTests(unittest.TestCase):
    def doc(self,body):return Document('fixture.html','<html><body>'+body+'</body></html>')
    def test_source_markup_is_reduced_to_inert_structure(self):
        doc=self.doc('<div id="w" style="color:red" onclick="x()"><h1>标题</h1><p>甲<script>alert(1)</script><img src=x onerror=alert(1)>'
                     '<a href="javascript:alert(1)" class="spell">火球术</a><iframe src="//x"></iframe></p>'
                     '<table><tr><td colspan="999" style="x">长</td><td colspan="2" rowspan="3">二</td></tr></table></div>')
        out=render(doc,0,len(doc.text),[])
        self.assertEqual(tags(out),{'div','h3','p','span','table','tbody','tr','td'})
        for bad in ['script','alert','onerror','onclick','style=','href','iframe','<img','id=','spell']:self.assertNotIn(bad,out)
        self.assertIn('<td class="short">长</td>',out);self.assertIn('<td colspan="2" rowspan="3" class="short">二</td>',out)
        doc.close()
    def test_text_is_escaped_and_clipped_to_the_range(self):
        doc=self.doc('<p>前文。&lt;b&gt;正文&amp;内容。后文。</p>')
        start=doc.text.index('<b>');end=doc.text.index('后文')
        out=render(doc,start,end,[])
        self.assertEqual(out,'<p>&lt;b&gt;正文&amp;内容。</p>')
        doc.close()
    def test_layers_mark_evidence_and_link_references_by_offset(self):
        doc=self.doc('<p><strong>施法时间：</strong>动作，见<em>火球术</em>。</p>')
        a=doc.text.index('施法');b=doc.text.index('，');r=doc.text.index('火球术')
        layers=[(a,b,'note',{'field':'casting_time','status':'原文明示'}),(a,b,'note',{'field':'casting_time_detail','status':'上下文推得'}),
                (r,r+3,'ref','ver_"x"')]
        out=render(doc,0,len(doc.text),layers)
        # One evidence span crossing an element boundary is marked on each side of it.
        self.assertEqual(out.count('data-notes="casting_time casting_time_detail"'),2)
        self.assertIn('<em><button type="button" class="ref" data-entry="ver_&quot;x&quot;">火球术</button></em>',out)
        inferred=render(doc,0,len(doc.text),[(a,b,'note',{'field':'x','status':'上下文推得'})])
        self.assertIn('class="note inferred"',inferred)
        self.assertTrue(set(re.findall(r'class="([^"]+)"',out+inferred))<={c for c in ALLOWED_CLASSES}|{'note inferred'})
        doc.close()


@unittest.skipUnless(DB.exists() and paths.WEB.is_dir(),'Build corpus first')
class CorpusLayoutTests(unittest.TestCase):
    def setUp(self):self.db=connect()
    def tearDown(self):self.db.close()
    def entry(self,kind,name,ruleset):return self.db.execute('SELECT id FROM entries WHERE type=? AND name_en=? AND ruleset=?',(kind,name,ruleset)).fetchone()[0]
    def test_spell_layout_marks_fields_after_the_title(self):
        eid=self.entry('spell','Etherealness','5r');layout=entry_layout(self.db,eid,paths.WEB)
        self.assertEqual(layout['status'],'ok')
        self.assertNotIn('以太化｜Etherealness',layout['html'])
        self.assertIn('<mark class="note" data-notes="school spell_level">七环 咒法</mark>',layout['html'])
        notes=[n['field'] for n in layout['notes']]
        self.assertEqual(notes[:2],['school','spell_level'])
        self.assertTrue({'type','ruleset','source_id','source_code'}.isdisjoint(notes))
        # Fields read from a quick-reference table elsewhere follow, unlocated.
        located=[n['located'] for n in layout['notes']]
        self.assertEqual(located,sorted(located,reverse=True));self.assertIn(False,located)
    def test_statblock_keeps_its_tables(self):
        layout=entry_layout(self.db,self.entry('creature','Adult Red Dragon','5r'),paths.WEB)
        self.assertEqual(layout['status'],'ok')
        self.assertGreaterEqual(layout['html'].count('<table>'),2);self.assertIn('colspan="2"',layout['html'])
        self.assertIn('<td class="short"><strong><mark class="note" data-notes="strength">力量</mark></strong></td>',layout['html'])
    def test_changed_or_missing_source_falls_back(self):
        eid=self.entry('spell','Etherealness','5r');path=json.loads(self.db.execute('SELECT data FROM entries WHERE id=?',(eid,)).fetchone()[0])['page_path']
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertEqual(entry_layout(self.db,eid,root)['status'],'missing')
            copy=root/path;copy.parent.mkdir(parents=True);shutil.copyfile(paths.WEB/path,copy)
            self.assertEqual(entry_layout(self.db,eid,root)['status'],'ok')
            copy.write_bytes(copy.read_bytes().replace('以太位面'.encode(),'以太界'.encode(),1))
            self.assertEqual(entry_layout(self.db,eid,root)['status'],'stale')
        with self.assertRaises(LookupError):entry_layout(self.db,'ver_missing',paths.WEB)
    def test_resolved_references_become_entry_buttons(self):
        row=self.db.execute("SELECT r.page_path,r.start,r.end,r.data FROM effective_references r JOIN entries e ON e.page_path=r.page_path AND r.start>=json_extract(e.data,'$.title_end') AND r.end<=e.end "
                            "WHERE e.type='spell' AND json_extract(r.data,'$.target_id') IS NOT NULL AND json_extract(r.data,'$.target_id')!=e.id LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        ref=json.loads(row[3])
        eid=self.db.execute("SELECT id FROM entries WHERE page_path=? AND type='spell' AND json_extract(data,'$.title_end')<=? AND end>=? ORDER BY start DESC",(row[0],row[1],row[2])).fetchone()[0]
        layout=entry_layout(self.db,eid,paths.WEB)
        self.assertIn(f'class="ref" data-entry="{ref["target_id"]}"',layout['html'])


if __name__=='__main__':unittest.main()
