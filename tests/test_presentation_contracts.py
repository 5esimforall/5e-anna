"""Frozen consumer contracts: no extractor, corpus build or review replay in setup."""
from contextlib import contextmanager
from copy import deepcopy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock
from urllib.parse import urlencode

from anna.presentation import catalog, contracts, reader
from anna.presentation.collections import (
    BookCatalog,
    CatalogProvider,
    Collection,
    CollectionRegistry,
    DEFAULT,
    FIVETOOLS,
    OUR5ETOOLS,
)
from anna.presentation.layout import entry_layout
from anna.server.linked_book import inject, text_slice
from anna.server.query import Handler
from anna.source.html import Document


def fixture(name):
    return json.loads((Path(__file__).parent / 'fixtures' / ('presentation-' + name + '.json')).read_text())


def database(data, path=':memory:'):
    db = sqlite3.connect(path)
    db.executescript('\n'.join(data['sql']))
    db.row_factory = sqlite3.Row
    catalog.browse.register(db)
    return db


@contextmanager
def endpoint(data, additional=()):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'catalog.sqlite'
        database(data, path).close()
        class ContractHandler(Handler):
            db_path = path
            additional_collections = additional
        server = ThreadingHTTPServer(('127.0.0.1', 0), ContractHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(route):
            connection = HTTPConnection('127.0.0.1', server.server_port)
            try:
                connection.request('GET', route)
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()
        try:
            yield request
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class CatalogContractTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture('catalog')
        self.db = database(self.data)
        self.addCleanup(self.db.close)

    def test_frozen_query_schema_order_counts_pagination_and_csv(self):
        for case in self.data['queries']:
            with self.subTest(params=case['params']):
                params = case['params']
                self.assertEqual(catalog.query(self.db, params), case['expected'])
                self.assertEqual(catalog.facets(self.db, params), case['facets'])
                rows = catalog.query(self.db, params, export=True)['rows']
                self.assertEqual(catalog.csv_export(rows, catalog.browse.schema_for(catalog.entry_types(params))).decode(), case['csv'])

    def test_frozen_entry_versions_and_metadata(self):
        self.assertEqual(catalog.meta(self.db), self.data['meta'])
        for case in self.data['details']:
            with self.subTest(entry=case['id']):
                self.assertEqual(catalog.detail(self.db, case['id']), case['expected'])

    def test_storage_metadata_does_not_become_a_query_or_detail_field(self):
        for eid, raw in self.db.execute('SELECT id,data FROM entries').fetchall():
            value = json.loads(raw)
            value['parser_cache'] = {'private': True}
            self.db.execute('UPDATE entries SET data=? WHERE id=?', (json.dumps(value), eid))
        self.test_frozen_query_schema_order_counts_pagination_and_csv()
        self.test_frozen_entry_versions_and_metadata()

    def test_http_routes_errors_and_download_contract(self):
        with endpoint(self.data) as request:
            for case in self.data['queries']:
                query = urlencode(case['params'], doseq=True)
                for route, expected in [('entries', case['expected']), ('facets', case['facets'])]:
                    status, headers, body = request('/api/' + route + '?' + query)
                    self.assertEqual(status, 200)
                    self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
                    self.assertEqual(json.loads(body), expected)
                status, headers, body = request('/api/export?' + query)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Disposition'], 'attachment; filename="5e-anna.csv"')
                self.assertEqual(body.decode(), case['csv'])
            for case in self.data['details']:
                status, _, body = request('/api/entry?' + urlencode({'id': case['id']}))
                self.assertEqual((status, json.loads(body)), (200, case['expected']))
            for route, status, message in [
                ('/api/entry?id=absent', 404, '条目不存在'),
                ('/api/entries?type=unknown', 400, '未知条目类型'),
            ]:
                actual, _, body = request(route)
                self.assertEqual(actual, status)
                self.assertIn(message, json.loads(body)['error'])

    def test_collection_selection_does_not_silently_fall_back(self):
        with endpoint(self.data) as request:
            status, _, body = request('/api/meta')
            self.assertEqual(status, 200)
            meta = json.loads(body)
            self.assertEqual(meta['collection'], DEFAULT)
            self.assertEqual(meta['default_collection'], DEFAULT)
            self.assertTrue(meta['source_text'])
            self.assertEqual({c['id']: c['status'] for c in meta['collections']},
                             {DEFAULT: 'ready', OUR5ETOOLS.key: 'reserved', FIVETOOLS.key: 'reserved'})
            for suffix, status, code in (
                ('our5etools', 409, 'collection_unavailable'),
                ('5etools', 409, 'collection_unavailable'),
                ('not-registered', 404, 'unknown_collection'),
                ('', 400, 'invalid_collection'),
                ('5echm&collection=our5etools', 400, 'invalid_collection'),
                ('5echm&collection=5etools', 400, 'invalid_collection'),
            ):
                actual, _, body = request('/api/entries?collection='+suffix)
                self.assertEqual((actual, json.loads(body)), (status, {'code':code}))

    def test_second_provider_serves_every_projection_in_its_own_scope(self):
        detail = self.data['details'][1]
        eid = detail['id']
        external = Mock(spec=CatalogProvider)
        external.meta.return_value = {'sources':[{'id':'external-book'}]}
        external.entries.return_value = {'rows':[{'id':eid}]}
        external.facets.return_value = {'groups':[]}
        external.detail.return_value = {'id':eid,'body':'外部正文'}
        external.layout.return_value = {'status':'empty'}
        external.export.return_value = b'external\n'
        # A future provider fills the reserved key without changing the router.
        with endpoint(self.data, (Collection(FIVETOOLS.key, FIVETOOLS.zh, FIVETOOLS.en, external),)) as request:
            def selected(path):
                return request(path+('&' if '?' in path else '?')+'collection=5etools')
            status, _, body = selected('/api/meta')
            self.assertEqual(status, 200)
            meta = json.loads(body)
            self.assertEqual(meta['collection'], FIVETOOLS.key)
            # Each collection says what it offers beyond the table, so the library can send the tour and Stella there.
            self.assertIn({'id':'5etools','label':{'zh':'5etools','en':'5etools'},'status':'ready','tour':False,'reader':False,'snapshot':None},meta['collections'])
            self.assertIn({'id':'our5etools','label':{'zh':'our5etools','en':'our5etools'},'status':'reserved','tour':False,'reader':False,'snapshot':None},meta['collections'])
            self.assertIsNone(meta['reader_url'])
            self.assertIsNone(meta['snapshot'])
            self.assertFalse(meta['source_text'])
            for path, expected in (
                ('/api/entries?q=Shield', external.entries.return_value),
                ('/api/facets?q=Shield', external.facets.return_value),
                ('/api/entry?id='+eid, external.detail.return_value),
                ('/api/entry-layout?id='+eid, external.layout.return_value),
            ):
                status, _, body = selected(path)
                self.assertEqual((status, json.loads(body)), (200, expected))
            status, headers, body = selected('/api/export?q=Shield')
            self.assertEqual((status, body), (200, b'external\n'))
            self.assertEqual(headers['Content-Disposition'], 'attachment; filename="5e-anna-5etools.csv"')
            for method in (external.entries, external.facets, external.export):
                method.assert_called_once_with({'q':['Shield']})
            external.detail.assert_called_once_with(eid)
            external.layout.assert_called_once_with(eid)
            # The same raw ID in the default collection still opens the original record.
            self.assertEqual(json.loads(request('/api/entry?id='+eid)[2]), detail['expected'])
            status, _, body = selected('/source')
            self.assertEqual((status, json.loads(body)), (400, {'code':'collection_unsupported'}))

    def test_registration_requires_distinct_safe_keys(self):
        book = BookCatalog(Path('unused.sqlite'), Path('.'))
        with self.assertRaisesRegex(ValueError,'Duplicate collection'):
            CollectionRegistry(book, (Collection(DEFAULT, '重复', 'Duplicate'),))
        with self.assertRaisesRegex(ValueError,'Invalid collection key'):
            CollectionRegistry(book, (Collection('../other', '无效', 'Invalid'),))


class ProjectionContractTests(unittest.TestCase):
    def test_ambiguous_names_and_conflicting_reviews_keep_their_candidates(self):
        value = {'id': 'reference', 'text': '同名', 'status': 'ambiguous',
                 'review_status': 'conflict', 'target_id': None,
                 'candidates': ['version_b', 'version_a'], 'can_link': False,
                 'evidence': {'path': 'topics/source.htm', 'span': [3, 5]},
                 'decision_ids': ['review_a', 'review_b']}
        projected = contracts.reference({**value, 'resolver_cache': 'private'})
        self.assertEqual(projected, value)
        projected['candidates'].reverse()
        self.assertEqual(value['candidates'], ['version_b', 'version_a'])

    def test_explicit_missing_values_and_input_ownership(self):
        value = {'id': 'entry', 'fields': {'unknown': None, 'explicit_no': False, 'conflict': ['yes', 'no']}}
        self.assertEqual(contracts.entry(value), value)
        self.assertNotIn('name', contracts.entry(value))
    def test_http_import_does_not_load_analysis_or_review_commands(self):
        code = '''
import sys
import anna.server.app
for name in ('anna.catalog.build', 'anna.review.repository', 'anna.review.search',
             'anna.review.context'):
    assert name not in sys.modules, name
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class ReaderContractTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture('reader')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)
        for path, raw in self.data['source'].items():
            file = self.source / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(raw)
        self.db = database(self.data)
        self.addCleanup(self.db.close)

    def test_layout_html_notes_and_fallback_statuses(self):
        eid = self.data['entry']['id']
        self.assertEqual(entry_layout(self.db, eid, self.source), self.data['layout'])
        path = self.data['entry']['page_path']
        (self.source / path).write_text('changed')
        self.assertEqual(entry_layout(self.db, eid, self.source), {'status': 'stale', 'path': path})
        (self.source / path).unlink()
        self.assertEqual(entry_layout(self.db, eid, self.source), {'status': 'missing', 'path': path})
        with self.assertRaises(LookupError):
            entry_layout(self.db, 'absent', self.source)

    def test_reader_definitions_navigation_and_dom_bindings(self):
        e = self.data['entry']
        navigation = reader.build_navigation(self.source, set(self.data['source']))
        navigation.update(snapshot='fixture-snapshot', pages=self.data['navigation']['pages'])
        self.assertEqual(navigation, self.data['navigation'])
        self.assertEqual(reader.page_data(navigation, self.data['manifest'], path=e['page_path']), self.data['page'])
        self.assertEqual(reader.page_data(navigation, self.data['manifest'], entry=e['id']), self.data['definition'])
        raw = self.data['source'][e['page_path']]
        doc = Document(e['page_path'], raw)
        try:
            payload = deepcopy(self.data['payload'])
            payload['definitions'][0]['body'] = text_slice(doc, e['start'], e['end'])
            payload['definitions'][0]['title'] = text_slice(doc, e['start'], e['title_end'])
            self.assertEqual(payload, self.data['payload'])
            self.assertEqual(inject(raw.encode(), payload).decode(), self.data['linked_html'])
        finally:
            doc.close()
        for location, code, status in [({}, 'invalid_location', 400),
                                       ({'entry': 'missing'}, 'entry_missing', 404),
                                       ({'path': 'missing'}, 'page_missing', 404),
                                       ({'path': e['page_path'], 'entry': e['id']}, 'invalid_location', 400)]:
            with self.assertRaises(reader.ReaderError) as error:
                reader.page_data(navigation, self.data['manifest'], **location)
            self.assertEqual((error.exception.code, error.exception.status), (code, status))
        unavailable = deepcopy(self.data['manifest'])
        unavailable['entries'][e['id']]['locatable'] = False
        with self.assertRaises(reader.ReaderError) as error:
            reader.page_data(navigation, unavailable, entry=e['id'])
        self.assertEqual((error.exception.code, error.exception.status), ('entry_unlocatable', 422))


if __name__ == '__main__':
    unittest.main()
