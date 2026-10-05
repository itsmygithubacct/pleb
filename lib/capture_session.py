"""Watch the physical desktop's lock state on logind's system bus."""
import os

import dbus

LOGIN = 'org.freedesktop.login1'
ROOT = '/org/freedesktop/login1'
MANAGER = LOGIN + '.Manager'
SESSION = LOGIN + '.Session'
PROPERTIES = 'org.freedesktop.DBus.Properties'


class CaptureSessionGuard:
    def __init__(self, blocked, display, *, session_id=None, bus=None, uid=None):
        self.blocked = blocked
        self.display = display.split('.', 1)[0]
        # pleb-session publishes the logind session its locker reports for.
        # Once that seated session has been seen, the guard follows it whatever
        # its Type (a startx login is Type=tty with no Display) and fails closed
        # if it disappears. An id that names no current session is stale, left
        # in the activation environment by an earlier login, and is ignored.
        # Unbound, only an x11 login on this display is managed and a nested X
        # server with no login of its own stays unmanaged.
        self.session_id = str(session_id) if session_id else None
        self.bound = False
        self.uid = os.getuid() if uid is None else uid
        self.path, self.properties = None, {}
        self.managed, self.ready, self.sleeping = False, False, False
        self.matches = []
        self.owns_bus = bus is None
        self.bus = bus
        try:
            if self.bus is None:
                self.bus = dbus.SystemBus(private=True)
            # Subscribe before the initial snapshot. Match the system service,
            # never a private app's claims about its own lock state.
            self.matches.append(self.bus.add_signal_receiver(
                self.changed, signal_name='PropertiesChanged', dbus_interface=PROPERTIES,
                bus_name=LOGIN, path_keyword='path'))
            for member in ('SessionNew', 'SessionRemoved'):
                self.matches.append(self.bus.add_signal_receiver(
                    self.sessions_changed, signal_name=member, dbus_interface=MANAGER,
                    bus_name=LOGIN, path=ROOT))
            self.matches.append(self.bus.add_signal_receiver(
                self.prepare_sleep, signal_name='PrepareForSleep', dbus_interface=MANAGER,
                bus_name=LOGIN, path=ROOT))
            self.matches.append(self.bus.add_signal_receiver(
                self.owner_changed, signal_name='NameOwnerChanged',
                dbus_interface='org.freedesktop.DBus', bus_name='org.freedesktop.DBus',
                path='/org/freedesktop/DBus', arg0=LOGIN))
            self.bus.call_on_disconnection(lambda _bus: self.fail())
            self.refresh()
        except dbus.exceptions.DBusException:
            self.fail()

    @property
    def can_capture(self):
        if not self.ready or self.sleeping:
            return False
        if self.path is None:
            # Nested X servers need not have their own logind session. Once
            # bound to a real desktop, losing that session must fail closed.
            return not self.managed
        return bool(self.properties.get('Active')) and not bool(self.properties.get('LockedHint', True))

    def get_properties(self, path, interface):
        return dict(dbus.Interface(self.bus.get_object(LOGIN, path), PROPERTIES)
                    .GetAll(interface, timeout=1))

    def refresh(self):
        before, previous = self.can_capture, self.path
        try:
            manager = dbus.Interface(self.bus.get_object(LOGIN, ROOT), MANAGER)
            candidates = []
            own = [row for row in manager.ListSessions(timeout=1) if int(row[1]) == self.uid]
            if len(own) > 32:
                raise dbus.exceptions.DBusException('Too many desktop sessions')
            if self.session_id is not None and not self.bound:
                self.bound = any(str(row[0]) == self.session_id and row[3] for row in own)
            if self.bound:
                self.managed = True
            for sid, _uid, _user, seat, path in own:
                if self.bound and str(sid) != self.session_id:
                    continue
                props = self.get_properties(path, SESSION)
                display = str(props.get('Display', '')).split('.', 1)[0]
                if self.bound:
                    login = bool(seat) and (props.get('Type') != 'x11' or display == self.display)
                else:
                    login = props.get('Type') == 'x11' and display == self.display
                if (int(props.get('User', (-1,))[0]) == self.uid
                        and login
                        and props.get('Class') in ('user', 'user-early', 'user-light')
                        and not props.get('Remote', True)):
                    self.managed = True
                    if props.get('Active'):
                        candidates.append((str(path), props))
            if len(candidates) > 1:
                raise dbus.exceptions.DBusException('Ambiguous physical desktop session')
            self.sleeping = bool(self.get_properties(ROOT, MANAGER)['PreparingForSleep'])
            self.path, self.properties = candidates[0] if candidates else (None, {})
            self.ready = True
        except (dbus.exceptions.DBusException, IndexError, KeyError, TypeError, ValueError):
            self.fail()
            return
        if before and (not self.can_capture or previous != self.path):
            self.blocked()

    def changed(self, interface, changed, invalidated, *, path):
        before = self.can_capture
        if interface == MANAGER and path == ROOT:
            if 'PreparingForSleep' in invalidated:
                self.refresh()
                return
            if 'PreparingForSleep' in changed:
                self.sleeping = bool(changed['PreparingForSleep'])
        elif interface == SESSION:
            if path != self.path:
                if changed.get('Active'):
                    self.refresh()
                return
            identity = ('User', 'Type', 'Class', 'Remote', 'Display')
            if (any(key in invalidated for key in ('Active', 'LockedHint') + identity)
                    or any(key in changed for key in identity)):
                self.refresh()
                return
            self.properties.update(changed)
        if before and not self.can_capture:
            self.blocked()

    def sessions_changed(self, _id, _path):
        self.refresh()

    def prepare_sleep(self, sleeping):
        before = self.can_capture
        self.sleeping = bool(sleeping)
        if before and not self.can_capture:
            self.blocked()

    def owner_changed(self, _name, old, new):
        if old and old != new:
            self.fail()
        if new:
            self.refresh()

    def fail(self):
        before = self.can_capture
        self.ready = False
        if before:
            self.blocked()

    def close(self):
        self.fail()
        for match in self.matches:
            match.remove()
        self.matches.clear()
        if self.owns_bus and self.bus is not None:
            self.bus.close()
