"""5etools-cn comparison: source alignment, entry matching, disagreements and their audit."""
import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import counterparts
from counterparts import align_sources, build_counterparts, compare, load_records
from audit import audit_counterparts


def spell(en, zh, source, level, school, **extra):
    return {'ENG_name': en, 'name': zh, 'source': source, 'level': level, 'school': school,
            'duration': [{'type': 'instant'}], **extra}


CATALOG = {
    'spells/spells-phb.json': {'spell': [
        spell('Fireball', '火球术', 'PHB', 3, 'V', translator='不全书', reprintedAs=['火球术|XPHB']),
        spell('Shield', '护盾术', 'PHB', 1, 'A', translator='不全书'),
        spell('Magic Missile', '魔法飞弹', 'PHB', 1, 'V')]},
    'spells/spells-xphb.json': {'spell': [
        spell('Fireball', '火球术', 'XPHB', 3, 'V', translator='不全书'),
        spell('Divine Smite', '至圣斩', 'XPHB', 1, 'V'),
        spell('Starry Wisp', '星火弹', 'XPHB', 0, 'V'),
        spell('Sorcerous Burst', '术法爆发', 'XPHB', 0, 'V'),
        spell('Shining Smite', '闪耀斩', 'XPHB', 2, 'T', translator='不全书')]},
    'class/class-cleric.json': {
        'class': [{'ENG_name': 'Cleric', 'name': '牧师', 'source': 'PHB'}],
        'subclass': [{'ENG_name': 'Life Domain', 'name': '生命领域', 'shortName': '生命', 'source': 'PHB', 'className': '牧师', 'classSource': 'PHB'},
                     {'ENG_name': 'Nature Domain', 'name': '自然领域', 'shortName': '自然', 'source': 'PHB', 'className': '牧师', 'classSource': 'PHB'}],
        'subclassFeature': [
            {'ENG_name': 'Divine Strike', 'name': '神圣打击', 'source': 'PHB', 'className': '牧师', 'classSource': 'PHB',
             'subclassShortName': '生命', 'subclassSource': 'PHB', 'level': 8},
            {'ENG_name': 'Divine Strike', 'name': '神圣打击', 'source': 'PHB', 'className': '牧师', 'classSource': 'PHB',
             'subclassShortName': '自然', 'subclassSource': 'PHB', 'level': 8}]},
    'bestiary/bestiary-mm.json': {'monster': [
        {'ENG_name': 'Goblin', 'name': '地精', 'source': 'MM', 'cr': '1/4', 'size': ['S']},
        {'ENG_name': 'Goblin Boss', 'name': '地精首领', 'source': 'MM', '_copy': {'name': 'Goblin'}}]},
    # A code that collides with 不全书's own abbreviation for an unrelated book.
    'adventure/adventure-cm.json': {'monster': [{'ENG_name': 'Book Wyrm', 'name': '书虫', 'source': 'CM', 'cr': '5', 'size': ['L'], 'translator': '不全书'}]},
    'generated/gendata-tables.json': {'spell': [spell('Fireball', '火球术', 'PHB', 9, 'N')]},
    'fluff-spells-phb.json': {'spell': [spell('Fireball', '火球术', 'PHB', 9, 'N')]},
}

SOURCES = {'phb14': ('玩家手册2014', ['PHB'], '5e'), 'phb24': ('玩家手册2024', ['PHB24'], '5r'),
           'mm': ('怪物图鉴2014', ['MM'], '5e'), 'cm': ('歪曲之月', ['CM'], '5e')}

ENTRIES = [
    # id, type, zh, en, source, ruleset, fields
    ('fire14', 'spell', '火球术', 'Fireball', 'phb14', '5e', {'spell_level': '三环', 'school': '塑能', 'concentration': False}),
    ('fire24', 'spell', '火球术', 'Fireball', 'phb24', '5r', {'spell_level': '三环', 'school': '塑能'}),
    ('smite', 'spell', '至圣斩', 'Divine Smite', 'phb24', '5r', {}),
    ('wisp', 'spell', '星火弹', 'Starry Wisp', 'phb24', '5r', {}),
    ('burst', 'spell', '术法爆发', 'Sorcerous Burst', 'phb24', '5r', {}),
    ('shield', 'spell', '护盾术', 'Shield', 'phb14', '5e', {'spell_level': '二环', 'school': '防护'}),
    ('shield24', 'spell', '护盾术', 'Shield', 'phb24', '5r', {}),
    ('missile', 'spell', '魔法飞弹', '', 'phb14', '5e', {}),
    ('homebrew', 'spell', '自制箭', 'Homebrew Bolt', 'phb14', '5e', {}),
    ('goblin', 'creature', '地精', 'Goblin', 'mm', '5e', {'cr': '1/4', 'size': '微型'}),
    ('boss', 'creature', '地精首领', 'Goblin Boss', 'mm', '5e', {'cr': '1', 'size': '中型'}),
    ('cm_goblin', 'creature', '地精', 'Goblin', 'cm', '5e', {}),
    ('cleric', 'class', '牧师', 'Cleric', 'phb14', '5e', {}),
    ('life', 'subclass', '生命领域', 'Life Domain', 'phb14', '5e', {}),
    ('strike', 'class_feature', '神圣打击', 'Divine Strike', 'phb14', '5e', {'level': '8'}),
    ('strike_unowned', 'class_feature', '神圣打击', 'Divine Strike', 'phb14', '5e', {}),
    ('fighter', 'class', '战士', 'Fighter', 'phb14', '5e', {}),
    ('strike_fighter', 'class_feature', '神圣打击', 'Divine Strike', 'phb14', '5e', {}),
]
RELATIONS = [('life', 'belongs_to_class', 'cleric'), ('strike', 'part_of', 'life'), ('strike_fighter', 'part_of', 'fighter')]


