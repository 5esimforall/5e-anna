"""The same home payload is published and served for the current collection snapshots."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from anna.server import home


class HomeCacheTests(unittest.TestCase):
    def test_matches_current_collection_descriptions(self):
        collections = [{'id': '5echm', 'snapshot': 'first'}]
        registry = SimpleNamespace(describe=lambda: collections)

        def handle(route):
            body = {'collections': collections} if route == '/api/library' else {'route': route}
            return 200, {}, json.dumps(body).encode()

        data = home.encode_home(handle)
        self.assertEqual(tuple(json.loads(data)), home.ROUTES)
        with TemporaryDirectory() as directory, patch.object(home, 'LOCAL_CACHE', Path(directory) / 'home.json'):
            self.assertIsNone(home.local_cache(registry))
            home.LOCAL_CACHE.write_bytes(data)
            name, served = home.local_cache(registry)
            self.assertEqual(served, data)
            self.assertIn(f'data-anna-home="{name}"'.encode(), home.mark_home(b'<html lang="zh-CN">', name))
            collections[0]['snapshot'] = 'rebuilt'
            self.assertIsNone(home.local_cache(registry))
            home.LOCAL_CACHE.write_bytes(b'bad json')
            self.assertIsNone(home.local_cache(registry))

    def test_a_failed_query_cannot_be_published(self):
        with self.assertRaisesRegex(ValueError, 'HTTP 409'):
            home.encode_home(lambda route: (409, {}, b'{}'))

    def test_scope_routes_and_extra_scopes(self):
        self.assertEqual(home.scope_routes(), home.ROUTES)
        self.assertEqual(home.scope_routes(('5echm',)), home.ROUTES)
        self.assertEqual(
            home.scope_routes(('5echm', 'our5etools')),
            (
                '/api/library',
                '/api/meta?libraries=5echm,our5etools',
                '/api/facets?libraries=5echm,our5etools',
                '/api/entries?offset=0&limit=150&libraries=5echm,our5etools',
            ),
        )
        self.assertEqual(home.extra_scopes(('5echm',)), [])
        self.assertEqual(
            home.extra_scopes(('5echm', 'our5etools')),
            [('5echm', 'our5etools'), ('our5etools',), ('our5etools', '5echm')],
        )

    def test_mark_home_scopes_and_local_scope_caches(self):
        collections = [
            {'id': '5echm', 'snapshot': 'snap-a'},
            {'id': 'our5etools', 'snapshot': 'snap-b'},
        ]
        registry = SimpleNamespace(describe=lambda: collections)

        def handle(route):
            body = {'collections': collections} if route == '/api/library' else {'route': route}
            return 200, {}, json.dumps(body).encode()

        home._read_local_scopes.cache_clear()
        self.assertIsNone(home.encode_local_scopes(handle, ('5echm',)))
        scopes_payload = home.encode_local_scopes(handle, ('5echm', 'our5etools'))
        self.assertIsNotNone(scopes_payload)

        with TemporaryDirectory() as directory, patch.object(home, 'LOCAL_SCOPES_CACHE', Path(directory) / 'home-scopes.json'):
            self.assertEqual(home.local_scope_caches(registry), ({}, {}))
            home.LOCAL_SCOPES_CACHE.write_bytes(scopes_payload)
            names, payloads = home.local_scope_caches(registry)
            self.assertEqual(set(names), {'5echm,our5etools', 'our5etools', 'our5etools,5echm'})
            self.assertEqual(set(payloads), set(names.values()))
            for scope_key, fname in names.items():
                decoded = json.loads(payloads[fname])
                self.assertEqual(tuple(decoded), home.scope_routes(tuple(scope_key.split(','))))

            marked = home.mark_home_scopes(b'<html lang="zh-CN">', names)
            expected_attr = ';'.join(f'{k}={v}' for k, v in names.items())
            self.assertIn(f'data-anna-home-scopes="{expected_attr}"'.encode(), marked)
            self.assertEqual(home.mark_home_scopes(b'<html lang="zh-CN">', {}), b'<html lang="zh-CN">')

            # Snapshot change invalidates cache match
            collections[0]['snapshot'] = 'snap-b'
            self.assertEqual(home.local_scope_caches(registry), ({}, {}))
            collections[0]['snapshot'] = 'snap-a'
            self.assertEqual(home.local_scope_caches(registry)[0], names)

            # Corrupt JSON or invalid scope entry fails gracefully
            home.LOCAL_SCOPES_CACHE.write_bytes(b'{"collections": [], "scopes": {"5echm": "bad"}}')
            self.assertEqual(home.local_scope_caches(registry), ({}, {}))
            home.LOCAL_SCOPES_CACHE.write_bytes(b'bad json')
            self.assertEqual(home.local_scope_caches(registry), ({}, {}))
