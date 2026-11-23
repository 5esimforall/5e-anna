"""Workflow and search migration regressions; all mutations use temporary fixtures."""
import os
from pathlib import Path
import re
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

    @staticmethod
    def our5etools_mirror(path, books=True):
        (path / 'data').mkdir(parents=True)
        if books:
            (path / '__api').mkdir()
            (path / '__api/books.json').write_text('[]')
        return path

    def test_local_settings_supply_defaults_that_command_line_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            env = {k: v for k, v in os.environ.items() if k != 'OUR5ETOOLS_DIR'}

            def companion(*args):
                return self.make(root, '-f', str(PROJECT / 'Makefile'), '-n', 'our5etools', *args, env=env)

            # Without local.mk the default is the sibling mirror, which this fixture does not have.
            self.assertIn(f'at {root.parent / "our5etools"};', companion().stderr)
            (root / 'local.mk').write_text(f'OUR5ETOOLS_DIR ?= {root / "local mirror"}\n')
            self.our5etools_mirror(root / 'local mirror')
            result = companion()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'--source "{root / "local mirror"}"', result.stdout)
            self.our5etools_mirror(root / 'override')
            override = companion(f'OUR5ETOOLS_DIR={root / "override"}')
            self.assertEqual(override.returncode, 0, override.stderr)
            self.assertIn(f'--source "{root / "override"}"', override.stdout)
            self.assertNotIn('local mirror', override.stdout)

    def stage_steps(self, root, *args):
        """Run `make -n` with fixture mirrors and reduce the printed recipe to one name per stage step."""
        env = {k: v for k, v in os.environ.items() if k not in {'WEB_DIR', 'OUR5ETOOLS_DIR', 'COUNTERPARTS_DIR'}}
        result = self.make(PROJECT, '-n', *args, f'WEB_DIR={root / "web mirror"}',
                           f'COUNTERPARTS_DIR={root / "counterparts mirror"}',
                           f'OUR5ETOOLS_DATA={root / "our data"}', f'PAGES_OUTPUT={root / "site"}',
                           'PAGES_BASE=/fixture/', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        steps = []
        for line in result.stdout.splitlines():
            module = re.search(r' -m anna\.(\S+)', line)
            if line.startswith('if [ ! -e '):
                steps.append('clone ' + Path(line.split('"')[1]).name)
            elif 'scripts/build_icons.py' in line:
                steps.append('icons')
            elif line.startswith('test -f data/extracted/catalog.sqlite'):
                steps.append('require built library')
            elif 'our5etools: skipped' in line:
                steps.append('skip our5etools')
            elif module:
                steps.append(module[1])
        return steps, result.stdout

    def test_each_stage_target_prints_only_its_commands(self):
        five_echm = ['clone web mirror', 'clone counterparts mirror', 'icons', 'catalog.build', 'server.linked_book']
        pages = ['require built library', 'server.pages_build']
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            mirror = f'OUR5ETOOLS_DIR={self.our5etools_mirror(root / "our mirror")}'
            expected = {
                ('5echm',): five_echm,
                ('linked',): ['server.linked_book'],
                ('our5etools', mirror): ['our5etools.build'],
                ('home',): ['server.home'],
                ('pages',): pages,
                ('build', mirror): five_echm + ['our5etools.build', 'server.home'],
                ('build', 'pages', mirror): five_echm + ['our5etools.build', 'server.home'] + pages,
                ('our5etools', 'home', 'pages', mirror): ['our5etools.build', 'server.home'] + pages,
            }
            for args, steps in expected.items():
                with self.subTest(args=args):
                    self.assertEqual(self.stage_steps(root, *args)[0], steps)
            steps, output = self.stage_steps(root, '5echm')
            self.assertIn(f'-m anna.catalog.build --counterparts "{root / "counterparts mirror"}"', output)
            steps, output = self.stage_steps(root, '5echm', 'NO_COUNTERPARTS=1')
            self.assertEqual(steps, ['clone web mirror', 'icons', 'catalog.build', 'server.linked_book'])
            self.assertIn('-m anna.catalog.build --no-counterparts', output)
            self.assertNotIn('counterparts mirror', output)
            steps, output = self.stage_steps(root, 'our5etools', mirror, f'OUR5ETOOLS_IMG_DIR={root / "img"}')
            self.assertIn(f'--source "{root / "our mirror"}" --output "{root / "our data"}" --img-dir "{root / "img"}"',
                          output)

    def test_pages_packages_the_built_libraries_with_its_own_home_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            steps, output = self.stage_steps(root, 'pages')
            self.assertIn(f'--base "/fixture/" --output "{root / "site"}"', output)
            self.assertIn('--home-cache', output)
            self.assertNotIn('--our5etools-data', output)
            # A built our5etools library is packaged; the check runs when the pages recipe starts.
            (root / 'our data').mkdir()
            (root / 'our data/manifest.json').write_text('{}')
            steps, output = self.stage_steps(root, 'pages')
            self.assertIn(f'--our5etools-data "{root / "our data"}" --home-cache', output)

    def test_pages_without_built_artifacts_stops_before_packaging(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            for built in ((), ('data/extracted/catalog.sqlite',)):
                for name in built:
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    (root / name).write_bytes(b'fixture')
                with self.subTest(built=built):
                    # PYTHON=false keeps a missing guard from starting a real Pages build.
                    result = self.make(root, '-f', str(PROJECT / 'Makefile'), 'pages',
                                       f'PAGES_OUTPUT={root / "site"}', 'PYTHON=false')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('pages: no built 5echm library in data/; run make build first', result.stderr)
                    self.assertNotIn('pages_build', result.stdout + result.stderr)
                    self.assertFalse((root / 'site').exists())

    def test_our5etools_stage_requires_a_complete_mirror(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            partial = self.our5etools_mirror(root / 'partial', books=False)
            for mirror in (root / 'missing', partial):
                with self.subTest(mirror=mirror.name):
                    result = self.make(PROJECT, '-n', 'our5etools', f'OUR5ETOOLS_DIR={mirror}')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f'our5etools: no mirror with data/ and __api/books.json at {mirror};',
                                  result.stderr)
                    self.assertNotIn('anna.our5etools.build', result.stdout)
                    # The full build skips the library instead of failing.
                    steps, output = self.stage_steps(root, 'build', f'OUR5ETOOLS_DIR={mirror}')
                    self.assertEqual(steps[-2:], ['skip our5etools', 'server.home'])
                    self.assertNotIn('anna.our5etools.build', output)

    def test_counterparts_mirror_follows_environment(self):
        env = dict(os.environ, COUNTERPARTS_DIR='/fixture/counterparts')
        result = self.make(PROJECT, '-n', '5echm', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--counterparts "/fixture/counterparts"', result.stdout)
        code = 'import anna.paths as paths; print(paths.COUNTERPARTS)'
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, PYTHONPATH=str(PROJECT))
            env.pop('COUNTERPARTS_DIR', None)
            result = subprocess.run([sys.executable, '-c', code], cwd=td, env=env,
                                    text=True, capture_output=True, check=True)
            self.assertEqual(Path(result.stdout.strip()), (WORKSPACE / '5etools-cn').resolve())
            env['COUNTERPARTS_DIR'] = 'custom mirror'
            result = subprocess.run([sys.executable, '-c', code], cwd=td, env=env,
                                    text=True, capture_output=True, check=True)
            self.assertEqual(Path(result.stdout.strip()), (Path(td) / 'custom mirror').resolve())

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
        result = self.make(WORKSPACE, '-n', 'build', 'pages')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(result.stdout.index('-m anna.server.home'), result.stdout.index('-m anna.server.pages_build'))
        result = self.make(WORKSPACE, '-n', 'retired-target')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No rule to make target', result.stderr)

    def test_clone_existing_source_does_not_touch_it(self):
        with tempfile.TemporaryDirectory() as td:
            web = Path(td) / 'mirror with spaces'
            (web / 'topics').mkdir(parents=True)
            sentinel = web / 'topics' / 'source.htm'
            sentinel.write_text('unchanged')
            counterparts = Path(td) / '5etools cn'
            (counterparts / 'data').mkdir(parents=True)
            catalog = counterparts / 'data' / 'spells.json'
            catalog.write_text('{}')
            result = self.make(PROJECT, 'clone', f'WEB_DIR={web}', 'REPO=/does/not/exist',
                               f'COUNTERPARTS_DIR={counterparts}', 'COUNTERPARTS_REPO=/does/not/exist')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sentinel.read_text(), 'unchanged')
            self.assertEqual(catalog.read_text(), '{}')
            self.assertFalse((web / '.git').exists())
            self.assertFalse((counterparts / '.git').exists())

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
            counterparts_source, counterparts = Path(td) / '5etools-origin', Path(td) / '5etools-cn'
            self.repository(source, 'pages', {'topics/test.htm': 'source'})
            # Only data/ is checked out; the site's pages and docs stay in the repository.
            self.repository(counterparts_source, 'main', {'data/spells.json': '{"spell":[]}', 'docs/guide.md': 'guide'})
            result = self.make(PROJECT, 'clone', f'WEB_DIR={dest}', f'REPO={source}',
                               f'COUNTERPARTS_DIR={counterparts}', f'COUNTERPARTS_REPO=file://{counterparts_source}')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((dest / 'topics/test.htm').read_text(), 'source')
            self.assertEqual((counterparts / 'data/spells.json').read_text(), '{"spell":[]}')
            self.assertFalse((counterparts / 'docs').exists())

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
            result = self.make(PROJECT, 'clone', f'WEB_DIR={web}', f'COUNTERPARTS_DIR={td}', 'COUNTERPARTS_REPO=/does/not/exist')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('5etools-cn has no data directory', result.stderr)
            self.assertEqual(sentinel.read_text(), 'keep')

    def test_only_explicit_update_pulls(self):
        for command in ('up', 'upper', 'build', '5echm', 'pages'):
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
