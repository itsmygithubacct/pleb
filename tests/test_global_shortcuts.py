from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import global_shortcuts as backend
from shortcut_keys import ShortcutKeys, ShortcutError
from _env_support import clean_env

ROOT = Path(__file__).resolve().parents[1]


class ShortcutPolicyTests(unittest.TestCase):
    def keyboard(self):
        keys = object.__new__(ShortcutKeys)
        keys.modifiers = dict(CTRL=4, SHIFT=1, ALT=8, LOGO=64, NUM=16)
        keys.locks = 2 | 16
        symbols = {'a': 97, 'l': 108, 'Tab': 9, 'F4': 104, 'F8': 1080, 'BackSpace': 8}
        keys.symbol = lambda name: symbols.get(name, 0)
        keys.first = 8
        keys.mapping = [(value, value) for value in symbols.values()]
        return keys

    def test_single_base_layer_chord_and_explicit_num_state(self):
        keys = self.keyboard()
        self.assertEqual(keys.chord('SHIFT+CTRL+a').trigger, 'CTRL+SHIFT+a')
        self.assertEqual(keys.chord('CTRL+a').masks, (4, 6, 20, 22))
        self.assertEqual(keys.chord('CTRL+NUM+a').masks, (20, 22))

    def test_reject_typing_shell_fragments_and_reserved_system_chords(self):
        for trigger in ('a', 'SHIFT+a', 'CTRL+CTRL+a', 'CTRL+ALT+F8', 'ALT+F4', 'ALT+Tab',
                        'CTRL+ALT+l', 'LOGO+l', 'CTRL+ALT+BackSpace', 'CTRL+a;touch_tmp', 'CTRL+$(id)', 'CTRL+NoSuchKey'):
            with self.subTest(trigger=trigger), self.assertRaises(ShortcutError):
                self.keyboard().chord(trigger)

    def test_ambiguous_key_and_multiple_layout_groups_fail_closed(self):
        keys = self.keyboard()
        keys.mapping.append((97, 97))
        with self.assertRaises(ShortcutError):
            keys.chord('CTRL+a')
        keys = self.keyboard()
        keys.mapping[0] = (97, 65, 113, 81)
        with self.assertRaises(ShortcutError):
            keys.chord('CTRL+a')

    def test_modifier_slots_shared_with_other_modifiers_are_refused(self):
        keys = self.keyboard()
        keys.modifiers['NUM'] = keys.modifiers['CTRL']
        with self.assertRaises(ShortcutError):
            keys.chord('CTRL+a')

    def test_backend_authentication_and_wrong_caller_handle(self):
        portal = object.__new__(backend.Portal)
        portal.bus = mock.Mock()
        portal.bus.get_name_owner.return_value = ':1.99'
        portal.bus.get_unix_user.return_value = os.getuid()
        portal.authenticate(':1.99')
        for sender in (None, ':1.98', backend.FRONTEND):
            with self.assertRaises(backend.dbus.exceptions.DBusException):
                portal.authenticate(sender)
        session = mock.Mock(owner=':1.4', closed=False)
        portal.sessions = {backend.ROOT + '/session/1_4/token': session}
        with self.assertRaises(ShortcutError):
            portal.session(backend.ROOT + '/session/1_4/token', backend.ROOT + '/request/1_5/token')

    def test_unsafe_restart_records_are_rejected(self):
        portal = object.__new__(backend.Portal)
        with tempfile.TemporaryDirectory() as directory:
            portal.state = Path(directory) / 'sessions.json'
            self.assertEqual(portal.load_sessions(), [])
            target = Path(directory) / 'target'
            target.write_text('[]')
            portal.state.symlink_to(target)
            with self.assertRaises(OSError):
                portal.load_sessions()
            portal.state.unlink()
            portal.state.write_text('[]')
            portal.state.chmod(0o644)
            with self.assertRaises(ShortcutError):
                portal.load_sessions()
            portal.state.chmod(0o600)
            portal.state.write_text('[["/x", "org.example.Untrusted"]]')
            with self.assertRaises(ShortcutError):
                portal.load_sessions()


class PublicPortalIntegration(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('PLEB_GLOBALSHORTCUTS_RELAY'),
                         'Set PLEB_GLOBALSHORTCUTS_RELAY to a committed Kilix portal_bridge.py for the real relay/frontend/X11 lane')
    def test_public_portal_private_apps_physical_consent_and_native_keyboard(self):
        for program in ('Xvfb', 'dbus-daemon', 'xdotool', 'openbox', 'xauth', 'setxkbmap', 'xmodmap'):
            self.assertIsNotNone(shutil.which(program), program)
        with tempfile.TemporaryDirectory(prefix='gs-integration-') as directory:
            env = clean_env(Path(directory), DISPLAY='', DBUS_SESSION_BUS_ADDRESS='', DBUS_SYSTEM_BUS_ADDRESS='')
            result = subprocess.run(['/usr/bin/python3', str(ROOT / 'tests/fixtures/global_shortcuts_fixture.py'),
                                     '--output', directory, '--relay', os.environ['PLEB_GLOBALSHORTCUTS_RELAY']],
                                    env=env, capture_output=True, text=True, timeout=100)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
