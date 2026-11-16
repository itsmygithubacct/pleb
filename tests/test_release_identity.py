"""A recorded release candidate reaches its published final; nothing else moves."""

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLEB = ROOT / "bin/pleb"

# Stands in for the target tag's selector. Like the real one it writes the
# selected identity into the closure it is given and keeps one previous closure
# for --rollback; it logs each call and the tag object it was asked to trust.
SELECTOR = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail
    printf '%s|%s\\n' "$*" "${PLEBIAN_OS_TRUSTED_TAG_OBJECT_SHA:-}" >>"$PLEB_TEST_SELECTOR_LOG"
    closure="$PLEBIAN_OS_CLOSURE_ENV"
    if [ "${1:-}" = --rollback ]; then
        [ "${PLEB_TEST_ROLLBACK_STATUS:-0}" = 0 ] || exit "$PLEB_TEST_ROLLBACK_STATUS"
        [ -f "$closure.prev" ] && mv -f -- "$closure.prev" "$closure"
        exit 0
    fi
    if [ "${PLEB_TEST_SELECTOR_KILL:-0}" = 1 ]; then
        kill -KILL "$PPID"
        exit 9
    fi
    target="$1" tag="v$1" dry=0
    shift
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --release-tag) tag="$2"; shift 2 ;;
            --dry-run) dry=1; shift ;;
            *) shift ;;
        esac
    done
    [ "$dry" = 0 ] || exit 0
    cp -- "$closure" "$closure.prev"
    {
        printf 'PLEBIAN_OS_VERSION=%s\\n' "$target"
        grep '^PLEBIAN_OS_REPO=' "$closure.prev" || true
        printf 'PLEBIAN_OS_RELEASE_TAG=%s\\n' "$tag"
        printf 'PLEBIAN_OS_RELEASE_TAG_OBJECT=%s\\n' "$PLEBIAN_OS_TRUSTED_TAG_OBJECT_SHA"
    } >"$closure"
    """
)


class ReleaseIdentityTests(unittest.TestCase):
    def _git(self, work, *args):
        return subprocess.run(["git", "-C", str(work), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def _remote(self, base: Path, tags) -> Path:
        """Publish each (version, tag) in order, one commit per tag."""
        remote = base / "plebian-os.git"
        work = base / "release-work"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        self._git(work, "config", "user.name", "release test")
        self._git(work, "config", "user.email", "release-test@example.invalid")
        self._git(work, "remote", "add", "origin", str(remote))
        (work / "provision").mkdir()
        (work / "releases").mkdir()
        (work / "releases/upgrade-policy.json").write_text(
            '{"schema_version": 1, "supported_upgrade_paths": []}\n')
        script = work / "provision/plebian-os-select-closure.sh"
        script.write_text(SELECTOR)
        script.chmod(0o755)
        for version, tag in tags:
            (work / "VERSION").write_text(version + "\n")
            (work / "releases" / f"{version}.env").write_text(
                f"PLEBIAN_OS_VERSION={version}\nPLEBIAN_OS_REF={tag}\n")
            self._git(work, "add", "-A")
            self._git(work, "commit", "-qm", f"release {tag}")
            self._git(work, "tag", "-a", tag, "-m", tag)
        self._git(work, "push", "-q", "origin", "HEAD:main", "--tags")
        return remote

    def _object(self, base: Path, tag: str) -> str:
        return self._git(base / "release-work", "rev-parse", f"refs/tags/{tag}")

    def _env(self, base: Path, remote: Path, closure_text: str) -> dict[str, str]:
        home = base / "home"
        data = home / ".local/gpu_terminal"
        storage = data / "pleb"
        config = storage / "config"
        sources = data / "sources"
        for path in (config, sources / "kilix", sources / "kilix-desktops/kilix-95"):
            path.mkdir(parents=True, exist_ok=True)
        (config / "session.env").write_text("PLEB_RESPAWN=0\n")
        closure = base / "closure.env"
        closure.write_text(f"PLEBIAN_OS_REPO={remote}\n" + closure_text)
        updater = base / "plebian-os-update"
        marker = base / "updater-failed-once"
        updater.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >>{str(base / 'updater.log')!r}\n"
            "if [ \"${PLEB_TEST_UPDATER_FAIL_ONCE:-0}\" = 1 ] "
            f"&& [ ! -e {str(marker)!r} ]; then\n"
            f"  : >{str(marker)!r}\n"
            "  exit 7\n"
            "fi\n"
        )
        updater.chmod(0o755)
        return {
            "HOME": str(home),
            "PATH": os.environ["PATH"],
            "LANG": "C",
            "TMPDIR": str(base),
            "GPU_TERMINAL_HOME": str(data),
            "GPU_TERMINAL_SOURCE_HOME": str(sources),
            "PLEB_STORAGE_HOME": str(storage),
            "PLEB_CONFIG_HOME": str(config),
            "PLEB_STATE_HOME": str(storage / "state"),
            "PLEB_CACHE_HOME": str(storage / "cache"),
            "PLEB_SESSION_HOME": str(storage / "session"),
            "PLEB_DATA_HOME": str(storage / "data"),
            "PLEB_ENV_SYSTEM": str(base / "absent-system-session.env"),
            "PLEB_ENV_USER": str(config / "session.env"),
            "PLEB_CLOSURE_SYSTEM": str(closure),
            "PLEB_CLOSURE_USER": str(base / "absent-user-closure.env"),
            "KILIX_DIR": str(sources / "kilix"),
            "KILIX95_DIR": str(sources / "kilix-desktops/kilix-95"),
            "PLEBIAN_OS_MANAGED_INSTALL": "1",
            "PLEBIAN_OS_SELECTOR_DST": str(base / "plebian-os-select-closure"),
            "PLEBIAN_OS_UPDATER_DST": str(updater),
            "PLEB_TEST_SELECTOR_LOG": str(base / "selector.log"),
        }

    def _recorded(self, version, tag, obj):
        return (f"PLEBIAN_OS_VERSION={version}\n"
                f"PLEBIAN_OS_RELEASE_TAG={tag}\n"
                f"PLEBIAN_OS_RELEASE_TAG_OBJECT={obj}\n")

    def _pleb(self, env, *args):
        return subprocess.run([str(PLEB), *args], env=env, capture_output=True,
                              text=True, check=False)

    def _report(self, env):
        command = (
            'set -euo pipefail; PLEB_ROOT="$1"; . "$PLEB_ROOT/lib/common.sh"; '
            '. "$PLEB_ROOT/lib/closure.sh"; pleb_report_newer_release'
        )
        return subprocess.run(["bash", "-c", command, "bash", str(ROOT)], env=env,
                              capture_output=True, text=True, check=False)

    def _calls(self, base):
        log = base / "selector.log"
        return log.read_text().splitlines() if log.exists() else []

    FINAL = (("0.2.0", "v0.2.0"), ("0.2.1", "v0.2.1-rc1"), ("0.2.1", "v0.2.1"))
    CANDIDATES = (("0.2.0", "v0.2.0"), ("0.2.1", "v0.2.1-rc1"), ("0.2.1", "v0.2.1-rc2"))

    def test_recorded_candidate_moves_to_published_final_by_the_tag_selector(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._remote(base, self.FINAL)
            rc, final = self._object(base, "v0.2.1-rc1"), self._object(base, "v0.2.1")
            env = self._env(base, remote, self._recorded("0.2.1", "v0.2.1-rc1", rc))
            report = self._report(env)
            self.assertEqual(report.returncode, 0, report.stderr)
            self.assertIn("final release v0.2.1 is published; this machine runs "
                          "release candidate v0.2.1-rc1", report.stderr)
            self.assertIn("pleb update --latest", report.stderr)

            dry = self._pleb(env, "update", "--latest", "--dry-run")
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertIn("release candidate v0.2.1-rc1 is installed and final "
                          "release v0.2.1 is published", dry.stdout)
            args, trusted = self._calls(base)[-1].split("|")
            self.assertTrue(args.startswith("0.2.1 --source "), args)
            self.assertNotIn("--release-tag", args)
            self.assertEqual(trusted, final)

            moved = self._pleb(env, "update", "--latest", "--no-restart")
            self.assertEqual(moved.returncode, 0, moved.stderr)
            self.assertIn("release hop v0.2.1-rc1 -> v0.2.1 completed", moved.stdout)
            self.assertEqual((base / "updater.log").read_text().splitlines(),
                             ["--revalidate-current"])
            closure = (base / "closure.env").read_text()
            self.assertIn("PLEBIAN_OS_RELEASE_TAG=v0.2.1\n", closure)
            self.assertIn(f"PLEBIAN_OS_RELEASE_TAG_OBJECT={final}\n", closure)
            active = Path(env["PLEB_STATE_HOME"]) / "release-hop/active"
            self.assertEqual(active.read_text().splitlines()[2:], [
                final, active.read_text().splitlines()[3], "v0.2.1"])
            trust = Path(env["PLEB_CACHE_HOME"]) / "release-hop/tag-trust/v0.2.1"
            self.assertEqual(trust.read_text().strip(), final)

            # Now on the final: neither the report nor --latest moves again.
            calls = len(self._calls(base))
            again = self._pleb(env, "update", "--latest", "--dry-run")
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("release 0.2.1 is already the latest published release", again.stdout)
            self.assertEqual(len(self._calls(base)), calls)
            self.assertIn("is the newest published release", self._report(env).stdout)

    def test_offline_latest_uses_only_the_cached_trusted_final(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._remote(base, self.FINAL)
            rc, final = self._object(base, "v0.2.1-rc1"), self._object(base, "v0.2.1")
            env = self._env(base, remote, self._recorded("0.2.1", "v0.2.1-rc1", rc))
            # Nothing cached yet: offline has no final to trust and stays put.
            empty = self._pleb(env, "update", "--latest", "--dry-run", "--offline")
            self.assertNotEqual(empty.returncode, 0)
            self.assertEqual(self._calls(base), [])
            self.assertEqual(self._pleb(env, "update", "--latest", "--dry-run").returncode, 0)
            offline = self._pleb(env, "update", "--latest", "--dry-run", "--offline")
            self.assertEqual(offline.returncode, 0, offline.stderr)
            args, trusted = self._calls(base)[-1].split("|")
            self.assertIn("--offline", args)
            self.assertEqual(trusted, final)

    def test_installed_final_never_moves_back_to_a_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._remote(base, self.FINAL + (("0.2.2", "v0.2.2-rc1"),))
            final = self._object(base, "v0.2.1")
            env = self._env(base, remote, self._recorded("0.2.1", "v0.2.1", final))
            result = self._pleb(env, "update", "--latest", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("release 0.2.1 is already the latest published release", result.stdout)
            self.assertEqual(self._calls(base), [])
            report = self._report(env)
            self.assertIn("installed release 0.2.1 is the newest published release", report.stdout)
            self.assertNotIn("candidate", report.stdout + report.stderr)

    def test_candidate_with_only_candidates_published_stays_unless_named(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._remote(base, self.CANDIDATES)
            rc1 = self._object(base, "v0.2.1-rc1")
            env = self._env(base, remote, self._recorded("0.2.1", "v0.2.1-rc1", rc1))
            original = (base / "closure.env").read_bytes()
            result = self._pleb(env, "update", "--latest", "--no-restart")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no final release 0.2.1 or later is published, so it stays selected",
                          result.stdout)
            self.assertEqual(self._calls(base), [])
            self.assertEqual((base / "closure.env").read_bytes(), original)
            report = self._report(env)
            self.assertEqual(report.returncode, 0, report.stderr)
            self.assertIn("no final release 0.2.1 is published yet", report.stdout)
            self.assertNotIn("newer published release", report.stderr)
            # A later candidate is reached only when it is named.
            named = self._pleb(env, "update", "--to", "0.2.1", "--release-tag",
                               "v0.2.1-rc2", "--dry-run")
            self.assertEqual(named.returncode, 0, named.stderr)
            args, trusted = self._calls(base)[-1].split("|")
            self.assertIn("--release-tag v0.2.1-rc2", args)
            self.assertEqual(trusted, self._object(base, "v0.2.1-rc2"))

    def test_unrecorded_closure_keeps_the_numeric_behaviour(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._remote(base, self.FINAL)
            env = self._env(base, remote, "PLEBIAN_OS_VERSION=0.2.1\n")
            result = self._pleb(env, "update", "--latest", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("release 0.2.1 is already the latest published release", result.stdout)
            self.assertEqual(self._calls(base), [])
            self.assertIn("installed release 0.2.1 is the newest published release",
                          self._report(env).stdout)
            # The documented route for such a machine: name the final.
            named = self._pleb(env, "update", "--to", "0.2.1", "--dry-run")
            self.assertEqual(named.returncode, 0, named.stderr)
            args, trusted = self._calls(base)[-1].split("|")
            self.assertTrue(args.startswith("0.2.1 --source "), args)
            self.assertEqual(trusted, self._object(base, "v0.2.1"))
        with tempfile.TemporaryDirectory() as td:
            # Unrecorded, with only candidates of 0.2.1 published: --latest
            # still names the older final exactly as before this change.
            base = Path(td)
            remote = self._remote(base, self.CANDIDATES)
            env = self._env(base, remote, "PLEBIAN_OS_VERSION=0.2.1\n")
            result = self._pleb(env, "update", "--latest", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("older than installed release 0.2.1", result.stderr)
            self.assertTrue(self._calls(base)[-1].startswith("0.2.0 --source "))

    def test_a_record_that_names_no_tag_object_is_ignored(self):
        for tag, obj in (("v0.2.0-rc1", "a" * 40), ("v0.2.1-rc1", "not-an-object"),
                         ("v0.2.1-beta1", "a" * 40)):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                remote = self._remote(base, self.FINAL)
                env = self._env(base, remote, self._recorded("0.2.1", tag, obj))
                result = self._pleb(env, "update", "--latest", "--dry-run")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("ignoring recorded release tag", result.stderr)
                self.assertIn("already the latest published release", result.stdout)
                self.assertEqual(self._calls(base), [])

    def test_recorded_identity_cannot_be_overridden_from_the_environment(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._remote(base, self.FINAL)
            env = self._env(base, remote, "PLEBIAN_OS_VERSION=0.2.1\n")
            for key, value in (("PLEBIAN_OS_RELEASE_TAG", "v0.2.1-rc1"),
                               ("PLEBIAN_OS_RELEASE_TAG_OBJECT", "a" * 40)):
                with self.subTest(key=key):
                    result = self._pleb(dict(env, **{key: value}), "update", "--latest", "--dry-run")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"while {key} is overridden", result.stderr)
            self.assertEqual(self._calls(base), [])

    def test_failed_final_apply_restores_the_candidate_closure_and_record(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._remote(base, self.FINAL)
            rc = self._object(base, "v0.2.1-rc1")
            env = self._env(base, remote, self._recorded("0.2.1", "v0.2.1-rc1", rc))
            original = (base / "closure.env").read_bytes()
            env["PLEB_TEST_UPDATER_FAIL_ONCE"] = "1"
            result = self._pleb(env, "update", "--latest", "--no-restart")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("target stack apply failed with status 7", result.stderr)
            self.assertIn("previous closure and stack were restored", result.stderr)
            self.assertEqual((base / "closure.env").read_bytes(), original)
            self.assertEqual(self._calls(base)[-1].split("|")[0], "--rollback")
            self.assertEqual((base / "updater.log").read_text().splitlines(),
                             ["--revalidate-current", "--revalidate-current"])
            self.assertFalse((Path(env["PLEB_STATE_HOME"]) / "release-hop/active").exists())
            # Restored to the candidate, it still sees the final waiting.
            self.assertIn("final release v0.2.1 is published", self._report(env).stderr)

    def test_completed_final_rolls_back_to_the_candidate_record(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._remote(base, self.FINAL)
            rc = self._object(base, "v0.2.1-rc1")
            env = self._env(base, remote, self._recorded("0.2.1", "v0.2.1-rc1", rc))
            original = (base / "closure.env").read_bytes()
            self.assertEqual(self._pleb(env, "update", "--latest", "--no-restart").returncode, 0)
            self.assertNotEqual((base / "closure.env").read_bytes(), original)
            back = self._pleb(env, "update", "--rollback", "--no-restart")
            self.assertEqual(back.returncode, 0, back.stderr)
            self.assertEqual((base / "closure.env").read_bytes(), original)

    def test_interrupted_same_version_selection_is_judged_by_its_tag(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._remote(base, self.FINAL)
            rc = self._object(base, "v0.2.1-rc1")
            env = self._env(base, remote, self._recorded("0.2.1", "v0.2.1-rc1", rc))
            killed = self._pleb(dict(env, PLEB_TEST_SELECTOR_KILL="1"),
                                "update", "--latest", "--no-restart")
            self.assertNotEqual(killed.returncode, 0)
            state = Path(env["PLEB_STATE_HOME"]) / "release-hop"
            self.assertEqual((state / "phase").read_text(), "selecting\n")
            # Something changed the closure, but it still records the
            # candidate, and the selector has no record of its own to restore.
            closure = base / "closure.env"
            closure.write_text(closure.read_text() + "# touched\n")
            recovered = self._pleb(dict(env, PLEB_TEST_ROLLBACK_STATUS="3"),
                                   "update", "--rollback", "--no-restart")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertIn("left no restorable selected closure", recovered.stderr)
            self.assertFalse((state / "active").exists())
            self.assertIn("PLEBIAN_OS_RELEASE_TAG=v0.2.1-rc1\n", closure.read_text())


if __name__ == "__main__":
    unittest.main()
