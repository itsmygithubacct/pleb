"""Actual private buses, independent clients, owned Xvfb and installed staging.

No workstation session/display is consulted. Run under validation/run.py so
the entire process tree also has scratch HOME/XDG and the execution boundary.
"""
import ctypes
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest

import dbus
import dbus.lowlevel

ROOT = Path(__file__).resolve().parents[1]
NAME = "org.freedesktop.ScreenSaver"
PATHS = ("/ScreenSaver", "/org/freedesktop/ScreenSaver")
MODULE = ROOT / "lib/screensaver.py"


def install_to(target, source=ROOT, fail=""):
    script = '''set -euo pipefail
source "$PLEB_ROOT/lib/common.sh"
source "$PLEB_ROOT/lib/install.sh"
run_root() {
 local -a args=("$@")
 local i
 for ((i=1;i<${#args[@]};i++)); do
  case "${args[$i]}" in /usr/local/*) args[$i]="$FIXTURE_ROOT${args[$i]}";; esac
 done
 if [ "$1" = install ] && [[ "${args[-1]}" = *"$FAIL_STAGE" ]] && [ -n "$FAIL_STAGE" ]; then return 1; fi
 "${args[@]}"
}
install_screensaver
'''
    env = dict(os.environ, PLEB_ROOT=str(source), FIXTURE_ROOT=str(target), FAIL_STAGE=fail)
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=10)


class XProbe:
    class Info(ctypes.Structure):
        _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int),
                    ("kind", ctypes.c_int), ("til", ctypes.c_ulong),
                    ("idle", ctypes.c_ulong), ("mask", ctypes.c_ulong)]

    def __init__(self, display):
        self.x = ctypes.CDLL("libX11.so.6")
        self.s = ctypes.CDLL("libXss.so.1")
        self.x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x.XOpenDisplay.restype = ctypes.c_void_p
        self.x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.x.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x.XSetScreenSaver.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.x.XResetScreenSaver.argtypes = [ctypes.c_void_p]
        self.x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.s.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(self.Info)]
        self.d = self.x.XOpenDisplay(display.encode())
        if not self.d:
            raise RuntimeError("cannot open owned Xvfb")
        self.root = self.x.XDefaultRootWindow(self.d)
        self.x.XSetScreenSaver(self.d, 1, 0, 1, 0)
        self.reset()

    def reset(self):
        self.x.XResetScreenSaver(self.d)
        self.x.XSync(self.d, 0)

    def state(self):
        info = self.Info()
        if not self.s.XScreenSaverQueryInfo(self.d, self.root, ctypes.byref(info)):
            raise RuntimeError("XSS query failed")
        return info.state

    def close(self):
        if self.d:
            self.x.XCloseDisplay(self.d)
            self.d = None


