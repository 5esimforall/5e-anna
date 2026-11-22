"""The reader's library: scope parsing, one table over several collections, and the HTTP surface.

Two copies of the frozen presentation fixture stand in for two collections. They share entry IDs, so every
answer must say which collection a version came from, and the reader's order decides which one leads."""
from contextlib import closing, contextmanager
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import anna.catalog.views as browse
from anna.presentation import catalog
from anna.presentation.alignment import align, title_key
from anna.presentation.collections import BookCatalog, Capabilities, Collection, CollectionRegistry, DEFAULT
from anna.presentation.expression import Query
from anna.presentation.library import Library, Scope, answers, localize, merge_order, parse_scope, describe_works, relations

from test_presentation_contracts import endpoint, fixture

SHIELD = 'ver_9362521b9c94f0358d220f29'      # 护盾术 Shield, 5e, PHB14
WIND = 'ver_958337258cbdf07fb3fe6c4f'        # 回气 Second Wind, 5r class feature, PHB24
PHB14, PHB24 = 'src_a00c764955b18f32fb27c71a', 'src_f473a607f72c5a164e63506e'


def catalog_copy(root, name, data, edit=None):
    path = Path(root) / (name + '.sqlite')
    with closing(sqlite3.connect(path)) as db:
        db.executescript('\n'.join(data['sql']))
        if edit:
            edit(db)
            db.commit()
    return BookCatalog(path, Path(root))


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture('catalog')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        book = catalog_copy(self.tmp.name, 'book', self.data)
        demo = catalog_copy(self.tmp.name, 'demo', self.data)
        self.registry = CollectionRegistry(book, (Collection('demo', '测试资料库', 'Fixture collection', demo),
                                                  Collection('reserved-demo', '预留测试库', 'Reserved fixture')))

    def library(self, **params):
        params = {k: v if isinstance(v, list) else [v] for k, v in params.items()}
        scope = parse_scope(params, self.registry)
        return Library(self.registry, scope), params

    # --- scope ---

    def test_scope_defaults_to_the_book_and_names_what_it_leaves_out(self):
        library, params = self.library(q='Shield')
        self.assertEqual(library.scope, Scope((DEFAULT,)))
        self.assertEqual(params, {'q': ['Shield']})
        scope = parse_scope({'libraries': ['demo,reserved-demo,ghost'], 'rulesets': ['5e,5r,待核'], 'content': ['official']}, self.registry)
        self.assertEqual(scope.libraries, ('demo',))
        self.assertEqual(scope.unavailable, ('reserved-demo', 'ghost'))
        self.assertIsNone(scope.rulesets)  # every generation is the same as no restriction
        self.assertEqual(scope.content, frozenset({'official'}))
        self.assertTrue(scope.narrowed)
        # Nothing the server can open falls back to the book, and says so.
        fallback = parse_scope({'libraries': ['reserved-demo']}, self.registry)
        self.assertEqual((fallback.libraries, fallback.fallback), ((DEFAULT,), True))

    def test_scope_rejects_malformed_choices(self):
        for params in ({'rulesets': ['5x']}, {'rulesets': ['']}, {'content': ['homebrew']}, {'versions': ['all']},
                       {'prefer': ['4e']}, {'hide': ['no-colon']}, {'hide': ['Bad:src']}, {'libraries': ['../x']},
                       {'libraries': ['demo', '5echm']}, {'lead': ['demo:']}, {'versions': ['each', 'stacked']}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                parse_scope(dict(params), self.registry)

    def test_scope_deduplicates_libraries_in_priority_order_and_keeps_query_conditions(self):
        library, params = self.library(libraries='demo,5echm,demo', lead='demo:'+PHB24, q='Shield')
        self.assertEqual(library.scope.libraries, ('demo', DEFAULT))
        self.assertEqual(library.scope.lead, frozenset({('demo', PHB24)}))
        self.assertEqual(params, {'q': ['Shield']})

    def test_merge_order_keeps_the_first_order_and_places_newcomers_after_their_predecessor(self):
        self.assertEqual(merge_order([['name', 'cr', 'sources', 'ruleset'], ['name', 'cr', 'lair', 'sources', 'ruleset'], ['x']]),
                         ['name', 'cr', 'lair', 'sources', 'ruleset', 'x'])

    # --- one table over two collections ---

    def test_same_name_versions_stack_across_collections_in_the_readers_order(self):
        library, params = self.library(libraries='5echm,demo')
        result = library.entries(params)
        self.assertEqual((result['total'], result['versions_total']), (3, 4))
        shield = next(r for r in result['rows'] if r['id'] == SHIELD)
        self.assertEqual(shield['collection'], DEFAULT)
        self.assertEqual([(v['collection'], v['id']) for v in shield['versions']], [(DEFAULT, SHIELD), ('demo', SHIELD)])
        self.assertEqual(shield['stack']['status'], 'library_candidate')
        # A class feature is scoped by its class, so the two collections' copies stay apart.
        winds = [r for r in result['rows'] if r['id'] == WIND]
        self.assertEqual(sorted(r['collection'] for r in winds), [DEFAULT, 'demo'])
        self.assertTrue(all(r['stack'] is None for r in winds))
        # Putting the other collection first lets its version lead the row.
        library, params = self.library(libraries='demo,5echm')
        shield = next(r for r in library.entries(params)['rows'] if r['id'] == SHIELD)
        self.assertEqual([v['collection'] for v in shield['versions']], ['demo', DEFAULT])
        # Without deduplication every version is its own row.
        library, params = self.library(libraries='5echm,demo', versions='each')
        result = library.entries(params)
        self.assertEqual((result['total'], result['versions']), (4, 'each'))
        self.assertTrue(all('versions' not in r for r in result['rows']))

    def test_paging_reads_one_order_and_rows_keep_their_collection(self):
        library, params = self.library(libraries='5echm,demo', versions='each', sort='name')
        whole = [(r['collection'], r['id']) for r in library.entries(params)['rows']]
        pages = []
        for offset in range(4):
            pages.extend((r['collection'], r['id']) for r in library.entries({**params, 'offset': [str(offset)], 'limit': ['1']})['rows'])
        self.assertEqual(pages, whole)
        self.assertEqual(len(set(whole)), 4)
        with self.assertRaises(ValueError):
            library.entries({**params, 'sort': ['rarity']})

    def test_rules_kinds_of_content_and_hidden_works_bound_every_answer(self):
        library, params = self.library(libraries='5echm,demo', rulesets='5e')
        result = library.entries(params)
        self.assertEqual((result['total'], result['versions_total']), (1, 2))
        self.assertTrue(all(v['ruleset'] == '5e' for r in result['rows'] for v in r['versions']))
        facets = library.facets(params)
        self.assertEqual(facets['total'], 2)
        self.assertEqual({o['value']: o['count'] for o in next(g for g in facets['groups'] if g['key'] == 'type')['options']},
                         {'class_feature': 0, 'spell': 2})
        library, params = self.library(libraries='5echm,demo', content='partnered')
        self.assertEqual(library.entries(params)['total'], 0)
        library, params = self.library(libraries='5echm,demo', hide=['demo:' + PHB14])
        shield = next(r for r in library.entries(params)['rows'] if r['id'] == SHIELD)
        self.assertEqual([v['collection'] for v in shield['versions']], [DEFAULT])
        # A single collection narrowed alone answers the same way.
        library, params = self.library(rulesets='5r')
        self.assertEqual([r['id'] for r in library.entries(params)['rows']], [WIND])

    def test_conditions_another_collection_cannot_answer_hold_for_none_of_its_entries(self):
        # A collection holding no class features cannot answer a class-feature query; it adds nothing.
        spells = catalog_copy(self.tmp.name, 'spells', self.data, lambda db: db.execute("DELETE FROM entries WHERE type='class_feature'"))
        self.registry = CollectionRegistry(self.registry.book, (Collection('spells', '法术库', 'Spells', spells),))
        library, params = self.library(libraries='5echm,spells')
        self.assertEqual(library.entries(params)['total'], 2)
        result = library.entries({**params, 'type': ['class_feature']})
        self.assertEqual([(r['collection'], r['id']) for r in result['rows']], [(DEFAULT, WIND)])
        # Excluding what a collection cannot hold excludes nothing from it.
        result = library.entries({**params, 'not_type': ['class_feature']})
        self.assertEqual([[v['collection'] for v in r['versions']] for r in result['rows']], [[DEFAULT, 'spells']])
        self.assertEqual(library.entries({**params, 'source': ['src:XPHB']})['total'], 0)
        self.assertEqual(library.entries({**params, 'source': [PHB14, 'src:XPHB']})['total'], 1)
        with self.assertRaisesRegex(ValueError, '未知条目类型'):
            library.entries({**params, 'type': ['dragon']})
        # Counts set aside the condition a collection cannot answer only for that condition's own field; removing the
        # type card (the line's first) brings back the other collection's spells.
        facets = library.facets({**params, 'type': ['class_feature']})
        types = {o['value']: o['count'] for o in next(g for g in facets['groups'] if g['key'] == 'type')['options']}
        self.assertEqual(types, {'class_feature': 1, 'spell': 2})
        self.assertEqual((facets['total'], facets['scope_total'], facets['without']), (1, 1, {'0': 2}))
        # Under ∨ the card a collection cannot answer drops out of its alternatives, not out of the query: the class
        # feature comes from the book, Shield from both collections.
        either = Query.parse([('type', 'class_feature'), ('or', ''), ('q', '护盾术')])
        result = library.entries(either)
        self.assertEqual(sorted((r['id'], [v['collection'] for v in r['versions']]) for r in result['rows']),
                         sorted([(WIND, [DEFAULT]), (SHIELD, [DEFAULT, 'spells'])]))
        facets = library.facets(either)
        self.assertEqual((facets['total'], facets['without']), (3, {}))
        probe = library.facets(Query.parse([('type', 'class_feature'), ('or', ''), ('probe', '')]))
        types = {o['value']: o['count'] for o in next(g for g in probe['groups'] if g['key'] == 'type')['options']}
        self.assertEqual((probe['probe_base'], types), (1, {'class_feature': 1, 'spell': 3}))
        # A single collection that cannot answer at all returns an empty table rather than an error.
        library, params = self.library(libraries='spells', type='class_feature')
        self.assertEqual(library.entries(params)['total'], 0)

    def test_localize_keeps_each_card_in_place_with_the_values_a_collection_holds(self):
        caps = self.registry.get(DEFAULT).provider.capabilities()
        every = caps.conditions | {'package', 'copy_status'}
        local = localize({'type': ['spell'], 'q': ['盾'], 'sort': ['name'], 'not_copy_status': ['ok'],
                          'origin': ['社区']}, caps, every, caps.types)
        # Every card keeps its place, so counts still name it; what this collection cannot hold reads as absent.
        self.assertEqual([(c.index, c.key, c.values) for c in local.cards()],
                         [(0, 'type', ('spell',)), (1, 'q', ('盾',)), (2, "not_copy_status", (browse.NONE,)), (3, "origin", (browse.NONE,))])
        self.assertEqual(dict(local), {'type': ['spell'], 'q': ['盾'], 'sort': ['name']})
        # An included card left without values holds for nothing; an excluded one excludes nothing.
        self.assertFalse(answers(local))
        self.assertTrue(answers(localize({'not_copy_status': ['ok'], 'q': ['盾']}, caps, every, caps.types)))
        # Under ∨ the card another collection cannot answer drops out of its alternatives.
        either = localize(Query.parse([('origin', '社区'), ('or', ''), ('q', '盾')]), caps, every, caps.types)
        self.assertTrue(answers(either))

    def test_facets_merge_options_and_counts_across_collections(self):
        library, params = self.library(libraries='5echm,demo')
        facets = library.facets({**params, 'type': ['spell']})
        types = {o['value']: o['count'] for o in next(g for g in facets['groups'] if g['key'] == 'type')['options']}
        self.assertEqual(types, {'class_feature': 2, 'spell': 2})
        self.assertEqual((facets['total'], facets['scope_total'], facets['without']), (2, 2, {'0': 2}))
        sources = next(g for g in facets['groups'] if g['key'] == 'source')['options']
        # The two collections share work IDs here; each option still names its collection.
        self.assertEqual({o['collection'] for o in sources}, {DEFAULT})
        self.assertEqual(facets['schema']['default_sort'], 'spell_level')

    def test_detail_opens_the_readers_first_holder_and_compares_versions_across_collections(self):
        library, _ = self.library(libraries='demo,5echm')
        entry = library.detail(SHIELD)
        self.assertEqual(entry['collection'], 'demo')
        self.assertEqual(entry['versions']['status'], 'library_candidate')
        self.assertEqual([m['collection'] for m in entry['versions']['members']], ['demo', DEFAULT])
        self.assertFalse(any(f['differs'] for f in entry['versions']['fields']))
        # Stella needs the owner even when only one collection answers.
        library, _ = self.library()
        self.assertEqual(library.detail(SHIELD), self.registry.book.detail(SHIELD) | {'collection': DEFAULT})
        with self.assertRaises(LookupError):
            self.library(libraries='5echm,demo')[0].detail('ver_missing')

    def test_export_lists_every_version_with_its_collection(self):
        library, params = self.library(libraries='5echm,demo')
        body, key = library.export({**params, 'type': ['spell']})
        self.assertEqual(key, 'library')
        rows = list(csv.reader(io.StringIO(body.decode('utf-8-sig'))))
        self.assertEqual(rows[0][:3], ['资料库', '版本ID', '中文名'])
        self.assertEqual(sorted((r[0], r[1]) for r in rows[1:]), [(DEFAULT, SHIELD), ('demo', SHIELD)])
        self.assertEqual({r[rows[0].index('来源性质')] for r in rows[1:]}, {'官方'})

    def test_works_describe_origin_rules_and_counts_for_the_settings(self):
        (book,) = describe_works(self.registry, [DEFAULT])
        self.assertEqual(book['id'], DEFAULT)
        works = {w['id']: w for w in book['works']}
        self.assertEqual(works[PHB14]['content'], 'official')
        self.assertEqual((works[PHB14]['count'], works[PHB24]['count']), (1, 1))


class HomebrewCatalog:
    """An in-memory catalog shaped like our5etools: official and homebrew works, copies that name the entry they
    were copied from, and own groups that hold recorded reprints only."""

    def __init__(self, name, works, entries):
        self.name, self.works_, self.entries_ = name, works, entries
        self.origin = {w['id']: w['origin'] for w in works}

    def works(self):
        return [{'label': w['id'], 'name': w['id'], 'ruleset': '5e', 'group': '', 'count': 1, 'codes': [], 'titles': [],
                 'abbreviations': []} | w for w in self.works_]

    def _row(self, e):
        return {'id': e['id'], 'type': e['type'], 'name': e['name_en'], 'name_zh': e['name_en'], 'name_en': e['name_en'],
                'ruleset': e.get('ruleset', '5e'), 'source_ids': [e['work']], 'source_names': [e['work']], 'fields': {},
                'field_conflicts': [], 'quickref': {}, 'legacy': 'unmarked', 'origin': self.origin[e['work']]}

    def ranked(self, params, restrict=None, sort_field=None):
        types = params.get('type')
        out = []
        for e in self.entries_:
            if types and e['type'] not in types:
                continue
            if restrict and restrict.sources is not None and e['work'] not in restrict.sources:
                continue
            base = next((b for b in self.entries_ if b['id'] == e.get('base')), None)
            out.append({'id': e['id'], 'type': e['type'], 'name': e['name_en'], 'name_zh': e['name_en'], 'name_en': e['name_en'],
                        'ruleset': e.get('ruleset', '5e'), 'stack': e.get('group'), 'value': None, 'source_key': (e['work'],),
                        'origin': self.origin[e['work']], 'work': e['work'], 'base': e.get('base'),
                        'base_work': base and base['work'], 'base_name_en': base and base['name_en']})
        return out

    def rows(self, ids):
        return {e['id']: self._row(e) for e in self.entries_ if e['id'] in ids}

    def detail(self, entry_id):
        return next(self._row(e) | {'versions': None, 'body': ''} for e in self.entries_ if e['id'] == entry_id)

    def schema(self, types, widened=False):
        return browse.schema_for(types, widened)

    def meta(self):
        return {'type_order': ['spell', 'magic_item'], 'types': {}, 'fields': {}, 'sources': [], 'packages': []}

    def capabilities(self):
        return Capabilities(conditions=frozenset({'type'}), facets=frozenset(), types=frozenset({'spell', 'magic_item'}),
                            sources=frozenset(self.origin), stackable=frozenset({'spell', 'magic_item'}), same_name_groups=False)

    def signature(self):
        return ('homebrew', self.name, id(self))


class LibraryRelationTests(unittest.TestCase):
    """Same-name versions share a row whatever their kind of content; each names the evidence relating it to the
    version shown: the same publication elsewhere, a copy, or the name alone."""

    def setUp(self):
        data = fixture('catalog')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        book = catalog_copy(self.tmp.name, 'book', data)
        # A book that publishes the Saddlebag as a partnered work, and a homebrew site that keeps it as community
        # content beside its own copy of the Player's Handbook and two homebrew works.
        tavern = HomebrewCatalog('tavern', [{'id': 'saddle', 'origin': '合作', 'abbreviations': ['TGS2']}],
                                 [{'id': 'tavern-cloak', 'type': 'magic_item', 'name_en': 'Mimicloak', 'work': 'saddle'}])
        brew = HomebrewCatalog('brew', [
            {'id': 'src:PHB', 'origin': '官方', 'abbreviations': ['PHB']},
            {'id': 'src:TGS2', 'origin': '社区', 'abbreviations': ['TGS2']},
            {'id': 'src:Alt', 'origin': '社区', 'abbreviations': ['ALTW']},
        ], [
            {'id': 'brew-shield', 'type': 'spell', 'name_en': 'Shield', 'work': 'src:PHB'},
            {'id': 'alt-shield', 'type': 'spell', 'name_en': 'Shield', 'work': 'src:Alt', 'base': 'brew-shield'},
            {'id': 'brew-cloak', 'type': 'magic_item', 'name_en': 'Mimicloak', 'work': 'src:TGS2'},
            {'id': 'alt-cloak', 'type': 'magic_item', 'name_en': 'Mimicloak', 'work': 'src:Alt'},
        ])
        self.registry = CollectionRegistry(book, (Collection('tavern', '酒馆', 'Tavern', tavern), Collection('brew', '家酿站', 'Brew', brew)))

    def library(self, **params):
        params = {k: v if isinstance(v, list) else [v] for k, v in params.items()}
        return Library(self.registry, parse_scope(params, self.registry)), params

    def row(self, name, **params):
        library, params = self.library(**params)
        return next(r for r in library.entries(params)['rows'] if r['name_en'] == name)

    @staticmethod
    def related(row):
        return [(v['collection'], v['id'], v['relation']) for v in row['versions']]

    def test_homebrew_shares_the_row_and_names_its_evidence(self):
        shield = self.row('Shield', libraries='5echm,brew')
        self.assertEqual(self.related(shield), [(DEFAULT, SHIELD, None), ('brew', 'brew-shield', 'translation'), ('brew', 'alt-shield', 'derived')])
        cloak = self.row('Mimicloak', libraries='tavern,brew')
        self.assertEqual(self.related(cloak), [('tavern', 'tavern-cloak', None), ('brew', 'brew-cloak', 'translation'), ('brew', 'alt-cloak', 'candidate')])
        # Without deduplication every version keeps its own row.
        library, params = self.library(libraries='tavern,brew', versions='each')
        self.assertEqual(library.entries({**params, 'type': ['magic_item']})['total'], 3)

    def test_one_publication_ranks_as_its_most_published_work_and_the_reader_chooses_whose_text_leads(self):
        # The homebrew site's copy of a partnered work leads when its collection comes first; homebrew of the same
        # name with no evidence never leads a published work.
        cloak = self.row('Mimicloak', libraries='brew,tavern')
        self.assertEqual(self.related(cloak), [('brew', 'brew-cloak', None), ('tavern', 'tavern-cloak', 'translation'), ('brew', 'alt-cloak', 'candidate')])
        cloak = self.row('Mimicloak', libraries='tavern,brew', lead='brew:src:TGS2')
        self.assertEqual(cloak['versions'][0]['id'], 'brew-cloak')
        # Leading a work does not lift homebrew above a published version of another work.
        shield = self.row('Shield', libraries='5echm,brew', lead='brew:src:Alt')
        self.assertEqual(shield['versions'][0]['id'], SHIELD)
        # Filters decide which versions exist: a homebrew version left alone heads its own row.
        library, params = self.library(libraries='5echm,brew', hide='brew:src:PHB')
        self.assertEqual(self.related(next(r for r in library.entries(params)['rows'] if r['name_en'] == 'Shield')),
                         [(DEFAULT, SHIELD, None), ('brew', 'alt-shield', 'derived')])
        for bad in ('brew', 'Bad:src', 'brew:'):
            with self.subTest(lead=bad), self.assertRaises(ValueError):
                parse_scope({'lead': [bad]}, self.registry)

    def test_a_collection_whose_groups_hold_only_reprints_is_stacked_by_the_library_when_read_alone(self):
        library, params = self.library(libraries='brew')
        self.assertTrue(library.stacking and not library.alone)
        shield = next(r for r in library.entries(params)['rows'] if r['name_en'] == 'Shield')
        self.assertEqual(self.related(shield), [('brew', 'brew-shield', None), ('brew', 'alt-shield', 'derived')])
        self.assertTrue(self.library(libraries='brew', versions='each')[0].alone)
        # The book's own groups already gather its same-name works; read alone it answers as before.
        self.assertTrue(self.library()[0].alone)

    def test_detail_relates_every_version_to_the_one_opened(self):
        library, _ = self.library(libraries='5echm,brew')
        entry = library.detail('alt-shield')
        self.assertEqual(entry['collection'], 'brew')
        self.assertEqual([(m['collection'], m['id'], m['relation']) for m in entry['versions']['members']],
                         [(DEFAULT, SHIELD, 'version'), ('brew', 'brew-shield', 'version'), ('brew', 'alt-shield', None)])
        self.assertEqual(entry['versions']['status'], 'library_candidate')
        entry = library.detail(SHIELD)
        self.assertEqual([m['relation'] for m in entry['versions']['members']], [None, 'translation', 'derived'])

    def test_settings_list_the_same_publication_in_other_collections(self):
        books = {c['id']: {w['id']: w for w in c['works']} for c in describe_works(self.registry, [DEFAULT, 'tavern', 'brew'])}
        self.assertEqual(books['brew']['src:TGS2']['counterparts'], [{'collection': 'tavern', 'id': 'saddle'}])
        self.assertEqual(books['brew']['src:PHB']['counterparts'], [{'collection': DEFAULT, 'id': PHB14}])
        self.assertNotIn('counterparts', books['brew']['src:Alt'])
        self.assertFalse({'codes', 'abbreviations', 'titles'} & set(books['brew']['src:PHB']))

    def test_relations_follow_the_shortest_chain_of_evidence(self):
        a = {'collection': 'x', 'id': 'a', 'work': 'w1', 'publication': 'p1', 'name_en': 'Orb'}
        b = {'collection': 'y', 'id': 'b', 'work': 'w2', 'publication': 'p1', 'name_en': 'Orb'}
        c = {'collection': 'y', 'id': 'c', 'work': 'w3', 'publication': 'p3', 'name_en': 'Orb', 'base': 'b', 'base_publication': 'p1', 'base_name_en': 'Orb'}
        d = {'collection': 'y', 'id': 'd', 'work': 'w2', 'publication': 'p1', 'name_en': 'Orb'}
        e = {'collection': 'z', 'id': 'e', 'work': 'w9', 'publication': 'p9', 'name_en': 'Orb'}
        self.assertEqual(relations([a, b, c, d, e]), [None, 'translation', 'derived', 'translation', 'candidate'])
        # Seen from the copy, the work it came from and that work's other texts are versions, not copies.
        self.assertEqual(relations([a, b, c, d, e], anchor=2), ['version', 'version', None, 'version', 'candidate'])
        # A copy of a differently named entry in the same publication is not a copy of this one.
        self.assertEqual(relations([a, {**c, 'base_name_en': 'Lesser Orb', 'base': 'x'}]), [None, 'candidate'])


class AlignmentTests(unittest.TestCase):
    def test_titles_drop_editions_years_and_filler(self):
        self.assertEqual(title_key("The Griffon's Saddlebag Book 2"), title_key('GriffonsSaddlebag2'))
        self.assertEqual(title_key('歪曲之月(2024)'), title_key('歪曲之月'))
        self.assertEqual(title_key('鬼魅幽谷 / 艾弗瑞斯巢穴集'), title_key('鬼魅幽谷：艾弗瑞斯巢穴集'))

    def test_shared_entries_settle_several_candidates_and_a_tie_aligns_nothing(self):
        book = [{'id': 'moon', 'titles': ['歪曲之月']}]
        site = [{'id': 'moon14', 'titles': ['歪曲之月(2014)']}, {'id': 'moon24', 'titles': ['歪曲之月(2024)']}]
        names = {'moon': {('spell', 'a'), ('spell', 'b')}}
        found = align([('book', book, names), ('site', site, {'moon24': {('spell', 'a'), ('spell', 'b')}, 'moon14': {('spell', 'a')}})])
        self.assertEqual(found.counterparts('book', 'moon'), [('site', 'moon24')])
        self.assertEqual(found.link(('book', 'moon'), ('site', 'moon24')).votes, 2)
        tie = align([('book', book, names), ('site', site, {'moon24': {('spell', 'a')}, 'moon14': {('spell', 'b')}})])
        self.assertEqual(tie.counterparts('book', 'moon'), [])
        self.assertEqual(tie.cluster('book', 'moon'), 'book:moon')
        # A code outranks a title, however many entries the title's candidate shares.
        coded = align([('book', [{'id': 'p', 'codes': ['PHB'], 'titles': ['手册']}], {'p': {('spell', 'a')}}),
                       ('site', [{'id': 'phb', 'codes': ['PHB']}, {'id': 'other', 'titles': ['手册']}], {'other': {('spell', 'a')}})])
        self.assertEqual(coded.counterparts('book', 'p'), [('site', 'phb')])


class LibraryRouteTests(unittest.TestCase):
    @contextmanager
    def served(self):
        data = fixture('catalog')
        with tempfile.TemporaryDirectory() as td:
            demo = catalog_copy(td, 'demo', data)
            with endpoint(data, (Collection('demo', '测试资料库', 'Fixture collection', demo),)) as request:
                yield request

    def test_library_route_and_scoped_queries(self):
        with self.served() as request:
            status, _, body = request('/api/library')
            self.assertEqual(status, 200)
            listed = json.loads(body)
            self.assertEqual([c['id'] for c in listed['collections']], [DEFAULT, 'demo', 'our5etools', '5etools'])
            self.assertNotIn('works', listed['collections'][0])
            works = json.loads(request('/api/library?works=1')[2])['collections']
            self.assertEqual([w['content'] for w in works[1]['works']], ['official', 'official'])
            meta = json.loads(request('/api/meta?libraries=demo,5echm&rulesets=5e')[2])
            self.assertEqual((meta['collection'], meta['library']['libraries'], meta['library']['rulesets']), ('demo', ['demo', DEFAULT], ['5e']))
            self.assertTrue(meta['source_text'])
            rows = json.loads(request('/api/entries?libraries=5echm,demo&type=spell')[2])['rows']
            self.assertEqual([[v['collection'] for v in r['versions']] for r in rows], [[DEFAULT, 'demo']])
            status, headers, _ = request('/api/export?libraries=5echm,demo')
            self.assertEqual((status, headers['Content-Disposition']), (200, 'attachment; filename="5e-anna-library.csv"'))
            for route in ('/api/entries?rulesets=9e', '/api/entries?libraries=../ghost',
                          '/api/meta?libraries=demo&libraries=5echm'):
                with self.subTest(route=route):
                    self.assertEqual(request(route)[0], 400)


if __name__ == '__main__':
    unittest.main()