def write_catalog(root, catalog=CATALOG):
    for rel, doc in catalog.items():
        path = root / 'data' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False))


def make_db(path, entries=ENTRIES, relations=RELATIONS, sources=SOURCES):
    db = sqlite3.connect(path)
    db.executescript('''
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE sources(id TEXT PRIMARY KEY,name TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE entries(id TEXT PRIMARY KEY,name TEXT,name_zh TEXT,name_en TEXT,type TEXT,ruleset TEXT,fields TEXT);
    CREATE TABLE entry_sources(entry_id TEXT,source_id TEXT);
    CREATE TABLE relations(id TEXT PRIMARY KEY,subject_id TEXT,predicate TEXT,object_id TEXT);
    ''')
    for sid, (name, abbreviations, ruleset) in sources.items():
        db.execute('INSERT INTO sources VALUES(?,?,?)', (sid, name, json.dumps({'id': sid, 'name': name, 'abbreviations': abbreviations, 'ruleset_hint': ruleset})))
    for eid, typ, zh, en, sid, ruleset, fields in entries:
        db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?)', (eid, zh + en, zh, en, typ, ruleset, json.dumps(fields, ensure_ascii=False)))
        db.execute('INSERT INTO entry_sources VALUES(?,?)', (eid, sid))
    for i, (subject, predicate, obj) in enumerate(relations):
        db.execute('INSERT INTO relations VALUES(?,?,?,?)', (f'rel{i}', subject, predicate, obj))
    db.commit()
    return db


class CounterpartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fivetools = self.root / '5etools-cn'
        write_catalog(self.fivetools)
        self.db = make_db(self.root / 'catalog.sqlite')
        records, self.snapshot, self.alignment, outcomes, self.findings, _ = compare(self.db, self.fivetools)
        self.records = {r['id']: r for r in records}
        self.outcomes = {o['entry_id']: o for o in outcomes}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def record_of(self, eid):
        o = self.outcomes[eid]
        return self.records[o['record_id']] if o['record_id'] else None

    def findings_of(self, kind):
        return [f for f in self.findings if f['kind'] == kind]

    def test_records_skip_generated_fluff_and_inherited_fields(self):
        self.assertEqual(sorted(self.snapshot), ['catalog', 'commit', 'files', 'input_sha256', 'root', 'unreadable_files'])
        self.assertEqual(self.snapshot['commit'], 'unversioned')
        self.assertFalse(any('generated/' in r['locator'] or 'fluff' in r['locator'] for r in self.records.values()))
        boss = next(r for r in self.records.values() if r['name_en'] == 'Goblin Boss')
        self.assertTrue(boss['copied'])
        self.assertEqual(boss['values'], {})
        self.assertEqual(self.outcomes['boss']['outcome'], 'resolved')
        self.assertFalse([f for f in self.findings if f['entry_id'] == 'boss'])

    def test_missing_mirror_is_rejected(self):
        with self.assertRaises(FileNotFoundError):
            load_records(self.root / 'absent')

    def test_sources_align_by_supported_code_and_by_votes(self):
        pairs = {(p['source_id'], p['code']): p for p in self.alignment}
        self.assertEqual(pairs[('phb14', 'PHB')]['status'], '上下文推得')
        self.assertIn('catalog_code', pairs[('phb14', 'PHB')]['rules'])
        self.assertEqual(pairs[('phb24', 'XPHB')]['rules'], ['unique_name_votes'])
        self.assertEqual(pairs[('phb24', 'XPHB')]['evidence']['votes'], 3)
        # One unique name is not enough to align 玩家手册2024 with the 2014 code.
        self.assertNotIn(('phb24', 'PHB'), pairs)
        # The colliding abbreviation has no name supporting it and stays out of matching.
        self.assertEqual(pairs[('cm', 'CM')]['status'], '待核')
        self.assertEqual(pairs[('cm', 'CM')]['evidence']['votes'], 0)

    def test_code_contradicted_by_votes_is_a_conflict(self):
        records = [{'kind': 'spell', 'name_en': n, 'source': 'Y'} for n in ('A', 'B', 'C', 'D')] + [{'kind': 'spell', 'name_en': 'E', 'source': 'X'}]
        entries = [{'type': 'spell', 'name_en': n, 'source_ids': ['s']} for n in 'ABCDE']
        pairs = align_sources({'s': {'abbreviations': ['X']}}, entries, records)
        self.assertEqual({(p['code'], p['status']) for p in pairs}, {('X', '冲突'), ('Y', '冲突')})

    def test_same_name_resolves_within_each_entry_source(self):
        self.assertEqual(self.record_of('fire14')['source'], 'PHB')
        self.assertEqual(self.record_of('fire24')['source'], 'XPHB')
        self.assertEqual(self.outcomes['fire14']['rule'], 'english_name')
        self.assertEqual(self.outcomes['fire24']['aligned_codes'], ['XPHB'])

    def test_name_found_only_outside_aligned_sources(self):
        o = self.outcomes['shield24']
        self.assertEqual((o['outcome'], o['record_id'], o['candidate_sources']), ('other_source', None, ['PHB']))
        o = self.outcomes['cm_goblin']
        self.assertEqual((o['outcome'], o['record_id'], o['aligned_codes']), ('unaligned', None, []))
        self.assertEqual(self.outcomes['homebrew']['outcome'], 'unmatched')
        self.assertEqual(self.outcomes['homebrew']['candidates'], [])

    def test_chinese_name_resolves_missing_english_name(self):
        o = self.outcomes['missile']
        self.assertEqual((o['outcome'], o['rule']), ('resolved', 'chinese_name'))
        missing = [f for f in self.findings_of('name_en_missing') if f['entry_id'] == 'missile']
        self.assertEqual(missing[0]['detail'], {'entry': '', 'record': 'Magic Missile'})
        self.assertFalse(self.findings_of('name_en_differs'))

    def test_owner_subclass_chooses_and_other_class_rules_out(self):
        o = self.outcomes['strike']
        self.assertEqual((o['outcome'], o['rule']), ('resolved', 'english_name+owner_context'))
        self.assertEqual(self.record_of('strike')['context']['subclass'], 'lifedomain')
        o = self.outcomes['strike_unowned']
        self.assertEqual((o['outcome'], len(o['candidates']), o['context_conflict']), ('ambiguous', 2, False))
        o = self.outcomes['strike_fighter']
        self.assertEqual((o['outcome'], o['record_id'], o['context_conflict']), ('ambiguous', None, True))

    def test_field_disagreements_are_reported_not_applied(self):
        differs = {(f['entry_id'], f['detail']['field']): f['detail'] for f in self.findings_of('field_differs')}
        self.assertEqual(differs[('shield', 'spell_level')], {'field': 'spell_level', 'entry': [2], 'record': [1]})
        self.assertEqual(differs[('goblin', 'size')], {'field': 'size', 'entry': ['微型'], 'record': ['小型']})
        self.assertNotIn(('goblin', 'cr'), differs)
        self.assertNotIn(('fire14', 'spell_level'), differs)
        self.assertNotIn(('shield', 'school'), differs)

    def test_reprints_link_resolved_entries_and_unclaimed_book_records(self):
        reprints = self.findings_of('reprinted_as')
        self.assertEqual([(f['entry_id'], f['detail']['other_entry_id']) for f in reprints], [('fire14', 'fire24')])
        unclaimed = {f['detail']['name_en'] for f in self.findings_of('unclaimed_record')}
        # Only 不全书 translations in aligned sources: Book Wyrm's CM is not aligned.
        self.assertEqual(unclaimed, {'Shining Smite'})

    def test_export_and_audit(self):
        out = self.root / 'out'
        out.mkdir()
        report = build_counterparts(self.db, self.fivetools, out)
        self.assertEqual(report['entries'], len(ENTRIES))
        self.assertEqual(report['outcomes'], {'resolved': 12, 'ambiguous': 2, 'other_source': 1, 'unaligned': 1, 'unmatched': 2})
        result = audit_counterparts(self.db, out)
        self.assertEqual(result['errors'], [])
        with (out / 'counterparts.csv').open(encoding='utf-8-sig', newline='') as f:
            rows = {r['entry_id']: r for r in csv.DictReader(f)}
        self.assertEqual(rows['fire24']['record_source'], 'XPHB')
        self.assertTrue(rows['fire24']['record_locator'].startswith('data/spells/spells-xphb.json#/spell/'))
        unmatched = next(eid for eid, o in self.outcomes.items() if o['outcome'] == 'unmatched')
        self.db.execute('UPDATE counterparts SET record_id=? WHERE entry_id=?', (self.outcomes['fire14']['record_id'], unmatched))
        self.assertIn(unmatched + ': unresolved record', audit_counterparts(self.db, out)['errors'])


if __name__ == '__main__':
    unittest.main()
