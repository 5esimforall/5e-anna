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
from serve import DB, Handler, ThreadingHTTPServer, connect, query, detail, meta, csv_export

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
        p={'view':['专长'],'source':[sid],'ruleset':['5r']}
        self.assertEqual(query(self.db,p)['total'],75)
        p['ruleset']=['5e'];self.assertEqual(query(self.db,p)['total'],0)
    def test_name_literal_and_case(self):
        self.assertEqual(query(self.db,{'q':["%' OR 1=1 --"]})['total'],0)
        self.assertEqual(query(self.db,{'q':['alert']})['total'],query(self.db,{'q':['ALERT']})['total'])
    def test_fields_and_conflicts(self):
        rows=query(self.db,{'view':['法术'],'field':['school'],'value':['塑能']})['rows']
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
    def test_invalid_query(self):
        for p in [{'view':['entries; DROP TABLE entries']},{'field':['bogus'],'value':['x']},{'sort':['invalid']},{'offset':['nan']},{'offset':['-1']},{'limit':['0']},{'limit':['1501']}]:
            with self.assertRaises(ValueError):query(self.db,p)
        with self.assertRaises(LookupError):detail(self.db,'absent')
    def test_export_all_not_one_page(self):
        data=query(self.db,{'view':['专长']},export=True)
        output=csv_export(data['rows']).decode('utf-8-sig')
        self.assertEqual(len(list(csv.reader(io.StringIO(output))))-1,data['total'])
        row=data['rows'][0].copy();row['name_en']='=1+1'
        self.assertIn("'=1+1",csv_export([row]).decode('utf-8-sig'))
    def test_http_and_host_guard(self):
        self.assertEqual(self.request('/')[0],200)
        self.assertEqual(self.request('/api/meta')[0],200)
        self.assertEqual(self.request('/api/entries?view=invalid')[0],400)
        self.assertEqual(self.request('/api/entries',{'Host':'attacker.example'})[0],403)
        self.assertEqual(self.request('/../scripts/serve.py')[0],404)
        self.assertEqual(self.request('/source?path=/etc/passwd')[0],404)
    def test_brand_assets(self):
        page=self.request('/')[2]
        self.assertIn(b'href="/favicon.ico"',page)
        for size in (16,32,48):
            path=f'/favicon-{size}.png'
            self.assertIn(f'href="{path}"'.encode(),page)
            status,kind,body=self.request(path)
            self.assertEqual((status,kind),(200,'image/png'))
            self.assertEqual(body[:8],b'\x89PNG\r\n\x1a\n')
            self.assertEqual(struct.unpack('>II',body[16:24]),(size,size))
            self.assertEqual(body[25],6)  # RGBA; preserve transparent edges.
        status,kind,body=self.request('/favicon.ico')
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
        self.assertEqual(self.request('/favicon-24.png')[0],404)
        self.assertEqual(self.request('/assets/branding/anna.png')[0],404)
        self.assertEqual(self.request('/favicon.ico',{'Host':'attacker.example'})[0],403)
        self.assertIn(b'class="brand-logo" src="/anna-logo.png" width="36" height="36" alt=""',page)
        self.assertNotIn(b'class="brand-icon"',page)
        status,kind,body=self.request('/anna-logo.png')
        self.assertEqual((status,kind),(200,'image/png'))
        self.assertEqual(body[:8],b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II',body[16:24]),(128,128))
        self.assertEqual(body[25],6)
        self.assertEqual(self.request('/anna-logo.png',{'Host':'attacker.example'})[0],403)
    def test_source_highlights(self):
        e=query(self.db,{})['rows'][0]
        url='/source?'+urlencode({'path':e['page_path'],'start':e['start'],'end':e['end']})
        status,kind,body=self.request(url)
        self.assertEqual(status,200);self.assertIn(b'<mark id="evidence">',body)
        self.assertIn('text/html',kind)
        self.assertIn(b'href="/favicon.ico"',body)
        self.assertLess(body.index(b'src="/theme.js"'),body.index(b'href="/style.css"'))
    def test_theme_bootstrap(self):
        status,kind,body=self.request('/theme.js')
        self.assertEqual((status,kind),(200,'text/javascript; charset=utf-8'))
        self.assertIn(b'prefers-color-scheme',body)
        self.assertEqual(self.request('/theme.js',{'Host':'attacker.example'})[0],403)
        page=self.request('/')[2]
        self.assertLess(page.index(b'src="/theme.js"'),page.index(b'href="/style.css"'))
        self.assertIn(b'id="theme"',page)
        self.assertIn(b'<button class="theme-button"',page)
        self.assertNotIn(b'id="snapshot"',page)
        self.assertNotIn(b'class="theme-control"',page)
    def test_mount_and_animal_catalog_views(self):
        mounts=query(self.db,{'view':['坐骑'],'versions':['each']})
        self.assertEqual(mounts['total'],20)
        self.assertEqual(sum(e['ruleset']=='5e' for e in mounts['rows']),12)
        self.assertEqual(sum(e['ruleset']=='5r' for e in mounts['rows']),8)
        self.assertFalse(any(e['name_en'] in {'Flying Snake','Wolf','Warhorse Skeleton'} for e in mounts['rows']))
        offers=query(self.db,{'view':['动物购置'],'versions':['each']})
        self.assertEqual(offers['total'],19)
        self.assertTrue(all(e['type']=='animal_offer' for e in offers['rows']))
        horse=next(e for e in mounts['rows'] if e['name_en']=='Warhorse' and e['ruleset']=='5r')
        d=detail(self.db,horse['id'])
        self.assertTrue(any(r['predicate']=='catalog_offer' for r in d['relations']))
        data=self.request('/api/entries?'+urlencode({'view':'动物购置'}))
        self.assertEqual(data[0],200)
        self.assertEqual(json.loads(data[2])['versions_total'],19)

    def test_entry_layout_endpoint(self):
        eid=self.db.execute("SELECT id FROM entries WHERE type='spell' AND name_en='Etherealness' AND ruleset='5r'").fetchone()[0]
        status,kind,body=self.request('/api/entry-layout?'+urlencode({'id':eid}))
        self.assertEqual(status,200);self.assertIn('application/json',kind)
        self.assertIn(json.loads(body)['status'],{'ok','missing','stale'})
        self.assertEqual(self.request('/api/entry-layout?id=ver_missing')[0],404)
        self.assertEqual(self.request('/api/entry-layout?id='+eid,{'Host':'attacker.example'})[0],403)
        # Reader notes read normalized values from the entry itself.
        self.assertEqual(detail(self.db,eid)['quickref']['spell_level'],['7'])

    def test_metadata(self):
        m=meta(self.db);self.assertEqual(len(m['views']),24);self.assertIn('动物购置',{v['name'] for v in m['views']})
        self.assertEqual(len(m['sources']),155)
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
