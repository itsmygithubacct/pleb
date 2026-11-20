"""Exercise packaged and checkout updates through the Pleb CLI with offline apt stubs."""
import unittest

from test_packaged_mode import Fixture


PACKAGES = ("task-pleb-desktop", "pleb-desktop", "pleb", "kilix")
ROLLBACK = "install the previous release's frozen suite"
STUB = r"""#!/bin/sh
set -eu
name=${0##*/}
case "$name" in
    dpkg-query)
        [ "$#" = 4 ] && [ "$1" = -W ] && [ "$2" = -f ] && [ "$3" = '${db:Status-Status}' ] || exit 90
        case " $INSTALLED " in
            *" $4 "*) printf installed ;;
            *) [ "$4" = "${RESIDUAL:-pleb-desktop}" ] || exit 1; printf config-files ;;
        esac
        exit 0 ;;
    id)
        if [ "$*" = -u ] && [ -n "${TEST_UID:-}" ]; then echo "$TEST_UID"; exit 0; fi
        exec /usr/bin/id "$@" ;;
esac
echo "$name $*" >> "$CALLS"
case "$name" in
    sudo) [ "$1" = apt-get ] || exit 91; exec "$@" ;;
    apt-get) [ "$1" != "${FAIL_APT:-}" ] || exit 42 ;;
    apt-cache)
        [ "$1" = policy ] || exit 92
        [ "${FAIL_APT:-}" != policy ] || exit 42
        shift
        for package do
            printf '%s:\n  Installed: 0.2.4-1\n  Candidate: 0.2.5-1\n' "$package"
        done ;;
    *) exit 93 ;;
esac
"""


def install_stubs(directory):
    for command in ("dpkg-query", "id", "sudo", "apt-get", "apt-cache", "git", "pleb", "systemd-run", "systemctl"):
        path = directory / command
        path.write_text(STUB)
        path.chmod(0o755)


class PackagedAptUpdateTests(Fixture):
    def setUp(self):
        super().setUp()
        self.mark()
        install_stubs(self.stubs)
        self.checkout = self.base / "checkout"
        (self.checkout / "bin").mkdir(parents=True)
        # Record any install attempt through the old checkout's absolute launcher.
        (self.checkout / "bin/pleb").symlink_to(self.stubs / "pleb")

    def update(self, *args, installed=PACKAGES, **env):
        return self.pleb("update", *args, INSTALLED=" ".join(installed), CALLS=str(self.calls),
                         PLEB_DIR=str(self.checkout), **env)

    def test_reports_fresh_versions_and_upgrades_installed_members(self):
        for uid, installed in (("1000", PACKAGES), ("0", ("pleb", "kilix"))):
            with self.subTest(uid=uid):
                self.calls.unlink(missing_ok=True)
                result = self.update(installed=installed, TEST_UID=uid)
                self.assertEqual(result.returncode, 0, result.stderr)
                names = " ".join(installed)
                update = "apt-get update"
                policy = f"apt-cache policy {names}"
                upgrade = f"apt-get upgrade --only-upgrade {names}"
                if uid != "0":
                    calls = [f"sudo {update}", update, policy, f"sudo {upgrade}", upgrade]
                else:
                    calls = [update, policy, upgrade]
                self.assertEqual(self.privileged_calls(), calls)
                for package in installed:
                    self.assertIn(f"{package}:\n  Installed: 0.2.4-1\n  Candidate: 0.2.5-1", result.stdout)
                self.assertIn(ROLLBACK, result.stdout)
                self.assertIn("snapshot", result.stdout)
                self.assertFalse((self.home / ".local/gpu_terminal").exists())

    def test_source_check_recovery_and_unknown_flags_leave_snapshot_alone(self):
        snapshot = self.home / ".local/gpu_terminal/pleb/state/update-rollback.ABCDEF"
        snapshot.mkdir(parents=True)
        marker = snapshot / "kilix.head"
        marker.write_text("original\n")
        for args, success, message in (
            (("--check-source-checkouts",), True, "apt owns updates"),
            (("--recover-interrupted",), False, ROLLBACK),
            (("--rollback",), False, ROLLBACK),
            (("--typo",), False, "--typo"),
            (("--check-source-checkouts", "--typo"), False, "option"),
        ):
            with self.subTest(args=args):
                result = self.update(*args)
                self.assertEqual(result.returncode == 0, success, result.stderr)
                self.assertIn(message, result.stdout if success else result.stderr)
                self.assertEqual(self.privileged_calls(), [])
                self.assertEqual(marker.read_text(), "original\n")
                self.assertEqual(list(snapshot.parent.iterdir()), [snapshot])

    def test_empty_package_set_cannot_start_an_unqualified_upgrade(self):
        result = self.update(installed=())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No installed", result.stderr)
        self.assertEqual(self.privileged_calls(), [])

    def test_apt_errors_propagate(self):
        for step in ("update", "policy", "upgrade"):
            with self.subTest(step=step):
                self.calls.unlink(missing_ok=True)
                result = self.update(FAIL_APT=step, TEST_UID="0")
                self.assertEqual(result.returncode, 42, result.stderr)
                self.assertNotIn(ROLLBACK, result.stdout)
                command = "apt-cache" if step == "policy" else "apt-get"
                self.assertEqual(self.privileged_calls()[-1].split()[:2], [command, step])
                if step == "update":
                    self.assertEqual(self.privileged_calls(), ["apt-get update"])

    def test_yes_is_forwarded_to_apt(self):
        result = self.update("--yes", "--no-restart", installed=("pleb",), TEST_UID="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.privileged_calls(),
                         ["apt-get update", "apt-cache policy pleb", "apt-get upgrade --only-upgrade -y pleb"])

    def test_unmarked_install_enters_checkout_update_without_apt(self):
        (self.tree / "PACKAGED").unlink()
        result = self.update("--no-restart")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no kilix git checkout", result.stderr)
        self.assertEqual(self.privileged_calls(), [])


if __name__ == "__main__":
    unittest.main()
