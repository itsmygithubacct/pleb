"""Actual activation, live login authority and physical/private lock dispatch."""
import ctypes
import json
import os
from pathlib import Path
import re
import select
import shlex
import signal
import subprocess
import sys
import time
import unittest
import dbus
import test_screensaver as base

KILIX = Path(os.environ.get("CHECK_KILIX", base.ROOT.parent / "kilix"))
ACTIVATED = '''import dbus,json,os,subprocess,sys,time
from pathlib import Path
bus=dbus.bus.BusConnection(os.environ['DBUS_SESSION_BUS_ADDRESS'])
bus.request_name(os.environ['ACTIVATED_NAME'],dbus.bus.NAME_FLAG_DO_NOT_QUEUE)
if os.environ.get('BEFORE_LOCK_GATE'):
 gate=Path(os.environ['BEFORE_LOCK_GATE'])
 Path(str(gate)+'-ready').write_text('ready')
 while not gate.exists():time.sleep(.01)
if os.environ.get('ACTIVATED_PRIVATE')=='1':
 sys.path.insert(0,os.environ['KILIX_CONFIG'])
 from kilix_sdk.xapp import XAppSession
 session=XAppSession('activated-lock',320,240)
 try:
  session.start_xvfb()
  app=session.launch_app(['xdg-screensaver','lock'],isolate_bus=True,clipboard=False,capture_source=False)
  result={'returncode':app.wait(timeout=8),'stderr':''}
 finally:session.close()
else:
 r=subprocess.run(['xdg-screensaver','lock'],capture_output=True,text=True,timeout=7)
 result={'returncode':r.returncode,'stderr':r.stderr}
result['env']={k:os.environ.get(k) for k in ['XDG_SESSION_ID','PLEB_DESKTOP_SESSION_ID','DISPLAY','XDG_CURRENT_DESKTOP']}
Path(os.environ['ACTIVATION_RESULT']).write_text(json.dumps(result))
time.sleep(20)
'''
FOREIGN = '''import dbus,dbus.service,os
from pathlib import Path
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
DBusGMainLoop(set_as_default=True)
bus=dbus.bus.BusConnection(os.environ['DBUS_SESSION_BUS_ADDRESS'])
class OtherDesktop(dbus.service.Object):
 @dbus.service.method('org.gnome.ScreenSaver',in_signature='',out_signature='')
 def Lock(self):Path(os.environ['OTHER_LOCK']).write_text('current other desktop')
obj=OtherDesktop(bus,'/org/gnome/ScreenSaver')
standard=OtherDesktop(bus,'/ScreenSaver')
bus.request_name('org.gnome.ScreenSaver',dbus.bus.NAME_FLAG_DO_NOT_QUEUE)
bus.request_name('org.freedesktop.ScreenSaver',dbus.bus.NAME_FLAG_DO_NOT_QUEUE)
Path(os.environ['OTHER_READY']).write_text('ready')
GLib.MainLoop().run()
'''


