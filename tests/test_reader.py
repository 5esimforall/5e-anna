"""Source release evidence is independent of the build snapshot and rules edition."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from reader import build_navigation, source_release


class SourceReleaseTests(unittest.TestCase):
    def test_printed_title_supplies_version_and_evidence_path(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td)
            (source / 'topics').mkdir()
            page = source / 'topics/写在前面.html'
            page.write_text('<meta charset="utf-8"><p><strong>DND 五版不全书 '
                            '<font>v20260913</font></strong></p><p>旧更新 v20250801，规则 2024</p>')
            release = {'version': '20260913', 'path': 'topics/写在前面.html'}
            self.assertEqual(source_release(source), release)
            self.assertEqual(build_navigation(source, set())['source_release'], release)
            page.write_text(page.read_text().replace('20260913', '20261001.1'))
            self.assertEqual(source_release(source)['version'], '20261001.1')

    def test_missing_unrelated_invalid_and_conflicting_versions_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td)
            self.assertIsNone(source_release(source))
            (source / 'topics').mkdir()
            page = source / 'topics/写在前面.html'
            for html in ('<p>更新时间 v20260913</p>', '<p>DND 五版不全书 v20261340</p>',
                         '<script>DND 五版不全书 v20260913</script>',
                         '<p>DND 五版不全书 v20260913</p><p>DND 五版不全书 v20261001</p>'):
                with self.subTest(html=html):
                    page.write_text('<meta charset="utf-8">' + html)
                    self.assertIsNone(source_release(source))
