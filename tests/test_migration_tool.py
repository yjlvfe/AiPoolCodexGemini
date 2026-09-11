import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import aipool_nonroot_migration as migration

class MigrationTests(unittest.TestCase):
    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(sys, 'argv', ['migration', '--dest', d]):
                self.assertEqual(migration.main(), 0)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_deployable_file_inventory_excludes_runtime_state(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'bridges').mkdir()
            (root / 'bridges' / 'oauth-client.private.json').write_text('{"client_secret":"do-not-copy"}')
            (root / 'cli').mkdir()
            (root / 'dashboard' / 'artifacts').mkdir(parents=True)
            (root / 'dashboard').mkdir(exist_ok=True)
            (root / 'dashboard' / 'auth.db').write_text('session')
            (root / 'dashboard' / 'auth.db-journal').write_text('journal')
            (root / 'dashboard' / 'client-identities.json').write_text('{"clients": [{"sha256": "secret"}]}')
            (root / 'dashboard' / 'artifacts' / 'secret').write_text('artifact')
            (root / 'dashboard' / 'server.py').write_text('source')
            (root / 'scripts').mkdir()
            (root / 'scripts' / 'integrations.py').write_text('integration')
            with patch.object(migration, 'ROOT', root):
                paths = {relative for _, relative in migration.files_to_copy()}
            self.assertIn(Path('dashboard/server.py'), paths)
            self.assertIn(Path('scripts/integrations.py'), paths)
            self.assertNotIn(Path('dashboard/auth.db'), paths)
            self.assertNotIn(Path('dashboard/auth.db-journal'), paths)
            self.assertNotIn(Path('bridges/oauth-client.private.json'), paths)
            self.assertNotIn(Path('dashboard/client-identities.json'), paths)
            self.assertFalse(any(path.parts[:2] == ('dashboard', 'artifacts') for path in paths))

    def test_copy_rejects_symlinked_ancestor(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'source.py'
            source.write_text('x = 1')
            real = root / 'real'
            real.mkdir()
            linked = root / 'linked'
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaises(ValueError):
                migration._copy_one(source, linked / 'source.py', 'root')

        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'dest'
            root.mkdir()
            manifest = root / 'migration-manifest.json'
            manifest.write_text(json.dumps({
                'destination': str(root),
                'files': [{'path': '../outside', 'sha256': '0' * 64}],
            }))
            ok, errors = migration.verify_manifest(manifest)
            self.assertFalse(ok)
            self.assertTrue(errors[0].startswith('unsafe:'))

if __name__ == '__main__':
    unittest.main()
