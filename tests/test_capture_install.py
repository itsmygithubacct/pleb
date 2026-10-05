from pathlib import Path
import ctypes
import subprocess
import tempfile
import unittest

from _env_support import clean_env

ROOT = Path(__file__).resolve().parents[1]


class CaptureInstallTests(unittest.TestCase):
    def install(self, target, managed, *, compiler="cc", success=True):
        env = clean_env(target, PLEBIAN_OS_MANAGED_INSTALL=str(int(managed)),
                        FIXTURE_ROOT=str(target), PLEB_ROOT=str(ROOT), CC=compiler)
        script = r'''
set -euo pipefail
source "$PLEB_ROOT/lib/common.sh"
source "$PLEB_ROOT/lib/install.sh"
run_root() {
    [ "$1" = install ] || return 90
    local -a args=("$@")
    local final=$((${#args[@]} - 1))
    [[ "${args[$final]}" = /* ]] || return 91
    args[$final]="$FIXTURE_ROOT${args[$final]}"
    "${args[@]}"
}
install_capture_portal
'''
        result = subprocess.run(["bash", "-c", script], env=env, cwd=ROOT, capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

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
