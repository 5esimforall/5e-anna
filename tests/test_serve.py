"""Read-only API regression tests against the generated corpus."""
import csv
import io
import json
import sqlite3
import struct
import sys
import threading
import unittest
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from serve import DB, Handler, ThreadingHTTPServer, connect, query, detail, meta, csv_export, facets
import browse

@unittest.skipUnless(DB.exists(),'Build the dataset first')
class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):self.db=connect()
    def tearDown(self):self.db.close()
    def request(self,path,headers=None):
        c=HTTPConnection('127.0.0.1',self.server.server_port)
        c.request('GET',path,headers=headers or {});r=c.getresponse();body=r.read();status=r.status;kind=r.getheader('Content-Type');c.close();return status,kind,body
    def test_readonly(self):
        with self.assertRaises(sqlite3.OperationalError):self.db.execute('DELETE FROM entries')
    def test_defaults_and_pages(self):
        r=query(self.db,{})
        # Rows stack same-name versions; every non-section version is still counted.
        self.assertEqual(r['versions_total'],self.db.execute("SELECT count(*) FROM entries WHERE type!='section'").fetchone()[0])
        self.assertLess(r['total'],r['versions_total'])
        a={e['id'] for e in r['rows']};b={e['id'] for e in query(self.db,{'offset':['150']})['rows']}
        self.assertEqual(len(a),150);self.assertEqual(len(b),150);self.assertFalse(a&b)
        # A restore may fetch several batches at once; past the end there are simply no rows.
        restored=query(self.db,{'limit':['300']})['rows'];self.assertEqual({e['id'] for e in restored},a|b)
        self.assertEqual(query(self.db,{'offset':['999999999']})['rows'],[])
    def test_phb_feats_and_intersection(self):
        sid=self.db.execute("select id from sources where name='玩家手册2024'").fetchone()[0]
        p={'type':['feat'],'source':[sid],'ruleset':['5r']}
        rows=query(self.db,p,export=True)['rows']
        self.assertTrue(rows)
        self.assertTrue(all(r['type']=='feat' and r['ruleset']=='5r' and sid in r['source_ids'] for r in rows))
        p['ruleset']=['5e'];self.assertEqual(query(self.db,p)['total'],0)
    def test_name_literal_and_case(self):
        self.assertEqual(query(self.db,{'q':["%' OR 1=1 --"]})['total'],0)
        self.assertEqual(query(self.db,{'q':['alert']})['total'],query(self.db,{'q':['ALERT']})['total'])
    def test_fields_and_conflicts(self):
        rows=query(self.db,{'type':['spell'],'field':['school'],'value':['塑能']})['rows']
        self.assertTrue(rows);self.assertTrue(all('塑能' in str(r['fields']['school']) for r in rows))
        rows=query(self.db,{'conflict':['1']})['rows'];self.assertTrue(rows);self.assertTrue(all(r['field_conflicts'] for r in rows))
    def test_detail_evidence(self):
        eid=query(self.db,{'q':['Medium Armor Master'],'ruleset':['5r']})['rows'][0]['id']
        d=detail(self.db,eid);self.assertTrue(d['body']);self.assertTrue(d['facts'])
        for f in d['facts']:
            if 'excerpt' in f:
                ev=f['evidence'];a,b=ev['span'];text=self.db.execute('select text from pages where path=?',(ev['path'],)).fetchone()[0]
                self.assertEqual(f['excerpt'],text[a:min(b,a+1200)])
    def test_concept_observations_are_scoped_and_keep_evidence(self):
        row=self.db.execute("SELECT e.id FROM entries e JOIN concept_observations m ON m.page_path=e.page_path AND m.start>=e.start AND m.end<=e.end WHERE e.type='spell' AND m.signal='format' AND m.status='resolved' LIMIT 1").fetchone()
        self.assertIsNotNone(row);d=detail(self.db,row[0]);self.assertTrue(d['concept_observations'])
        for m in d['concept_observations']:
            self.assertEqual(m['path'],d['page_path'])
            self.assertTrue(d['start']<=m['start']<=m['end']<=d['end'])
            if m['signal']=='format' and m['status']=='resolved':
                self.assertEqual(len(m['evidence']['stylesheet_sha256']),64)
                self.assertEqual(m['candidates'],[m['target_id']])
            if not m['target_id']:
                self.assertEqual([c['id'] for c in m['candidate_entries']],m['candidates'])
                self.assertTrue(all(c['name'] for c in m['candidate_entries']))
    def test_table_grant_query_facets_exclusion_and_inherited_export(self):
        condition={'spell':'迷踪步','mode':'prepared','resource':'slot_free','recovery':'long_rest','access_condition':'option_selected'}
        params={'casting':[json.dumps(condition)],'versions':['each']}
        rows=query(self.db,params)['rows'];elf=next(e for e in rows if e['name_zh']=='精灵' and e['ruleset']=='5r')
        status,_,raw=self.request('/api/casting-facets?'+urlencode({'casting':json.dumps(condition)}))
        self.assertEqual(status,200);groups=json.loads(raw)['groups']
        self.assertEqual(next(v['count'] for v in groups['access_condition'] if v['value']=='option_selected'),len(rows))
        excluded=query(self.db,{'q':['精灵'],'not_casting':[json.dumps(condition)],'versions':['each']})
        self.assertNotIn(elf['id'],{e['id'] for e in excluded['rows']})
        wrong={**condition,'resource':'spell_slot'};self.assertFalse(query(self.db,{'casting':[json.dumps(wrong)]})['rows'])
        domain=self.db.execute("SELECT id FROM entries WHERE name_en='Life Domain' AND type='subclass' AND ruleset='5r'").fetchone()[0]
        spell=self.db.execute("SELECT id FROM entries WHERE name_en='Mass Cure Wounds' AND type='spell' AND ruleset='5r' AND page_path LIKE '%玩家手册2024%'").fetchone()[0]
        args={'id':domain,'spell':spell};status,_,raw=self.request('/api/knowledge-chain?'+urlencode(args));self.assertEqual(status,200)
        value=json.loads(raw);self.assertEqual(len(value['reading']['path']),3)
        self.assertEqual(value['reading']['path'][0],domain);self.assertEqual(value['reading']['path'][-1],spell)
        self.assertEqual(value['bindings'][0]['conditions'][0]['scope']['subject_id'],domain)
        status,_,raw=self.request('/api/knowledge-export?'+urlencode(args));self.assertEqual(status,200);self.assertEqual(json.loads(raw)['chain'],value)
        status,_,raw=self.request('/api/entry?'+urlencode({'id':domain,'chain_spell':spell}));self.assertEqual(status,200);self.assertEqual(json.loads(raw)['rule_chain'],value)
        fake_path=json.dumps([domain,spell]);self.assertEqual(self.request('/api/knowledge-chain?'+urlencode({**args,'path':fake_path}))[0],400)

    def test_casting_chain_query_export_and_invalid_selection(self):
        condition=json.dumps({'mode':'prepared','resource':'slot_free','recovery':'long_rest','spell':'疗伤术'},ensure_ascii=False)
        rows=query(self.db,{'casting':[condition],'versions':['each']})['rows']
        self.assertTrue(any(r['name_zh']=='侍僧' for r in rows))
        eid=next(r['id'] for r in rows if r['name_zh']=='侍僧')
        status,_,raw=self.request('/api/knowledge-chain?'+urlencode({'id':eid}))
        self.assertEqual(status,200);chain=json.loads(raw)
        status,_,raw=self.request('/api/knowledge-export?'+urlencode({'id':eid}))
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['chain'],chain)
        self.assertEqual(self.request('/api/knowledge-chain?'+urlencode({'id':eid,'spell':'absent'}))[0],400)
        self.assertEqual(self.request('/api/knowledge-chain?id=absent')[0],404)
        provider=chain['options'][0]['subject_id'];path=json.dumps([eid,provider])
        status,_,raw=self.request('/api/knowledge-chain?'+urlencode({'id':eid,'path':path}))
        self.assertEqual(status,200);reading=json.loads(raw)
        self.assertEqual(reading['reading']['path'],[eid,provider])
        for endpoint,key in [('/api/knowledge-export','path'),('/api/entry','chain_path')]:
            status,_,raw=self.request(endpoint+'?'+urlencode({'id':eid,key:path}))
            self.assertEqual(status,200);self.assertEqual(json.loads(raw)['chain' if key=='path' else 'rule_chain'],reading)
            self.assertEqual(self.request(endpoint+'?'+urlencode({'id':eid,key:json.dumps([eid,'absent'])}))[0],400)
        for invalid_path in [json.dumps([eid,eid]),'{',json.dumps([eid,4])]:
            self.assertEqual(self.request('/api/knowledge-chain?'+urlencode({'id':eid,'path':invalid_path}))[0],400)
        self.assertEqual(self.request('/api/entries?'+urlencode({'casting':'{"mode":"bad"}'}))[0],400)
        status,_,raw=self.request('/api/casting-facets?'+urlencode({'casting':condition}))
        self.assertEqual(status,200)
        values={x['value']:x['count'] for x in json.loads(raw)['groups']['resource']}
        self.assertEqual(values['slot_free'],query(self.db,{'casting':[condition],'versions':['each']})['total'])
        self.assertEqual(values['spell_slot'],0)
        invalid=json.dumps({'resource':'spell_slot','recovery':'long_rest'})
        self.assertEqual(query(self.db,{'casting':[invalid]})['total'],0)

    def test_effect_filters_and_local_focus_share_the_export_contract(self):
        condition={'effect':'damage','damage_type':'lightning','delivery':'automatic','timing':'ongoing','resource':'slot_free','spell':'巫术箭'}
        rows=query(self.db,{'casting':[json.dumps(condition)],'versions':['each']})['rows']
        root=next(r['id'] for r in rows if r['name_zh']=='智者')
        self.assertFalse(any(r['name_zh']=='侍僧' for r in rows))
        wrong={**condition,'delivery':'attack_roll'}
        self.assertFalse(query(self.db,{'casting':[json.dumps(wrong)]})['rows'])
        status,_,raw=self.request('/api/casting-facets?'+urlencode({'casting':json.dumps(condition)}))
        self.assertEqual(status,200)
        counts={r['value']:r['count'] for r in json.loads(raw)['groups']['delivery']}
        self.assertEqual(counts['automatic'],len(rows));self.assertEqual(counts['attack_roll'],0)
        excluded=query(self.db,{'not_casting':[json.dumps(condition)],'versions':['each'],'limit':['1500']})
        self.assertFalse({r['id'] for r in rows}&{r['id'] for r in excluded['rows']})
        self.assertEqual(excluded['total']+len(rows),query(self.db,{'versions':['each']})['total'])
        spell=next(r['id'] for r in query(self.db,{'q':['巫术箭'],'versions':['each']})['rows'] if r['ruleset']=='5r')
        args={'id':root,'spell':spell,'effect':'effect_4'}
        status,_,raw=self.request('/api/knowledge-chain?'+urlencode(args));self.assertEqual(status,200)
        value=json.loads(raw);self.assertEqual(value['reading']['effect_id'],'effect_4')
        status,_,raw=self.request('/api/knowledge-export?'+urlencode(args));self.assertEqual(status,200);self.assertEqual(json.loads(raw)['chain'],value)
        status,_,raw=self.request('/api/entry?'+urlencode({'id':root,'chain_spell':spell,'chain_effect':'effect_4'}))
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['rule_chain'],value)
        for effect in ('missing','effect_0'):
            self.assertEqual(self.request('/api/knowledge-chain?'+urlencode({**args,'effect':effect}))[0],400)
        for field in ('effect','damage_type','delivery','timing','save_ability','trigger','end_trigger','access_condition','casting_target'):
            self.assertEqual(self.request('/api/entries?'+urlencode({'casting':json.dumps({field:'invalid'})}))[0],400)

    def test_scoped_acquisition_and_second_save_survive_public_consumers(self):
        query_args={'access_condition':'class_level','casting_target':'self','resource':'slot_free'}
        status,_,raw=self.request('/api/entries?'+urlencode({'casting':json.dumps(query_args),'versions':'each'}))
        self.assertEqual(status,200);rows=json.loads(raw)['rows']
        self.assertTrue(any(e['name_en']=='Otherworldly Leap' for e in rows))
        effect_query={'effect':'condition','save_ability':'感知','timing':'repeat','trigger':'save_result','spell':'睡眠术'}
        status,_,raw=self.request('/api/entries?'+urlencode({'casting':json.dumps(effect_query),'versions':'each'}))
        self.assertEqual(status,200);root=next(e['id'] for e in json.loads(raw)['rows'] if e['name_zh']=='智者')
        spell=next(e['id'] for e in query(self.db,{'q':['睡眠术'],'versions':['each']})['rows'] if e['ruleset']=='5r')
        args={'id':root,'spell':spell,'effect':'effect_5'}
        status,_,raw=self.request('/api/knowledge-chain?'+urlencode(args));self.assertEqual(status,200);chain=json.loads(raw)
        effects=next(r['value']['effects'] for r in chain['records'] if r['kind']=='procedure' and r['subject_id']==spell)
        self.assertEqual(effects[4]['when']['effect_id'],'effect_4');self.assertEqual(effects[3]['repeat_of']['effect_id'],'effect_2')
        status,_,raw=self.request('/api/knowledge-export?'+urlencode(args));self.assertEqual(status,200);self.assertEqual(json.loads(raw)['chain'],chain)
        status,_,raw=self.request('/api/entry?'+urlencode({'id':root,'chain_spell':spell,'chain_effect':'effect_5'}))
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['rule_chain'],chain)
        status,_,raw=self.request('/api/casting-facets?'+urlencode({'casting':json.dumps(effect_query)}));self.assertEqual(status,200)
        counts={row['value']:row['count'] for row in json.loads(raw)['groups']['save_ability']}
        self.assertGreater(counts['感知'],0);self.assertEqual(counts['体质'],0)

    def test_cumulative_and_target_scope_filters_reach_real_spells_and_public_exports(self):
        condition={'query_subject':'spell','effect':'condition','cumulative_result':'failure','cumulative_count':3,
                   'save_ability':'体质','timing':'cumulative','target_scope':'single'}
        status,_,raw=self.request('/api/entries?'+urlencode({'casting':json.dumps(condition),'versions':'each'}))
        self.assertEqual(status,200);rows=json.loads(raw)['rows']
        stone=next(r for r in rows if r['name_en']=='Flesh to Stone' and r['ruleset']=='5r')
        self.assertFalse(any(r['name_en']=='Flesh to Stone' and r['ruleset']=='5e' for r in rows))
        excluded=query(self.db,{'not_casting':[json.dumps(condition)],'versions':['each']})
        self.assertEqual(excluded['total']+len(rows),query(self.db,{'versions':['each']})['total'])
        for field,value in [('cumulative_count',2),('cumulative_result','success'),('save_ability','感知'),('target_scope','area')]:
            query_params={'casting':[json.dumps({**condition,field:value})],'versions':['each']}
            self.assertNotIn(stone['id'],{e['id'] for e in query(self.db,query_params)['rows']})
        status,_,raw=self.request('/api/casting-facets?'+urlencode({'casting':json.dumps(condition)}))
        self.assertEqual(status,200);groups=json.loads(raw)['groups']
        counts={r['value']:r['count'] for r in groups['cumulative_result']}
        self.assertGreater(counts['failure'],0);self.assertEqual(counts['success'],0)
        self.assertTrue(all(r['count']==0 for r in groups['resource']))
        args={'id':stone['id'],'effect':'effect_9'}
        status,_,raw=self.request('/api/knowledge-chain?'+urlencode(args));self.assertEqual(status,200);chain=json.loads(raw)
        effects=next(r['value']['effects'] for r in chain['records'] if r['kind']=='procedure' and r['subject_id']==stone['id'])
        self.assertEqual(effects[8]['when']['effect_id'],'effect_7');self.assertEqual(effects[6]['check']['effect_id'],'effect_6')
        self.assertEqual(effects[5]['repeat_of']['effect_id'],'effect_2')
        self.assertEqual(effects[6]['initial_inclusion'],'not_stated')
        status,_,raw=self.request('/api/knowledge-export?'+urlencode(args));self.assertEqual(status,200);self.assertEqual(json.loads(raw)['chain'],chain)
        status,_,raw=self.request('/api/entry?'+urlencode({'id':stone['id'],'chain_effect':'effect_9'}));self.assertEqual(status,200);self.assertEqual(json.loads(raw)['rule_chain'],chain)
        for bad in [0,True,'3',1.5,None]:
            self.assertEqual(self.request('/api/entries?'+urlencode({'casting':json.dumps({**condition,'cumulative_count':bad})}))[0],400)
        for field in ['query_subject','target_scope','cumulative_result']:
            self.assertEqual(self.request('/api/entries?'+urlencode({'casting':json.dumps({field:'invalid'})}))[0],400)
        self.assertEqual(self.request('/api/entries?'+urlencode({'casting':json.dumps({**condition,'resource':'slot_free'})}))[0],400)

    def test_area_save_query_does_not_borrow_single_target_attack(self):
        condition={'spell':'冰刃','effect':'damage','damage_type':'cold','target_scope':'area','delivery':'saving_throw','resource':'slot_free'}
        rows=query(self.db,{'casting':[json.dumps(condition)],'versions':['each']})['rows']
        self.assertTrue(any(r['name_zh']=='智者' for r in rows))
        for change in [{'delivery':'attack_roll'},{'target_scope':'single'},{'damage_type':'piercing'}]:
            self.assertFalse(query(self.db,{'casting':[json.dumps({**condition,**change})]})['rows'])
        status,_,raw=self.request('/api/casting-facets?'+urlencode({'casting':json.dumps(condition)}))
        self.assertEqual(status,200)
        subject_counts={r['value']:r['count'] for r in json.loads(raw)['groups']['query_subject']}
        switched={k:v for k,v in condition.items() if k!='resource'}|{'query_subject':'spell'}
        self.assertEqual(subject_counts['spell'],query(self.db,{'casting':[json.dumps(switched)],'versions':['each']})['total'])
        self.assertGreater(subject_counts['spell'],0)

    def test_invalid_query(self):
        for p in [{'type':['entries; DROP TABLE entries']},{'type':['spell','section']},{'field':['bogus'],'value':['x']},{'sort':['invalid']},{'offset':['nan']},{'offset':['-1']},{'limit':['0']},{'limit':['1501']}]:
            with self.assertRaises(ValueError):query(self.db,p)
        with self.assertRaises(LookupError):detail(self.db,'absent')

    def test_common_rule_queries_read_nonspell_procedures_and_export_the_same_graph(self):
        condition={'query_subject':'entry','effect':'healing','use_action':'bonus_action'}
        status,_,raw=self.request('/api/entries?'+urlencode({'casting':json.dumps(condition),'versions':'each'}))
        self.assertEqual(status,200);rows=json.loads(raw)['rows']
        wind=next(e for e in rows if e['id']=='ver_0fd1de793ebd67111e8e6e30')
        all_healing=query(self.db,{'casting':[json.dumps({'query_subject':'entry','effect':'healing'})]},export=True)['rows']
        self.assertIn(wind['id'],{e['id'] for e in all_healing})
        self.assertTrue(any(e['type']=='spell' for e in all_healing))
        self.assertNotIn('ver_958337258cbdf07fb3fe6c4f',{e['id'] for e in rows})
        for value in ['reaction','action']:
            self.assertNotIn(wind['id'],{e['id'] for e in query(self.db,{'casting':[json.dumps({**condition,'use_action':value})]},export=True)['rows']})
        status,_,raw=self.request('/api/casting-facets?'+urlencode({'casting':json.dumps(condition)}))
        self.assertEqual(status,200)
        counts={r['value']:r['count'] for r in json.loads(raw)['groups']['use_action']}
        self.assertEqual(counts['bonus_action'],len(rows))
        excluded=query(self.db,{'not_casting':[json.dumps(condition)],'versions':['each']})
        self.assertEqual(excluded['total']+len(rows),query(self.db,{'versions':['each']})['total'])
        eid='ver_e03ee891694944041e9b1434';args={'id':eid,'effect':'effect_2'}
        status,_,raw=self.request('/api/knowledge-chain?'+urlencode(args));self.assertEqual(status,200);chain=json.loads(raw)
        self.assertEqual(chain['schema'],2);self.assertEqual(chain['options'],[])
        record=next(r for r in chain['records'] if r['kind']=='procedure')
        self.assertEqual(record['value']['effects'][1]['applies_to']['effect_id'],'effect_1')
        self.assertEqual(record['value']['effects'][1]['use']['action'],'reaction')
        self.assertNotIn('casting',record['value'])
        status,_,raw=self.request('/api/knowledge-export?'+urlencode(args));self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)['schema'],2);self.assertEqual(json.loads(raw)['chain'],chain)
        status,_,raw=self.request('/api/entry?'+urlencode({'id':eid,'chain_effect':'effect_2'}));self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)['rule_chain'],chain)
        for invalid in [{'query_subject':'entry','mode':'known'},{'query_subject':'entry','use_action':'swift'}]:
            self.assertEqual(self.request('/api/entries?'+urlencode({'casting':json.dumps(invalid)}))[0],400)
    def test_export_all_not_one_page(self):
        data=query(self.db,{'type':['feat']},export=True)
        output=csv_export(data['rows']).decode('utf-8-sig')
        self.assertEqual(len(list(csv.reader(io.StringIO(output))))-1,data['total'])
        row=data['rows'][0].copy();row['name_en']='=1+1'
        self.assertIn("'=1+1",csv_export([row]).decode('utf-8-sig'))
    def test_http_and_host_guard(self):
        self.assertEqual(self.request('/')[0],200)
        status,_,body=self.request('/api/meta')
        self.assertEqual(status,200)
        # Running alone, the query page has no reader to share a workspace with.
        self.assertEqual({k:json.loads(body)[k] for k in ('reader_url','snapshot')},{'reader_url':None,'snapshot':None})
        self.assertEqual(self.request('/api/entries?type=invalid')[0],400)
        self.assertEqual(self.request('/api/facets?type=invalid')[0],400)
        self.assertEqual(self.request('/api/export?type=invalid')[0],400)
        self.assertEqual(self.request('/api/entries',{'Host':'attacker.example'})[0],403)
        self.assertEqual(self.request('/../scripts/serve.py')[0],404)
        self.assertEqual(self.request('/source?path=/etc/passwd')[0],404)
    def check_brand_png(self,path,size):
        status,kind,body=self.request(path)
        self.assertEqual((status,kind),(200,'image/png'))
        self.assertEqual(body[:8],b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II',body[16:24]),(size,size))
        self.assertEqual(body[25],6)  # RGBA; preserve transparent edges.
        return body
    def check_brand_ico(self,path):
        status,kind,body=self.request(path)
        self.assertEqual((status,kind),(200,'image/x-icon'))
        self.assertEqual(struct.unpack('<HHH',body[:6]),(0,1,6))
        sizes=set()
        for index in range(6):
            width,height,_,_,_,_,length,offset=struct.unpack_from('<BBBBHHII',body,6+index*16)
            sizes.add((width or 256,height or 256))
            self.assertGreater(length,0)
            self.assertGreaterEqual(offset,102)
            self.assertLessEqual(offset+length,len(body))
        self.assertEqual(sizes,{(size,size) for size in (16,32,48,64,128,256)})
        return body
    def test_brand_assets(self):
        page=self.request('/')[2]
        self.assertIn(b'<title>5e Anna</title>',page)
        # Both pages name themselves; the shared topbar script draws the brand from that name.
        self.assertIn(b'<header class="topbar" data-page="query">',page)
        self.assertIn(b'<script src="/topbar.js"></script>',page)
        topbar=self.request('/topbar.js')[2]
        self.assertIn(b"query:{home:'/', logo:'/anna-logo.png'}",topbar)
        self.assertIn(b"book:{home:'/read', logo:'/stella-logo.png'}",topbar)
        self.assertIn(b'<title>5e Anna</title>',self.request('/review')[2])
        self.assertIn(b'href="/favicon.ico"',page)
        for size in (16,32,48):
            path=f'/favicon-{size}.png'
            self.assertIn(f'href="{path}"'.encode(),page)
            self.check_brand_png(path,size)
        self.check_brand_ico('/favicon.ico')
        self.assertEqual(self.request('/favicon-24.png')[0],404)
        self.assertEqual(self.request('/assets/branding/anna.png')[0],404)
        self.assertEqual(self.request('/favicon.ico',{'Host':'attacker.example'})[0],403)
        self.assertNotIn(b'class="brand-icon"',page)
        self.check_brand_png('/anna-logo.png',128)
        self.assertEqual(self.request('/anna-logo.png',{'Host':'attacker.example'})[0],403)
    def test_reader_brand_assets(self):
        page=self.request('/read')[2]
        self.assertIn(b'<title>5e Stella</title>',page)
        self.assertIn(b'<header class="topbar book-topbar" data-page="book">',page)
        self.assertIn(b'<script src="/topbar.js"></script>',page)
        self.assertIn(b'href="/stella-favicon.ico"',page)
        self.assertNotIn(b'href="/favicon.ico"',page)
        self.assertNotIn(b'src="/anna-logo.png"',page)
        self.assertNotEqual(self.check_brand_png('/stella-logo.png',128),self.request('/anna-logo.png')[2])
        self.assertNotEqual(self.check_brand_ico('/stella-favicon.ico'),self.request('/favicon.ico')[2])
        for path in ('/stella-logo.png','/stella-favicon.ico'):
            self.assertEqual(self.request(path,{'Host':'attacker.example'})[0],403)
        self.assertEqual(self.request('/assets/branding/stella.png')[0],404)
    def test_source_highlights(self):
        e=query(self.db,{})['rows'][0]
        url='/source?'+urlencode({'path':e['page_path'],'start':e['start'],'end':e['end']})
        status,kind,body=self.request(url)
        self.assertEqual(status,200);self.assertIn(b'<mark id="evidence">',body)
        self.assertIn('text/html',kind)
        self.assertIn('<title>页面文本 · 5e Anna</title>'.encode(),body)
        self.assertIn(b'href="/favicon.ico"',body)
        self.assertLess(body.index(b'src="/theme.js"'),body.index(b'href="/style.css"'))
    def test_source_snapshot_guard(self):
        row=self.db.execute('SELECT path,sha256 FROM pages LIMIT 1').fetchone()
        url='/source?'+urlencode({'path':row[0],'sha256':row[1]})
        self.assertEqual(self.request(url)[0],200)
        status,_,body=self.request('/source?'+urlencode({'path':row[0],'sha256':'stale'}))
        self.assertEqual(status,409)
        self.assertEqual(json.loads(body)['error'],'source_snapshot_mismatch')
    def test_reader_modules_are_served_with_script_types(self):
        page=self.request('/read')[2]
        for name in ('model','flow'):
            path=f'/reader-{name}.js'
            status,kind,body=self.request(path)
            self.assertEqual((status,kind),(200,'text/javascript; charset=utf-8'))
            self.assertTrue(body)
            self.assertIn(path.encode(),page)
            self.assertEqual(self.request(path,{'Host':'attacker.example'})[0],403)
        self.assertEqual(self.request('/reader-unknown.js')[0],404)

    def test_theme_bootstrap(self):
        status,kind,body=self.request('/theme.js')
        self.assertEqual((status,kind),(200,'text/javascript; charset=utf-8'))
        self.assertIn(b'prefers-color-scheme',body)
        self.assertEqual(self.request('/theme.js',{'Host':'attacker.example'})[0],403)
        page=self.request('/')[2]
        self.assertLess(page.index(b'src="/theme.js"'),page.index(b'href="/style.css"'))
        # The shared topbar adds the theme button that theme.js reveals once it can switch.
        self.assertLess(page.index(b'src="/kit.js"'),page.index(b'src="/theme.js"'))
        topbar=self.request('/topbar.js')[2]
        self.assertIn(b"theme.id = 'theme'",topbar)
        self.assertIn(b"theme.className = 'icon-button theme-button'",topbar)
        self.assertNotIn(b'id="snapshot"',page)
        self.assertNotIn(b'class="theme-control"',page)
    def test_mount_and_animal_catalog_types(self):
        mounts=query(self.db,{'type':['creature'],'f_role':['坐骑'],'versions':['each']})
        self.assertFalse(any(e['name_en'] in {'Flying Snake','Wolf','Warhorse Skeleton'} for e in mounts['rows']))
        offers=query(self.db,{'type':['animal_offer'],'versions':['each']})
        self.assertTrue(all(e['type']=='animal_offer' for e in offers['rows']))
        horse=next(e for e in mounts['rows'] if e['name_en']=='Warhorse' and e['ruleset']=='5r')
        d=detail(self.db,horse['id'])
        self.assertTrue(any(r['predicate']=='catalog_offer' for r in d['relations']))
        data=self.request('/api/entries?'+urlencode({'type':'animal_offer'}))
        self.assertEqual(data[0],200)
        self.assertEqual(json.loads(data[2])['versions_total'],offers['total'])

    def test_presets_are_one_type_and_one_facet_value(self):
        # Each preset reproduces the catalog's SQL view of the same name through ordinary filters.
        for preset in browse.PRESETS:
            params={'type':[preset['type']],'f_'+preset['field']:[preset['value']],'versions':['each']}
            ids={e['id'] for e in query(self.db,params,export=True)['rows']}
            view={r[0] for r in self.db.execute('SELECT id FROM "'+preset['name']+'"')}
            self.assertEqual(ids,view,preset['name'])

    def test_search_terms_all_match_and_rank_by_the_latest(self):
        both=query(self.db,{'q':['火','球'],'versions':['each']},export=True)['rows']
        self.assertTrue(both);self.assertTrue(all('火' in r['name'] and '球' in r['name'] for r in both))
        one=query(self.db,{'q':['火'],'versions':['each']})['total']
        self.assertLess(len(both),one)
        # Repeated and blank terms add nothing; the latest term decides match order.
        self.assertEqual(query(self.db,{'q':['火','球','火',' ']})['total'],query(self.db,{'q':['火','球']})['total'])
        self.assertEqual(query(self.db,{'q':['球','火球术']})['rows'][0]['name_zh'],'火球术')
        # The term being typed filters like a kept one and decides match order; a repeat adds nothing.
        self.assertEqual(query(self.db,{'q':['火'],'typing':['球']})['total'],query(self.db,{'q':['火','球']})['total'])
        self.assertEqual(query(self.db,{'q':['球'],'typing':['火球术']})['rows'][0]['name_zh'],'火球术')
        self.assertNotIn('typing',facets(self.db,{'q':['火'],'typing':['火']})['without'])
        self.assertIn('typing',facets(self.db,{'type':['spell'],'typing':['成年红龙']})['without'])
        for bad in [{'q':[str(i) for i in range(21)]},{'q':['x'*201]},{'typing':['x'*201]}]:
            with self.assertRaises(ValueError):query(self.db,bad)

    def test_empty_name_search_offers_unclassified_matches(self):
        typed=query(self.db,{'q':['盾矮人'],'versions':['each']})
        self.assertEqual(typed['total'],0)
        sections=query(self.db,{'q':['盾矮人'],'sections':['1'],'versions':['each']})
        self.assertGreater(sections['total'],0)
        self.assertEqual(typed['section_matches'],sections['total'])
        self.assertTrue(all(r['type']=='section' for r in sections['rows']))
        # The offer disappears once sections are included, a typed result exists, or no name is searched.
        self.assertEqual(sections['section_matches'],0)
        self.assertEqual(query(self.db,{'q':['幸运符']})['section_matches'],0)
        self.assertEqual(query(self.db,{'body':['卓尔蘑菇扒']})['section_matches'],0)
        # It follows every condition, including a chosen type that a section cannot stand in for.
        self.assertEqual(query(self.db,{'type':['spell'],'q':['盾矮人']})['section_matches'],0)
        constrained=query(self.db,{'q':['盾矮人'],'ruleset':['5e'],'versions':['each']})
        self.assertEqual(constrained['section_matches'],1)
        self.assertEqual(self.request('/api/entries?'+urlencode({'q':'盾矮人'}))[0],200)

    def test_body_searches_read_the_entry_text_after_its_title(self):
        rows=query(self.db,{'type':['spell'],'body':['专注','火焰'],'versions':['each']},export=True)['rows']
        self.assertTrue(rows)
        for r in rows[:20]:
            text=detail(self.db,r['id'])['body']
            self.assertIn('专注',text);self.assertIn('火焰',text)
        # A title is not body text: a word only in a name does not match the body.
        title_only=self.db.execute("SELECT name_zh FROM entries WHERE type='spell' AND name_zh='亡者丧钟' LIMIT 1").fetchone()[0]
        named={r['id'] for r in query(self.db,{'type':['spell'],'q':[title_only],'versions':['each']},export=True)['rows']}
        bodied={r['id'] for r in query(self.db,{'type':['spell'],'body':[title_only],'versions':['each']},export=True)['rows']}
        self.assertTrue(named);self.assertFalse(named&bodied)
        data=facets(self.db,{'type':['spell'],'body':['专注','火焰']})
        self.assertEqual(data['total'],len(rows));self.assertIn('body0',data['without']);self.assertIn('body1',data['without'])
        for bad in [{'body':[str(i) for i in range(21)]},{'body':['x'*201]}]:
            with self.assertRaises(ValueError):query(self.db,bad)

    def test_excluded_conditions_keep_entries_without_their_values(self):
        total=lambda p:query(self.db,{**p,'versions':['each']})['total']
        spells=total({'type':['spell']})
        # Including and excluding the same values split the table; unextracted values are not excluded.
        for key,value in [('f_school','塑能'),('q','火'),('body','专注'),('ruleset','5r')]:
            self.assertEqual(total({'type':['spell'],key:[value]})+total({'type':['spell'],'not_'+key:[value]}),spells,key)
        unknown=total({'type':['spell'],'f_school':[browse.UNKNOWN]})
        self.assertEqual(total({'type':['spell'],'not_f_school':['塑能']}),spells-total({'type':['spell'],'f_school':['塑能']}))
        self.assertGreaterEqual(total({'type':['spell'],'not_f_school':['塑能']}),unknown)
        self.assertEqual(total({'not_type':['spell']}),total({})-spells)
        # A field can include and exclude at once; each list is its own condition.
        both=facets(self.db,{'type':['spell'],'f_school':['塑能','咒法'],'not_f_school':['咒法']})
        self.assertEqual(both['total'],total({'type':['spell'],'f_school':['塑能']}))
        self.assertEqual(both['without']['not_f_school'],total({'type':['spell'],'f_school':['咒法']}))
        # Counts set aside both lists of their own field; excluded values stay listed.
        school={o['value']:o['count'] for g in both['groups'] if g['key']=='f_school' for o in g['options']}
        self.assertEqual(school['咒法'],total({'type':['spell'],'f_school':['咒法']}))
        notq=facets(self.db,{'type':['spell'],'q':['火'],'not_q':['球']})
        self.assertEqual(notq['total'],total({'type':['spell'],'q':['火']})-total({'type':['spell'],'q':['火','球']}))
        self.assertIn('not_q0',notq['without'])
        for bad in [{'not_cr_min':['1']},{'not_typing':['x']},{'not_conflict':['1']},{'not_type':['section']}]:
            with self.assertRaises(ValueError):query(self.db,bad)

    def test_counts_say_what_choosing_each_option_would_match(self):
        params={'type':['spell'],'q':['火'],'f_school':['塑能']}
        data=facets(self.db,params)
        self.assertEqual(data['total'],query(self.db,{**params,'versions':['each']})['total'])
        self.assertEqual(data['scope_total'],query(self.db,{'type':['spell'],'versions':['each']})['total'])
        groups={g['key']:g for g in data['groups']}
        self.assertEqual(list(groups)[0],'type')
        # An option counts the matches with it chosen and every other condition kept.
        school={o['value']:o['count'] for o in groups['f_school']['options']}
        for value in ['塑能','咒法','防护']:
            self.assertEqual(school[value],query(self.db,{**params,'f_school':[value],'versions':['each']})['total'])
        # A type counts what switching to it matches: conditions that type cannot answer drop out.
        types={o['value']:o['count'] for o in groups['type']['options']}
        self.assertEqual(types['spell'],data['total'])
        self.assertEqual(types['magic_item'],query(self.db,{'type':['magic_item'],'q':['火'],'versions':['each']})['total'])
        self.assertNotIn('section',types)
        # Removing one condition: each search term and field is its own condition.
        self.assertEqual(data['without']['q0'],query(self.db,{'type':['spell'],'f_school':['塑能'],'versions':['each']})['total']-data['total'])
        self.assertEqual(data['without']['f_school'],query(self.db,{'type':['spell'],'q':['火'],'versions':['each']})['total']-data['total'])
        # A chosen value stays listed when nothing in scope holds it, so it can be unchosen.
        elsewhere=self.db.execute("SELECT es.source_id FROM entry_sources es JOIN entries e ON e.id=es.entry_id GROUP BY es.source_id HAVING sum(e.type='spell')=0 LIMIT 1").fetchone()[0]
        odd={g['key']:g for g in facets(self.db,{'type':['spell'],'source':[elsewhere]})['groups']}
        self.assertEqual([o['count'] for o in odd['source']['options'] if o['value']==elsewhere],[0])
        # Shared filters narrow every count.
        narrowed={o['value']:o['count'] for o in facets(self.db,{**params,'ruleset':['5r']})['groups'][0]['options']}
        self.assertTrue(all(narrowed[t]<=n for t,n in types.items()))
        self.assertEqual(self.request('/api/facets?'+urlencode({'type':'spell','q':'火'}))[0],200)

    def test_entry_layout_endpoint(self):
        eid=self.db.execute("SELECT id FROM entries WHERE type='spell' AND name_en='Etherealness' AND ruleset='5r'").fetchone()[0]
        status,kind,body=self.request('/api/entry-layout?'+urlencode({'id':eid}))
        self.assertEqual(status,200);self.assertIn('application/json',kind)
        self.assertIn(json.loads(body)['status'],{'ok','missing','stale'})
        self.assertEqual(self.request('/api/entry-layout?id=ver_missing')[0],404)
        self.assertEqual(self.request('/api/entry-layout?id='+eid,{'Host':'attacker.example'})[0],403)
        # Reader notes read normalized values from the entry itself.
        self.assertEqual(detail(self.db,eid)['quickref']['spell_level'],['7'])

    def test_environmental_hazards_are_queryable(self):
        data=query(self.db,{'type':['hazard'],'versions':['each']})
        self.assertTrue(all(r['type']=='hazard' for r in data['rows']))
        names={r['name_zh'] for r in data['rows']}
        # Printed labels, DMG 2024 severity bands and catalogs in both editions all reach the same type.
        for name in ['雪崩','奥法风暴','燃烧','流沙陷坑','薄冰','亵渎之地']:self.assertIn(name,names)
        self.assertNotIn('自然灾害',names);self.assertNotIn('魔法现象',names)
        self.assertIn('hazard',meta(self.db)['type_order'])
        expected={r[0] for r in self.db.execute('SELECT id FROM "危害" WHERE type=\'hazard\'')}
        self.assertEqual({r['id'] for r in query(self.db,{'type':['hazard']},export=True)['rows']},expected)
        self.assertEqual(self.request('/api/entries?type=hazard')[0],200)

    def test_abilities_and_skills_are_queryable(self):
        abilities=query(self.db,{'type':['ability']},export=True)['rows']
        self.assertTrue(abilities);self.assertTrue(all(r['type']=='ability' for r in abilities))
        each=query(self.db,{'type':['skill']},export=True)
        self.assertTrue(each['rows']);self.assertTrue({'5e','5r'}<={r['ruleset'] for r in each['rows']})
        skills=query(self.db,{'type':['skill']})
        # Skills read in ability order, and filter by the ability they use.
        self.assertEqual(skills['rows'][0]['name_zh'],'运动')
        dex=query(self.db,{'type':['skill'],'f_ability':['敏捷']})
        self.assertEqual(sorted(r['name_zh'] for r in dex['rows']),['巧手','特技','隐匿'])
        self.assertEqual(meta(self.db)['type_conditions']['skill'],['ability'])
        expected={r[0] for r in self.db.execute("SELECT id FROM \"状态动作规则\" WHERE type IN ('ability','skill')")}
        self.assertEqual({r['id'] for r in abilities+each['rows']},expected)

    def test_metadata(self):
        m=meta(self.db)
        # The picker offers the types the data holds, in reading order; untyped sections stay behind their own filter.
        held={r[0] for r in self.db.execute("SELECT DISTINCT type FROM entries WHERE type!='section'")}
        self.assertEqual(set(m['type_order']),held);self.assertIn('hazard',m['type_order'])
        self.assertEqual(m['type_order'],sorted(m['type_order'],key=browse.TYPE_ORDER.index))
        self.assertEqual(m['type_conditions']['creature'],['size','creature_type','role','legendary_actions','cr'])
        self.assertEqual(m['type_conditions']['class'],[])
        self.assertEqual({p['name'] for p in m['presets']},{'NPC','伙伴','坐骑','魔法武器','战斗风格','传奇恩惠'})
        self.assertEqual({s['id'] for s in m['sources']},{r[0] for r in self.db.execute('SELECT id FROM sources')})
        self.assertTrue(all(s['count']==0 for s in m['sources'] if s['coverage']=='catalog_only'))
        # Short labels come from the abbreviation catalog, never from guessing at aliases.
        by_name={s['name']:s['short_name'] for s in m['sources']}
        self.assertEqual(by_name['被遗忘的国度：费伦英雄'],'FRHoF')
        self.assertEqual(by_name['瓦尔达的秘密尖塔 / 玩家包Ⅰ'],'VSS:PP')
        self.assertEqual(by_name['破解奥秘（UA） / 灵能I'],'UA 灵能I')
        self.assertEqual(by_name['模组相关 / 纸质出版物 / 黄金宝库之钥'],'黄金宝库之钥')
        self.assertEqual(by_name['模组相关 / 纸质出版物 / 冰风谷：冰霜少女的雾凇'],'冰风谷')
        self.assertEqual(by_name['模组相关 / 纸质出版物 / 深水城：龙金之劫'],'龙金之劫')
        self.assertEqual(by_name['其他/新UA/奥术子职'],'UA 奥术子职')
        codes=[s['short_name'] for s in m['sources']];self.assertEqual(len(codes),len(set(codes)))

if __name__=='__main__':unittest.main()
