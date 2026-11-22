"""One shelf over the reader's collections: layout, addresses, pages, search and the other texts of a work."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from anna.presentation.collections import Capabilities, Collection, CollectionRegistry, SearchHits
from anna.presentation.library import parse_scope, publications_of
from anna.presentation.reader import ReaderError
from anna.presentation.shelf import Shelf, heading_key, qualify, split
from anna.server.pages_publications import build_publications, load_publications


def node(id, parent, label, path=''):
    return {'id': id, 'parent_id': parent, 'label': label, 'path': path, 'fragment': '', 'status': 'present' if path else 'placeholder'}


BOOK_NODES = [
    node('1', '', '写在前面', 'topics/a.htm'),
    node('2', '', '——核心规则——'),
    node('3', '', '玩家手册'),
    node('4', '3', '第一章：进行游戏', 'topics/ch1.htm'),
    node('5', '3', '第二章：创建角色', 'topics/ch2.htm'),
    node('6', '5', '第一步', 'topics/step1.htm'),
    node('8', '3', '杂项', 'topics/misc.htm'),
    node('7', '', '旧书', 'topics/old.htm'),
]
BREW_NODES = [
    node('div_a', '', '——官方规则书——'),
    node('n_phb', '', '玩家手册（2024）'),
    node('n_phb_1', 'n_phb', '第 1 章：进行游戏', 'books/phb/1.html'),
    node('n_phb_2', 'n_phb', '第 2 章：创建角色', 'books/phb/2.html'),
    node('n_phb_2_s0', 'n_phb_2', '第一步', 'books/phb/2.html'),
    node('div_b', '', '——社区书籍——'),
    node('n_new', '', '新书'),
    node('n_new_0', 'n_new', '简介', 'books/new/0.html'),
]


def work(id, name, root, codes=()):
    return {'id': id, 'name': name, 'label': name, 'origin': '官方', 'ruleset': '5r', 'count': 1, 'group': '',
            'codes': list(codes), 'abbreviations': [], 'titles': [name], 'reader_root': root}


class Provider:
    """The parts of a catalog the shelf reads: its works, ranked rows and entry rows."""

    def __init__(self, works, ranked=(), rows=None):
        self._works, self._ranked, self._rows = works, list(ranked), rows or {}

    def works(self):
        return self._works

    def ranked(self, params, restrict=None, sort_field=None):
        return list(self._ranked)

    def rows(self, ids):
        return {i: self._rows[i] for i in ids if i in self._rows}

    def capabilities(self):
        return Capabilities(conditions=frozenset(), facets=frozenset(), types=frozenset({'spell'}), sources=frozenset(),
                            stackable=frozenset({'spell'}))

    def signature(self):
        return ('fake', id(self))


class Reader:
    """A collection's book: contents, pages with their definitions, entry places and ranked search hits."""

    def __init__(self, snapshot, nodes, pages, located=None, hits=('all', [])):
        self.snapshot, self.nodes, self.pages, self.located, self.hits = snapshot, nodes, pages, located or {}, hits

    def navigation(self):
        return {'nodes': self.nodes, 'default_page': next(n['path'] for n in self.nodes if n['path']), 'page_base': '/x-book/'}

    def page_data(self, path=None, entry=None):
        if entry:
            if entry not in self.located:
                raise ReaderError('entry_missing', 404)
            path = self.located[entry]
        if path not in self.pages:
            raise ReaderError('page_missing', 404)
        return {'snapshot': self.snapshot, 'path': path, 'fragment': '', 'title': path, 'definitions': self.pages[path]}

    def search(self, keyword, *, title_only=False):
        match, hits = self.hits
        return SearchHits(match, [(score, path) for score, path in hits], lambda path, score: {'path': path, 'title': path, 'rank': score})


def registry(book_hits=('all', []), brew_hits=('all', [])):
    book = Reader('s-book', BOOK_NODES, {n['path']: [] for n in BOOK_NODES if n['path']} | {'topics/misc.htm': [{'id': 'ver_x'}]},
                  hits=book_hits)
    brew = Reader('s-brew', BREW_NODES, {n['path']: [] for n in BREW_NODES if n['path']}, located={'o5e_y': 'books/phb/2.html'},
                  hits=brew_hits)
    book_works = Provider([work('src_phb', '玩家手册', '3', ['XPHB']), work('src_old', '旧书', '7')],
                          rows={'ver_x': {'id': 'ver_x', 'type': 'spell', 'name_en': 'Fire Bolt'}})
    brew_works = Provider([work('src:XPHB', '玩家手册（2024）', 'n_phb', ['XPHB']), work('src:NEW', '新书', 'n_new', ['NEW'])],
                          ranked=[{'id': 'o5e_y', 'type': 'spell', 'name_en': 'Fire Bolt', 'work': 'src:XPHB'}])
    return CollectionRegistry(book_works, (Collection('brew', '家酿站', 'Brew', brew_works, snapshot='s-brew', reader=brew),),
                              book_reader=book, book_snapshot='s-book')


