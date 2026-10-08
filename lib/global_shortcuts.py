#!/usr/bin/python3
"""Version 1 GlobalShortcuts backend and consent on Pleb's physical X11 desktop."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile
import unicodedata

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk

from capture_portal import Properties, Request, ROOT, IMPL, FRONTEND
from capture_sources import physical_environment, CaptureError
from capture_session import CaptureSessionGuard
from shortcut_keys import ShortcutKeys, ShortcutError

BUS_NAME = 'org.freedesktop.impl.portal.desktop.pleb.shortcuts'
INTERFACE = IMPL + 'GlobalShortcuts'
HANDLE = re.compile(re.escape(ROOT) + r'/(request|session)/([0-9]+(?:_[0-9]+)+)/[A-Za-z0-9_]+\Z')


def text(value, limit=200):
    return ''.join(char for char in str(value) if not unicodedata.category(char).startswith('C'))[:limit]


def shortcut_array(items):
    return dbus.Array([dbus.Struct((ident, dbus.Dictionary(
        {'description': item['description'], 'trigger_description': item['trigger']}, signature='sv')),
        signature='sa{sv}') for ident, item in items.items() if item['trigger']], signature='(sa{sv})')


class ConsentRequest(Request):
    def __init__(self, portal, path, success, session):
        super().__init__(portal, path, success)
        self.session = session
        self.timer = GLib.timeout_add_seconds(120, self.expire)

    def expire(self):
        self.finish(1)
        return False

    def finish(self, response, results=None):
        if self.done:
            return
        GLib.source_remove(self.timer)
        if self.session.pending is self:
            self.session.pending = None
        super().finish(response, results)


class Session(Properties):
    properties = {IMPL + 'Session': {'version': dbus.UInt32(1)}}

    def __init__(self, portal, path, owner, app, frontend):
        super().__init__(portal.bus, path)
        self.portal, self.path, self.owner, self.app, self.frontend = portal, str(path), owner, str(app), frontend
        self.closed, self.attempted = False, False
        self.shortcuts, self.pending, self.indicator, self.editor = {}, None, None, None
        self.status = None

    @property
    def identity(self):
        return (text(self.app) or 'Unidentified application') + ' — connection ' + self.owner

    @property
    def history_key(self):
        # Anonymous private applications must never share saved choices.
        return ('app', self.app) if self.app else ('caller', self.owner)

    @dbus.service.method(IMPL + 'Session', in_signature='', out_signature='', sender_keyword='sender')
    def Close(self, sender=None):
        self.portal.authenticate(sender)
        if sender != self.frontend:
            raise ShortcutError('Stale frontend session')
        self.close()

    @dbus.service.signal(IMPL + 'Session', signature='')
    def Closed(self):
        pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.portal.keys.remove(self)
        if self.pending:
            self.pending.finish(1)
            self.pending = None
        for window in (self.editor, self.indicator):
            if window:
                window.destroy()
        self.editor = self.indicator = None
        self.portal.sessions.pop(self.path, None)
        self.portal.save_sessions()
        self.portal.emit(self.frontend, self.path, IMPL + 'Session', 'Closed', '', ())
        self.remove_from_connection()

    def apply(self, items):
        self.portal.require_unlocked()
        chords = {ident: self.portal.keys.chord(item['trigger']) for ident, item in items.items() if item['trigger']}
        self.portal.keys.replace(self, chords)
        self.shortcuts = {ident: dict(item, trigger=chords[ident].trigger if ident in chords else '')
                          for ident, item in items.items()}
        history = self.portal.history
        history[self.history_key] = dict(self.shortcuts)
        while len(history) > 64:
            history.pop(next(iter(history)))
        self.update_status()

    def update_status(self, error=None):
        if self.status:
            summary = '\n'.join(item['description'] + ': ' + (item['trigger'] or 'Disabled')
                                for item in self.shortcuts.values())
            self.status.set_text(error or summary or 'No shortcuts enabled')

    def show_indicator(self):
        self.indicator = Gtk.Window(title='Pleb global shortcuts')
        self.indicator.set_wmclass('pleb-shortcuts', 'Pleb-shortcuts')
        self.indicator.set_default_size(440, 150)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)
        label = Gtk.Label(label=self.identity)
        label.set_line_wrap(True)
        box.pack_start(label, False, False, 0)
        self.status = Gtk.Label()
        self.status.set_line_wrap(True)
        box.pack_start(self.status, True, True, 0)
        configure = Gtk.Button.new_with_mnemonic('_Configure shortcuts')
        configure.connect('clicked', lambda *_args: self.edit(self.shortcuts))
        box.pack_start(configure, False, False, 0)
        revoke = Gtk.Button.new_with_mnemonic('_Revoke shortcuts')
        revoke.connect('clicked', lambda *_args: self.close())
        box.pack_start(revoke, False, False, 0)
        self.indicator.add(box)
        self.indicator.connect('delete-event', lambda *_args: (self.close(), True)[1])
        self.update_status()
        self.indicator.show_all()

    def edit(self, items, request=None):
        if self.editor:
            self.editor.present()
            return
        self.portal.require_unlocked()
        dialog = Gtk.Dialog(title='Allow global shortcuts' if request else 'Configure global shortcuts', modal=True)
        dialog.set_wmclass('pleb-shortcuts', 'Pleb-shortcuts')
        dialog.set_keep_above(True)
        dialog.set_default_size(600, 200)
        dialog.add_button('_Cancel', Gtk.ResponseType.CANCEL)
        dialog.add_button('_Allow' if request else '_Apply', Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        self.editor = dialog
        if request:
            request.dialog = dialog
        area = dialog.get_content_area()
        area.set_border_width(12)
        intro = Gtk.Label(label=self.identity + '\nThese shortcuts work while another window is focused.\n'
                          'Edit each XDG chord (for example CTRL+SHIFT+F8). Leave it empty to disable it.')
        intro.set_line_wrap(True)
        area.pack_start(intro, False, False, 8)
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        entries = {}
        for row, (ident, item) in enumerate(items.items()):
            label = (Gtk.Label.new_with_mnemonic('_' + str(row + 1) + ' ' + item['description'].replace('_', '__'))
                     if row < 9 else Gtk.Label(label=item['description']))
            label.set_line_wrap(True)
            label.set_max_width_chars(40)
            entry = Gtk.Entry()
            entry.set_max_length(64)
            entry.set_text(item['trigger'])
            label.set_mnemonic_widget(entry)
            entry.get_accessible().set_name(item['description'] + ' shortcut')
            grid.attach(label, 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
            entries[ident] = entry
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(450)
        scroll.set_propagate_natural_height(True)
        scroll.add(grid)
        area.pack_start(scroll, True, True, 8)
        problem = Gtk.Label()
        problem.set_line_wrap(True)
        area.pack_start(problem, False, False, 8)

        def response(_dialog, code):
            if self.closed or (request and request.done):
                return
            if code == Gtk.ResponseType.OK:
                try:
                    choices = {ident: dict(items[ident], trigger=entry.get_text().strip())
                               for ident, entry in entries.items()}
                    self.apply(choices)
                except ShortcutError as error:
                    print('Shortcut configuration refused: ' + str(error), file=sys.stderr, flush=True)
                    problem.set_text(str(error))
                    return
            self.editor = None
            if request:
                self.pending = None
                request.finish(0 if code == Gtk.ResponseType.OK else 1,
                               {'shortcuts': shortcut_array(self.shortcuts)} if code == Gtk.ResponseType.OK else {})
                if code == Gtk.ResponseType.OK:
                    self.show_indicator()
            else:
                dialog.destroy()
                if code == Gtk.ResponseType.OK:
                    self.portal.changed(self)

        dialog.connect('response', response)
        dialog.connect('destroy', lambda *_args: setattr(self, 'editor', None))
        dialog.show_all()
        # The first default action is always cancellation, never silent grant.
        dialog.get_widget_for_response(Gtk.ResponseType.CANCEL).grab_focus()
        dialog.present()


class Portal(Properties):
    properties = {INTERFACE: {'version': dbus.UInt32(1)}}

    def __init__(self, bus):
        self.bus = bus
        self.name = dbus.service.BusName(BUS_NAME, bus=bus, do_not_queue=True)
        super().__init__(bus, ROOT)
        self.sessions, self.requests, self.history = {}, {}, {}
        self.state = self.state_path()
        previous = self.load_sessions()
        self.keys = ShortcutKeys(self.activated, self.deactivated, self.remapped, self.input_failed)
        environment = physical_environment()
        self.guard = CaptureSessionGuard(self.close, environment['DISPLAY'],
                                         session_id=environment.get('PLEB_DESKTOP_SESSION_ID'))
        bus.add_signal_receiver(self.owner_changed, signal_name='NameOwnerChanged',
                                dbus_interface='org.freedesktop.DBus', bus_name='org.freedesktop.DBus')
        bus.call_on_disconnection(lambda _bus: (self.close(), Gtk.main_quit()))
        # Hard death cannot emit Closed. Retain only volatile handle identities
        # so the next supervised/activated backend closes the old frontend
        # sessions. Never restore grants, triggers, or application permissions.
        for path, frontend in previous:
            try:
                if frontend == bus.get_name_owner(FRONTEND) and HANDLE.fullmatch(path):
                    self.emit(frontend, path, IMPL + 'Session', 'Closed', '', ())
            except dbus.exceptions.DBusException:
                pass
        self.save_sessions()

    def state_path(self):
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR', ''))
        info = runtime.lstat()
        if not runtime.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ShortcutError('A private session runtime directory is required')
        scope = os.environ['DISPLAY'] + '\0' + os.environ['DBUS_SESSION_BUS_ADDRESS']
        directory = runtime / ('pleb-shortcuts-' + hashlib.sha256(scope.encode()).hexdigest()[:16])
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ShortcutError('Unsafe shortcut runtime directory')
        return directory / 'sessions.json'

    def load_sessions(self):
        try:
            fd = os.open(self.state, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return []
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 32768:
                raise ShortcutError('Unsafe shortcut session record')
            records = json.loads(stream.read(32769))
        if (not isinstance(records, list) or len(records) > 16
                or any(not isinstance(row, list) or len(row) != 2 or any(not isinstance(item, str) for item in row)
                       or len(row[0]) > 1024 or not re.fullmatch(r':[0-9]+(?:\.[0-9]+)+', row[1]) for row in records)):
            raise ShortcutError('Invalid shortcut session record')
        return records

    def save_sessions(self):
        fd, temporary = tempfile.mkstemp(dir=self.state.parent, prefix='.sessions-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump([[session.path, session.frontend] for session in self.sessions.values()], stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def authenticate(self, sender):
        try:
            if sender and sender == self.bus.get_name_owner(FRONTEND):
                return
        except dbus.exceptions.DBusException:
            pass
        raise dbus.exceptions.DBusException('Use the public desktop portal', name='org.freedesktop.DBus.Error.AccessDenied')

    def owner(self, path, kind):
        if len(str(path)) > 1024:
            raise ShortcutError('Excessive portal handle')
        match = HANDLE.fullmatch(str(path))
        if not match or match[1] != kind:
            raise ShortcutError('Invalid portal handle')
        owner = ':' + match[2].replace('_', '.')
        try:
            if self.bus.get_unix_user(owner) != os.getuid():
                raise ShortcutError('Wrong session user')
        except dbus.exceptions.DBusException:
            raise ShortcutError('The requesting application disconnected') from None
        return owner

    def session(self, path, handle):
        session = self.sessions.get(str(path))
        if session is None or session.closed or self.owner(handle, 'request') != session.owner:
            raise ShortcutError('Unknown or foreign shortcuts session')
        return session

    def require_unlocked(self):
        if not self.guard.can_capture:
            raise ShortcutError('The physical desktop is locked, inactive or unavailable')

    def emit(self, destination, path, interface, member, signature, args):
        # Backend signals go only to the authenticated frontend; the public
        # frontend unicasts them to the original caller, including via Kilix.
        message = dbus.lowlevel.SignalMessage(path, interface, member)
        message.set_destination(destination)
        message.append(*args, signature=signature)
        try:
            self.bus.send_message(message)
        except dbus.exceptions.DBusException:
            pass

    def activated(self, session, ident, timestamp):
        if not session.closed and self.guard.can_capture:
            self.emit(session.frontend, ROOT, INTERFACE, 'Activated', 'osta{sv}',
                      (session.path, ident, dbus.UInt64(timestamp), dbus.Dictionary({}, signature='sv')))

    def deactivated(self, session, ident, timestamp):
        if not session.closed and self.guard.can_capture:
            self.emit(session.frontend, ROOT, INTERFACE, 'Deactivated', 'osta{sv}',
                      (session.path, ident, dbus.UInt64(timestamp), dbus.Dictionary({}, signature='sv')))

    def changed(self, session):
        self.emit(session.frontend, ROOT, INTERFACE, 'ShortcutsChanged', 'oa(sa{sv})',
                  (session.path, shortcut_array(session.shortcuts)))

    @dbus.service.signal(INTERFACE, signature='osta{sv}')
    def Activated(self, session_handle, shortcut_id, timestamp, options):
        pass

    @dbus.service.signal(INTERFACE, signature='osta{sv}')
    def Deactivated(self, session_handle, shortcut_id, timestamp, options):
        pass

    @dbus.service.signal(INTERFACE, signature='oa(sa{sv})')
    def ShortcutsChanged(self, session_handle, shortcuts):
        pass

    @dbus.service.method(INTERFACE, in_signature='oosa{sv}', out_signature='ua{sv}', sender_keyword='sender')
    def CreateSession(self, handle, session_handle, app_id, options, sender=None):
        self.authenticate(sender)
        try:
            self.require_unlocked()
            owner = self.owner(session_handle, 'session')
            if owner != self.owner(handle, 'request') or str(session_handle) in self.sessions or len(self.sessions) >= 16:
                raise ShortcutError('Duplicate, foreign or excessive session')
            self.sessions[str(session_handle)] = Session(self, session_handle, owner, app_id, sender)
            self.save_sessions()
            return 0, {}
        except ShortcutError:
            return 2, {}

    @dbus.service.method(INTERFACE, in_signature='ooa(sa{sv})sa{sv}', out_signature='ua{sv}',
                         async_callbacks=('success', 'failure'), sender_keyword='sender')
    def BindShortcuts(self, handle, session_handle, shortcuts, parent_window, options, success, failure, sender=None):
        self.authenticate(sender)
        request = None
        try:
            self.require_unlocked()
            session = self.session(session_handle, handle)
            if session.attempted or not 1 <= len(shortcuts) <= 16:
                raise ShortcutError('Bind once per session, with 1 to 16 shortcuts')
            items = {}
            for ident, properties in shortcuts:
                ident = str(ident)
                description = properties.get('description')
                trigger = properties.get('preferred_trigger', '')
                if (not ident or len(ident) > 128 or ident in items or not isinstance(description, str)
                        or not text(description) or not isinstance(trigger, str) or len(trigger) > 64):
                    raise ShortcutError('Invalid shortcut description, ID or trigger')
                items[ident] = {'description': text(description), 'trigger': str(trigger)}
            session.attempted = True
            request = ConsentRequest(self, handle, success, session)
            session.pending = request
            session.edit(items, request)
        except (ShortcutError, CaptureError):
            if request:
                request.finish(2)
            else:
                success(dbus.UInt32(2), dbus.Dictionary({}, signature='sv'))

    @dbus.service.method(INTERFACE, in_signature='oo', out_signature='ua{sv}', sender_keyword='sender')
    def ListShortcuts(self, handle, session_handle, sender=None):
        self.authenticate(sender)
        try:
            session = self.session(session_handle, handle)
            items = session.shortcuts if session.attempted else self.history.get(session.history_key, {})
            return 0, {'shortcuts': shortcut_array(items)}
        except ShortcutError:
            return 2, {}

    def remapped(self):
        for session in list(self.sessions.values()):
            if not session.shortcuts:
                continue
            try:
                session.apply(session.shortcuts)
            except ShortcutError as error:
                session.shortcuts = {ident: dict(item, trigger='') for ident, item in session.shortcuts.items()}
                session.update_status(str(error) + '\nConfigure the shortcuts again.')
            self.changed(session)

    def owner_changed(self, name, old, new):
        if old and old != new:
            if name == FRONTEND:
                self.close()
            else:
                for session in list(self.sessions.values()):
                    if session.owner == name:
                        session.close()
                self.history.pop(('caller', str(name)), None)

    def close(self):
        for session in list(self.sessions.values()):
            session.close()
        for request in list(self.requests.values()):
            request.finish(1)

    def input_failed(self):
        self.close()
        Gtk.main_quit()


def main():
    os.environ.update(physical_environment())
    os.umask(0o077)
    DBusGMainLoop(set_as_default=True)
    Gtk.init([])
    portal = Portal(dbus.SessionBus())
    def stop():
        portal.close()
        Gtk.main_quit()
        return False
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, stop)
    try:
        Gtk.main()
    finally:
        portal.close()
        portal.guard.close()
        portal.keys.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('pleb global shortcuts: ' + str(error), file=sys.stderr)
        sys.exit(1)