LOGIND = '''import dbus,dbus.service,json,os,sys
from pathlib import Path
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
DBusGMainLoop(set_as_default=True)
bus=dbus.bus.BusConnection(os.environ['DBUS_SYSTEM_BUS_ADDRESS'])
loop=GLib.MainLoop()
def session_properties():
 changes=json.loads(Path(os.environ['SESSION_STATE']).read_text())
 values={'Id':os.environ['PLEB_DESKTOP_SESSION_ID'],'User':(dbus.UInt32(os.getuid()),dbus.ObjectPath('/org/freedesktop/login1/user/self')),
         'Leader':dbus.UInt32(os.getppid()),'TimestampMonotonic':dbus.UInt64(123456),
         'Display':os.environ['DISPLAY'],'Type':'x11','Remote':dbus.Boolean(False),'Active':dbus.Boolean(True),'State':'active'}
 values.update(changes)
 return values
class Session(dbus.service.Object):
 @dbus.service.method('org.freedesktop.DBus.Properties',in_signature='s',out_signature='a{sv}')
 def GetAll(self,interface):
  if interface!='org.freedesktop.login1.Session':raise dbus.DBusException('unknown interface')
  return session_properties()
class Manager(dbus.service.Object):
 @dbus.service.method('org.freedesktop.login1.Manager',in_signature='u',out_signature='o')
 def GetSessionByPID(self,pid):
  if json.loads(Path(os.environ['SESSION_STATE']).read_text()).get('Missing'):
   raise dbus.DBusException('session ended',name='org.freedesktop.login1.NoSessionForPID')
  if not Path('/proc',str(int(pid))).exists():raise dbus.DBusException('parent not alive')
  with open(os.environ['SESSION_QUERIES'],'a') as f:f.write(json.dumps({'pid':int(pid)})+'\\n')
  return dbus.ObjectPath('/org/freedesktop/login1/session/current')
 @dbus.service.method('org.freedesktop.login1.Manager',in_signature='s',out_signature='',sender_keyword='sender',async_callbacks=('reply','error'))
 def LockSession(self,session,sender=None,reply=None,error=None):
  daemon=dbus.Interface(bus.get_object('org.freedesktop.DBus','/org/freedesktop/DBus'),'org.freedesktop.DBus')
  pid=int(daemon.GetConnectionUnixProcessID(sender))
  env=dict(x.split('=',1) for x in Path('/proc',str(pid),'environ').read_bytes().decode().split('\\0') if '=' in x)
  with open(os.environ['LOCK_RECORD'],'a') as f:
   f.write(json.dumps({'session':str(session),'pid':pid,'display':env.get('DISPLAY'),'authority':env.get('XAUTHORITY')})+'\\n')
  mode=Path(os.environ['LOCK_MODE']).read_text()
  if mode=='drop': return
  if mode=='fail': error(dbus.DBusException('fixture lock refused',name='org.freedesktop.login1.Error.Failed'))
  else: reply()
obj=Manager(bus,'/org/freedesktop/login1')
session=Session(bus,'/org/freedesktop/login1/session/current')
bus.request_name('org.freedesktop.login1',dbus.bus.NAME_FLAG_DO_NOT_QUEUE)
Path(os.environ['LOGIND_READY']).write_text('ready')
loop.run()
'''

CLIENT = '''import dbus,os,sys
bus=dbus.bus.BusConnection(os.environ['DBUS_SESSION_BUS_ADDRESS'])
obj=dbus.Interface(bus.get_object('org.freedesktop.ScreenSaver','/ScreenSaver'),'org.freedesktop.ScreenSaver')
if sys.argv[1]=='hold':
 cookie=obj.Inhibit('fixture','playing',timeout=3)
 print(int(cookie),flush=True)
 sys.stdin.read()
elif sys.argv[1]=='lock':
 obj.Lock(timeout=6)
else:
 raise SystemExit(2)
'''


