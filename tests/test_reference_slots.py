"""Reference vocabulary comes from the definition collection, not a global alias for a letter or noun."""
import json
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from anna.catalog.concepts import build_concepts
from anna.catalog.frames import extract_page_entries, weapon_cells
from anna.catalog.reference_slots import SlotVocabulary, measurement_rule, value_tokens
from anna.catalog.types import NO_TOC_PLACE
from anna.source.html import Document


class SlotTests(unittest.TestCase):
    def test_field_grammar_uses_original_spans_and_rejects_mixed_text(self):
        for field,text,want in [
            ('components',' Q、R（用到 Q、R（任选）），未知 ',['Q','R','未知']),
            ('properties','轻灵（范围 20/60；例外），复合（1d8）',['轻灵','复合']),
            ('damage',' 1d8 + 2 示范 ',['示范']),('damage','触碰后造成1d8示范',[]),
            ('duration','稳定，至多5分钟',['稳定','5分钟']),('duration','5分钟',['5分钟']),
            ('duration','稳定或 1 个小时（见正文）',['稳定','1 个小时']),
            ('duration','稳定（译注：示例）',['稳定']),('duration','持续5点能量',[]),
            ('components','V、Q（嵌套（说明）例外)。',['V','Q']),
            ('damage','1d6 烈焰/冲击（由你选择）',['烈焰','冲击']),
            ('properties','折光2、炽光',['折光2','炽光']),('properties','折光（1/2）炽光',['折光','炽光']),
            ('components','Q（嵌套（错误）',[]),('properties','重型（夹注)) 、轻型',[]),
            ('components','Q and $$',[]),('properties','—',[])]:
            with self.subTest(field=field,text=text):
                self.assertEqual([text[a:b] for a,b in value_tokens(field,text)],want)

    def test_reference_family_requires_a_labelled_definition_collection(self):
        path='topics/test/规则.htm'
        for name, en, column, expect in [
            ('伤害类型','Damage Types','例子',['烈焰','冲击']),
            ('遭遇类型','Encounter Types','例子',[]), ('伤害类型','Damage Types','价格',[])]:
            raw=f'<h2>总则 Rules</h2><p><b>{name} {en}</b></p><table><tr><th>类型</th><th>{column}</th></tr><tr><td>烈焰 Ember</td><td>例一</td></tr><tr><td>冲击 Impact</td><td>例二</td></tr></table>'
            doc=Document(path,raw)
            entries,_,_=extract_page_entries(doc,[],'',NO_TOC_PLACE,True)
            ds=[e for e in entries if e.get('slot_definition')]
            self.assertEqual([e['name'].split()[0] for e in ds],expect)
            for e in ds:
                self.assertEqual(e['slot_definition']['family'],'damage')
                self.assertIn('例',doc.text[e['title_end']:e['end']])
            doc.close()

    def test_supplement_must_identify_its_properties_as_weapon_rules(self):
        for item in ('武器', '工具'):
            doc = Document('topics/test/规则.htm', f'<h2>总则 Sample</h2><h3>词条 Properties</h3>'
                           f'<h4>折光 Prism</h4><p>有此词条的{item}标识为“折光X”。</p>'
                           f'<h4>交响 Echo</h4><p>有此词条的{item}可以发声。</p>')
            entries,_,_ = extract_page_entries(doc, [], '', NO_TOC_PLACE, False)
            members = [(e['name'],e['type'],e['slot_definition']['family']) for e in entries if 'slot_definition' in e]
            self.assertEqual(members, [('折光 Prism','rule','properties'),('交响 Echo','rule','properties')] if item=='武器' else [])
            doc.close()

    def test_weapon_roles_follow_repeated_headers_without_prices(self):
        for header_tag in ('th','td'):
            def tr(tag, cells):
                return '<tr>'+''.join(f'<{tag}>{v}</{tag}>' for v in cells)+'</tr>'
            raw = '<table>'+tr('th',['早期物品','伤害','属性','重量'])
            raw += tr('td',['武器甲 Alpha','1d8烈焰','折光','3磅'])
            raw += tr(header_tag,['晚期物品','属性','重量','伤害'])
            raw += tr('td',['武器乙 Beta','交响','5磅','1d6冲击'])
            raw += tr('td',['章节 Chapter','交响','5磅','说明'])+'</table>'
            doc=Document('topics/test/武器.htm',raw)
            entries,_,_=extract_page_entries(doc,[],'',NO_TOC_PLACE,False)
            gear=[e for e in entries if e['type']=='equipment']
            self.assertEqual([e['name'] for e in gear],['武器甲 Alpha','武器乙 Beta'])
            cells=weapon_cells(doc.path,doc.text,doc.dom_snapshot()['concept_tables'])
            for e, damage, prop in zip(gear,('1d8烈焰','1d6冲击'),('折光','交响')):
                for role,label in [('damage',damage),('properties',prop)]:
                    val,a,b=e['fields'][role][0]
                    kind, ev=cells[a+e['start'],b+e['start']]
                    self.assertEqual((val,kind,ev['cell_text']), (label,role,label))
                    self.assertEqual(ev['headers'][ev['column']], '伤害' if role=='damage' else '属性')
            doc.close()

    def test_parameter_and_measured_duration_require_a_printed_rule(self):
        entries = {}
        pages = {}
        def member(i, name, family, body, **evidence):
            entries[i] = dict(id=i,name=name,name_zh=name,name_en='',page_path=i,title_end=0,end=len(body),
                              slot_definition=dict(family=family,heading='一组定义',**evidence))
            pages[i] = {'text':body}
        member('template','折光','properties','这个词条记作“折光X”。')
        member('literal','折光2','properties','这是名称。')
        member('other','发光','properties','这把武器有2道光。')
        rule = measurement_rule('单位是轮、分钟或其他。')
        self.assertEqual(rule, '单位是轮、分钟或其他')
        self.assertIsNone(measurement_rule('例如1分钟。')); self.assertIsNone(measurement_rule('单位是磅、尺。'))
        member('interval','区间','duration',rule,measure=rule)
        v = SlotVocabulary(SimpleNamespace(entries=entries),pages)
        self.assertCountEqual(v.candidates('properties','折光2'), ['template','literal'])
        self.assertEqual(v.candidates('properties','折光9')['template'][0]['notation'], '折光X')
        self.assertFalse(v.candidates('properties','发光9')); self.assertFalse(v.candidates('mastery','折光9'))
        self.assertEqual(v.candidates('duration','一轮')['interval'][0]['measure'],rule)
        self.assertFalse(v.candidates('duration','一磅')); self.assertFalse(v.candidates('components','一轮'))

    def observations(self, *, duplicate=False, generation='5r', unknown_scope=False):
        path='topics/test/甲.htm'; defs='topics/test/规则.htm'
        raw=('<h2>样本 Sample</h2><p>法术成分：Q、Z（印着Q）</p><p>持续时间：轻灵，至多1分钟</p>'
             '<p>其他的 Q、轻灵。</p><table><tr><th>名称</th><th>伤害</th><th>词条</th><th>精通</th></tr>'
             '<tr><td>兵器</td><td>1d6 轻灵</td><td>轻灵（说明）</td><td>轻灵</td></tr></table>')
        doc=Document(path,raw);text=doc.text
        def entry(i,n,kind,p=path,r='5r',**kw):
            return dict(id=i,name=n,name_zh=n.split()[0],name_en=n.partition(' ')[2],type=kind,
                        source_ids=['s' if r=='5r' else 'old'],ruleset=r,page_path=p,start=0,title_end=0,end=len(text),**kw)
        entries=[entry('spell','样本','spell'),entry('gear','兵器','equipment')]
        for i,n,f in [('q','气音 Echo（Q）','components'),('d','轻灵 Nimble','duration'),
                      ('p','轻灵 Nimble','properties'),('m','轻灵 Nimble','mastery'),('x','轻灵 Nimble','damage')]:
            entries.append(entry(i,n,'rule',defs,generation,slot_definition={'family':f,'heading':'一组定义'}))
        entries.append(entry('c','成分合集','rule',defs,generation,slot_collection='components'))
        entries.append(entry('global','Q','spell',defs))
        if duplicate:
            entries.append({**entries[2],'id':'q2'})
        facts=[]
        def fact(field,a,b,owner,rule='body_label'):
            facts.append(dict(field=field,entry_id=owner,rule=rule,evidence={'span':[a,b],'path':path}))
        for tag,field in zip(doc.body.find_all('p'),('components','duration','components')):
            fact(field,*doc.span(tag),'spell')
        for tag,field in zip(doc.body.find_all('td')[1:],('damage','properties','mastery')):
            fact(field,*doc.span(tag),'gear')
        with tempfile.TemporaryDirectory() as td:
            db=sqlite3.connect(':memory:')
            db.executescript('create table metadata(key,value);')
            pages={path:{'text':text,'parse_status':'ok'},defs:{'text':'定义','parse_status':'excluded'}}
            build_concepts(db,Path(td),None,entries,pages,
                           [] if unknown_scope else [{'id':'s','ruleset_hint':'5r'},{'id':'old','ruleset_hint':'5e'}],
                           dom_by_path={path:doc.dom_snapshot()},field_facts=list(reversed(facts)))
            rows=[json.loads(d) for d, in db.execute("select data from effective_references where signal in ('field','table') order by start")]
            db.close()
        doc.close()
        return rows

    def test_same_text_resolves_by_family_and_letter_does_not_escape_its_field(self):
        rows=self.observations()
        self.assertEqual([(r['text'],r['target_id']) for r in rows],
                         [('法术成分','c'),('Q','q'),('Z',None),('持续时间',None),('轻灵','d'),('1分钟',None),('轻灵','x'),('轻灵','p'),('轻灵','m')])
        self.assertEqual(rows[2]['reason'],'no_definition')
        self.assertEqual(rows[-1]['evidence']['table']['headers'][3],'精通')

    def test_ambiguous_missing_and_wrong_generation_are_not_linked(self):
        duplicated=self.observations(duplicate=True)[1]
        self.assertEqual((duplicated['status'],duplicated['candidates']),('ambiguous',['q','q2']))
        for kwargs,reason in [({'generation':'5e'},'ruleset_mismatch'),({'unknown_scope':True},'slot_scope_unconfirmed')]:
            row=self.observations(**kwargs)[1]
            self.assertIsNone(row['target_id']);self.assertEqual(row['reason'],reason)
