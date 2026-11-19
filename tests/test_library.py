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

from anna.presentation import catalog
from anna.presentation.collections import BookCatalog, Collection, CollectionError, CollectionRegistry, DEFAULT
from anna.presentation.library import Library, Scope, localize, merge_order, parse_scope, describe_works

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

    def test_scope_rejects_malformed_choices_and_pins_one_collection(self):
        for params in ({'rulesets': ['5x']}, {'rulesets': ['']}, {'content': ['homebrew']}, {'versions': ['all']},
                       {'prefer': ['4e']}, {'hide': ['no-colon']}, {'hide': ['Bad:src']}, {'libraries': ['../x']},
                       {'versions': ['each', 'stacked']}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                parse_scope(dict(params), self.registry)
        pinned = parse_scope({'collection': ['demo'], 'libraries': ['5echm']}, self.registry)
        self.assertEqual((pinned.libraries, pinned.pinned), (('demo',), True))
        for value, code in ((['reserved-demo'], 'collection_unavailable'), (['ghost'], 'unknown_collection'), ([''], 'invalid_collection')):
            with self.subTest(value=value), self.assertRaises(CollectionError) as caught:
                parse_scope({'collection': value}, self.registry)
            self.assertEqual(caught.exception.code, code)

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
        # Counts set aside the condition a collection cannot answer only for that condition's own field.
        facets = library.facets({**params, 'type': ['class_feature']})
        types = {o['value']: o['count'] for o in next(g for g in facets['groups'] if g['key'] == 'type')['options']}
        self.assertEqual(types, {'class_feature': 1, 'spell': 2})
        self.assertEqual((facets['total'], facets['scope_total'], facets['without']), (1, 1, {'type': 2}))
        # A single collection that cannot answer at all returns an empty table rather than an error.
        library, params = self.library(libraries='spells', type='class_feature')
        self.assertEqual(library.entries(params)['total'], 0)

    def test_localize_keeps_what_a_collection_answers_and_reports_what_it_cannot(self):
        caps = self.registry.get(DEFAULT).provider.capabilities()
        every = caps.conditions | {'package', 'copy_status'}
        local, unanswerable = localize({'type': ['spell'], 'q': ['盾'], 'sort': ['name'], 'not_copy_status': ['ok'],
                                        'origin': ['社区']}, caps, every, caps.types)
        self.assertEqual(local, {'type': ['spell'], 'q': ['盾'], 'sort': ['name']})
        self.assertEqual(unanswerable, {'origin': 'origin'})

    def test_facets_merge_options_and_counts_across_collections(self):
        library, params = self.library(libraries='5echm,demo')
        facets = library.facets({**params, 'type': ['spell']})
        types = {o['value']: o['count'] for o in next(g for g in facets['groups'] if g['key'] == 'type')['options']}
        self.assertEqual(types, {'class_feature': 2, 'spell': 2})
        self.assertEqual((facets['total'], facets['scope_total'], facets['without']), (2, 2, {'type': 2}))
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
        # A single collection answers exactly as its provider does.
        library, _ = self.library()
        self.assertNotIn('collection', library.detail(SHIELD))
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
            for route, expected in (('/api/entries?rulesets=9e', 400), ('/api/entries?collection=ghost', 404),
                                    ('/api/meta?collection=5etools', 409)):
                with self.subTest(route=route):
                    self.assertEqual(request(route)[0], expected)


if __name__ == '__main__':
    unittest.main()