def shelf(reg, libraries='5echm,brew', **scope):
    params = {'libraries': [libraries], **{k: [v] if isinstance(v, str) else v for k, v in scope.items()}}
    return Shelf(reg, parse_scope(params, reg))


class PublishedRelationshipsTests(unittest.TestCase):
    def test_round_trip_preserves_evidence_without_reranking_entries(self):
        reg = registry()
        snapshots = {'5echm': 's-book', 'brew': 's-brew'}
        expected = publications_of(reg)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'publications.json'
            build_publications(path, reg)
            reg.published_publications = load_publications(path, snapshots)
            for key in snapshots:
                with self.subTest(collection=key), self.assertRaisesRegex(ValueError, 'library snapshots'):
                    load_publications(path, snapshots | {key: 'rebuilt'})
        with patch.object(Provider, 'ranked', side_effect=AssertionError('Pages should use published relationships')):
            self.assertEqual(publications_of(reg), expected)
            self.assertEqual(publications_of(reg).alignment.counterparts('5echm', 'src_phb'), [('brew', 'src:XPHB')])
            self.assertEqual(publications_of(reg).alignment.link(('5echm', 'src_phb'), ('brew', 'src:XPHB')).rule, 'code')


class AddressTests(unittest.TestCase):
    def test_addresses_name_their_collection(self):
        self.assertEqual(qualify('brew', 'books/phb/1.html'), 'brew:books/phb/1.html')
        self.assertEqual(split('brew:books/phb/1.html'), ('brew', 'books/phb/1.html'))
        for bad in ('', 'books/phb/1.html', 'Brew:x', 'brew:', ':x'):
            with self.subTest(address=bad), self.assertRaises(ReaderError) as caught:
                split(bad)
            self.assertEqual((caught.exception.code, caught.exception.status), ('invalid_location', 400))

    def test_headings_compare_without_chapter_numbering(self):
        self.assertEqual(heading_key('第二章：创建角色'), heading_key('第 2 章：创建角色'))
        self.assertEqual(heading_key('附录C：术语汇编'), heading_key('附录 C：术语汇编'))
        self.assertNotEqual(heading_key('第二章：创建角色'), heading_key('第 2 章：角色职业'))


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.reg = registry()

    def top(self, nav):
        return [(n['id'], bool(n.get('alternate'))) for n in nav['nodes'] if not n['parent_id']]

    def test_the_first_collection_lays_out_the_shelf_and_later_ones_add_their_own_works(self):
        nav = shelf(self.reg).navigation()
        # The shared work stands once where the book places it, its other text beside it; the brew's divider that
        # headed only the shared work goes, the one heading its own work stays.
        self.assertEqual(self.top(nav), [('5echm:1', False), ('5echm:2', False), ('5echm:3', False), ('brew:n_phb', True),
                                         ('5echm:7', False), ('brew:div_b', False), ('brew:n_new', False)])
        by_id = {n['id']: n for n in nav['nodes']}
        self.assertEqual(by_id['5echm:3']['texts'], [{'id': '5echm:3', 'collection': '5echm'}, {'id': 'brew:n_phb', 'collection': 'brew'}])
        self.assertEqual(by_id['brew:n_phb_2']['parent_id'], 'brew:n_phb')
        self.assertEqual(by_id['brew:n_phb_1']['path'], 'brew:books/phb/1.html')
        self.assertNotIn('path', by_id['5echm:3'])
        self.assertEqual(nav['default_page'], '5echm:topics/a.htm')
        self.assertEqual([(c['id'], c['snapshot']) for c in nav['collections']], [('5echm', 's-book'), ('brew', 's-brew')])

    def test_the_reader_chooses_whose_text_leads(self):
        nav = shelf(self.reg, lead='brew:src:XPHB').navigation()
        self.assertEqual(self.top(nav)[2:4], [('brew:n_phb', False), ('5echm:3', True)])
        self.assertEqual(nav['nodes'][[n['id'] for n in nav['nodes']].index('brew:n_phb')]['texts'][0]['collection'], 'brew')

    def test_the_library_order_decides_the_layout(self):
        nav = shelf(self.reg, 'brew,5echm').navigation()
        self.assertEqual(self.top(nav)[:3], [('brew:div_a', False), ('brew:n_phb', False), ('5echm:3', True)])
        self.assertIn(('5echm:2', False), self.top(nav))

    def test_one_collection_reads_alone(self):
        nav = shelf(self.reg, 'brew').navigation()
        self.assertEqual([n['id'] for n in nav['nodes']], ['brew:' + n['id'] for n in BREW_NODES])
        self.assertFalse(any('texts' in n for n in nav['nodes']))
        self.assertEqual(len(shelf(self.reg, 'brew').signature), 24)
        self.assertNotEqual(shelf(self.reg, 'brew').signature, shelf(self.reg).signature)


