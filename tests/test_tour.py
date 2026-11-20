"""The tour: curated starting points over the book's own contents, page entries and relations."""
from contextlib import closing
from copy import deepcopy
from functools import partial
from http.client import HTTPConnection
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from unittest import mock

from anna import paths
from anna.presentation import catalog, library_tour
from anna.presentation.collections import BookCatalog, Capabilities, CollectionRegistry
from anna.presentation.library import parse_scope
from anna.presentation.library_tour import LibraryTour
from anna.presentation.tour import Tour, TourError, clip, first_sentence, load
from anna.server.app import _try_load_our5etools, create_server, load_bundle
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
    'schema': 2,
    'sections': [{'key': 'start', 'label': {'zh': '开始', 'en': 'Start'}, 'topics': [
        {'key': 'create', 'label': {'zh': '创建角色', 'en': 'Create'}, 'ruleset': '5r', 'contents': {'5echm': '玩家手册 / 第二章'}, 'sequence': True},
        {'key': 'wizard', 'label': {'zh': '法师', 'en': 'Wizard'}, 'contents': {'5echm': '本书速查 / 职业速查 / 法师'}},
        {'key': 'gone', 'label': {'zh': '缺失', 'en': 'Gone'}, 'contents': {'5echm': '没有 / 这一章'}},
        {'key': 'twin', 'label': {'zh': '重名', 'en': 'Twin'}, 'contents': {'5echm': '重名'}},
        {'key': 'spells', 'label': {'zh': '法术', 'en': 'Spells'}, 'query': {'type': ['spell']}},
        {'key': 'packs', 'label': {'zh': '资料包', 'en': 'Packs'}, 'dynamic': {'brew': 'packs_ours'}},
        {'key': 'elsewhere', 'label': {'zh': '别处', 'en': 'Elsewhere'}, 'contents': {'brew': '某书'}},
    ]}],
    'choices': {'5echm': {'玩家手册 / 第二章 / 第一步': ['wizard', 'packs'], '没有': ['create']}},
}
# A curation every level of the published fixture can answer without a full catalog behind it.
CONTENTS_ONLY = {'schema': 2, 'sections': [{'key': 'start', 'label': {'zh': '开始', 'en': 'Start'}, 'topics': [
    {'key': 'create', 'label': {'zh': '创建角色', 'en': 'Create'}, 'contents': {'5echm': '玩家手册 / 第二章'}}]}], 'choices': {}}


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


class Provider:
    """The parts of a catalog the library tour asks: entries, facets and their vocabulary."""

    ENTRIES = [('ver_e1', '护盾术 Shield', 'spell'), ('ver_b1', '法师 Wizard', 'class'), ('ver_c1', '防护学派 Abjurer', 'subclass')]

    def matching(self, params):
        types = params.get('type')
        return [e for e in self.ENTRIES if not types or e[2] in types]

    def meta(self):
        return {'types': {'spell': '法术', 'class': '职业', 'subclass': '子职'}, 'type_order': ['class', 'subclass', 'spell'], 'fields': {}}

    def capabilities(self):
        return Capabilities(conditions=frozenset({'type'}), facets=frozenset(), types=frozenset({'spell', 'class', 'subclass'}), sources=frozenset())

    def signature(self):
        return ('tour-fixture', id(self))

    def works(self):
        return []

    def entries(self, params, restrict=None):
        found = self.matching(params)
        rows = [{'id': i, 'name': n, 'name_zh': n.split(' ')[0], 'type': t, 'ruleset': '5r'} for i, n, t in found]
        return {'total': len(rows), 'versions_total': len(rows), 'rows': rows[:int(params.get('limit', ['150'])[0])]}

    def facets(self, params, restrict=None):
        found = self.matching({k: v for k, v in params.items() if k != 'type'})
        options = [{'value': t, 'label': t, 'count': sum(e[2] == t for e in found)} for t in ('class', 'spell')]
        return {'groups': [{'key': 'type', 'label': '类型', 'options': options}], 'total': len(found), 'scope_total': len(found), 'without': {}}


