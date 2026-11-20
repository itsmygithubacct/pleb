"""Pleb installed from the pleb Debian package.

The package keeps the whole tree in /usr/lib/pleb with a PACKAGED marker and
no checkout. Then the CLI's destinations are the package's files, `pleb
install` does only per-user steps (never sudo, never a clone), `pleb update`
and `pleb uninstall` defer to apt, and pleb-session maps the fixed /usr/local
paths `pleb install` used onto the package's tree.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def copy_tree(destination):
    shutil.copytree(ROOT, destination, ignore=shutil.ignore_patterns(
        ".git", "tests", "__pycache__", "debian"))


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pleb-packaged-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.tree = self.base / "usr/lib/pleb"
        copy_tree(self.tree)
        # Pleb refuses writable data below a group- or world-writable parent,
        # so the fixture does not inherit the caller's umask.
        self.base.chmod(0o700)
        self.home = self.base / "home"
        self.home.mkdir()
        self.home.chmod(0o700)
        # Records any privileged command instead of running it.
        self.stubs = self.base / "stubs"
        self.stubs.mkdir()
        self.calls = self.base / "calls"
        for name in ("sudo", "git", "apt-get"):
            stub = self.stubs / name
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\nexit 97\n')
            stub.chmod(0o755)
        # A Kilix checkout stand-in with only the settings tool.
        self.kilix = self.base / "kilix"
        self.kilix.mkdir()
        (self.kilix / "kilix-settings").write_text(
            "import os, sys\n"
            "assert sys.argv[1:] == ['--ensure'], sys.argv\n"
            "path = os.environ['GPU_TERMINAL_SETTINGS_FILE']\n"
            "open(path, 'a').close()\n")

    def mark(self):
        (self.tree / "PACKAGED").write_text("deb\n")

    def env(self, **extra):
        env = {
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "PLEB_ENV_SYSTEM": str(self.base / "no-system.env"),
            "PLEB_CLOSURE_SYSTEM": str(self.base / "no-closure.env"),
            "KILIX_DIR": str(self.kilix),
            "KILIX95_DIR": str(self.base / "no-kilix-95"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        env.update(extra)
        return env

    def pleb(self, *args, **extra):
        return subprocess.run([str(self.tree / "bin/pleb"), *args], env=self.env(**extra),
                              capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)

    def common(self, *names):
        script = 'set -e; . "$1/lib/common.sh"; shift; for n; do printf "%s=%s\\n" "$n" "${!n}"; done'
        result = subprocess.run(["bash", "-c", script, "sh", str(self.tree), *names],
                                env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def privileged_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []


PACKAGED_DESTINATIONS = {
    "SESSION_BIN_DST": "/usr/bin/pleb-session",
    "PLEB_LOCK_DST": "/usr/bin/pleb-lock",
    "LID_POLICY_DST": "/usr/lib/systemd/logind.conf.d/50-pleb-lid.conf",
    "KILIX_LINK": "/usr/bin/kilix",
    "KILIX_SETTINGS_LINK": "/usr/bin/kilix-settings",
    "TMUX_TUI_LINK": "/usr/bin/tmux-tui",
    "TMUX_CLI_LINK": "/usr/bin/tb",
    "PLEB_LINK": "/usr/bin/pleb",
    "PLEB_RECOVERY_DOC_DST": "/usr/share/pleb/RECOVERY.md",
    "NOTIFICATION_THEME_DST": "/usr/share/themes/Smoke/xfce-notify-4.0/gtk.css",
}


class DetectionTests(Fixture):
    def test_a_marked_tree_uses_the_package_destinations(self):
        self.mark()
        values = self.common("PLEB_PACKAGED", "PLEB_DISPLAYS_DST", "OPENBOX_CONFIG_DST",
                             *PACKAGED_DESTINATIONS)
        self.assertEqual(values.pop("PLEB_PACKAGED"), "1")
        self.assertEqual(values.pop("PLEB_DISPLAYS_DST"), str(self.tree / "lib/displays.py"))
        self.assertEqual(values.pop("OPENBOX_CONFIG_DST"), str(self.tree / "share/openbox/rc.xml"))
        self.assertEqual(values, PACKAGED_DESTINATIONS)

    def test_an_unmarked_tree_keeps_the_checkout_destinations(self):
        values = self.common("PLEB_PACKAGED", "SESSION_BIN_DST", "PLEB_LINK", "KILIX_SETTINGS_LINK")
        self.assertEqual(values, {"PLEB_PACKAGED": "0",
                                  "SESSION_BIN_DST": "/usr/local/bin/pleb-session",
                                  "PLEB_LINK": "/usr/local/bin/pleb",
                                  "KILIX_SETTINGS_LINK": "/usr/local/bin/kilix-settings"})

    def test_a_checkout_or_a_symlinked_marker_is_not_packaged(self):
        self.mark()
        (self.tree / ".git").mkdir()
        self.assertEqual(self.common("PLEB_PACKAGED"), {"PLEB_PACKAGED": "0"})
        (self.tree / ".git").rmdir()
        (self.tree / "PACKAGED").unlink()
        (self.base / "elsewhere").write_text("deb\n")
        (self.tree / "PACKAGED").symlink_to(self.base / "elsewhere")
        self.assertEqual(self.common("PLEB_PACKAGED"), {"PLEB_PACKAGED": "0"})

    def test_an_explicit_destination_still_wins(self):
        self.mark()
        script = ('SESSION_BIN_DST=/opt/x; . "$1/lib/common.sh"; echo "$SESSION_BIN_DST"')
        result = subprocess.run(["bash", "-c", script, "sh", str(self.tree)],
                                env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.stdout.strip(), "/opt/x", result.stderr)


class CommandTests(Fixture):
    def test_install_does_only_per_user_steps(self):
        self.mark()
        result = self.pleb("install")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.privileged_calls(), [])
        shared = self.home / ".local/gpu_terminal"
        self.assertTrue((shared / "settings.conf").is_file())
        self.assertTrue((shared / "pleb/data/wallpapers/plebian-os.png").is_file())
        self.assertIn("installed from Debian packages", result.stdout + result.stderr)

    def test_install_needs_the_kilix_settings_tool(self):
        self.mark()
        (self.kilix / "kilix-settings").unlink()
        result = self.pleb("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kilix-settings", result.stderr)
        self.assertEqual(self.privileged_calls(), [])

    def test_install_refuses_a_symlinked_kilix_settings_tool(self):
        self.mark()
        real = self.base / "elsewhere-settings"
        (self.kilix / "kilix-settings").rename(real)
        (self.kilix / "kilix-settings").symlink_to(real)
        result = self.pleb("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe Kilix settings TUI", result.stderr)
        self.assertFalse((self.home / ".local/gpu_terminal/settings.conf").exists())

    def test_uninstall_defers_to_apt(self):
        self.mark()
        result = self.pleb("uninstall")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("sudo apt remove pleb", result.stderr)
        self.assertEqual(self.privileged_calls(), [])

    def test_update_forwards_arguments_and_exit_status_to_the_selected_updater(self):
        # Load the real CLI, then substitute recorders for both update backends.
        script = ('source "$1/bin/pleb" version >/dev/null; shift; '
                  'unset -f do_update do_packaged_update; main update "$@"')
        for backend in ("do_update", "do_packaged_update"):
            stub = self.stubs / backend
            stub.write_text('#!/bin/sh\nprintf "<%s>\\n" "${0##*/}" "$@"\nexit 23\n')
            stub.chmod(0o755)
        for packaged, expected in ((False, "do_update"), (True, "do_packaged_update")):
            with self.subTest(packaged=packaged):
                if packaged:
                    self.mark()
                result = subprocess.run(
                    ["bash", "-c", script, "sh", str(self.tree), "--dry-run", "two words", ""],
                    env=self.env(), capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 23, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [f"<{expected}>", "<--dry-run>", "<two words>", "<>"])

    def test_packaged_diagnostics_describe_missing_files_without_checkout_advice(self):
        absent = str(self.base / "absent")
        paths = dict(XSESSION_DST=absent, OPENBOX_CONFIG_DST=absent, KILIX_LINK=absent,
                     KILIX_SETTINGS_LINK=absent, TMUX_TUI_LINK=absent, TMUX_CLI_LINK=absent)
        for command in ("status", "doctor"):
            self.assertIn("run: pleb install", self.pleb(command, **paths).stdout)
        self.mark()
        for command, code in (("status", 0), ("doctor", 1)):
            with self.subTest(command=command):
                result = self.pleb(command, **paths)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertNotIn("run: pleb install", result.stdout + result.stderr)
                self.assertIn("not installed", result.stdout)
                if command == "status":
                    self.assertIn(f"kilix cmd: not installed at {absent}", result.stdout)
                    self.assertIn(f"command not installed at {absent}", result.stdout)
                    self.assertIn("tmux     : manager or tb alias not installed", result.stdout)

    def test_packaged_status_accepts_executables_symlinks_and_per_user_tmux(self):
        self.mark()
        kilix = self.stubs / "kilix"
        manager = self.stubs / "tmux-tui"
        cli = self.stubs / "tb"
        for exe in (kilix, manager, cli):
            exe.write_text("#!/bin/sh\nexit 0\n")
            exe.chmod(0o755)
        paths = dict(KILIX_LINK=str(kilix), TMUX_TUI_LINK=str(manager), TMUX_CLI_LINK=str(cli))
        result = self.pleb("status", **paths)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"kilix cmd: {kilix}", result.stdout)
        self.assertIn(f"manager {manager}; cli alias {cli}", result.stdout)

        target = self.kilix / "kilix"
        kilix.rename(target)
        kilix.symlink_to(target)
        local_cli = self.home / ".local/bin/tb"
        local_cli.parent.mkdir(parents=True)
        cli.rename(local_cli)
        result = self.pleb("status", **paths)
        self.assertIn(f"kilix cmd: {kilix}", result.stdout)
        self.assertIn(f"manager {manager}; cli alias {local_cli}", result.stdout)
        target.unlink()
        self.assertIn(f"kilix cmd: not installed at {kilix}", self.pleb("status", **paths).stdout)

    def test_an_unmarked_install_still_takes_the_checkout_path(self):
        # PLEB_SKIP_DEPS keeps apt out; the first root step reaches the stub.
        result = self.pleb("install", PLEB_SKIP_DEPS="1")
        self.assertNotIn("installed from Debian packages", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)


class SessionPathTests(unittest.TestCase):
    """_pleb_installed, taken from bin/pleb-session with its tree relocated."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pleb-session-paths-")
        self.addCleanup(temporary.cleanup)
        self.tree = Path(temporary.name) / "pleb"
        self.tree.mkdir()
        text = (ROOT / "bin/pleb-session").read_text()
        start = text.index("_PLEB_PACKAGED_TREE=/usr/lib/pleb\n")
        end = text.index("\n}\n", start) + 3
        self.function = text[start:end].replace(
            "_PLEB_PACKAGED_TREE=/usr/lib/pleb", f"_PLEB_PACKAGED_TREE={self.tree}")
        self.assertEqual(text.count("_PLEB_PACKAGED_TREE=/usr/lib/pleb\n"), 1)

    def mapped(self, path):
        result = subprocess.run(["bash", "-c", self.function + '\n_pleb_installed "$1"', "sh", path],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.rstrip("\n")

    CASES = {
        "/usr/local/lib/pleb/screensaver.py": "{tree}/lib/screensaver.py",
        "/usr/local/lib/pleb/session-bin": "{tree}/lib/session-bin",
        "/usr/local/share/pleb/openbox/rc.xml": "{tree}/share/openbox/rc.xml",
        "/usr/local/bin/pleb-lock": "/usr/bin/pleb-lock",
        "/usr/local/share/doc/pleb/RECOVERY.md": "/usr/local/share/doc/pleb/RECOVERY.md",
        "/opt/fixture/screensaver.py": "/opt/fixture/screensaver.py",
    }

    def test_without_the_marker_nothing_is_mapped(self):
        for path in self.CASES:
            with self.subTest(path=path):
                self.assertEqual(self.mapped(path), path)

    def test_with_the_marker_the_three_prefixes_are_mapped(self):
        (self.tree / "PACKAGED").write_text("deb\n")
        for path, expected in self.CASES.items():
            with self.subTest(path=path):
                self.assertEqual(self.mapped(path), expected.format(tree=self.tree))

    def test_a_symlinked_marker_maps_nothing(self):
        other = self.tree.parent / "marker"
        other.write_text("deb\n")
        (self.tree / "PACKAGED").symlink_to(other)
        for path in self.CASES:
            with self.subTest(path=path):
                self.assertEqual(self.mapped(path), path)

    def test_every_fixed_session_path_goes_through_the_mapping(self):
        text = (ROOT / "bin/pleb-session").read_text()
        for literal in ("/usr/local/share/pleb/openbox/rc.xml", "/usr/local/lib/pleb/screenlock.py",
                        "/usr/local/bin/pleb-lock", "/usr/local/lib/pleb/displays.py"):
            with self.subTest(literal=literal):
                self.assertIn(f"_pleb_installed {literal}", text)
        for variable in ("module", "session_bin", "notifications"):
            with self.subTest(variable=variable):
                self.assertIn(f'{variable}="$(_pleb_installed "${variable}")"', text)


if __name__ == "__main__":
    unittest.main()
