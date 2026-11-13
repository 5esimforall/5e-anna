import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from concepts import Resolver,format_kind,build_concepts,plain_syntax,style_profile,term_scanner
from html_extract import Document
from entry_types import NO_TOC_PLACE
from build import Build,parse_one

def entry(eid,name,path='topics/book/rules.htm',ruleset='5r',kind='rule',source='book'):
    return dict(id=eid,entity_id=eid,name=name,name_zh=name,name_en='',type=kind,page_path=path,ruleset=ruleset,source_ids=[source],start=0,title_end=0,end=100,anchors=[eid],is_primary=True)

class ConceptTests(unittest.TestCase):
    def test_rule_children_require_parallel_definitions(self):
        raw=('<h3>屏障 Barrier</h3>'
             '<p>屏障分为两种：<u>软壁</u>（阻挡小型生物（含变形者））、<u>硬壁</u>（阻挡所有生物）。共同规则。</p>'
             '<p>例如有两种：<u>山墙</u>（此处不是规则）、<u>外墙</u>（只是场景说明）。</p>'
             '<p>包括：<u>浅壁</u>（阻挡体型较小者）、<u>厚壁</u>（未闭合。</p>'
             '<p>处于<b>低阶屏障</b><b>Low Barrier</b>下的目标获得一项增益。<br>'
             '处于<strong>高阶屏障 High Barrier</strong>下的目标获得两项增益。<br>'
             '例如处于<b>传奇屏障 Legendary Barrier</b>下的目标获得三项增益。</p>'
             '<p><strong>规则解释 Rules Explanation。</strong>这只是附注，不另立词条。<br>'
             '<strong>屏障例外 Barrier Exception。</strong>此屏障不会阻挡它的施法者。</p>'
             '<blockquote><p>包括：<u>远墙</u>（说明其独立效果）、<u>近墙</u>（说明其独立效果）。</p></blockquote>')
        doc=Document('topics/fixture.htm',raw)
        children=list(doc.rule_children(doc.span(doc.soup.h3)[1],len(doc.text),'屏障 Barrier'))
        self.assertEqual([row[3] for row in children],['软壁','硬壁','低阶屏障Low Barrier','高阶屏障 High Barrier','屏障例外 Barrier Exception'])
        for a,b,end,name,method in children:
            self.assertTrue(a<b<end)
            self.assertNotIn('共同规则',doc.text[b:end])
            self.assertNotIn('例如',doc.text[b:end])
            self.assertEqual(doc.text[a:b].strip().rstrip('。'),name)
        self.assertEqual(doc.text[children[0][1]:children[0][2]],'（阻挡小型生物（含变形者））')
        doc.close()

    def test_core_rule_children_keep_parent_generation_and_source_evidence(self):
        from paths import WEB
        build=Build(WEB)
        for path,ruleset,body in [
            ('topics/玩家手册/战斗/掩护.htm','5e','+2'),
            ('topics/玩家手册2024/术语汇编/其他术语.htm','5r','+2'),
        ]:
            result=parse_one(build.parse_job(path));build.consume(result)
            children=[e for e in build.page_entries[path] if e['name_zh'] in {'半身掩护','四分之三掩护','全身掩护'}]
            self.assertEqual([e['name_zh'] for e in children],['半身掩护','四分之三掩护','全身掩护'])
            half=children[0];parent=build.entry_by_id[half['parent_id']]
            self.assertEqual((half['type'],half['ruleset'],half['source_ids']),('rule',ruleset,parent['source_ids']))
            self.assertEqual(parent['name_zh'],'掩护')
            self.assertIn(body,result['page']['text'][half['title_end']:half['end']])
            self.assertNotIn('四分之三掩护',result['page']['text'][half['start']:half['end']])
            self.assertFalse(any(c['classification_rule']=='rule_subdefinition' for c in parse_one((*build.parse_job(path)[:-1],False))['candidates']))
        path='topics/玩家手册2024/术语汇编/状态.htm'
        build.consume(parse_one(build.parse_job(path)))
        levels=next(e for e in build.page_entries[path] if e['name_zh']=='力竭等级')
        self.assertEqual(build.entry_by_id[levels['parent_id']]['name_zh'],'力竭')
        self.assertFalse(any(e['name_zh']=='攻击影响' for e in build.page_entries[path]))

    def test_plain_rule_use_requires_syntax_scope_and_unique_core_identity(self):
        path='topics/fixture/prose.htm'
        context=entry('context','正文',path,kind='section',source='supplement')
        new=entry('new','护障',source='core')
        old=entry('old','护障',path='topics/old/rule.htm',ruleset='5e',source='old_core')
        sources=[
            {'id':'core','ruleset_hint':'5r','reference_role':'core_rules','evidence':[]},
            {'id':'old_core','ruleset_hint':'5e','reference_role':'core_rules','evidence':[]},
            {'id':'supplement','ruleset_hint':'5r','reference_role':'other','evidence':[]},
        ]
        def resolve(text,entries=None,non_prose=False,previous=None):
            a=text.rindex('护障');b=a+2
            return Resolver(entries or [context,new,old],sources).resolve_plain('护障',path,a,b,plain_syntax(text,a,b,previous),non_prose)
        result,evidence=resolve('你获得护障。')
        self.assertEqual((result['status'],result['reason'],result['targets']),('resolved','source_core_rules',['new']))
        self.assertEqual(evidence['scope'],{'source_ids':['supplement'],'ruleset':'5r','owner_ids':['context']})
        self.assertEqual(evidence['syntax'],{'rule':'rule_use_clause','slot':'rule','cue_span':[1,3]})
        result,evidence=resolve('你获得护盾和护障。',previous=(len('你获得护盾'),'rule'))
        self.assertEqual((result['targets'],evidence['syntax']['rule']),(['new'],'coordinated_rule_use'))
        for prose in ('他介绍护障。','你获得护障器。','你获得新护障。','你可以护障。'):
            self.assertEqual(resolve(prose)[0]['reason'],'lexical_candidate')
        self.assertEqual(resolve('你获得护障。',non_prose=True)[0]['reason'],'plain_non_prose')
        self.assertEqual(resolve('你获得护障。',[context,old])[0]['reason'],'ruleset_mismatch')
        self.assertEqual(resolve('你获得护障。',[{**context,'ruleset':'待核'},new])[0]['reason'],'plain_scope_unconfirmed')
        result,_=resolve('你获得护障。',[context,new,{**new,'id':'another'}])
        self.assertEqual((result['status'],result['targets']),('ambiguous',['another','new']))
        result,_=resolve('你获得护障。',[context,new,{**new,'id':'spell','type':'spell'}])
        self.assertEqual((result['status'],result['reason'],result['targets']),('ambiguous','plain_competing_kind',['new','spell']))
        self.assertEqual(resolve('你获得护障。',[context,new,{**new,'id':'local','source_ids':['supplement']}])[0]['reason'],'plain_non_core_target')
        self.assertEqual(resolve('你获得护障。',[{**new,'page_path':path}])[0]['reason'],'definition_self_reference')
        action={**new,'type':'action'}
        self.assertEqual(resolve('他通常需要进行护障。',[context,action])[0]['reason'],'plain_action_context')
        self.assertEqual(resolve('你可以执行护障。',[context,action])[0]['targets'],['new'])
        self.assertEqual(resolve('你可以进行护障动作。',[context,action])[0]['status'],'resolved')

    def test_term_scanner_preserves_leftmost_longest_and_regex_case_behavior(self):
        names=['遮蔽','重度遮蔽','遮蔽区','Shield','shield wall','SHIELD','Keen','Keen eye','İN','in','long-rest','[D]']
        text='重度遮蔽遮蔽区 (sHiELD WALL, Shield) KEEN EYE, Keen; IΝ İN ın IN [d] LONG-REST'
        expected=re.compile('|'.join(map(re.escape,sorted(names,key=lambda n:(-len(n),n)))),re.I)
        spans=lambda matches:[(m.span(),m[0]) for m in matches]
        self.assertEqual(spans(term_scanner(names)(text)),spans(expected.finditer(text)))

    def test_format_order_and_conflicts(self):
        for html,kind in [('<u>优势</u>','concept'),('<u><em>法术</em></u>','spell'),('<em><u>物品</u></em>','magic_item'),('<b><u>怪物</u></b>','creature'),('<em><u><i>冲突</i></u></em>','ambiguous'),('<u>部分<em>斜体</em></u>','ambiguous'),('<u><em>全</em><i>斜体</i></u>','spell')]:
            d=Document('topics/a.htm',html);self.assertEqual(format_kind(d.soup.u),kind);d.close()
    def test_version_and_ambiguity(self):
        a=entry('a','专注');b=entry('b','专注',ruleset='5e');context=entry('c','正文','topics/book/text.htm')
        r=Resolver([a,b,context]);self.assertEqual(r.resolve('专注','concept',context['page_path'])['targets'],['a'])
        r=Resolver([a,entry('d','专注'),context]);self.assertEqual(r.resolve('专注','concept',context['page_path'])['targets'],['a','d'])
        context['ruleset']='待核';r=Resolver([a,context]);self.assertEqual(r.resolve('专注','concept',context['page_path'])['status'],'review')
    def test_an_out_of_scope_class_feature_never_supplies_the_reference_scope(self):
        # A same-name feature of another class, page and generation is left out of the candidates; its owner's
        # generation must not stand in for the page's own (it made 专精 on 2024 feat pages depend on set order).
        rule=entry('rule','专精');context=entry('c','正文','topics/book/feats.htm')
        feature={**entry('feature','专精',path='topics/old/rogue.htm',ruleset='5e',kind='class_feature',source='old'),'parent_id':''}
        result=Resolver([rule,feature,context]).resolve('专精','concept',context['page_path'],10)
        self.assertEqual((result['status'],result['targets'],result['reason']),('resolved',['rule'],'same_source'))
        self.assertEqual(result['resolution_evidence'],{'out_of_scope_feature_ids':['feature']})

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
        result=parse_one((str(WEB),path,[],path,NO_TOC_PLACE,False))
        self.assertEqual(next(c for c in result['candidates'] if c['name'].startswith('横扫'))['type'],'rule')