class Activation(base.Screensaver):
    def setUp(self):
        self.assertEqual(ctypes.CDLL(None).prctl(36, 1, 0, 0, 0), 0)
        self.activated_pids = []
        self.activation_number = 0
        super().setUp()

    def cleanup(self):
        super().cleanup()
        for pid in self.activated_pids:
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass

    def activation_bus(self):
        self.stop(self.service)
        directory = self.root / "activation"
        directory.mkdir()
        config = self.root / "activation-bus.conf"
        config.write_text('<busconfig><type>session</type><listen>unix:tmpdir=/tmp</listen><auth>EXTERNAL</auth>'
            '<servicedir>' + str(directory) + '</servicedir><policy context="default">'
            '<allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
        saved = self.env
        self.env = dict(os.environ)
        self.activation_daemon = self.spawn(["dbus-daemon", "--nofork", "--print-address", "--config-file=" + str(config)],
                                             stdout=subprocess.PIPE)
        self.assertTrue(select.select([self.activation_daemon.stdout], [], [], 5)[0])
        address = self.activation_daemon.stdout.readline().decode().strip()
        self.env = saved
        self.env.update(DBUS_SESSION_BUS_ADDRESS=address, PLEB_DESKTOP_BUS_ADDRESS=address,
                        XDG_CURRENT_DESKTOP="Pleb", XDG_SESSION_DESKTOP="pleb")
        self.env["PATH"] = str(self.shim.parent) + ":" + self.env["PATH"]
        self.activation_directory = directory
        self.activation_client = dbus.bus.BusConnection(address)
        self.clients.append(self.activation_client)
        self.start_service()
        text = (base.ROOT / "bin/pleb-session").read_text().replace("\\\n", " ")
        keys = set()
        for command in re.findall(r"dbus-update-activation-environment --systemd ([^\n]+)", text):
            keys.update(shlex.split(command.split("2>", 1)[0]))
        self.imported = {key: self.env[key] for key in keys if key in self.env}
        self.assertNotIn("XDG_SESSION_ID", self.imported)

    def activate(self, *, private=False, imports=None, before_lock=None):
        self.activation_number += 1
        name = "org.example.PlebFixActivation" + str(self.activation_number)
        result = self.root / ("activated-" + str(self.activation_number) + ".json")
        script = self.root / "activated.py"
        script.write_text(ACTIVATED)
        (self.activation_directory / (name + ".service")).write_text(
            "[D-BUS Service]\nName=" + name + "\nExec=/usr/bin/python3 " + str(script) + "\n")
        self.activation_client.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "ReloadConfig", "", (), timeout=2)
        values = dict(self.imported if imports is None else imports,
            ACTIVATED_NAME=name, ACTIVATION_RESULT=str(result), ACTIVATED_PRIVATE="1" if private else "0",
            KILIX_CONFIG=str(KILIX / "config"))
        if before_lock is not None:
            gate = self.root / ("before-lock-" + str(self.activation_number))
            values["BEFORE_LOCK_GATE"] = str(gate)
        self.activation_client.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "UpdateActivationEnvironment", "a{ss}", (values,), timeout=2)
        self.activation_client.start_service_by_name(name)
        owner = self.activation_client.get_name_owner(name)
        pid = int(self.activation_client.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", (owner,), timeout=2))
        self.activated_pids.append(pid)
        self.external_pidfds.append(os.pidfd_open(pid))
        if before_lock is not None:
            self.until(lambda: Path(str(gate) + "-ready").exists())
            before_lock()
            gate.write_text("continue")
        self.until(result.exists, seconds=9)
        report = json.loads(result.read_text())
        self.assertIsNone(report["env"]["XDG_SESSION_ID"])
        print(json.dumps({"activation": name, **report}), flush=True)
        return report

    def locks(self):
        path = self.root / "lock.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def state(self, **changes):
        (self.root / "session-state.json").write_text(json.dumps(changes))

    def test_activation_current_physical_lock_without_login_id_while_inhibited(self):
        self.activation_bus()
        bus, obj = self.client()
        cookie = self.hold(obj)
        report = self.activate()
        self.assertEqual(report["returncode"], 0, report)
        self.assertEqual(len(self.locks()), 1)
        self.assertEqual(self.locks()[0]["session"], "fixture")
        self.assertEqual(self.locks()[0]["display"], self.env["DISPLAY"])
        self.assertEqual(self.locks()[0]["authority"], self.env["XAUTHORITY"])
        queries = [json.loads(line)["pid"] for line in (self.root / "session-queries.jsonl").read_text().splitlines()]
        self.assertIn(os.getpid(), queries, "logind must resolve the serviced parent PID")
        self.assert_held()
        obj.UnInhibit(cookie, timeout=3)

    def test_activation_current_private_sdk_lock_without_login_id(self):
        self.activation_bus()
        bus, obj = self.client()
        cookie = self.hold(obj)
        report = self.activate(private=True)
        self.assertEqual(report["returncode"], 0, report)
        self.assertEqual([row["session"] for row in self.locks()], ["fixture"])
        self.assertEqual(self.locks()[0]["display"], self.env["DISPLAY"])
        self.assertEqual(self.locks()[0]["authority"], self.env["XAUTHORITY"])
        self.assert_held()
        obj.UnInhibit(cookie, timeout=3)

    def test_activation_current_startx_tty_session_without_logind_display(self):
        self.activation_bus()
        self.stop(self.service)
        self.state(Display="", Type="tty")
        self.start_service()
        self.assertEqual(self.activate()["returncode"], 0)
        self.assertEqual([row["session"] for row in self.locks()], ["fixture"])

    def test_activation_stale_markers_after_inactive_session_do_not_lock(self):
        self.activation_bus()
        self.state(Active=False)
        self.assertNotEqual(self.activate()["returncode"], 0)
        self.assertEqual(self.locks(), [])

    def test_activation_stale_markers_after_logout_do_not_lock(self):
        self.activation_bus()
        self.state(Missing=True)
        self.assertNotEqual(self.activate()["returncode"], 0)
        self.assertEqual(self.locks(), [])

    def test_activation_logout_also_refuses_standard_lock_without_changing_inhibitors(self):
        bus, obj = self.client()
        cookie = self.hold(obj)
        self.state(Missing=True)
        with self.assertRaises(dbus.DBusException):
            obj.Lock(timeout=5)
        self.assertEqual(self.locks(), [])
        self.assert_held()
        obj.UnInhibit(cookie, timeout=3)
        self.assert_resumed()

    def test_activation_reused_session_id_with_different_logind_lifetime_do_not_lock(self):
        self.activation_bus()
        self.state(TimestampMonotonic=123457)
        self.assertNotEqual(self.activate()["returncode"], 0)
        self.assertEqual(self.locks(), [])

    def test_activation_new_login_refuses_stale_markers_then_accepts_current(self):
        self.activation_bus()
        self.stop(self.service)
        self.env.update(PLEB_DESKTOP_SESSION_ID="new-login", XDG_SESSION_ID="new-login")
        self.state(Id="new-login")
        self.start_service()
        self.assertNotEqual(self.activate()["returncode"], 0)
        self.assertEqual(self.locks(), [])
        current = dict(self.imported, PLEB_DESKTOP_SESSION_ID="new-login")
        self.assertEqual(self.activate(imports=current)["returncode"], 0)
        self.assertEqual([row["session"] for row in self.locks()], ["new-login"])

    def test_activation_wrong_caller_display_does_not_use_stale_physical_marker(self):
        self.activation_bus()
        values = dict(self.imported, DISPLAY=":65534")
        self.assertNotEqual(self.activate(imports=values)["returncode"], 0)
        self.assertEqual(self.locks(), [])

    def test_activation_logind_owner_loss_refuses_stale_service_authority(self):
        self.activation_bus()
        self.stop(self.logind)
        self.assertNotEqual(self.activate()["returncode"], 0)
        self.assertEqual(self.locks(), [])

    def test_activation_replaced_logind_owner_cannot_reuse_session_fingerprint(self):
        self.activation_bus()
        self.stop(self.logind)
        (self.root / "logind-ready").unlink()
        self.logind = self.spawn(["/usr/bin/python3", "-c", base.LOGIND])
        self.until(lambda: (self.root / "logind-ready").exists())
        self.assertNotEqual(self.activate()["returncode"], 0)
        self.assertEqual(self.locks(), [])

    def test_activation_caller_bus_loss_is_bounded_without_physical_fallback(self):
        self.activation_bus()
        started = time.monotonic()
        report = self.activate(before_lock=lambda: self.stop(self.activation_daemon))
        self.assertNotEqual(report["returncode"], 0, report)
        self.assertLess(time.monotonic() - started, 6.5)
        self.assertEqual(self.locks(), [])

    def test_activation_inactive_session_refuses_context_but_explicit_standard_lock_works(self):
        self.activation_bus()
        self.state(Active=False)
        self.assertNotEqual(self.activate()["returncode"], 0)
        self.assertEqual(self.locks(), [])
        bus, obj = self.client()
        cookie = self.hold(obj)
        obj.Lock(timeout=5)
        self.assertEqual([row["session"] for row in self.locks()], ["fixture"])
        self.assert_held()
        obj.UnInhibit(cookie, timeout=3)

    def test_activation_service_loss_is_closed_without_generic_fallback(self):
        self.activation_bus()
        self.stop(self.service)
        self.assertNotEqual(self.activate()["returncode"], 0)
        self.assertEqual(self.locks(), [])

    def test_activation_stale_generation_refused_with_standard_lock_still_available(self):
        bus, obj = self.client()
        context = bus.call_blocking(base.NAME, base.PATHS[0], "org.pleb.ScreenSaver1", "GetContext", "", (), timeout=2)
        self.stop(self.service)
        self.start_service()
        with self.assertRaises(dbus.DBusException):
            bus.call_blocking(base.NAME, base.PATHS[0], "org.pleb.ScreenSaver1", "LockForContext", "sss", context[:3], timeout=3)
        self.assertEqual(self.locks(), [])
        bus.call_blocking(base.NAME, base.PATHS[0], base.NAME, "Lock", "", (), timeout=5)
        self.assertEqual(len(self.locks()), 1)

    def test_activation_later_nonpleb_login_uses_actual_system_tool_current_desktop(self):
        self.activation_bus()
        self.stop(self.service)
        self.env.update(OTHER_LOCK=str(self.root / "other-lock"), OTHER_READY=str(self.root / "other-ready"))
        self.spawn(["/usr/bin/python3", "-c", FOREIGN])
        self.until(lambda: (self.root / "other-ready").exists())
        values = dict(self.imported, XDG_CURRENT_DESKTOP="GNOME", XDG_SESSION_DESKTOP="gnome")
        report = self.activate(imports=values)
        self.assertEqual(report["returncode"], 0, report)
        self.assertEqual((self.root / "other-lock").read_text(), "current other desktop")
        self.assertEqual(self.locks(), [], "stale markers must not dispatch the old physical Pleb lock")


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(Activation(name) for name in loader.getTestCaseNames(Activation)
                              if name.startswith("test_activation_"))
