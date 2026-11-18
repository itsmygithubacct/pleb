"""A standalone machine that never selected a Plebian-OS release.

Its first `pleb update --to` has no closure to fall back to. Rolling that
selection back, by `--rollback` or after a failed stack apply, must return the
machine to no closure AND to the commits its checkouts were on: an unpinned
`pleb update` would instead move every checkout to its branch head.

The real release-hop driver runs here. The target selector and the stack
updater (`$PLEB_ROOT/bin/pleb update`) are stand-ins that record what they are
asked to do.
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import clean_env  # noqa: E402

GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="pleb tests", GIT_AUTHOR_EMAIL="tests@example.invalid",
    GIT_COMMITTER_NAME="pleb tests", GIT_COMMITTER_EMAIL="tests@example.invalid",
)


def git(*argv, cwd=None):
    return subprocess.run(
        ["git", *argv], cwd=None if cwd is None else str(cwd), check=True,
        text=True, capture_output=True, env=GIT_ENV,
    ).stdout.strip()


# Simulates the target selector's split-closure contract: one generation of
# closure.env, and a first selection whose rollback leaves no closure.
SELECTOR = textwrap.dedent("""\
    #!/usr/bin/env bash
    set -euo pipefail
    printf '%s mode=%s\\n' "$*" "$PLEBIAN_OS_SELECTOR_MODE" >>"$PLEB_TEST_SELECTOR_LOG"
    closure="$PLEBIAN_OS_CLOSURE_ENV"
    if [ "${1:-}" = --rollback ]; then
        if [ -e "$closure.prev" ]; then mv -f "$closure.prev" "$closure"; else rm -f "$closure"; fi
        exit 0
    fi
    for arg; do [ "$arg" != --dry-run ] || exit 0; done
    if [ -e "$closure" ]; then cp "$closure" "$closure.prev"; else rm -f "$closure.prev"; fi
    printf 'PLEBIAN_OS_VERSION=%s\\nPLEBIAN_OS_RELEASE_MODE=1\\n' "$1" >"$closure"
    """)

# Stands in for `$PLEB_ROOT/bin/pleb update`: with a closure it moves each
# checkout to its "selected" commit; given a pin it checks that out; with
# neither it does what an unpinned update does and moves to the branch head.
STACK = textwrap.dedent("""\
    #!/usr/bin/env bash
    set -euo pipefail
    closure=no; [ ! -e "$PLEB_CLOSURE_USER" ] || closure=yes
    printf '%s closure=%s PLEB_REF=%s KILIX_REF=%s KILIX95_REF=%s\\n' "$*" "$closure" \\
        "${PLEB_REF:-}" "${KILIX_REF:-}" "${KILIX95_REF:-}" >>"$PLEB_TEST_STACK_LOG"
    if [ "$closure" = yes ] && [ "${PLEB_TEST_APPLY_FAIL:-0}" = 1 ]; then exit 7; fi
    move() {
        local dir="$1" pin="$2"
        [ -d "$dir/.git" ] || return 0
        if [ -n "$pin" ]; then git -C "$dir" checkout -q --detach "$pin"
        elif [ "$closure" = yes ]; then git -C "$dir" checkout -q --detach selected
        else git -C "$dir" checkout -q --detach drift; fi
    }
    move "$PLEB_TEST_PLEB" "${PLEB_REF:-}"
    move "$PLEB_TEST_KILIX" "${KILIX_REF:-}"
    move "$PLEB_TEST_KILIX95" "${KILIX95_REF:-}"
    """)


class StandaloneFirstSelectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = None
        self._fresh()

    def _fresh(self):
        """A new, empty machine directory (one per subtest)."""
        if self._tmp is not None:
            self._tmp.cleanup()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _remote(self, versions=("0.2.0", "0.2.1", "0.2.2")):
        remote = self.base / "plebian-os.git"
        work = self.base / "release-work"
        git("init", "-q", "--bare", str(remote))
        git("init", "-q", "-b", "main", str(work))
        git("remote", "add", "origin", str(remote), cwd=work)
        (work / "provision").mkdir()
        (work / "releases").mkdir()
        selector = work / "provision/plebian-os-select-closure.sh"
        selector.write_text(SELECTOR)
        selector.chmod(0o755)
        (work / "releases/upgrade-policy.json").write_text(
            '{"schema_version": 1, "supported_upgrade_paths": []}\n')
        for version in versions:
            (work / "VERSION").write_text(version + "\n")
            (work / "releases" / f"{version}.env").write_text(
                f"PLEBIAN_OS_VERSION={version}\n")
            git("add", "-A", cwd=work)
            git("commit", "-qm", f"release {version}", cwd=work)
            git("tag", "-a", f"v{version}", "-m", f"v{version}", cwd=work)
        git("push", "-q", "origin", "HEAD:main", "--tags", cwd=work)
        return remote

    def _gitconfig(self):
        config = self.base / "gitconfig"
        config.write_text(
            f'[url "file://{self._remote()}"]\n'
            "\tinsteadOf = https://github.com/itsmygithubacct/plebian-os.git\n")
        return config

    def _checkout(self, path: Path, version=None):
        """A checkout on main, with 'selected' and 'drift' commits elsewhere."""
        path.mkdir(parents=True)
        git("init", "-q", "-b", "main", str(path))
        if version is not None:
            (path / "VERSION").write_text(version + "\n")
        (path / "state").write_text("installed\n")
        git("add", "-A", cwd=path)
        git("commit", "-qm", "installed", cwd=path)
        installed = git("rev-parse", "HEAD", cwd=path)
        for name in ("selected", "drift"):
            git("checkout", "-q", "--detach", installed, cwd=path)
            (path / "state").write_text(name + "\n")
            git("commit", "-qam", name, cwd=path)
            git("tag", name, cwd=path)
        git("checkout", "-q", "main", cwd=path)
        return installed

    def _machine(self, pleb_version="0.2.1", session_text=None, kilix95=False):
        home = self.base / "home"
        data = home / ".local/gpu_terminal"
        sources = data / "sources"
        storage = data / "pleb"
        config = storage / "config"
        config.mkdir(parents=True)
        self.session = config / "session.env"
        self.closure = config / "closure.env"
        if session_text is not None:
            self.session.write_text(session_text)
        self.pleb = sources / "pleb"
        self.kilix = sources / "kilix"
        self.kilix95 = sources / "kilix-desktops/kilix-95"
        self.installed = {
            "pleb": self._checkout(self.pleb, pleb_version),
            "kilix": self._checkout(self.kilix),
        }
        (self.pleb / "bin").mkdir()
        (self.pleb / "bin/pleb").write_text(STACK)
        (self.pleb / "bin/pleb").chmod(0o755)
        git("add", "-A", cwd=self.pleb)
        git("commit", "-qm", "stack stand-in", cwd=self.pleb)
        self.installed["pleb"] = git("rev-parse", "HEAD", cwd=self.pleb)
        for name in ("selected", "drift"):
            git("tag", "-f", name, "HEAD", cwd=self.pleb)
        if kilix95:
            self.installed["kilix95"] = self._checkout(self.kilix95)
        self.state = storage / "state"
        self.selector_log = self.base / "selector.log"
        self.stack_log = self.base / "stack.log"
        self.env = clean_env(
            home,
            PATH=os.environ["PATH"], LANG="C", TMPDIR=str(self.base),
            GPU_TERMINAL_HOME=str(data), GPU_TERMINAL_SOURCE_HOME=str(sources),
            PLEB_STORAGE_HOME=str(storage), PLEB_CONFIG_HOME=str(config),
            PLEB_STATE_HOME=str(self.state), PLEB_CACHE_HOME=str(storage / "cache"),
            PLEB_SESSION_HOME=str(storage / "session"), PLEB_DATA_HOME=str(storage / "data"),
            PLEB_ENV_SYSTEM=str(self.base / "absent-system-session.env"),
            PLEB_ENV_USER=str(self.session),
            PLEB_CLOSURE_SYSTEM=str(self.base / "absent-system-closure.env"),
            PLEB_CLOSURE_USER=str(self.closure),
            PLEB_ROOT=str(self.pleb), PLEB_DIR=str(self.pleb),
            KILIX_DIR=str(self.kilix), KILIX95_DIR=str(self.kilix95),
            # Never-selected: no closure names the repository, so the default
            # URL is used. Point that URL at the fixture remote.
            GIT_CONFIG_GLOBAL=str(self._gitconfig()),
            PLEB_TEST_SELECTOR_LOG=str(self.selector_log),
            PLEB_TEST_STACK_LOG=str(self.stack_log),
            PLEB_TEST_PLEB=str(self.pleb), PLEB_TEST_KILIX=str(self.kilix),
            PLEB_TEST_KILIX95=str(self.kilix95),
        )
        self.env.pop("PLEBIAN_OS_MANAGED_INSTALL", None)   # standalone

    def _pleb(self, *args, **extra):
        script = ('set -euo pipefail\n'
                  f'. {str(ROOT / "lib/common.sh")!r}\n'
                  f'. {str(ROOT / "lib/update.sh")!r}\n'
                  'do_update "$@"\n')
        return subprocess.run(["bash", "-c", script, "pleb", *args],
                              env=dict(self.env, **extra), text=True,
                              capture_output=True, check=False)

    def _position(self, path):
        branch = subprocess.run(["git", "-C", str(path), "symbolic-ref", "-q", "--short", "HEAD"],
                                text=True, capture_output=True).stdout.strip()
        return git("rev-parse", "HEAD", cwd=path), branch

    def _assert_unselected(self, session_before):
        self.assertFalse(self.closure.exists())
        self.assertEqual(self.session.read_bytes() if self.session.exists() else None,
                         session_before)
        self.assertEqual(self._position(self.pleb), (self.installed["pleb"], "main"))
        self.assertEqual(self._position(self.kilix), (self.installed["kilix"], "main"))
        self.assertFalse((self.state / "release-hop/unselected-checkouts").exists())

    def _stack_calls(self):
        return self.stack_log.read_text().splitlines()

    def test_first_selection_records_where_the_checkouts_were(self):
        for session in (None, "PLEB_RESPAWN=0\n"):
            with self.subTest(session=session):
                self._fresh()
                self._machine(session_text=session)
                result = self._pleb("--to", "0.2.2", "--no-restart")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("mode=standalone", self.selector_log.read_text())
                self.assertEqual(self.closure.read_text().splitlines()[0],
                                 "PLEBIAN_OS_VERSION=0.2.2")
                record = (self.state / "release-hop/unselected-checkouts").read_text()
                self.assertEqual(record.splitlines(), [
                    f"PLEB_REF\t{self.installed['pleb']}\tmain",
                    f"KILIX_REF\t{self.installed['kilix']}\tmain",
                    "KILIX95_REF\tabsent\t-",
                ])
                self.assertEqual(self._stack_calls(),
                                 ["update --no-restart closure=yes PLEB_REF= KILIX_REF= KILIX95_REF="])
                self.assertEqual(git("rev-parse", "HEAD", cwd=self.kilix),
                                 git("rev-parse", "selected", cwd=self.kilix))

    def test_rollback_returns_to_no_closure_and_the_recorded_commits(self):
        for session in (None, "PLEB_RESPAWN=0\n"):
            with self.subTest(session=session):
                self._fresh()
                self._machine(session_text=session)
                before = self.session.read_bytes() if self.session.exists() else None
                self.assertEqual(self._pleb("--to", "0.2.2", "--no-restart").returncode, 0)
                rolled = self._pleb("--rollback", "--no-restart")
                self.assertEqual(rolled.returncode, 0, rolled.stderr)
                self.assertIn("restoring the never-selected stack", rolled.stdout + rolled.stderr)
                self.assertEqual(
                    self._stack_calls()[-1],
                    f"update --no-restart closure=no PLEB_REF={self.installed['pleb']} "
                    f"KILIX_REF={self.installed['kilix']} KILIX95_REF=")
                self._assert_unselected(before)
                self.assertFalse((self.state / "release-hop/active").exists())

    def test_failed_stack_apply_restores_no_closure_and_the_recorded_commits(self):
        for session in (None, "PLEB_RESPAWN=0\n"):
            with self.subTest(session=session):
                self._fresh()
                self._machine(session_text=session)
                before = self.session.read_bytes() if self.session.exists() else None
                failed = self._pleb("--to", "0.2.2", "--no-restart", PLEB_TEST_APPLY_FAIL="1")
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn("target stack apply failed with status 7", failed.stderr)
                self.assertIn("previous closure and stack were restored", failed.stderr)
                self.assertIn("--rollback", self.selector_log.read_text().splitlines()[-1])
                self._assert_unselected(before)
                self.assertFalse((self.state / "release-hop/active").exists())

    def test_a_branch_that_moved_meanwhile_is_left_detached_on_the_commit(self):
        self._machine()
        self.assertEqual(self._pleb("--to", "0.2.2", "--no-restart").returncode, 0)
        git("branch", "-f", "main", "drift", cwd=self.kilix)
        rolled = self._pleb("--rollback", "--no-restart")
        self.assertEqual(rolled.returncode, 0, rolled.stderr)
        self.assertEqual(self._position(self.kilix), (self.installed["kilix"], ""))
        self.assertIn("branch main has moved; left detached", rolled.stderr)

    def test_a_provider_the_selection_installed_is_reported_not_removed(self):
        self._machine()
        self.assertEqual(self._pleb("--to", "0.2.2", "--no-restart").returncode, 0)
        self._checkout(self.kilix95)   # what the selected stack installed
        rolled = self._pleb("--rollback", "--no-restart")
        self.assertEqual(rolled.returncode, 0, rolled.stderr)
        self.assertIn("did not exist before the selection and is left in place", rolled.stderr)
        self.assertTrue((self.kilix95 / ".git").exists())

    def test_repeat_selection_is_an_ordinary_hop_with_no_unselected_record(self):
        self._machine()
        self.assertEqual(self._pleb("--to", "0.2.2", "--no-restart").returncode, 0)
        self.assertEqual(self._pleb("--rollback", "--no-restart").returncode, 0)
        # Select again from the restored unselected machine.
        again = self._pleb("--to", "0.2.2", "--no-restart")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertTrue((self.state / "release-hop/unselected-checkouts").exists())
        # Now a closure exists: reselecting records no unselected checkouts,
        # and its rollback reconciles from the restored pins without overrides.
        reselect = self._pleb("--to", "0.2.2", "--no-restart")
        self.assertEqual(reselect.returncode, 0, reselect.stderr)
        self.assertFalse((self.state / "release-hop/unselected-checkouts").exists())
        rolled = self._pleb("--rollback", "--no-restart")
        self.assertEqual(rolled.returncode, 0, rolled.stderr)
        self.assertTrue(self.closure.exists())
        self.assertEqual(self._stack_calls()[-1],
                         "update --no-restart closure=yes PLEB_REF= KILIX_REF= KILIX95_REF=")

    def test_installed_release_of_a_never_selected_machine_is_its_pleb_version(self):
        # Adjacent hops are judged from the Pleb checkout's VERSION.
        self._machine(pleb_version="0.2.0")
        refused = self._pleb("--to", "0.2.2", "--dry-run")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("unsupported release skip 0.2.0 -> 0.2.2", refused.stderr)
        self.assertIn("pleb update --to 0.2.1", refused.stderr)

    def test_unknown_pleb_version_is_refused_with_its_reason(self):
        self._machine(pleb_version="not-a-release")
        refused = self._pleb("--to", "0.2.2", "--dry-run")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("no closure is selected and", refused.stderr)
        self.assertIn("names no release", refused.stderr)

    def test_latest_selects_the_release_a_never_selected_pleb_declares(self):
        self._machine(pleb_version="0.2.2")
        result = self._pleb("--latest", "--no-restart")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("already the latest", result.stdout + result.stderr)
        self.assertEqual(self.closure.read_text().splitlines()[0], "PLEBIAN_OS_VERSION=0.2.2")
        again = self._pleb("--latest", "--no-restart")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("release 0.2.2 is already the latest published release", again.stdout)


if __name__ == "__main__":
    unittest.main()
