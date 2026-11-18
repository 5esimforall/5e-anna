import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

from anna.catalog.reference_coverage import compare_baseline, inventory, proper_target


class CoverageTests(unittest.TestCase):
    def test_inventory_counts_printed_uses_even_when_no_reference_exists(self):
        text='持续时间：恒定连着正文\n受到2d4折光伤害，折光如昼。'
        pattern=re.compile(r'\d+d\d+(折光)伤害')
        uses=list(inventory([],text,pattern,[(0,len(text))], re.compile(r'\s*(恒定)')))
        self.assertCountEqual([(text[a:b],family) for a,b,family in uses],
                              [('恒定','duration'),('持续时间','duration_label'),('折光','damage_notation')])

    def test_equal_spelling_does_not_validate_wrong_family_edition_or_legend_scope(self):
        e=dict(name_zh='折光',name_en='Prism',ruleset='5r',type='rule',slot_definition={'family':'damage'})
        scope={'ruleset':'5r'}
        self.assertTrue(proper_target('damage_notation','折光',e,scope,'p',0))
        self.assertFalse(proper_target('school','折光',e,scope,'p',0))
        self.assertFalse(proper_target('damage_notation','折光',e,{'ruleset':'5e'},'p',0))
        symbol={**e,'page_path':'p','slot_definition':{'family':'legend','symbol':'Q','scope_span':[5,9]}}
        self.assertTrue(proper_target('legend','Q',symbol,scope,'p',7))
        self.assertFalse(proper_target('legend','Q',symbol,scope,'p',9))
        self.assertFalse(proper_target('legend','Q',symbol,scope,'q',7))

    def test_compare_only_equal_snapshots_and_detect_retargeting(self):
        schema='CREATE TABLE effective_references(page_path,start,end,target_id,can_link);'
        summary={'snapshot_commit':'rev','input_manifest_sha256':'inputs'}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.sqlite'
            old=sqlite3.connect(path);old.executescript(schema+'CREATE TABLE metadata(key,value);')
            old.execute('INSERT INTO metadata VALUES(?,?)',('summary',json.dumps(summary)))
            old.execute("INSERT INTO effective_references VALUES('p',2,3,'a',1)");old.commit();old.close()
            for target,expected in [('a',0),('b',1)]:
                db=sqlite3.connect(':memory:');db.executescript(schema)
                db.execute("INSERT INTO effective_references VALUES('p',2,3,?,1)",(target,))
                self.assertEqual(compare_baseline(db,path,summary),expected);db.close()
            db=sqlite3.connect(':memory:')
            with self.assertRaisesRegex(ValueError,'不同原文快照'):
                compare_baseline(db,path,{**summary,'input_manifest_sha256':'changed'})
            db.close()
