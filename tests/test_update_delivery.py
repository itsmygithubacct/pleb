"""What `pleb update` is supposed to carry onto an already-provisioned machine.

Three delivery gaps found by using the updater to deliver fixes, each pinned
here so it cannot come back:

* Pleb updated every component except the one it runs from, so two provisioned
  machines sat on the same Pleb commit through repeated updates.
* A pinned ref that walks an installed component backwards said nothing, so a
  plain update could reinstate a persisted pin and quietly undo a delivered fix.
* Kilix Voice is installed lazily and was therefore never refreshed, so a
  bumped voice pin reached fresh machines only. Refreshing it must not turn the
  laziness off: an update may move what is installed and may not install
  anything.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def clean_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("GPU_TERMINAL", "KILIX", "PLEB")):
            env.pop(key)
    env["HOME"] = str(home)
    env["PLEB_ENV_SYSTEM"] = str(home / "missing-system.env")
    env["PLEB_ENV_USER"] = str(home / "missing-user.env")
    # The system session launcher of the developer's own machine must never be
    # what a test compares against.
    env["SESSION_BIN_DST"] = str(home / "no-such-session-launcher")
    return env


def git(*argv: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *argv],
        cwd=None if cwd is None else str(cwd),
        check=True,
        text=True,
        capture_output=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME="pleb tests",
            GIT_AUTHOR_EMAIL="tests@example.invalid",
            GIT_COMMITTER_NAME="pleb tests",
            GIT_COMMITTER_EMAIL="tests@example.invalid",
        ),
    )
    return result.stdout.strip()


def run_update_shell(body: str, env: dict[str, str], **assign: str):
    if "PLEB_ROOT" not in assign:
        raise AssertionError("update-shell tests must provide an isolated PLEB_ROOT")
    if "PLEB_STATE_HOME" in assign and "PLEB_STORAGE_HOME" not in assign:
        assign["PLEB_STORAGE_HOME"] = str(Path(assign["PLEB_STATE_HOME"]).parent)
    pleb_root = Path(assign["PLEB_ROOT"])
    fixture_storage = pleb_root / "lib/storage.sh"
    if not fixture_storage.exists():
        fixture_storage.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "lib/storage.sh", fixture_storage)
    prelude = "\n".join(f"{name}={value}" for name, value in assign.items())
    script = textwrap.dedent(
        f"""
        set -uo pipefail
        PLEB_CODE_ROOT={ROOT!s}
        {prelude}
        . "$PLEB_CODE_ROOT/lib/common.sh"
        . "$PLEB_CODE_ROOT/lib/install.sh"
        . "$PLEB_CODE_ROOT/lib/update.sh"
        {body}
        """
    )
    return subprocess.run(
        ["bash", "-c", script], cwd=ROOT, env=env, text=True, capture_output=True
    )


class SelfUpdateTests(unittest.TestCase):
    """Pleb must move its own checkout the way it moves every other one."""

    def _stack(
        self, tmp: Path, *, break_the_update: bool = False,
        incoming_camera: bool = False,
    ):
        """An origin carrying one new Pleb commit, and a checkout behind it.

        Seeded from the working tree rather than cloned from it, so the code
        under test is the code in the editor and not the last commit.
        """
        upstream = tmp / "upstream"
        shutil.copytree(ROOT, upstream, ignore=shutil.ignore_patterns(".git"))
        git("init", "-q", "-b", "main", str(upstream))
        git("add", "-A", cwd=upstream)
        git("commit", "-qm", "test: the installed pleb", cwd=upstream)
        base = git("rev-parse", "HEAD", cwd=upstream)
        (upstream / "VERSION").write_text("9.9.9-test\n")
        if incoming_camera:
            (upstream / "camera.sh").write_text("release camera\n")
        if break_the_update:
            # A shipped tree that cannot parse is the failure this has to
            # survive: the machine must end the run on the version it had.
            (upstream / "lib" / "kiosk.sh").write_text("kiosk_is_on() {\n")
        git("add", "-A", cwd=upstream)
        git("commit", "-qm", "test: move pleb forward", cwd=upstream)
        head = git("rev-parse", "HEAD", cwd=upstream)

        checkout = tmp / "pleb"
        git("clone", "-q", str(upstream), str(checkout))
        git("checkout", "-q", "--detach", base, cwd=checkout)
        return upstream, checkout, base, head

    def test_local_paths_survive_a_real_self_update_with_asymmetric_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            upstream, checkout, _, head = self._stack(
                tmp, incoming_camera=True
            )
            (checkout / "VERSION").write_text("operator version edit\n")
            (checkout / "camera.sh").write_text("operator camera\n")
            (checkout / "camera.sh").chmod(0o700)
            env = clean_env(tmp)
            result = run_update_shell(
                "_prepare_pleb_self_update\n_update_pleb_self",
                env,
                PLEB_ROOT=str(checkout),
                PLEB_DIR=str(checkout),
                PLEB_REPO=str(upstream),
                PLEB_REF=head,
                PLEB_STATE_HOME=str(tmp / "state"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), head)
            self.assertEqual((checkout / "VERSION").read_text(), "9.9.9-test\n")
            self.assertEqual(
                (checkout / "VERSION.local").read_text(),
                "operator version edit\n",
            )
            self.assertEqual((checkout / "camera.sh").read_text(), "operator camera\n")
            self.assertEqual(
                stat.S_IMODE((checkout / "camera.sh").stat().st_mode), 0o700
            )
            self.assertEqual(
                (checkout / f"camera.sh.from-{head[:12]}").read_text(),
                "release camera\n",
            )
            snapshots = list(
                (tmp / "state" / "update-preserve").glob("*-forward-pleb")
            )
            self.assertEqual(len(snapshots), 1)
            subprocess.run(
                ["sha256sum", "-c", "MANIFEST.sha256"],
                cwd=snapshots[0],
                check=True,
                stdout=subprocess.DEVNULL,
            )

    def test_a_pinned_ref_moves_the_running_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            upstream, checkout, base, head = self._stack(tmp)
            env = clean_env(tmp)
            result = run_update_shell(
                "_prepare_pleb_self_update\n_update_pleb_self",
                env,
                PLEB_ROOT=str(checkout),
                PLEB_DIR=str(checkout),
                PLEB_REPO=str(upstream),
                PLEB_REF=head,
                PLEB_STATE_HOME=str(tmp / "state"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), head)
            self.assertEqual((checkout / "VERSION").read_text().strip(), "9.9.9-test")
            self.assertIn("pleb updated:", result.stdout)
            self.assertIn("VERSION 9.9.9-test", result.stdout)
            self.assertNotEqual(base, head)

    def test_an_unrunnable_new_checkout_is_rolled_back(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            upstream, checkout, base, head = self._stack(tmp, break_the_update=True)
            env = clean_env(tmp)
            result = run_update_shell(
                "_prepare_pleb_self_update\n_update_pleb_self",
                env,
                PLEB_ROOT=str(checkout),
                PLEB_DIR=str(checkout),
                PLEB_REPO=str(upstream),
                PLEB_REF=head,
                PLEB_STATE_HOME=str(tmp / "state"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), base)
            self.assertNotEqual((checkout / "VERSION").read_text().strip(), "9.9.9-test")
            self.assertIn("rolled back", result.stderr)
            self.assertIn("previous version is still installed", result.stderr)

    def test_failed_self_fetch_restores_the_operator_state_it_prepared(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            upstream, checkout, base, _ = self._stack(tmp)
            (checkout / "VERSION").write_text("operator version\n")
            (checkout / "operator-only").write_bytes(b"\x00operator\xff")
            status_before = subprocess.check_output(
                [
                    "git", "-C", str(checkout), "status", "--porcelain=v1",
                    "--untracked-files=all",
                ]
            )
            env = clean_env(tmp)
            missing = "f" * 40
            result = run_update_shell(
                "_prepare_pleb_self_update\n"
                "trap _update_cleanup EXIT\n"
                "_update_pleb_self",
                env,
                PLEB_ROOT=str(checkout),
                PLEB_DIR=str(checkout),
                PLEB_REPO=str(upstream),
                PLEB_REF=missing,
                PLEB_STATE_HOME=str(tmp / "state"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("fetch failed", result.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), base)
            self.assertEqual((checkout / "VERSION").read_text(), "operator version\n")
            self.assertEqual(
                (checkout / "operator-only").read_bytes(), b"\x00operator\xff"
            )
            self.assertEqual(
                subprocess.check_output(
                    [
                        "git", "-C", str(checkout), "status", "--porcelain=v1",
                        "--untracked-files=all",
                    ]
                ),
                status_before,
            )

    def test_a_mutable_ref_is_refused_before_anything_moves(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            upstream, checkout, base, _ = self._stack(tmp)
            env = clean_env(tmp)
            result = run_update_shell(
                "_prepare_pleb_self_update",
                env,
                PLEB_ROOT=str(checkout),
                PLEB_DIR=str(checkout),
                PLEB_REPO=str(upstream),
                PLEB_REF="main",
                PLEB_STATE_HOME=str(tmp / "state"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("PLEB_REF must be a full 40-character commit SHA", result.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), base)

    def test_self_update_can_be_turned_off(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            upstream, checkout, base, head = self._stack(tmp)
            env = clean_env(tmp)
            result = run_update_shell(
                "_prepare_pleb_self_update\n_update_pleb_self",
                env,
                PLEB_ROOT=str(checkout),
                PLEB_DIR=str(checkout),
                PLEB_REPO=str(upstream),
                PLEB_REF=head,
                PLEB_SELF_UPDATE="0",
                PLEB_STATE_HOME=str(tmp / "state"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), base)
            self.assertIn("self-update disabled", result.stdout)

    def test_an_outer_updater_keeps_ownership_of_the_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            upstream, checkout, base, head = self._stack(tmp)
            env = clean_env(tmp)
            result = run_update_shell(
                "_UPDATE_LOCK_BORROWED=1\n"
                "_prepare_pleb_self_update\n_update_pleb_self",
                env,
                PLEB_ROOT=str(checkout),
                PLEB_DIR=str(checkout),
                PLEB_REPO=str(upstream),
                PLEB_REF=head,
                PLEB_STATE_HOME=str(tmp / "state"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), base)
            self.assertIn("outer updater owns the pleb checkout", result.stdout)

    def test_a_configured_directory_elsewhere_is_never_moved(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            upstream, checkout, base, head = self._stack(tmp)
            env = clean_env(tmp)
            result = run_update_shell(
                "_prepare_pleb_self_update\n_update_pleb_self",
                env,
                PLEB_ROOT=str(checkout),
                PLEB_DIR=str(upstream),
                PLEB_REPO=str(upstream),
                PLEB_REF=head,
                PLEB_STATE_HOME=str(tmp / "state"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), base)
            self.assertIn("not the checkout this pleb runs from", result.stderr)

    def test_self_update_preserves_an_edit_made_after_the_initial_gate(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            upstream, checkout, base, head = self._stack(tmp)
            env = clean_env(tmp)
            result = run_update_shell(
                "_prepare_pleb_self_update\n"
                "printf '\\noperator edit\\n' >>\"$PLEB_ROOT/VERSION\"\n"
                f"git -C \"$PLEB_ROOT\" remote set-url origin {tmp / 'missing-origin'}\n"
                "_update_pleb_self",
                env,
                PLEB_ROOT=str(checkout),
                PLEB_DIR=str(checkout),
                PLEB_REPO=str(upstream),
                PLEB_REF=head,
                PLEB_STATE_HOME=str(tmp / "state"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), head)
            self.assertEqual((checkout / "VERSION").read_text(), "9.9.9-test\n")
            self.assertIn("operator edit", (checkout / "VERSION.local").read_text())
            snapshots = list((tmp / "state" / "update-preserve").glob("*-forward-pleb"))
            self.assertEqual(len(snapshots), 1)
            subprocess.run(
                ["sha256sum", "-c", "MANIFEST.sha256"],
                cwd=snapshots[0],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            self.assertNotIn("fetch failed", result.stderr)


class PinnedMoveReportingTests(unittest.TestCase):
    """A pin that rewinds an installed component has to say so."""

    def _component(self, tmp: Path):
        origin = tmp / "origin"
        origin.mkdir()
        git("init", "-q", "-b", "main", str(origin))
        (origin / "file").write_text("old\n")
        git("add", "-A", cwd=origin)
        git("commit", "-qm", "old", cwd=origin)
        old = git("rev-parse", "HEAD", cwd=origin)
        (origin / "file").write_text("new\n")
        git("commit", "-qam", "new", cwd=origin)
        new = git("rev-parse", "HEAD", cwd=origin)

        checkout = tmp / "component"
        git("clone", "-q", str(origin), str(checkout))
        git("checkout", "-q", "--detach", new, cwd=checkout)
        return checkout, old, new

    def test_a_rewind_names_the_file_that_pinned_it(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            checkout, old, new = self._component(tmp)
            session = tmp / "session.env"
            session.write_text(
                f'if [ -z "${{KILIX95_REF+x}}" ]; then KILIX95_REF={old}; fi\n'
            )
            env = clean_env(tmp)
            env["PLEB_ENV_SYSTEM"] = str(session)
            result = run_update_shell(
                f'checkout_fetched_ref "{checkout}" "$KILIX95_REF" "kilix 95" KILIX95_REF',
                env,
                PLEB_ROOT=str(tmp / "pleb-test-root"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), old)
            self.assertIn("DOWNGRADE", result.stderr)
            self.assertIn(f"{new[:12]} -> {old[:12]}", result.stderr)
            self.assertIn(str(session), result.stderr)
            self.assertIn("export KILIX95_REF", result.stderr)

    def test_a_forward_move_is_reported_without_shouting(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            checkout, old, new = self._component(tmp)
            git("checkout", "-q", "--detach", old, cwd=checkout)
            env = clean_env(tmp)
            env["KILIX95_REF"] = new
            result = run_update_shell(
                'checkout_fetched_ref "%s" "$KILIX95_REF" "kilix 95" KILIX95_REF'
                % checkout,
                env,
                PLEB_ROOT=str(tmp / "pleb-test-root"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), new)
            self.assertNotIn("DOWNGRADE", result.stdout + result.stderr)
            self.assertIn(f"{old[:12]} -> {new[:12]}", result.stdout)
            self.assertIn("pinned by the environment", result.stdout)


class VoiceRefreshTests(unittest.TestCase):
    """Refresh what is installed; never install what is not."""

    def _refresh(self, tmp: Path, *, installed: bool, dictation: bool):
        kilix = tmp / "kilix"
        (kilix / "scripts").mkdir(parents=True)
        calls = tmp / "voice-calls"
        installer = kilix / "scripts" / "install-kilix-voice.sh"
        installer.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >>"{calls}"\n'
            'exit "${VOICE_REFRESH_EXIT:-0}"\n'
        )
        installer.chmod(0o755)

        local_bin = tmp / ".local" / "bin"
        local_bin.mkdir(parents=True)
        if installed:
            for name in ("kilix-tts", "kilix-stt"):
                tool = local_bin / name
                tool.write_text("#!/bin/sh\nexit 0\n")
                tool.chmod(0o755)
        data = tmp / "kilix-data"
        if dictation:
            (data / "voice" / "lib" / "current").mkdir(parents=True)
            (data / "voice" / "lib" / "current" / "libvosk.so").write_text("")
        return kilix, data, calls

    def test_an_installed_closure_is_refreshed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            kilix, data, calls = self._refresh(tmp, installed=True, dictation=True)
            result = run_update_shell(
                "refresh_kilix_voice",
                clean_env(tmp),
                PLEB_ROOT=str(tmp / "pleb-test-root"),
                KILIX_DIR=str(kilix),
                KILIX_DATA_HOME=str(data),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls.read_text().splitlines(), [""])
            self.assertIn("refreshing the installed Kilix Voice closure", result.stdout)

    def test_a_read_aloud_only_machine_stays_read_aloud_only(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            kilix, data, calls = self._refresh(tmp, installed=True, dictation=False)
            result = run_update_shell(
                "refresh_kilix_voice",
                clean_env(tmp),
                PLEB_ROOT=str(tmp / "pleb-test-root"),
                KILIX_DIR=str(kilix),
                KILIX_DATA_HOME=str(data),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls.read_text().splitlines(), ["--without-dictation"])

    def test_an_absent_closure_is_never_installed_by_an_update(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            kilix, data, calls = self._refresh(tmp, installed=False, dictation=False)
            result = run_update_shell(
                "refresh_kilix_voice",
                clean_env(tmp),
                PLEB_ROOT=str(tmp / "pleb-test-root"),
                KILIX_DIR=str(kilix),
                KILIX_DATA_HOME=str(data),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(calls.exists())
            self.assertIn("an update never installs it", result.stdout)

    def test_a_failed_refresh_leaves_the_previous_closure_and_the_update_alive(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            kilix, data, calls = self._refresh(tmp, installed=True, dictation=True)
            env = clean_env(tmp)
            env["VOICE_REFRESH_EXIT"] = "1"
            result = run_update_shell(
                "refresh_kilix_voice\necho survived",
                env,
                PLEB_ROOT=str(tmp / "pleb-test-root"),
                KILIX_DIR=str(kilix),
                KILIX_DATA_HOME=str(data),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("survived", result.stdout)
            self.assertIn("previously installed closure is still in place", result.stderr)


if __name__ == "__main__":
    unittest.main()
