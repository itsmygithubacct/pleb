"""Exercise real launcher handoffs without touching the user's display/bus."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pleb_devices', ROOT / 'lib/devices.py')
devices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(devices)


class DesktopControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home(), prefix='pleb-controls-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.record = self.root / 'record.json'
        self.capture = self.root / 'i3lock'
        self.capture.write_text('''#!/usr/bin/python3
import json,os,sys
from pathlib import Path
Path(os.environ['FIXTURE_RECORD']).write_text(json.dumps({
    'argv':sys.argv[1:], 'display':os.environ.get('DISPLAY'),
    'authority':os.environ.get('XAUTHORITY'),
    'bus':os.environ.get('DBUS_SESSION_BUS_ADDRESS'),
    'wayland':os.environ.get('WAYLAND_DISPLAY')}))
''')
        self.capture.chmod(0o700)
        self.env = dict(os.environ, PATH=f'{self.root}:/usr/bin:/bin',
                        FIXTURE_RECORD=str(self.record), DISPLAY=':93',
                        XAUTHORITY='/private/app-authority', KILIX_PRIVATE_XAPP='1',
                        PLEB_DESKTOP_DISPLAY=':71', PLEB_DESKTOP_XAUTHORITY='/physical/authority',
                        PLEB_DESKTOP_BUS_ADDRESS='unix:path=/physical/bus',
                        DBUS_SESSION_BUS_ADDRESS='unix:path=/private/bus', WAYLAND_DISPLAY='wayland-test',
                        PLEB_SESSION_SERVICES='off', XDG_SESSION_ID='owned-fixture')

    def test_manual_lock_uses_the_supervised_logind_locker_in_serviced_sessions(self):
        # pleb-session publishes its session id only once its locker runs.
        (self.root / 'loginctl').symlink_to(self.capture)
        self.env['PLEB_DESKTOP_SESSION_ID'] = 'owned-fixture'
        result = subprocess.run([ROOT / 'bin/pleb-lock'], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(self.record.read_text())
        self.assertEqual(record['argv'], ['lock-session', '--', 'owned-fixture'])

    def test_another_desktop_running_the_seeded_lock_command_gets_a_real_locker(self):
        # Xfce's LockCommand is seeded with pleb-lock. Outside Pleb's serviced
        # session nobody answers loginctl lock-session, so lock directly.
        (self.root / 'loginctl').symlink_to(self.capture)
        for published in (None, 'an-earlier-pleb-login'):
            with self.subTest(published=published):
                self.env.pop('PLEB_DESKTOP_SESSION_ID', None)
                if published:
                    self.env['PLEB_DESKTOP_SESSION_ID'] = published
                self.env.update(PLEB_SESSION_SERVICES='auto', XDG_SESSION_ID='xfce-login')
                result = subprocess.run([ROOT / 'bin/pleb-lock'], env=self.env,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--color=101010', json.loads(self.record.read_text())['argv'])

    def test_nofork_callback_does_not_recurse_into_the_coordinator(self):
        (self.root / 'loginctl').symlink_to(self.capture)
        self.env.update(PLEB_SESSION_SERVICES='on', PLEB_DESKTOP_SESSION_ID='owned-fixture')
        result = subprocess.run([ROOT / 'bin/pleb-lock', '--nofork'], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--nofork', json.loads(self.record.read_text())['argv'])

    def test_disabled_services_and_auto_kiosk_keep_the_direct_locker(self):
        for policy, kiosk in (('off', '0'), ('0', '0'), ('auto', '1')):
            with self.subTest(policy=policy, kiosk=kiosk):
                self.env.update(PLEB_SESSION_SERVICES=policy, PLEB_RESPAWN=kiosk)
                result = subprocess.run([ROOT / 'bin/pleb-lock'], env=self.env,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--color=101010', json.loads(self.record.read_text())['argv'])

    def test_failed_coordinated_request_is_not_reported_as_a_successful_lock(self):
        login = self.root / 'loginctl'
        login.write_text('#!/bin/sh\nexit 23\n')
        login.chmod(0o700)
        self.env.update(PLEB_SESSION_SERVICES='on', PLEB_DESKTOP_SESSION_ID='owned-fixture')
        result = subprocess.run([ROOT / 'bin/pleb-lock'], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 23)
        self.assertFalse(self.record.exists())

    def test_lock_from_private_pane_targets_physical_display(self):
        result = subprocess.run([ROOT / 'bin/pleb-lock', '--nofork'], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(self.record.read_text())
        self.assertEqual(record['display'], ':71')
        self.assertEqual(record['authority'], '/physical/authority')
        self.assertIsNone(record['wayland'])
        self.assertIn('--nofork', record['argv'])
        self.assertNotIn('--debug', record['argv'])

    def test_lock_refuses_a_private_display_without_physical_context(self):
        self.env.pop('PLEB_DESKTOP_DISPLAY')
        result = subprocess.run([ROOT / 'bin/pleb-lock'], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.record.exists())

    def test_lock_rejects_forwarded_displays_and_arbitrary_arguments(self):
        self.env['PLEB_DESKTOP_DISPLAY'] = 'localhost:10.0'
        result = subprocess.run([ROOT / 'bin/pleb-lock'], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.env['PLEB_DESKTOP_DISPLAY'] = ':71'
        result = subprocess.run([ROOT / 'bin/pleb-lock', '--debug'], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.record.exists())

    def test_device_control_keeps_private_display_and_reaches_host_bus(self):
        # Simulate the real runner's environment replacement at the app boundary.
        (self.root / 'blueman-manager').symlink_to(self.capture)
        runner = self.root / 'kilix'
        runner.write_text('''#!/usr/bin/python3
import os,sys
assert sys.argv[1:4] == ['run','--size','1000x720']
os.environ['DISPLAY']=':93'
os.environ['DBUS_SESSION_BUS_ADDRESS']='unix:path=/runner-private/bus'
os.execv(sys.argv[4], sys.argv[4:])
''')
        runner.chmod(0o700)
        result = subprocess.run(['/usr/bin/python3', ROOT / 'lib/devices.py', 'bluetooth'],
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(self.record.read_text())
        self.assertEqual(record['display'], ':93')
        self.assertEqual(record['authority'], '/private/app-authority')
        self.assertEqual(record['bus'], 'unix:path=/physical/bus')

    def test_missing_desktop_bus_or_tool_is_a_visible_failure(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(devices.shutil, 'which', return_value='/bin/true'):
            os.environ.pop('PLEB_DESKTOP_BUS_ADDRESS')
            with self.assertRaisesRegex(RuntimeError, 'physical desktop'):
                devices.command('power')
        with patch.object(devices.shutil, 'which', return_value=None):
            with self.assertRaises(FileNotFoundError):
                devices.command('power')


if __name__ == '__main__':
    unittest.main()
