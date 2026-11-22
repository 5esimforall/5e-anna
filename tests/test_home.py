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
