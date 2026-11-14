"""Cleanup is confined to reproducible local outputs, never shared inputs."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('clean', PROJECT / 'scripts/clean.py')
cleaner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleaner)


class CleanTests(unittest.TestCase):
    def test_generated_data_removed_and_inputs_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (*cleaner.GENERATED, '.build-interrupted', 'anna/__pycache__'):
                folder = root / name
                folder.mkdir(parents=True)
                (folder / 'generated').write_text('generated')
            (root / 'web').mkdir()
            for name in cleaner.ICONS:
                (root / 'web' / name).write_bytes(b'generated')
            inputs = ['.git/objects/keep', 'reviews/decisions.jsonl', 'rules/core/library.json',
                      'assets/branding/anna.png', 'anna/source.py', 'baselines/current/manifest.json']
            for name in inputs:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('input')
            (root / '.DS_Store').touch()
            (root / 'anna/stray.pyc').touch()
            removed = cleaner.clean(root)
            self.assertTrue(set(cleaner.GENERATED) <= set(removed))
            self.assertTrue(all(not (root / 'web' / name).exists() for name in cleaner.ICONS))
            self.assertFalse((root / 'anna/__pycache__').exists())
            self.assertFalse((root / 'anna/stray.pyc').exists())
            self.assertFalse((root / '.DS_Store').exists())
            self.assertTrue(all((root / name).read_text() == 'input' for name in inputs))
            self.assertEqual(cleaner.clean(root), [])

    def test_links_are_removed_without_traversing_external_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'; root.mkdir()
            mirror = Path(tmp) / 'source'; mirror.mkdir()
            (mirror / '__pycache__').mkdir()
            (mirror / '__pycache__/keep').write_text('source')
            for name in ('data', 'source-link', '.build-link'):
                (root / name).symlink_to(mirror, target_is_directory=True)
            (root / 'output').symlink_to(Path(tmp) / 'missing', target_is_directory=True)
            cleaner.clean(root)
            self.assertFalse((root / 'data').is_symlink())
            self.assertFalse((root / 'output').is_symlink())
            self.assertFalse((root / '.build-link').is_symlink())
            self.assertTrue((root / 'source-link').is_symlink())
            self.assertEqual((mirror / '__pycache__/keep').read_text(), 'source')

    def test_make_clean_uses_only_local_maintenance_command(self):
        result = subprocess.run(['make', '-n', 'clean', 'WEB_DIR=/external/source'], cwd=PROJECT,
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), 'python3 scripts/clean.py')

    def test_build_regenerates_icons_before_catalog_and_linked_book(self):
        result = subprocess.run(['make', '-n', 'build'], cwd=PROJECT,
                                text=True, capture_output=True, check=True)
        self.assertLess(result.stdout.index('scripts/build_icons.py'), result.stdout.index('-m anna.catalog.build'))
        self.assertIn('-m anna.server.linked_book', result.stdout)


if __name__ == '__main__':
    unittest.main()
