"""Run the session's actual lifecycle section with private process stand-ins.

The earlier configuration/storage preflight is covered separately. In a mapped
user namespace its host-root ancestors are deliberately untrusted; this fixture
supplies normalized settings and executes the unchanged lifecycle source text.
"""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

from _env_support import clean_env

ROOT = Path(__file__).resolve().parents[1]


class SessionProcesses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pleb-session-', dir=Path.home())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.handles = []
        self.controls = []
        self.addCleanup(self.cleanup)
        for name in ('xset', 'xsetroot', 'xrandr', 'xterm'):
            self.script(name, '#!/bin/sh\nexit 0\n')
        self.script('xprop', '''#!/bin/sh
if [ "$FIXTURE_MODE" = adopted ] || { [ "$FIXTURE_MODE" = ready ] && [ -f "$FIXTURE_ROOT/wm.pid" ]; }; then
    printf '_NET_SUPPORTING_WM_CHECK: window id # 0x123\n'
else
    exit 1
fi
''')
        for name in ('wm', 'kilix'):
            self.script(name, '''#!/usr/bin/python3
import os,signal,time
from pathlib import Path
name=Path(__file__).name
signal.signal(signal.SIGTERM,signal.SIG_IGN)
Path(os.environ['FIXTURE_ROOT'],name+'.pid').write_text(str(os.getpid()))
if name=='kilix' and os.environ['FIXTURE_MODE']=='adopted': raise SystemExit(0)
while True: time.sleep(.05)
''')
        self.env = clean_env(self.root, PATH=str(self.bin)+':/usr/bin:/bin',
                             DISPLAY='', XAUTHORITY='', PLEB_MIC_DEFAULT='off',
                             PLEB_CLIPBOARD='off', PLEB_NO_FILL='1',
                             PLEB_DESKTOP='0', PLEB_RESPAWN='0',
                             PLEB_WM=str(self.bin/'wm'), PLEB_WM_TIMEOUT='30',
                             PLEB_LOG=str(self.root/'session.log'),
                             KILIX=str(self.bin/'kilix'), KILIX_DIR=str(self.root/'absent'),
                             FIXTURE_ROOT=str(self.root), FIXTURE_MODE='starting')
        lifecycle = (ROOT/'bin/pleb-session').read_text().split(
            '# --- window manager ---', 1)[1]
        self.driver = self.root/'session-under-test'
        self.driver.write_text('''#!/bin/bash
set -u
_PLEB_OPENBOX_CONFIG_DEFAULT=0
PLEB_OPENBOX_CONFIG=/unused
_PLEB_KILIX_ARGS_DEFAULT=1
_PLEB_RUN_ALIASES_DEFAULT=1
KILIX_RUN_ALIAS_APPS=
KILIX_RUN_ALIAS_EXCLUDE_APPS=
exec >>"$PLEB_LOG" 2>&1
# --- window manager ---'''+lifecycle)
        self.driver.chmod(0o700)

    def script(self, name, text):
        p = self.bin/name
        p.write_text(text)
        p.chmod(0o700)

    def start(self):
        with (self.root/'startup.log').open('w') as log:
            self.process = subprocess.Popen([str(self.driver)], env=self.env,
                                            stdout=log, stderr=log)
        self.handles.append((self.process.pid, os.pidfd_open(self.process.pid)))
        return self.process

    def child(self, name):
        path = self.root/(name+'.pid')
        until = time.monotonic()+8
        while not path.exists():
            if time.monotonic() >= until or self.process.poll() is not None:
                self.fail('child did not start: '+self.log())
            time.sleep(.02)
        pid = int(path.read_text())
        self.handles.append((pid, os.pidfd_open(pid)))
        return pid

    def log(self):
        p = self.root/'session.log'
        return p.read_text() if p.exists() else (self.root/'startup.log').read_text()

    def cleanup(self):
        for _, fd in reversed(self.handles):
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(fd)
        if hasattr(self, 'process'):
            self.process.wait(timeout=5)
        for control in self.controls:
            control.wait(timeout=5)

    def assert_stopped(self, *pids):
        for pid in pids:
            self.assertFalse(Path('/proc',str(pid)).exists(), (pid,self.log()))

    def test_term_during_window_manager_startup_reaps_resistant_child(self):
        p = self.start()
        wm = self.child('wm')
        p.terminate()
        self.assertEqual(p.wait(timeout=7), 143, self.log())
        self.assert_stopped(wm)
        self.assertFalse((self.root/'kilix.pid').exists())

    def test_no_wm_kiosk_shutdown_reaps_resistant_engine(self):
        self.env.update(PLEB_WM='none', PLEB_RESPAWN='1')
        p = self.start()
        engine = self.child('kilix')
        p.terminate()
        self.assertEqual(p.wait(timeout=7), 143, self.log())
        self.assert_stopped(engine)

    def test_owned_wm_and_engine_shutdown_is_bounded_and_reaped(self):
        self.env['FIXTURE_MODE']='ready'
        p = self.start()
        wm, engine = self.child('wm'), self.child('kilix')
        p.terminate()
        self.assertEqual(p.wait(timeout=7), 143, self.log())
        self.assert_stopped(wm, engine)

    def test_failed_wm_readiness_reaps_resistant_child(self):
        self.env['PLEB_WM_TIMEOUT']='1'
        p = self.start()
        wm = self.child('wm')
        self.assertEqual(p.wait(timeout=7), 78, self.log())
        self.assert_stopped(wm)

    def test_adopted_wm_control_survives_normal_session_exit(self):
        control = subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(30)'])
        self.controls.append(control)
        self.handles.append((control.pid,os.pidfd_open(control.pid)))
        self.env['FIXTURE_MODE']='adopted'
        p = self.start()
        self.assertEqual(p.wait(timeout=7), 0, self.log())
        self.assertIsNone(control.poll())
        self.assertFalse((self.root/'wm.pid').exists())

    def test_signal_between_spawn_and_identity_publication_is_deferred(self):
        text = self.driver.read_text()
        # Deterministically interrupt the actual launch helper at its publication
        # boundary. The child and cleanup are real; only signal timing is injected.
        text = text.replace('    child=$!\n', '    child=$!\n'
                            '    printf "%s" "$child" >"$FIXTURE_ROOT/launch.pid"\n'
                            '    kill -TERM "$$"\n', 1)
        self.driver.write_text(text)
        p = self.start()
        self.assertEqual(p.wait(timeout=7), 143, self.log())
        self.assert_stopped(int((self.root/'launch.pid').read_text()))

    def test_wrong_start_tick_does_not_signal_a_live_direct_child(self):
        setup = self.driver.read_text().split('PLEB_WM_ACTIVE="$(_pleb_wm_window)"', 1)[0]
        self.driver.write_text(setup+'''
"$KILIX" &
control=$!
_pleb_record_identity "$control" expected
_pleb_stop_owned "$control:0" || exit 81
_pleb_owned_alive "$control" "$expected" || exit 82
_pleb_stop_owned "$control:$expected" || exit 83
''')
        p = self.start()
        self.assertEqual(p.wait(timeout=7), 0, self.log())

    def test_cleanup_failure_is_not_reported_as_success(self):
        setup = self.driver.read_text().split('PLEB_WM_ACTIVE="$(_pleb_wm_window)"', 1)[0]
        self.driver.write_text(setup+'\n_pleb_stop_owned() { return 1; }\nexit 0\n')
        p = self.start()
        self.assertEqual(p.wait(timeout=7), 1, self.log())

    def test_recovery_terminal_remains_owned_during_signal(self):
        self.env['PLEB_WM_TIMEOUT']='1'
        self.script('xterm', (self.bin/'kilix').read_text())
        p = self.start()
        wm = self.child('wm')
        terminal = self.child('xterm')
        p.terminate()
        self.assertEqual(p.wait(timeout=7), 143, self.log())
        self.assert_stopped(wm, terminal)

    def with_services(self):
        self.env.update(PLEB_SESSION_SERVICES='on', PLEB_INPUT_METHOD='off',
                        DISPLAY=':77', DBUS_SESSION_BUS_ADDRESS='unix:path=/fixture',
                        PYTHONPATH=str(self.root), FIXTURE_MODE='ready')
        locker = self.root / 'pleb-lock'
        locker.write_text((ROOT / 'bin/pleb-lock').read_text())
        locker.chmod(0o700)
        self.script('i3lock', '#!/bin/sh\nexit 0\n')
        self.script('xfconf-query', '#!/bin/sh\nexit 0\n')
        for name in ('xss-lock', 'xssproxy', 'lxpolkit', 'xfce4-power-manager', 'blueman-applet', 'udiskie'):
            self.script(name, (self.bin / 'wm').read_text())
        # Only logind is a stand-in here. Process ownership, signals, service
        # launch arguments and the session supervision loop execute unchanged.
        (self.root / 'dbus.py').write_text('''
import os
from pathlib import Path
class SystemBus:
    def get_object(self,*args): return self
def Interface(obj,*args): return obj
def _inhibitors(self,**kwargs):
    p=Path(os.environ['FIXTURE_ROOT'],'xss-lock.pid')
    return [('sleep','fixture','fixture','delay',os.getuid(),int(p.read_text()))] if p.exists() else []
SystemBus.ListInhibitors=_inhibitors
''')

    def test_owned_desktop_services_are_reaped_with_the_session(self):
        self.with_services()
        p = self.start()
        children = [self.child(name) for name in ('wm', 'xss-lock', 'xssproxy', 'lxpolkit',
                    'xfce4-power-manager', 'blueman-applet', 'udiskie', 'kilix')]
        p.terminate()
        self.assertEqual(p.wait(timeout=7), 143, self.log())
        self.assert_stopped(*children)

    def test_locker_failure_ends_the_desktop_and_reaps_owned_children(self):
        self.with_services()
        p = self.start()
        children = [self.child(name) for name in ('wm', 'xss-lock', 'xssproxy', 'lxpolkit',
                    'xfce4-power-manager', 'blueman-applet', 'udiskie', 'kilix')]
        fd = next(fd for pid, fd in self.handles if pid == children[1])
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        self.assertEqual(p.wait(timeout=7), 78, self.log())
        self.assert_stopped(*children)

    def test_missing_locker_prevents_the_engine_from_starting(self):
        self.with_services()
        (self.bin / 'i3lock').unlink()
        # Constrain resolution so an installed host locker is never selected.
        self.driver.write_text(self.driver.read_text().replace(
            'command -v i3lock >/dev/null', 'command -v fixture-missing-locker >/dev/null'))
        p = self.start()
        wm = self.child('wm')
        self.assertEqual(p.wait(timeout=7), 78, self.log())
        self.assert_stopped(wm)
        self.assertFalse((self.root/'kilix.pid').exists())


if __name__ == '__main__':
    unittest.main()
