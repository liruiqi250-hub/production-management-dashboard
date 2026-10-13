"""新副本不得静默借用其它电脑的微信授权资料。"""
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch


class CollectorRuntimeTests(unittest.TestCase):
    def test_new_copy_uses_own_runtime_and_binding_even_before_configuration(self):
        source = Path(__file__).resolve().parents[1] / 'scripts/collect_scoped_wechat_V0.1.3.py'
        with tempfile.TemporaryDirectory(prefix='collector 新副本 ') as temporary:
            root = Path(temporary)
            script = root / 'scripts' / source.name
            script.parent.mkdir()
            script.write_bytes(source.read_bytes())
            with patch.dict(os.environ, {}, clear=True):
                namespace = runpy.run_path(str(script))
            self.assertEqual(namespace['RUNTIME'], root / 'vendor/wechat')
            self.assertEqual(namespace['PRIOR_ACCOUNT_BINDING'], root / 'private/wechat_binding.json')
            self.assertFalse(namespace['PRIOR_ACCOUNT_BINDING'].exists())

    def test_explicit_local_operator_paths_are_respected(self):
        source = Path(__file__).resolve().parents[1] / 'scripts/collect_scoped_wechat_V0.1.3.py'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {'DINO_WECHAT_RUNTIME': str(root / 'selected_runtime'),
                   'DINO_WECHAT_BINDING': str(root / 'selected_binding.json')}
            with patch.dict(os.environ, env):
                namespace = runpy.run_path(str(source))
            self.assertEqual(namespace['RUNTIME'], root / 'selected_runtime')
            self.assertEqual(namespace['PRIOR_ACCOUNT_BINDING'], root / 'selected_binding.json')


if __name__ == '__main__':
    unittest.main()
