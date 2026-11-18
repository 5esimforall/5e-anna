"""The tour: curated starting points over the book's own contents, page entries and relations."""
from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from anna import paths
from anna.presentation import catalog, tour
from anna.presentation.tour import Tour, TourError, clip, first_sentence, load
from anna.server.app import create_server
from anna.server.linked_book import BOOK, build_book
import test_linked_book as linked_fixtures

SCHEMA = '''
CREATE TABLE entries(id TEXT PRIMARY KEY,entity_id TEXT,name TEXT,name_zh TEXT,name_en TEXT,type TEXT,page_path TEXT,start INTEGER,end INTEGER,ruleset TEXT,legacy TEXT,classification_status TEXT,fields TEXT,data TEXT NOT NULL);
CREATE TABLE sources(id TEXT PRIMARY KEY,name TEXT NOT NULL,origin TEXT,ruleset_hint TEXT,release_status TEXT,source_type TEXT,archived INTEGER,coverage TEXT,data TEXT NOT NULL);
CREATE TABLE relations(id TEXT PRIMARY KEY,subject_id TEXT,predicate TEXT,object_id TEXT,target_label TEXT,target_locator TEXT,evidence TEXT,status TEXT);
CREATE TABLE pages(path TEXT PRIMARY KEY,title TEXT,role TEXT,parse_status TEXT,sha256 TEXT,text TEXT,source_ids TEXT,data TEXT NOT NULL);
CREATE TABLE entry_sources(entry_id TEXT,source_id TEXT,PRIMARY KEY(entry_id,source_id));
'''
STEP_ONE = ' \n第一步 Step One\n \n选择一个职业，写在角色卡上。职业细节见第三章。\n职业表 Class Table\n甲 乙 丙\n记下等级 Write Level\n把等级写在角色卡上。\n'
WIZARD = ' \n法师 Wizard\n学术派奥术施法者。\n1级：施法 Spellcasting\n你能施展法术。\n'
SCHOOL = ' \n防护学派 Abjurer\n守护者。\n'
SOLDIER = ' \n士兵 Soldier\n专长：野蛮打击者。\n'


def node(id, parent, label, path='', status='present'):
    return {'id': id, 'parent_id': parent, 'label': label, 'path': path, 'fragment': '', 'status': status if path else 'placeholder'}


NODES = [
    node('1', '', '本书速查', 'topics/q.htm'), node('2', '1', '职业速查', 'topics/q.htm'), node('3', '2', '法师', 'topics/wizard.htm'),
    node('10', '', '——核心规则——', 'topics/分隔符.htm'),
    node('11', '', '玩家手册', 'topics/cover.htm'), node('12', '11', '第二章', 'topics/ch2.htm'),
    node('13', '12', '第一步', 'topics/step1.htm'), node('14', '12', '————', 'topics/分隔符.htm'), node('15', '12', '第二步', 'topics/step2.htm'),
    node('16', '11', '第三章', 'topics/wizard.htm'), node('17', '16', '防护学派', 'topics/school.htm'),
    node('20', '', '——旧版内容——', 'topics/分隔符.htm'), node('21', '', '旧书'), node('22', '', '重名', 'topics/cover.htm'), node('23', '', '重名', 'topics/cover.htm'),
]
CURATION = {
    'schema': 1,
    'sections': [{'key': 'start', 'label': {'zh': '开始', 'en': 'Start'}, 'topics': [
        {'key': 'create', 'label': {'zh': '创建角色', 'en': 'Create'}, 'ruleset': '5r', 'contents': '玩家手册 / 第二章', 'sequence': True},
        {'key': 'wizard', 'label': {'zh': '法师', 'en': 'Wizard'}, 'contents': '本书速查 / 职业速查 / 法师'},
        {'key': 'gone', 'label': {'zh': '缺失', 'en': 'Gone'}, 'contents': '没有 / 这一章'},
        {'key': 'twin', 'label': {'zh': '重名', 'en': 'Twin'}, 'contents': '重名'},
        {'key': 'spells', 'label': {'zh': '法术', 'en': 'Spells'}, 'query': {'type': ['spell']}},
    ]}],
    'choices': {'玩家手册 / 第二章 / 第一步': ['wizard'], '没有': ['create']},
}