class Reader:
    """The book's contents as Stella's shelf reads them."""

    snapshot = 's-book'

    def navigation(self):
        return {'nodes': NODES, 'default_page': 'topics/q.htm', 'page_base': '/book/'}


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
        self.assertEqual(loaded['sections'][0]['topics'], ['create', 'wizard', 'gone', 'twin', 'spells', 'packs', 'elsewhere'])
        create = loaded['topics']['create']
        self.assertTrue(create.sequence)
        self.assertEqual((create.contents_in('5echm'), create.contents_in('brew')), ('玩家手册 / 第二章', None))
        self.assertEqual(loaded['topics']['packs'].dynamic_in('brew'), 'packs_ours')
        self.assertEqual(loaded['choices']['5echm']['玩家手册 / 第二章 / 第一步'], ['wizard', 'packs'])

    def test_malformed_curation_is_rejected_whole(self):
        topics = lambda c: c['sections'][0]['topics']
        self.rejected(lambda c: c.update(schema=1), 'schema')
        # Topic keys share the URL with contents nodes and entries.
        self.rejected(lambda c: topics(c)[0].update(key='n12'), "'n12'")
        self.rejected(lambda c: topics(c)[0].update(key='ver_a1'), "'ver_a1'")
        self.rejected(lambda c: topics(c).append(dict(topics(c)[1])), "'wizard' repeated")
        self.rejected(lambda c: topics(c)[0].update(label={'zh': '创建角色'}), "'create' label")
        self.rejected(lambda c: topics(c)[0].update(ruleset='2024'), "'create' ruleset")
        self.rejected(lambda c: topics(c)[0].update(facet='spell_level'), "'create' facet")
        self.rejected(lambda c: topics(c)[4].update(query={'type': 'spell'}), "'spells' query")
        self.rejected(lambda c: topics(c)[4].update(browse='unknown'), "'spells' browse")
        self.rejected(lambda c: topics(c)[4].update(facet='spell_level', browse='subclasses'), "'spells' browse")
        self.rejected(lambda c: topics(c)[1].pop('contents'), "'wizard' leads nowhere")
        # A chapter is named once per collection, by that collection's own contents.
        self.rejected(lambda c: topics(c)[1].update(contents='本书速查 / 职业速查 / 法师'), "'wizard' contents")
        self.rejected(lambda c: topics(c)[1].update(contents={'Bad Key': '法师'}), "'wizard' contents")
        self.rejected(lambda c: topics(c)[5].update(dynamic={'brew': ''}), "'packs' dynamic")
        self.rejected(lambda c: c['choices']['5echm'].update({'玩家手册': ['absent']}), "choices '5echm' '玩家手册'")
        self.rejected(lambda c: c['choices'].update({'5echm': ['create']}), "choices '5echm'")
        (self.root / 'broken.json').write_text('{')
        with self.assertRaises(TourError):
            load(self.root / 'broken.json')

    def test_browse_topic_loads_without_contents_or_facet(self):
        curation = deepcopy(CURATION)
        curation['sections'][0]['topics'].append({
            'key': 'subclasses', 'label': {'zh': '子职', 'en': 'Subclasses'},
            'query': {'type': ['subclass']}, 'browse': 'subclasses',
        })
        loaded = load(write(self.root, curation))
        self.assertEqual(loaded['topics']['subclasses'].browse, 'subclasses')

    def test_quoted_text_is_clipped_at_sentences(self):
        self.assertEqual(clip('短句。', 10), ('短句。', False))
        self.assertEqual(clip('第一句很长很长。第二句', 10), ('第一句很长很长。', True))
        self.assertEqual(clip('没有句号的一段很长很长的文字', 10), ('没有句号的一段很长很…', True))
        self.assertEqual(first_sentence('选择一个职业。职业细节见第三章。'), '选择一个职业。')
        # Table cells run on without a sentence end; they give no hint rather than a cut-off row.
        self.assertEqual(first_sentence('职业 喜好 主要属性 ' * 10), '')


