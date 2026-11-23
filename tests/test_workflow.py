"""Workflow and search migration regressions; all mutations use temporary fixtures."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
import anna.source.search as search


class WorkflowTests(unittest.TestCase):
    def make(self, cwd, *args, **kwargs):
        return subprocess.run(['make', '--no-print-directory', *args], cwd=cwd,
                              text=True, capture_output=True, **kwargs)

    def test_default_goal_points_to_usage_doc(self):
        result = self.make(PROJECT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('USAGE.md', result.stdout)
        # The command list lives in the doc; no separate help target duplicates it.
        self.assertNotEqual(self.make(PROJECT, '-n', 'help').returncode, 0)

    def test_default_single_port_command(self):
        result = self.make(PROJECT, '-n', 'up')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--port "8000"', result.stdout)
        self.assertNotIn('--book-port', result.stdout)

    def test_upper_reloads_the_same_single_port_service(self):
        result = self.make(PROJECT, '-n', 'upper')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--port "8000"', result.stdout)
        self.assertIn('--watch', result.stdout)
        self.assertNotIn('--book-port', result.stdout)

    def test_status_with_and_without_catalog(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for built in (False, True):
                if built:
                    (root / 'data/extracted').mkdir(parents=True)
                    (root / 'data/extracted/catalog.sqlite').write_bytes(b'fixture')
                result = self.make(root, '-f', str(PROJECT / 'Makefile'), 'status', f'WEB_DIR={root / "missing"}')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('not downloaded yet', result.stdout)
                self.assertIn('catalog: ' if built else 'catalog: not built yet (run make build)', result.stdout)

    def test_workspace_delegates_and_preserves_relative_path(self):
        # The outer Makefile is an optional consumer, not an anna dependency.
        wrapper = WORKSPACE / 'Makefile'
        if not wrapper.exists() or '5e-anna' not in wrapper.read_text():
            self.skipTest('standalone checkout without workspace wrapper')
        for command in ('up', 'upper'):
            result = self.make(WORKSPACE, '-n', command, 'WEB_DIR=custom mirror', 'ANNA_PORT=8123')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'WEB_DIR="{WORKSPACE / "custom mirror"}"', result.stdout)
            self.assertIn('-m anna.server.app', result.stdout)
            self.assertIn('--port "8123"', result.stdout)
            self.assertNotIn('git pull', result.stdout)
            self.assertEqual('--watch' in result.stdout, command == 'upper')

    def test_paths_independent_of_cwd_and_environment_override(self):
        code = 'import anna.paths as paths, anna.catalog.build as build, anna.source.search as book_search; print(str(paths.WEB)); assert paths.WEB == build.DEFAULT_WEB == book_search.WEB'
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, PYTHONPATH=str(PROJECT))
            env.pop('WEB_DIR', None)
            result = subprocess.run([sys.executable, '-c', code], cwd=td, env=env,
                                    text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), str(WORKSPACE / '5echm-web'))
            env['WEB_DIR'] = 'custom mirror'
            result = subprocess.run([sys.executable, '-c', code], cwd=td, env=env,
                                    text=True, capture_output=True, check=True)
            self.assertEqual(Path(result.stdout.strip()), (Path(td) / 'custom mirror').resolve())

    def test_our5etools_mirror_defaults_to_sibling_and_follows_environment(self):
        code = 'import anna.paths as paths; print(paths.OUR5ETOOLS)'
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, PYTHONPATH=str(PROJECT))
            env.pop('OUR5ETOOLS_DIR', None)
            result = subprocess.run([sys.executable, '-c', code], cwd=td, env=env,
                                    text=True, capture_output=True, check=True)
            self.assertEqual(Path(result.stdout.strip()), (WORKSPACE / 'our5etools').resolve())
            env['OUR5ETOOLS_DIR'] = 'custom mirror'
            result = subprocess.run([sys.executable, '-c', code], cwd=td, env=env,
                                    text=True, capture_output=True, check=True)
            self.assertEqual(Path(result.stdout.strip()), (Path(td) / 'custom mirror').resolve())

    def test_local_settings_supply_defaults_that_command_line_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            env = {k: v for k, v in os.environ.items() if k != 'OUR5ETOOLS_DIR'}

            def companion_source(*args):
                result = self.make(root, '-f', str(PROJECT / 'Makefile'), '-n', 'build',
                                   'COLLECTION=our5etools', *args, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout

            self.assertIn(f'--source "{root.parent / "our5etools"}"', companion_source())
            (root / 'local.mk').write_text(f'OUR5ETOOLS_DIR ?= {root / "local mirror"}\n')
            self.assertIn(f'--source "{root / "local mirror"}"', companion_source())
            override = companion_source(f'OUR5ETOOLS_DIR={root / "override"}')
            self.assertIn(f'--source "{root / "override"}"', override)
            self.assertNotIn('local mirror', override)

    def test_workspace_forwards_every_target_and_reports_unknown_ones(self):
        wrapper = WORKSPACE / 'Makefile'
        if not wrapper.exists() or '5e-anna' not in wrapper.read_text():
            self.skipTest('standalone checkout without workspace wrapper')
        for target, command in (('icons', 'scripts/build_icons.py'),
                                ('audit-links', '-m anna.catalog.reference_coverage'),
                                ('our5etools-audit', '-m anna.our5etools.audit')):
            with self.subTest(target=target):
                result = self.make(WORKSPACE, '-n', target)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(command, result.stdout)
        result = self.make(WORKSPACE, '-n', 'retired-target')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No rule to make target', result.stderr)

    def test_clone_existing_source_does_not_touch_it(self):
        with tempfile.TemporaryDirectory() as td:
            web = Path(td) / 'mirror with spaces'
            (web / 'topics').mkdir(parents=True)
            sentinel = web / 'topics' / 'source.htm'
            sentinel.write_text('unchanged')
            fivetools = Path(td) / '5etools cn'
            (fivetools / 'data').mkdir(parents=True)
            catalog = fivetools / 'data' / 'spells.json'
            catalog.write_text('{}')
            result = self.make(PROJECT, 'clone', f'WEB_DIR={web}', 'REPO=/does/not/exist',
                               f'5ETOOLS_DIR={fivetools}', '5ETOOLS_REPO=/does/not/exist')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sentinel.read_text(), 'unchanged')
            self.assertEqual(catalog.read_text(), '{}')
            self.assertFalse((web / '.git').exists())
            self.assertFalse((fivetools / '.git').exists())

    @staticmethod
    def repository(path, branch, files):
        path.mkdir()
        subprocess.run(['git', 'init', '-q', '-b', branch, str(path)], check=True)
        for name, text in files.items():
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(text)
        subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(path), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                        'commit', '-qm', 'fixture'], check=True)

    def test_clone_missing_source_from_local_repository(self):
        with tempfile.TemporaryDirectory() as td:
            source, dest = Path(td) / 'origin', Path(td) / 'mirror'
            fivetools_source, fivetools = Path(td) / '5etools-origin', Path(td) / '5etools-cn'
            self.repository(source, 'pages', {'topics/test.htm': 'source'})
            # Only data/ is checked out; the site's pages and docs stay in the repository.
            self.repository(fivetools_source, 'main', {'data/spells.json': '{"spell":[]}', 'docs/guide.md': 'guide'})
            result = self.make(PROJECT, 'clone', f'WEB_DIR={dest}', f'REPO={source}',
                               f'5ETOOLS_DIR={fivetools}', f'5ETOOLS_REPO=file://{fivetools_source}')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((dest / 'topics/test.htm').read_text(), 'source')
            self.assertEqual((fivetools / 'data/spells.json').read_text(), '{"spell":[]}')
            self.assertFalse((fivetools / 'docs').exists())

    def test_invalid_directory_rejected_without_deletion(self):
        with tempfile.TemporaryDirectory() as td:
            sentinel = Path(td) / 'keep'
            sentinel.write_text('keep')
            result = self.make(PROJECT, 'clone', f'WEB_DIR={td}')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Source has no topics directory', result.stderr)
            self.assertEqual(sentinel.read_text(), 'keep')
            # An existing 5etools-cn location without data/ is not replaced either.
            web = Path(td) / 'mirror'
            (web / 'topics').mkdir(parents=True)
            result = self.make(PROJECT, 'clone', f'WEB_DIR={web}', f'5ETOOLS_DIR={td}', '5ETOOLS_REPO=/does/not/exist')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('5etools-cn has no data directory', result.stderr)
            self.assertEqual(sentinel.read_text(), 'keep')

    def test_only_explicit_update_pulls(self):
        for command in ('up', 'upper', 'build'):
            result = self.make(PROJECT, '-n', command)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('git pull', result.stdout)
            self.assertNotIn(' pull ', result.stdout)
        result = self.make(PROJECT, '-n', 'update')
        self.assertIn('pull --ff-only', result.stdout)

    def test_removed_commands_are_gone(self):
        # make up is the sole HTTP service entry; the query module has no standalone server.
        for command in ('serve','open','inventory','index'):
            result=self.make(PROJECT,'-n',command)
            self.assertNotEqual(result.returncode,0)
        self.assertFalse((PROJECT/'scripts/serve_5echm.py').exists())
        self.assertEqual({p.name for p in (PROJECT/'scripts').glob('*.py')},{'build_icons.py','clean.py'})
        from anna.server import query
        self.assertFalse(hasattr(query,'main'))


class BookSearchIndexTests(unittest.TestCase):
    def test_loads_sqlite_book_search_when_signature_matches_and_rescans_on_change(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            web = root / 'web'
            (web / 'topics').mkdir(parents=True)
            page = web / 'topics/page.htm'
            page.write_text('<title>before</title><body><p>original body</p></body>')
            db_path = root / 'catalog.sqlite'
            conn = sqlite3.connect(db_path)
            conn.executescript('''
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE book_search(url TEXT PRIMARY KEY, src TEXT NOT NULL, title TEXT NOT NULL, cat TEXT NOT NULL, text TEXT NOT NULL);
            ''')
            conn.execute('INSERT INTO book_search VALUES(?,?,?,?,?)',
                         ('topics/page.htm', 'page.htm', 'from-sqlite', 'page.htm', 'indexed body'))
            conn.execute('INSERT INTO metadata VALUES(?,?)',
                         ('book_search_signature', search.index_signature(web)))
            conn.commit()
            conn.close()
            with patch.object(search, 'scan_entries', side_effect=AssertionError('unexpected disk rescan')):
                entries = search.load_index(db_path, web, quiet=True)
            self.assertEqual((entries[0]['title'], entries[0]['text']), ('from-sqlite', 'indexed body'))
            # Modifying any source page invalidates the SQLite signature and forces a live scan.
            page.write_text('<title>changed</title><body><p>updated body</p></body>')
            entries = search.load_index(db_path, web, quiet=True)
            self.assertEqual((entries[0]['title'], entries[0]['text']), ('changed', 'updated body'))

    def test_missing_topics_rejected(self):
        with tempfile.TemporaryDirectory() as td, patch.object(search, 'WEB', Path(td)):
            with self.assertRaises(SystemExit):
                search.load_index(None, Path(td), quiet=True)


if __name__ == '__main__':
    unittest.main()
