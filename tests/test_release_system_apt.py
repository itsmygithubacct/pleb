"""Release mode on Plebian OS does not install through the system apt configuration.

The release closure was already installed. A package still missing is named and
left uninstalled. A stub apt that would record a third-party 2.0 install must
not run. Development, and release mode on a machine that is not Plebian OS,
keep today's apt-get command.
"""
import os
import subprocess
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import clean_env  # noqa: E402

THIRD = "third-party-libdep-sim-2.0"


def _write(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


class ReleaseSystemAptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = __import__("tempfile").TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.record = self.base / "apt-record"
        self.installed = self.base / "installed"
        self.marker = self.base / "build-info.env"
        self.marker.write_text("PLEBIAN_OS_VERSION=0.2.3\n")
        self.stubs = self.base / "stubs"
        self.stubs.mkdir()
        _write(self.stubs / "apt-get", "#!/bin/sh\nexit 97\n")
        _write(self.stubs / "dpkg-query", textwrap.dedent("""\
            #!/bin/sh
            pkg=
            for arg in "$@"; do pkg=$arg; done
            case "$pkg" in
              libdep-sim|mbrola|mbrola-us1) exit 1 ;;
            esac
            printf 'install ok installed\\n'
            """))

    def tearDown(self):
        self._tmp.cleanup()

    def run_install(self, script: str, **env):
        base_env = clean_env(
            self.base,
            PATH=f"{self.stubs}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
            PLEBIAN_OS_MACHINE_MARKER=str(self.marker),
            PLEB_ENV_SYSTEM=str(self.base / "absent-system.env"),
            PLEB_ENV_USER=str(self.base / "absent-user.env"),
            PLEB_CLOSURE_SYSTEM=str(self.base / "absent-closure-system.env"),
            PLEB_CLOSURE_USER=str(self.base / "absent-closure-user.env"),
        )
        base_env.update(env)
        body = textwrap.dedent(f"""\
            set -euo pipefail
            PLEB_ROOT={str(ROOT)!r}
            . "$PLEB_ROOT/lib/common.sh"
            . "$PLEB_ROOT/lib/install.sh"
            run_root() {{
                printf '%s\\n' "$*" >> {str(self.record)!r}
                case "$*" in
                    *apt-get*install*) printf '%s\\n' {THIRD!r} >> {str(self.installed)!r} ;;
                esac
            }}
            {script}
            """)
        return subprocess.run(
            ["bash", "-c", body], env=base_env, text=True, capture_output=True)

    def test_a_missing_package_is_refused_and_the_third_party_version_is_not_used(self):
        result = self.run_install(
            '_install_missing_apt_packages "fixture" libdep-sim',
            PLEBIAN_OS_RELEASE_MODE="1",
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("release package closure is incomplete; missing libdep-sim", result.stderr)
        self.assertIn("the system apt configuration was not used", result.stderr)
        self.assertFalse(self.installed.exists(), result.stderr)
        self.assertNotIn("apt-get", self.record.read_text() if self.record.exists() else "")
        self.assertNotIn(THIRD, result.stdout + result.stderr)

    def test_an_installed_package_is_left_at_its_current_version(self):
        _write(self.stubs / "dpkg-query", "#!/bin/sh\nprintf 'install ok installed\\n'\n")
        result = self.run_install(
            '_install_missing_apt_packages "fixture" libdep-sim',
            PLEBIAN_OS_RELEASE_MODE="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already installed", result.stdout)
        self.assertFalse(self.installed.exists())
        self.assertNotIn(THIRD, result.stdout + result.stderr)

    def test_development_mode_still_uses_the_system_apt_command(self):
        result = self.run_install(
            '_install_missing_apt_packages "fixture" libdep-sim',
            PLEBIAN_OS_RELEASE_MODE="0",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = self.record.read_text()
        self.assertIn("apt-get update", recorded)
        self.assertIn("apt-get install -y --no-install-recommends libdep-sim", recorded)
        self.assertNotIn("--no-remove", recorded)
        self.assertNotIn("APT_CONFIG", recorded)
        self.assertEqual(self.installed.read_text().strip(), THIRD)

    def test_release_mode_off_a_plebian_machine_keeps_the_system_apt_command(self):
        missing = self.base / "not-a-machine"
        result = self.run_install(
            '_install_missing_apt_packages "fixture" libdep-sim',
            PLEBIAN_OS_RELEASE_MODE="1",
            PLEBIAN_OS_MACHINE_MARKER=str(missing),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("apt-get install -y --no-install-recommends libdep-sim", self.record.read_text())
        self.assertEqual(self.installed.read_text().strip(), THIRD)

    def test_a_symlink_marker_does_not_count_as_the_installed_machine(self):
        link = self.base / "marker-link"
        link.symlink_to(self.marker)
        result = self.run_install(
            '_install_missing_apt_packages "fixture" libdep-sim',
            PLEBIAN_OS_RELEASE_MODE="1",
            PLEBIAN_OS_MACHINE_MARKER=str(link),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("apt-get install -y --no-install-recommends", self.record.read_text())

    def test_the_environment_marker_wins_over_a_session_file(self):
        session = self.base / "session.env"
        session.write_text("PLEBIAN_OS_MACHINE_MARKER=/no/such/build-info.env\n")
        result = self.run_install(
            '_install_missing_apt_packages "fixture" libdep-sim',
            PLEBIAN_OS_RELEASE_MODE="1",
            PLEB_ENV_USER=str(session),
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("missing libdep-sim", result.stderr)
        self.assertFalse(self.installed.exists())

    def test_optional_mbrola_stays_uninstalled_without_failing_the_runtime_closure(self):
        result = self.run_install(
            "ensure_system_deps",
            PLEBIAN_OS_RELEASE_MODE="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("missing mbrola mbrola-us1", result.stderr)
        self.assertIn("the system apt configuration was not used", result.stderr)
        self.assertIn("mbrola voices unavailable", result.stderr)
        self.assertFalse(self.installed.exists())

    def test_kilix_installer_inherits_the_pinned_marker(self):
        kilix = self.base / "kilix"
        scripts = kilix / "scripts"
        scripts.mkdir(parents=True)
        calls = self.base / "calls"
        ready = self.base / "ready"
        _write(scripts / "install-build-deps.sh", textwrap.dedent(f"""\
            #!/bin/sh
            printf '%s marker=%s mode=%s\\n' "${{1:-install}}" \\
                "${{PLEBIAN_OS_MACHINE_MARKER-}}" "${{PLEBIAN_OS_RELEASE_MODE-}}" >> {calls!s}
            if [ "${{1:-}}" = --verify ]; then
                [ -f {ready!s} ]
                exit $?
            fi
            : > {ready!s}
            """))
        result = self.run_install(
            "ensure_kilix_build_deps",
            PLEBIAN_OS_RELEASE_MODE="1",
            KILIX_DIR=str(kilix),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = calls.read_text()
        self.assertIn(f"install marker={self.marker} mode=1", text)
        self.assertNotIn("/no/such/build-info.env", text)


if __name__ == "__main__":
    unittest.main()