def span(text, phrase, end_phrase=None):
    start = text.index(phrase)
    return start, (text.index(end_phrase, start) if end_phrase else len(text))


class Catalog:
    """A small published catalog: pages, nested entries, relations and sources, in the real table shapes."""

    def __init__(self, root):
        self.db = root / 'catalog.sqlite'
        self.rows = []
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO sources VALUES('src_phb','玩家手册','官方','5r','正式','核心',0,'','{}')")
            for path, text in {'topics/step1.htm': STEP_ONE, 'topics/wizard.htm': WIZARD, 'topics/school.htm': SCHOOL,
                               'topics/soldier.htm': SOLDIER, 'topics/step2.htm': ' \n第二步\n确定起源。\n', 'topics/cover.htm': ' \n封面\n'}.items():
                conn.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?)', (path, '', 'document', 'ok', '', text, '["src_phb"]', '{}'))
            step = span(STEP_ONE, '第一步')
            self.entry(conn, 'ver_a1', '第一步 Step One', 'section', 'topics/step1.htm', step, STEP_ONE.index('\n', step[0]), primary=True)
            self.entry(conn, 'ver_a2', '职业表 Class Table', 'lore', 'topics/step1.htm', span(STEP_ONE, '职业表', '记下'),
                       STEP_ONE.index('\n', STEP_ONE.index('职业表')), parent='ver_a1', table=True)
            self.entry(conn, 'ver_a3', '记下等级 Write Level', 'section', 'topics/step1.htm', span(STEP_ONE, '记下等级'),
                       STEP_ONE.index('\n', STEP_ONE.index('记下等级')), parent='ver_a1')
            wizard = span(WIZARD, '法师')
            self.entry(conn, 'ver_b1', '法师 Wizard', 'class', 'topics/wizard.htm', wizard, WIZARD.index('\n', wizard[0]), primary=True, name_zh='法师')
            self.entry(conn, 'ver_b2', '1级：施法 Spellcasting', 'class_feature', 'topics/wizard.htm', span(WIZARD, '1级'),
                       WIZARD.index('\n', WIZARD.index('1级')), parent='ver_b1')
            self.entry(conn, 'ver_c1', '防护学派 Abjurer', 'subclass', 'topics/school.htm', span(SCHOOL, '防护'),
                       SCHOOL.index('\n', SCHOOL.index('防护')), primary=True)
            self.entry(conn, 'ver_d1', '士兵 Soldier', 'background', 'topics/soldier.htm', span(SOLDIER, '士兵'),
                       SOLDIER.index('\n', SOLDIER.index('士兵')), primary=True)
            self.entry(conn, 'ver_d2', '野蛮打击者 Savage Attacker', 'feat', 'topics/soldier.htm', (0, 1), 1)
            self.entry(conn, 'ver_e1', '护盾术 Shield', 'spell', 'topics/cover.htm', (0, 1), 1)
            conn.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?,?,?)', [
                ('rel_1', 'ver_c1', 'belongs_to_class', 'ver_b1', '法师', '', '{}', '上下文推得'),
                ('rel_2', 'ver_b2', 'belongs_to_class', 'ver_b1', '法师', '', '{}', '上下文推得'),
                ('rel_3', 'ver_d1', 'grants_feat', 'ver_d2', '野蛮打击者', '', '{}', '原文明示'),
                ('rel_4', 'ver_d1', 'grants_feat', None, '不存在', '', '{}', '待核')])
            conn.commit()

    def entry(self, conn, id, name, type, path, span, title_end, *, parent='', primary=False, table=False, name_zh=''):
        data = {'id': id, 'name': name, 'type': type, 'page_path': path, 'start': span[0], 'end': span[1], 'title_end': title_end,
                'parent_id': parent, 'is_primary': primary, 'ruleset': '5r', 'source_ids': ['src_phb'], 'fields': {}}
        if table:
            data['table_span'] = [title_end, span[1]]
        conn.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (id, 'ent_' + id, name, name_zh or name.split(' ')[0], '', type, path, span[0], span[1], '5r', 'unmarked', '原文明示', '{}', json.dumps(data)))
        conn.execute("INSERT INTO entry_sources VALUES(?, 'src_phb')", (id,))


