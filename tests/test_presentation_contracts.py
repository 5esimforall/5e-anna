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
from urllib.parse import urlencode

from anna.presentation import catalog, contracts, knowledge, reader
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
def endpoint(data):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'catalog.sqlite'
        database(data, path).close()
        class ContractHandler(Handler):
            db_path = path
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

    def test_frozen_entry_evidence_versions_and_metadata(self):
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
        self.test_frozen_entry_evidence_versions_and_metadata()

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


class KnowledgeContractTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture('knowledge')
        self.db = database(self.data)
        self.addCleanup(self.db.close)

    def test_frozen_paths_effect_focus_states_and_evidence(self):
        for case in self.data['chains']:
            with self.subTest(subject=case['subject'], path=case['path']):
                self.assertEqual(knowledge.chain(self.db, case['subject'], reading_path=case['path'], effect_id=case['effect']), case['expected'])
        for case in self.data['inspection']:
            self.assertEqual(knowledge.inspect(self.db, case['id']), case['expected'])

    def test_filters_remain_correlated_to_one_branch_or_effect(self):
        for case in self.data['queries']:
            sql, values = knowledge.casting_condition(case['conditions'])
            actual = [r[0] for r in self.db.execute('SELECT e.id FROM entries e WHERE ' + sql + ' ORDER BY e.id', values)]
            self.assertEqual(actual, case['expected'])
        for invalid in [{}, {'unknown': 'field'}, {'resource': 'invented'},
                        {'cumulative_count': True}, {'cumulative_count': 0},
                        {'query_subject': 'entry', 'mode': 'known'}, '{broken']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                knowledge.casting_query(invalid)

    def test_invalid_paths_targets_and_effects_are_rejected(self):
        for path in [['spell'], ['background', 'background'], ['background', 'other'],
                     ['background'] * 17, 'not JSON']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                knowledge.chain(self.db, 'background', reading_path=path)
        with self.assertRaisesRegex(ValueError, 'effective option'):
            knowledge.chain(self.db, 'background', spell_id='other')
        with self.assertRaisesRegex(ValueError, 'focused procedure'):
            knowledge.chain(self.db, 'background', effect_id='effect_1')
        with self.assertRaises(LookupError):
            knowledge.chain(self.db, 'missing')
        with self.assertRaisesRegex(ValueError, 'not found'):
            knowledge.inspect(self.db, 'missing')

    def test_missing_knowledge_keeps_the_existing_empty_contract(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        self.assertEqual(knowledge.chain(db, 'absent'),
                         {'schema': 2, 'records': [], 'nodes': [], 'options': [], 'incoming': []})

    def test_internal_fields_do_not_escape_through_records_or_options(self):
        def decorate(value):
            value['parser_cache'] = 'private'
            if 'value' in value:
                body = value['value']
                body['parser_cache'] = 'private'
                for effect in body.get('effects', []):
                    effect['parser_cache'] = 'private'
                for grant in body.get('grants', []):
                    decorate_grant(grant)
            if 'grant' in value:
                decorate_grant(value['grant'])
            if 'branch' in value:
                value['branch']['parser_cache'] = 'private'
            return value
        def decorate_grant(grant):
            grant['parser_cache'] = 'private'
            for branch in grant['branches']:
                branch['parser_cache'] = 'private'
        for table in ('effective_knowledge', 'casting_options'):
            for rid, raw in self.db.execute('SELECT id,data FROM ' + table).fetchall():
                self.db.execute('UPDATE ' + table + ' SET data=? WHERE id=?', (json.dumps(decorate(json.loads(raw))), rid))
        for case in self.data['chains']:
            self.assertEqual(knowledge.chain(self.db, case['subject'], reading_path=case['path'], effect_id=case['effect']), case['expected'])

    def test_http_chain_and_export_share_the_frozen_reading(self):
        with endpoint(self.data) as request:
            for case in self.data['chains']:
                params = {'id': case['subject']}
                if case['path'] is not None:
                    params['path'] = json.dumps(case['path'])
                if case['effect'] is not None:
                    params['effect'] = case['effect']
                query = urlencode(params)
                status, _, body = request('/api/knowledge-chain?' + query)
                self.assertEqual((status, json.loads(body)), (200, case['expected']))
                status, headers, body = request('/api/knowledge-export?' + query)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Disposition'], 'attachment; filename="anna-rule-chain.json"')
                self.assertEqual(json.loads(body), {'schema': 2, 'snapshot': {'fixture': 'presentation-v1'}, 'chain': case['expected']})
            for case in self.data['inspection']:
                status, _, body = request('/api/knowledge-inspect?' + urlencode({'id': case['id']}))
                self.assertEqual((status, json.loads(body)), (200, case['expected']))
            status, _, body = request('/api/knowledge-chain?' + urlencode({'id': 'background', 'path': '["background","other"]'}))
            self.assertEqual((status, json.loads(body)), (400, {'error': 'Reading path has no evidenced relationship'}))


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

    def test_explicit_states_missing_values_and_input_ownership(self):
        original = fixture('knowledge')['inspection'][0]['expected']['effective']
        for status in ('resolved', 'partial', 'review', 'rejected'):
            for review in ('unreviewed', 'applied', 'revoked', 'stale', 'conflict'):
                value = deepcopy(original)
                value.update(status=status, review_status=review)
                projected = contracts.record(value)
                self.assertEqual(projected, value)
                projected['value']['unparsed'].append({'reason': 'new'})
                self.assertNotEqual(projected, value)
        value = {'id': 'entry', 'fields': {'unknown': None, 'explicit_no': False, 'conflict': ['yes', 'no']}}
        self.assertEqual(contracts.entry(value), value)
        self.assertNotIn('name', contracts.entry(value))
        with self.assertRaisesRegex(ValueError, 'schema'):
            contracts.record({**original, 'schema': 999})

    def test_http_import_does_not_load_analysis_or_review_commands(self):
        code = '''
import sys
import anna.server.app
for name in ('anna.catalog.build', 'anna.knowledge.repository', 'anna.knowledge.casting',
             'anna.knowledge.spells', 'anna.knowledge.levels', 'anna.knowledge.features',
             'anna.review.commands'):
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
