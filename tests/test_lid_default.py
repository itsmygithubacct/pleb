"""Lid close does nothing by default in the Pleb session, and a user choice wins.

Owner answer 17 (2026-10-07). xfce4-power-manager holds logind's lid inhibitor
so its xfconf setting decides. These tests run the real `_pleb_seed_lid_default`
from bin/pleb-session against a real xfconfd on a private session bus, with a
scratch HOME and XDG dirs: the live user's xfconf is never touched.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from _env_support import clean_env

ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "bin/pleb-session"
CH = ("-c", "xfce4-power-manager")
AC, BAT = "/xfce4-power-manager/lid-action-on-ac", "/xfce4-power-manager/lid-action-on-battery"
NOTHING = "4"


def fn_text(name):
    m = re.search(r"^" + name + r"\(\) \{\n.*?^\}\n", SESSION.read_text(), re.S | re.M)
    return m.group(0) if m else None


def seed_function():
    names = ("_pleb_seed_lid_default", "_pleb_auto_lock_policy")
    parts = [fn_text(n) for n in names]
    return "\n".join(parts) if all(parts) else None


@unittest.skipUnless(shutil.which("dbus-run-session") and shutil.which("xfconf-query")
                     and Path("/usr/lib/x86_64-linux-gnu/xfce4/xfconf/xfconfd").exists(),
                     "xfconf and a private D-Bus session are required")
class LidDefault(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for d in ("home", "config", "cache", "data", "run"):
            (self.tmp / d).mkdir(mode=0o700)
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.tmp / "home"),
                    "XDG_CONFIG_HOME": str(self.tmp / "config"),
                    "XDG_CACHE_HOME": str(self.tmp / "cache"),
                    "XDG_DATA_HOME": str(self.tmp / "data"),
                    "XDG_RUNTIME_DIR": str(self.tmp / "run")}

    def bus(self, script):
        """Run script under a private session bus; xfconfd is D-Bus activated."""
        fn = seed_function()
        self.assertIsNotNone(fn, "_pleb_seed_lid_default is missing")
        r = subprocess.run(["dbus-run-session", "--", "bash", "-c", fn + "\n" + script],
                           env=self.env, capture_output=True, text=True, timeout=60)
        return r

    def q(self, prop):
        return f'xfconf-query -c xfce4-power-manager -p {prop}'

    def read(self, script_tail=""):
        r = self.bus(f'_pleb_seed_lid_default; echo "ac=$({self.q(AC)}) bat=$({self.q(BAT)})"; {script_tail}')
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip().splitlines()[-1]

    def test_fresh_user_gets_nothing_on_ac_and_battery(self):
        self.assertEqual(self.read(), f"ac={NOTHING} bat={NOTHING}")

    def test_channel_reads_from_the_scratch_config_not_the_real_one(self):
        self.read()
        xml = self.tmp / "config/xfce4/xfconf/xfce-perchannel-xml/xfce4-power-manager.xml"
        self.assertTrue(xml.exists())
        self.assertIn(f'name="lid-action-on-ac" type="uint" value="{NOTHING}"', xml.read_text())

    def test_user_choices_are_never_overwritten(self):
        r = self.bus(
            f'{self.q(AC)} --create --type uint --set 1; '
            f'{self.q(BAT)} --create --type uint --set 3; '
            f'_pleb_seed_lid_default; _pleb_seed_lid_default; '
            f'echo "ac=$({self.q(AC)}) bat=$({self.q(BAT)})"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "ac=1 bat=3")

    def test_each_property_is_seeded_independently(self):
        r = self.bus(
            f'{self.q(AC)} --create --type uint --set 1; _pleb_seed_lid_default; '
            f'echo "ac=$({self.q(AC)}) bat=$({self.q(BAT)})"')
        self.assertEqual(r.stdout.strip().splitlines()[-1], f"ac=1 bat={NOTHING}")

    def test_user_who_later_opts_in_keeps_it_across_sessions(self):
        # What xfce4-power-manager-settings does: write the property, then the
        # next session start runs the seed again.
        self.assertEqual(self.read(), f"ac={NOTHING} bat={NOTHING}")
        r = self.bus(f'{self.q(AC)} --set 1; {self.q(BAT)} --set 3')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read(), "ac=1 bat=3")

    def test_seeded_values_are_uint(self):
        r = self.bus(f'_pleb_seed_lid_default; {self.q(AC)} -v; {self.q(BAT)} -v')
        self.assertEqual(r.returncode, 0, r.stderr)
        xml = (self.tmp / "config/xfce4/xfconf/xfce-perchannel-xml/xfce4-power-manager.xml").read_text()
        self.assertEqual(xml.count('type="uint" value="4"'), 2)

    SLEEP = "/xfce4-power-manager/lock-screen-suspend-hibernate"

    def test_no_lock_before_sleep_is_seeded_false_when_automatic_locking_is_off(self):
        r = self.bus(f'_PLEB_AUTO_LOCK=off; _pleb_seed_lid_default; echo "v=$({self.q(self.SLEEP)})"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "v=false")
        xml = (self.tmp / "config/xfce4/xfconf/xfce-perchannel-xml/xfce4-power-manager.xml").read_text()
        self.assertIn('name="lock-screen-suspend-hibernate" type="bool" value="false"', xml)

    def test_a_stored_sleep_lock_choice_is_kept(self):
        r = self.bus(f'{self.q(self.SLEEP)} --create --type bool --set true; _PLEB_AUTO_LOCK=off; '
                     f'_pleb_seed_lid_default; echo "v=$({self.q(self.SLEEP)})"')
        self.assertEqual(r.stdout.strip().splitlines()[-1], "v=true")

    def test_sleep_lock_is_left_to_upstream_when_automatic_locking_is_on(self):
        r = self.bus(f'_PLEB_AUTO_LOCK=on; _pleb_seed_lid_default; {self.q(self.SLEEP)}; echo rc=$?')
        self.assertIn("rc=1", r.stdout)

    def test_unreadable_channel_does_not_seed_the_sleep_lock(self):
        log = self.tmp / "calls"
        r = self.stub_run(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n',
                          "_PLEB_AUTO_LOCK=off; _pleb_seed_lid_default; echo rc=$?")
        self.assertIn("rc=0", r.stdout)
        self.assertIn("lock-screen-suspend-hibernate was not seeded", r.stderr)
        self.assertNotIn("--create", log.read_text())

    def test_policy_matrix(self):
        fn = seed_function()
        for idle, setting, want in (("600", "", "on"), ("0", "", "off"), ("0", "on", "on"),
                                    ("600", "off", "off"), ("600", "auto", "on"), ("0", "auto", "off")):
            with self.subTest(idle=idle, setting=setting):
                env = {**self.env}
                if setting:
                    env["PLEB_AUTO_LOCK"] = setting
                r = subprocess.run(["bash", "-c", fn + f'\n_pleb_auto_lock_policy {idle}; echo $_PLEB_AUTO_LOCK'],
                                   env=env, capture_output=True, text=True)
                self.assertEqual(r.stdout.strip(), want, r.stderr)
        bad = subprocess.run(["bash", "-c", fn + "\n_pleb_auto_lock_policy 0; echo rc=$?"],
                             env={**self.env, "PLEB_AUTO_LOCK": "maybe"}, capture_output=True, text=True)
        self.assertIn("rc=1", bad.stdout)

    def stub_run(self, stub_body, tail):
        fake = self.tmp / "bin"
        fake.mkdir(exist_ok=True)
        (fake / "xfconf-query").write_text(stub_body)
        (fake / "xfconf-query").chmod(0o700)
        return subprocess.run(["bash", "-c", seed_function() + "\n" + tail],
                              env={**self.env, "PATH": f"{fake}:/usr/bin:/bin"},
                              capture_output=True, text=True)

    def test_unreachable_channel_warns_writes_nothing_and_continues(self):
        log = self.tmp / "calls"
        r = self.stub_run(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n', "_pleb_seed_lid_default; echo rc=$?")
        self.assertIn("rc=0", r.stdout)
        self.assertIn("could not read the xfce4-power-manager settings; the lid default for", r.stderr)
        self.assertNotIn("--create", log.read_text())

    def test_failed_create_warns_per_property_and_continues(self):
        r = self.stub_run('#!/bin/sh\ncase "$*" in *-l*) exit 0;; *) exit 1;; esac\n',
                          "_pleb_seed_lid_default; echo rc=$?")
        self.assertIn("rc=0", r.stdout)
        self.assertIn("could not seed the lid default for lid-action-on-ac", r.stderr)
        self.assertIn("lid-action-on-battery", r.stderr)

    def test_transient_read_failure_never_overwrites_an_existing_choice(self):
        # Reviewer regression: AC=3 is stored, one read fails, later calls work.
        # Whatever form the read takes, a failed read must leave 3 in place.
        r = self.bus(
            f'{self.q(AC)} --create --type uint --set 3\n'
            'failed=0\n'
            'xfconf-query() { if [ "$failed" = 0 ]; then failed=1; return 1; fi; command xfconf-query "$@"; }\n'
            f'_pleb_seed_lid_default; echo "ac=$(command {self.q(AC)})"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "ac=3")
        self.assertIn("WARNING", r.stderr)

    def test_failed_read_of_each_call_form_keeps_stored_values(self):
        # Fail every read form in turn (the Nth call), real xfconfd behind it.
        for n in (1, 2, 3):
            with self.subTest(failing_call=n):
                r = self.bus(
                    f'{self.q(AC)} --create --type uint --set 3; {self.q(BAT)} --create --type uint --set 1\n'
                    f'count=0\nxfconf-query() {{ count=$((count+1)); if [ "$count" = {n} ]; then return 1; fi; command xfconf-query "$@"; }}\n'
                    f'_pleb_seed_lid_default; echo "ac=$(command {self.q(AC)}) bat=$(command {self.q(BAT)})"')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.strip().splitlines()[-1], "ac=3 bat=1")


class SessionWiring(unittest.TestCase):
    def locker(self):
        return fn_text("_pleb_start_locker")

    def test_off_mode_locker_ignores_xss_and_sleep_but_stays_the_logind_lock_handler(self):
        t = self.locker()
        self.assertIn('xss-lock --ignore-xss --ignore-sleep -- "$lock_bin" --nofork', t)
        # the on-mode command keeps lock-before-sleep and the XSS events
        self.assertIn('xss-lock --transfer-sleep-lock -- "$lock_bin" --nofork', t)
        self.assertLess(t.index('if [ "$_PLEB_AUTO_LOCK" = on ]; then'), t.index('--transfer-sleep-lock'))
        self.assertLess(t.index('--transfer-sleep-lock'), t.index('--ignore-xss'))

    def test_off_mode_skips_the_sleep_inhibitor_wait_and_zeroes_the_idle_timer(self):
        t = self.locker()
        wait = t.index("<<'PY_INHIBITOR'")
        self.assertLess(t.rindex('if [ "$_PLEB_AUTO_LOCK" = on ]; then', 0, wait), wait)
        self.assertLess(t.index("\nPY_INHIBITOR\n"), t.index("\n    fi\n    PLEB_LOCKER_ACTIVE=1"))
        self.assertLess(t.index('_pleb_auto_lock_policy "$idle"'), t.index('_PLEB_IDLE_LOCK="$idle"'))
        self.assertIn('[ "$_PLEB_AUTO_LOCK" = on ] || idle=0', t)

    def test_sleep_lock_seed_only_when_automatic_locking_is_off(self):
        fn = fn_text("_pleb_seed_lid_default")
        self.assertIn('[ "${_PLEB_AUTO_LOCK:-on}" != off ]', fn)
        self.assertIn("lock-screen-suspend-hibernate bool false sleep-lock", fn)

    def test_seed_runs_before_the_power_manager_starts(self):
        text = SESSION.read_text()
        self.assertLess(text.index("\n    _pleb_seed_lid_default\n"),
                        text.index("_pleb_service_start power xfce4-power-manager"))

    def test_seed_uses_the_nothing_action_for_both_properties(self):
        fn = fn_text("_pleb_seed_lid_default")
        self.assertIn("lid-action-on-ac uint 4", fn)
        self.assertIn("lid-action-on-battery uint 4", fn)
        self.assertRegex(fn, r"--create --type \"\$type\" --set \"\$value\"")

    def test_seed_only_creates_never_resets_or_rewrites_user_values(self):
        fn = fn_text("_pleb_seed_lid_default")
        self.assertNotRegex(fn, r"xfconf-query[^\n]*(\s-r\b|--reset)")
        self.assertIn("xfconf-query -c \"$ch\" -l", fn)
        self.assertIn("grep -qxF", fn)

    def test_packaged_xfconf_defaults_are_not_edited(self):
        # The distribution owns /etc/xdg/xfce4/xfconf/.../xfce4-power-manager.xml.
        for path in ROOT.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                self.assertNotEqual(path.name, "xfce4-power-manager.xml", path)


class LogindDropIn(unittest.TestCase):
    NAME = "50-pleb-lid.conf"
    EXPECTED = {"HandleLidSwitch": "ignore", "HandleLidSwitchExternalPower": "ignore",
                "HandleLidSwitchDocked": "ignore"}

    def run_fn(self, target, managed, call="install_lid_policy"):
        script = r'''
set -euo pipefail
source "$PLEB_ROOT/lib/common.sh"
source "$PLEB_ROOT/lib/install.sh"
run_root() {
    local -a args=("$@"); local last=$((${#args[@]} - 1))
    [[ "${args[$last]}" = /* ]] && args[$last]="$FIXTURE_ROOT${args[$last]}"
    "${args[@]}"
}
''' + call + "\n"
        env = clean_env(target, PLEBIAN_OS_MANAGED_INSTALL=str(int(managed)),
                        FIXTURE_ROOT=str(target), PLEB_ROOT=str(ROOT))
        return subprocess.run(["bash", "-c", script], env=env, cwd=ROOT,
                              capture_output=True, text=True)

    def test_shipped_file_has_exact_keys_and_values(self):
        lines = [l for l in (ROOT / "share/logind" / self.NAME).read_text().splitlines()
                 if l and not l.startswith("#")]
        self.assertEqual(lines[0], "[Login]")
        self.assertEqual(dict(l.split("=", 1) for l in lines[1:]), self.EXPECTED)

    def test_standalone_install_adds_only_its_file_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)
            dd = target / "etc/systemd/logind.conf.d"
            dd.mkdir(parents=True)
            (dd / "10-no-sleep-on-ac.conf").write_text("[Login]\nHandleLidSwitch=ignore\n")
            (dd / "50-plebian-lid.conf").write_text("not ours\n")
            for _ in range(2):
                r = self.run_fn(target, False)
                self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((dd / self.NAME).read_bytes(),
                             (ROOT / "share/logind" / self.NAME).read_bytes())
            self.assertEqual((dd / "10-no-sleep-on-ac.conf").read_text(),
                             "[Login]\nHandleLidSwitch=ignore\n")
            self.assertEqual((dd / "50-plebian-lid.conf").read_text(), "not ours\n")
            self.assertEqual(sorted(p.name for p in dd.iterdir()),
                             ["10-no-sleep-on-ac.conf", self.NAME, "50-plebian-lid.conf"])

    def test_plebian_os_managed_install_leaves_logind_to_plebian_os(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_fn(Path(d), True).returncode, 0)
            self.assertFalse((Path(d) / "etc/systemd").exists())

    def test_installer_installs_before_the_session_launcher_and_uninstall_removes_it(self):
        text = (ROOT / "lib/install.sh").read_text()
        self.assertLess(text.index("\n    install_lid_policy\n"),
                        text.index('"$PLEB_BIN_SRC" "$SESSION_BIN_DST"'))
        self.assertIn('for f in "$LID_POLICY_DST"', text)

    def test_installer_never_restarts_logind(self):
        text = (ROOT / "lib/install.sh").read_text() + SESSION.read_text()
        self.assertNotRegex(text, r"systemctl\s+(try-)?(restart|reload)\s+systemd-logind")


if __name__ == "__main__":
    unittest.main()
