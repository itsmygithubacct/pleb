"""Shared saved policy, actual session consumption, and terminal command paths."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("screenlock", ROOT / "lib/screenlock.py")
settings = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(settings)


def function(name):
    return re.search(r"^" + name + r"\(\) \{\n.*?^\}\n", (ROOT / "bin/pleb-session").read_text(), re.M | re.S).group()


class ScreenLock(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.base),
                    "PLEB_CONFIG_HOME": str(self.base / "config")}
        patcher = patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def session(self):
        source = function("_pleb_load_lock_settings") + function("_pleb_auto_lock_policy")
        source += '\nPLEB_AUTO_LOCK=auto; PLEB_IDLE_LOCK_SECONDS=321; _PLEB_LOCK_PREFERENCE=session\n'
        source += '_pleb_load_lock_settings ' + shlex.quote(str(ROOT / "lib/screenlock.py")) + ' || exit 1\n'
        source += '_pleb_auto_lock_policy "$PLEB_IDLE_LOCK_SECONDS" || exit 1\n'
        source += 'echo "$_PLEB_AUTO_LOCK $PLEB_IDLE_LOCK_SECONDS $_PLEB_LOCK_PREFERENCE"\n'
        return subprocess.run(["bash", "-uc", source], env=self.env, capture_output=True, text=True, timeout=5)

    def test_no_preference_and_follow_defaults_preserve_session_configuration(self):
        for write in (False, True):
            if write:
                settings.save("session", 999)
            result = self.session()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "on 321 session")

    def test_saved_off_on_and_zero_idle_are_consumed_by_actual_session_functions(self):
        for policy, idle, expected in (("off", 777, "off 777 off"), ("on", 90, "on 90 on"), ("on", 0, "on 0 on")):
            settings.save(policy, idle)
            result = self.session()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)
            self.assertEqual(settings.settings_path().stat().st_mode & 0o777, 0o600)

    def test_invalid_or_executable_data_cannot_become_a_session_policy(self):
        path = settings.settings_path()
        path.parent.mkdir()
        for data in ('not json', '{"version":true,"policy":"off","idle_seconds":600}',
                     '{"version":1,"policy":"on","idle_seconds":true}',
                     '{"version":1,"policy":"off","idle_seconds":86401}',
                     '{"version":1,"policy":"$(touch marker)","idle_seconds":0}'):
            path.write_text(data)
            result = self.session()
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.base / "marker").exists())
        path.unlink()
        target = self.base / "other"
        target.write_text("keep")
        path.symlink_to(target)
        with self.assertRaises((OSError, ValueError)):
            settings.load()
        with self.assertRaises((OSError, ValueError)):
            settings.save("off", 0)
        self.assertEqual(target.read_text(), "keep")

    def test_custom_session_environment_directory_is_shared_with_settings(self):
        os.environ["PLEB_ENV_USER"] = str(self.base / "custom/session.env")
        self.env["PLEB_ENV_USER"] = os.environ["PLEB_ENV_USER"]
        settings.save("off", 0)
        self.assertEqual(settings.settings_path(), self.base / "custom/screen-lock.json")
        self.assertEqual(self.session().stdout.strip(), "off 0 off")

    def test_cli_updates_and_rejects_invalid_timeout_without_changing_saved_data(self):
        entry = [str(ROOT / "bin/pleb"), "screen-lock"]
        result = subprocess.run([*entry, "--set", "off", "--idle-seconds", "42"], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        shown = subprocess.run([*entry, "--show"], env=self.env, capture_output=True, text=True)
        self.assertEqual(json.loads(shown.stdout), {"version": 1, "policy": "off", "idle_seconds": 42})
        bad = subprocess.run([*entry, "--set", "on", "--idle-seconds", "-1"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(settings.load()["policy"], "off")


@unittest.skipUnless(shutil.which("dbus-run-session") and shutil.which("xfconf-query"), "private xfconf session required")
class PowerPolicy(unittest.TestCase):
    setUp = ScreenLock.setUp
    def test_explicit_preferences_update_real_private_xfconf_and_defaults_preserve_it(self):
        for directory in ("xdg-config", "xdg-cache", "xdg-data", "run"):
            (self.base / directory).mkdir(mode=0o700)
        env = dict(self.env, XDG_CONFIG_HOME=str(self.base / "xdg-config"),
                   XDG_CACHE_HOME=str(self.base / "xdg-cache"), XDG_DATA_HOME=str(self.base / "xdg-data"),
                   XDG_RUNTIME_DIR=str(self.base / "run"))
        query = "xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/lock-screen-suspend-hibernate"
        script = function("_pleb_apply_power_lock_preference")
        script += f'\n{query} --create --type bool --set true\n'
        for policy, value in (("off", "off"), ("on", "on"), ("session", "off")):
            script += f'_PLEB_LOCK_PREFERENCE={policy}; _PLEB_AUTO_LOCK={value}; _pleb_apply_power_lock_preference || exit 1\n'
            script += query + '\n'
        result = subprocess.run(["dbus-run-session", "--", "bash", "-uc", script], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["false", "true", "true"])


if __name__ == "__main__":
    unittest.main()
