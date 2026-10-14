#!/usr/bin/python3
"""Pleb's supervised physical X11 ScreenSaver service.

The session starts this after its xss-lock coordinator and before applications.
Holds belong to a D-Bus unique sender, and XSS suspension belongs to this one
X connection: even SIGKILL releases it. Explicit locks use the session's
pleb-lock/logind coordinator, independently of idle inhibition.
"""
import argparse
import ctypes
import os
import re
import secrets
import select
import signal
import subprocess
import sys
import threading
import time

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

NAME = IFACE = "org.freedesktop.ScreenSaver"
PATHS = ("/ScreenSaver", "/org/freedesktop/ScreenSaver")
READY_IFACE = "org.pleb.ScreenSaver1"
MAX_HOLDS = 1024
MAX_CALLERS = 128
MAX_PER_CALLER = 64
MAX_LOCK_WAITERS = 64
MAX_LOCK_PER_CALLER = 8
LOCK_TIMEOUT = 3.0
X_TIMEOUT = 2.0
STARTUP_TIMEOUT = 5.0
AUTHORITY_TIMEOUT = .25


class StartupDeadline:
    """A kernel-backed deadline even when synchronous D-Bus auth holds the GIL.

    Fork before opening X or starting the X watchdog thread. The small child
    waits only on the exact parent pidfd and a completion pipe; it exits when
    startup finishes or its parent dies. A timeout kills only that parent.
    """
    def __enter__(self):
        read_fd, self.write_fd = os.pipe2(os.O_CLOEXEC)
        parent_fd = os.pidfd_open(os.getpid())
        try:
            self.child = os.fork()
        except BaseException:
            os.close(read_fd)
            os.close(self.write_fd)
            os.close(parent_fd)
            raise
        if self.child == 0:
            os.close(self.write_fd)
            try:
                ready, _, _ = select.select([read_fd, parent_fd], [], [], STARTUP_TIMEOUT)
                if not ready:
                    os.write(2, b"pleb-screensaver: startup timed out\n")
                    try:
                        signal.pidfd_send_signal(parent_fd, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            finally:
                os._exit(0)
        os.close(read_fd)
        os.close(parent_fd)
        return self

    def __exit__(self, kind, value, traceback):
        os.close(self.write_fd)
        os.waitpid(self.child, 0)


class InvalidCookie(dbus.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class LimitExceeded(dbus.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.LimitsExceeded"


class Unavailable(dbus.DBusException):
    _dbus_error_name = "org.freedesktop.ScreenSaver.Error.Unavailable"


def display_identity(value):
    match = re.fullmatch(r":([0-9]+)(?:\.([0-9]+))?", str(value))
    return (int(match[1]), int(match[2] or "0")) if match else None


def lock_current_context(env):
    """Return False only for an identified other desktop's system delegation."""
    desktop = env.get("XDG_CURRENT_DESKTOP") or env.get("XDG_SESSION_DESKTOP", "")
    other_desktop = bool(desktop) and "pleb" not in desktop.lower().split(":")
    with StartupDeadline():
        bus = dbus.bus.BusConnection(env["DBUS_SESSION_BUS_ADDRESS"])
        try:
            def current_owner():
                return str(bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "GetNameOwner", "s", (NAME,), timeout=AUTHORITY_TIMEOUT))
            try:
                owner = current_owner()
                context = bus.call_blocking(owner, PATHS[0], READY_IFACE, "GetContext", "", (), timeout=1.5)
            except dbus.DBusException as exc:
                if other_desktop and exc.get_dbus_name() in (
                        "org.freedesktop.DBus.Error.NameHasNoOwner", "org.freedesktop.DBus.Error.UnknownMethod",
                        "org.freedesktop.DBus.Error.UnknownInterface"):
                    return False
                raise
            if len(context) != 4 or any(not str(value) for value in context):
                raise Unavailable("invalid Pleb service context")
            generation, session_id, physical_display, route_display = map(str, context)
            if other_desktop or env.get("PLEB_DESKTOP_SESSION_ID") != session_id \
                    or (env.get("XDG_SESSION_ID") and env["XDG_SESSION_ID"] != session_id) \
                    or display_identity(physical_display) is None \
                    or display_identity(env.get("PLEB_DESKTOP_DISPLAY", "")) != display_identity(physical_display) \
                    or display_identity(route_display) is None \
                    or display_identity(env.get("DISPLAY", "")) != display_identity(route_display):
                raise Unavailable("the live Pleb service does not match this application's session/display")
            if current_owner() != owner:
                raise Unavailable("the ScreenSaver owner changed before locking")
            bus.call_blocking(owner, PATHS[0], READY_IFACE, "LockForContext", "sss",
                              (generation, session_id, physical_display), timeout=4.5)
            return True
        finally:
            bus.close()


class SessionAuthority:
    """Bind the adapter's parent to one live logind login, not imported hints.

    Activated applications do not belong to a logind session themselves. The
    serviced parent does: logind resolves its PID, and its session fingerprint
    must remain unchanged before a context can authorize a lock.
    """
    def __init__(self, display):
        self.parent = os.getppid()
        self.display = display
        self.session_id = os.environ["PLEB_DESKTOP_SESSION_ID"]
        self.bus = dbus.SystemBus(private=True)
        self.bus.set_exit_on_disconnect(False)
        try:
            self.owner = str(self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "GetNameOwner", "s", ("org.freedesktop.login1",)))
            self.path, properties = self.snapshot()
            self.fingerprint = self.identity(properties)
            self.validate(properties, active=False)
        except BaseException:
            self.bus.close()
            raise

    def call(self, destination, path, interface, method, signature="", arguments=()):
        return self.bus.call_blocking(destination, path, interface, method,
                                      signature, arguments, timeout=AUTHORITY_TIMEOUT)

    def snapshot(self):
        path = str(self.call(self.owner, "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                            "GetSessionByPID", "u", (dbus.UInt32(self.parent),)))
        properties = self.call(self.owner, path, "org.freedesktop.DBus.Properties", "GetAll",
                               "s", ("org.freedesktop.login1.Session",))
        return path, properties

    @staticmethod
    def identity(properties):
        return (str(properties["Id"]), int(properties["User"][0]),
                int(properties["Leader"]), int(properties["TimestampMonotonic"]),
                str(properties["Display"]), str(properties["Type"]))

    def validate(self, properties, *, active):
        if str(properties["Id"]) != self.session_id or int(properties["User"][0]) != os.getuid() \
                or bool(properties["Remote"]) or str(properties["State"]) == "closing":
            raise Unavailable("the serviced login session is no longer current")
        session_display = str(properties["Display"])
        if display_identity(self.display) is None or (display_identity(session_display) != display_identity(self.display) \
                and not (not session_display and str(properties["Type"]) == "tty")):
            raise Unavailable("logind does not identify the physical Pleb display")
        if active and not bool(properties["Active"]):
            raise Unavailable("the serviced Pleb login session is inactive")

    def verify(self, *, active):
        try:
            owner = str(self.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                  "GetNameOwner", "s", ("org.freedesktop.login1",)))
            if owner != self.owner:
                raise Unavailable("logind ownership changed")
            path, properties = self.snapshot()
            if path != self.path or self.identity(properties) != self.fingerprint:
                raise Unavailable("the serviced login session identity changed")
            self.validate(properties, active=active)
        except (dbus.DBusException, KeyError, TypeError, ValueError) as exc:
            raise Unavailable("cannot verify the live Pleb login session") from exc

    def close(self):
        self.bus.close()


class XSuspension:
    """One client-owned XSS hold, with a watchdog for a stopped X server.

    Xlib's synchronous operations can otherwise block the D-Bus loop forever.
    On a timeout the process exits, closing its X socket and releasing all
    client-owned XSS holds. The session supervisor then ends the desktop.
    """
    def __init__(self, display):
        self.x11 = ctypes.CDLL("libX11.so.6")
        self.xss = ctypes.CDLL("libXss.so.1")
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.x11.XConnectionNumber.argtypes = [ctypes.c_void_p]
        self.x11.XConnectionNumber.restype = ctypes.c_int
        self.x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.xss.XScreenSaverQueryExtension.argtypes = [ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        self.xss.XScreenSaverQueryVersion.argtypes = [ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        self.xss.XScreenSaverSuspend.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.condition = threading.Condition()
        self.deadline = None
        self.closed = False
        self.display = None
        self.active = False
        threading.Thread(target=self._watchdog, daemon=True).start()
        try:
            self.display = self._call(self.x11.XOpenDisplay, display.encode())
            if not self.display:
                raise RuntimeError("cannot open the physical X11 display")
            major, minor = ctypes.c_int(), ctypes.c_int()
            if not self._call(self.xss.XScreenSaverQueryExtension, self.display,
                              ctypes.byref(major), ctypes.byref(minor)):
                raise RuntimeError("the physical display has no XScreenSaver extension")
            if not self._call(self.xss.XScreenSaverQueryVersion, self.display,
                              ctypes.byref(major), ctypes.byref(minor)) or (major.value, minor.value) < (1, 1):
                raise RuntimeError("XScreenSaver 1.1 or newer is required")
            self.fd = self.x11.XConnectionNumber(self.display)
        except BaseException:
            self.close()
            raise

    def _watchdog(self):
        with self.condition:
            while not self.closed:
                if self.deadline is None:
                    self.condition.wait()
                elif self.deadline > time.monotonic():
                    self.condition.wait(self.deadline - time.monotonic())
                else:
                    os.write(2, b"pleb-screensaver: physical X11 request timed out\n")
                    os._exit(1)

    def _call(self, fn, *args):
        with self.condition:
            self.deadline = time.monotonic() + X_TIMEOUT
            self.condition.notify()
        try:
            return fn(*args)
        finally:
            with self.condition:
                self.deadline = None
                self.condition.notify()

    def set(self, active):
        if active != self.active:
            self._call(self.xss.XScreenSaverSuspend, self.display, int(active))
            self._call(self.x11.XSync, self.display, 0)
            self.active = active

    def close(self):
        if self.display:
            self.set(False)
            self._call(self.x11.XCloseDisplay, self.display)
            self.display = None
        with self.condition:
            self.closed = True
            self.condition.notify()


class Service(dbus.service.Object):
    SUPPORTS_MULTIPLE_OBJECT_PATHS = True

    def __init__(self, bus, suspension, lock_command, loop, authority):
        self.bus = bus
        self.suspension = suspension
        self.lock_command = lock_command
        self.loop = loop
        self.authority = authority
        self.generation = secrets.token_hex(16)
        self.stopping = False
        self.holds = {}  # cookie -> unique sender; no application text is retained
        self.callers = {}
        self.next_cookie = secrets.randbelow(0xffffffff) + 1
        self.issued = 0
        self.lock_process = None
        self.lock_pidfd = None
        self.lock_deadline = None
        self.lock_waiters = []
        self.lock_timed_out = False
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.owner = str(bus.get_unique_name())
        super().__init__(bus, PATHS[0])
        self.add_to_connection(bus, PATHS[1])
        bus.add_signal_receiver(self.owner_changed, signal_name="NameOwnerChanged",
            dbus_interface="org.freedesktop.DBus", bus_name="org.freedesktop.DBus",
            path="/org/freedesktop/DBus")
        bus.call_on_disconnection(lambda connection: self.stop("session bus disconnected"))
        bus.set_exit_on_disconnect(False)
        self.x_watch = GLib.io_add_watch(suspension.fd, GLib.IO_HUP | GLib.IO_ERR,
                                         self.x_lost)
        # Own the public name only after every resource and both objects exist.
        if bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                             "RequestName", "su", (NAME, dbus.UInt32(dbus.bus.NAME_FLAG_DO_NOT_QUEUE)), timeout=.5) \
                != dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER:
            raise RuntimeError("another desktop already owns org.freedesktop.ScreenSaver")

    def x_lost(self, source, condition):
        self.stop("physical display disconnected")
        return False

    def stop(self, reason=None):
        if not self.stopping:
            self.stopping = True
            if reason:
                print("pleb-screensaver: " + reason, file=sys.stderr)
            self.loop.quit()
        return GLib.SOURCE_REMOVE

    def owner_changed(self, name, old, new):
        name = str(name)
        if name == NAME and str(old) == self.owner and str(new) != self.owner:
            self.stop("ScreenSaver name ownership lost")
        elif name.startswith(":") and not new:
            for cookie in self.callers.pop(name, ()):
                self.holds.pop(cookie, None)
            self.suspension.set(bool(self.holds))
            self.lock_waiters = [row for row in self.lock_waiters if row[0] != name]

    def require_caller(self, sender):
        if self.stopping:
            raise Unavailable("the desktop service is stopping")
        # A queued Inhibit may reach us after its caller has already died.
        if not sender or not str(sender).startswith(":") or not self.bus.call_blocking(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", "s", (sender,), timeout=.5):
            raise Unavailable("the caller has disconnected")
        return str(sender)

    @dbus.service.method(IFACE, in_signature="ss", out_signature="u", sender_keyword="sender",
                         message_keyword="message")
    def Inhibit(self, application, reason, sender=None, message=None):
        if message is not None and message.get_no_reply():
            raise InvalidCookie("Inhibit requires a reply to deliver its cookie")
        sender = self.require_caller(sender)
        if len(application) > 512 or len(reason) > 512:
            raise LimitExceeded("inhibitor descriptions must be at most 512 characters")
        if len(self.holds) >= MAX_HOLDS or len(self.callers.get(sender, ())) >= MAX_PER_CALLER \
                or (sender not in self.callers and len(self.callers) >= MAX_CALLERS):
            raise LimitExceeded("too many screensaver inhibitors")
        # Never reuse a released cookie during this service lifetime.
        if self.issued >= 0xffffffff:
            raise LimitExceeded("screensaver cookie space exhausted")
        cookie = self.next_cookie
        self.next_cookie = cookie % 0xffffffff + 1
        self.issued += 1
        self.suspension.set(True)
        self.holds[cookie] = sender
        self.callers.setdefault(sender, set()).add(cookie)
        return dbus.UInt32(cookie)

    @dbus.service.method(IFACE, in_signature="u", out_signature="", sender_keyword="sender")
    def UnInhibit(self, cookie, sender=None):
        sender = self.require_caller(sender)
        cookie = int(cookie)
        if self.holds.get(cookie) != sender:
            raise InvalidCookie("the cookie does not belong to this caller")
        self.holds.pop(cookie)
        self.callers[sender].remove(cookie)
        if not self.callers[sender]:
            self.callers.pop(sender)
        self.suspension.set(bool(self.holds))

    @dbus.service.method(READY_IFACE, in_signature="", out_signature="u")
    def Ready(self):
        return dbus.UInt32(os.getpid())

    def require_current_context(self, sender):
        sender = self.require_caller(sender)
        owner = str(self.bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetNameOwner", "s", (NAME,), timeout=AUTHORITY_TIMEOUT))
        if owner != self.owner:
            raise Unavailable("the ScreenSaver owner changed")
        self.authority.verify(active=True)
        return sender

    @dbus.service.method(READY_IFACE, in_signature="", out_signature="ssss", sender_keyword="sender")
    def GetContext(self, sender=None):
        self.require_current_context(sender)
        return (self.generation, self.authority.session_id, self.authority.display, self.authority.display)

    @dbus.service.method(READY_IFACE, in_signature="sss", out_signature="", sender_keyword="sender",
                         async_callbacks=("reply", "error"))
    def LockForContext(self, generation, session_id, display, sender=None, reply=None, error=None):
        sender = self.require_current_context(sender)
        if (str(generation), str(session_id), str(display)) != \
                (self.generation, self.authority.session_id, self.authority.display):
            raise Unavailable("the Pleb lock context is stale")
        self.queue_lock(sender, reply, error)

    @dbus.service.method(IFACE, in_signature="", out_signature="", sender_keyword="sender",
                         async_callbacks=("reply", "error"))
    def Lock(self, sender=None, reply=None, error=None):
        sender = self.require_caller(sender)
        self.authority.verify(active=False)
        self.queue_lock(sender, reply, error)

    def queue_lock(self, sender, reply, error):
        if len(self.lock_waiters) >= MAX_LOCK_WAITERS or sum(row[0] == sender for row in self.lock_waiters) >= MAX_LOCK_PER_CALLER:
            raise LimitExceeded("too many pending lock requests")
        if self.lock_process is None:
            parent = os.getpid()
            libc = self.libc
            def child_setup():
                # A killed adapter must not leave a wedged loginctl behind.
                if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
                    os._exit(1)
            try:
                self.lock_process = subprocess.Popen([self.lock_command], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, close_fds=True, preexec_fn=child_setup)
                self.lock_pidfd = os.pidfd_open(self.lock_process.pid)
            except OSError:
                if self.lock_process is not None:
                    self.lock_process.kill()
                    self.lock_process.wait(timeout=.5)
                    self.lock_process = None
                raise Unavailable("cannot start the authenticated lock coordinator")
            self.lock_timed_out = False
            self.lock_deadline = time.monotonic() + LOCK_TIMEOUT
            GLib.timeout_add(25, self.poll_lock)
        self.lock_waiters.append((sender, reply, error))

    def poll_lock(self):
        if self.stopping or self.lock_process is None:
            return GLib.SOURCE_REMOVE
        rc = self.lock_process.poll()
        if rc is None and time.monotonic() >= self.lock_deadline:
            self.lock_timed_out = True
            try:
                signal.pidfd_send_signal(self.lock_pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Report uncertainty promptly; keep the single child slot occupied
            # until it is reaped, so a stuck kernel task cannot cause a fork storm.
            waiters, self.lock_waiters = self.lock_waiters, []
            for sender, reply, error in waiters:
                error(Unavailable("the lock coordinator timed out"))
            return GLib.SOURCE_CONTINUE
        if rc is None:
            return GLib.SOURCE_CONTINUE
        os.close(self.lock_pidfd)
        self.lock_pidfd = None
        self.lock_process = None
        waiters, self.lock_waiters = self.lock_waiters, []
        for sender, reply, error in waiters:
            if rc == 0 and not self.lock_timed_out:
                reply()
            else:
                error(Unavailable("the lock coordinator failed"))
        return GLib.SOURCE_REMOVE

    def close(self):
        self.stopping = True
        self.holds.clear()
        self.callers.clear()
        self.lock_waiters.clear()
        if self.lock_pidfd is not None:
            try:
                signal.pidfd_send_signal(self.lock_pidfd, signal.SIGKILL)
                self.lock_process.wait(timeout=.5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            finally:
                os.close(self.lock_pidfd)
                self.lock_pidfd = None
        self.suspension.close()
        self.bus.close()


def wait_ready(pid):
    with StartupDeadline():
        deadline = time.monotonic() + STARTUP_TIMEOUT
        bus = dbus.bus.BusConnection(os.environ["DBUS_SESSION_BUS_ADDRESS"])
        def daemon(method, argument):
            return bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                     method, "s", (argument,), timeout=.2)
        try:
            while time.monotonic() < deadline:
                try:
                    owner = str(daemon("GetNameOwner", NAME))
                    if int(daemon("GetConnectionUnixProcessID", owner)) != pid:
                        raise RuntimeError("the ScreenSaver name belongs to another process")
                    if int(bus.call_blocking(owner, PATHS[0], READY_IFACE, "Ready", "", (), timeout=.2)) == pid \
                            and str(daemon("GetNameOwner", NAME)) == owner:
                        return
                except dbus.DBusException:
                    pass
                time.sleep(.05)
            raise RuntimeError("the ScreenSaver service did not become ready")
        finally:
            bus.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-command")
    parser.add_argument("--wait-ready", type=int)
    args = parser.parse_args()
    if args.wait_ready is not None:
        wait_ready(args.wait_ready)
        return
    # The adapter is a session child, including during X/bus startup before
    # GLib can watch anything. Abrupt parent death must close its X connection
    # and kill a pending lock helper rather than leave an orphaned inhibitor.
    parent = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    if parent <= 1 or libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        raise RuntimeError("cannot bind the ScreenSaver service to its session parent")
    if not args.lock_command or not os.path.isabs(args.lock_command) or not os.access(args.lock_command, os.X_OK):
        raise RuntimeError("an installed authenticated lock command is required")
    if not os.environ.get("PLEB_DESKTOP_SESSION_ID") or os.environ["PLEB_DESKTOP_SESSION_ID"] != os.environ.get("XDG_SESSION_ID"):
        raise RuntimeError("a serviced Pleb login session is required")
    display = os.environ.get("PLEB_DESKTOP_DISPLAY")
    if not display or display != os.environ.get("DISPLAY"):
        raise RuntimeError("the physical Pleb display is required")
    DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()
    suspension = None
    authority = None
    bus = None
    service = None
    try:
        with StartupDeadline():
            bus = dbus.bus.BusConnection(os.environ["DBUS_SESSION_BUS_ADDRESS"])
            authority = SessionAuthority(display)
            suspension = XSuspension(display)
            service = Service(bus, suspension, args.lock_command, loop, authority)
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, service.stop)
        loop.run()
    finally:
        if service is not None:
            service.close()
        else:
            if suspension is not None:
                suspension.close()
            if bus is not None:
                bus.close()
        if authority is not None:
            authority.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, dbus.DBusException) as exc:
        print("pleb-screensaver: " + str(exc), file=sys.stderr)
        sys.exit(1)
