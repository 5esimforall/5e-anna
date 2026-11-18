import unittest

from anna.catalog.concepts import Resolver, overlap_check
from anna.catalog.frames import extract_page_entries
from anna.catalog.reference_slots import SlotVocabulary, resolve_field_slots
from anna.catalog.typed_mentions import column_mentions, damage_mentions, damage_notation
from anna.catalog.types import NO_TOC_PLACE
from anna.source.html import Document, split_name


def entry(id,name,path,source='s',ruleset='5r',kind='rule',**kw):
    zh,en=split_name(name)
    return dict(id=id,type=kind,name=name,name_zh=zh,name_en=en,page_path=path,source_ids=[source],
                ruleset=ruleset,start=0,title_end=0,end=999,**kw)


def catalog(entries,texts):
    sources=[{'id':'s','ruleset_hint':'5r'},{'id':'old','ruleset_hint':'5e'}]
    resolver=Resolver(entries,sources)
    return resolver,SlotVocabulary(resolver,{p:{'text':t} for p,t in texts.items()})


class MentionTests(unittest.TestCase):
    def test_typed_dice_only_matches_the_name_and_preserves_ambiguity_and_scope(self):
        text='示例，17（3D8＋2）点碎光伤害；碎光如昼；8d6虚构伤害；5d6碎光伤害。ab1d6碎光伤害。'
        def member(i,**kw):
            return entry(i,'碎光 Glint','defs',slot_definition={'family':'damage','heading':'伤害类型'},**kw)
        owner=entry('owner','正文','prose',kind='spell')
        source=[owner,member('d'),member('d14',source='old',ruleset='5e'),entry('other','碎光','defs',kind='spell')]
        def scan(rows):
            r,v=catalog(rows,{'prose':text,'defs':'解释'})
            return list(damage_mentions(text,'prose',r,v,damage_notation(v),overlap_check([(text.index('5d6'),text.index('。'))])))
        found=scan(source);self.assertEqual(len(found),2)
        (a,b),(_,ev,res)=found[0]
        self.assertEqual((text[a:b],res['targets'],res['status']),('碎光',['d'],'resolved'))
        self.assertEqual(ev['syntax']['notation'],'3D8＋2）点碎光伤害')
        self.assertEqual(found[1][1][2]['reason'],'plain_non_prose')
        self.assertEqual(scan(source+[member('twin')])[0][1][2]['status'],'ambiguous')
        self.assertEqual(scan(source[1:])[0][1][2]['reason'],'slot_scope_unconfirmed')

    def test_legend_is_a_local_clause_not_an_alias_for_the_letter(self):
        table='<table><tr><th>法术</th><th>学派</th><th>记号</th></tr><tr><td>示例</td><td>折光</td><td>Q，Z</td></tr></table>'
        raw=('<h2>说明 Guide</h2><p>记号一栏中，Q代表第一次解释。</p>'+table+
             '<h2>续篇 Other</h2><p>记号一栏中，Q代表另一个解释。</p>'+table+
             '<h2>无声明 No Legend</h2>'+table)
        doc=Document('p',raw)
        try:
            es=extract_page_entries(doc,[],'',NO_TOC_PLACE,False)[0]
            members=[e for e in es if e.get('slot_definition',{}).get('family')=='legend']
            self.assertEqual([e['slot_definition']['meaning'] for e in members],['第一次解释','另一个解释'])
            src=[{**entry(str(i),e['name'],'p'),**e} for i,e in enumerate(members)]
            src += [entry('owner','总览','p'),entry('school','折光 Prism','defs',
                     slot_definition={'family':'school','heading':'学派'}),entry('homonym','折光 Prism','defs',kind='spell')]
            r,v=catalog(src,{'p':doc.text,'defs':'定义'})
            tables=doc.dom_snapshot()['concept_tables']
            def scan():return list(column_mentions(doc.text,'p',tables,r,v))
            found=scan()
            self.assertEqual([(doc.text[a:b],z['targets']) for (a,b),(_,_,z) in found],
                             [('折光',['school']),('Q',['0']),('Z',[]),('折光',['school']),('Q',['1']),('Z',[]),('折光',['school'])])
            v.legends['p','记号'].append({**src[0],'id':'conflict'})
            r.entries['conflict']={**src[0],'id':'conflict'}
            self.assertEqual(scan()[1][1][2]['status'],'ambiguous')
            self.assertFalse(v.candidates('legend','Q'))
            for table_meta in tables:table_meta['irregular_columns']=True
            self.assertEqual(scan(),[])
        finally:doc.close()

    def test_range_parameters_require_both_property_declaration_and_named_table_member(self):
        text='替身（射程 10/30；针弹）'
        body='所需弹药的种类会与武器的射程写在一起。'
        gear=entry('gear','武器','source',kind='equipment')
        prop=entry('p','替身 Proxy','prop',slot_definition={'family':'properties','heading':'武器词条'})
        ammo=entry('a','针弹 Dart','ammo',kind='equipment',slot_definition={'family':'ammunition','heading':'弹药'})
        def scan(paragraph,extra=()):
            r,v=catalog([gear,prop,ammo,entry('same','针弹','ammo',kind='spell'),*extra],
                        {'source':text,'prop':paragraph,'ammo':'表行'})
            ev={'field':'properties','field_span':[0,len(text)],'field_text':text}
            return resolve_field_slots([(gear,0,2,'table',ev)],r,v,'source',text)
        found=scan(body)
        self.assertEqual(found[text.index('针弹'),text.index('针弹')+2][2]['targets'],['a'])
        self.assertEqual(scan('其所需的弹药类型会在该武器的射程旁边注明。')[text.index('针弹'),text.index('针弹')+2][2]['targets'],['a'])
        self.assertEqual(len(scan('无参数说明。')),1)
        self.assertEqual(len(scan('弹药的种类不会与武器的射程写在一起。')),1)
        self.assertEqual(len(scan(body,[{**prop,'id':'conflict'}])),1)
        self.assertEqual(scan(body,[{**ammo,'id':'other'}])[text.index('针弹'),text.index('针弹')+2][2]['status'],'ambiguous')

    def test_ammunition_inventory_accepts_bounded_section_or_caption_not_a_nearby_mention(self):
        table=('<table><tr><th>类型</th><th>数量</th><th>存放容器</th><th>重量</th><th>价格</th></tr>'
               '<tr><td>针弹 Darts</td><td>5</td><td>袋</td><td>1磅</td><td>2GP</td></tr></table>')
        for heading,between,expected in [('额外弹药类型 Additional Ammunition Types','',True),
                                         ('武器 Weapon','',False),
                                         ('额外弹药类型 Additional Ammunition Types','<h2>其他 Other</h2>',False)]:
            with self.subTest(heading=heading,between=between):
                doc=Document('p','<h1>第一章 Chapter</h1><h2>'+heading+'</h2>'+between+table)
                try:
                    rows=extract_page_entries(doc,[],'',NO_TOC_PLACE,False)[0]
                    self.assertEqual(any(e.get('slot_definition',{}).get('family')=='ammunition' for e in rows),expected)
                finally:doc.close()
