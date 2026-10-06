"""Run the session's actual lifecycle section with private process stand-ins.

The earlier configuration/storage preflight is covered separately. In a mapped
user namespace its host-root ancestors are deliberately untrusted; this fixture
supplies normalized settings and executes the unchanged lifecycle source text.
"""
import json
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

    def child(self, name, previous=None):
        path = self.root/(name+'.pid')
        until = time.monotonic()+8
        while True:
            try:
                pid = int(path.read_text())
                if pid != previous:
                    fd = os.pidfd_open(pid)
                    self.handles.append((pid, fd))
                    return pid
            except (FileNotFoundError, ProcessLookupError, ValueError):
                pass
            if time.monotonic() >= until or self.process.poll() is not None:
                self.fail('child did not start: '+self.log())
            time.sleep(.02)

    def engine_exits(self, *statuses):
        self.script('kilix', '''#!/usr/bin/python3
import json,os,time
from pathlib import Path
root=Path(os.environ['FIXTURE_ROOT'])
record=root/'engine-runs.json'
runs=json.loads(record.read_text()) if record.exists() else []
statuses='''+repr(statuses)+'''
status=statuses[min(len(runs),len(statuses)-1)]
runs.append({'pid':os.getpid(),'status':status,
             'startup_token':os.environ.get('KITTY_PTY_BROKER_STARTUP_TOKEN'),
             'recover_startup':os.environ.get('KITTY_PTY_BROKER_RECOVER_STARTUP')})
pending=record.with_suffix('.pending')
pending.write_text(json.dumps(runs))
pending.replace(record)
(root/'kilix.pid').write_text(str(os.getpid()))
time.sleep(.2)
if status is not None: raise SystemExit(status)
while True: time.sleep(.05)
''')

    def engine_runs(self):
        return json.loads((self.root/'engine-runs.json').read_text())

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

    def test_openbox_runs_without_the_input_method_applications_keep(self):
        # RC5 VM: with XMODIFIERS=@im=ibus inherited from the login, Openbox
        # opened the IBus XIM server and stopped managing windows after an
        # IBus restart. Applications keep the input method.
        self.with_services()
        profile = self.root/'rc.xml'
        profile.write_text('<openbox_config/>\n')
        self.driver.write_text(self.driver.read_text().replace(
            'PLEB_OPENBOX_CONFIG=/unused', 'PLEB_OPENBOX_CONFIG='+str(profile), 1))
        record = (self.bin/'wm').read_text().replace(
            "Path(os.environ['FIXTURE_ROOT'],name+'.pid').write_text(str(os.getpid()))",
            "Path(os.environ['FIXTURE_ROOT'],name+'.env').write_text("
            "'\\n'.join(k+'='+v for k,v in os.environ.items() if 'IM_MODULE' in k or k=='XMODIFIERS'))\n"
            "Path(os.environ['FIXTURE_ROOT'],name+'.pid').write_text(str(os.getpid()))")
        self.script('openbox', record.replace("name=Path(__file__).name", "name='wm'"))
        self.script('kilix', record)
        self.script('ibus-daemon', record.replace("name=Path(__file__).name", "name='ibus'"))
        self.script('ibus', '#!/bin/sh\n[ "$1" = address ] && echo unix:path=/fixture-ibus\n')
        self.script('dbus-update-activation-environment', '#!/bin/sh\nexit 0\n')
        self.env.update(PLEB_WM='openbox', PLEB_INPUT_METHOD='ibus', XMODIFIERS='@im=ibus')
        p = self.start()
        self.child('wm'); self.child('kilix')
        wm = dict(line.split('=', 1) for line in (self.root/'wm.env').read_text().splitlines())
        engine = dict(line.split('=', 1) for line in (self.root/'kilix.env').read_text().splitlines())
        p.terminate()
        p.wait(timeout=7)
        self.assertEqual(wm.get('XMODIFIERS'), '@im=none', self.log())
        self.assertEqual(engine.get('GTK_IM_MODULE'), 'ibus', self.log())
        self.assertEqual(engine.get('XMODIFIERS'), '@im=ibus', self.log())

    def test_owned_desktop_services_are_reaped_with_the_session(self):
        self.with_services()
        p = self.start()
        children = [self.child(name) for name in ('wm', 'xss-lock', 'xssproxy', 'lxpolkit',
                    'xfce4-power-manager', 'blueman-applet', 'udiskie', 'kilix')]
        p.terminate()
        self.assertEqual(p.wait(timeout=7), 143, self.log())
        self.assert_stopped(*children)

    def test_default_login_recovers_killed_frontend_without_replacing_services(self):
        self.with_services()
        p = self.start()
        names = ('wm', 'xss-lock', 'xssproxy', 'lxpolkit',
                 'xfce4-power-manager', 'blueman-applet', 'udiskie')
        children = [self.child(name) for name in names]
        original = self.child('kilix')
        fd = next(fd for pid, fd in self.handles if pid == original)
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        replacement = self.child('kilix', previous=original)
        self.assertNotEqual(original, replacement)
        self.assertIsNone(p.poll(), self.log())
        for name, pid in zip(names, children):
            self.assertEqual(int((self.root/(name+'.pid')).read_text()), pid)
            self.assertTrue(Path('/proc', str(pid)).exists(), self.log())
        p.terminate()
        self.assertEqual(p.wait(timeout=7), 143, self.log())
        self.assert_stopped(*children, original, replacement)

    def test_default_login_clean_frontend_exit_ends_session(self):
        self.with_services()
        self.engine_exits(0)
        p = self.start()
        children = [self.child(name) for name in ('wm', 'xss-lock', 'xssproxy',
                    'lxpolkit', 'xfce4-power-manager', 'blueman-applet', 'udiskie')]
        self.assertEqual(p.wait(timeout=7), 0, self.log())
        self.assertEqual(len(self.engine_runs()), 1)
        self.assert_stopped(*children)

    def test_recovery_off_ends_failed_frontend_and_cleans_up_services(self):
        self.with_services()
        self.env['PLEB_RECOVER_CRASHES'] = 'off'
        self.engine_exits(17)
        p = self.start()
        children = [self.child(name) for name in ('wm', 'xss-lock', 'xssproxy',
                    'lxpolkit', 'xfce4-power-manager', 'blueman-applet', 'udiskie')]
        self.assertEqual(p.wait(timeout=7), 17, self.log())
        self.assertEqual(len(self.engine_runs()), 1)
        self.assert_stopped(*children)

    def test_auto_recovery_keeps_nested_session_exit_behavior(self):
        self.env['FIXTURE_MODE'] = 'ready'
        self.engine_exits(17)
        p = self.start()
        wm = self.child('wm')
        self.assertEqual(p.wait(timeout=7), 17, self.log())
        self.assertEqual(len(self.engine_runs()), 1)
        self.assert_stopped(wm)

    def test_auto_recovery_keeps_adopted_window_manager_exit_behavior(self):
        self.with_services()
        self.env['FIXTURE_MODE'] = 'adopted'
        self.engine_exits(17)
        p = self.start()
        self.assertEqual(p.wait(timeout=7), 17, self.log())
        self.assertEqual(len(self.engine_runs()), 1)
        self.assertFalse((self.root/'wm.pid').exists())

    def test_explicit_recovery_retries_failure_then_ends_on_clean_exit(self):
        self.env.update(PLEB_WM='none', PLEB_RECOVER_CRASHES='on')
        self.env.update(KITTY_PTY_BROKER_STARTUP_TOKEN='f'*32,
                        KITTY_PTY_BROKER_RECOVER_STARTUP='1')
        self.engine_exits(17, 17, 0)
        p = self.start()
        self.assertEqual(p.wait(timeout=12), 0, self.log())
        runs = self.engine_runs()
        self.assertEqual([run['status'] for run in runs], [17, 17, 0])
        token = runs[0]['startup_token']
        self.assertRegex(token, r'^[0-9a-f]{32}$')
        self.assertNotEqual(token, self.env['KITTY_PTY_BROKER_STARTUP_TOKEN'])
        self.assertEqual([run['startup_token'] for run in runs], [token]*3)
        self.assertEqual([run['recover_startup'] for run in runs], ['0', '1', '1'])

    def test_kiosk_still_restarts_clean_frontend_exit(self):
        self.env.update(PLEB_WM='none', PLEB_RESPAWN='1')
        self.engine_exits(0, None)
        p = self.start()
        until = time.monotonic()+8
        while not (self.root/'engine-runs.json').exists() or len(self.engine_runs()) < 2:
            if time.monotonic() >= until or p.poll() is not None:
                self.fail('kiosk did not restart clean exit: '+self.log())
            time.sleep(.02)
        replacement = self.child('kilix', previous=self.engine_runs()[0]['pid'])
        runs = self.engine_runs()
        self.assertEqual([run['recover_startup'] for run in runs], ['0', '0'])
        self.assertEqual(runs[0]['startup_token'], runs[1]['startup_token'])
        p.terminate()
        self.assertEqual(p.wait(timeout=7), 143, self.log())
        self.assertEqual(len(self.engine_runs()), 2)
        self.assert_stopped(replacement)

    def test_locker_death_during_retry_prevents_replacement_frontend(self):
        self.with_services()
        self.engine_exits(17, None)
        p = self.start()
        children = [self.child(name) for name in ('wm', 'xss-lock', 'xssproxy',
                    'lxpolkit', 'xfce4-power-manager', 'blueman-applet', 'udiskie')]
        until = time.monotonic()+5
        while 'respawning in' not in self.log():
            if time.monotonic() >= until or p.poll() is not None:
                self.fail('frontend did not enter retry delay: '+self.log())
            time.sleep(.02)
        fd = next(fd for pid, fd in self.handles if pid == children[1])
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        self.assertEqual(p.wait(timeout=7), 78, self.log())
        self.assertEqual(len(self.engine_runs()), 1)
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