class TourTests(unittest.TestCase):
    """The book's own levels: contents, page entries and relations, named by the book's segments."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.catalog = Catalog(root)
        self.tour = Tour(self.catalog.db, NODES, {'ver_b1'}, load(write(root, CURATION)), root / 'source')

    def items(self, view, key):
        return [i for g in view['groups'] if g['key'] == key for i in g['items']]

    def test_unresolved_paths_are_reported_not_guessed(self):
        # Only the paths this book names count; another collection's chapter is not this book's to resolve.
        self.assertEqual(self.tour.unresolved, {('topic', 'gone'): 'missing', ('topic', 'twin'): 'ambiguous', ('choices', '没有'): 'missing'})
        self.assertEqual(self.tour.resolve('玩家手册 / 第二章'), ('ok', '12'))
        self.assertEqual(self.tour.resolve('重名'), ('ambiguous', None))

    def test_a_divider_heads_the_books_after_it(self):
        core = self.tour.view('n10')
        self.assertEqual((core['title'], core['read']), ('核心规则', None))
        self.assertEqual([i['id'] for i in self.items(core, 'contents')], ['n11'])

    def test_a_step_leads_with_source_words_and_its_checklist_then_choices(self):
        step = self.tour.view('n13')
        self.assertEqual(step['lead'], {'text': '选择一个职业，写在角色卡上。职业细节见第三章。', 'truncated': False})
        self.assertEqual(step['sources'], ['玩家手册'])
        self.assertEqual([g['key'] for g in step['groups']], ['outline', 'choices'])
        # The choices are named here; the library tour answers them for the whole library.
        self.assertEqual(self.items(step, 'choices'), [{'id': 'wizard', 'kind': 'topic'}, {'id': 'packs', 'kind': 'topic'}])
        outline = self.items(step, 'outline')
        self.assertEqual([(i['id'], i['table'], i.get('hint')) for i in outline],
                         [('ver_a2', True, None), ('ver_a3', False, '把等级写在角色卡上。')])
        # The previous and next steps skip the divider between them.
        self.assertEqual(step['sequence'], {'previous': None, 'next': self.tour.contents_item('15')})
        self.assertEqual(self.tour.view('n15')['sequence']['previous']['id'], 'n13')
        # A section page entry is reading matter, not an Anna entry.
        self.assertIsNone(step['anna'])
        self.assertEqual(self.tour.view('n12')['read'], {'root': '12'})
        self.assertEqual(self.tour.below('12'), 2)

    def test_a_class_lists_its_parts_and_subclasses_in_its_own_book(self):
        page = self.tour.view('n3')
        self.assertEqual(page['anna'], {'entry': 'ver_b1', 'type': 'class'})
        self.assertEqual([i['id'] for i in self.items(page, 'outline')], ['ver_b2'])
        # A chapter's own page reads before the subpages below it.
        chapter = self.tour.view('n16')
        self.assertEqual([(g['key'], [i['id'] for i in g['items']]) for g in chapter['groups']],
                         [('outline', ['ver_b2']), ('contents', ['n17'])])
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
        shapes = {s: self.tour.view(s)['shape'] for s in ('n12', 'n13', 'ver_b1', 'ver_d1', 'ver_a2', 'ver_a3')}
        # Levels whose lead already says everything before their rows are indexes; leaves read as details.
        self.assertEqual(shapes, {'n12': 'index', 'n13': 'index', 'ver_b1': 'index', 'ver_d1': 'index', 'ver_a2': 'detail', 'ver_a3': 'detail'})
        parted = {'lead': {'text': '学术派奥术施法者。', 'truncated': True}, 'groups': [{'key': 'parts', 'items': []}]}
        self.assertEqual(Tour.shaped(parted)['shape'], 'detail')

    def test_a_levels_own_words_are_the_range_its_lead_quotes(self):
        for segment in ('n3', 'n13', 'ver_b1', 'ver_a3'):
            with self.subTest(segment=segment):
                self.assertEqual(self.tour.span_text(self.tour.own_span(segment)), self.tour.view(segment)['lead']['text'])
        # An entry's own words stop where its first part begins, and its own references are not links.
        self.assertEqual(self.tour.own_span('ver_b1'), ('topics/wizard.htm', WIZARD.index('\n', WIZARD.index('法师')), WIZARD.index('1级'), 'ver_b1'))
        self.assertIsNone(self.tour.own_span(''))
        self.assertEqual(self.tour.layout(''), {'status': 'empty'})
        self.assertEqual(self.tour.layout('ver_a3'), {'status': 'missing', 'path': 'topics/step1.htm'})

    def test_subclass_records_resolve_owners_and_disambiguate_shared_headings(self):
        # Add an unowned subclass and two generic-headed PHB-style subclasses sharing one heading in the same source.
        with closing(sqlite3.connect(self.catalog.db)) as conn:
            conn.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?)',
                         ('topics/evoc.htm', '', 'document', 'ok', '', ' \n奥术传承\n塑能说明。\n', '["src_phb"]', '{}'))
            conn.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?)',
                         ('topics/trans.htm', '', 'document', 'ok', '', ' \n奥术传承\n变化说明。\n', '["src_phb"]', '{}'))
            conn.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?)',
                         ('topics/misc.htm', '', 'document', 'ok', '', ' \n子职更新\n总述。\n', '["src_phb"]', '{}'))
            self.catalog.entry(conn, 'ver_c2', '奥术传承 Arcane Traditions', 'subclass', 'topics/evoc.htm', (2, 12), 6, primary=True, name_zh='奥术传承')
            self.catalog.entry(conn, 'ver_c3', '奥术传承 Arcane Traditions', 'subclass', 'topics/trans.htm', (2, 12), 6, primary=True, name_zh='奥术传承')
            self.catalog.entry(conn, 'ver_c4', '子职更新 Subclasses Update', 'subclass', 'topics/misc.htm', (2, 10), 6, primary=True, name_zh='子职更新')
            conn.execute("UPDATE entries SET name_en='Arcane Traditions' WHERE id IN ('ver_c2', 'ver_c3')")
            conn.commit()
        extra_nodes = [
            *NODES,
            node('18', '16', '塑能学派', 'topics/evoc.htm'),
            node('19', '16', '变化学派', 'topics/trans.htm'),
            node('24', '3', '塑能学派', 'topics/evoc.htm'),
            node('25', '3', '变化学派', 'topics/trans.htm'),
        ]
        tour = Tour(self.catalog.db, extra_nodes, {'ver_b1'}, load(write(Path(self.tmp.name), CURATION)), Path(self.tmp.name) / 'source')
        records = {r['id']: r for r in tour.subclass_records()}
        self.assertEqual(records['ver_c1']['owner_class'], '法师')
        self.assertEqual((records['ver_c2']['owner_class'], records['ver_c2']['title'], records['ver_c2']['name_en']), ('法师', '塑能学派', ''))
        self.assertEqual((records['ver_c3']['owner_class'], records['ver_c3']['title'], records['ver_c3']['name_en']), ('法师', '变化学派', ''))
        self.assertIsNone(records['ver_c4']['owner_class'])
        self.assertEqual(tour.view('ver_c2')['title'], '塑能学派')

    def test_unknown_and_malformed_nodes_are_explicit(self):
        # Topics belong to the library tour; the book names only its contents and entries.
        for segment, code, status in [('bad.seg', 'invalid_node', 400), ('N13', 'invalid_node', 400), ('create', 'invalid_node', 400),
                                      ('n999', 'node_missing', 404), ('ver_ffff', 'node_missing', 404)]:
            for ask in (self.tour.view, self.tour.layout):
                with self.subTest(segment=segment, ask=ask), self.assertRaises(TourError) as caught:
                    ask(segment)
                self.assertEqual((caught.exception.code, caught.exception.status), (code, status))


class LibraryTourTests(unittest.TestCase):
    """The first column, the topics and the shelf over the reader's library, the book's levels qualified."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        curation = write(root, CURATION)
        self.book = Tour(Catalog(root).db, NODES, {'ver_b1'}, load(curation), root / 'source')
        registry = CollectionRegistry(Provider(), book_tour=self.book, book_reader=Reader(), book_snapshot='s-book')
        self.tour = LibraryTour(registry, parse_scope({}, registry), curation)

    def items(self, view, key):
        return [i for g in view['groups'] if g['key'] == key for i in g['items']]

    def test_root_lists_the_topics_the_library_answers_then_the_shelf(self):
        root = self.tour.view('')
        self.assertEqual([g['key'] for g in root['groups']], ['section', 'shelf'])
        topics = {i['id']: (i['status'], i['count']) for i in self.items(root, 'section')}
        # A listing or a chapter only another collection holds does not show; the rest are answered here.
        self.assertEqual(topics, {'create': ('ok', 2), 'wizard': ('ok', 1), 'gone': ('missing', None), 'twin': ('ambiguous', None), 'spells': ('ok', 1)})
        shelf = self.items(root, 'shelf')
        self.assertEqual([(i['id'], i['kind'], i['title'], i['count']) for i in shelf],
                         [('5echm:n1', 'contents', '本书速查', 1), ('5echm:n10', 'shelf', '核心规则', 1), ('5echm:n20', 'shelf', '旧版内容', 3)])
        self.assertEqual((root['snapshot'], root['snapshots']), (self.tour.signature, {'5echm': 's-book'}))

    def test_a_shelf_group_lists_the_books_after_its_divider(self):
        core = self.tour.view('5echm:n10')
        self.assertEqual((core['title'], core['collection']), ('核心规则', '5echm'))
        self.assertEqual([i['id'] for i in self.items(core, 'contents')], ['5echm:n11'])
        old = self.tour.view('5echm:n20')
        self.assertEqual([i['id'] for i in self.items(old, 'contents')], ['5echm:n21', '5echm:n22', '5echm:n23'])

    def test_a_topic_opens_its_chapter_with_every_node_named_by_its_collection(self):
        view = self.tour.view('create')
        self.assertEqual((view['trail'], view['ruleset'], view['read'], view['collection']), (['玩家手册', '第二章'], '5r', {'root': '5echm:12'}, '5echm'))
        contents = [g for g in view['groups'] if g['key'] == 'contents']
        self.assertEqual([[i['id'] for i in g['items']] for g in contents], [['5echm:n13'], ['5echm:n15']])
        self.assertTrue(all(g['ordered'] for g in contents))
        self.assertEqual(contents[0]['items'][0]['hint'], '选择一个职业，写在角色卡上。')
        self.assertEqual((self.tour.view('twin')['status'], self.tour.view('twin')['groups']), ('ambiguous', []))

    def test_a_collection_level_is_qualified_and_its_topics_answered_for_the_library(self):
        step = self.tour.view('5echm:n13')
        self.assertEqual([i['id'] for i in self.items(step, 'outline')], ['5echm:ver_a2', '5echm:ver_a3'])
        # A topic the library cannot answer drops out of the choices.
        self.assertEqual([(i['id'], i['label']['zh'], i['count']) for i in self.items(step, 'choices')], [('wizard', '法师', 1)])
        self.assertEqual(step['sequence']['next']['id'], '5echm:n15')
        entry = self.tour.view('5echm:ver_b1')
        # Stella's address names the collection; Anna finds the entry by its own id.
        self.assertEqual((entry['id'], entry['read'], entry['anna']), ('5echm:ver_b1', {'entry': '5echm:ver_b1'}, {'entry': 'ver_b1', 'type': 'class'}))
        self.assertEqual([i['id'] for i in self.items(entry, 'subclasses')], ['5echm:ver_c1'])

    def test_a_query_topic_reads_as_anna_answers_the_library(self):
        spells = self.tour.view('spells')
        self.assertEqual(spells['anna'], {'query': {'type': ['spell']}, 'count': 1})
        self.assertEqual([(i['id'], i['type_label']) for i in self.items(spells, 'entries')], [('5echm:ver_e1', '法术')])

    def test_layouts_are_the_collections_own(self):
        self.assertEqual(self.tour.layout('5echm:ver_a3'), {'status': 'missing', 'path': 'topics/step1.htm'})
        for segment in ('', 'spells', 'gone', 'twin'):
            with self.subTest(segment=segment):
                self.assertEqual(self.tour.layout(segment), {'status': 'empty'})

    def test_subclasses_browse_drills_through_classes_to_subclass_entries(self):
        curation = deepcopy(CURATION)
        curation['sections'][0]['topics'].append({
            'key': 'subclasses', 'label': {'zh': '子职', 'en': 'Subclasses'},
            'query': {'type': ['subclass']}, 'browse': 'subclasses',
        })
        curation_path = write(Path(self.tmp.name), curation)
        registry = CollectionRegistry(Provider(), book_tour=self.book, book_reader=Reader(), book_snapshot='s-book')
        tour = LibraryTour(registry, parse_scope({}, registry), curation_path)

        # Column 1: owning classes are drillable levels, never query jump links.
        level1 = tour.view('subclasses')
        classes = self.items(level1, 'classes')
        self.assertEqual([(i['id'], i['kind'], i['title'], i['count']) for i in classes],
                         [('subclasses.wizard', 'class', '法师', 1)])

        # Column 2: wizard's subclasses are clickable entry levels that open directly in the tour.
        level2 = tour.view('subclasses.wizard')
        self.assertEqual((level2['id'], level2['kind'], level2['title']), ('subclasses.wizard', 'class', '法师'))
        subs = self.items(level2, 'subclasses')
        self.assertEqual([(i['id'], i['kind'], i['title'], i['ruleset']) for i in subs],
                         [('5echm:ver_c1', 'entry', '防护学派', '5r')])
        self.assertEqual(tour.layout('subclasses.wizard'), {'status': 'empty'})

        # Column 3: clicking a subclass opens its entry directly.
        level3 = tour.view(subs[0]['id'])
        self.assertEqual((level3['id'], level3['type']), ('5echm:ver_c1', 'subclass'))
        self.assertEqual([i['id'] for i in self.items(level3, 'class')], ['5echm:ver_b1'])

        # Library ruleset restriction filters the subclass index.
        only_5e = LibraryTour(registry, parse_scope({'rulesets': ['5e']}, registry), curation_path)
        with self.assertRaises(TourError) as missing:
            only_5e.view('subclasses.wizard')
        self.assertEqual((missing.exception.code, missing.exception.status), ('node_missing', 404))

    def test_unknown_and_malformed_levels_are_explicit(self):
        for segment, code, status in [('bad.seg', 'invalid_node', 400), ('5echm:bad.seg', 'invalid_node', 400), ('Bad:n1', 'invalid_node', 400),
                                      ('subclasses.', 'invalid_node', 400), ('subclasses.BAD!', 'invalid_node', 400),
                                      ('subclasses.nope', 'node_missing', 404),
                                      ('5echm:n999', 'node_missing', 404), ('brew:n1', 'node_missing', 404), ('nope', 'node_missing', 404),
                                      ('packs', 'node_missing', 404)]:
            with self.subTest(segment=segment), self.assertRaises(TourError) as caught:
                self.tour.view(segment)
            self.assertEqual((caught.exception.code, caught.exception.status), (code, status))
        with self.assertRaises(TourError) as caught:
            self.tour.layout('subclasses.nope')
        self.assertEqual((caught.exception.code, caught.exception.status), ('node_missing', 404))
        with self.assertRaises(TourError) as caught:
            self.tour.check('old')
        self.assertEqual((caught.exception.code, caught.exception.status), ('snapshot_changed', 409))

    def test_relations_only_entries_still_read_as_detail(self):
        leaf = {'lead': {'text': '简短特性正文。', 'truncated': False}, 'groups': [{'key': 'relations', 'items': [{'id': 'x'}]}]}
        parent = {'lead': {'text': '简短子职导语。', 'truncated': False}, 'groups': [{'key': 'parts', 'items': [{'id': 'y'}]}]}
        self.assertEqual(library_tour._shaped(leaf)['shape'], 'detail')
        self.assertEqual(library_tour._shaped(parent)['shape'], 'index')


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
            curation = write(root, CONTENTS_ONLY)
            synthetic = Tour(Catalog(root / 'tour').db, NODES, set(), load(curation), source)
            server.bundle.tour = lambda: synthetic
            server.bundle.book_reader.reader = {**server.bundle.book_reader.reader, 'nodes': NODES}
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                with mock.patch.object(library_tour, 'CURATION', curation):
                    status, body = self.request(server, '/api/tour')
                    self.assertEqual((status, body['kind'], body['snapshots']), (200, 'root', {'5echm': server.bundle.snapshot}))
                    signature = body['snapshot']
                    self.assertEqual(self.request(server, '/api/tour?at=5echm:n13')[1]['title'], '第一步')
                    self.assertEqual(self.request(server, '/api/tour?at=create')[1]['read'], {'root': '5echm:12'})
                    self.assertEqual(self.request(server, '/api/tour?at=5echm:bad.seg'), (400, {'code': 'invalid_node'}))
                    self.assertEqual(self.request(server, '/api/tour?at=5echm:n13&at=5echm:n15'), (400, {'code': 'invalid_node'}))
                    self.assertEqual(self.request(server, '/api/tour?at=5echm:n999'), (404, {'code': 'node_missing'}))
                    self.assertEqual(self.request(server, f'/api/tour?at=5echm:n13&snapshot={signature}')[0], 200)
                    self.assertEqual(self.request(server, '/api/tour?at=5echm:n13&snapshot=old'), (409, {'code': 'snapshot_changed'}))
                    self.assertEqual(self.request(server, '/api/tour-layout?at=5echm:ver_a3'), (200, {'status': 'missing', 'path': 'topics/step1.htm'}))
                    self.assertEqual(self.request(server, '/api/tour-layout?at='), (200, {'status': 'empty'}))
                    self.assertEqual(self.request(server, '/api/tour-layout?at=5echm:bad.seg'), (400, {'code': 'invalid_node'}))
                    self.assertEqual(self.request(server, '/api/tour-layout?at=5echm:n999'), (404, {'code': 'node_missing'}))
                    self.assertEqual(self.request(server, '/api/tour-layout?at=5echm:n13&snapshot=old'), (409, {'code': 'snapshot_changed'}))
                    # The reader's library decides who answers; a library naming nothing the server reads falls back to the book.
                    self.assertEqual(self.request(server, '/api/tour?libraries=ghost')[0], 200)
                    self.assertEqual(self.request(server, '/api/tour?libraries=Bad'), (400, {'error': '无效资料库'}))
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_refresh_reads_revised_topics_and_choices_without_rebuilding_the_book(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, db = linked_fixtures.LinkedBuildTests().fixture(root)
            manifest, _ = build_book(source, db, root / 'book')
            server = create_server(db, root / 'book', manifest, 0)
            first_node = server.bundle.reader['nodes'][0]
            selected = '5echm:n' + first_node['id']
            curation = deepcopy(CONTENTS_ONLY)
            topic = curation['sections'][0]['topics'][0]
            topic['contents'] = {'5echm': first_node['label']}
            curation['choices'] = {'5echm': {first_node['label']: ['create']}}
            path = write(root, curation)
            book_tour = partial(server.bundle.tours.get, curation_path=path)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                with (mock.patch.object(library_tour, 'CURATION', path),
                      mock.patch.object(server.bundle.tours, 'get', book_tour)):
                    def view(at=''):
                        status, body = self.request(server, '/api/tour?at=' + at)
                        self.assertEqual(status, 200)
                        return body

                    old_root, old_topic, old_node = view(), view('create'), view(selected)
                    root_item = lambda level: level['groups'][0]['items'][0]
                    self.assertEqual(root_item(old_root)['label']['zh'], '创建角色')
                    self.assertEqual(old_topic['label']['zh'], '创建角色')
                    self.assertEqual([i['id'] for g in old_node['groups'] if g['key'] == 'choices' for i in g['items']],
                                     ['create'])

                    topic['label']['zh'] = '创建你的角色'
                    curation['choices']['5echm'].clear()
                    write(root, curation)

                    # A browser reload fetches the same addresses from the same server and published book.
                    new_root, new_topic, new_node = view(), view('create'), view(selected)
                    self.assertEqual(new_root['snapshots'], old_root['snapshots'])
                    self.assertEqual(root_item(new_root)['label']['zh'], '创建你的角色')
                    self.assertEqual(new_topic['label']['zh'], '创建你的角色')
                    self.assertEqual(new_topic['read'], old_topic['read'])
                    self.assertNotIn('choices', [g['key'] for g in new_node['groups']])
            finally:
                server.shutdown(); server.server_close(); thread.join()


@unittest.skipUnless(catalog.DB.exists() and (BOOK / 'anna-reader.json').exists(), 'Build the dataset first')
class CorpusTests(unittest.TestCase):
    """The curated tour against the published corpus: every starting point names exactly one place in each book it names."""

    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(paths.WEB, catalog.DB, BOOK)
        cls.our5etools = _try_load_our5etools(paths.OUR5ETOOLS_DATA)
        cls.registry = CollectionRegistry(BookCatalog(cls.bundle.db, cls.bundle.source), (cls.our5etools,) if cls.our5etools else (),
                                          book_tour=cls.bundle.book_tour, book_reader=cls.bundle.book_reader, book_snapshot=cls.bundle.snapshot)
        cls.tour = LibraryTour(cls.registry, parse_scope({}, cls.registry))
        cls.db = catalog.connect()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def items(self, view, key):
        return [i for g in view['groups'] if g['key'] == key for i in g['items']]

    def test_every_curated_path_resolves(self):
        self.assertEqual(self.bundle.tour().unresolved, {})
        for key in self.tour.topics:
            with self.subTest(topic=key):
                if self.tour.available(self.tour.topics[key]):
                    self.assertEqual(self.tour.view(key)['status'], 'ok')

    @unittest.skipUnless(paths.OUR5ETOOLS_DATA.joinpath('manifest.json').exists(), 'Build our5etools first')
    def test_every_curated_our5etools_path_resolves(self):
        curation = load()
        for key, topic in curation['topics'].items():
            if topic.contents_in('our5etools'):
                with self.subTest(topic=key):
                    self.assertEqual(self.our5etools.tour.resolve(topic.contents_in('our5etools'))[0], 'ok')
        for where in curation['choices'].get('our5etools', {}):
            with self.subTest(choices=where):
                self.assertEqual(self.our5etools.tour.resolve(where)[0], 'ok')

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

    def test_a_class_reaches_its_subclasses_and_spell_list(self):
        wizard = self.db.execute("SELECT id FROM entries WHERE type='class' AND ruleset='5r' AND name_en='Wizard'").fetchone()[0]
        view = self.tour.view('5echm:' + wizard)
        subclasses = self.items(view, 'subclasses')
        self.assertGreaterEqual(len(subclasses), 4)
        self.assertTrue(all(s['type'] == 'subclass' and s['id'].startswith('5echm:') for s in subclasses))
        spells = self.items(view, 'spells')[0]
        self.assertEqual(spells['query'], {'type': ['spell'], 'f_spell_classes': ['法师'], 'ruleset': ['5r']})
        self.assertEqual(spells['count'], catalog.query(self.db, spells['query'])['versions_total'])
        self.assertEqual(view['trail'][0], '玩家手册2024')

    def test_a_detail_lays_out_its_own_words_and_an_index_stays_a_list(self):
        create = self.tour.view('create')
        self.assertEqual(create['shape'], 'detail')
        layout = self.tour.layout('create')
        self.assertEqual(layout['status'], 'ok')
        self.assertIn('选择职业', layout['html'])
        self.assertEqual([self.tour.view(s['id'])['shape'] for s in self.items(create, 'contents')], ['index'] * 5)
        self.assertEqual({self.tour.view(k)['shape'] for k in ('classes', 'spells', 'monsters')}, {'index'})

    @unittest.skipUnless(paths.OUR5ETOOLS_DATA.joinpath('manifest.json').exists(), 'Build our5etools first')
    def test_a_library_of_both_collections_tours_them_as_one(self):
        both = LibraryTour(self.registry, parse_scope({'libraries': ['5echm,our5etools']}, self.registry))
        root = both.view('')
        sections = [g['section'] for g in root['groups'] if g['key'] == 'section']
        self.assertIn('packages', sections)
        shelf = self.items(root, 'shelf')
        self.assertEqual(shelf[0]['id'], '5echm:n0')
        self.assertTrue(any(i.get('collection') == 'our5etools' and i['kind'] == 'shelf' for i in shelf))
        # A chapter opens in the text the reader leads with.
        self.assertEqual(both.view('create')['collection'], '5echm')
        phb = next(w['id'] for w in self.our5etools.provider.works() if w.get('reader_root') == 'n_book_xphb')
        led = LibraryTour(self.registry, parse_scope({'libraries': ['5echm,our5etools'], 'lead': ['our5etools:' + phb]}, self.registry))
        self.assertEqual(led.view('create')['collection'], 'our5etools')
        packs = both.view('packs-ours')
        self.assertTrue(all(i['id'].startswith('our5etools:pack.') for i in self.items(packs, 'packages')))

    def test_subclasses_drill_down_in_the_tour_across_collections(self):
        # 5echm alone: classes are drillable and wizard subclasses open directly in the tour.
        sub_root = self.tour.view('subclasses')
        classes = self.items(sub_root, 'classes')
        self.assertTrue(all(c['kind'] == 'class' and c['id'].startswith('subclasses.') for c in classes))
        self.assertIn('subclasses.wizard', [c['id'] for c in classes])
        wiz = self.tour.view('subclasses.wizard')
        wiz_items = self.items(wiz, 'subclasses')
        self.assertTrue(all(i['kind'] == 'entry' and i['id'].startswith('5echm:') for i in wiz_items))
        titles = [i['title'] for i in wiz_items]
        self.assertIn('塑能师', titles)
        self.assertIn('塑能学派', titles)
        self.assertNotIn('奥术传承', titles)
        evoker = next(i for i in wiz_items if i['title'] == '塑能师')
        evoker_view = self.tour.view(evoker['id'])
        self.assertGreaterEqual(len(self.items(evoker_view, 'parts')), 4)

        if not paths.OUR5ETOOLS_DATA.joinpath('manifest.json').exists():
            return
        # our5etools alone: subclass entries show real intro lead, level parts, and deduplicated parent class.
        only_o5e = LibraryTour(self.registry, parse_scope({'libraries': ['our5etools']}, self.registry))
        o5e_wiz = only_o5e.view('subclasses.wizard')
        self.assertIsNotNone(o5e_wiz['anna'])
        self.assertEqual(o5e_wiz['anna']['query'], {'type': ['subclass'], 'f_owner_class': ['法师']})
        o5e_evoker = next(i for i in self.items(o5e_wiz, 'subclasses') if i['title'] == '塑能师')
        o5e_ev_view = only_o5e.view(o5e_evoker['id'])
        self.assertIn('元素力量', o5e_ev_view['lead']['text'])
        self.assertGreaterEqual(len(self.items(o5e_ev_view, 'parts')), 5)
        self.assertEqual(len(self.items(o5e_ev_view, 'class')), 1)
        self.assertEqual(only_o5e.layout(o5e_evoker['id'])['status'], 'ok')
        # A short subclass feature reads as a detail (no circular parent links in relations) with its owner trail.
        sculpt = next(p for p in self.items(o5e_ev_view, 'parts') if '法术塑形' in p['title'])
        sculpt_view = only_o5e.view(sculpt['id'])
        self.assertEqual(sculpt_view['shape'], 'detail')
        self.assertEqual(sculpt_view['trail'], ['职业能力', '法师', '塑能师'])
        self.assertEqual(self.items(sculpt_view, 'relations'), [])
        self.assertEqual(only_o5e.layout(sculpt['id'])['status'], 'ok')
        # Navigating from a subclass up to its parent class in our5etools exposes its parts, subclasses and spells.
        o5e_class = only_o5e.view(self.items(o5e_ev_view, 'class')[0]['id'])
        self.assertGreaterEqual(len(self.items(o5e_class, 'parts')), 10)
        self.assertGreaterEqual(len(self.items(o5e_class, 'subclasses')), 4)
        self.assertEqual(len(self.items(o5e_class, 'spells')), 1)

        # Combined library merges both collections and stacks same-name subclasses with version links.
        both = LibraryTour(self.registry, parse_scope({'libraries': ['5echm,our5etools']}, self.registry))
        both_wiz = both.view('subclasses.wizard')
        both_items = self.items(both_wiz, 'subclasses')
        combined_evoker = next(i for i in both_items if i['title'] == '塑能师')
        self.assertTrue(combined_evoker['id'].startswith('5echm:'))
        self.assertTrue(any(v['id'].startswith('our5etools:') for v in combined_evoker['versions']))
        # Disabling stacking lists each version as its own row.
        unstacked = LibraryTour(self.registry, parse_scope({'libraries': ['5echm,our5etools'], 'versions': ['each']}, self.registry))
        self.assertGreater(len(self.items(unstacked.view('subclasses.wizard'), 'subclasses')), len(both_items))
        # Official-only content scope excludes community subclasses.
        official = LibraryTour(self.registry, parse_scope({'libraries': ['5echm,our5etools'], 'content': ['official']}, self.registry))
        self.assertEqual([g['label']['zh'] for g in official.view('subclasses.wizard')['groups']], ['官方'])


if __name__ == '__main__':
    unittest.main()
