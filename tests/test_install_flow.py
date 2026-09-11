import json
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
    def test_render_bot_unit_uses_deployed_auth_environment(self):
        content = manage_install.render_unit('ai-bot', 'dashboard/bot_service.py', Path('/usr/local'))
        self.assertIn('EnvironmentFile=-/etc/aipool/aipool.env', content)
        dashboard = manage_install.render_unit('ai-dashboard', 'dashboard/server.py', Path('/usr/local'))
        self.assertNotIn('EnvironmentFile=-/etc/aipool/aipool.env', dashboard)


if __name__ == "__main__":
    unittest.main()