def write(root, curation):
    path = root / 'tour.json'
    path.write_text(json.dumps(curation, ensure_ascii=False))
    return path


class CurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def rejected(self, change, message):
        curation = json.loads(json.dumps(CURATION))
        change(curation)
        with self.assertRaises(TourError) as caught:
            load(write(self.root, curation))
        self.assertEqual((caught.exception.code, caught.exception.status), ('curation_invalid', 500))
        self.assertIn(message, str(caught.exception))

    def test_valid_curation_loads_topics_and_choices(self):
        loaded = load(write(self.root, CURATION))
        self.assertEqual(loaded['sections'][0]['topics'], ['create', 'wizard', 'gone', 'twin', 'spells'])
        self.assertTrue(loaded['topics']['create'].sequence)
        self.assertEqual(loaded['choices']['玩家手册 / 第二章 / 第一步'], ['wizard'])

    def test_malformed_curation_is_rejected_whole(self):
        topics = lambda c: c['sections'][0]['topics']
        self.rejected(lambda c: c.update(schema=2), 'schema')
        # Topic keys share the URL with contents nodes and entries.
        self.rejected(lambda c: topics(c)[0].update(key='n12'), "'n12'")
        self.rejected(lambda c: topics(c)[0].update(key='ver_a1'), "'ver_a1'")
        self.rejected(lambda c: topics(c).append(dict(topics(c)[1])), "'wizard' repeated")
        self.rejected(lambda c: topics(c)[0].update(label={'zh': '创建角色'}), "'create' label")
        self.rejected(lambda c: topics(c)[0].update(ruleset='2024'), "'create' ruleset")
        self.rejected(lambda c: topics(c)[0].update(facet='spell_level'), "'create' facet")
        self.rejected(lambda c: topics(c)[4].update(query={'type': 'spell'}), "'spells' query")
        self.rejected(lambda c: topics(c)[1].pop('contents'), "'wizard' leads nowhere")
        self.rejected(lambda c: c['choices'].update({'玩家手册': ['absent']}), "choices '玩家手册'")
        (self.root / 'broken.json').write_text('{')
        with self.assertRaises(TourError):
            load(self.root / 'broken.json')

    def test_quoted_text_is_clipped_at_sentences(self):
        self.assertEqual(clip('短句。', 10), ('短句。', False))
        self.assertEqual(clip('第一句很长很长。第二句', 10), ('第一句很长很长。', True))
        self.assertEqual(clip('没有句号的一段很长很长的文字', 10), ('没有句号的一段很长很…', True))
        self.assertEqual(first_sentence('选择一个职业。职业细节见第三章。'), '选择一个职业。')
        # Table cells run on without a sentence end; they give no hint rather than a cut-off row.
        self.assertEqual(first_sentence('职业 喜好 主要属性 ' * 10), '')


class TourTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.catalog = Catalog(root)
        self.tour = Tour(self.catalog.db, NODES, {'ver_b1'}, load(write(root, CURATION)))

    def items(self, view, key):
        return [i for g in view['groups'] if g['key'] == key for i in g['items']]

    def test_unresolved_paths_are_reported_not_guessed(self):
        self.assertEqual(self.tour.unresolved, {('topic', 'gone'): 'missing', ('topic', 'twin'): 'ambiguous', ('choices', '没有'): 'missing'})
        root = self.tour.view('')
        status = {i['id']: i['status'] for i in self.items(root, 'section')}
        self.assertEqual(status, {'create': 'ok', 'wizard': 'ok', 'gone': 'missing', 'twin': 'ambiguous', 'spells': 'ok'})
        self.assertEqual(self.tour.view('twin')['status'], 'ambiguous')
        self.assertEqual(self.tour.view('twin')['groups'], [])

    def test_root_lists_sections_then_the_shelf_grouped_by_dividers(self):
        root = self.tour.view('')
        self.assertEqual([g['key'] for g in root['groups']], ['section', 'shelf'])
        counts = {i['id']: i['count'] for i in self.items(root, 'section')}
        self.assertEqual((counts['create'], counts['spells']), (2, 1))
        shelf = self.items(root, 'shelf')
        self.assertEqual([(i['id'], i['kind'], i['title'], i['count']) for i in shelf],
                         [('n1', 'contents', '本书速查', 1), ('n10', 'shelf', '核心规则', 1), ('n20', 'shelf', '旧版内容', 3)])
        core = self.tour.view('n10')
        self.assertEqual((core['title'], core['read']), ('核心规则', None))
        self.assertEqual([i['id'] for i in self.items(core, 'contents')], ['n11'])

    def test_a_sequence_quotes_each_step_and_numbers_across_bare_dividers(self):
        view = self.tour.view('create')
        self.assertEqual((view['trail'], view['ruleset'], view['read']), (['玩家手册', '第二章'], '5r', {'root': '12'}))
        contents = [g for g in view['groups'] if g['key'] == 'contents']
        self.assertEqual([[i['id'] for i in g['items']] for g in contents], [['n13'], ['n15']])
        self.assertTrue(all(g['ordered'] for g in contents))
        self.assertEqual(contents[0]['items'][0]['hint'], '选择一个职业，写在角色卡上。')

    def test_a_step_leads_with_source_words_then_choices_and_its_checklist(self):
        step = self.tour.view('n13')
        self.assertEqual(step['lead'], {'text': '选择一个职业，写在角色卡上。职业细节见第三章。', 'truncated': False})
        self.assertEqual(step['sources'], ['玩家手册'])
        self.assertEqual([i['id'] for i in self.items(step, 'choices')], ['wizard'])
        outline = self.items(step, 'outline')
        self.assertEqual([(i['id'], i['table'], i.get('hint')) for i in outline],
                         [('ver_a2', True, None), ('ver_a3', False, '把等级写在角色卡上。')])
        # The previous and next steps skip the divider between them.
        self.assertEqual(step['sequence'], {'previous': None, 'next': self.tour.contents_item('15')})
        self.assertEqual(self.tour.view('n15')['sequence']['previous']['id'], 'n13')
        # A section page entry is reading matter, not an Anna entry.
        self.assertIsNone(step['anna'])

    def test_a_class_lists_its_parts_and_subclasses_in_its_own_book(self):
        page = self.tour.view('n3')
        self.assertEqual(page['anna'], {'entry': 'ver_b1', 'type': 'class'})
        self.assertEqual([i['id'] for i in self.items(page, 'outline')], ['ver_b2'])
        entry = self.tour.view('ver_b1')
        # The quick-reference index lists the page but the book owns it.
        self.assertEqual(entry['trail'], ['玩家手册', '第三章'])
        self.assertEqual((entry['lead']['text'], entry['read']), ('学术派奥术施法者。', {'entry': 'ver_b1'}))
        self.assertEqual([i['id'] for i in self.items(entry, 'parts')], ['ver_b2'])
        self.assertEqual([i['id'] for i in self.items(entry, 'subclasses')], ['ver_c1'])
        subclass = self.tour.view('ver_c1')
        self.assertEqual([i['id'] for i in self.items(subclass, 'class')], ['ver_b1'])
        # Without a located definition there is nothing for Stella to open.
        self.assertIsNone(subclass['read'])

    def test_a_background_names_the_feat_it_grants(self):
        background = self.tour.view('ver_d1')
        self.assertEqual([i['id'] for i in self.items(background, 'feats')], ['ver_d2'])

    def test_a_level_reads_as_a_detail_when_its_own_words_outrun_its_lead(self):
        shapes = {s: self.tour.view(s)['shape'] for s in ('', 'create', 'n13', 'ver_b1', 'ver_d1', 'ver_a2', 'ver_a3')}
        # Levels whose lead already says everything before their rows are indexes; leaves read as details.
        self.assertEqual(shapes, {'': 'index', 'create': 'index', 'n13': 'index', 'ver_b1': 'index',
                                  'ver_d1': 'index', 'ver_a2': 'detail', 'ver_a3': 'detail'})
        parted = {'lead': {'text': '学术派奥术施法者。', 'truncated': True}, 'groups': [{'key': 'parts', 'items': []}]}
        self.assertEqual(Tour.shaped(parted)['shape'], 'detail')

    def test_a_levels_own_words_are_the_range_its_lead_quotes(self):
        for segment in ('n3', 'n13', 'wizard', 'ver_b1', 'ver_a3'):
            with self.subTest(segment=segment):
                self.assertEqual(self.tour.span_text(self.tour.own_span(segment)), self.tour.view(segment)['lead']['text'])
        # An entry's own words stop where its first part begins, and its own references are not links.
        self.assertEqual(self.tour.own_span('ver_b1'), ('topics/wizard.htm', WIZARD.index('\n', WIZARD.index('法师')), WIZARD.index('1级'), 'ver_b1'))
        source = Path(self.tmp.name) / 'source'
        for segment in ('', 'spells', 'gone', 'twin'):
            with self.subTest(segment=segment):
                self.assertIsNone(self.tour.own_span(segment))
                self.assertEqual(self.tour.layout(segment, source), {'status': 'empty'})
        self.assertEqual(self.tour.layout('ver_a3', source), {'status': 'missing', 'path': 'topics/step1.htm'})

    def test_unknown_and_malformed_nodes_are_explicit(self):
        for segment, code, status in [('bad.seg', 'invalid_node', 400), ('N13', 'invalid_node', 400), ('n999', 'node_missing', 404),
                                      ('ver_ffff', 'node_missing', 404), ('nope', 'node_missing', 404)]:
            for ask in (self.tour.view, lambda segment: self.tour.layout(segment, Path(self.tmp.name))):
                with self.subTest(segment=segment, ask=ask), self.assertRaises(TourError) as caught:
                    ask(segment)
                self.assertEqual((caught.exception.code, caught.exception.status), (code, status))


