"""The source lock names one snapshot for local builds and Pages; mirrors are read, never moved."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sources', PROJECT / 'scripts/sources.py')
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)

IDENTITY = ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false']
PIN = 'a' * 40


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True, text=True, capture_output=True).stdout.strip()


def commit(repo, files):
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, 'add', '.')
    git(repo, *IDENTITY, 'commit', '-qm', 'fixture')
    return git(repo, 'rev-parse', 'HEAD')


def repository(path, branch, files):
    path.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', '-b', branch, str(path)], check=True)
    return commit(path, files)


def mirror(repo, branch, revision, **extra):
    return {'repo': repo, 'branch': branch, 'revision': revision, **extra}


def write_lock(path, **mirrors):
    path.write_text(json.dumps({'schema': 1, 'mirrors': mirrors}, indent=2) + '\n')
    return path


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = sources.main([str(arg) for arg in argv])
    return code, out.getvalue(), err.getvalue()


class LockFormatTests(unittest.TestCase):
    def test_project_lock_pins_every_mirror_in_canonical_form(self):
        data = sources.load(sources.LOCK)
        self.assertEqual(list(data['mirrors']), ['web', 'counterparts', 'our5etools'])
        self.assertEqual(data['mirrors']['web']['branch'], 'pages')
        self.assertEqual(data['mirrors']['counterparts']['sparse'], ['data'])
        # Recording a revision rewrites the file in this form, so updates change only the revision lines.
        self.assertEqual(json.dumps(data, indent=2, ensure_ascii=False) + '\n', sources.LOCK.read_text())

    def test_missing_or_malformed_lock_is_rejected(self):
        good = mirror('https://example.invalid/r.git', 'main', PIN)
        cases = {
            'missing': None,
            'not json': '{',
            'wrong schema': json.dumps({'schema': 2, 'mirrors': {'web': good}}),
            'extra top-level field': json.dumps({'schema': 1, 'mirrors': {'web': good}, 'note': 'x'}),
            'no mirrors': json.dumps({'schema': 1, 'mirrors': {}}),
            'missing revision': json.dumps({'schema': 1, 'mirrors': {'web': {'repo': 'r', 'branch': 'main'}}}),
            'short revision': json.dumps({'schema': 1, 'mirrors': {'web': good | {'revision': 'abc1234'}}}),
            'unknown field': json.dumps({'schema': 1, 'mirrors': {'web': good | {'tag': 'v1'}}}),
            'repo with spaces': json.dumps({'schema': 1, 'mirrors': {'web': good | {'repo': 'a b'}}}),
            'sparse not a list': json.dumps({'schema': 1, 'mirrors': {'web': good | {'sparse': 'data'}}}),
        }
        with tempfile.TemporaryDirectory() as td:
            for name, text in cases.items():
                with self.subTest(name):
                    lock = Path(td) / 'sources.json'
                    lock.unlink(missing_ok=True)
                    if text is not None:
                        lock.write_text(text)
                    code, out, err = run('--lock', lock, 'get', 'web', 'repo')
                    self.assertEqual((code, out), (1, ''))
                    self.assertTrue(err.startswith(f'sources: {lock}: '), err)

    def test_get_prints_one_field_and_rejects_unknown_names(self):
        with tempfile.TemporaryDirectory() as td:
            lock = write_lock(Path(td) / 'sources.json', web=mirror('https://example.invalid/w.git', 'pages', PIN),
                              counterparts=mirror('https://example.invalid/c.git', 'main', PIN, sparse=['data', 'docs']))
            self.assertEqual(run('--lock', lock, 'get', 'web', 'branch')[:2], (0, 'pages\n'))
            self.assertEqual(run('--lock', lock, 'get', 'counterparts', 'sparse')[:2], (0, 'data docs\n'))
            self.assertEqual(run('--lock', lock, 'get', 'web', 'sparse')[:2], (0, '\n'))
            code, _, err = run('--lock', lock, 'get', 'our5etools', 'repo')
            self.assertEqual(code, 1)
            self.assertIn("unknown mirror 'our5etools'; the lock names web, counterparts", err)
            code, _, err = run('--lock', lock, 'get', 'web', 'url')
            self.assertEqual(code, 1)
            self.assertIn("unknown field 'url'", err)


class MirrorStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_and_check_compare_heads_without_moving_them(self):
        pinned = self.root / 'pinned'
        head = repository(pinned, 'main', {'a.txt': 'a'})
        drifted = self.root / 'drifted'
        drifted_head = repository(drifted, 'main', {'b.txt': 'b'})
        # A copy without its own .git sits inside another repository; its enclosing HEAD must not count.
        enclosing = self.root / 'enclosing'
        repository(enclosing, 'main', {'copy/data/x.json': '{}'})
        lock = write_lock(self.root / 'sources.json', web=mirror('r', 'main', head),
                          counterparts=mirror('r', 'main', PIN), our5etools=mirror('r', 'main', PIN))
        code, out, err = run('--lock', lock, 'status', f'web={pinned}', f'counterparts={drifted}',
                             f'our5etools={self.root / "missing"}')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(out.splitlines(), [
            f'web: pinned {head[:7]}, HEAD matches',
            f'counterparts: pinned aaaaaaa, HEAD {drifted_head[:7]} differs',
            f'our5etools: pinned aaaaaaa, mirror not present at {self.root / "missing"}',
        ])
        code, out, _ = run('--lock', lock, 'status', f'our5etools={enclosing / "copy"}')
        self.assertEqual(out.strip(), f'our5etools: pinned aaaaaaa, {enclosing / "copy"} is not a Git checkout')
        # A build warns about drift and carries on; a matching mirror stays silent.
        self.assertEqual(run('--lock', lock, 'check', f'web={pinned}'), (0, '', ''))
        code, out, err = run('--lock', lock, 'check', f'web={pinned}', f'counterparts={drifted}')
        self.assertEqual((code, out), (0, ''))
        self.assertEqual(err.splitlines(), [f'warning: counterparts: pinned aaaaaaa, HEAD {drifted_head[:7]} differs; '
                                            'this build reads the mirror as it is, Pages builds the pin'])
        self.assertEqual(git(drifted, 'rev-parse', 'HEAD'), drifted_head)

    def test_record_pins_published_heads_only(self):
        origin = self.root / 'origin'
        first = repository(origin, 'main', {'a.txt': 'one'})
        published = self.root / 'published'
        subprocess.run(['git', 'clone', '-q', f'file://{origin}', str(published)], check=True)
        second = commit(origin, {'a.txt': 'two'})
        git(published, 'pull', '-q', '--ff-only')
        local = self.root / 'local'
        subprocess.run(['git', 'clone', '-q', f'file://{origin}', str(local)], check=True)
        unpublished = commit(local, {'b.txt': 'local only'})
        lock = write_lock(self.root / 'sources.json', web=mirror(f'file://{origin}', 'main', first),
                          counterparts=mirror(f'file://{origin}', 'main', second, sparse=['data']),
                          our5etools=mirror(f'file://{origin}', 'main', first))
        before = lock.read_text()
        code, out, err = run('--lock', lock, 'record', f'web={published}', f'counterparts={published}',
                             f'our5etools={local}')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(out.splitlines(), [
            f'web: pin {first[:7]} -> {second[:7]}',
            f'counterparts: pin {second[:7]} unchanged',
            f'our5etools: HEAD {unpublished[:7]} is not on origin/main; pin {first[:7]} unchanged',
        ])
        after = lock.read_text()
        self.assertEqual(after, before.replace(first, second, 1))
        self.assertEqual(sources.load(lock)['mirrors']['our5etools']['revision'], first)
        # Nothing changes for a missing mirror, and an unchanged lock is not rewritten.
        stat = lock.stat()
        code, out, _ = run('--lock', lock, 'record', f'web={published}', f'our5etools={self.root / "missing"}')
        self.assertEqual(out.splitlines(), [f'web: pin {second[:7]} unchanged',
                                            f'our5etools: mirror not present at {self.root / "missing"}; '
                                            f'pin {first[:7]} unchanged'])
        self.assertEqual((lock.read_text(), lock.stat().st_mtime_ns), (after, stat.st_mtime_ns))
        self.assertEqual(git(local, 'rev-parse', 'HEAD'), unpublished)

    def test_record_rejects_unknown_mirrors_without_writing(self):
        lock = write_lock(self.root / 'sources.json', web=mirror('r', 'main', PIN))
        before = lock.read_text()
        for pair in ('pages=/x', 'web'):
            with self.subTest(pair=pair):
                code, _, err = run('--lock', lock, 'record', pair)
                self.assertEqual(code, 1)
                self.assertTrue(err.startswith('sources: '), err)
        self.assertEqual(lock.read_text(), before)


class MakeSourcesTests(unittest.TestCase):
    """make clone/update/status read the lock; only temporary repositories take part."""

    def make(self, *args, lock):
        env = {k: v for k, v in os.environ.items()
               if k not in {'REPO', 'BRANCH', 'COUNTERPARTS_REPO', 'COUNTERPARTS_BRANCH', 'WEB_DIR',
                            'COUNTERPARTS_DIR', 'OUR5ETOOLS_DIR'}}
        return subprocess.run(['make', '--no-print-directory', *args, f'SOURCES_LOCK={lock}',
                               f'WEB_DIR={self.web}', f'COUNTERPARTS_DIR={self.counterparts}',
                               f'OUR5ETOOLS_DIR={self.root / "no our5etools"}'],
                              cwd=PROJECT, env=env, text=True, capture_output=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.web, self.counterparts = self.root / 'web mirror', self.root / 'counterparts mirror'

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_clones_from_the_lock_pulls_and_records_new_heads(self):
        web_origin, counterparts_origin = self.root / 'web-origin', self.root / 'counterparts-origin'
        web_first = repository(web_origin, 'pages', {'topics/page.htm': 'one'})
        counterparts_first = repository(counterparts_origin, 'main', {'data/spells.json': '{}', 'docs/guide.md': 'g'})
        lock = write_lock(self.root / 'sources.json',
                          web=mirror(f'file://{web_origin}', 'pages', web_first),
                          counterparts=mirror(f'file://{counterparts_origin}', 'main', counterparts_first,
                                              sparse=['data']),
                          our5etools=mirror('https://example.invalid/o.git', 'main', PIN))
        result = self.make('update', lock=lock)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.web / 'topics/page.htm').read_text(), 'one')
        self.assertTrue((self.counterparts / 'data/spells.json').is_file())
        self.assertFalse((self.counterparts / 'docs').exists())
        self.assertIn(f'web: pin {web_first[:7]} unchanged', result.stdout)
        self.assertIn('our5etools: mirror not present', result.stdout)

        web_second = commit(web_origin, {'topics/page.htm': 'two'})
        counterparts_second = commit(counterparts_origin, {'data/spells.json': '{"spell":[]}'})
        result = self.make('status', lock=lock)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'web: pinned {web_first[:7]}, HEAD matches', result.stdout)

        result = self.make('update', lock=lock)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.web / 'topics/page.htm').read_text(), 'two')
        self.assertIn(f'web: pin {web_first[:7]} -> {web_second[:7]}', result.stdout)
        self.assertIn(f'counterparts: pin {counterparts_first[:7]} -> {counterparts_second[:7]}', result.stdout)
        pins = sources.load(lock)['mirrors']
        self.assertEqual((pins['web']['revision'], pins['counterparts']['revision'], pins['our5etools']['revision']),
                         (web_second, counterparts_second, PIN))

        # A mirror that moves on its own is reported as drift; status does not fail or move it.
        local = commit(self.web, {'topics/local.htm': 'local'})
        result = self.make('status', lock=lock)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'web: pinned {web_second[:7]}, HEAD {local[:7]} differs', result.stdout)
        self.assertIn(f'counterparts: pinned {counterparts_second[:7]}, HEAD matches', result.stdout)
        self.assertEqual(git(self.web, 'rev-parse', 'HEAD'), local)

    def test_malformed_lock_stops_commands_that_read_it(self):
        lock = self.root / 'sources.json'
        lock.write_text('{"schema": 1}')
        result = self.make('status', lock=lock)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f'sources: {lock}: expected', result.stderr)
        result = self.make('clone', lock=lock)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.web.exists())


if __name__ == '__main__':
    unittest.main()
