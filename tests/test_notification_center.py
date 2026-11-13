"""Exercise the notification protocol and real GTK window on a private display."""
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NotificationDisplayTests(unittest.TestCase):
    def test_private_display_and_bus(self):
        if not shutil.which('Xvfb') or not shutil.which('dbus-run-session'):
            self.skipTest('private display and D-Bus tools unavailable')
        with tempfile.TemporaryDirectory() as directory:
            env = {'PATH': os.defpath, 'HOME': directory, 'LC_ALL': 'C.UTF-8',
                   'NO_AT_BRIDGE': '1', 'XDG_CACHE_HOME': directory + '/cache'}
            with subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1024x768x24',
                                   '-nolisten', 'tcp', '-noreset'], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) as display:
                try:
                    self.assertTrue(select.select([display.stdout], [], [], 10)[0], 'Xvfb not ready')
                    number = display.stdout.readline().decode().strip()
                    self.assertTrue(number.isdecimal(), number)
                    env['DISPLAY'] = ':' + number
                    result = subprocess.run(['dbus-run-session', '--', '/usr/bin/python3',
                                             str(Path(__file__).resolve()), '--inner'],
                                            env=env, capture_output=True, text=True, timeout=40)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                finally:
                    display.terminate()
                    display.wait(timeout=5)


def inner():
    import importlib.util
    import time
    spec = importlib.util.spec_from_file_location('notification_center', ROOT / 'lib/notification_center.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    Gtk, Gio, GLib = module.Gtk, module.Gio, module.GLib
    Gtk.init([])

    def pump(seconds=.08):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(.002)

    def client():
        return Gio.DBusConnection.new_for_address_sync(
            os.environ['DBUS_SESSION_BUS_ADDRESS'],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)

    class ProtocolTests(unittest.TestCase):
        def setUp(self):
            self.bus = client()
            self.center = module.NotificationCenter(self.bus)
            result, = module.bus_call(self.bus, 'RequestName', GLib.Variant('(su)', (module.NAME, 4)), '(u)')
            self.assertEqual(result, 1)
            self.clients = [client(), client()]
            self.events = [[], []]
            for connection, events in zip(self.clients, self.events):
                connection.signal_subscribe(None, module.NAME, None, module.PATH, None,
                                            Gio.DBusSignalFlags.NONE,
                                            lambda c, s, p, i, member, args, data:
                                            data.append((member, args.unpack())), events)

        def tearDown(self):
            self.center.stop()
            for connection in self.clients + [self.bus]:
                connection.close_sync(None)
            pump(.01)

        def call(self, client, method, signature, values, result='(u)'):
            done = []
            def complete(connection, response, unused):
                try:
                    done.append(connection.call_finish(response).unpack())
                except GLib.Error as error:
                    done.append(error)
            client.call(module.NAME, module.PATH, module.NAME, method,
                        GLib.Variant(signature, values), GLib.VariantType.new(result),
                        Gio.DBusCallFlags.NONE, 1000, None, complete, None)
            deadline = time.monotonic() + 2
            while not done and time.monotonic() < deadline:
                pump(.005)
            self.assertTrue(done, method)
            if isinstance(done[0], Exception):
                raise done[0]
            pump()
            return done[0]

        def notify(self, client=0, replaces=0, title='Build complete', body='Pane finished its command.',
                   actions=('default', 'Focus'), hints=None, timeout=0):
            return self.call(self.clients[client], 'Notify', '(susssasa{sv}i)',
                             ('Kilix', replaces, 'dialog-information', title, body,
                              list(actions), hints or {}, timeout))[0]

        def geometry(self):
            pump()
            window = self.center.window.get_window()
            return window.get_xid(), tuple(window.get_geometry())

        def test_one_window_grows_and_retracts_downward_and_each_icon_routes_its_own_action(self):
            first = self.notify()
            window1, (x1, y1, w1, h1) = self.geometry()
            second = self.notify(client=1, title='Tests finished', body='Another pane is ready.')
            window2, (x2, y2, w2, h2) = self.geometry()
            self.assertEqual(window1, window2)
            self.assertEqual((x1, y1, w1), (x2, y2, w2))
            self.assertGreaterEqual(h1, 72)
            self.assertGreater(h2, h1)
            self.assertEqual(self.center.box.get_children(),
                             [self.center.items[first]['row'], self.center.items[second]['row']])
            # Only the icon and dismiss control are buttons for a default
            # action. The client's default label never becomes a button below.
            row = self.center.items[first]['row']
            buttons = [widget for widget in row.get_children() if isinstance(widget, Gtk.Button)]
            self.assertEqual(len(buttons), 2)
            self.assertIsInstance(self.center.items[first]['default_button'].get_child(), Gtk.Image)
            self.center.items[first]['default_button'].clicked()
            pump()
            self.assertIn(('ActionInvoked', (first, 'default')), self.events[0])
            self.assertNotIn(('ActionInvoked', (first, 'default')), self.events[1])
            self.assertIn(second, self.center.items)
            window3, (x3, y3, w3, h3) = self.geometry()
            self.assertEqual((window3, x3, y3, w3), (window1, x1, y1, w1))
            self.assertLess(h3, h2)
            self.center.items[second]['default_button'].clicked()
            pump()
            self.assertIn(('ActionInvoked', (second, 'default')), self.events[1])
            self.assertFalse(self.center.window.get_visible())
            again = self.notify()
            self.assertEqual(self.geometry()[0], window1)
            self.assertGreater(again, second)

        def test_replace_expiry_close_and_foreign_ids(self):
            number = self.notify(timeout=1000)
            replaced = self.notify(replaces=number, title='Updated', timeout=0)
            self.assertEqual(replaced, number)
            self.assertEqual(len(self.center.items), 1)
            foreign = self.notify(client=1, replaces=number)
            self.assertNotEqual(foreign, number)
            with self.assertRaises(GLib.Error):
                self.call(self.clients[1], 'CloseNotification', '(u)', (number,), '()')
            self.assertIn(number, self.center.items)
            self.call(self.clients[0], 'CloseNotification', '(u)', (number,), '()')
            self.assertIn(('NotificationClosed', (number, 3)), self.events[0])
            expiring = self.notify(timeout=30)
            pump(.1)
            self.assertNotIn(expiring, self.center.items)
            self.assertIn(('NotificationClosed', (expiring, 1)), self.events[0])
            persistent = self.notify(hints={'resident': GLib.Variant('b', True)})
            self.center.items[persistent]['default_button'].clicked()
            pump()
            self.assertIn(persistent, self.center.items)

        def test_overflow_is_scrollable_and_plain_text_is_not_markup(self):
            for index in range(15):
                self.notify(title='<b>literal title</b>', body='Message ' + str(index))
            xid, (x, y, width, height) = self.geometry()
            self.assertGreater(width, 100)
            self.assertLessEqual(y + height, 768)
            self.assertEqual(len(self.center.items), 15)
            self.assertGreater(self.center.scroll.get_vadjustment().get_upper(), height)
            caps, = self.call(self.clients[0], 'GetCapabilities', '()', (), '(as)')
            self.assertNotIn('body-markup', caps)
            # A second daemon cannot steal the session's notification name.
            result, = module.bus_call(self.clients[0], 'RequestName',
                                      GLib.Variant('(su)', (module.NAME, 4)), '(u)')
            self.assertEqual(result, 3)

    return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProtocolTests)).wasSuccessful()


if __name__ == '__main__':
    if sys.argv[1:] == ['--inner']:
        raise SystemExit(0 if inner() else 1)
    unittest.main()
