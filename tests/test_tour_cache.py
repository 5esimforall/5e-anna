"""Tests for multi-scope static tour caches (backend encoding, invalidation, Pages build, and web/tour-cache.js)."""
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from anna import paths
from anna.presentation import tour as tour_curation
from anna.publish.pages_build import build as build_pages
from anna.publish.pages_data import export_pages_data
from anna.server import tour_cache
from test_pages_perf import _make_5echm_fixture


class TourCacheTests(unittest.TestCase):
    def test_encode_tour_and_invalidation_on_snapshot_or_curation_change(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            curation_file = root / 'tour.json'
            curation_file.write_text(json.dumps({
                'schema': 2,
                'sections': [{
                    'key': 'start',
                    'label': {'zh': '开始', 'en': 'Start'},
                    'topics': [
                        {'key': 'create', 'label': {'zh': '创建角色', 'en': 'Create'}, 'contents': {'5echm': 'PHB / Ch1'}},
                        {'key': 'spells', 'label': {'zh': '法术', 'en': 'Spells'}, 'query': {'type': ['spell']}},
                    ],
                }],
                'choices': {'5echm': {}},
            }), encoding='utf-8')
            collections = [{'id': '5echm', 'snapshot': 'snap-1'}]
            registry = SimpleNamespace(describe=lambda: collections)
            asked = []

            def handle(route):
                asked.append(route)
                if route == '/api/library':
                    return 200, {}, json.dumps({'collections': collections}).encode()
                if route == '/api/tour?at=':
                    return 200, {}, json.dumps({
                        'id': '', 'kind': 'root', 'snapshot': 'tour-snap-1',
                        'groups': [{'key': 'section', 'items': [
                            {'id': 'create', 'kind': 'topic', 'status': 'ok'},
                            {'id': 'spells', 'kind': 'topic', 'status': 'ok'},
                        ]}],
                    }).encode()
                if route == '/api/tour?at=create':
                    return 200, {}, json.dumps({'id': 'create', 'shape': 'detail', 'groups': []}).encode()
                if route == '/api/tour-layout?at=create':
                    return 200, {}, json.dumps({'status': 'ok', 'html': '<p>intro</p>'}).encode()
                if route == '/api/tour?at=spells':
                    return 200, {}, json.dumps({'id': 'spells', 'shape': 'index', 'groups': []}).encode()
                return 404, {}, b'{}'

            payload = tour_cache.encode_tour(handle, curation_path=curation_file)
            decoded = json.loads(payload)
            self.assertEqual(decoded['schema'], 1)
            self.assertEqual(decoded['snapshot'], 'tour-snap-1')
            self.assertEqual(set(decoded['responses']), {
                '/api/library', '/api/tour?at=', '/api/tour?at=create', '/api/tour-layout?at=create', '/api/tour?at=spells',
            })
            self.assertEqual(len(decoded['skeleton']['sections'][0]['topics']), 2)

            tour_cache._read_local.cache_clear()
            with patch.object(tour_cache, 'LOCAL_CACHE', root / 'tour.json.cache'):
                self.assertIsNone(tour_cache.local_cache(registry, curation_file))
                tour_cache.LOCAL_CACHE.write_bytes(payload)
                name, served = tour_cache.local_cache(registry, curation_file)
                self.assertEqual(served, payload)
                self.assertTrue(name.startswith('tour.'))
                # Snapshot change invalidates
                collections[0]['snapshot'] = 'snap-2'
                self.assertIsNone(tour_cache.local_cache(registry, curation_file))
                collections[0]['snapshot'] = 'snap-1'
                self.assertIsNotNone(tour_cache.local_cache(registry, curation_file))
                # Curation edit invalidates
                curation_file.write_text(json.dumps({
                    'schema': 2,
                    'sections': [{'key': 'start', 'label': {'zh': '开始2', 'en': 'Start 2'}, 'topics': []}],
                    'choices': {'5echm': {}},
                }), encoding='utf-8')
                self.assertIsNone(tour_cache.local_cache(registry, curation_file))

    def test_encode_local_scopes_and_pages_build_stamps_tour_html(self):
        with TemporaryDirectory() as td:
            root = Path(td).resolve()
            source, orig = _make_5echm_fixture(root)
            book = root / 'book'
            (book / 'topics').mkdir(parents=True)
            shutil.copyfile(source / 'topics' / 'spells.htm', book / 'topics' / 'spells.htm')
            from hashlib import sha256
            db_sha = sha256(orig.read_bytes()).hexdigest()
            page_sha = sha256((book / 'topics' / 'spells.htm').read_bytes()).hexdigest()
            reader = {
                'schema': 6,
                'snapshot': 'snap-fixture',
                'nodes': [{'id': '1', 'parent_id': '', 'label': 'Spells', 'path': 'topics/spells.htm', 'fragment': '', 'status': 'present'}],
                'default_page': 'topics/spells.htm',
                'pages': {'topics/spells.htm': {'title': 'Spells'}},
                'presentation_resources': [],
                'source_release': None,
            }
            reader_bytes = json.dumps(reader).encode('utf-8')
            (book / 'anna-reader.json').write_bytes(reader_bytes)
            manifest = {
                'reader_schema': 6,
                'database_sha256': db_sha,
                'outputs': {'anna-reader.json': sha256(reader_bytes).hexdigest(), 'topics/spells.htm': page_sha},
                'entries': {eid: {'path': 'topics/spells.htm', 'fragment': '', 'locatable': True} for eid in ('ver_1', 'ver_2', 'ver_3')},
            }
            (book / 'anna-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            curation = root / 'tour.json'
            curation.write_text(json.dumps({
                'schema': 2,
                'sections': [{'key': 'rules', 'label': {'zh': '规则', 'en': 'Rules'},
                              'topics': [{'key': 'spells', 'label': {'zh': '法术', 'en': 'Spells'}, 'contents': {'5echm': 'Spells'}}]}],
                'choices': {'5echm': {}},
            }), encoding='utf-8')

            def wrapped_export(stage, *args, **kwargs):
                return export_pages_data(stage, *args, curation=curation, **kwargs)

            built = root / 'built-pages'
            with patch('anna.publish.pages_build.stage_browser_runtime', lambda target: target.mkdir(parents=True, exist_ok=True)), \
                 patch('anna.publish.pages_build.export_pages_data', wrapped_export):
                build_pages(built, source, orig, book, '/5e-anna/', home_cache=True)

            self.assertFalse((built / 'tour-index.json').exists())
            tour_html = (built / 'tour' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('data-anna-tour="tour.', tour_html)
            tour_file = tour_html.split('data-anna-tour="', 1)[1].split('"', 1)[0]
            self.assertTrue((built / tour_file).is_file())
            cached = json.loads((built / tour_file).read_text(encoding='utf-8'))
            self.assertIn('/api/tour?at=', cached['responses'])
            self.assertIn('/api/tour?at=spells', cached['responses'])

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required to test web/tour-cache.js')
    def test_tour_cache_js_node_unit(self):
        tour_cache_url = (paths.PROJECT / 'web' / 'tour-cache.js').resolve().as_uri()
        script = f"""
import assert from 'node:assert/strict';
import {{ request as tourRequest, skeleton as tourSkeleton }} from {json.dumps(tour_cache_url)};

const cacheFile = 'tour.0123456789abcdef.json';
const payload = {{
  schema: 1,
  snapshot: 'tour-snap-1',
  skeleton: {{ sections: [{{ key: 'start', label: {{ zh: '开始', en: 'Start' }}, topics: [{{ id: 'create' }}] }}] }},
  responses: {{
    '/api/library': {{ collections: [{{ id: '5echm' }}, {{ id: 'our5etools' }}] }},
    '/api/tour?at=&libraries=5echm,our5etools': {{ id: '', kind: 'root', groups: [] }},
    '/api/tour?at=create&libraries=5echm,our5etools': {{ id: 'create', shape: 'detail', groups: [] }},
    '/api/tour-layout?at=create&libraries=5echm,our5etools': {{ status: 'ok', html: '<p>ok</p>' }},
  }},
}};
let fetchCalls = 0;
let releaseDelayed;
const delayedGate = new Promise(resolve => {{ releaseDelayed = resolve; }});
globalThis.fetch = async (url) => {{
  fetchCalls++;
  if (String(url).includes('deadbeef')) {{
    await delayedGate;
    return Response.json(payload);
  }}
  return Response.json(payload);
}};

// 1. Serves /api/library, root, level-1 topic, and level-1 layout with single shared fetch
const r0 = await tourRequest('/api/library', cacheFile);
const r1 = await tourRequest('/api/tour?at=&libraries=5echm%2Cour5etools', cacheFile);
const r2 = await tourRequest('/api/tour?at=create&snapshot=tour-snap-1&libraries=5echm,our5etools', cacheFile);
const r3 = await tourRequest('/api/tour-layout?at=create&snapshot=tour-snap-1&libraries=5echm%2Cour5etools', cacheFile);
assert.equal(fetchCalls, 1);
for (const r of [r0, r1, r2, r3]) {{
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('X-Anna-Tour-Cache'), '1');
}}
const skel = await tourSkeleton(cacheFile);
assert.equal(skel.sections[0].topics[0].id, 'create');
assert.equal(fetchCalls, 1);

// 2. Rejects snapshot mismatches, deeper paths, narrowed scopes, and invalid filenames
for (const miss of [
  '/api/library?works=1',
  '/api/tour?at=create&snapshot=other-snap&libraries=5echm,our5etools',
  '/api/tour?at=5echm:n924&libraries=5echm,our5etools',
  '/api/tour?at=&libraries=5echm,our5etools&rulesets=5r',
  '/api/tour?at=&libraries=5echm,our5etools&hide=5echm:phb',
  '/api/tour?at=create&extra=1',
  'https://evil.invalid/api/tour?at=',
]) {{
  assert.equal(await tourRequest(miss, cacheFile), undefined, miss);
}}
assert.equal(await tourRequest('/api/tour?at=', 'bad-file.json'), undefined);

// 3. AbortSignal rejects only the aborted caller
const ac = new AbortController();
const delayedFile = 'tour.deadbeefdeadbeef.json';
const pAbort = tourRequest('/api/tour?at=&libraries=5echm,our5etools', delayedFile, ac.signal);
const pKeep = tourRequest('/api/tour?at=create&libraries=5echm,our5etools', delayedFile);
ac.abort(new DOMException('cancel', 'AbortError'));
await assert.rejects(pAbort, {{ name: 'AbortError' }});
releaseDelayed();
assert.equal((await pKeep).status, 200);
"""
        proc = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)


if __name__ == '__main__':
    unittest.main()