class RouteTests(unittest.TestCase):
    def request(self, server, path):
        with closing(HTTPConnection('127.0.0.1', server.server_port)) as c:
            c.request('GET', path)
            r = c.getresponse()
            return r.status, json.loads(r.read() or b'null')

    def test_tour_route_binds_the_snapshot_and_reports_errors(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, db = linked_fixtures.LinkedBuildTests().fixture(root)
            manifest, _ = build_book(source, db, root / 'book')
            server = create_server(db, root / 'book', manifest, 0)
            (root / 'tour').mkdir()
            synthetic = Tour(Catalog(root / 'tour').db, NODES, set(), load(write(root, CURATION)))
            server.bundle.tour = lambda: synthetic
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                status, body = self.request(server, '/api/tour')
                self.assertEqual((status, body['kind'], body['snapshot']), (200, 'root', server.bundle.snapshot))
                self.assertEqual(self.request(server, '/api/tour?node=n13')[1]['title'], '第一步')
                self.assertEqual(self.request(server, '/api/tour?node=bad.seg'), (400, {'code': 'invalid_node'}))
                self.assertEqual(self.request(server, '/api/tour?node=n13&node=n15'), (400, {'code': 'invalid_node'}))
                self.assertEqual(self.request(server, '/api/tour?node=n999'), (404, {'code': 'node_missing'}))
                self.assertEqual(self.request(server, '/api/tour?node=n13&snapshot=old'), (409, {'code': 'snapshot_changed'}))
                self.assertEqual(self.request(server, '/api/tour-layout?node=ver_a3'), (200, {'status': 'missing', 'path': 'topics/step1.htm'}))
                self.assertEqual(self.request(server, '/api/tour-layout?node='), (200, {'status': 'empty'}))
                self.assertEqual(self.request(server, '/api/tour-layout?node=bad.seg'), (400, {'code': 'invalid_node'}))
                self.assertEqual(self.request(server, '/api/tour-layout?node=n13&node=n15'), (400, {'code': 'invalid_node'}))
                self.assertEqual(self.request(server, '/api/tour-layout?node=n999'), (404, {'code': 'node_missing'}))
                self.assertEqual(self.request(server, '/api/tour-layout?node=n13&snapshot=old'), (409, {'code': 'snapshot_changed'}))
            finally:
                server.shutdown(); server.server_close(); thread.join()


@unittest.skipUnless(catalog.DB.exists() and (BOOK / 'anna-reader.json').exists(), 'Build the dataset first')
class CorpusTests(unittest.TestCase):
    """The curated tour against the published corpus: every starting point names exactly one place in the book."""

    @classmethod
    def setUpClass(cls):
        reader = json.loads((BOOK / 'anna-reader.json').read_text())
        manifest = json.loads((BOOK / 'anna-manifest.json').read_text())
        cls.tour = Tour(catalog.DB, reader['nodes'], {k for k, v in manifest['entries'].items() if v['locatable']}, load())
        cls.db = catalog.connect()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def items(self, view, key):
        return [i for g in view['groups'] if g['key'] == key for i in g['items']]

    def test_every_curated_path_resolves(self):
        self.assertEqual(self.tour.unresolved, {})
        for key in self.tour.topics:
            with self.subTest(topic=key):
                self.assertEqual(self.tour.view(key)['status'], 'ok')

    def test_character_creation_walks_the_players_handbook_steps(self):
        view = self.tour.view('create')
        steps = self.items(view, 'contents')
        self.assertEqual([s['title'][:3] for s in steps], ['第一步', '第二步', '第三步', '第四步', '第五步'])
        self.assertTrue(all(g['ordered'] for g in view['groups'] if g['key'] == 'contents'))
        self.assertTrue(view['lead']['text'].startswith('下面是创建角色的步骤'))
        first = self.tour.view(steps[0]['id'])
        self.assertEqual([i['id'] for i in self.items(first, 'choices')], ['classes'])
        self.assertIn(('职业简介Class Overview', True), [(i['title'], i['table']) for i in self.items(first, 'outline')])
        self.assertEqual(first['sequence']['next']['id'], steps[1]['id'])

    def test_collections_count_what_anna_finds(self):
        classes = self.tour.view('classes')
        self.assertEqual(classes['anna']['count'], catalog.query(self.db, {'type': ['class']})['versions_total'])
        self.assertIn('合作内容分割线', [g['label'] for g in classes['groups'] if g['key'] == 'contents'])
        for row in self.items(self.tour.view('spells'), 'facet'):
            with self.subTest(level=row['title']):
                self.assertEqual(row['count'], catalog.query(self.db, {k: v for k, v in row['query'].items()})['versions_total'])
        conditions = self.tour.view('conditions')
        listed = [i['id'] for i in self.items(conditions, 'entries')] + [v['id'] for i in self.items(conditions, 'entries') for v in i['versions']]
        self.assertEqual(len(set(listed)), conditions['anna']['count'])

    def test_a_class_reaches_its_subclasses_and_spell_list(self):
        wizard = self.db.execute("SELECT id FROM entries WHERE type='class' AND ruleset='5r' AND name_en='Wizard'").fetchone()[0]
        view = self.tour.view(wizard)
        subclasses = self.items(view, 'subclasses')
        self.assertGreaterEqual(len(subclasses), 4)
        self.assertTrue(all(s['type'] == 'subclass' for s in subclasses))
        spells = self.items(view, 'spells')[0]
        self.assertEqual(spells['query'], {'type': ['spell'], 'f_spell_classes': ['法师'], 'ruleset': ['5r']})
        self.assertEqual(spells['count'], catalog.query(self.db, spells['query'])['versions_total'])
        self.assertEqual(view['trail'][0], '玩家手册2024')

    def test_a_detail_lays_out_its_own_words_and_an_index_stays_a_list(self):
        create = self.tour.view('create')
        self.assertEqual(create['shape'], 'detail')
        layout = self.tour.layout('create', paths.WEB)
        self.assertEqual(layout['status'], 'ok')
        self.assertIn('选择职业', layout['html'])
        self.assertEqual([self.tour.view(s['id'])['shape'] for s in self.items(create, 'contents')], ['index'] * 5)
        self.assertEqual({self.tour.view(k)['shape'] for k in ('classes', 'spells', 'monsters')}, {'index'})


if __name__ == '__main__':
    unittest.main()