class Screensaver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ss-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.procs = []
        self.external_pidfds = []
        self.clients = []
        self.probe = None
        self.addCleanup(self.cleanup)
        self.env = dict(os.environ, HOME=str(self.root / "home"),
                        XDG_SESSION_ID="fixture", PLEB_DESKTOP_SESSION_ID="fixture",
                        PLEB_SCREENSAVER_SERVICE="1", PLEB_AUTO_LOCK="off",
                        PLEB_IDLE_LOCK_SECONDS="0", XAUTHORITY=str(self.root / "authority"))
        for key, suffix in (("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"),
                            ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"),
                            ("XDG_RUNTIME_DIR", "run")):
            self.env[key] = str(self.root / suffix)
            (self.root / suffix).mkdir(mode=0o700)
        (self.root / "home").mkdir()
        self.env["DBUS_SESSION_BUS_ADDRESS"] = self.start_bus()
        self.env["DBUS_SYSTEM_BUS_ADDRESS"] = self.start_bus()
        fd, output = os.pipe()
        try:
            self.xvfb = self.spawn(["Xvfb", "-displayfd", str(output), "-screen", "0", "400x300x24",
                                    "-nolisten", "tcp", "-ac"], pass_fds=(output,))
        finally:
            os.close(output)
        try:
            self.assertTrue(select.select([fd], [], [], 5)[0], "owned Xvfb did not start")
            display = ":" + os.read(fd, 32).decode().strip()
        finally:
            os.close(fd)
        self.env.update(DISPLAY=display, PLEB_DESKTOP_DISPLAY=display,
                        PLEB_DESKTOP_XAUTHORITY=self.env["XAUTHORITY"])
        self.probe = XProbe(display)
        self.env.update(LOCK_RECORD=str(self.root / "lock.jsonl"), LOCK_MODE=str(self.root / "mode"),
                        LOGIND_READY=str(self.root / "logind-ready"), SESSION_STATE=str(self.root / "session-state.json"),
                        SESSION_QUERIES=str(self.root / "session-queries.jsonl"))
        (self.root / "mode").write_text("ok")
        (self.root / "session-state.json").write_text("{}")
        self.logind = self.spawn(["/usr/bin/python3", "-c", LOGIND])
        self.until(lambda: (self.root / "logind-ready").exists())
        # pleb-lock checks i3lock's availability before choosing loginctl. It
        # must never execute this stand-in for a normal serviced-session lock.
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        fake = fake_bin / "i3lock"
        fake.write_text("#!/bin/sh\nexit 97\n")
        fake.chmod(0o700)
        self.env["PATH"] = str(fake_bin) + ":" + self.env["PATH"]
        installed = install_to(self.root)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.module = self.root / "usr/local/lib/pleb/screensaver.py"
        self.shim = self.root / "usr/local/lib/pleb/session-bin/xdg-screensaver"
        self.locker = self.root / "usr/local/bin/pleb-lock"
        self.locker.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "bin/pleb-lock", self.locker)
        self.locker.chmod(0o755)
        self.start_service()

    def spawn(self, args, **kwargs):
        proc = subprocess.Popen(args, env=self.env, stdin=kwargs.pop("stdin", subprocess.DEVNULL),
            stdout=kwargs.pop("stdout", subprocess.DEVNULL), stderr=kwargs.pop("stderr", subprocess.DEVNULL), **kwargs)
        self.procs.append((proc, os.pidfd_open(proc.pid)))
        return proc

    def start_bus(self):
        proc = self.spawn(["dbus-daemon", "--session", "--nofork", "--print-address"], stdout=subprocess.PIPE)
        self.assertTrue(select.select([proc.stdout], [], [], 5)[0], "private bus did not start")
        return proc.stdout.readline().decode().strip()

    def start_service(self, module=None):
        module = module or self.module
        self.service_log = open(self.root / "service.log", "a")
        self.addCleanup(self.service_log.close)
        self.service = self.spawn(["/usr/bin/python3", str(module), "--lock-command", str(self.locker)],
                                  stderr=self.service_log)
        ready = subprocess.run(["/usr/bin/python3", str(module), "--wait-ready", str(self.service.pid)],
                               env=self.env, capture_output=True, text=True, timeout=7)
        self.assertEqual(ready.returncode, 0, ready.stderr + (self.root / "service.log").read_text())

    def client(self, path=PATHS[0]):
        bus = dbus.bus.BusConnection(self.env["DBUS_SESSION_BUS_ADDRESS"])
        self.clients.append(bus)
        return bus, dbus.Interface(bus.get_object(NAME, path), NAME)

    def until(self, fn, seconds=4):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if fn():
                return
            time.sleep(.02)
        self.fail("condition timed out")

    def hold(self, obj):
        return obj.Inhibit("fixture", "playing", timeout=3)

    def assert_held(self):
        self.probe.reset()
        time.sleep(1.3)
        self.assertNotEqual(self.probe.state(), 1, "physical screensaver activated during the hold")

    def assert_resumed(self):
        self.until(lambda: self.probe.state() == 1)

    def stop(self, proc, sig=signal.SIGTERM):
        fd = next(fd for p, fd in self.procs if p is proc)
        signal.pidfd_send_signal(fd, sig)
        proc.wait(timeout=5)

    def cleanup(self):
        for bus in self.clients:
            bus.close()
        if self.probe:
            self.probe.close()
        for fd in self.external_pidfds:
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(fd)
        for proc, fd in reversed(self.procs):
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
            os.close(fd)
            if proc.stdout:
                proc.stdout.close()
            if proc.stdin:
                proc.stdin.close()

    def test_both_paths_share_sender_cookies_and_real_physical_suspend(self):
        bus, first = self.client()
        same = dbus.Interface(bus.get_object(NAME, PATHS[1]), NAME)
        cookie = self.hold(first)
        self.assert_held()
        same.UnInhibit(cookie, timeout=3)
        self.assert_resumed()

    def test_cross_caller_unknown_and_repeated_cookie_cannot_release(self):
        a, obj = self.client()
        b, other = self.client(PATHS[1])
        cookie = self.hold(obj)
        for wrong in (0, int(cookie) % 0xffffffff + 1, cookie):
            with self.assertRaises(dbus.DBusException) as caught:
                other.UnInhibit(dbus.UInt32(wrong), timeout=3)
            self.assertEqual(caught.exception.get_dbus_name(), "org.freedesktop.DBus.Error.InvalidArgs")
        self.assert_held()
        obj.UnInhibit(cookie, timeout=3)
        with self.assertRaises(dbus.DBusException):
            obj.UnInhibit(cookie, timeout=3)
        self.assert_resumed()

    def test_aggregation_disconnect_and_stale_cookie_after_restart(self):
        a, obj = self.client()
        b, other = self.client()
        old = self.hold(obj)
        second = self.hold(other)
        a.close()
        self.assert_held()
        other.UnInhibit(second, timeout=3)
        self.assert_resumed()
        stale = self.hold(other)
        self.stop(self.service, signal.SIGKILL)
        self.assert_resumed()
        self.start_service()
        other = dbus.Interface(b.get_object(NAME, PATHS[0]), NAME)
        new = self.hold(other)
        with self.assertRaises(dbus.DBusException):
            other.UnInhibit(stale, timeout=3)
        other.UnInhibit(new, timeout=3)
        self.assert_resumed()

    def test_app_hard_death_releases_its_actual_hold(self):
        app = self.spawn(["/usr/bin/python3", "-c", CLIENT, "hold"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.assertTrue(select.select([app.stdout], [], [], 5)[0])
        self.assertTrue(int(app.stdout.readline()))
        self.assert_held()
        self.stop(app, signal.SIGKILL)
        self.assert_resumed()

    def test_no_reply_hold_never_allocates_an_inaccessible_cookie(self):
        bus, obj = self.client()
        msg = dbus.lowlevel.MethodCallMessage(NAME, PATHS[0], NAME, "Inhibit")
        msg.append("fixture", "no reply", signature="ss")
        msg.set_no_reply(True)
        bus.send_message(msg)
        self.probe.reset()
        self.assert_resumed()
        # A live caller can still allocate its entire quota: the unacknowledged
        # request consumed neither a cookie slot nor a physical suspension.
        cookies = [self.hold(obj) for _ in range(64)]
        for cookie in cookies:
            obj.UnInhibit(cookie, timeout=3)

    def test_sender_limit_many_calls_and_released_cookie_never_reused(self):
        bus, obj = self.client()
        issued = [self.hold(obj) for _ in range(64)]
        with self.assertRaises(dbus.DBusException) as caught:
            self.hold(obj)
        self.assertEqual(caught.exception.get_dbus_name(), "org.freedesktop.DBus.Error.LimitsExceeded")
        for cookie in issued:
            obj.UnInhibit(cookie, timeout=3)
        for _ in range(100):
            cookie = self.hold(obj)
            self.assertNotIn(cookie, issued)
            issued.append(cookie)
            obj.UnInhibit(cookie, timeout=3)
        self.assertEqual(len(issued), len(set(issued)))
        self.assert_resumed()

    def test_malformed_and_large_descriptions_are_rejected_without_hold(self):
        bus, obj = self.client()
        with self.assertRaises(dbus.DBusException):
            obj.Inhibit("x" * 513, "reason", timeout=3)
        msg = dbus.lowlevel.MethodCallMessage(NAME, PATHS[0], NAME, "Inhibit")
        msg.append(dbus.UInt32(1), signature="u")
        with self.assertRaises(dbus.DBusException):
            bus.send_message_with_reply_and_block(msg, 3)
        self.assert_resumed()

    def test_lock_while_inhibited_uses_actual_loginctl_on_private_system_bus(self):
        bus, obj = self.client()
        cookie = self.hold(obj)
        for path in PATHS:
            dbus.Interface(bus.get_object(NAME, path), NAME).Lock(timeout=5)
        rows = [json.loads(line) for line in (self.root / "lock.jsonl").read_text().splitlines()]
        self.assertEqual([r["session"] for r in rows], ["fixture", "fixture"])
        self.assertTrue(all(r["display"] == self.env["DISPLAY"] and r["authority"] == self.env["XAUTHORITY"] for r in rows))
        self.assert_held()
        obj.UnInhibit(cookie, timeout=3)
        self.assert_resumed()

    def test_xdg_exact_lock_routes_and_nonpleb_and_other_arguments_delegate(self):
        cmd = [str(self.shim)]
        r = subprocess.run(cmd + ["lock"], env=self.env, capture_output=True, text=True, timeout=6)
        self.assertEqual(r.returncode, 0, r.stderr)
        count = len((self.root / "lock.jsonl").read_text().splitlines())
        # Actual system-tool safe --version proves argv/env pass-through.
        for env in (self.env, dict(self.env, PLEB_SCREENSAVER_SERVICE="0")):
            want = subprocess.run(["/usr/bin/xdg-screensaver", "--version"], env=env, capture_output=True)
            actual = subprocess.run(cmd + ["--version"], env=env, capture_output=True)
            self.assertEqual((actual.returncode, actual.stdout, actual.stderr), (want.returncode, want.stdout, want.stderr))
        self.assertEqual(len((self.root / "lock.jsonl").read_text().splitlines()), count)

    def test_lock_failure_and_timeout_are_bounded_and_child_is_reaped(self):
        bus, obj = self.client()
        (self.root / "mode").write_text("fail")
        with self.assertRaises(dbus.DBusException):
            obj.Lock(timeout=5)
        (self.root / "mode").write_text("drop")
        before = time.monotonic()
        with self.assertRaises(dbus.DBusException) as caught:
            obj.Lock(timeout=5)
        self.assertLess(time.monotonic() - before, 4)
        self.assertIn("timed out", str(caught.exception))
        row = json.loads((self.root / "lock.jsonl").read_text().splitlines()[-1])
        self.until(lambda: not Path("/proc", str(row["pid"])).exists())
        (self.root / "mode").write_text("ok")
        obj.Lock(timeout=5)

    def test_adapter_hard_death_kills_pending_lock_child(self):
        (self.root / "mode").write_text("drop")
        app = self.spawn(["/usr/bin/python3", "-c", CLIENT, "lock"])
        self.until(lambda: (self.root / "lock.jsonl").exists())
        row = json.loads((self.root / "lock.jsonl").read_text().splitlines()[-1])
        self.external_pidfds.append(os.pidfd_open(row["pid"]))
        self.stop(self.service, signal.SIGKILL)
        # Reparented children can be zombies until init reaps them; they cannot
        # dispatch after death. Assert exit via /proc state or disappearance.
        def dead():
            try:
                return Path("/proc", str(row["pid"]), "stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
            except FileNotFoundError:
                return True
        self.until(dead)
        app.wait(timeout=5)

    def test_bus_loss_and_graceful_shutdown_resume_physical_screensaver(self):
        bus, obj = self.client()
        self.hold(obj)
        self.assert_held()
        self.stop(self.service)
        self.assert_resumed()
        self.start_service()
        obj = dbus.Interface(bus.get_object(NAME, PATHS[0]), NAME)
        self.hold(obj)
        self.stop(self.procs[0][0])  # the exact owned session bus daemon
        self.service.wait(timeout=5)
        self.assert_resumed()

    def test_existing_owner_is_preserved_and_readiness_checks_expected_pid(self):
        bus, obj = self.client()
        owner = str(bus.get_name_owner(NAME))
        other = self.spawn(["/usr/bin/python3", str(self.module), "--lock-command", str(self.locker)])
        self.assertNotEqual(other.wait(timeout=5), 0)
        self.assertEqual(str(bus.get_name_owner(NAME)), owner)
        contender = dbus.bus.BusConnection(self.env["DBUS_SESSION_BUS_ADDRESS"])
        self.clients.append(contender)
        self.assertEqual(contender.request_name(NAME, dbus.bus.NAME_FLAG_REPLACE_EXISTING | dbus.bus.NAME_FLAG_DO_NOT_QUEUE),
                         dbus.bus.REQUEST_NAME_REPLY_EXISTS)
        self.assertEqual(str(bus.get_name_owner(NAME)), owner)
        ready = subprocess.run(["/usr/bin/python3", str(self.module), "--wait-ready", str(other.pid)],
                               env=self.env, capture_output=True, text=True, timeout=7)
        self.assertNotEqual(ready.returncode, 0)
        self.assertIn("another process", ready.stderr)

    def test_physical_x_loss_stops_adapter(self):
        # Close the observer before deliberately stopping its X server; native
        # Xlib would otherwise exit this test runner on a later query/close.
        self.probe.close()
        self.stop(self.xvfb)
        self.assertNotEqual(self.service.wait(timeout=5), 0)

    def test_stopped_x_request_watchdog_exits_and_recovers_owned_x(self):
        bus, obj = self.client()
        fd = next(fd for p, fd in self.procs if p is self.xvfb)
        signal.pidfd_send_signal(fd, signal.SIGSTOP)
        try:
            before = time.monotonic()
            with self.assertRaises(dbus.DBusException):
                self.hold(obj)
            self.assertLess(time.monotonic() - before, 3)
            self.assertNotEqual(self.service.wait(timeout=4), 0)
        finally:
            signal.pidfd_send_signal(fd, signal.SIGCONT)
        self.assert_resumed()

    def test_forged_owner_loss_signal_does_not_drop_another_caller_hold(self):
        a, obj = self.client()
        b, other = self.client()
        self.hold(obj)
        signal_message = dbus.lowlevel.SignalMessage("/org/freedesktop/DBus", "org.freedesktop.DBus", "NameOwnerChanged")
        signal_message.append(a.get_unique_name(), a.get_unique_name(), "", signature="sss")
        b.send_message(signal_message)
        self.assert_held()
        a.close()
        self.assert_resumed()

    def test_global_hold_limit_is_bounded_and_disconnect_releases_every_slot(self):
        rows = [self.client() for _ in range(16)]
        for bus, obj in rows:
            for _ in range(64):
                self.hold(obj)
        bus, extra = self.client()
        with self.assertRaises(dbus.DBusException) as caught:
            self.hold(extra)
        self.assertEqual(caught.exception.get_dbus_name(), "org.freedesktop.DBus.Error.LimitsExceeded")
        for bus, obj in rows:
            bus.close()
        self.assert_resumed()
        cookie = self.hold(extra)
        extra.UnInhibit(cookie, timeout=3)

    def test_lock_waiter_limits_coalesce_many_no_reply_calls_without_fork_storm(self):
        (self.root / "mode").write_text("drop")
        rows = [self.client() for _ in range(8)]
        for bus, obj in rows:
            for _ in range(8):
                msg = dbus.lowlevel.MethodCallMessage(NAME, PATHS[0], NAME, "Lock")
                msg.set_no_reply(True)
                bus.send_message(msg)
        self.until(lambda: (self.root / "lock.jsonl").exists())
        bus, extra = self.client()
        with self.assertRaises(dbus.DBusException) as caught:
            extra.Lock(timeout=5)
        self.assertEqual(caught.exception.get_dbus_name(), "org.freedesktop.DBus.Error.LimitsExceeded")
        self.assertEqual(len((self.root / "lock.jsonl").read_text().splitlines()), 1)
        for bus, obj in rows:
            bus.close()
        time.sleep(3.2)
        (self.root / "mode").write_text("ok")
        extra.Lock(timeout=5)

    def test_per_sender_lock_waiter_limit(self):
        (self.root / "mode").write_text("drop")
        bus, obj = self.client()
        for _ in range(8):
            msg = dbus.lowlevel.MethodCallMessage(NAME, PATHS[0], NAME, "Lock")
            msg.set_no_reply(True)
            bus.send_message(msg)
        with self.assertRaises(dbus.DBusException) as caught:
            obj.Lock(timeout=5)
        self.assertEqual(caught.exception.get_dbus_name(), "org.freedesktop.DBus.Error.LimitsExceeded")

    def test_session_parent_hard_death_releases_adapter_resources(self):
        self.stop(self.service)
        code = '''import os,subprocess,sys,time
p=subprocess.Popen(['/usr/bin/python3',sys.argv[1],'--lock-command',sys.argv[2]])
print(p.pid,flush=True)
while True: time.sleep(.05)
'''
        parent = self.spawn(["/usr/bin/python3", "-c", code, str(self.module), str(self.locker)], stdout=subprocess.PIPE)
        self.assertTrue(select.select([parent.stdout], [], [], 5)[0])
        child = int(parent.stdout.readline())
        self.external_pidfds.append(os.pidfd_open(child))
        ready = subprocess.run(["/usr/bin/python3", str(self.module), "--wait-ready", str(child)],
                               env=self.env, capture_output=True, text=True, timeout=7)
        self.assertEqual(ready.returncode, 0, ready.stderr)
        bus, obj = self.client()
        self.hold(obj)
        self.assert_held()
        self.stop(parent, signal.SIGKILL)
        self.assertTrue(select.select([self.external_pidfds[-1]], [], [], 4)[0], "adapter outlived its session parent")
        self.assert_resumed()

    def test_stalled_authentication_is_bounded_for_daemon_and_readiness(self):
        directory = tempfile.TemporaryDirectory(prefix="pleb-auth-", dir="/tmp")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "bus"
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path))
        listener.listen(4)
        listener.settimeout(.1)
        accepted = []
        stopped = threading.Event()
        def accept():
            while not stopped.is_set():
                try:
                    peer, address = listener.accept()
                    accepted.append(peer)
                except socket.timeout:
                    continue
                except OSError:
                    return
        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        old = self.env["DBUS_SESSION_BUS_ADDRESS"]
        self.env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + str(path)
        try:
            for args in (["/usr/bin/python3", str(self.module), "--wait-ready", str(self.service.pid)],
                         ["/usr/bin/python3", str(self.module), "--lock-command", str(self.locker)],
                         [str(self.shim), "lock"]):
                with self.subTest(args=args):
                    before = time.monotonic()
                    child = self.spawn(args, stderr=subprocess.PIPE)
                    stdout, stderr = child.communicate(timeout=6)
                    self.assertNotEqual(child.returncode, 0)
                    self.assertLess(time.monotonic() - before, 6)
                    self.assertIn(b"startup timed out", stderr)
            self.assertEqual(len(accepted), 3, "all commands must actually reach stalled authentication")
        finally:
            self.env["DBUS_SESSION_BUS_ADDRESS"] = old
            stopped.set()
            listener.close()
            thread.join(timeout=1)
            for peer in accepted:
                peer.close()


class Delivery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ss-delivery-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def install(self, source=ROOT, fail=""):
        return install_to(self.root, source, fail)

    def test_actual_install_bytes_modes_staging_failure_and_no_global_replacement(self):
        system = self.root / "usr/bin/xdg-screensaver"
        system.parent.mkdir(parents=True)
        system.write_text("operator system tool")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        module = self.root / "usr/local/lib/pleb/screensaver.py"
        shim = module.parent / "session-bin/xdg-screensaver"
        self.assertEqual(module.read_bytes(), MODULE.read_bytes())
        self.assertEqual(shim.read_bytes(), (ROOT / "bin/xdg-screensaver").read_bytes())
        self.assertEqual(module.stat().st_mode & 0o777, 0o644)
        self.assertEqual(shim.stat().st_mode & 0o777, 0o755)
        module.write_text("previous module")
        shim.write_text("previous shim")
        result = self.install(fail=".xdg-screensaver.new")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(module.read_text(), "previous module")
        self.assertEqual(shim.read_text(), "previous shim")
        self.assertFalse((module.parent / ".screensaver.py.new").exists())
        self.assertFalse((shim.parent / ".xdg-screensaver.new").exists())
        self.assertEqual(system.read_text(), "operator system tool")
        self.assertFalse((self.root / "usr/local/bin/xdg-screensaver").exists())

    def test_uninstall_removes_adapter_and_shim_and_preserves_other_files(self):
        self.assertEqual(self.install().returncode, 0)
        dest = self.root / "usr/local/lib/pleb"
        neighbor = dest / "session-bin/operator-command"
        neighbor.write_text("operator tool")
        # Redirect the actual uninstall's fixed filesystem namespace into this
        # scratch installation; no host path is inspected by its existence tests.
        source = (ROOT / "lib/install.sh").read_text().replace("/usr/local/", str(self.root / "usr/local") + "/")
        self.file = self.root / "install-fixture.sh"
        self.file.write_text(source)
        env = dict(os.environ, PLEB_ROOT=str(ROOT), FIXTURE_ROOT=str(self.root))
        for key in ("XSESSION_DST", "SESSION_BIN_DST", "PLEB_RECOVERY_DOC_DST", "OPENBOX_CONFIG_DST",
                    "PLEB_DISPLAYS_DST", "PLEB_LOCK_DST", "LID_POLICY_DST", "KILIX_LINK",
                    "KILIX_SETTINGS_LINK"):
            env[key] = str(self.root / key)
        script = 'set -eu\nsource "$PLEB_ROOT/lib/common.sh"\nsource "$FIXTURE_ROOT/install-fixture.sh"\nrun_root() { "$@"; }\ndo_uninstall\n'
        result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((dest / "screensaver.py").exists())
        self.assertFalse((dest / "session-bin/xdg-screensaver").exists())
        self.assertEqual(neighbor.read_text(), "operator tool")


if __name__ == "__main__":
    unittest.main()
