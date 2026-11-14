import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from anna.catalog.concepts import Resolver,check_notation,format_kind,build_concepts,plain_syntax,style_profile,table_cell_names,term_scanner
from anna.source.html import Document
from anna.catalog.types import NO_TOC_PLACE
from anna.catalog.build import Build,parse_one

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
        from anna.paths import WEB
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

    def test_check_notation_applies_abilities_and_skills_of_their_own_kind(self):
        path='topics/fixture/prose.htm'
        context=entry('context','正文',path,kind='section',source='supplement')
        wisdom=entry('wis','感知',kind='ability',source='core');perception=entry('per','察觉',kind='skill',source='core')
        persuasion=entry('pers','游说',kind='skill',source='core');death=entry('dead','死亡',source='core')
        check=entry('check','感知检定',source='old_core',ruleset='5e')
        sources=[{'id':'core','ruleset_hint':'5r','reference_role':'core_rules','evidence':[]},
                 {'id':'old_core','ruleset_hint':'5e','reference_role':'core_rules','evidence':[]},
                 {'id':'supplement','ruleset_hint':'5r','reference_role':'other','evidence':[]}]
        # A table titled after an ability is not what a notation tests.
        table={**entry('table','感知',source='core',kind='lore'),'table_span':[0,100]}
        charmed=entry('charmed','魅惑',kind='condition',source='core');armor=entry('ac','护甲等级',source='core')
        resolver=Resolver([context,wisdom,perception,persuasion,death,check,table,charmed,armor],sources)
        def resolve(text,name,at=0):
            a=text.index(name,at);b=a+len(name)
            return resolver.resolve_plain(name,path,a,b,plain_syntax(text,a,b,None),False)
        for text,name,target,role in [('进行一次DC 15的感知（察觉）检定。','感知','wis','ability'),
                                      ('进行一次DC 15的感知（察觉）检定。','察觉','per','skill'),
                                      ('进行一次魅力（欺瞒或游说）检定。','游说','pers','skill'),
                                      ('成功通过一次感知或魅力豁免。','感知','wis','tested'),
                                      ('进行一次察觉检定。','察觉','per','tested')]:
            result,evidence=resolve(text,name)
            self.assertEqual((result['status'],result['targets'],evidence['syntax']['role']),('resolved',[target],role),(text,name))
            self.assertNotIn('clause',evidence['syntax'])
        # Each part must be a definition of its own kind: 死亡豁免 does not apply 死亡 as an ability, and the
        # skill in the parentheses is no ability.
        self.assertEqual(resolve('你需要进行死亡豁免。','死亡')[0]['reason'],'plain_check_part_mismatch')
        self.assertEqual(resolve('察觉（感知）检定','感知')[0]['reason'],'plain_check_part_mismatch')
        # A list of tested names holds short names, not a clause: 魅惑 here is a condition in a rules-use clause.
        result,evidence=resolve('不容易陷入魅惑、恐慌或是受其他感知豁免的影响。','魅惑')
        self.assertEqual((result['targets'],evidence['syntax']['rule']),(['charmed'],'rule_use_clause'))
        self.assertIsNone(check_notation('抵抗魅惑和恐慌而作的豁免检定',2,4))
        # A name the notation cannot use still fills the rules-use clause it sits in.
        result,evidence=resolve('若它提供护甲等级、攻击和伤害掷骰、豁免或法术豁免DC的加值','护甲等级')
        self.assertEqual((result['targets'],evidence['syntax']['rule']),(['ac'],'rule_use_clause'))
        # Outside the notation the words are ordinary prose.
        for text,name in [('他的感知很敏锐。','感知'),('被动感知（察觉）值','察觉'),('你察觉到了。','察觉')]:
            self.assertEqual(resolve(text,name)[0]['reason'],'lexical_candidate',text)
        # A test named in the other generation only stays a candidate of that generation.
        self.assertEqual(resolve('进行一次感知检定。','感知检定')[0]['reason'],'ruleset_mismatch')

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

    def test_out_of_scope_evidence_order_is_stable_for_review_fingerprints(self):
        rule=entry('rule','专精');context=entry('c','正文','topics/book/feats.htm')
        features=[{**entry(eid,'专精',path='topics/old/'+eid+'.htm',ruleset='5e',kind='class_feature',source='old'),'parent_id':''}
                  for eid in ['feature_z','feature_a']]
        resolver=Resolver([rule,context,*features]);results=[]
        # Exercise both source-index traversal orders without relying on a
        # particular process's randomized set iteration.
        for ids in [['feature_z','rule','feature_a'],['feature_a','feature_z','rule']]:
            resolver.names['专精']=ids
            results.append(resolver.resolve('专精','concept',context['page_path'],10))
        self.assertEqual(results[0],results[1])
        self.assertEqual(results[0]['resolution_evidence'],{'out_of_scope_feature_ids':['feature_a','feature_z']})
        self.assertEqual(results[0]['targets'],['rule'])

    def test_table_cells_accept_complete_name_lists_only(self):
        scan=term_scanner(['专精','偷袭','盗贼黑话','诗人激励','休憩曲','动作如潮','力量','敏捷','Concentration'])
        for text,expected in [
            ('专精，偷袭，盗贼黑话',['专精','偷袭','盗贼黑话']),
            ('诗人激励 (d6)',['诗人激励']),
            ('动作如潮（一次）',['动作如潮']),
            ('诗人激励 (d8) ，休憩曲',['诗人激励','休憩曲']),
            ('力量与敏捷',['力量','敏捷']),
            ('选择诗人学院，专精',[]),
            ('你没有专精',[]),
            ('影响，取悦或误导',[]),
            ('XConcentration',[]),
        ]:
            self.assertEqual([label for _,_,label in table_cell_names(text,scan)],expected,text)

    def test_table_name_lists_become_scoped_class_feature_references(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'topics/book').mkdir(parents=True)
            raw=('<h3 id="class">示例职业 Example Class</h3><p>导言</p>'
                 '<table><tr><td>等级</td><td>职业特性</td></tr>'
                 '<tr><td>1</td><td>专精，偷袭</td></tr>'
                 '<tr><td>2</td><td>灵巧动作（一次）</td></tr>'
                 '<tr><td>2</td><td>灵巧动作Cunning Action</td></tr>'
                 '<tr><td>3</td><td>子职特性，隐藏招式</td></tr>'
                 '<tr><td>0</td><td>力量</td></tr></table>'
                 '<h4 id="f1">1级：专精 Expertise</h4><p>专精正文</p>'
                 '<h4 id="f2">1级：偷袭 Sneak Attack</h4><p>偷袭正文</p>'
                 '<h4 id="f3">2级：灵巧动作 Cunning Action</h4><p>灵巧正文</p>'
                 '<h4 id="f4">3级：隐藏招式 Hidden Maneuver</h4><p>隐藏正文</p>')
            path='topics/book/class.htm';(root/path).write_text(raw)
            doc=Document(path,raw);text=doc.text
            spans={tag.get('id'):doc.span(tag) for tag in doc.soup.find_all(['h3','h4'])}
            def feature(eid,name,zh,en,anchor):
                a,b=spans[anchor]
                return {**entry(eid,name,path,kind='class_feature'),'name_zh':zh,'name_en':en,
                        'start':a,'title_end':b,'end':len(text),'parent_id':'class','is_primary':False}
            class_start,class_title_end=spans['class']
            entries=[{**entry('class','示例职业 Example Class',path,kind='class'),'name_zh':'示例职业',
                      'name_en':'Example Class','start':class_start,'title_end':class_title_end,'end':len(text)},
                     feature('f1','1级：专精 Expertise','专精','Expertise','f1'),
                     feature('f2','1级：偷袭 Sneak Attack','偷袭','Sneak Attack','f2'),
                     feature('f3','2级：灵巧动作 Cunning Action','灵巧动作','Cunning Action','f3'),
                     feature('f4','3级：隐藏招式 Hidden Maneuver','隐藏招式','Hidden Maneuver','f4'),
                     {**entry('ability','力量',path,kind='ability'),'name_zh':'力量','name_en':'Strength'}]
            pages={path:{'text':text,'parse_status':'ok'}}
            db=sqlite3.connect(':memory:')
            db.executescript('CREATE TABLE entries(id PRIMARY KEY);CREATE TABLE pages(path PRIMARY KEY,text);CREATE TABLE metadata(key PRIMARY KEY,value);')
            db.executemany('INSERT INTO entries VALUES(?)',[(e['id'],) for e in entries])
            db.executemany('INSERT INTO pages VALUES(?,?)',[(p,pages[p]['text']) for p in pages])
            report=build_concepts(db,root,root,entries,pages)
            self.assertEqual(report['outcomes']['table'],{'resolved':4})
            rows=[json.loads(r[0]) for r in db.execute("SELECT data FROM concept_observations WHERE signal='table' ORDER BY start")]
            self.assertEqual([r['text'] for r in rows],['专精','偷袭','灵巧动作','灵巧动作Cunning Action'])
            self.assertEqual([r['target_id'] for r in rows],['f1','f2','f3','f3'])
            for row in rows:
                cell=row['evidence']['table']['cell_span']
                self.assertTrue(cell[0]<=row['start']<row['end']<=cell[1])
                self.assertEqual(row['evidence']['table']['headers'],['等级','职业特性'])
            self.assertFalse(any(r['text'] in {'子职特性','隐藏招式'} for r in rows))
            from anna.catalog.audit import audit_concepts
            self.assertEqual(audit_concepts(db,root)['errors'],[])
            doc.close();db.close()

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
        from anna.paths import WEB
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
            from anna.paths import WEB
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
            from anna.catalog.audit import audit_concepts
            self.assertEqual(audit_concepts(db,root)['errors'],[])
            db.execute("UPDATE concept_observations SET target_id='a' WHERE status='review'")
            self.assertTrue(any('unresolved target' in e for e in audit_concepts(db,root)['errors']))
            (root/'concept_observations.jsonl').write_text('')
            self.assertIn('concept_observations.jsonl: IDs mismatch',audit_concepts(db,root)['errors'])
            db.close()
    def test_roles_evidence_unknown_format_and_navigation_outside_quick_directory(self):
        from anna.paths import WEB
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
        from anna.paths import WEB
        path='topics/玩家手册2024/装备/精通词条.htm'
        result=parse_one((str(WEB),path,[],path,NO_TOC_PLACE,False))
        self.assertEqual(next(c for c in result['candidates'] if c['name'].startswith('横扫'))['type'],'rule')
