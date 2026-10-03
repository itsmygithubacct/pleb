from pathlib import Path
import subprocess
import tempfile
import unittest

from _env_support import clean_env

ROOT = Path(__file__).resolve().parents[1]


class CaptureInstallTests(unittest.TestCase):
    def install(self, target, managed):
        env = clean_env(target, PLEBIAN_OS_MANAGED_INSTALL=str(int(managed)),
                        FIXTURE_ROOT=str(target), PLEB_ROOT=str(ROOT))
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
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
            for module in ("capture_sources.py", "capture_worker.py", "capture_portal.py"):
                self.assertEqual((target / "usr/local/lib/pleb" / module).read_bytes(),
                                 (ROOT / "lib" / module).read_bytes())
            service = target / "usr/local/share/dbus-1/services/org.freedesktop.impl.portal.desktop.pleb.service"
            self.assertIn("Exec=/usr/bin/python3 /usr/local/lib/pleb/capture_portal.py", service.read_text())

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


if __name__ == "__main__":
    unittest.main()