class PageTests(unittest.TestCase):
    def setUp(self):
        self.shelf = shelf(registry())

    def test_pages_and_entries_are_read_from_their_collection(self):
        page = self.shelf.page(path='brew:books/phb/1.html', snapshot='s-brew')
        self.assertEqual((page['path'], page['collection']), ('brew:books/phb/1.html', 'brew'))
        self.assertEqual(self.shelf.page(entry='brew:o5e_y')['path'], 'brew:books/phb/2.html')

    def test_an_address_outside_the_shelf_or_its_build_is_refused(self):
        for kwargs, code, status in [({'path': 'brew:books/phb/1.html', 'snapshot': 's-book'}, 'snapshot_changed', 409),
                                     ({'path': 'topics/a.htm'}, 'invalid_location', 400),
                                     ({'path': 'nowhere:x'}, 'invalid_location', 400),
                                     ({}, 'invalid_location', 400),
                                     ({'path': '5echm:topics/a.htm', 'entry': '5echm:ver_x'}, 'invalid_location', 400)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ReaderError) as caught:
                self.shelf.page(**kwargs)
            self.assertEqual((caught.exception.code, caught.exception.status), (code, status))
        # A collection the reader's library leaves out is not on the shelf, though the server holds it.
        with self.assertRaises(ReaderError) as caught:
            shelf(registry(), '5echm').page(path='brew:books/phb/1.html')
        self.assertEqual((caught.exception.code, caught.exception.status), ('outside_library', 404))


class SearchTests(unittest.TestCase):
    def test_matches_interleave_by_score_across_collections(self):
        found = shelf(registry(('all', [(1500, 'topics/ch1.htm'), (10, 'topics/a.htm')]),
                               ('all', [(2000, 'books/phb/1.html'), (5, 'books/new/0.html')]))).search('进行')
        self.assertEqual([r['path'] for r in found['results']],
                         ['brew:books/phb/1.html', '5echm:topics/ch1.htm', '5echm:topics/a.htm', 'brew:books/new/0.html'])
        self.assertEqual((found['total'], found['match'], found['totalPages']), (4, 'all', 1))
        second = shelf(registry(('all', [(1500, 'topics/ch1.htm'), (10, 'topics/a.htm')]),
                                ('all', [(2000, 'books/phb/1.html')]))).search('进行', page=2, page_size=2)
        self.assertEqual(([r['path'] for r in second['results']], second['totalPages']), (['5echm:topics/a.htm'], 2))

    def test_pages_matching_every_term_outrank_partial_matches_elsewhere(self):
        found = shelf(registry(('all', [(10, 'topics/a.htm')]), ('any', [(2000, 'books/phb/1.html')]))).search('甲 乙')
        self.assertEqual(([r['path'] for r in found['results']], found['match']), (['5echm:topics/a.htm'], 'all'))
        partial = shelf(registry(('any', [(10, 'topics/a.htm')]), ('any', [(20, 'books/phb/1.html')]))).search('甲 乙')
        self.assertEqual(([r['collection'] for r in partial['results']], partial['match']), (['brew', '5echm'], 'any'))
        with self.assertRaises(ReaderError):
            shelf(registry()).search('x' * 2001)


class CounterpartTests(unittest.TestCase):
    def setUp(self):
        self.shelf = shelf(registry())

    def test_the_other_text_opens_at_the_same_heading(self):
        self.assertEqual(self.shelf.counterpart('5echm:6', 'brew'), {'root': 'brew:n_phb', 'node': 'brew:n_phb_2_s0'})
        self.assertEqual(self.shelf.counterpart('5echm:6', 'brew', '5echm:5'), {'root': 'brew:n_phb_2', 'node': 'brew:n_phb_2_s0'})
        self.assertEqual(self.shelf.counterpart('brew:n_phb_1', '5echm'), {'root': '5echm:3', 'node': '5echm:4'})

    def test_without_a_shared_heading_the_entries_a_page_defines_lead_the_way(self):
        self.assertEqual(self.shelf.counterpart('5echm:8', 'brew'), {'root': 'brew:n_phb', 'node': 'brew:n_phb_2'})

    def test_without_evidence_the_other_text_opens_at_its_start(self):
        self.assertEqual(self.shelf.counterpart('5echm:3', 'brew'), {'root': 'brew:n_phb', 'node': 'brew:n_phb'})
        for node, collection, code in [('5echm:7', 'brew', 'no_counterpart'), ('5echm:99', 'brew', 'invalid_location')]:
            with self.subTest(node=node), self.assertRaises(ReaderError) as caught:
                self.shelf.counterpart(node, collection)
            self.assertEqual(caught.exception.code, code)


if __name__ == '__main__':
    unittest.main()
