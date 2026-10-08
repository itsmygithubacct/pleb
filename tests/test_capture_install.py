from pathlib import Path
import ctypes
import shutil
import subprocess
import tempfile
import unittest

from _env_support import clean_env

ROOT = Path(__file__).resolve().parents[1]


class CaptureInstallTests(unittest.TestCase):
    def install(self, target, managed, *, compiler="cc", success=True):
        env = clean_env(target, PLEBIAN_OS_MANAGED_INSTALL=str(int(managed)),
                        FIXTURE_ROOT=str(target), PLEB_ROOT=str(ROOT), CC=compiler)
        script = self.install_script()
        result = subprocess.run(["bash", "-c", script], env=env, cwd=ROOT, capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    @staticmethod
    def install_script():
        return r'''
set -euo pipefail
source "$PLEB_ROOT/lib/common.sh"
source "$PLEB_ROOT/lib/install.sh"
run_root() {
    local -a args=("$@")
    case "$1" in
        install)
            local final=$((${#args[@]} - 1))
            [[ "${args[$final]}" = /* ]] || return 91
            args[$final]="$FIXTURE_ROOT${args[$final]}"
            # FAIL_STAGE=NAME: the copy to .NAME.new writes part of the file
            # and fails, as a full disk would.
            if [[ -n "${FAIL_STAGE:-}" && "${args[$final]}" = */."$FAIL_STAGE".new ]]; then
                printf partial > "${args[$final]}"
                return 1
            fi ;;
        mv|rm)
            local i
            for ((i = 2; i < ${#args[@]}; i++)); do
                [[ "${args[$i]}" = /* ]] && args[$i]="$FIXTURE_ROOT${args[$i]}"
            done
            # FAIL_MV=NAME: renaming .NAME.new into place fails.
            if [[ "$1" = mv && -n "${FAIL_MV:-}" && "${args[2]}" = */."$FAIL_MV".new ]]; then
                return 1
            fi ;;
        *) return 90 ;;
    esac
    "${args[@]}"
}
install_capture_portal
'''

    def test_standalone_installs_backend_without_changing_desktop_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            profile = target / "etc/wireplumber/wireplumber.conf.d/50pleb-video-only.conf"
            preferences = target / "etc/xdg-desktop-portal/pleb-portals.conf"
            for path in (profile, preferences):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("operator choice\n")
            self.install(target, False)
            self.assertEqual(profile.read_text(), "operator choice\n")
            self.assertEqual(preferences.read_text(), "operator choice\n")
            for module in ("capture_registry.py", "capture_sources.py", "capture_session.py", "capture_worker.py", "capture_screenshot.py", "capture_portal.py"):
                self.assertEqual((target / "usr/local/lib/pleb" / module).read_bytes(),
                                 (ROOT / "lib" / module).read_bytes())
            transport = ctypes.CDLL(str(target / "usr/local/lib/pleb/capture_transport.so"))
            for symbol in ("pleb_capture_open", "pleb_capture_push", "pleb_capture_close"):
                self.assertTrue(callable(getattr(transport, symbol)))
            service = target / "usr/local/share/dbus-1/services/org.freedesktop.impl.portal.desktop.pleb.service"
            self.assertIn("Exec=/usr/bin/python3 /usr/local/lib/pleb/capture_portal.py", service.read_text())
            shortcuts = target / "usr/local/share/dbus-1/services/org.freedesktop.impl.portal.desktop.pleb.shortcuts.service"
            self.assertIn("Exec=/usr/bin/python3 /usr/local/lib/pleb/global_shortcuts.py", shortcuts.read_text())
            self.assertEqual((target / "usr/local/share/xdg-desktop-portal/portals/pleb-shortcuts.portal").read_bytes(),
                             (ROOT / "share/portals/pleb-shortcuts.portal").read_bytes())

    def previous_backend(self, target):
        directory = target / "usr/local/lib/pleb"
        directory.mkdir(parents=True)
        names = ["capture_transport.so"] + self.shared_module_list()
        for name in names:
            (directory / name).write_bytes(b"previous installed " + name.encode() + b"\n")
        return directory, names

    def run_install(self, target, source, **extra):
        env = clean_env(target, PLEBIAN_OS_MANAGED_INSTALL="0", FIXTURE_ROOT=str(target),
                        PLEB_ROOT=str(source), CC="cc", **extra)
        return subprocess.run(["bash", "-c", self.install_script()], env=env, cwd=source,
                              capture_output=True, text=True)

    def assert_backend_untouched(self, directory, names):
        # The transport is checked too: capture_worker.py loads it through
        # ctypes, so a new .so beside the old modules is a mixed install.
        for name in names:
            self.assertEqual((directory / name).read_bytes(),
                             b"previous installed " + name.encode() + b"\n", name)
        self.assertEqual(sorted(p.name for p in directory.iterdir() if p.name.endswith(".new")), [])

    def test_failed_module_staging_leaves_the_installed_backend_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "root"
            source = Path(directory) / "pleb"
            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            modules, names = self.previous_backend(target)
            (source / "lib/capture_worker.py").unlink()  # staging fails part-way
            result = self.run_install(target, source)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assert_backend_untouched(modules, names)

    def test_a_partial_staged_module_is_removed_with_the_rest(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "root"
            modules, names = self.previous_backend(target)
            result = self.run_install(target, ROOT, FAIL_STAGE="capture_worker.py")
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Could not stage the capture backend module capture_worker.py", result.stderr)
            self.assert_backend_untouched(modules, names)

    def test_a_failed_rename_fails_the_install_and_leaves_nothing_staged(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "root"
            modules, names = self.previous_backend(target)
            failing = names[2]
            result = self.run_install(target, ROOT, FAIL_MV=failing)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Could not move the capture backend file " + failing, result.stderr)
            self.assertEqual(sorted(p.name for p in modules.iterdir() if p.name.endswith(".new")), [])
            # Everything after the failure keeps its installed version.
            for name in names[2:]:
                self.assertEqual((modules / name).read_bytes(),
                                 b"previous installed " + name.encode() + b"\n", name)
            self.assertFalse((target / "usr/local/share/xdg-desktop-portal/portals/pleb.portal").exists())

    def shared_module_list(self):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                ["bash", "-c", 'source lib/common.sh; source lib/install.sh; printf %s "$PLEB_CAPTURE_MODULES"'],
                env=clean_env(Path(home)), cwd=ROOT, capture_output=True, text=True, check=True)
        return result.stdout.split()

    def test_installed_modules_are_exactly_the_list_uninstall_removes(self):
        # Plebian-OS protects these same files in its root transactions, and
        # reads the list from here; a second hand-typed copy is how the lock
        # guard went missing from uninstall and from the distribution rollback.
        modules = self.shared_module_list()
        self.assertIn("capture_session.py", modules)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.install(target, False)
            installed = sorted(p.name for p in (target / "usr/local/lib/pleb").glob("*.py"))
        self.assertEqual(installed, sorted(modules))
        source = (ROOT / "lib/install.sh").read_text()
        uninstall = source[source.index("do_uninstall() {"):]
        uninstall = uninstall[:uninstall.index("\n}\n")]
        self.assertIn("for module in $PLEB_CAPTURE_MODULES; do", uninstall)
        self.assertNotIn("/usr/local/lib/pleb/capture_portal.py", uninstall)

    def test_distribution_installs_explicit_capture_and_video_only_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.install(target, True)
            profile = target / "etc/wireplumber/wireplumber.conf.d/50pleb-video-only.conf"
            self.assertIn("wireplumber.profile = video-only", profile.read_text())
            preferences = (target / "etc/xdg-desktop-portal/pleb-portals.conf").read_text()
            self.assertIn("org.freedesktop.impl.portal.ScreenCast=pleb", preferences)
            self.assertIn("org.freedesktop.impl.portal.Screenshot=pleb", preferences)
            self.assertIn("default=gtk", preferences)
            self.assertIn("org.freedesktop.impl.portal.GlobalShortcuts=pleb-shortcuts", preferences)

    def test_failed_transport_build_preserves_the_installed_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            paths = [target / "usr/local/lib/pleb" / name for name in
                     ("capture_transport.so", "capture_worker.py", "capture_portal.py")]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"previous installed backend\n")
            compiler = target / "failed-compiler"
            compiler.write_text("#!/bin/sh\nwhile [ $# -gt 0 ]; do\n"
                                "  if [ \"$1\" = -o ]; then printf partial > \"$2\"; exit 1; fi\n"
                                "  shift\ndone\nexit 1\n")
            compiler.chmod(0o755)
            self.install(target, False, compiler=str(compiler), success=False)
            for path in paths:
                self.assertEqual(path.read_bytes(), b"previous installed backend\n")


if __name__ == "__main__":
    unittest.main()
