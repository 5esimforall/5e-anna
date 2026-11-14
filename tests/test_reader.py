"""Source release evidence is independent of the build snapshot and rules edition."""
from pathlib import Path
import tempfile
import unittest

from anna.server.reader import build_navigation, source_release, content_file, strip_reader_scripts, WINCHM_NAVIGATION
from anna.source.html import Document
from anna.server.linked_book import text_slice


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


class PublishedContentTests(unittest.TestCase):
    def test_only_topics_and_presentation_assets_are_published(self):
        for path in ('topics/page.htm', 'topics/images/map.png', 'topics/style.css',
                     'assets/webhelp-topic.css', 'assets/content-enhance.js', 'assets/webhelp-contrast.js'):
            self.assertTrue(content_file(path), path)
        for path in ('index.htm', 'webhelp-content-bridge.js', 'assets/webhelp-prepaint.js',
                     'assets/webhelp-shell.js', 'assets/webhelp-nav.css', 'assets/webhelp-legacy.js',
                     'style.css', 'icons/1.gif', 'imgs/plus.gif', '.git/config', 'topics/.secret'):
            self.assertFalse(content_file(path), path)

    def test_removed_shell_scripts_keep_source_text_and_dom_bindings(self):
        raw = (b'<head><script src="../assets/webhelp-prepaint.js"></script></head><body>'
               b'<p>before</p><script src="../webhelp-content-bridge.js"></script><h2>after</h2>'
               b'<script src="./rules.js"></script><script src="../assets/content-enhance.js"></script>'
               b'<script>window.content = true;</script></body>')
        cleaned = strip_reader_scripts(raw, 'topics/test.htm')
        self.assertNotIn(b'webhelp-prepaint', cleaned)
        self.assertNotIn(b'webhelp-content-bridge', cleaned)
        self.assertIn(b'<script src="./rules.js"></script>', cleaned)
        self.assertIn(b'<script src="../assets/content-enhance.js"></script>', cleaned)
        self.assertIn(b'<script>window.content = true;</script>', cleaned)
        before, after = Document('topics/test.htm',raw.decode()), Document('topics/test.htm',cleaned.decode())
        try:
            self.assertEqual(before.text, after.text)
            start=before.text.index('after')
            self.assertEqual(text_slice(before,start,start+5),text_slice(after,start,start+5))
        finally:
            before.close();after.close()


    def test_only_complete_winchm_navigation_template_is_removed(self):
        script=WINCHM_NAVIGATION.replace('NODE','3602').replace('HOME','"../../index.htm?page=chapter/page.htm"')
        raw=('<body><script>'+script+'</script><h2>Content</h2></body>').encode()
        cleaned=strip_reader_scripts(raw,'topics/chapter/page.htm')
        self.assertEqual(cleaned,b'<body><!-- source shell script removed --><h2>Content</h2></body>')
        mixed=raw.replace(b'</script>',b'window.content = true;</script>')
        self.assertEqual(strip_reader_scripts(mixed,'topics/chapter/page.htm'),mixed)


class ReaderModelTests(unittest.TestCase):
    def test_scope_and_position_model(self):
        import subprocess
        result = subprocess.run(['node', str(Path(__file__).with_name('reader_model.cjs'))], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shared_workspace_record(self):
        import subprocess
        result = subprocess.run(['node', str(Path(__file__).with_name('workspace.cjs'))], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
