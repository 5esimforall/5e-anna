"""Comprehensive unit and real-corpus integration tests for the our5etools pipeline, presentation, and server."""
from __future__ import annotations

from contextlib import closing
import csv
from http.client import HTTPConnection
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from anna.our5etools import audit, books, build, inventory, render, semantics, types as otypes
from anna.paths import OUR5ETOOLS, OUR5ETOOLS_DATA
from anna.presentation.our5etools import (
    Our5etoolsCatalog,
    Our5etoolsReader,
    Our5etoolsTour,
    load_fivetools_collection,
    load_our5etools_collection,
)
from anna.presentation.expression import Query
from anna.presentation.reader import SCHEMA
from anna.presentation.tour import load as load_curation
from anna.server.app import create_server
from anna.publish.linked_book import build_book

try:
    from tests import test_linked_book as linked_fixtures
except ImportError:
    import test_linked_book as linked_fixtures


REAL_CORPUS_READY = OUR5ETOOLS.is_dir() and (OUR5ETOOLS_DATA / 'manifest.json').is_file()


def _make_mini_repo(root: Path) -> Path:
    """Create a minimal valid our5etools mirror for fast synthetic end-to-end build/audit/rollback tests."""
    src = root / 'mini_our5etools'
    for sub in (
        '__api',
        '__ours',
        'homebrew/_recipes',
        'homebrew/_data',
        'data/spells',
        'data/bestiary',
        'data/book',
        'data/generated',
        'audio/bestiary',
    ):
        (src / sub).mkdir(parents=True, exist_ok=True)

    parser_js = (
        'Parser.SOURCE_JSON_TO_FULL = {"PHB": "玩家手册", "XPHB": "玩家手册（2024）", "MM": "怪物手册"};\n'
        'Parser.SOURCE_JSON_TO_ABV = {"PHB": "PHB", "XPHB": "XPHB", "MM": "MM"};\n'
        'Parser.SOURCE_JSON_TO_DATE = {"PHB": "2014-08-19", "XPHB": "2024-09-17", "MM": "2014-09-30"};\n'
        'Parser.SOURCES_PARTNERED_WOTC = new Set();\n'
        'Parser.SOURCES_NON_STANDARD_WOTC = new Set();\n'
        'Parser.SOURCES_LEGACY_WOTC = new Set();\n'
        'Parser.SOURCES_AVAILABLE_DOCS_BOOK = {"PHB": "PHB"};\n'
        'Parser.SOURCES_AVAILABLE_DOCS_ADVENTURE = {};\n'
    )
    (src / '__ours/assets.json').write_text(json.dumps({'/js/parser.js': parser_js}), encoding='utf-8')
    (src / '__ours/ratings.json').write_text(json.dumps({}), encoding='utf-8')
    (src / '__api/status.json').write_text(
        json.dumps({'app': 'web-5etools', 'version': '1.9', 'build': 'test', 'data_date': '2026-01-01'}),
        encoding='utf-8',
    )
    (src / '__api/stats.json').write_text(
        json.dumps({'official': {'spell': 2, 'monster': 1}, 'brew': {}, 'ours': {}, 'books': 0, 'ourBooks': 0}),
        encoding='utf-8',
    )
    (src / '__api/books.json').write_text(json.dumps([]), encoding='utf-8')
    (src / '__api/tiers.json').write_text(json.dumps({}), encoding='utf-8')
    (src / '__api/ratings.json').write_text(json.dumps({'base': {}}), encoding='utf-8')
    (src / 'homebrew/_recipes/all.json').write_text(json.dumps({'docs': {}, 'packs': {}}), encoding='utf-8')
    (src / 'homebrew/index.json').write_text(json.dumps({'toImport': []}), encoding='utf-8')

    # Generated lookup stubs
    (src / 'data/generated/gendata-spell-source-lookup.json').write_text(json.dumps({}), encoding='utf-8')
    (src / 'data/generated/gendata-maps.json').write_text(json.dumps({}), encoding='utf-8')
    (src / 'data/generated/gendata-tag-redirects.json').write_text(json.dumps({}), encoding='utf-8')
    (src / 'data/generated/gendata-nav-adventure-book-index.json').write_text(json.dumps({}), encoding='utf-8')
    (src / 'data/spells/sources.json').write_text(json.dumps({}), encoding='utf-8')
    (src / 'data/bestiary/template.json').write_text(
        json.dumps({'monsterTemplate': [], 'legendaryGroupTemplate': []}),
        encoding='utf-8',
    )
    (src / 'data/bestiary/legendarygroups.json').write_text(json.dumps({'legendaryGroup': []}), encoding='utf-8')

    # Spells: 1 complete spell + 1 missing-base _copy + 1 Chinese-only spell with no ENG_name
    (src / 'data/spells/spells-phb.json').write_text(
        json.dumps(
            {
                'spell': [
                    {
                        'name': '火球术',
                        'ENG_name': 'Fireball',
                        'source': 'PHB',
                        'level': 3,
                        'school': 'V',
                        'time': [{'number': 1, 'unit': 'action'}],
                        'range': {'type': 'point', 'distance': {'type': 'feet', 'amount': 150}},
                        'components': {'v': True, 's': True, 'm': '蝙蝠粪与硫磺'},
                        'duration': [{'type': 'instant'}],
                        'entries': ['一道明亮的火焰射出，详见 {@condition 目盲|PHB}。'],
                    },
                    {
                        'name': '无英文名秘术',
                        'source': 'PHB',
                        'level': 1,
                        'school': 'A',
                        'time': [{'number': 1, 'unit': 'action'}],
                        'duration': [{'type': 'instant'}],
                        'components': {'v': True},
                        'entries': ['仅含中文名称的法术正文。'],
                    },
                    {
                        'name': '断链法术',
                        'ENG_name': 'Broken Spell',
                        'source': 'PHB',
                        '_copy': {'name': '不存在的基类', 'source': 'PHB'},
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )

    # Monster with local audio (.mp3 -> .opus) and remote unbundled image
    (src / 'audio/bestiary/vampire.opus').write_bytes(b'OggS' + b'\x00' * 128)
    (src / 'data/bestiary/bestiary-mm.json').write_text(
        json.dumps(
            {
                'monster': [
                    {
                        'name': '吸血鬼',
                        'ENG_name': 'Vampire',
                        'source': 'MM',
                        'size': ['M'],
                        'type': 'undead',
                        'ac': [16],
                        'hp': {'average': 144, 'formula': '17d8 + 68'},
                        'cr': '13',
                        'soundClip': {'type': 'internal', 'path': 'bestiary/vampire.mp3'},
                        'entries': [{'type': 'image', 'href': {'type': 'internal', 'path': 'bestiary/MM/Vampire.webp'}}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )

    # 1 official book + chapter
    (src / 'data/books.json').write_text(
        json.dumps(
            {
                'book': [
                    {
                        'id': 'PHB',
                        'name': '玩家手册（2014）',
                        'ENG_name': "Player's Handbook",
                        'source': 'PHB',
                        'group': 'core',
                        'contents': [{'name': '第一章：施法', 'headers': ['火球术']}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    (src / 'data/book/book-phb.json').write_text(
        json.dumps(
            {
                'data': [
                    {
                        'type': 'section',
                        'name': '第一章：施法',
                        'entries': [
                            '施放 {@spell 火球术|PHB} 时请注意范围。',
                            '<script>alert(1)</script> 安全文本。',
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    (src / 'data/adventures.json').write_text(json.dumps({'adventure': []}), encoding='utf-8')
    return src


class Our5etoolsUnitSemanticsAndRenderTests(unittest.TestCase):
    """Fast synthetic unit tests (run unconditionally without requiring local mirror)."""

    def test_unknown_booleans_never_coerced_to_false(self):
        # Missing-base spell: positive v=True preserved, all absent booleans stay None -> ['__unknown__']
        f_sp, q_sp, _ = otypes.extract_fields(
            'spell',
            'spell',
            {'name': '残缺法术', 'level': 3, 'components': {'v': True}},
            copy_status='missing_base',
        )
        self.assertIs(f_sp['verbal'], True)
        for bf in ('somatic', 'material', 'special_material', 'concentration', 'ritual'):
            self.assertIsNone(f_sp[bf], f'{bf} must stay None on missing_base')
            self.assertEqual(q_sp[bf], [otypes.UNKNOWN], f'quickref[{bf}] must be __unknown__')

        # Missing-base creature: legendary_actions and lair_actions stay None -> ['__unknown__']
        f_cr, q_cr, _ = otypes.extract_fields(
            'monster',
            'creature',
            {'name': '残缺生物', 'cr': '5', 'ac': [15], 'hp': {'average': 60}},
            copy_status='missing_base',
        )
        for bf in ('legendary_actions', 'lair_actions', 'has_local_audio'):
            self.assertIsNone(f_cr[bf])
            self.assertEqual(q_cr[bf], [otypes.UNKNOWN])

        # Missing-base magic item: attunement stays None -> ['__unknown__']
        f_mi, q_mi, _ = otypes.extract_fields(
            'item',
            'magic_item',
            {'name': '残缺奇物', 'rarity': 'rare', 'wondrous': True},
            copy_status='missing_base',
        )
        self.assertIsNone(f_mi['attunement'])
        self.assertEqual(q_mi['attunement'], [otypes.UNKNOWN])

        # Incomplete stub even with copy_status='ok' must not infer False from absent defining blocks
        f_stub, q_stub, _ = otypes.extract_fields('spell', 'spell', {'name': '仅名法术'}, copy_status='ok')
        for bf in ('verbal', 'somatic', 'material', 'special_material', 'concentration', 'ritual'):
            self.assertIsNone(f_stub[bf])
            self.assertEqual(q_stub[bf], [otypes.UNKNOWN])

    def test_synthetic_copy_missing_ambiguous_and_cycle_surface_warnings(self):
        with tempfile.TemporaryDirectory() as td:
            src = _make_mini_repo(Path(td))
            # Add ambiguous base (two identical-name spells in PHB) and a 2-node _copy cycle
            spells_path = src / 'data/spells/spells-phb.json'
            doc = json.loads(spells_path.read_text(encoding='utf-8'))
            doc['spell'].extend([
                {
                    'name': '重名基类',
                    'ENG_name': 'Dup Base',
                    'source': 'PHB',
                    'level': 1,
                    'school': 'A',
                    'time': [{'number': 1, 'unit': 'action'}],
                    'duration': [{'type': 'instant'}],
                    'components': {'v': True},
                    'entries': ['基类 1'],
                },
                {
                    'name': '重名基类',
                    'ENG_name': 'Dup Base',
                    'source': 'PHB',
                    'level': 2,
                    'school': 'V',
                    'time': [{'number': 1, 'unit': 'action'}],
                    'duration': [{'type': 'instant'}],
                    'components': {'s': True},
                    'entries': ['基类 2'],
                },
                {
                    'name': '歧义继承法术',
                    'source': 'PHB',
                    '_copy': {'name': '重名基类', 'source': 'PHB'},
                },
                {
                    'name': '循环甲',
                    'source': 'PHB',
                    '_copy': {'name': '循环乙', 'source': 'PHB'},
                },
                {
                    'name': '循环乙',
                    'source': 'PHB',
                    '_copy': {'name': '循环甲', 'source': 'PHB'},
                },
            ])
            spells_path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')

            inv = inventory.load_inventory(src)
            corpus = semantics.resolve_corpus(inv)
            by_name = {e['name']: e for e in corpus.entries}

            self.assertEqual(by_name['断链法术']['copy_status'], 'missing_base')
            self.assertEqual(by_name['歧义继承法术']['copy_status'], 'ambiguous_base')
            self.assertEqual(by_name['循环甲']['copy_status'], 'cycle')
            self.assertEqual(by_name['循环乙']['copy_status'], 'cycle')

            # Every broken _copy surfaces an explicit warning banner in rendered HTML (never silent half-data)
            for name in ('断链法术', '歧义继承法术', '循环甲'):
                entry = by_name[name]
                # Unresolved booleans must remain None / ['__unknown__']
                self.assertIsNone(entry['fields'].get('concentration'))
                self.assertEqual(entry['quickref']['concentration'], [otypes.UNKNOWN])
                rendered = render.render_entry_detail(entry)
                html_out = rendered['html']
                self.assertIn('o5e-copy-warning', html_out, f'{name} missing visible copy warning')

    def test_render_link_isolation_unresolved_and_xss_sanitization(self):
        lookup = {
            ('spell', '火球术', 'phb'): ['o5e_1111111111111111'],
            ('condition', '目盲', 'phb'): ['o5e_2222222222222222'],
            # Deliberately plant a 5echm-style ID to verify strict isolation rejects non-o5e_ targets
            ('spell', '越界法术', 'phb'): ['ver_5echm_forbidden'],
        }
        resolver = render.LinkResolver(lookup, default_source='PHB')
        ctx = render.RenderContext(resolver=resolver, default_source='PHB')

        html_ok = render.render_inline(
            '施放 {@spell 火球术|PHB} 造成 {@condition 目盲|PHB}，'
            '浏览 {@filter 三环塑能法术|spells|level=3|school=V}，'
            '以及 {@spell 不存在的法术|PHB} 和 {@spell 越界法术|PHB}。',
            ctx,
        )
        self.assertIn('data-entry="o5e_1111111111111111"', html_ok)
        self.assertIn('data-entry="o5e_2222222222222222"', html_ok)
        self.assertIn('collection=our5etools', html_ok)
        self.assertIn('f_spell_level=3', html_ok)
        self.assertIn('f_school=', html_ok)
        # Strict 5etools isolation: non-o5e_ target is NEVER emitted as clickable data-entry
        self.assertNotIn('ver_5echm_forbidden', html_ok)
        # Unresolved tag preserves readable display text
        self.assertIn('不存在的法术', html_ok)
        self.assertIn('ref-unresolved', html_ok)
        self.assertTrue(any(ev['status'] == 'unresolved' for ev in ctx.link_events))

        # XSS sanitization in inline text, links, and book chapters
        xss_text = (
            '<script>alert(1)</script>'
            '<img src=x onerror=alert(2)>'
            '{@link 恶意链接|javascript:alert(3)}'
        )
        xss_html = render.render_inline(xss_text, ctx)
        self.assertNotIn('<script', xss_html.lower())
        self.assertNotIn('<img', xss_html.lower())
        self.assertNotIn('javascript:', xss_html.lower())
        self.assertIn('&lt;script&gt;', xss_html)
        self.assertIn('&lt;img ', xss_html)

        # Book chapter XSS sanitization via build_reader_pages
        with tempfile.TemporaryDirectory() as td:
            src = _make_mini_repo(Path(td))
            inv = inventory.load_inventory(src)
            book_idx = books.collect_book_index(inv)
            out_dir = Path(td) / 'reader'
            books.build_reader_pages(book_idx, out_dir, resolver=resolver, snapshot='snap-xss')
            ch_html = (out_dir / 'books/phb/0.html').read_text(encoding='utf-8')
            self.assertNotIn('<script>alert(1)</script>', ch_html)
            self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', ch_html)

    def test_render_local_audio_vs_remote_unbundled_image(self):
        entry = {
            'id': 'o5e_aaaa000000000001',
            'type': 'creature',
            'raw_kind': 'monster',
            'name': '吸血鬼',
            'name_en': 'Vampire',
            'source_code': 'MM',
            'source_name': '怪物手册',
            'copy_status': 'ok',
            'fields': {'cr': '13', 'has_local_audio': True},
            'fluff': {
                'images': [
                    {'type': 'image', 'href': {'type': 'internal', 'path': 'bestiary/MM/Vampire.webp'}}
                ]
            },
            'data': {
                'name': '吸血鬼',
                'ENG_name': 'Vampire',
                'source': 'MM',
                'size': ['M'],
                'type': 'undead',
                'ac': [16],
                'hp': {'average': 144},
                'cr': '13',
                'soundClip': {'type': 'internal', 'path': 'bestiary/vampire.mp3'},
                '_local_audio': 'audio/bestiary/vampire.opus',
            },
        }
        rendered = render.render_entry_detail(
            entry,
            media_checker=lambda rel: rel in {'audio/bestiary/vampire.opus', 'bestiary/vampire.opus'},
        )
        self.assertIn('/our5etools-media/audio/bestiary/vampire.opus', rendered['html'])
        media_summary = rendered['media_summary']
        self.assertIn('audio/bestiary/vampire.opus', media_summary['local_media'])
        self.assertIn('远端', rendered['html'])
        self.assertTrue(any('Vampire.webp' in m for m in media_summary['remote_media']))
        self.assertFalse(any('Vampire.webp' in m for m in media_summary['local_media']))

    def test_command_line_names_the_mirror_and_the_data_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_mini_repo(root)
            out = root / 'our data'
            self.assertEqual(build.main(['--our5etools', str(src), '--our5etools-data', str(out), '--quiet']), 0)
            self.assertEqual(json.loads((out / 'manifest.json').read_text())['counts']['entries'], 5)
            with patch('sys.stdout', io.StringIO()):
                self.assertEqual(audit.main(['--our5etools-data', str(out), '--output', str(root / 'report.md')]), 0)
            self.assertTrue((root / 'report.md').is_file())

    def test_mini_build_idempotency_rollback_and_audit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_mini_repo(root)
            out = root / 'built'

            # 1. Initial build -> audit exit 0
            m1 = build.build(source=src, output=out, quiet=True)
            self.assertEqual(m1['counts']['entries'], 5)  # 3 spells + 1 monster + 1 book
            self.assertEqual(audit.main(['--our5etools-data', str(out)]), 0)
            self.assertTrue((out / 'audit.md').is_file())

            # 2. Idempotent rebuild -> identical snapshot and empty delta
            m2 = build.build(source=src, output=out, quiet=True)
            self.assertEqual(m1['snapshot'], m2['snapshot'])
            self.assertFalse(m2['delta']['is_initial'])
            self.assertEqual(m2['delta']['counts'], {})

            # 3. Failed update rollback: staging failure leaves previous snapshot intact & cleans .tmp/.prev
            sentinel = (out / 'manifest.json').read_bytes()
            with patch.object(build, '_validate_staged_output', side_effect=RuntimeError('simulated validation error')):
                with self.assertRaisesRegex(RuntimeError, 'simulated validation error'):
                    build.build(source=src, output=out, quiet=True)
            self.assertEqual((out / 'manifest.json').read_bytes(), sentinel)
            leftovers = [p.name for p in root.iterdir() if '.tmp.' in p.name or '.prev.' in p.name]
            self.assertEqual(leftovers, [])
            self.assertEqual(audit.main(['--our5etools-data', str(out)]), 0)

            # 4. Corrupted invariant -> audit exit 1
            db = sqlite3.connect(out / 'catalog.sqlite')
            db.execute(
                "UPDATE entries SET quickref_json = replace(quickref_json, '\"__unknown__\"', '\"false\"') "
                "WHERE copy_status = 'missing_base'"
            )
            db.commit()
            db.close()
            self.assertEqual(audit.main(['--our5etools-data', str(out)]), 1)

    def test_self_contained_bundle_and_opt_in_lazy_media(self):
        import email.message
        import io
        import shutil
        import socket
        import urllib.error
        import urllib.request

        class _FakeResp(io.BytesIO):
            def __init__(self, data: bytes, content_type: str, final_url: str, status: int = 200):
                super().__init__(data)
                self.status = status
                self._final_url = final_url
                msg = email.message.Message()
                msg['Content-Type'] = content_type
                msg['Content-Length'] = str(len(data))
                self.headers = msg

            def getcode(self) -> int:
                return self.status

            def geturl(self) -> str:
                return self._final_url

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_mini_repo(root)
            (src / 'data/bestiary/fluff-bestiary-mm.json').write_text(
                json.dumps(
                    {
                        'monsterFluff': [
                            {
                                'name': '吸血鬼',
                                'ENG_name': 'Vampire',
                                'source': 'MM',
                                'images': [{'type': 'image', 'href': {'type': 'internal', 'path': 'bestiary/MM/Vampire.webp'}}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )
            out = root / 'built'
            img_bundle = root / 'local-img'
            (img_bundle / 'custom').mkdir(parents=True)
            (img_bundle / 'custom/Hero.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 32)

            m1 = build.build(source=src, output=out, img_dir=img_bundle, quiet=True)
            self.assertGreaterEqual(m1['counts']['bundled_media_files'], 1)
            self.assertEqual(m1['counts']['bundled_img_dir_files'], 1)
            self.assertTrue((out / 'media/audio/bestiary/vampire.opus').is_file())
            self.assertTrue((out / 'media/img/custom/Hero.png').is_file())
            self.assertTrue((out / 'media-remotes.json').is_file())

            # Remove source repo completely to prove 100% runtime self-containment.
            shutil.rmtree(src)

            col_off = load_our5etools_collection(out, lazy_media=False)
            assert col_off is not None
            cat_off: Our5etoolsCatalog = col_off.provider
            rdr_off: Our5etoolsReader = col_off.reader
            self.assertIs(cat_off.meta()['lazy_media'], False)
            self.assertIs(rdr_off.navigation()['lazy_media'], False)

            vamp_id = cat_off.entries({'q': ['Vampire']})['rows'][0]['id']
            vamp_layout_off = cat_off.layout(vamp_id)
            self.assertIn('o5e-media-remote', vamp_layout_off['html'])
            self.assertIn('data-lazy-media="/our5etools-media/img/bestiary/MM/Vampire.webp"', vamp_layout_off['html'])
            self.assertNotIn('<img ', vamp_layout_off['html'])

            # Bundled audio and --img-dir images serve offline with zero source repo.
            audio_res = rdr_off.resolve_media_asset('audio/bestiary/vampire.opus')
            self.assertIsNotNone(audio_res)
            self.assertEqual(audio_res[0], (out / 'media/audio/bestiary/vampire.opus').resolve())
            hero_res = rdr_off.resolve_media_asset('img/custom/Hero.png')
            self.assertIsNotNone(hero_res)

            # Uncached remote image is strictly offline (404) and makes zero network calls when lazy_media=False.
            with patch.object(urllib.request.OpenerDirector, 'open', side_effect=AssertionError('unexpected fetch')):
                self.assertIsNone(rdr_off.resolve_media_asset('img/bestiary/MM/Vampire.webp'))

            # Opt-in lazy_media=True promotes markup and caches safely from allowlisted mirror fallback.
            col_on = load_our5etools_collection(out, lazy_media=True)
            assert col_on is not None
            cat_on: Our5etoolsCatalog = col_on.provider
            rdr_on: Our5etoolsReader = col_on.reader
            self.assertIs(cat_on.meta()['lazy_media'], True)
            self.assertIs(rdr_on.navigation()['lazy_media'], True)
            self.assertEqual(rdr_on.navigation()['cached_media'], ['img/custom/Hero.png'])
            vamp_layout_on = cat_on.layout(vamp_id)
            self.assertIn('<img src="/our5etools-media/img/bestiary/MM/Vampire.webp"', vamp_layout_on['html'])
            self.assertIn('loading="lazy" decoding="async" data-o5e-fallback="1"', vamp_layout_on['html'])

            seen_urls: list[str] = []
            webp_bytes = b'RIFF\x18\x00\x00\x00WEBPVP8 ' + b'\x00' * 16

            def _public_dns(host, *args, **kwargs):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]

            def _mock_open(opener_self, req, timeout=10):
                url = req.full_url
                seen_urls.append(url)
                if '5e.kiwee.top' in url:
                    raise urllib.error.HTTPError(url, 404, 'Not Found', email.message.Message(), None)
                return _FakeResp(webp_bytes, 'image/webp', url)

            with patch.object(socket, 'getaddrinfo', side_effect=_public_dns):
                with patch.object(urllib.request.OpenerDirector, 'open', autospec=True, side_effect=_mock_open):
                    cached = rdr_on.resolve_media_asset('img/bestiary/MM/Vampire.webp')

            self.assertIsNotNone(cached)
            self.assertEqual(cached[1], 'image/webp')
            self.assertEqual(cached[0].read_bytes(), webp_bytes)
            self.assertTrue(any('5e.kiwee.top' in u for u in seen_urls))
            self.assertTrue(any('raw.githubusercontent.com/5etools-mirror-3/5etools-img/main/' in u for u in seen_urls))

            # Once cached, default lazy_media=False serves it offline & promotes it cleanly.
            col_cached = load_our5etools_collection(out, lazy_media=False)
            assert col_cached is not None
            self.assertIn('img/bestiary/MM/Vampire.webp', col_cached.reader.navigation()['cached_media'])
            self.assertIsNotNone(col_cached.reader.resolve_media_asset('img/bestiary/MM/Vampire.webp'))
            self.assertIn('<img src="/our5etools-media/img/bestiary/MM/Vampire.webp"', col_cached.provider.layout(vamp_id)['html'])

            # Rebuild preserves previously cached lazy media under media/img/ & media/ext/.
            src_rebuild = _make_mini_repo(root / 'src_rebuild')
            m_rebuild = build.build(source=src_rebuild, output=out, img_dir=img_bundle, quiet=True)
            self.assertGreaterEqual(m_rebuild['counts']['preserved_cached_media'], 1)
            self.assertTrue((out / 'media/img/bestiary/MM/Vampire.webp').is_file())

            # Security guards on fresh lazy reader: SSRF private IP blocked, HTML rejected, unlisted ext rejected.
            rdr_sec = Our5etoolsReader(out, lazy_media=True)
            with patch.object(
                socket,
                'getaddrinfo',
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))],
            ):
                with patch.object(urllib.request.OpenerDirector, 'open', side_effect=AssertionError('ssrf must block dns')):
                    self.assertIsNone(rdr_sec.resolve_media_asset('img/bestiary/MM/Blocked.webp'))

            with patch.object(socket, 'getaddrinfo', side_effect=_public_dns):
                with patch.object(
                    urllib.request.OpenerDirector,
                    'open',
                    autospec=True,
                    side_effect=lambda o, r, timeout=10: _FakeResp(b'<html>404</html>', 'text/html', r.full_url),
                ):
                    self.assertIsNone(rdr_sec.resolve_media_asset('img/bestiary/MM/HtmlError.webp'))
            self.assertFalse((out / 'media/img/bestiary/MM/HtmlError.webp').exists())

            with patch.object(urllib.request.OpenerDirector, 'open', side_effect=AssertionError('unlisted ext blocked')):
                self.assertIsNone(rdr_sec.resolve_media_asset('ext/deadbeefdeadbeefdeadbeef.webp'))

    def test_reserved_5etools_collection_interface_and_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_mini_repo(root)
            out_our = root / 'built_our5etools'
            out_5e = root / 'built_5etools'
            empty_5e = root / 'empty_5etools'

            self.assertIsNone(load_fivetools_collection(empty_5e))

            m_our = build.build(source=src, output=out_our, quiet=True)
            m_5e = build.build(source=src, output=out_5e, collection_key='5etools', quiet=True)
            self.assertEqual(m_our['collection'], 'our5etools')
            self.assertEqual(m_5e['collection'], '5etools')
            self.assertEqual(audit.main(['--our5etools-data', str(out_our)]), 0)
            self.assertEqual(audit.main(['--our5etools-data', str(out_5e)]), 0)

            # Collection loaders strictly enforce matching manifest/reader collection key.
            self.assertIsNone(load_our5etools_collection(out_5e))
            self.assertIsNone(load_fivetools_collection(out_our))

            col_our = load_our5etools_collection(out_our)
            col_5e = load_fivetools_collection(out_5e)
            assert col_our is not None and col_5e is not None
            self.assertEqual(col_our.key, 'our5etools')
            self.assertEqual(col_our.book_base, '/our5etools-book/')
            self.assertEqual(col_our.media_base, '/our5etools-media/')
            self.assertEqual(col_5e.key, '5etools')
            self.assertEqual(col_5e.book_base, '/5etools-book/')
            self.assertEqual(col_5e.media_base, '/5etools-media/')

            # Mount both collections alongside native 5echm and verify isolated routing/exports.
            srv_root = root / 'srv'
            srv_root.mkdir()
            b_src, b_db = linked_fixtures.LinkedBuildTests().fixture(srv_root)
            b_book = srv_root / 'book'
            b_manifest, _ = build_book(b_src, b_db, b_book)
            server = create_server(b_db, b_book, b_manifest, 0, additional_collections=(col_our, col_5e))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def req(path: str):
                    with closing(HTTPConnection('127.0.0.1', server.server_port)) as conn:
                        conn.request('GET', path)
                        resp = conn.getresponse()
                        return resp.status, dict(resp.getheaders()), resp.read()

                for col in (col_our, col_5e):
                    with self.subTest(collection=col.key):
                        st, _, body = req('/api/meta?libraries='+col.key)
                        meta = json.loads(body)
                        self.assertEqual((st, meta['collection'], meta['tour_url'], meta['reader_url']),
                                         (200, col.key, '/tour', '/read'))
                        member = next(c for c in meta['collections'] if c['id'] == col.key)
                        self.assertEqual((member['tour'], member['reader'], member['snapshot']), (True, True, col.snapshot))

                st, _, body = req('/api/tour?libraries=5etools&at=5etools:nn_book_phb')
                view = json.loads(body)
                self.assertEqual((st, view['collection'], view['read']['root']), (200, '5etools', '5etools:n_book_phb'))
                st, _, body = req('/api/tour?libraries=5etools&at=packs-ours')
                self.assertEqual((st, json.loads(body)), (404, {'code': 'node_missing'}))

                st, hdrs, _ = req('/api/export?libraries=5etools&type=spell')
                self.assertEqual(st, 200)
                self.assertEqual(hdrs['Content-Disposition'], 'attachment; filename="5e-anna-5etools.csv"')

                st, _, nav_5e_raw = req('/api/reader?libraries=5etools')
                self.assertEqual(st, 200)
                nav_5e = json.loads(nav_5e_raw)
                (reader,) = nav_5e['collections']
                self.assertEqual((reader['id'], reader['page_base'], reader['snapshot']),
                                 ('5etools', '/5etools-book/', col_5e.snapshot))
                self.assertTrue(nav_5e['default_page'].startswith('5etools:books/'))
                st, _, body = req('/api/reader-page?libraries=our5etools&path=5etools:books/phb/0.html')
                self.assertEqual((st, json.loads(body)), (404, {'code': 'outside_library'}))

                st, _, ch_5e = req('/5etools-book/books/phb/0.html')
                self.assertEqual(st, 200)
                self.assertIn(b'/5etools-book/anna-assets/links.js', ch_5e)

                st, _, aud_5e = req('/5etools-media/audio/bestiary/vampire.opus')
                self.assertEqual(st, 200)
                self.assertGreater(len(aud_5e), 16)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()



@unittest.skipUnless(REAL_CORPUS_READY, 'Requires local our5etools repo and built data/our5etools')
class Our5etoolsRealCorpusInventoryAndSemanticsTests(unittest.TestCase):
    """Real-corpus integration tests over KuroNeko233333.github.io and built data/our5etools."""

    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((OUR5ETOOLS_DATA / 'manifest.json').read_text(encoding='utf-8'))
        cls.inv_json = json.loads((OUR5ETOOLS_DATA / 'inventory.json').read_text(encoding='utf-8'))
        cls.recon = json.loads((OUR5ETOOLS_DATA / 'reconciliation.json').read_text(encoding='utf-8'))
        cls.col = load_our5etools_collection()
        assert cls.col is not None
        cls.cat: Our5etoolsCatalog = cls.col.provider

    def _connect_ro(self) -> sqlite3.Connection:
        db = sqlite3.connect((OUR5ETOOLS_DATA / 'catalog.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        return db

    def test_inventory_files_exclusions_and_packages(self):
        self.assertEqual(self.inv_json['total_files'], 2048)
        self.assertEqual(len(self.inv_json['files']), 2048)
        excluded_prefixes = ('search/', 'data/generated/', 'homebrew/_bundle/', 'homebrew/_indexes/')
        for f in self.inv_json['files']:
            p = f['path']
            disp = f['disposition']
            self.assertIn(disp, inventory.DISPOSITIONS)
            self.assertTrue(f.get('reason'))
            if disp == 'entity_data':
                self.assertFalse(any(p.startswith(pref) for pref in excluded_prefixes), p)
                name = Path(p).name
                self.assertNotIn(name, inventory.TOOL_DATA_NAMES, p)
                self.assertFalse(name.startswith('foundry') and name.endswith('.json'), p)

        checks = self.inv_json['checks']
        self.assertTrue(checks['books_recipe_keys_match'])
        self.assertEqual(checks['missing_shard_files'], [])
        self.assertEqual(checks['missing_shard_keys'], [])
        self.assertEqual(checks['missing_site_packages'], [])
        self.assertEqual(self.manifest['counts']['brew_packages'], 321)
        self.assertEqual(self.manifest['counts']['site_packages'], 1)
        self.assertEqual(self.manifest['counts']['packages'], 323)

        # Confirm official sources count from live inventory parser (183 official sources)
        inv = inventory.load_inventory(OUR5ETOOLS)
        self.assertEqual(len(inv.official_sources), 183)
        self.assertTrue(any('likoko; DND5e新人科普漫画.json' in p['package_id'] for p in inv.packages))

    def test_identity_provenance_stacking_no_english_and_audit(self):
        with closing(self._connect_ro()) as db:
            total, distinct_ids, bad_prefix = db.execute(
                "SELECT count(*), count(DISTINCT id), sum(CASE WHEN id NOT LIKE 'o5e_%' THEN 1 ELSE 0 END) FROM entries"
            ).fetchone()
            self.assertEqual(total, 61480)
            self.assertEqual(distinct_ids, total)
            self.assertEqual(bad_prefix, 0)

            # classFeature & subclassFeature preserve owner_class, owner_subclass, owner_level
            cf = db.execute(
                "SELECT owner_class, owner_level FROM entries WHERE raw_kind='classFeature' AND owner_class IS NOT NULL AND owner_level IS NOT NULL LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(cf)
            scf = db.execute(
                "SELECT owner_class, owner_subclass, owner_level FROM entries "
                "WHERE raw_kind='subclassFeature' AND owner_class IS NOT NULL AND owner_subclass IS NOT NULL AND owner_level IS NOT NULL LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(scf)

        # Same-package 5e/5r reprint stacking for Fireball (official PHB + XPHB; query also matches Delayed Blast Fireball)
        stacked = self.cat.entries({'q': ['Fireball'], 'type': ['spell'], 'package': ['official'], 'versions': ['stacked']})
        each = self.cat.entries({'q': ['Fireball'], 'type': ['spell'], 'package': ['official'], 'versions': ['each']})
        self.assertEqual(each['total'], 4)
        self.assertEqual(stacked['total'], 2)
        fb_stacked = [r for r in stacked['rows'] if r['name_en'] == 'Fireball']
        fb_each = [r for r in each['rows'] if r['name_en'] == 'Fireball']
        self.assertEqual(len(fb_stacked), 1)
        self.assertEqual(len(fb_each), 2)
        self.assertEqual(len(fb_stacked[0]['versions']), 2)
        self.assertEqual({v['ruleset'] for v in fb_stacked[0]['versions']}, {'5e', '5r'})

        # Cross-package brew homonyms NEVER merge across distinct community packages
        brew_hom = self.cat.entries({'q': ['中和场域'], 'type': ['spell'], 'versions': ['stacked']})
        brew_rows = [r for r in brew_hom['rows'] if r['name'] == '中和场域' and r['package_id'].startswith('brew:')]
        self.assertGreaterEqual(len(brew_rows), 2)
        self.assertEqual(len({r['package_id'] for r in brew_rows}), len(brew_rows))

        # No-English-name entry (site comic) is searchable, locatable, and renderable
        comic_res = self.cat.entries({'q': ['新人向科普']})
        self.assertGreaterEqual(comic_res['total'], 1)
        comic_row = next(r for r in comic_res['rows'] if r['package_id'].startswith('site:'))
        self.assertEqual(comic_row['name_en'], '')
        comic_detail = self.cat.detail(comic_row['id'])
        self.assertIsNotNone(comic_detail.get('reader_location'))
        comic_layout = self.cat.layout(comic_row['id'])
        self.assertEqual(comic_layout['status'], 'ok')
        self.assertTrue(comic_layout['html'])

        # Full real-corpus audit confirms 0 unknown-boolean coercions and all 17 invariants pass
        report = audit.audit_data(OUR5ETOOLS_DATA)
        self.assertTrue(report['ok'], [i for i in report['invariants'] if not i['ok']])

    def test_real_semantics_copy_magicvariant_subrace_audio_and_fluff(self):
        with closing(self._connect_ro()) as db:
            # _copy + _mod resolved creature (Black Guard Drake / Guard Drake)
            drake = db.execute(
                "SELECT id, fields_json, copy_status FROM entries WHERE name_en = 'Black Guard Drake' LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(drake)
            self.assertEqual(drake['copy_status'], 'ok')
            drake_fields = json.loads(drake['fields_json'])
            self.assertEqual(drake_fields['cr'], '2')
            self.assertEqual(drake_fields['ac'], '14')
            self.assertEqual(drake_fields['hp'], '52')

            # magicvariant {=bonusWeapon} template substitution (+1 Ammunition)
            mv = db.execute(
                "SELECT id, layout_html FROM entries WHERE raw_kind = 'magicvariant' AND name_en = '+1 Ammunition' LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(mv)
            self.assertNotIn('{=bonusWeapon}', mv['layout_html'])
            self.assertIn('+1', mv['layout_html'])

            # subrace part_of relations exist
            subrace_rels = db.execute(
                "SELECT count(*) FROM relations r JOIN entries e ON e.id = r.source_id "
                "WHERE e.raw_kind = 'subrace' AND r.type = 'part_of'"
            ).fetchone()[0]
            self.assertGreater(subrace_rels, 50)

            # Vampire (MM): legendaryGroup + .mp3 -> .opus local audio, remote image NOT bundled
            vamp = db.execute(
                "SELECT id, fields_json, provenance_json, layout_html FROM entries "
                "WHERE name = '吸血鬼' AND source_id = 'src:MM' AND type = 'creature' LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(vamp)
            vf = json.loads(vamp['fields_json'])
            vp = json.loads(vamp['provenance_json'])
            self.assertIs(vf['has_local_audio'], True)
            self.assertIn('audio/bestiary/vampire.opus', vp['media_summary']['local_media'])
            self.assertTrue(any('Vampire.webp' in p for p in vp['media_summary']['remote_media']))
            self.assertFalse(any('Vampire.webp' in p for p in vp['media_summary']['local_media']))
            self.assertIn('/our5etools-media/audio/bestiary/vampire.opus', vamp['layout_html'])

        # Orphan fluff accounted for (> 0) and 0 unknown tags / unsupported blocks
        self.assertGreater(self.recon['fluff']['orphan_fluff_count'], 0)
        self.assertFalse(self.recon['unknown_tags'])
        self.assertFalse(self.recon['unsupported_blocks'])

    def test_real_links_strict_5etools_isolation(self):
        self.assertEqual(self.manifest['collection'], 'our5etools')
        with closing(self._connect_ro()) as db:
            meta_col = db.execute("SELECT value FROM metadata WHERE key = 'collection'").fetchone()
            self.assertEqual(meta_col[0] if meta_col else None, 'our5etools')
            bad_prov = db.execute(
                "SELECT count(*) FROM entries WHERE coalesce(json_extract(provenance_json, '$.collection'), '') != 'our5etools'"
            ).fetchone()[0]
            self.assertEqual(bad_prov, 0)
            bad_html = db.execute(
                'SELECT count(*) FROM entries WHERE layout_html LIKE \'%collection=5etools%\' OR layout_html LIKE \'%title="5etools 中%\''
            ).fetchone()[0]
            self.assertEqual(bad_html, 0)
            rows = db.execute(
                "SELECT id, layout_html FROM entries "
                "WHERE layout_html LIKE '%data-entry=%' OR layout_html LIKE '%ref-filter%' LIMIT 200"
            ).fetchall()
            self.assertTrue(rows)
            for r in rows:
                html_txt = r['layout_html']
                for target in re.findall(r'data-entry="([^"]+)"', html_txt):
                    self.assertTrue(target.startswith('o5e_'), f'{r["id"]} jumped to non-our5etools target {target}')
                for href in re.findall(r'class="ref-filter"[^>]*href="([^"]+)"', html_txt):
                    self.assertIn('collection=our5etools', href)

    def test_real_bundle_media_self_contained(self):
        self.assertEqual(self.manifest['counts']['bundled_media_files'], 816)
        self.assertTrue((OUR5ETOOLS_DATA / 'media-remotes.json').is_file())
        col_self = load_our5etools_collection(OUR5ETOOLS_DATA, lazy_media=False)
        self.assertIsNotNone(col_self)
        assert col_self is not None
        rdr: Our5etoolsReader = col_self.reader
        self.assertEqual(rdr.media_dir, (OUR5ETOOLS_DATA / 'media').resolve())
        audio = rdr.resolve_media_asset('audio/bestiary/vampire.opus')
        self.assertIsNotNone(audio)
        self.assertTrue(audio[0].is_file())


@unittest.skipUnless(REAL_CORPUS_READY, 'Requires local our5etools repo and built data/our5etools')
class Our5etoolsPresentationAndServerTests(unittest.TestCase):
    """Query, Facet, CSV, Tour, Reader, and HTTP Server integration tests."""

    @classmethod
    def setUpClass(cls):
        cls.col = load_our5etools_collection()
        assert cls.col is not None
        cls.cat: Our5etoolsCatalog = cls.col.provider
        cls.tour: Our5etoolsTour = cls.col.tour
        cls.reader: Our5etoolsReader = cls.col.reader

    def test_catalog_meta_entries_facets_and_csv_export(self):
        meta = self.cat.meta()
        self.assertEqual(meta['collection'], 'our5etools')
        self.assertEqual(len(meta['sources']), 541)
        self.assertEqual(len(meta['packages']), 323)
        self.assertEqual(set(meta['types']), set(otypes.TYPES))

        # Exact count parity between entries() and facets() across domain, negation, CR range, and package filters
        filter_cases = [
            {'type': ['spell'], 'f_spell_level': ['3'], 'not_f_school': ['塑能']},
            {'type': ['creature'], 'cr_min': ['5'], 'cr_max': ['10']},
            {'package': ['site:homebrew/likoko; DND5e新人科普漫画.json']},
        ]
        for params in filter_cases:
            with self.subTest(params=params):
                ent = self.cat.entries({**params, 'versions': ['each']})
                fac = self.cat.facets(params)
                self.assertEqual(ent['versions_total'], fac['total'])
                self.assertGreater(ent['versions_total'], 0)

        # Verify negation actually excludes 塑能
        not_evo = self.cat.entries({'type': ['spell'], 'f_spell_level': ['3'], 'not_f_school': ['塑能'], 'limit': ['50']})
        for row in not_evo['rows']:
            self.assertNotIn('塑能', row['quickref']['school'])

        # UTF-8 BOM CSV export row count matches exact filtered scope
        csv_params = {'type': ['spell'], 'f_spell_level': ['3'], 'source': ['src:PHB']}
        expected_n = self.cat.entries({**csv_params, 'versions': ['each']})['versions_total']
        raw_csv = self.cat.export(csv_params)
        self.assertTrue(raw_csv.startswith(b'\xef\xbb\xbf'))
        parsed_rows = list(csv.reader(io.StringIO(raw_csv.decode('utf-8-sig'))))
        self.assertEqual(len(parsed_rows) - 1, expected_n)
        col_idx = parsed_rows[0].index('资料库')
        self.assertEqual(parsed_rows[1][col_idx], 'our5etools')

        # Missing entry ID raises LookupError
        with self.assertRaises(LookupError):
            self.cat.detail('o5e_0000000000000000')
        with self.assertRaises(LookupError):
            self.cat.layout('o5e_0000000000000000')

    def test_cards_join_with_or_and_regroup_in_parentheses_in_entries_and_counts(self):
        OR, OPEN, CLOSE = ('or', ''), ('open', ''), ('close', '')
        line = lambda *pairs: Query.parse(pairs)
        total = lambda *pairs: self.cat.entries(line(*pairs, ('versions', 'each')))['versions_total']
        ids = lambda *pairs: {r['id'] for r in self.cat.entries(line(*pairs, ('versions', 'each'), ('limit', '1500')))['rows']}
        spell, third, evocation, dragon = ('type', 'spell'), ('f_spell_level', '3'), ('f_school', '塑能'), ('q', '龙')
        # ∨ binds tighter than ∧; parentheses regroup.
        self.assertEqual(ids(spell, third, OR, evocation), ids(spell, third) | ids(spell, evocation))
        grouped = total(OPEN, spell, third, CLOSE, OR, dragon)
        self.assertEqual(grouped, total(spell, third) + total(dragon) - total(spell, third, dragon))
        self.assertGreater(grouped, total(spell, third, OR, dragon))
        # Counts follow the tree, and the probe counts what each value placed at the caret would match.
        # Removing an alternative narrows the query, so only the type's removal adds matches.
        facets = self.cat.facets(line(spell, third, OR, dragon))
        self.assertEqual(facets['total'], total(spell, third, OR, dragon))
        self.assertEqual(facets['without'], {'0': total(third, OR, dragon) - facets['total']})
        probe = self.cat.facets(line(spell, third, OR, ('probe', '')))
        groups = {g['key']: {o['value']: o['count'] for o in g['options']} for g in probe['groups']}
        self.assertEqual(probe['probe_base'], total(spell, third))
        self.assertEqual(groups['f_school']['塑能'], total(spell, third, OR, evocation))
        self.assertEqual(groups['type']['creature'], total(spell, third, OR, ('type', 'creature')))

    def test_tour_books_and_dynamic_packages_link_to_their_own_entries(self):
        phb = self.tour.view('nn_book_phb')
        self.assertEqual(phb['read'], {'root': 'n_book_phb', 'page': 'books/phb/0.html'})
        chapter = next(g for g in phb['groups'] if g['key'] == 'contents')['items'][0]
        self.assertEqual(self.tour.view(chapter['id'])['read']['page'], 'books/phb/0.html')
        self.assertEqual(phb['anna']['query'], {'source': ['src:PHB']})
        self.assertEqual(phb['anna']['count'], self.cat.entries(phb['anna']['query'])['versions_total'])

        (packs,) = self.tour.view('packs-ours')['groups']
        self.assertEqual(packs['key'], 'packages')
        self.assertGreaterEqual(len(packs['items']), 34)
        self.assertEqual(self.tour.listing_count(load_curation()['topics']['packs-ours']),
                         len(packs['items']) + packs.get('more', 0))
        pack = self.tour.view(packs['items'][0]['id'])
        self.assertTrue(pack['provenance']['ours'])
        self.assertEqual(pack['anna']['query'], {'package': [pack['provenance']['package_id']]})
        self.assertEqual(pack['anna']['count'], self.cat.entries(pack['anna']['query'])['versions_total'])

    def test_server_routes_reader_media_and_security(self):
        reader_json = json.loads((OUR5ETOOLS_DATA / 'reader/anna-reader.json').read_text(encoding='utf-8'))
        locatable_id, location = next((eid, loc) for eid, loc in reader_json['entry_locations'].items() if loc['locatable'])

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, db = linked_fixtures.LinkedBuildTests().fixture(root)
            book = root / 'book'
            manifest, _ = build_book(source, db, book)
            server = create_server(db, book, manifest, 0, additional_collections=(self.col,))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def req(path: str, headers: dict[str, str] | None = None):
                    with closing(HTTPConnection('127.0.0.1', server.server_port)) as conn:
                        conn.request('GET', path, headers=headers or {})
                        resp = conn.getresponse()
                        body = resp.read()
                        return resp.status, dict(resp.getheaders()), body

                st, _, body = req('/api/reader?libraries=our5etools')
                self.assertEqual(st, 200)
                nav = json.loads(body)
                self.assertEqual(nav['schema'], SCHEMA)
                (reader,) = nav['collections']
                self.assertEqual((reader['id'], reader['page_base'], reader['snapshot']),
                                 ('our5etools', '/our5etools-book/', self.col.snapshot))
                self.assertEqual(nav['default_page'], 'our5etools:' + reader_json['default_page'])
                self.assertEqual([n['id'] for n in nav['nodes']], ['our5etools:' + n['id'] for n in reader_json['nodes']])

                page = '/api/reader-page?' + urlencode({'libraries': 'our5etools', 'path': 'our5etools:books/phb/0.html'})
                st, _, body = req(page + '&snapshot=' + reader['snapshot'])
                self.assertEqual(st, 200)
                page_data = json.loads(body)
                self.assertEqual((page_data['path'], page_data['collection']), ('our5etools:books/phb/0.html', 'our5etools'))
                st, _, body = req(page + '&snapshot=stale-snap')
                self.assertEqual(st, 409)
                self.assertEqual(json.loads(body)['code'], 'snapshot_changed')

                # A shared Anna entry remains readable outside the selection. Its Stella address does not
                # silently switch the reader's library or resolve the ID against the default book.
                st, _, body = req('/api/entry?' + urlencode({'id': locatable_id}))
                detail = json.loads(body)
                self.assertEqual((st, detail['id'], detail['collection']), (200, locatable_id, 'our5etools'))
                entry = '/api/reader-page?' + urlencode({'entry': 'our5etools:' + locatable_id})
                st, _, body = req(entry)
                self.assertEqual((st, json.loads(body)), (404, {'code': 'outside_library'}))
                st, _, body = req(entry + '&libraries=our5etools&snapshot=' + reader['snapshot'])
                located = json.loads(body)
                self.assertEqual((st, located['path'], located['fragment']),
                                 (200, 'our5etools:' + location['path'], location['fragment']))

                # /api/book-search
                st, _, body = req('/api/book-search?' + urlencode({'libraries': 'our5etools', 'keyword': '火球术'}))
                self.assertEqual(st, 200)
                hits = json.loads(body)
                self.assertGreater(hits['total'], 0)
                self.assertTrue(hits['results'])
                self.assertTrue(all(r['collection'] == 'our5etools' and r['path'].startswith('our5etools:') for r in hits['results']))

                # /our5etools-book/ chapter HTML, /definition/<id> 302, traversal & metadata 404
                st, _, body = req('/our5etools-book/books/phb/0.html')
                self.assertEqual(st, 200)
                self.assertIn(b'anna-bindings', body)

                st, hdrs, _ = req(f'/our5etools-book/definition/{locatable_id}')
                self.assertEqual(st, 302)
                self.assertTrue(hdrs.get('Location', '').startswith('/our5etools-book/'))

                for bad_book_path in (
                    '/our5etools-book/../manifest.json',
                    '/our5etools-book/%2e%2e/catalog.sqlite',
                    '/our5etools-book/anna-reader.json',
                ):
                    st, _, _ = req(bad_book_path)
                    self.assertEqual(st, 404, bad_book_path)

                # /our5etools-media/ audio 200 + 206 Range + non-media JSON rejection (404)
                st, hdrs, full_audio = req('/our5etools-media/audio/bestiary/vampire.opus')
                self.assertEqual(st, 200)
                self.assertGreater(len(full_audio), 64)

                st, hdrs, part_audio = req(
                    '/our5etools-media/audio/bestiary/vampire.opus',
                    headers={'Range': 'bytes=0-63'},
                )
                self.assertEqual(st, 206)
                self.assertEqual(len(part_audio), 64)
                self.assertEqual(part_audio, full_audio[:64])
                self.assertTrue(hdrs.get('Content-Range', '').startswith('bytes 0-63/'))

                for bad_media_path in (
                    '/our5etools-media/data/books.json',
                    '/our5etools-media/__api/status.json',
                    '/our5etools-media/../catalog.sqlite',
                ):
                    st, _, _ = req(bad_media_path)
                    self.assertEqual(st, 404, bad_media_path)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
