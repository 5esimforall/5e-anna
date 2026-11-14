"""Package boundaries and executable entry points replace path-injected scripts."""
import ast
from contextlib import redirect_stdout
import importlib
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from anna.paths import PROJECT
from anna.review import store
from anna.serialization import canonical, digest


class PackageLayoutTests(unittest.TestCase):
    def test_all_modules_import_without_starting_commands(self):
        output=io.StringIO()
        with redirect_stdout(output):
            for file in sorted((PROJECT/'anna').rglob('*.py')):
                name='.'.join(file.relative_to(PROJECT).with_suffix('').parts)
                module=importlib.import_module(name)
                self.assertEqual(Path(module.__file__).resolve(),file.resolve())
                tree=ast.parse(file.read_text())
                for node in ast.walk(tree):
                    if isinstance(node,ast.ImportFrom) and node.level==0:
                        self.assertNotIn(node.module,{'build','paths','review_store','html_extract','serve'})
        self.assertEqual(output.getvalue(),'')
        self.assertEqual(sorted(p.name for p in (PROJECT/'scripts').glob('*.py')),['build_icons.py','clean.py'])

    def test_module_commands_expose_help(self):
        commands=('catalog.build','catalog.audit','catalog.query','source.search',
                  'review.commands','knowledge.repository','compare.semantics',
                  'snapshots.workflow','datalog.runtime','server.linked_book','server.app')
        for command in commands:
            with self.subTest(command=command):
                result=subprocess.run([sys.executable,'-m','anna.'+command,'--help'],cwd=PROJECT,
                                      text=True,capture_output=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('usage:',result.stdout)
                self.assertNotIn('RuntimeWarning',result.stderr)

    def test_retired_snapshot_conversion_command_is_unavailable(self):
        result=subprocess.run([sys.executable,'-m','anna.snapshots.workflow','convert','unused'],
                              cwd=PROJECT,text=True,capture_output=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('invalid choice',result.stderr)
        from anna.snapshots import baseline
        self.assertFalse(hasattr(baseline,'convert'))

    def test_fingerprints_follow_package_not_maintenance_scripts(self):
        self.assertIn('source/pages.py',store.EXTRACTION_MODULES)
        self.assertIn('serialization.py',store.EXTRACTION_MODULES)
        self.assertTrue(all((PROJECT/'anna'/p).is_file() for p in store.EXTRACTION_MODULES))
        original=Path.read_bytes
        before=(store.pipeline_hash(),store.extraction_hash())
        def changed(path):
            raw=original(path)
            return raw+b'\n# fingerprint fixture' if path==PROJECT/'anna/source/pages.py' else raw
        with patch.object(Path,'read_bytes',changed):
            self.assertNotEqual(store.pipeline_hash(),before[0])
            self.assertNotEqual(store.extraction_hash(),before[1])
        def maintenance(path):
            self.assertNotEqual(path,PROJECT/'scripts/build_icons.py')
            return original(path)
        with patch.object(Path,'read_bytes',maintenance):
            self.assertEqual((store.pipeline_hash(),store.extraction_hash()),before)

    def test_shared_serialization_preserves_existing_identity(self):
        from hashlib import sha256
        value={'z':1,'a':['中文',True]}
        expected='{"a":["中文",true],"z":1}'
        self.assertEqual(canonical(value),expected)
        self.assertEqual(digest(value),sha256(expected.encode()).hexdigest())
        from anna.server import query
        self.assertFalse(hasattr(query,'main'))


if __name__=='__main__':unittest.main()
