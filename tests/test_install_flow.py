import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import manage_install


class InstallFlowTests(unittest.TestCase):
    def test_missing_agent_is_skipped_without_creating_config(self):
        with tempfile.TemporaryDirectory() as d, patch.object(manage_install, "ROOT", Path(d)):
            with patch("shutil.which", return_value=None):
                result = manage_install.integrate_installed_agents()
            self.assertTrue(result["hermes"]["skipped"])
            self.assertTrue(result["openclaw"]["skipped"])

    def test_agent_integration_requires_verified_readback(self):
        with tempfile.TemporaryDirectory() as d, patch.object(manage_install, "ROOT", Path(d)):
            hermes = Path(d) / "hermes"
            hermes.write_text("#!/bin/sh\n")
            config = Path(d) / "config.yaml"
            config.write_text("providers: {}\n")
            with patch("shutil.which", side_effect=lambda name: str(hermes) if name == "hermes" else None), patch.dict(manage_install.os.environ, {"HERMES_HOME": str(config.parent)}):
                with patch("subprocess.run") as run:
                    run.return_value.returncode = 0
                    run.return_value.stdout = json.dumps({"success": True, "verified": True})
                    result = manage_install.integrate_installed_agents()
            self.assertTrue(result["hermes"]["verified"])
            run.assert_called_once()

    def test_unverified_integration_fails(self):
        with tempfile.TemporaryDirectory() as d, patch.object(manage_install, "ROOT", Path(d)):
            config = Path(d) / "config.yaml"
            config.write_text("providers: {}\n")
            with patch("shutil.which", return_value="/bin/hermes"), patch.dict(manage_install.os.environ, {"HERMES_HOME": str(config.parent)}), patch("subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = json.dumps({"success": False, "verified": False})
                with self.assertRaises(ValueError):
                    manage_install.integrate_installed_agents()
    def test_render_provider_units_use_deployed_paths(self):
        codex = manage_install.render_unit('codex', 'bridges/codex_bridge.py')
        gemini = manage_install.render_unit('gemini', 'bridges/gemini_bridge.py')
        dashboard = manage_install.render_unit('dashboard', 'dashboard/server.py')
        self.assertIn('/var/lib/aipool/venv/bin/python', codex)
        self.assertIn('/var/lib/aipool/app/bridges/codex_bridge.py', codex)
        self.assertIn('/var/lib/aipool/app/bridges/gemini_bridge.py', gemini)
        for line in manage_install.HARDENED_COMMON:
            self.assertIn(line, codex)
            self.assertIn(line, gemini)
        self.assertIn('ReadWritePaths=/var/lib/aipool/runtime /var/lib/aipool/accounts', codex)
        for line in manage_install.HARDENED_COMMON:
            self.assertIn(line, dashboard)
        self.assertEqual(set(manage_install.SERVICES), {'codex', 'gemini', 'dashboard'})

    def test_legacy_units_are_discovered_from_systemctl(self):
        old = 'aipool-codex-bridge.service loaded enabled\\n'
        with patch('subprocess.run') as run:
            run.return_value.stdout = old
            self.assertEqual(manage_install.legacy_units(), ('aipool-codex-bridge.service',))

    def test_migration_does_not_retire_legacy_on_new_failure(self):
        with tempfile.TemporaryDirectory() as d, patch.object(manage_install, 'SYSTEM_UNIT_DIR', Path(d)), patch.object(manage_install, 'USER_UNIT_DIRS', ()), patch.object(manage_install, 'legacy_units', return_value=('ai-codex-bridge.service',)):
            with patch.object(manage_install, 'run', side_effect=lambda command: (_ for _ in ()).throw(subprocess.CalledProcessError(1, command)) if command[:2] == ['systemctl', 'is-active'] else None), patch('subprocess.run') as systemctl:
                with self.assertRaises(subprocess.CalledProcessError):
                    manage_install.install_system_services()
            self.assertFalse(any('disable' in call.args[0] for call in systemctl.call_args_list))
            self.assertTrue((Path(d) / 'codex.service').exists())

    def test_migration_retires_legacy_only_after_new_units_are_active(self):
        events = []

        def record_run(command):
            events.append(('run', command))

        def record_systemctl(command, **kwargs):
            events.append(('systemctl', command))
            result = subprocess.CompletedProcess(command, 0, stdout='', stderr='')
            return result

        with tempfile.TemporaryDirectory() as d, patch.object(manage_install, 'SYSTEM_UNIT_DIR', Path(d)), patch.object(manage_install, 'USER_UNIT_DIRS', ()), patch.object(manage_install, 'legacy_units', return_value=('aipool-codex-bridge.service',)), patch.object(manage_install, 'run', side_effect=record_run), patch('subprocess.run', side_effect=record_systemctl):
            manage_install.install_system_services()

        active = max(i for i, (kind, command) in enumerate(events) if kind == 'run' and command[1:3] == ['is-active', '--quiet'])
        disable = next(i for i, (kind, command) in enumerate(events) if kind == 'systemctl' and command[-2:] == ['disable', 'aipool-codex-bridge.service'])
        self.assertGreater(disable, active)

    def test_migration_is_idempotent_on_clean_and_second_runs(self):
        with tempfile.TemporaryDirectory() as d, patch.object(manage_install, 'SYSTEM_UNIT_DIR', Path(d)), patch.object(manage_install, 'USER_UNIT_DIRS', ()), patch.object(manage_install, 'legacy_units', return_value=()), patch.object(manage_install, 'run') as run, patch('subprocess.run'):
            manage_install.install_system_services()
            manage_install.install_system_services()
            self.assertEqual({p.name for p in Path(d).glob('*.service')}, {'codex.service', 'gemini.service', 'dashboard.service'})


if __name__ == "__main__":
    unittest.main()
