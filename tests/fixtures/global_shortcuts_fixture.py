"""Public portal, production relay, GTK consent and native XTEST input, all owned.

The only stand-in is logind on a separate disposable system bus. No application
or backend portal calls/signals or X keyboard events are mocked.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time

ROOT = '/org/freedesktop/portal/desktop'
PUBLIC = 'org.freedesktop.portal.GlobalShortcuts'
BACKEND = 'org.freedesktop.impl.portal.desktop.pleb.shortcuts'
IMPL = 'org.freedesktop.impl.portal.GlobalShortcuts'


def output(**values):
    print(json.dumps(values), flush=True)


def application():
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GLib, Gtk
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus(private=True)
    portal = bus.get_object('org.freedesktop.portal.Desktop', ROOT, introspect=False)
    interface = dbus.Interface(portal, PUBLIC)
    bus.add_signal_receiver(lambda code, results, path: output(response=int(code), results=results, path=path),
                            signal_name='Response', dbus_interface='org.freedesktop.portal.Request', path_keyword='path')
    for member in ('Activated', 'Deactivated'):
        bus.add_signal_receiver(lambda session, ident, timestamp, options, member=member:
                                output(event=member, session=session, ident=ident, timestamp=int(timestamp)),
                                signal_name=member, dbus_interface=PUBLIC, path=ROOT)
    bus.add_signal_receiver(lambda session, shortcuts: output(event='ShortcutsChanged', session=session, shortcuts=shortcuts),
                            signal_name='ShortcutsChanged', dbus_interface=PUBLIC, path=ROOT)
    bus.add_signal_receiver(lambda details, path: output(event='Closed', session=path),
                            signal_name='Closed', dbus_interface='org.freedesktop.portal.Session', path_keyword='path')
    window = Gtk.Window(title='Private shortcut application')
    window.add(Gtk.Entry())
    window.show_all()
    counter = [0]
    def command(_fd, _condition):
        line = sys.stdin.readline()
        if not line:
            Gtk.main_quit()
            return False
        data = json.loads(line)
        counter[0] += 1
        token = 'r' + str(counter[0])
        try:
            options = dbus.Dictionary({'handle_token': dbus.String(token)}, signature='sv')
            if data['op'] == 'create':
                options['session_handle_token'] = dbus.String(token)
                handle = interface.CreateSession(options)
            elif data['op'] == 'bind':
                shortcuts = dbus.Array([dbus.Struct((item[0], dbus.Dictionary(item[1], signature='sv')),
                                                    signature='sa{sv}') for item in data['shortcuts']], signature='(sa{sv})')
                handle = interface.BindShortcuts(dbus.ObjectPath(data['session']), shortcuts, 'x11:123456', options)
            elif data['op'] == 'list':
                handle = interface.ListShortcuts(dbus.ObjectPath(data['session']), options)
            elif data['op'] in ('close_session', 'close_request'):
                dbus.Interface(bus.get_object('org.freedesktop.portal.Desktop', data['path'], introspect=False),
                               'org.freedesktop.portal.Session' if data['op'] == 'close_session' else 'org.freedesktop.portal.Request').Close()
                output(done=data['op'], token=token)
                return True
            elif data['op'] == 'disconnect':
                bus.close()
                Gtk.main_quit()
                return False
            else:
                raise ValueError('Unknown operation')
            output(handle=handle, token=token)
        except Exception as error:
            output(error=str(error), token=token)
        return True
    GLib.io_add_watch(sys.stdin, GLib.IO_IN | GLib.IO_HUP, command)
    output(ready=True, unique=bus.get_unique_name(), display=os.environ['DISPLAY'])
    Gtk.main()


def login_service():
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    DBusGMainLoop(set_as_default=True)
    bus = dbus.bus.BusConnection(os.environ['DBUS_SYSTEM_BUS_ADDRESS'])
    name = dbus.service.BusName('org.freedesktop.login1', bus, do_not_queue=True)
    props = {'User': dbus.Struct((dbus.UInt32(os.getuid()), dbus.ObjectPath('/org/freedesktop/login1/user/u1')), signature='uo'),
             'Type': 'x11', 'Class': 'user', 'Remote': False, 'Display': os.environ['DISPLAY'], 'Active': True, 'LockedHint': False}
    path = '/org/freedesktop/login1/session/c1'
    class Session(dbus.service.Object):
        @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='s', out_signature='a{sv}')
        def GetAll(self, interface):
            return props
        @dbus.service.signal('org.freedesktop.DBus.Properties', signature='sa{sv}as')
        def PropertiesChanged(self, interface, changed, invalidated):
            pass
    session = Session(bus, path)
    class Manager(dbus.service.Object):
        sleeping = False
        @dbus.service.method('org.freedesktop.login1.Manager', in_signature='', out_signature='a(susso)')
        def ListSessions(self):
            return [('fixture', os.getuid(), 'fixture', 'seat0', path)]
        @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='s', out_signature='a{sv}')
        def GetAll(self, interface):
            return {'PreparingForSleep': self.sleeping}
        @dbus.service.method('org.example.ShortcutsFixture', in_signature='bbb', out_signature='')
        def SetState(self, active, locked, sleeping):
            props.update(Active=bool(active), LockedHint=bool(locked))
            self.sleeping = bool(sleeping)
            session.PropertiesChanged('org.freedesktop.login1.Session', {'Active': active, 'LockedHint': locked}, [])
            self.PrepareForSleep(sleeping)
        @dbus.service.signal('org.freedesktop.login1.Manager', signature='b')
        def PrepareForSleep(self, sleeping):
            pass
    manager = Manager(bus, '/org/freedesktop/login1')
    output(ready=True)
    GLib.MainLoop().run()


def focus_windows():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    windows = []
    for number in (1, 2):
        window = Gtk.Window(title='Shortcut focus ' + str(number))
        window.set_default_size(350, 120)
        entry = Gtk.Entry()
        window.add(entry)
        entry.connect('key-press-event', lambda _widget, event: (output(key=int(event.keyval)), False)[1])
        window.show_all()
        entry.grab_focus()
        windows.append(window)
    output(ready=True)
    Gtk.main()


def monitor():
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus(private=True)
    bus.add_signal_receiver(lambda *args, member: output(backend_event=member),
                            dbus_interface=IMPL, member_keyword='member')
    output(ready=True)
    GLib.MainLoop().run()


def screenshot(path):
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gdk, Gtk
    Gtk.init([])
    root = Gdk.get_default_root_window()
    image = Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())
    image.savev(path, 'png', [], [])


class Process:
    def __init__(self, process, label):
        self.process, self.label = process, label
        self.pidfd = os.pidfd_open(process.pid)
        self.buffer, self.messages = b'', []
        if process.stdout:
            os.set_blocking(process.stdout.fileno(), False)
    def read(self):
        if not self.process.stdout:
            return
        try:
            chunk = os.read(self.process.stdout.fileno(), 65536)
        except BlockingIOError:
            return
        self.buffer += chunk
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            self.messages.append(json.loads(line))
    def send(self, **message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()
    def kill(self, sig=signal.SIGTERM):
        if self.process.poll() is None:
            try:
                signal.pidfd_send_signal(self.pidfd, sig)
            except ProcessLookupError:
                pass


class Fixture:
    def __init__(self, args):
        self.args = args
        self.directory = Path(args.output).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.processes, self.files, self.observations = [], [], []
        self.helpers = []
        assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0  # Reap only our orphaned relay descendants.
        self.source = Path(__file__).resolve().parents[2]
        self.runtime_tmp = tempfile.TemporaryDirectory(prefix='pleb-gs-')
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.directory / 'home'), 'LANG': 'C.UTF-8',
                    'NO_AT_BRIDGE': '1', 'GTK_USE_PORTAL': '0', 'GSETTINGS_BACKEND': 'memory',
                    'XDG_CURRENT_DESKTOP': 'Pleb', 'XDG_CONFIG_HOME': str(self.directory / 'config'),
                    'XDG_DATA_HOME': str(self.directory / 'data'), 'XDG_RUNTIME_DIR': self.runtime_tmp.name,
                    'XDG_CACHE_HOME': str(self.directory / 'cache'), 'XDG_DATA_DIRS': str(self.directory / 'data'),
                    'GSETTINGS_SCHEMA_DIR': '/usr/share/glib-2.0/schemas',
                    'XDG_CONFIG_DIRS': str(self.directory / 'config'), 'XMODIFIERS': '', 'PYTHONNOUSERSITE': '1'}
        for name in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR', 'XDG_CACHE_HOME'):
            Path(self.env[name]).mkdir(mode=0o700, exist_ok=True)
        self.env['XAUTHORITY'] = str(self.directory / 'Xauthority')
        self.physical = self.xserver('physical')
        self.env['DISPLAY'] = self.physical
        os.environ['XAUTHORITY'] = self.env['XAUTHORITY']
        from Xlib import display
        connection = display.Display(self.physical)
        info = connection.display.info
        self.keyboard_first = info.min_keycode
        self.original_keyboard = connection.get_keyboard_mapping(info.min_keycode, info.max_keycode - info.min_keycode + 1)
        self.original_modifiers = connection.get_modifier_mapping()
        connection.close()
        self.env['PLEB_DESKTOP_DISPLAY'] = self.physical
        self.env['PLEB_DESKTOP_XAUTHORITY'] = self.env['XAUTHORITY']
        self.env['DBUS_SESSION_BUS_ADDRESS'] = self.bus('desktop')
        self.env['DBUS_SYSTEM_BUS_ADDRESS'] = self.bus('logind')
        self.env['PLEB_DESKTOP_BUS_ADDRESS'] = self.env['DBUS_SESSION_BUS_ADDRESS']
        self.private_bus = self.bus('applications')
        self.private_display = self.xserver('applications')
        self.private_bus_b = self.bus('applications-b')
        self.private_display_b = self.xserver('applications-b')
        self.login = self.start('logind', ['--login'])
        self.wait(lambda: any(m.get('ready') for m in self.login.messages))
        self.backend = self.start('backend', command=['/usr/bin/python3', str(self.source / 'lib/global_shortcuts.py')])
        self.wait_name(BACKEND)
        portals = Path(self.env['XDG_DATA_HOME']) / 'xdg-desktop-portal/portals'
        portals.mkdir(parents=True)
        (portals / 'pleb-shortcuts.portal').write_bytes((self.source / 'share/portals/pleb-shortcuts.portal').read_bytes())
        config = Path(self.env['XDG_CONFIG_HOME']) / 'xdg-desktop-portal'
        config.mkdir(parents=True)
        (config / 'pleb-portals.conf').write_text('[preferred]\ndefault=none\norg.freedesktop.impl.portal.GlobalShortcuts=pleb-shortcuts\n')
        (portals / 'pleb-portals.conf').write_bytes((config / 'pleb-portals.conf').read_bytes())
        self.env['XDG_DESKTOP_PORTAL_DIR'] = str(portals)
        self.frontend = self.start('frontend', command=['/usr/libexec/xdg-desktop-portal', '--verbose'])
        self.wait_name('org.freedesktop.portal.Desktop')
        version = self.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.portal.Desktop', '--object-path', ROOT,
                            '--method', 'org.freedesktop.DBus.Properties.Get', PUBLIC, 'version'])
        self.check('real-public-frontend-advertises-version-one', 'uint32 1' in version)
        (self.directory / 'public-introspection.xml').write_text(self.run([
            'gdbus', 'introspect', '--session', '--dest', 'org.freedesktop.portal.Desktop', '--object-path', ROOT, '--xml']))
        (self.directory / 'backend-introspection.xml').write_text(self.run([
            'gdbus', 'introspect', '--session', '--dest', BACKEND, '--object-path', ROOT, '--xml']))
        # Use the real shipped WM, with only the manual-lock executable replaced
        # by a disposable marker. This fixture never starts a real locker.
        marker = self.directory / 'lock-marker'
        marker.write_text('#!/bin/sh\n/usr/bin/touch ' + str(self.directory / 'manual-lock') + '\n')
        marker.chmod(0o700)
        wmconfig = self.directory / 'rc.xml'
        wmconfig.write_text((self.source / 'share/openbox/rc.xml').read_text().replace('pleb-lock', str(marker)))
        self.wm = self.start('wm', command=['openbox', '--config-file', str(wmconfig), '--sm-disable'],
                             env=dict(self.env, XDG_DATA_DIRS=self.env['XDG_DATA_DIRS'] + ':/usr/share'))
        self.wait(lambda: self.wm.process.poll() is None and '0x' in subprocess.run(
            ['xprop', '-root', '_NET_SUPPORTING_WM_CHECK'], env=self.env, capture_output=True, text=True).stdout)
        self.focus = self.start('focus', ['--focus'])
        self.wait(lambda: any(m.get('ready') for m in self.focus.messages))
        self.app_a = self.app('a')
        self.app_b = self.app('b')
        self.monitor = self.start('monitor', ['--monitor'])
        self.wait(lambda: any(m.get('ready') for m in self.monitor.messages))

    def start(self, label, extra=None, command=None, env=None, pass_fds=()):
        log = (self.directory / (label + '.log')).open('w')
        self.files.append(log)
        process = subprocess.Popen(command or ['/usr/bin/python3', __file__] + (extra or []),
                                   env=env or self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=log, text=True, pass_fds=pass_fds)
        result = Process(process, label)
        self.processes.append(result)
        return result

    def run(self, command, env=None):
        result = subprocess.run(command, env=env or self.env, capture_output=True, text=True, timeout=8)
        assert result.returncode == 0, (command, result.stdout, result.stderr)
        return result.stdout.strip()

    def xserver(self, label):
        cookie = os.urandom(16).hex()
        authority = self.directory / (label + '.authority')
        # Server auth matches cookie bytes; the client entry is rewritten with
        # the allocated display once -displayfd returns. No existing display.
        self.run(['xauth', '-f', str(authority), 'add', ':9999', '.', cookie])
        read_fd, write_fd = os.pipe()
        try:
            server = self.start('x-' + label, command=['Xvfb', '-displayfd', str(write_fd), '-auth', str(authority),
                                '-nolisten', 'tcp', '-noreset', '-screen', '0', '1024x768x24'], pass_fds=(write_fd,))
            os.close(write_fd)
            write_fd = -1
            assert select.select([read_fd], [], [], 8)[0], 'Xvfb failed to allocate an owned display'
            display = ':' + os.read(read_fd, 100).decode().strip()
            self.run(['xauth', '-f', self.env['XAUTHORITY'], 'add', display, '.', cookie])
            return display
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)

    def bus(self, label):
        config = self.directory / (label + '-bus.conf')
        config.write_text('<busconfig><type>session</type><listen>unix:tmpdir=' + self.env['XDG_RUNTIME_DIR'] +
                          '</listen><auth>EXTERNAL</auth><policy context="default">'
                          '<allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/>'
                          '</policy></busconfig>')
        bus = self.start('bus-' + label, command=['dbus-daemon', '--nofork', '--config-file=' + str(config), '--print-address=1'])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                line = os.read(bus.process.stdout.fileno(), 4096)
                if line:
                    return line.decode().strip()
            except BlockingIOError:
                pass
            time.sleep(.02)
        raise AssertionError('Private bus failed to start')

    def wait(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for process in self.processes:
                # D-Bus daemon's first address line was read by bus().
                if not process.label.startswith(('bus-', 'backend', 'frontend')) and process.label != 'wm' and not process.label.startswith('x-'):
                    process.read()
            if predicate():
                return
            time.sleep(.015)
        raise AssertionError('Timed out; messages=' + repr({p.label: p.messages for p in self.processes if p.messages}))

    def wait_name(self, name):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus', '--object-path',
                                     '/org/freedesktop/DBus', '--method', 'org.freedesktop.DBus.NameHasOwner', name],
                                    env=self.env, capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and 'true' in result.stdout:
                return
            time.sleep(.05)
        raise AssertionError('Service did not start: ' + name)

    def app(self, label, shared=False):
        env = dict(self.env, DBUS_SESSION_BUS_ADDRESS=self.private_bus_b if label == 'b' else self.private_bus,
                   DISPLAY=self.private_display_b if label == 'b' else self.private_display,
                   KILIX_PORTAL_HOST_BUS=self.env['DBUS_SESSION_BUS_ADDRESS'], KILIX_PRIVATE_XAPP='1')
        relay = self.args.relay
        command = ['/usr/bin/python3', relay, '--wrap', '--', '/usr/bin/python3', __file__, '--app']
        if shared:
            command = ['/usr/bin/python3', __file__, '--app']
        app = self.start('app-' + label, command=command, env=env)
        self.wait(lambda: any(m.get('ready') for m in app.messages))
        for pid in Path('/proc/' + str(app.process.pid) + '/task/' + str(app.process.pid) + '/children').read_text().split():
            self.helpers.append((int(pid), os.pidfd_open(int(pid))))
        return app

    def create(self, app):
        start = len(app.messages)
        app.send(op='create')
        self.wait(lambda: any('response' in m for m in app.messages[start:]))
        response = next(m for m in app.messages[start:] if 'response' in m)
        assert response['response'] == 0, response
        path = response['results']['session_handle']
        unique = next(m['unique'] for m in app.messages if 'unique' in m)
        assert '/session/' + unique[1:].replace('.', '_') + '/' in path, (path, unique)
        return path

    def bind(self, app, session, trigger='CTRL+SHIFT+F8', ident='action'):
        start = len(app.messages)
        app.send(op='bind', session=session, shortcuts=[[ident, {'description': 'Fixture action', 'preferred_trigger': trigger}]])
        self.wait(lambda: any('handle' in m or 'error' in m for m in app.messages[start:]))
        return next(m for m in app.messages[start:] if 'handle' in m or 'error' in m)

    def window(self, name):
        # Public Request handles are returned before the asynchronous backend
        # has necessarily mapped its physical GTK window.
        self.wait(lambda: bool(subprocess.run(['xdotool', 'search', '--onlyvisible', '--name', '^' + name + '$'],
                                              env=self.env, capture_output=True).stdout))
        return self.run(['xdotool', 'search', '--onlyvisible', '--name', '^' + name + '$']).splitlines()[-1]

    def click(self, name, key):
        window = self.window(name)
        self.settle(.15)
        self.run(['xdotool', 'windowactivate', '--sync', window])
        self.run(['xdotool', 'windowfocus', '--sync', window])
        self.settle(.08)
        self.run(['xdotool', 'key', '--clearmodifiers', key])

    def approve(self, app, handle):
        self.wait(lambda: bool(subprocess.run(['xdotool', 'search', '--onlyvisible', '--name', '^Allow global shortcuts$'],
                                              env=self.env, capture_output=True).stdout))
        self.click('Allow global shortcuts', 'alt+a')
        self.wait(lambda: any(m.get('path') == handle and 'response' in m for m in app.messages))
        result = next(m for m in app.messages if m.get('path') == handle and 'response' in m)
        assert result['response'] == 0, result
        return result

    def key(self, key, display=None):
        self.run(['xdotool', 'key', '--clearmodifiers', key], env=dict(self.env, DISPLAY=display or self.physical))

    def edit_trigger(self, name, trigger):
        self.click(name, 'alt+1')
        self.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+a'])
        self.run(['xdotool', 'type', '--clearmodifiers', trigger])

    def free_grab(self, trigger='F8'):
        from Xlib import X, XK, display
        connection = display.Display(self.physical)
        failures = []
        try:
            code = connection.keysym_to_keycode(XK.string_to_keysym(trigger))
            connection.screen().root.grab_key(code, X.ControlMask | X.ShiftMask, False,
                                             X.GrabModeAsync, X.GrabModeAsync,
                                             onerror=lambda err, _request: failures.append(str(err)))
            connection.sync()
            if not failures:
                connection.screen().root.ungrab_key(code, X.ControlMask | X.ShiftMask)
                connection.sync()
            return not failures
        finally:
            connection.close()

    def cancel_dialog(self):
        self.click('Allow global shortcuts', 'Escape')
        self.settle()

    def pointer_allow(self):
        window = self.window('Allow global shortcuts')
        self.settle(.15)
        geometry = dict(line.split('=', 1) for line in self.run(['xdotool', 'getwindowgeometry', '--shell', window]).splitlines())
        self.run(['xdotool', 'mousemove', '--sync', '--window', window,
                  str(int(geometry['WIDTH']) - 55), str(int(geometry['HEIGHT']) - 25)])
        self.run(['xdotool', 'click', '1'])

    def bind_new(self, app, trigger='CTRL+SHIFT+F8'):
        session = self.create(app)
        request = self.bind(app, session, trigger)
        assert 'handle' in request, request
        self.approve(app, request['handle'])
        return session

    def events(self, app, member='Activated'):
        app.read()
        return [m for m in app.messages if m.get('event') == member]

    def settle(self, seconds=.2):
        until = time.monotonic() + seconds
        self.wait(lambda: time.monotonic() >= until, timeout=seconds + 2)

    def check(self, name, condition=True):
        assert condition, name
        self.observations.append(name)
        output(check=name, passed=True)

    def tests(self):
        os.environ['XAUTHORITY'] = self.env['XAUTHORITY']
        a, b = self.app_a, self.app_b
        session = self.create(a)
        request = self.bind(a, session)
        assert 'handle' in request, request
        self.click('Shortcut focus 1', 'Escape')
        self.key('ctrl+shift+F8')
        self.settle()
        self.check('no-events-before-consent', not self.events(a))
        result = self.approve(a, request['handle'])
        self.check('public-create-bind-and-physical-consent', result['results']['shortcuts'][0][1]['trigger_description'] == 'CTRL+SHIFT+F8')
        self.click('Shortcut focus 1', 'Escape')
        self.key('ctrl+shift+F8')
        self.wait(lambda: len(self.events(a, 'Deactivated')) >= 1)
        self.check('cross-focus-activated-and-deactivated', len(self.events(a)) == 1 and self.events(a)[0]['session'] == session)
        self.check('two-unrelated-clients-receive-only-own-events', not self.events(b))
        self.check('backend-events-are-not-broadcast-to-other-desktop-clients', not any('backend_event' in m for m in self.monitor.messages))
        self.key('ctrl+shift+F8', self.private_display)
        self.key('a')
        self.settle()
        self.check('private-display-and-ungranted-keys-produce-no-shortcut-events', len(self.events(a)) == 1)
        self.check('native-keyboard-timestamp', self.events(a, 'Deactivated')[0]['timestamp'] >= self.events(a)[0]['timestamp'] > 0)
        start = len(a.messages)
        a.send(op='list', session=session)
        self.wait(lambda: any('response' in m for m in a.messages[start:]))
        self.check('list-returns-only-granted-shortcuts', next(m for m in a.messages[start:] if 'response' in m)['results']['shortcuts'] == result['results']['shortcuts'])
        wrong = self.bind(b, session, 'CTRL+SHIFT+F9')
        self.check('cross-client-session-refused', 'error' in wrong)
        same_bus = self.app('same-bus', shared=True)
        same_wrong = self.bind(same_bus, session, 'CTRL+SHIFT+F9')
        self.check('same-private-bus-foreign-handle-refused-by-production-relay', 'error' in same_wrong)
        self.negative_and_lifecycle(session, a, b)

    def negative_and_lifecycle(self, session, a, b):
        owner = self.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus', '--object-path',
                          '/org/freedesktop/DBus', '--method', 'org.freedesktop.DBus.GetNameOwner',
                          'org.freedesktop.portal.Desktop']).split("'")[1][1:].replace('.', '_')
        direct = subprocess.run(['gdbus', 'call', '--session', '--dest', BACKEND, '--object-path', ROOT,
                                 '--method', IMPL + '.CreateSession', ROOT + '/request/' + owner + '/bypass',
                                 ROOT + '/session/' + owner + '/bypass', 'org.example.Spoofed', '{}'],
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.check('direct-backend-bypass-refused', direct.returncode != 0 and 'AccessDenied' in direct.stderr)
        repeat = self.bind(a, session)
        if 'handle' in repeat:
            self.wait(lambda: any(m.get('path') == repeat['handle'] and 'response' in m for m in a.messages))
            self.check('bind-once-per-session', next(m for m in a.messages if m.get('path') == repeat['handle'] and 'response' in m)['response'] == 2)
        else:
            self.check('bind-once-per-session')
        conflict_session = self.create(b)
        conflict = self.bind(b, conflict_session)
        self.click('Allow global shortcuts', 'alt+a')
        self.settle()
        self.run(['/usr/bin/python3', __file__, '--screenshot', str(self.directory / 'conflict-consent.png')])
        self.check('conflicting-grant-is-refused-without-replacing-first-client', not any(m.get('path') == conflict['handle'] and 'response' in m for m in b.messages)
                   and 'conflicts with another granted shortcut' in (self.directory / 'backend.log').read_text())
        self.cancel_dialog()
        self.check('conflict-dialog-cancellation', next(m for m in b.messages if m.get('path') == conflict['handle'] and 'response' in m)['response'] == 1)
        canceled_session = self.create(b)
        canceled = self.bind(b, canceled_session, 'CTRL+SHIFT+F9')
        self.wait(lambda: bool(subprocess.run(['xdotool', 'search', '--onlyvisible', '--name', '^Allow global shortcuts$'],
                                              env=self.env, capture_output=True).stdout))
        b.send(op='close_request', path=canceled['handle'])
        self.wait(lambda: any(m.get('done') == 'close_request' for m in b.messages))
        self.settle()
        self.check('request-close-removes-dialog-and-acquires-no-grabs', self.free_grab('F9') and
                   not subprocess.run(['xdotool', 'search', '--onlyvisible', '--name', '^Allow global shortcuts$'],
                                      env=self.env, capture_output=True).stdout)
        self.extra_request_checks(b)
        self.click('Pleb global shortcuts', 'alt+c')
        self.edit_trigger('Configure global shortcuts', 'CTRL+SHIFT+F10')
        self.click('Configure global shortcuts', 'alt+a')
        self.wait(lambda: bool(self.events(a, 'ShortcutsChanged')))
        self.check('physical-reconfiguration-emits-shortcuts-changed-and-releases-old-grab',
                   self.free_grab('F8') and self.events(a, 'ShortcutsChanged')[-1]['shortcuts'][0][1]['trigger_description'] == 'CTRL+SHIFT+F10')
        self.click('Pleb global shortcuts', 'alt+c')
        self.edit_trigger('Configure global shortcuts', 'CTRL+SHIFT+F8')
        self.click('Configure global shortcuts', 'alt+a')
        self.wait(lambda: len(self.events(a, 'ShortcutsChanged')) >= 2)
        session_b = self.bind_new(b, 'CTRL+SHIFT+F9')
        self.click('Shortcut focus 1', 'Escape')
        a_count, b_count = len(self.events(a)), len(self.events(b))
        b_released = len(self.events(b, 'Deactivated'))
        self.key('ctrl+shift+F9')
        self.wait(lambda: len(self.events(b, 'Deactivated')) > b_released)
        self.check('second-private-pane-has-distinct-grant-and-signal-scope', len(self.events(a)) == a_count and
                   len(self.events(b)) == b_count + 1 and self.events(b)[-1]['session'] == session_b)
        self.native_input(a)
        self.lifecycles(a, b, session_b)

    def extra_request_checks(self, b):
        from Xlib import X, XK, display
        external = display.Display(self.physical)
        code = external.keysym_to_keycode(XK.string_to_keysym('F11'))
        external.screen().root.grab_key(code, X.ControlMask | X.ShiftMask, False, X.GrabModeAsync, X.GrabModeAsync)
        external.sync()
        try:
            session = self.create(b)
            request = self.bind(b, session, 'CTRL+SHIFT+F11')
            self.pointer_allow()
            self.settle()
            self.check('other-native-client-grab-produces-real-badaccess', not any(m.get('path') == request['handle'] and 'response' in m for m in b.messages)
                       and 'already used by the desktop or another application' in (self.directory / 'backend.log').read_text())
            self.cancel_dialog()
            b.send(op='close_session', path=session)
            self.settle()
        finally:
            external.close()
        self.check('failed-grab-transaction-does-not-leak-native-grabs', self.free_grab('F11'))
        session = self.create(b)
        opaque = '$(touch "$HOME/field-command")'
        start = len(b.messages)
        b.send(op='bind', session=session, shortcuts=[[opaque, {'description': opaque,
               'preferred_trigger': 'CTRL+SHIFT+F11;' + opaque}]])
        self.wait(lambda: any('handle' in m for m in b.messages[start:]))
        request = next(m['handle'] for m in b.messages[start:] if 'handle' in m)
        self.edit_trigger('Allow global shortcuts', 'CTRL+SHIFT+F11')
        self.approve(b, request)
        self.click('Shortcut focus 1', 'Escape')
        released = len(self.events(b, 'Deactivated'))
        self.key('ctrl+shift+F11')
        self.wait(lambda: len(self.events(b, 'Deactivated')) > released)
        self.check('application-fields-remain-opaque-data-and-never-shell-commands', self.events(b)[-1]['ident'] == opaque
                   and not (Path(self.env['HOME']) / 'field-command').exists())
        b.send(op='close_session', path=session)
        self.settle()

    def native_input(self, a):
        self.key('super+l')
        self.wait(lambda: (self.directory / 'manual-lock').exists())
        self.check('manual-super-l-still-reaches-owned-wm')
        before = self.run(['xdotool', 'getactivewindow'])
        self.key('alt+Tab')
        after = self.run(['xdotool', 'getactivewindow'])
        self.check('manual-alt-tab-still-switches-physical-windows', before != after)
        self.click('Shortcut focus 1', 'Escape')
        a_count, released = len(self.events(a)), len(self.events(a, 'Deactivated'))
        self.run(['xdotool', 'keydown', 'Control_L', 'Shift_L', 'F8'])
        self.wait(lambda: len(self.events(a)) > a_count)
        self.settle(.15)
        self.check('held-trigger-stays-active', len(self.events(a, 'Deactivated')) == released)
        focus_count = len(self.focus.messages)
        self.run(['xdotool', 'key', 'b'])
        self.settle()
        self.check('intervening-typing-goes-to-focused-window', any(m.get('key') in (ord('b'), ord('B')) for m in self.focus.messages[focus_count:]))
        self.run(['xdotool', 'keyup', 'Control_L'])
        self.wait(lambda: len(self.events(a, 'Deactivated')) > released)
        self.run(['xdotool', 'keyup', 'F8', 'Shift_L'])
        self.check('modifier-release-first-deactivates-without-stuck-state', len(self.events(a)) == a_count + 1)
        self.run(['xmodmap', '-e', 'add Mod3 = Scroll_Lock'])
        self.settle(.25)
        for lock in ('Caps_Lock', 'Num_Lock', 'Scroll_Lock'):
            count = len(self.events(a))
            self.run(['xdotool', 'key', lock])
            self.run(['xdotool', 'key', 'ctrl+shift+F8'])
            self.wait(lambda: len(self.events(a)) > count)
            self.run(['xdotool', 'key', lock])
            self.settle()
            self.check('shortcut-with-' + lock, len(self.events(a)) == count + 1)
        self.run(['xmodmap', '-e', 'clear Mod3'])
        self.run(['setxkbmap', '-layout', 'de'])
        self.settle(.25)
        count = len(self.events(a))
        self.key('ctrl+shift+F8')
        self.wait(lambda: len(self.events(a)) > count)
        self.check('single-german-layout-remap-retains-semantic-chord')
        self.run(['setxkbmap', '-layout', 'us'])
        self.settle()
        from Xlib import XK, display
        connection = display.Display(self.physical)
        code8 = connection.keysym_to_keycode(XK.string_to_keysym('F8'))
        code10 = connection.keysym_to_keycode(XK.string_to_keysym('F10'))
        connection.close()
        self.run(['xmodmap', '-e', 'keycode ' + str(code8) + ' = F10', '-e', 'keycode ' + str(code10) + ' = F8'])
        self.settle(.25)
        count = len(self.events(a))
        self.key('ctrl+shift+F8')
        self.wait(lambda: len(self.events(a)) > count)
        self.key('ctrl+shift+F10')
        self.settle()
        self.check('keycode-remap-regrabs-new-code-and-releases-old-code', len(self.events(a)) == count + 1)
        self.run(['xmodmap', '-e', 'clear Control', '-e', 'add Mod3 = Control_L Control_R'])
        self.settle(.25)
        count = len(self.events(a))
        self.key('ctrl+shift+F8')
        self.wait(lambda: len(self.events(a)) > count)
        self.check('remapped-control-modifier-uses-actual-modifier-map')
        self.run(['xdotool', 'keyup', 'Control_L', 'Control_R', 'Shift_L', 'Shift_R', 'Alt_L', 'Alt_R', 'Super_L', 'Super_R'])
        connection = display.Display(self.physical)
        connection.change_keyboard_mapping(self.keyboard_first, self.original_keyboard)
        connection.set_modifier_mapping(self.original_modifiers)
        connection.sync()
        connection.close()
        self.settle(.25)

    def lifecycles(self, a, b, session_b):
        b.send(op='close_session', path=session_b)
        self.wait(lambda: any(m.get('done') == 'close_session' for m in b.messages))
        self.settle()
        self.check('session-close-releases-native-grabs', self.free_grab('F9'))
        pending_b = self.create(b)
        request_b = self.bind(b, pending_b, 'CTRL+SHIFT+F9')
        a.kill(signal.SIGKILL)
        a.process.wait(timeout=3)
        self.settle(.3)
        self.check('hard-app-death-releases-physical-grabs', self.free_grab('F8'))
        self.approve(b, request_b['handle'])
        self.click('Shortcut focus 1', 'Escape')
        count = len(self.events(b))
        self.key('ctrl+shift+F9')
        self.wait(lambda: len(self.events(b)) > count)
        self.check('unrelated-pending-consent-survives-other-app-death')
        self.frontend.kill(signal.SIGKILL)
        self.frontend.process.wait(timeout=3)
        self.wait(lambda: any(m.get('event') == 'Closed' and m.get('session') == pending_b for m in b.messages))
        self.settle()
        self.check('frontend-hard-restart-closes-relay-sessions-and-grabs', self.free_grab('F9'))
        self.frontend = self.start('frontend-restarted', command=['/usr/libexec/xdg-desktop-portal', '--verbose'])
        self.wait_name('org.freedesktop.portal.Desktop')
        active_b = self.bind_new(b, 'CTRL+SHIFT+F9')
        self.backend.kill(signal.SIGKILL)
        self.backend.process.wait(timeout=3)
        self.check('backend-hard-death-releases-server-grabs', self.free_grab('F9'))
        self.backend = self.start('backend-restarted', command=['/usr/bin/python3', str(self.source / 'lib/global_shortcuts.py')])
        self.wait_name(BACKEND)
        self.wait(lambda: any(m.get('event') == 'Closed' and m.get('session') == active_b for m in b.messages))
        self.check('backend-restart-journal-closes-old-public-session')
        lock_session = self.bind_new(b, 'CTRL+SHIFT+F9')
        self.run(['gdbus', 'call', '--address', self.env['DBUS_SYSTEM_BUS_ADDRESS'], '--dest', 'org.freedesktop.login1',
                  '--object-path', '/org/freedesktop/login1', '--method', 'org.example.ShortcutsFixture.SetState', 'true', 'true', 'false'])
        self.wait(lambda: any(m.get('event') == 'Closed' and m.get('session') == lock_session for m in b.messages))
        self.check('trusted-lock-guard-closes-session-and-releases-grabs', self.free_grab('F9'))
        start = len(b.messages)
        b.send(op='create')
        self.wait(lambda: any('response' in m for m in b.messages[start:]))
        self.settle(.1)
        close_error = 'UnknownMethod: Method "Close" with signature "" on interface "org.freedesktop.impl.portal.Session"'
        self.check('locked-desktop-refuses-fresh-session', next(m for m in b.messages[start:] if 'response' in m)['response'] == 2
                   and close_error not in (self.directory / 'frontend-restarted.log').read_text())
        self.run(['gdbus', 'call', '--address', self.env['DBUS_SYSTEM_BUS_ADDRESS'], '--dest', 'org.freedesktop.login1',
                  '--object-path', '/org/freedesktop/login1', '--method', 'org.example.ShortcutsFixture.SetState', 'true', 'false', 'false'])
        self.settle()
        count = len(self.events(b))
        self.key('ctrl+shift+F9')
        self.settle()
        self.check('unlock-does-not-restore-unapproved-grabs-or-events', len(self.events(b)) == count and self.free_grab('F9'))
        # Remove only our owned WM so explicit reservation is tested without
        # being masked by the WM's own BadAccess protection.
        self.wm.kill()
        self.wm.process.wait(timeout=3)
        reserved_session = self.create(b)
        # Use a new ID so remembered preferences cannot prefill a different
        # previously approved chord and mask the actual reservation check.
        reserved = self.bind(b, reserved_session, 'LOGO+l', ident='reserved-lock')
        self.settle(.25)
        window = self.window('Allow global shortcuts')
        self.run(['xdotool', 'windowfocus', '--sync', window])
        self.settle(.15)
        self.pointer_allow()
        self.settle()
        self.run(['/usr/bin/python3', __file__, '--screenshot', str(self.directory / 'reserved-consent.png')])
        self.check('desktop-lock-chord-reserved-even-with-wm-absent', not any(m.get('path') == reserved['handle'] and 'response' in m for m in b.messages)
                   and 'reserved by the desktop' in (self.directory / 'backend-restarted.log').read_text())
        b.send(op='close_request', path=reserved['handle'])
        self.settle()
        disconnect_session = self.create(b)
        disconnect_request = self.bind(b, disconnect_session, 'CTRL+SHIFT+F9')
        self.settle()
        b.send(op='disconnect')
        b.process.wait(timeout=3)
        self.settle(.25)
        self.check('normal-disconnect-removes-pending-consent-and-grabs', self.free_grab('F9') and
                   not subprocess.run(['xdotool', 'search', '--onlyvisible', '--name', '^Allow global shortcuts$'],
                                      env=self.env, capture_output=True).stdout)

    def close(self):
        for process in reversed(self.processes):
            process.kill()
            try:
                process.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill(signal.SIGKILL)
                process.process.wait(timeout=2)
            os.close(process.pidfd)
            if process.label.startswith(('backend', 'frontend')) or process.label == 'wm':
                remaining = process.process.stdout.read()
                (self.directory / (process.label + '-stdout.log')).write_text(remaining or '')
            for stream in (process.process.stdin, process.process.stdout):
                if stream:
                    stream.close()
        for stream in self.files:
            stream.close()
        for pid, fd in getattr(self, 'helpers', []):
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            os.close(fd)
        if hasattr(self, 'runtime_tmp'):
            self.runtime_tmp.cleanup()
        (self.directory / 'observations.json').write_text(json.dumps(self.observations, indent=2) + '\n')
        (self.directory / 'client-events.json').write_text(json.dumps({p.label: p.messages for p in self.processes if p.messages}, indent=2) + '\n')
        output(cleanup=True, owned_pids=[p.process.pid for p in self.processes] + [pid for pid, fd in getattr(self, 'helpers', [])])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', action='store_true')
    parser.add_argument('--login', action='store_true')
    parser.add_argument('--focus', action='store_true')
    parser.add_argument('--monitor', action='store_true')
    parser.add_argument('--screenshot')
    parser.add_argument('--output')
    parser.add_argument('--relay')
    args = parser.parse_args()
    if args.app:
        application()
    elif args.login:
        login_service()
    elif args.focus:
        focus_windows()
    elif args.monitor:
        monitor()
    elif args.screenshot:
        screenshot(args.screenshot)
    else:
        fixture = Fixture.__new__(Fixture)
        fixture.processes, fixture.files, fixture.observations = [], [], []
        fixture.directory = Path(args.output)
        try:
            fixture.__init__(args)
            fixture.tests()
        finally:
            fixture.close()


if __name__ == '__main__':
    main()
