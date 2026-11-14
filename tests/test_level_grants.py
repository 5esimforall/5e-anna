"""Independent table fixtures, acquisition conditions and source-bounded rejection."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from html_extract import Document
from table_extract import extract_tables, table_grid
from level_grants import parse_table_access, feature_access
from knowledge import Context, record_for, branches, replay, binding, chain, build_knowledge, casting_condition, audit_knowledge
from review_store import digest, canonical
from rule_conditions import condition_terms
from rule_model import rule_value

POLICY=('从星裔表格中选择其一。你获得该血系的1级好处。'
        '当你到达3级和5级时，你分别习得一道表格上更高级的法术；你时刻准备着这道习得的法术，且可以不消耗法术位施展此法术一次，当你完成一次长休时，你重获施展该道法术的能力。'
        '你也可以用任何你拥有的相应环阶法术位施展该道法术。当你选择血系时，选择智力、感知、或魅力之一，该属性即是你用此特质施展法术时的施法属性。')
LINEAGE=('<h1>星民 Starfolk</h1><p>'+POLICY+'</p><h3>星裔 Star Lineages</h3><table>'
         '<tr><td>血系</td><td>1级</td><td>3级</td><td>5级</td></tr>'
         '<tr><td>红支 Red</td><td>你的黑暗视觉提升至120尺。你习得戏法微光Little light。</td><td>光球Light Orb</td><td>夜幕Nightfall</td></tr>'
         '<tr><td>蓝支 Blue</td><td>你知晓戏法微光Little light。每当你完成长休时，你可以将它替换为法师法术列表中的另一个戏法。</td><td>夜幕Nightfall</td><td>光球Light Orb</td></tr></table>')
LIFE=('<h1>星辰领域 Star Domain</h1><h2>3级：星辰法术 Star Spells</h2>'
      '<p>你与此神圣领域的链接使你始终准备着特定的法术。当你到达星辰法术表中特定的牧师等级时，你就始终准备着表中对应的法术。</p>'
      '<h3>星辰法术 Star Spells</h3><table><tr><td>牧师等级</td><td>准备法术</td></tr>'
      '<tr><td rowspan="2">3</td><td>微光Little light，光球Light Orb</td></tr><tr><td>夜幕Nightfall</td></tr>'
      '<tr><td>5</td><td>光球Light Orb</td></tr><tr><td>7</td><td>夜幕Nightfall</td></tr><tr><td>9</td><td>光球Light Orb</td></tr></table>')


def entry(eid,kind,path,text,start=0,title_end=None,end=None,**extra):
    heading_start=start
    while heading_start<len(text) and text[heading_start].isspace():heading_start+=1
    name=text[heading_start:text.find('\n',heading_start)].strip()
    return {'id':eid,'entity_id':eid,'name':name,'name_zh':name,'name_en':'','type':kind,'ruleset':'5r',
            'page_path':path,'start':start,'title_end':title_end if title_end is not None else text.find('\n',heading_start)+1,
            'end':end if end is not None else len(text),'source_ids':['core'],'fields':{},'is_primary':True,**extra}


def context(kind='lineage',html=None,owner=True):
    db=sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE entries(id TEXT PRIMARY KEY,name TEXT,type TEXT,ruleset TEXT,page_path TEXT,start INTEGER,end INTEGER,data TEXT);
        CREATE TABLE pages(path TEXT PRIMARY KEY,text TEXT); CREATE TABLE sources(id TEXT,data TEXT);
        CREATE TABLE raw_tables(data TEXT); CREATE TABLE relations(subject_id TEXT,predicate TEXT,object_id TEXT,evidence TEXT);
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE review_queue(id TEXT,target TEXT,reason TEXT,priority TEXT,data TEXT);''')
    source={'id':'core','name':'核心','ruleset_hint':'5r','reference_role':'core_rules'}
    db.execute('INSERT INTO sources VALUES(?,?)',('core',canonical(source)))
    path='fixture.htm';doc=Document(path,html or (LINEAGE if kind=='lineage' else LIFE));text=doc.text
    _,tables,_=extract_tables(doc,[]);db.execute('INSERT INTO pages VALUES(?,?)',(path,text))
    for table in tables:db.execute('INSERT INTO raw_tables VALUES(?)',(canonical(table),))
    if kind=='lineage':
        root=entry('root','species',path,text);subject=root;caption_start=text.index('星裔 Star Lineages')
    else:
        root=entry('root','subclass',path,text)
        start=text.rfind('\n',0,text.index('：星辰法术'))+1;subject=entry('feature','class_feature',path,text,start,parent_id='root',is_primary=False)
        caption_start=text.index('星辰法术 Star Spells',subject['title_end'])
    caption=entry('caption','lore',path,text,caption_start,end=tables[0]['end'],
        name_zh='星裔' if kind=='lineage' else '星辰法术',is_primary=False,table_span=[tables[0]['start'],tables[0]['end']])
    entries=[root,caption]+([subject] if subject is not root else [])
    if kind=='life':
        db.execute('INSERT INTO pages VALUES(?,?)',('class.htm','牧师 Cleric\n职业说明'))
        entries.append(entry('cleric','class','class.htm','牧师 Cleric\n职业说明',name_zh='牧师'))
        if owner:
            for e in [root,subject]:db.execute('INSERT INTO relations VALUES(?,?,?,?)',(e['id'],'belongs_to_class','cleric','{"path":"toc.htm","node_id":"1"}'))
    for eid,zh,en,level in [('light','微光','Little Lights',0),('orb','光球','Light Orb',1),('night','夜幕','Nightfall',2)]:
        body=zh+' '+en+'\n施法时间：动作\n施法距离：自身\n法术成分：V\n持续时间：立即\n说明。'
        db.execute('INSERT INTO pages VALUES(?,?)',(eid+'.htm',body))
        entries.append(entry(eid,'spell',eid+'.htm',body,name_zh=zh,name_en=en,fields={'spell_level':str(level),'spell_classes':['法师']}))
    for e in entries:db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?)',tuple(e[k] for k in ('id','name','type','ruleset','page_path','start','end'))+(canonical(e),))
    doc.close();return db,Context(db),subject


class LevelGrantTests(unittest.TestCase):
    def setUp(self):self.dbs=[]
    def tearDown(self):
        for db in self.dbs:db.close()
    def fixture(self,*args,**kwargs):
        db,ctx,subject=context(*args,**kwargs);self.dbs.append(db);return db,ctx,subject
    def record(self,ctx,subject):return record_for(subject,'grant',parse_table_access(subject,ctx),ctx)

    def test_renamed_lineage_retains_choices_levels_and_separate_usage(self):
        _,ctx,e=self.fixture();r=self.record(ctx,e)
        self.assertEqual(r['status'],'resolved');self.assertEqual(len(r['value']['grants']),6)
        options=list(branches(ctx,[r]));self.assertEqual(len(options),10)
        for option in options:
            terms=[t for c in option['conditions'] for t in condition_terms(c['expression'])]
            self.assertEqual(len([t for t in terms if t.get('kind')=='option_selected']),1)
            level=next(t['minimum'] for t in terms if t.get('kind')=='level')
            self.assertEqual(option['mode'],'known' if level==1 else 'prepared')
            if option['resource']=='slot_free':
                self.assertIn(level,[3,5]);self.assertEqual(option['branch']['uses']['per'],'spell');self.assertEqual(option['recovery'],'long_rest')
            elif option['resource']=='spell_slot':self.assertEqual(option['recovery'],'not_stated')
        target=r['value']['grants'][0]['target']
        self.assertEqual(target['lookup_names'],['微光','Little light']);self.assertEqual(target['resolution']['targets'],['light'])
        self.assertEqual(target['resolution']['resolution_evidence']['lookups'][1]['resolution']['status'],'unresolved')
        modifier=r['value']['modifiers'][0]
        self.assertEqual(modifier['trigger'],'long_rest');self.assertEqual(modifier['grant_id'],'grant_2_1')
        self.assertEqual(modifier['target']['options'],['light']);self.assertEqual(modifier['target']['constraints']['different_from'],'current_spell');self.assertEqual(len(r['value']['descriptions']),1)

    def test_named_trait_scope_does_not_skip_multiline_preamble(self):
        intro='星裔Star Lineage。你属于一支星辰血系，并因此获得了超自然能力。'
        _,ctx,e=self.fixture(html=LINEAGE.replace(POLICY,intro+'<br>'+POLICY))
        self.assertEqual(self.record(ctx,e)['status'],'resolved')
        _,ctx,e=self.fixture(html=LINEAGE.replace(POLICY,intro+'<br>只有夜间才能获得。<br>'+POLICY))
        r=self.record(ctx,e);self.assertEqual(r['value']['unparsed'][0]['reason'],'unparsed_table_prefix');self.assertFalse(list(branches(ctx,[r])))

    def test_rowspan_keeps_physical_evidence_and_class_levels(self):
        _,ctx,e=self.fixture('life');r=self.record(ctx,e);parent=record_for(ctx.entries['root'],'grant',feature_access(ctx.entries['root'],ctx),ctx)
        self.assertEqual(r['status'],'resolved');grants=r['value']['grants'];self.assertEqual([g['when']['minimum'] for g in grants],[3,3,3,5,7,9])
        self.assertEqual(grants[0]['evidence'][1],grants[2]['evidence'][1]);self.assertNotEqual(grants[2]['target']['evidence'],grants[1]['target']['evidence'])
        options=list(branches(ctx,[r,parent]));root=[o for o in options if o['root_id']=='root'];self.assertEqual(len(root),6)
        high=next(o for o in root if o['grant_id']=='grant_6')
        self.assertEqual([t['minimum'] for c in high['conditions'] for t in condition_terms(c['expression']) if t.get('kind')=='class_level'],[3,3,9])
        self.assertEqual(high['acquisition_steps'][0]['target_id'],'feature');self.assertEqual(high['resource'],'not_stated')
        self.assertEqual(high['condition_kinds'],['class_level'])

    def test_table_rejections_block_projection(self):
        variants=[('换行前置',LINEAGE.replace('从星裔','只有夜间才能获得。<br>从星裔')),('表外前句',LINEAGE.replace('从星裔','仅在夜间，才可以从星裔')),('表外尾句',LINEAGE+'<p>仅在夜间才能使用。</p>'),('多选',LINEAGE.replace('选择其一','选择其中两支')),
                  ('未知限定',LINEAGE.replace('你也可以用','只有在夜间，你也可以用')),
                  ('表头',LINEAGE.replace('<td>5级</td>','<td>环阶</td>')),
                  ('跨行',LINEAGE.replace('<td>红支 Red</td>','<td rowspan="3">红支 Red</td>')),
                  ('缺列',LINEAGE.replace('<td>夜幕Nightfall</td>','',1)),
                  ('不完整法术',LINEAGE.replace('光球Light Orb</td>','光球Light Orb（仅夜间）</td>',1)),
                  ('未知低级条件',LINEAGE.replace('你习得戏法','如果你独处，你习得戏法',1))]
        for label,html in variants:
            with self.subTest(label=label):
                _,ctx,e=self.fixture(html=html);r=self.record(ctx,e);self.assertEqual(r['status'],'partial');self.assertTrue(r['value']['unparsed']);self.assertFalse(list(branches(ctx,[r])))

    def test_missing_competing_table_or_caption_is_not_nearest_match(self):
        for change in ('missing','duplicate','caption'):
            with self.subTest(change=change):
                _,ctx,e=self.fixture()
                if change=='missing':ctx.tables=[]
                elif change=='duplicate':ctx.tables.append(deepcopy(ctx.tables[0]))
                else:ctx.entries['caption']['name_zh']='相近名称'
                r=self.record(ctx,e);self.assertEqual(r['value']['unparsed'][0]['reason'],'missing_or_competing_grant_table');self.assertFalse(list(branches(ctx,[r])))

    def test_bilingual_conflict_and_missing_name_remain_inspectable(self):
        _,ctx,e=self.fixture()
        original=ctx.resolve
        def conflict(entry,name,kind):
            return {'status':'resolved','targets':['night']} if name=='Little light' else original(entry,name,kind)
        ctx.resolve=conflict;r=self.record(ctx,e)
        self.assertEqual(r['status'],'partial');self.assertEqual(r['value']['grants'][0]['target']['resolution']['status'],'ambiguous');self.assertFalse(list(branches(ctx,[r])))
        _,ctx,e=self.fixture(html=LINEAGE.replace('光球Light Orb','未知Unknown'))
        r=self.record(ctx,e);self.assertEqual(r['status'],'partial');self.assertFalse(list(branches(ctx,[r])))

    def test_class_owner_and_level_boundaries(self):
        for html,owner,reason in [(LIFE,False,'unresolved_level_owner'),(LIFE.replace('3级：','等级：'),True,'unresolved_level_owner'),
            (LIFE.replace('<td>9</td>','<td>2</td>'),True,'invalid_grant_level'),
            (LIFE.replace('特定的牧师等级','特定的法师等级'),True,'unresolved_level_owner')]:
            with self.subTest(reason=reason):
                _,ctx,e=self.fixture('life',html,owner);r=self.record(ctx,e)
                self.assertIn(reason,[g['reason'] for g in r['value']['unparsed']]);self.assertFalse(list(branches(ctx,[r])))
        _,ctx,e=self.fixture('life');ctx.class_links.append({'subject_id':'feature','object_id':'missing','evidence':{}})
        self.assertEqual(self.record(ctx,e)['value']['unparsed'][0]['reason'],'unresolved_level_owner')
        _,ctx,e=self.fixture('life');ctx.entries['cleric']['ruleset']='5e'
        self.assertEqual(self.record(ctx,e)['value']['unparsed'][0]['reason'],'unresolved_level_owner')

    def test_grid_rejects_malformed_geometry(self):
        _,ctx,_=self.fixture('life');table=ctx.tables[0];grid=table_grid(table)
        self.assertIs(grid[1][0],grid[2][0]);self.assertEqual(grid[1][0]['row'],1)
        for change in ('zero','negative','nonnumeric','missing','overlap','wide'):
            row=deepcopy(table)
            if change=='missing':row['rows'][1].pop('cell_spans')
            elif change=='overlap':row['rows'][0]['rowspan'][1]='2';row['rows'][1]['colspan'][0]='2'
            else:row['rows'][1]['rowspan'][0]={'zero':'0','negative':'-1','nonnumeric':'x','wide':'999'}[change]
            with self.subTest(change=change),self.assertRaises(ValueError):table_grid(row)

    def test_review_replay_changes_in_policy_geometry_target_and_owner(self):
        _,ctx,e=self.fixture('life');r=self.record(ctx,e)
        event={'id':'decision','operation':'knowledge_accept','binding':binding(r),'basis':ctx.basis(r),'basis_sha256':digest(ctx.basis(r)),'supersedes':None}
        effective,decisions=replay([r],[event],ctx);self.assertEqual(effective[0]['review_status'],'applied')
        for mutation in ('owner','geometry','target','policy'):
            _,other,subject=self.fixture('life');new=self.record(other,subject)
            if mutation=='owner':next(r for r in other.class_links if r['subject_id']=='root')['evidence']={'path':'changed.htm'}
            elif mutation=='geometry':other.tables[0]['rows'][1]['rowspan'][0]='1';new=self.record(other,subject)
            elif mutation=='target':other.entries['orb']['name_en']='Changed'
            else:other.pages[subject['page_path']]=other.pages[subject['page_path']].replace('始终','偶尔',1);new=self.record(other,subject)
            effective,decisions=replay([new],[event],other)
            with self.subTest(mutation=mutation):self.assertEqual(effective[0]['review_status'],'stale');self.assertFalse(list(branches(other,effective)))
        rejected={**event,'operation':'knowledge_reject'};effective,_=replay([r],[rejected],ctx);self.assertFalse(list(branches(ctx,effective)))

    def test_query_export_and_reading_use_same_inherited_conditions(self):
        db,ctx,e=self.fixture('life')
        with tempfile.TemporaryDirectory() as folder:
            build_knowledge(db,folder,Path(folder)/'reviews.jsonl')
            result=chain(db,'root','night');self.assertEqual(result['reading']['path'],['root','feature','night'])
            self.assertTrue(result['bindings']);self.assertEqual(result['bindings'][0]['conditions'][0]['scope']['subject_id'],'root')
            reverse=chain(db,'night');self.assertTrue(any(edge['relation']=='provided_by' and edge['target_id']=='root' for edge in reverse['edges']))
            where,values=casting_condition({'spell':'夜幕','access_condition':'class_level','mode':'prepared'})
            self.assertEqual({r[0] for r in db.execute('SELECT e.id FROM entries e WHERE '+where,values)},{'root','feature'})
            where,values=casting_condition({'spell':'夜幕','resource':'slot_free'})
            self.assertFalse(db.execute('SELECT e.id FROM entries e WHERE '+where,values).fetchall())
            exported=[json.loads(line) for line in (Path(folder)/'effective_knowledge.jsonl').read_text().splitlines()]
            self.assertEqual({r['id'] for r in exported if r['kind']=='grant'},{r['id'] for r in result['records'] if r['kind']=='grant'})
            # Independent JSON consumer: no parser functions establish these row thresholds.
            grant=next(r for r in exported if r['subject_id']=='feature' and r['kind']=='grant')
            self.assertEqual([g['when']['minimum'] for g in grant['value']['grants']],[3,3,3,5,7,9])
            self.assertEqual(audit_knowledge(db,folder)['errors'],[])

    def test_orphan_local_choice_rejected(self):
        _,ctx,e=self.fixture();value=parse_table_access(e,ctx);value['grants'][0]['when']['all'][0]['option_id']='missing'
        with self.assertRaisesRegex(ValueError,'local grant choice'):record_for(e,'grant',value,ctx)
        _,ctx,e=self.fixture('life');value=feature_access(ctx.entries['root'],ctx);value['grants'][0]['target']['resolution']['targets']=['night']
        with self.assertRaisesRegex(ValueError,'wrong definition type'):record_for(ctx.entries['root'],'grant',value,ctx)


class AcquisitionPathTests(unittest.TestCase):
    setUp=LevelGrantTests.setUp
    tearDown=LevelGrantTests.tearDown
    fixture=LevelGrantTests.fixture
    record=LevelGrantTests.record
    # Separate routes may reach the same provider; their conditions and domains
    # are not a dictionary to overwrite or a bag to combine across branches.
    def test_multihop_paths_keep_ancestor_conditions_and_distinct_identity(self):
        _,ctx,e=self.fixture();provider=self.record(ctx,e);provider['subject_id']='provider';provider['id']='provider_record'
        ctx.entries['provider']={**e,'id':'provider','type':'feat'}
        ev=provider['evidence']
        def gain(gid,target,when=None,constraints=None):
            return {'id':gid,'entry_type':'feat','mode':'gain','constraints':constraints or {},'when':when,'branches':[],
                'target':{'kind':'entry','name':target,'resolution':{'status':'resolved','targets':[target]},'evidence':ev},'evidence':[ev]}
        def bundle(eid,grants,minimum):
            ctx.entries[eid]={**e,'id':eid,'type':'feat'}
            return record_for(ctx.entries[eid],'grant',rule_value(grants=grants,
                prerequisites={'status':'parsed','expression':{'kind':'level','minimum':minimum},'evidence':ev},
                activation={'expression':{'kind':'campaign','name':eid},'evidence':ev}),ctx)
        left=bundle('left',[gain('to_provider','provider')],4)
        right=bundle('right',[gain('to_provider','provider')],8)
        root=bundle('root',[gain('left_route','left',{'kind':'item_state','subject':'source_item','state':'held'}),
                            gain('right_route','right',{'kind':'item_state','subject':'source_item','state':'worn'})],2)
        options=[o for o in branches(ctx,[root,left,right,provider]) if o['root_id']=='root']
        self.assertEqual(len(options),20);self.assertEqual(len({o['id'] for o in options}),20)
        for o in options:
            middle=o['acquisition_steps'][0]['target_id'];self.assertEqual(o['acquisition_steps'][1]['subject_id'],middle)
            terms=[t for c in o['conditions'] for t in condition_terms(c['expression'])]
            self.assertEqual({t['state'] for t in terms if t.get('kind')=='item_state'},{'held' if middle=='left' else 'worn'})
            self.assertTrue({2,4 if middle=='left' else 8}<={t['minimum'] for t in terms if t.get('kind')=='level'})
        left['status']='rejected'
        self.assertEqual(len([o for o in branches(ctx,[root,left,right,provider]) if o['root_id']=='root']),10)
        right['value']['grants'].append(gain('cycle','root'))
        self.assertEqual(len([o for o in branches(ctx,[root,left,right,provider]) if o['root_id']=='root']),10)

    def test_restrictions_intersect_instead_of_last_writer_wins(self):
        _,ctx,e=self.fixture();provider=self.record(ctx,e);provider['subject_id']='provider';provider['id']='provider_record';ctx.entries['provider']={**e,'id':'provider','type':'feat'}
        ev=provider['evidence'];grant=provider['value']['grants'][0]
        grant['target']={'kind':'choice','count':1,'constraints':{'levels':[0],'class_list':{'values':['法师','牧师']}}}
        provider['value']['grants']=[grant]
        def parent(eid,target,classes):
            ctx.entries[eid]={**e,'id':eid,'type':'feat'}
            return record_for(ctx.entries[eid],'grant',rule_value(grants=[{'id':'gain','entry_type':'feat','mode':'gain','branches':[],
                'constraints':{'class_list':classes},'target':{'kind':'entry','name':target,'resolution':{'status':'resolved','targets':[target]},'evidence':ev},'evidence':[ev]}]),ctx)
        middle=parent('middle','provider',['法师']);root=parent('root','middle',['法师','牧师'])
        options=[o for o in branches(ctx,[root,middle,provider]) if o['root_id']=='root']
        self.assertEqual(len(options),1);self.assertEqual(options[0]['constraints']['class_list'],['法师'])
        root['value']['grants'][0]['constraints']['class_list']=['牧师']
        self.assertFalse([o for o in branches(ctx,[root,middle,provider]) if o['root_id']=='root'])


@unittest.skipUnless((Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite').exists(),'Build the dataset first')
class LevelGrantCorpusTests(unittest.TestCase):
    def setUp(self):self.db=sqlite3.connect(Path(__file__).resolve().parents[1]/'data/extracted/catalog.sqlite')
    def tearDown(self):self.db.close()
    def entry(self,name,kind,ruleset='5r'):
        rows=[json.loads(r[0]) for r in self.db.execute('SELECT data FROM entries WHERE type=? AND ruleset=?',(kind,ruleset))]
        return next(e for e in rows if e['name_zh']==name and '玩家手册' in e['page_path'] and e.get('is_primary')==(kind in {'species','subclass'}))
    def test_original_elf_table_matches_independent_expectations(self):
        e=self.entry('精灵','species');result=chain(self.db,e['id']);r=next(r for r in result['records'] if r['subject_id']==e['id'])
        self.assertEqual(r['status'],'resolved');self.assertEqual(len(result['options']),15)
        grants=r['value']['grants'];self.assertEqual([g['target']['lookup_names'][0] for g in grants],['舞光术','妖火','黑暗术','魔法伎俩','侦测魔法','迷踪步','德鲁伊伎俩','大步奔行','行动无踪'])
        self.assertEqual([g['when']['all'][1]['minimum'] for g in grants],[1,3,5]*3)
        self.assertEqual(grants[0]['target']['lookup_names'][1],'Dancing light')
        self.assertTrue(all(g['ability']['values']==['智力','感知','魅力'] for g in grants))
        old=self.entry('精灵','species','5e');self.assertFalse(chain(self.db,old['id'])['options'])
    def test_original_life_domain_preserves_subclass_feature_spell_path(self):
        e=self.entry('生命领域','subclass');result=chain(self.db,e['id']);self.assertEqual(len(result['options']),10)
        options=result['options'];provider=options[0]['subject_id'];r=next(r for r in result['records'] if r['subject_id']==provider and r['kind']=='grant')
        self.assertEqual([g['when']['minimum'] for g in r['value']['grants']],[3]*4+[5]*2+[7]*2+[9]*2)
        self.assertEqual([g['target']['lookup_names'][0] for g in r['value']['grants']],['援助术','祝福术','疗伤术','次等复原术','群体治愈真言','回生术','生命灵光','防死结界','高等复原术','群体疗伤术'])
        high=next(o for o in options if o['grant_id']=='grant_10');reading=chain(self.db,e['id'],high['spell_ids'][0])
        self.assertEqual(reading['reading']['path'],[e['id'],provider,high['spell_ids'][0]])
        self.assertEqual({t['minimum'] for c in high['conditions'] for t in condition_terms(c['expression']) if t.get('kind')=='class_level'},{3,9})
        self.assertTrue(all(o['resource']=='not_stated' and o['recovery']=='not_stated' for o in options))
