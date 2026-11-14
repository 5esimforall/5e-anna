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
sys.path.insert(0, str(PROJECT / 'scripts'))
import book_search as search


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

    def test_status_with_and_without_search_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for cached in (False, True):
                if cached:
                    (root / '.cache').mkdir()
                    (root / '.cache/5echm-search.pkl').write_bytes(b'fixture')
                result = self.make(root, '-f', str(PROJECT / 'Makefile'), 'status', f'WEB_DIR={root / "missing"}')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('not downloaded yet', result.stdout)
                self.assertIn('fingerprint checked at start' if cached else 'make up builds it', result.stdout)

    def test_workspace_delegates_and_preserves_relative_path(self):
        # The outer Makefile is an optional consumer, not an anna dependency.
        wrapper = WORKSPACE / 'Makefile'
        if not wrapper.exists() or '5e-anna' not in wrapper.read_text():
            self.skipTest('standalone checkout without workspace wrapper')
        for command in ('up', 'upper'):
            result = self.make(WORKSPACE, '-n', command, 'WEB_DIR=custom mirror', 'ANNA_PORT=8123')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'WEB_DIR="{WORKSPACE / "custom mirror"}"', result.stdout)
            self.assertIn('scripts/up.py', result.stdout)
            self.assertIn('--port "8123"', result.stdout)
            self.assertNotIn('git pull', result.stdout)
            self.assertEqual('--watch' in result.stdout, command == 'upper')

    def test_paths_independent_of_cwd_and_environment_override(self):
        code = 'import paths, build, inventory, book_search; print(str(paths.WEB)); assert paths.WEB == build.DEFAULT_WEB == inventory.WEB == book_search.WEB'
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, PYTHONPATH=str(PROJECT / 'scripts'))
            env.pop('WEB_DIR', None)
            result = subprocess.run([sys.executable, '-c', code], cwd=td, env=env,
                                    text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), str(WORKSPACE / '5echm-web'))
            env['WEB_DIR'] = 'custom mirror'
            result = subprocess.run([sys.executable, '-c', code], cwd=td, env=env,
                                    text=True, capture_output=True, check=True)
            self.assertEqual(Path(result.stdout.strip()), (Path(td) / 'custom mirror').resolve())

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
            for command in ('clone', 'clean'):
                result = self.make(PROJECT, command, f'WEB_DIR={td}')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Refusing to delete' if command == 'clean' else 'Source has no topics directory', result.stderr)
                self.assertEqual(sentinel.read_text(), 'keep')
            # An existing 5etools-cn location without data/ is not replaced either.
            web = Path(td) / 'mirror'
            (web / 'topics').mkdir(parents=True)
            result = self.make(PROJECT, 'clone', f'WEB_DIR={web}', f'5ETOOLS_DIR={td}', '5ETOOLS_REPO=/does/not/exist')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('5etools-cn has no data directory', result.stderr)
            self.assertEqual(sentinel.read_text(), 'keep')

    def test_only_explicit_update_pulls(self):
        for command in ('up', 'upper', 'build', 'index', 'inventory'):
            result = self.make(PROJECT, '-n', command)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('git pull', result.stdout)
            self.assertNotIn(' pull ', result.stdout)
        result = self.make(PROJECT, '-n', 'update')
        self.assertIn('pull --ff-only', result.stdout)
        self.assertIn('--rebuild', result.stdout)

    def test_removed_commands_are_gone(self):
        # The query-only server is scripts/serve.py; make up is the single run entry.
        for command in ('serve','open','anna'):
            result=self.make(PROJECT,'-n',command)
            self.assertNotEqual(result.returncode,0)
        self.assertFalse((PROJECT/'scripts/serve_5echm.py').exists())


class SearchCacheTests(unittest.TestCase):
    def test_cache_is_scoped_to_source_and_commit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / 'cache/index.pkl'
            first, second = root / 'first', root / 'second'
            for web, title in ((first, 'first'), (second, 'second')):
                (web / 'topics').mkdir(parents=True)
                (web / 'topics/page.htm').write_text(f'<title>{title}</title><body>{title}</body>')
            with patch.object(search, 'CACHE', cache), patch.object(search, 'web_commit', return_value='same-commit'):
                with patch.object(search, 'WEB', first):
                    search.build_index(quiet=True)
                    with patch.object(search, 'load_toc_titles', side_effect=AssertionError('cache not reused')):
                        self.assertEqual(search.build_index(quiet=True)[0]['title'], 'first')
                with patch.object(search, 'WEB', second):
                    self.assertEqual(search.build_index(quiet=True)[0]['title'], 'second')
                    (second / 'topics/page.htm').write_text('<title>changed</title>')
                    # Working-tree changes invalidate the index even without a new commit.
                    self.assertEqual(search.build_index(quiet=True)[0]['title'], 'changed')
                    with patch.object(search, 'web_commit', return_value='new-commit'):
                        self.assertEqual(search.build_index(quiet=True)[0]['title'], 'changed')

    def test_unversioned_source_is_rescanned(self):
        with tempfile.TemporaryDirectory() as td:
            web = Path(td)
            (web / 'topics').mkdir()
            page = web / 'topics/page.htm'
            page.write_text('<title>before</title>')
            with patch.object(search, 'WEB', web), patch.object(search, 'CACHE', web / 'index.pkl'), patch.object(search, 'web_commit', return_value='unknown'):
                search.build_index(quiet=True)
                page.write_text('<title>after</title>')
                self.assertEqual(search.build_index(quiet=True)[0]['title'], 'after')

    def test_missing_topics_rejected(self):
        with tempfile.TemporaryDirectory() as td, patch.object(search, 'WEB', Path(td)):
            with self.assertRaises(SystemExit):
                search.build_index(quiet=True)


if __name__ == '__main__':
    unittest.main()
