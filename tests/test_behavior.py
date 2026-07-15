import fcntl
import hashlib
import io
import os
import stat
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COORDINATED_STORAGE_VARS = (
    "KILIX_CONFIG_HOME",
    "KILIX_STATE_DIRECTORY",
    "KILIX_CACHE_HOME",
    "KILIX_SESSION_HOME",
    "KILIX_PREBUILT_HOME",
    "KILIX95_CONFIG_HOME",
    "KILIX95_STATE_HOME",
    "KILIX95_CACHE_HOME",
    "KILIX95_SESSION_HOME",
)


def clean_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("GPU_TERMINAL", "KILIX", "PLEB")):
            env.pop(key)
    env["HOME"] = str(home)
    env["GOTELEMETRY"] = "off"
    env["PLEB_ENV_SYSTEM"] = str(home / "missing-system.env")
    env["PLEB_ENV_USER"] = str(home / "missing-user.env")
    return env


def write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def make_go_archive(path: Path, version: str = "go1.26.4") -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name in ("go", "go/bin"):
            entry = tarfile.TarInfo(name)
            entry.type = tarfile.DIRTYPE
            entry.mode = 0o755
            archive.addfile(entry)
        scripts = {
            "go/bin/go": f"#!/bin/sh\necho 'go version {version} linux/amd64'\n",
            "go/bin/gofmt": "#!/bin/sh\nexit 0\n",
        }
        for name, content in scripts.items():
            data = content.encode()
            entry = tarfile.TarInfo(name)
            entry.mode = 0o755
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))


class PlebBehaviorTests(unittest.TestCase):
    def test_version_command_reports_release_file(self):
        with tempfile.TemporaryDirectory() as td:
            result = subprocess.run(
                [str(ROOT / "bin/pleb"), "version"],
                cwd=ROOT,
                env=clean_env(Path(td)),
                text=True,
                capture_output=True,
                check=True,
            )
        self.assertEqual(result.stdout.strip(), f"pleb {(ROOT / 'VERSION').read_text().strip()}")

    def test_recovery_doc_installs_readable_and_uninstalls_from_override_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            destination = tmp / "usr/local/share/doc/pleb/RECOVERY.md"
            env = clean_env(tmp)
            env.update(
                {
                    "PLEB_RECOVERY_DOC_DST": str(destination),
                    "XSESSION_DST": str(tmp / "missing/pleb.desktop"),
                    "SESSION_BIN_DST": str(tmp / "missing/pleb-session"),
                    "KILIX_LINK": str(tmp / "missing/kilix"),
                    "PLEB_LINK": str(tmp / "missing/pleb"),
                    "AUTOLOGIN_CONF": str(tmp / "missing/autologin.conf"),
                }
            )
            install_script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/install.sh"
                run_root() {{ "$@"; }}
                install_recovery_document
                """
            )
            installed = subprocess.run(
                ["bash", "-c", install_script],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(destination.read_bytes(), (ROOT / "docs/RECOVERY.md").read_bytes())
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o644)

            uninstall_script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/install.sh"
                run_root() {{ "$@"; }}
                do_uninstall
                """
            )
            removed = subprocess.run(
                ["bash", "-c", uninstall_script],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(destination.exists())

    def test_persisted_roots_are_applied_before_dependent_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = tmp / "persisted-source"
            data = tmp / "persisted-data"
            state = data / "pleb/special-state"
            config = tmp / "session.env"
            config.write_text(
                f"GPU_TERMINAL_SOURCE_HOME={source!s}\n"
                f"GPU_TERMINAL_HOME={data!s}\n"
                f"PLEB_STATE_HOME={state!s}\n"
            )
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                . "$PLEB_ROOT/lib/common.sh"
                printf '%s\n' "$GPU_TERMINAL_SOURCE_HOME" "$GPU_TERMINAL_HOME" \
                    "$PLEB_STORAGE_HOME" "$PLEB_STATE_HOME" "$PLEB_DATA_HOME" \
                    "$KILIX_DIR" "$KILIX_DATA_HOME" "$KILIX_BUILD_DIRECTORY" "$KILIX_PREBUILT_HOME" \
                    "$KILIX95_DIR" "$KILIX95_DATA_HOME" "$KILIX_DESKTOP_DIR"
                """
            )
            env = clean_env(tmp)
            env["PLEB_ENV_USER"] = str(config)
            result = subprocess.run(
                ["bash", "-c", script],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str(source),
                    str(data),
                    str(data / "pleb"),
                    str(state),
                    str(data / "pleb/data"),
                    str(source / "kilix"),
                    str(data / "kilix/data"),
                    str(data / "kilix/build"),
                    str(data / "kilix/prebuilt/kitty.app"),
                    str(source / "kilix-95"),
                    str(data / "kilix-95/data"),
                    str(data / "pleb/data/desktop"),
                ],
            )

            explicit_data = tmp / "explicit-data"
            env["GPU_TERMINAL_HOME"] = str(explicit_data)
            overridden = subprocess.run(
                ["bash", "-c", script],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(overridden.stdout.splitlines()[1], str(explicit_data))
            self.assertEqual(overridden.stdout.splitlines()[2], str(explicit_data / "pleb"))

    def test_coordinated_storage_env_is_exported_and_explicit_values_win(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            persisted = {name: tmp / "persisted" / name.lower() for name in COORDINATED_STORAGE_VARS}
            explicit = {name: tmp / "explicit" / name.lower() for name in COORDINATED_STORAGE_VARS}
            observed = tmp / "observed"
            engine = tmp / "kilix"
            names = " ".join(COORDINATED_STORAGE_VARS)
            write_executable(
                engine,
                "#!/usr/bin/env bash\n"
                f"for name in {names}; do printf '%s=%s\\n' \"$name\" \"${{!name}}\"; done >{observed!s}\n",
            )
            config = tmp / "session.env"
            config.write_text(
                "".join(f"{name}={value!s}\n" for name, value in persisted.items())
                + f"KILIX={engine!s}\nPLEB_NO_FILL=1\n"
            )
            print_script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                . "$PLEB_ROOT/lib/common.sh"
                for name in {names}; do printf '%s=%s\n' "$name" "${{!name}}"; done
                """
            )

            explicit_env = clean_env(tmp)
            explicit_env["PLEB_ENV_USER"] = str(config)
            explicit_env.update({name: str(value) for name, value in explicit.items()})
            common = subprocess.run(
                ["bash", "-c", print_script],
                cwd=ROOT,
                env=explicit_env,
                text=True,
                capture_output=True,
                check=True,
            )
            expected_explicit = [f"{name}={explicit[name]}" for name in COORDINATED_STORAGE_VARS]
            self.assertEqual(common.stdout.splitlines(), expected_explicit)

            subprocess.run(
                [str(ROOT / "bin/pleb-session")],
                cwd=ROOT,
                env=explicit_env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(observed.read_text().splitlines(), expected_explicit)

            persisted_env = clean_env(tmp)
            persisted_env["PLEB_ENV_USER"] = str(config)
            subprocess.run(
                [str(ROOT / "bin/pleb-session")],
                cwd=ROOT,
                env=persisted_env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(
                observed.read_text().splitlines(),
                [f"{name}={persisted[name]}" for name in COORDINATED_STORAGE_VARS],
            )

    def test_session_loads_and_exports_the_same_source_and_storage_contract(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = tmp / "source"
            data = tmp / "data"
            observed = tmp / "observed"
            engine = tmp / "kilix"
            write_executable(
                engine,
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$GPU_TERMINAL_SOURCE_HOME\" \"$GPU_TERMINAL_HOME\" "
                f"\"$PLEB_STORAGE_HOME\" \"$PLEB_DATA_HOME\" "
                f"\"$KILIX_STORAGE_HOME\" \"$KILIX_CONFIG_HOME\" "
                f"\"$KILIX_STATE_DIRECTORY\" \"$KILIX_CACHE_HOME\" "
                f"\"$KILIX_SESSION_HOME\" \"$KILIX_DATA_HOME\" "
                f"\"$KILIX_BUILD_DIRECTORY\" \"$KILIX_PREBUILT_HOME\" \"$KILIX95_STORAGE_HOME\" "
                f"\"$KILIX95_CONFIG_HOME\" \"$KILIX95_STATE_HOME\" "
                f"\"$KILIX95_CACHE_HOME\" \"$KILIX95_SESSION_HOME\" "
                f"\"$KILIX95_DATA_HOME\" "
                f"\"$KILIX_DESKTOP_DIR\" "
                f"\"$KILIX_DIR\" \"$KILIX95_DIR\" >{observed!s}\n",
            )
            config = tmp / ".local/gpu_terminal/pleb/config/session.env"
            config.parent.mkdir(parents=True)
            config.write_text(
                f"GPU_TERMINAL_SOURCE_HOME={source!s}\n"
                f"GPU_TERMINAL_HOME={data!s}\n"
                f"KILIX={engine!s}\n"
                "PLEB_NO_FILL=1\n"
            )
            env = clean_env(tmp)
            env.pop("PLEB_ENV_USER")
            subprocess.run(
                [str(ROOT / "bin/pleb-session")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(
                observed.read_text().splitlines(),
                [
                    str(source),
                    str(data),
                    str(data / "pleb"),
                    str(data / "pleb/data"),
                    str(data / "kilix"),
                    str(data / "kilix/config"),
                    str(data / "kilix/state"),
                    str(data / "kilix/cache"),
                    str(data / "kilix/session"),
                    str(data / "kilix/data"),
                    str(data / "kilix/build"),
                    str(data / "kilix/prebuilt/kitty.app"),
                    str(data / "kilix-95"),
                    str(data / "kilix-95/config"),
                    str(data / "kilix-95/state"),
                    str(data / "kilix-95/cache"),
                    str(data / "kilix-95/session"),
                    str(data / "kilix-95/data"),
                    str(data / "pleb/data/desktop"),
                    str(source / "kilix"),
                    str(source / "kilix-95"),
                ],
            )
            session_log = data / "pleb/state/session.log"
            self.assertTrue(session_log.is_file())
            self.assertEqual(stat.S_IMODE(session_log.stat().st_mode), 0o600)

    def test_session_log_is_private_rotated_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            engine = tmp / "kilix"
            write_executable(engine, "#!/bin/sh\nexit 0\n")
            log = tmp / "state/session.log"
            log.parent.mkdir()
            original = b"x" * (1048576 + 1)
            log.write_bytes(original)
            log.chmod(0o644)
            env = clean_env(tmp)
            env.update(
                {
                    "KILIX": str(engine),
                    "PLEB_LOG": str(log),
                    "PLEB_NO_FILL": "1",
                }
            )
            subprocess.run(
                [str(ROOT / "bin/pleb-session")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual((Path(f"{log}.1")).read_bytes(), original)
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(Path(f"{log}.1").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)

            target = tmp / "must-not-be-written"
            target.write_text("sentinel\n")
            linked_log = tmp / "state/linked.log"
            linked_log.symlink_to(target)
            env["PLEB_LOG"] = str(linked_log)
            rejected = subprocess.run(
                [str(ROOT / "bin/pleb-session")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("refusing unsafe session log", rejected.stderr)
            self.assertEqual(target.read_text(), "sentinel\n")

    def test_status_uses_persisted_session_env_and_explicit_env_wins(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            engine = tmp / "configured-kilix"
            write_executable(
                engine,
                "#!/bin/sh\n[ \"${1:-}\" = --which ] && echo /engine/from/persisted-env\n",
            )
            system_env = tmp / "system.env"
            user_env = tmp / "user.env"
            system_env.write_text(
                f"KILIX_DIR={tmp / 'kilix-checkout'}\n"
                f"KILIX={engine}\n"
                "PLEB_DESKTOP=1\n"
                "KILIX_DESKTOP_PROVIDER=external\n"
            )
            user_env.write_text(
                "KILIX_DESKTOP_PROVIDER=command\n"
                "KILIX_DESKTOP_NAME='persisted desktop'\n"
                "PLEB_RESPAWN=1\n"
            )
            xsession = tmp / "pleb.desktop"
            launcher = tmp / "pleb-session"
            xsession.touch()
            write_executable(launcher, "#!/bin/sh\nexit 0\n")
            env = clean_env(tmp)
            env.update(
                {
                    "PLEB_ENV_SYSTEM": str(system_env),
                    "PLEB_ENV_USER": str(user_env),
                    "XSESSION_DST": str(xsession),
                    "SESSION_BIN_DST": str(launcher),
                    "AUTOLOGIN_CONF": str(tmp / "autologin.conf"),
                    "KILIX_LINK": str(tmp / "kilix-link"),
                }
            )
            result = subprocess.run(
                [str(ROOT / "bin/pleb"), "status"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("/engine/from/persisted-env", result.stdout)
            self.assertIn("enabled via command (persisted desktop)", result.stdout)
            self.assertIn("kiosk    : HARD", result.stdout)

            subprocess.run(
                [str(ROOT / "bin/pleb"), "kiosk", "off"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(stat.S_IMODE(user_env.stat().st_mode), 0o600)
            self.assertEqual(user_env.read_text().count("PLEB_RESPAWN="), 1)
            persisted_off = subprocess.run(
                [str(ROOT / "bin/pleb"), "status"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("kiosk    : soft", persisted_off.stdout)

            env["PLEB_DESKTOP"] = "0"
            env["PLEB_RESPAWN"] = "0"
            overridden = subprocess.run(
                [str(ROOT / "bin/pleb"), "status"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("desktop  : plain kilix shell", overridden.stdout)
            self.assertIn("kiosk    : soft", overridden.stdout)

    def test_session_truthy_respawn_values_match_kiosk_status_semantics(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            kilix = tmp / "kilix"
            count = tmp / "launch-count"
            write_executable(
                kilix,
                f"#!/bin/sh\nprintf 'launch\\n' >>{count!s}\nexit 1\n",
            )
            env = clean_env(tmp)
            env.update(
                {
                    "DISPLAY": ":999",
                    "KILIX": str(kilix),
                    "PLEB_LOG": str(tmp / "session.log"),
                    "PLEB_NO_FILL": "1",
                    "PLEB_RESPAWN": "true",
                }
            )
            result = subprocess.run(
                # The first crash backs off for two seconds. Leave enough
                # startup margin for this assertion on a busy parallel CI host.
                ["timeout", "4.5", str(ROOT / "bin/pleb-session")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 124)
            self.assertGreaterEqual(count.read_text().count("launch"), 2)

            count.unlink()
            env["PLEB_RESPAWN"] = "off"
            result = subprocess.run(
                [str(ROOT / "bin/pleb-session")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(count.read_text().count("launch"), 1)

    def test_root_context_does_not_source_user_owned_config(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            marker = tmp / "executed"
            config = tmp / "untrusted.env"
            config.write_text(f"touch {marker!s}\n")
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                id() {{ [ "${{1:-}}" = -u ] && {{ echo 0; return; }}; command id "$@"; }}
                PLEB_ROOT={ROOT!s}
                PLEB_ENV_SYSTEM={config!s}
                PLEB_ENV_USER={tmp / 'missing-user.env'!s}
                . "$PLEB_ROOT/lib/common.sh"
                [ ! -e {marker!s} ]
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("refusing to source", result.stderr)

    def test_explicit_restart_does_not_prompt_or_require_a_tty(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            xsession = tmp / "pleb.desktop"
            autologin = tmp / "autologin.conf"
            xsession.touch()
            autologin.write_text("autologin-session=pleb\n")
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                XSESSION_DST={xsession!s}
                AUTOLOGIN_CONF={autologin!s}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/install.sh"
                . "$PLEB_ROOT/lib/kiosk.sh"
                . "$PLEB_ROOT/lib/update.sh"
                _UPDATE_RESTART=yes
                _UPDATE_YES=0
                _do_restart() {{ printf '%s\\n' RESTARTED; }}
                _offer_restart </dev/null
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("RESTARTED", result.stdout)
            self.assertNotIn("non-interactive; not restarting", result.stderr)

    def test_update_lock_rejects_a_second_process(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = tmp / ".local/gpu_terminal/pleb/state"
            state.mkdir(parents=True)
            lock_path = state / "update.lock"
            with lock_path.open("w") as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                script = textwrap.dedent(
                    f"""
                    set -euo pipefail
                    PLEB_ROOT={ROOT!s}
                    PLEB_STATE_HOME={state!s}
                    . "$PLEB_ROOT/lib/common.sh"
                    . "$PLEB_ROOT/lib/update.sh"
                    _acquire_update_lock
                    """
                )
                result = subprocess.run(
                    ["bash", "-c", script],
                    env=clean_env(tmp),
                    text=True,
                    capture_output=True,
                )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already running", result.stderr)

    def test_inherited_update_lock_is_validated_borrowed_and_left_open(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = tmp / ".local/gpu_terminal/pleb/state"
            state.mkdir(parents=True)
            user_env = tmp / "session.env"
            user_env.write_text("PLEB_UPDATE_LOCK_FD=9999\n")
            lock_fd = os.open(state / "update.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                script = textwrap.dedent(
                    f"""
                    set -euo pipefail
                    PLEB_ROOT={ROOT!s}
                    PLEB_STATE_HOME={state!s}
                    . "$PLEB_ROOT/lib/common.sh"
                    . "$PLEB_ROOT/lib/update.sh"
                    _acquire_update_lock
                    [ "$_UPDATE_LOCK_BORROWED" = 1 ]
                    _release_update_lock
                    [ -e /proc/$$/fd/{lock_fd} ]
                    flock -n -x {lock_fd}
                    printf '%s\n' BORROWED_LOCK_OK
                    """
                )
                env = clean_env(tmp)
                env["PLEB_UPDATE_LOCK_FD"] = str(lock_fd)
                env["PLEB_ENV_USER"] = str(user_env)
                result = subprocess.run(
                    ["bash", "-c", script],
                    env=env,
                    pass_fds=(lock_fd,),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertIn("BORROWED_LOCK_OK", result.stdout)

                competitor = os.open(state / "update.lock", os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(competitor)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    def test_inherited_update_lock_rejects_invalid_closed_and_busy_fds(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = tmp / ".local/gpu_terminal/pleb/state"
            state.mkdir(parents=True)

            def attempt(value: str, pass_fds: tuple[int, ...] = ()) -> subprocess.CompletedProcess[str]:
                script = textwrap.dedent(
                    f"""
                    set -euo pipefail
                    PLEB_ROOT={ROOT!s}
                    PLEB_STATE_HOME={state!s}
                    . "$PLEB_ROOT/lib/common.sh"
                    . "$PLEB_ROOT/lib/update.sh"
                    _acquire_update_lock
                    """
                )
                env = clean_env(tmp)
                env["PLEB_UPDATE_LOCK_FD"] = value
                return subprocess.run(
                    ["bash", "-c", script],
                    env=env,
                    pass_fds=pass_fds,
                    text=True,
                    capture_output=True,
                )

            invalid = attempt("not-a-fd")
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("must be a numeric", invalid.stderr)
            closed = attempt("9999")
            self.assertNotEqual(closed.returncode, 0)
            self.assertIn("is not open", closed.stderr)

            # A persisted file cannot manufacture this process capability.
            persisted = tmp / "persisted.env"
            persisted.write_text("PLEB_UPDATE_LOCK_FD=9999\n")
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                PLEB_STATE_HOME={state!s}
                PLEB_ENV_USER={persisted!s}
                . "$PLEB_ROOT/lib/common.sh"
                [ -z "${{PLEB_UPDATE_LOCK_FD:-}}" ]
                . "$PLEB_ROOT/lib/update.sh"
                _acquire_update_lock
                _release_update_lock
                """
            )
            subprocess.run(
                ["bash", "-c", script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
                check=True,
            )

            lock_path = state / "update.lock"
            holder = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            candidate = os.open(lock_path, os.O_RDWR)
            try:
                fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                busy = attempt(str(candidate), (candidate,))
                self.assertNotEqual(busy.returncode, 0)
                self.assertIn("could not acquire the inherited", busy.stderr)
            finally:
                fcntl.flock(holder, fcntl.LOCK_UN)
                os.close(candidate)
                os.close(holder)

            # Even an otherwise valid, lockable descriptor cannot redirect the
            # capability away from the one shared Pleb state lock.
            arbitrary = os.open(state / "arbitrary.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                redirected = attempt(str(arbitrary), (arbitrary,))
                self.assertNotEqual(redirected.returncode, 0)
                self.assertIn("must refer to", redirected.stderr)
            finally:
                os.close(arbitrary)

    def test_inherited_kilix_transaction_lock_is_validated_and_borrowed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            storage = tmp / ".local/gpu_terminal/kilix"
            state = storage / "state"
            state.mkdir(parents=True)
            state.chmod(0o700)
            lock = state / "build-update.lock"
            lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                script = textwrap.dedent(
                    f"""
                    set -euo pipefail
                    PLEB_ROOT={ROOT!s}
                    KILIX_STORAGE_HOME={storage!s}
                    KILIX_STATE_DIRECTORY={state!s}
                    . "$PLEB_ROOT/lib/common.sh"
                    . "$PLEB_ROOT/lib/update.sh"
                    _acquire_kilix_transaction_lock
                    [ "$_KILIX_TXN_LOCK_BORROWED" = 1 ]
                    [ "$KILIX_TRANSACTION_LOCK_PATH" = {lock!s} ]
                    _release_kilix_transaction_lock
                    [ -e /proc/$$/fd/{lock_fd} ]
                    flock -n -x {lock_fd}
                    printf '%s\n' BORROWED_KILIX_LOCK_OK
                    """
                )
                env = clean_env(tmp)
                env["KILIX_TRANSACTION_LOCK_FD"] = str(lock_fd)
                result = subprocess.run(
                    ["bash", "-c", script],
                    env=env,
                    pass_fds=(lock_fd,),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertIn("BORROWED_KILIX_LOCK_OK", result.stdout)

                competitor = os.open(lock, os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(competitor)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    def test_update_rebuild_uses_kilix_dependency_contract_for_libxxhash(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            kilix = tmp / "kilix"
            scripts = kilix / "scripts"
            scripts.mkdir(parents=True)
            build = tmp / "kilix-data/build"
            kilix_state = tmp / "kilix-data/state"
            state = tmp / "pleb-state"
            head = "a" * 40
            calls = tmp / "dependency-calls"
            ready = tmp / "libxxhash-ready"
            installer = scripts / "install-build-deps.sh"
            write_executable(
                installer,
                "#!/bin/sh\n"
                "set -eu\n"
                f"calls={calls!s}\n"
                f"ready={ready!s}\n"
                "if [ \"${1:-}\" = --verify ]; then\n"
                "  echo 'verify libxxhash' >>\"$calls\"\n"
                "  [ -f \"$ready\" ] || { echo 'pkg-config libxxhash: MISSING'; exit 1; }\n"
                "else\n"
                "  echo 'install libxxhash' >>\"$calls\"\n"
                "  : >\"$ready\"\n"
                "fi\n",
            )
            launcher = kilix / "kilix"
            generation = build / "generations/build.Test"
            fork = build / "current/src/kitty/launcher/kitty"
            kitten = build / "current/src/kitty/launcher/kitten"
            write_executable(
                launcher,
                "#!/bin/sh\n"
                "set -eu\n"
                "case \"${1:-}\" in\n"
                "  --build)\n"
                f"    echo build >>{calls!s}\n"
                f"    mkdir -p {generation / 'src/kitty/launcher'!s} {kilix_state!s}\n"
                f"    chmod 0700 {build!s} {kilix_state!s}\n"
                f"    rm -rf {build / 'current'!s}\n"
                f"    ln -s generations/build.Test {build / 'current'!s}\n"
                f"    printf '#!/bin/sh\\n' >{fork!s}\n"
                f"    printf '#!/bin/sh\\n' >{kitten!s}\n"
                f"    chmod 0755 {fork!s} {kitten!s}\n"
                f"    printf '%s\\n' {head!s} >{build / 'current/source-id'!s}\n"
                f"    printf '%s\\t%s\\n' {kilix.resolve()!s} {head!s} >{kilix_state / 'fork-built-ref'!s}\n"
                f"    chmod 0600 {kilix_state / 'fork-built-ref'!s}\n"
                "    ;;\n"
                f"  --which) echo {fork!s}; echo kilix-test ;;\n"
                "  *) exit 2 ;;\n"
                "esac\n",
            )
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                KILIX_DIR={kilix!s}
                KILIX_STORAGE_HOME={build.parent!s}
                KILIX_BUILD_DIRECTORY={build!s}
                KILIX_STATE_DIRECTORY={kilix_state!s}
                PLEB_STATE_HOME={state!s}
                PLEB_SKIP_DEPS=0
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/install.sh"
                . "$PLEB_ROOT/lib/update.sh"
                _ensure_go_for_kilix_build() {{ :; }}
                _kilix_fork_head() {{ printf '%s\\n' {head!s}; }}
                _rebuild_kilix_fork
                """
            )
            rebuilt = subprocess.run(
                ["bash", "-c", script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
            )
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            self.assertEqual(
                calls.read_text().splitlines(),
                [
                    "verify libxxhash",
                    "install libxxhash",
                    "verify libxxhash",
                    "build",
                ],
            )

            ready.unlink()
            calls.write_text("")
            skip_script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                KILIX_DIR={kilix!s}
                PLEB_SKIP_DEPS=1
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/install.sh"
                ensure_kilix_build_deps
                """
            )
            skipped = subprocess.run(
                ["bash", "-c", skip_script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(skipped.returncode, 0)
            self.assertEqual(calls.read_text().splitlines(), ["verify libxxhash"])
            self.assertIn("PLEB_SKIP_DEPS=1", skipped.stderr)
            self.assertIn("libxxhash", skipped.stdout + skipped.stderr)

    def test_failed_update_transaction_restores_both_checkouts_and_fork_engine(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            def two_commit_repo(path: Path, ignore: str = "") -> tuple[str, str]:
                subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
                subprocess.run(["git", "-C", str(path), "config", "user.name", "Pleb Test"], check=True)
                subprocess.run(
                    ["git", "-C", str(path), "config", "user.email", "pleb@example.invalid"],
                    check=True,
                )
                if ignore:
                    (path / ".gitignore").write_text(ignore)
                (path / "payload").write_text("before\n")
                subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
                subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "before"], check=True)
                before = subprocess.check_output(
                    ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
                ).strip()
                (path / "payload").write_text("after\n")
                subprocess.run(["git", "-C", str(path), "commit", "-qam", "after"], check=True)
                after = subprocess.check_output(
                    ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
                ).strip()
                subprocess.run(["git", "-C", str(path), "reset", "-q", "--hard", before], check=True)
                return before, after

            kilix = tmp / "kilix"
            src = kilix / "src"
            kilix_before, kilix_after = two_commit_repo(kilix, "src/\n")
            src_before, src_after = two_commit_repo(
                src, "kitty/launcher/kitty\nkitty/launcher/kitten\n"
            )
            subprocess.run(
                ["git", "-C", str(src), "checkout", "-q", "--detach", src_before], check=True
            )
            kilix95 = tmp / "kilix-95"
            kilix95_before, kilix95_after = two_commit_repo(kilix95)
            build = tmp / "kilix-storage" / "build"
            generations = build / "generations"
            old_current_generation = generations / "build.OldCurrent"
            old_previous_generation = generations / "build.OldPrevious"
            new_generation = generations / "build.NewFailed"
            current = build / "current"
            previous = build / "previous"
            fork = current / "src/kitty/launcher/kitty"
            kitten = current / "src/kitty/launcher/kitten"
            (old_current_generation / "src/kitty/launcher").mkdir(parents=True)
            old_previous_generation.mkdir()
            current.symlink_to("generations/build.OldCurrent")
            previous.symlink_to("generations/build.OldPrevious")
            build.chmod(0o700)
            write_executable(fork, "#!/bin/sh\necho old-fork\n")
            write_executable(kitten, "#!/bin/sh\necho old-kitten\n")
            (current / "source-id").write_text("old\n")
            (old_previous_generation / "sentinel").write_text("older-generation\n")
            current_identity = (os.lstat(current).st_dev, os.lstat(current).st_ino)
            previous_identity = (os.lstat(previous).st_dev, os.lstat(previous).st_ino)
            state = tmp / ".local/gpu_terminal/pleb/state"
            state.mkdir(parents=True)
            legacy_stamp = state / "kilix-fork-built-ref"
            legacy_stamp.write_text("legacy-stamp\n")
            kilix_state = tmp / "kilix-storage/state"
            kilix_state.mkdir(parents=True)
            kilix_state.chmod(0o700)
            stamp = kilix_state / "fork-built-ref"
            stamp.write_text("old-stamp\n")
            stamp.chmod(0o600)

            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                PLEB_STATE_HOME={state!s}
                KILIX_STORAGE_HOME={build.parent!s}
                KILIX_STATE_DIRECTORY={kilix_state!s}
                KILIX_DIR={kilix!s}
                KILIX_BUILD_DIRECTORY={build!s}
                KILIX95_DIR={kilix95!s}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/update.sh"
                _acquire_update_lock
                _update_transaction_begin
                git -C "$KILIX_DIR" reset --hard {kilix_after!s} >/dev/null
                git -C "$KILIX_DIR/src" reset --hard {src_after!s} >/dev/null
                git -C "$KILIX95_DIR" reset --hard {kilix95_after!s} >/dev/null
                mv {current!s} {previous!s}
                mkdir -p {new_generation / 'src/kitty/launcher'!s}
                ln -s generations/build.NewFailed {current!s}
                printf '%s\n' new >{current / 'source-id'!s}
                printf '%s\n' new-fork >{fork!s}
                printf '%s\n' new-kitten >{kitten!s}
                printf '%s\n' new-stamp >{stamp!s}
                exit 73
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 73)
            self.assertIn("restoring the previous coherent", result.stderr)
            for repo, expected, expected_branch in (
                (kilix, kilix_before, "main"),
                (src, src_before, None),
                (kilix95, kilix95_before, "main"),
            ):
                actual = subprocess.check_output(
                    ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
                ).strip()
                self.assertEqual(actual, expected)
                branch = subprocess.run(
                    ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
                    text=True,
                    capture_output=True,
                )
                if expected_branch is None:
                    self.assertNotEqual(branch.returncode, 0)
                else:
                    self.assertEqual(branch.stdout.strip(), expected_branch)
            self.assertIn("old-fork", fork.read_text())
            self.assertIn("old-kitten", kitten.read_text())
            self.assertEqual(os.readlink(current), "generations/build.OldCurrent")
            self.assertEqual(os.readlink(previous), "generations/build.OldPrevious")
            self.assertEqual(
                (os.lstat(current).st_dev, os.lstat(current).st_ino),
                current_identity,
            )
            self.assertEqual(
                (os.lstat(previous).st_dev, os.lstat(previous).st_ino),
                previous_identity,
            )
            self.assertEqual(
                (old_previous_generation / "sentinel").read_text(),
                "older-generation\n",
            )
            self.assertFalse(new_generation.exists())
            self.assertEqual(stamp.read_text(), "old-stamp\n")
            self.assertEqual(legacy_stamp.read_text(), "legacy-stamp\n")
            self.assertEqual(list(state.glob("update-rollback.*")), [])

            committed = subprocess.run(
                [
                    "bash",
                    "-c",
                    textwrap.dedent(
                        f"""
                        set -euo pipefail
                        PLEB_ROOT={ROOT!s}
                        PLEB_STATE_HOME={state!s}
                        KILIX_STORAGE_HOME={build.parent!s}
                        KILIX_STATE_DIRECTORY={kilix_state!s}
                        KILIX_DIR={kilix!s}
                        KILIX_BUILD_DIRECTORY={build!s}
                        KILIX95_DIR={kilix95!s}
                        . "$PLEB_ROOT/lib/common.sh"
                        . "$PLEB_ROOT/lib/update.sh"
                        _acquire_update_lock
                        _update_transaction_begin
                        _update_transaction_commit
                        """
                    ),
                ],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
            )
            self.assertEqual(committed.returncode, 0, committed.stderr)
            self.assertFalse(legacy_stamp.exists())

    def test_dirty_checkout_is_rejected_before_update(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            checkout = tmp / "checkout"
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            (checkout / "local-notes.txt").write_text("do not overwrite\n")
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                . "$PLEB_ROOT/lib/common.sh"
                require_clean_checkout {checkout!s} kilix
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("local-notes.txt", result.stderr)
            self.assertIn("refusing to update", result.stderr)

    def test_pinned_checkout_uses_fetched_ref_not_poisoned_local_tag(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            remote = tmp / "remote.git"
            seed = tmp / "seed"
            checkout = tmp / "checkout"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
            for repo in (seed,):
                subprocess.run(["git", "-C", str(repo), "config", "user.name", "Pleb Test"], check=True)
                subprocess.run(
                    ["git", "-C", str(repo), "config", "user.email", "pleb@example.invalid"],
                    check=True,
                )
            (seed / "payload").write_text("trusted remote commit\n")
            subprocess.run(["git", "-C", str(seed), "add", "payload"], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "remote"], check=True)
            subprocess.run(["git", "-C", str(seed), "tag", "release"], check=True)
            subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main", "release"], check=True)
            subprocess.run(
                ["git", f"--git-dir={remote}", "symbolic-ref", "HEAD", "refs/heads/main"],
                check=True,
            )
            subprocess.run(["git", "clone", "-q", str(remote), str(checkout)], check=True)
            subprocess.run(["git", "-C", str(checkout), "checkout", "-q", "main"], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Pleb Test"], check=True)
            subprocess.run(
                ["git", "-C", str(checkout), "config", "user.email", "pleb@example.invalid"],
                check=True,
            )
            (checkout / "payload").write_text("poisoned local commit\n")
            subprocess.run(["git", "-C", str(checkout), "commit", "-qam", "local"], check=True)
            subprocess.run(
                ["git", "-C", str(checkout), "tag", "-f", "release"],
                check=True,
                capture_output=True,
            )
            poisoned = subprocess.check_output(
                ["git", "-C", str(checkout), "rev-parse", "release^{commit}"], text=True
            ).strip()
            trusted = subprocess.check_output(
                ["git", f"--git-dir={remote}", "rev-parse", "release^{commit}"], text=True
            ).strip()
            self.assertNotEqual(poisoned, trusted)

            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                . "$PLEB_ROOT/lib/common.sh"
                require_clean_checkout {checkout!s} kilix
                if (require_immutable_ref release 0 KILIX_REF KILIX_ALLOW_MUTABLE_REF); then exit 99; fi
                require_immutable_ref release 1 KILIX_REF KILIX_ALLOW_MUTABLE_REF
                checkout_fetched_ref {checkout!s} release kilix
                require_immutable_ref {trusted!s} 0 KILIX_REF KILIX_ALLOW_MUTABLE_REF
                checkout_fetched_ref {checkout!s} {trusted!s} kilix
                """
            )
            subprocess.run(
                ["bash", "-c", script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
                check=True,
            )
            head = subprocess.check_output(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
            ).strip()
            self.assertEqual(head, trusted)

    def test_engine_bootstrap_receives_persisted_prebuilt_pin(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            checkout = tmp / "kilix"
            checkout.mkdir()
            marker = checkout / "ready"
            observed = tmp / "observed"
            write_executable(
                checkout / "kilix",
                f"""#!/bin/sh
if [ "${{1:-}}" = --which ] && [ -f {marker!s} ]; then
    echo /verified/prebuilt/engine
    exit 0
fi
exit 1
""",
            )
            write_executable(
                checkout / "bootstrap.sh",
                f"""#!/bin/sh
printf '%s\n%s\n' "$KILIX_PREBUILT_VERSION" "$KILIX_PREBUILT_SHA256" >{observed!s}
touch {marker!s}
""",
            )
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                KILIX_DIR={checkout!s}
                KILIX_PREBUILT_VERSION=kitty-9.9.9
                KILIX_PREBUILT_SHA256={'a' * 64}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/install.sh"
                ensure_engine
                """
            )
            subprocess.run(
                ["bash", "-c", script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(observed.read_text(), f"kitty-9.9.9\n{'a' * 64}\n")

    def test_existing_engine_does_not_bypass_configured_prebuilt_pin(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            checkout = tmp / "kilix"
            checkout.mkdir()
            observed = tmp / "bootstrap-called"
            write_executable(
                checkout / "kilix",
                "#!/bin/sh\n[ \"${1:-}\" = --which ] && { echo /existing/engine; exit 0; }\nexit 1\n",
            )
            write_executable(
                checkout / "bootstrap.sh",
                f"#!/bin/sh\nprintf '%s\\n%s\\n' \"$KILIX_PREBUILT_VERSION\" \"$KILIX_PREBUILT_SHA256\" >{observed!s}\n",
            )
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                KILIX_DIR={checkout!s}
                KILIX_PREBUILT_VERSION=0.47.0
                KILIX_PREBUILT_SHA256={'b' * 64}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/install.sh"
                ensure_engine
                """
            )
            subprocess.run(
                ["bash", "-c", script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(observed.read_text(), f"0.47.0\n{'b' * 64}\n")

    def test_engine_bootstrap_success_must_produce_a_runnable_engine(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            checkout = tmp / "kilix"
            checkout.mkdir()
            write_executable(checkout / "kilix", "#!/bin/sh\nexit 1\n")
            write_executable(checkout / "bootstrap.sh", "#!/bin/sh\nexit 0\n")
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                KILIX_DIR={checkout!s}
                KILIX_PREBUILT_VERSION=0.47.0
                KILIX_PREBUILT_SHA256={'c' * 64}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/install.sh"
                ensure_engine
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no runnable engine", result.stderr)

    def test_unverified_engine_prompt_follows_the_bootstrap_url_display(self):
        text = (ROOT / "lib/install.sh").read_text()
        display = text.index("KILIX_ALLOW_UNVERIFIED_PREBUILT=0")
        prompt = text.index("Allow bootstrap.sh to download that unverified asset?")
        self.assertLess(display, prompt)
        self.assertNotIn("displayed unverified asset", text)

    def test_fork_build_contract_uses_one_coherent_canonical_identity(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            kilix = tmp / "kilix-real"
            src = kilix / "src"
            subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
            subprocess.run(["git", "-C", str(src), "config", "user.name", "Pleb Test"], check=True)
            subprocess.run(
                ["git", "-C", str(src), "config", "user.email", "pleb@example.invalid"],
                check=True,
            )
            (src / "tracked").write_text("source\n")
            subprocess.run(["git", "-C", str(src), "add", "tracked"], check=True)
            subprocess.run(["git", "-C", str(src), "commit", "-q", "-m", "source"], check=True)
            head = subprocess.check_output(
                ["git", "-C", str(src), "rev-parse", "HEAD"], text=True
            ).strip()
            checkout = tmp / "kilix-link"
            checkout.symlink_to(kilix, target_is_directory=True)

            build = tmp / "kilix-storage" / "build"
            generation = build / "generations/build.Valid"
            (generation / "src/kitty/launcher").mkdir(parents=True)
            (build / "current").symlink_to("generations/build.Valid")
            build.chmod(0o700)
            fork = build / "current/src/kitty/launcher/kitty"
            kitten = build / "current/src/kitty/launcher/kitten"
            write_executable(fork, "#!/bin/sh\nexit 0\n")
            write_executable(kitten, "#!/bin/sh\nexit 0\n")
            source_id = build / "current/source-id"
            source_id.write_text(head + "\n")

            kilix_state = tmp / "kilix-storage" / "state"
            kilix_state.mkdir()
            kilix_state.chmod(0o700)
            stamp = kilix_state / "fork-built-ref"
            stamp.write_text(f"{kilix.resolve()}\t{head}\n")
            stamp.chmod(0o600)
            pleb_state = tmp / "pleb-state"

            launcher = kilix / "kilix"

            def write_launcher(engine: Path = fork, rc: int = 0) -> None:
                write_executable(
                    launcher,
                    "#!/bin/sh\n"
                    "[ \"${1:-}\" = --which ] || exit 2\n"
                    f"printf '%s\\n' '{engine}'\n"
                    "printf '%s\\n' 'kilix-test 1.0'\n"
                    f"exit {rc}\n",
                )

            write_launcher()
            prefix = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                PLEB_STATE_HOME={pleb_state!s}
                KILIX_STORAGE_HOME={build.parent!s}
                KILIX_STATE_DIRECTORY={kilix_state!s}
                KILIX_DIR={checkout!s}
                KILIX_BUILD_DIRECTORY={build!s}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/update.sh"
                """
            )

            def run(body: str):
                return subprocess.run(
                    ["bash", "-c", prefix + body],
                    env=clean_env(tmp),
                    text=True,
                    capture_output=True,
                )

            valid = run("_verify_kilix_fork_build\n! _kilix_fork_needs_rebuild\n")
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertFalse((pleb_state / "kilix-fork-built-ref").exists())

            source_id.write_text("wrong\n")
            self.assertEqual(run("_kilix_fork_needs_rebuild\n").returncode, 0)
            source_id.write_text(head + "\n\n")
            self.assertEqual(run("_kilix_fork_needs_rebuild\n").returncode, 0)
            source_id.write_text(head + "\n")

            stamp.write_text("wrong\n")
            self.assertEqual(run("_kilix_fork_needs_rebuild\n").returncode, 0)
            stamp.write_text(f"{kilix.resolve()}\t{head}\n\n")
            self.assertEqual(run("_kilix_fork_needs_rebuild\n").returncode, 0)
            stamp.write_text(f"{kilix.resolve()}\t{head}\n")

            kitten.unlink()
            self.assertEqual(run("_kilix_fork_needs_rebuild\n").returncode, 0)
            write_executable(kitten, "#!/bin/sh\nexit 0\n")

            write_executable(kitten, "#!/bin/sh\nexit 74\n")
            self.assertEqual(run("_kilix_fork_needs_rebuild\n").returncode, 0)
            broken_kitten = run("_verify_kilix_fork_build\n")
            self.assertNotEqual(broken_kitten.returncode, 0)
            self.assertIn("kitten failed", broken_kitten.stderr)
            write_executable(kitten, "#!/bin/sh\nexit 0\n")

            (build / "current").unlink()
            (build / "current").symlink_to(generation)
            self.assertEqual(run("_kilix_fork_needs_rebuild\n").returncode, 0)
            unsafe_generation = run("_verify_kilix_fork_build\n")
            self.assertNotEqual(unsafe_generation.returncode, 0)
            self.assertIn("contained current generation", unsafe_generation.stderr)
            (build / "current").unlink()
            (build / "current").symlink_to("generations/build.Valid")

            write_launcher(tmp / "wrong-engine")
            self.assertEqual(run("_kilix_fork_needs_rebuild\n").returncode, 0)

            write_launcher(rc=73)
            self.assertEqual(run("_kilix_fork_needs_rebuild\n").returncode, 0)
            failed_probe = run("_verify_kilix_fork_build\n")
            self.assertNotEqual(failed_probe.returncode, 0)
            self.assertIn("failed its post-build version probe", failed_probe.stderr)

            write_launcher()
            stamp.chmod(0o644)
            unsafe_mode = run("_validate_kilix_fork_stamp_path\n")
            self.assertNotEqual(unsafe_mode.returncode, 0)
            self.assertIn("mode 0600", unsafe_mode.stderr)

            stamp.chmod(0o600)
            alias = kilix_state / "fork-built-ref.alias"
            os.link(stamp, alias)
            unsafe_links = run("_validate_kilix_fork_stamp_path\n")
            self.assertNotEqual(unsafe_links.returncode, 0)
            self.assertIn("exactly one hard link", unsafe_links.stderr)
            alias.unlink()

            kilix_state.chmod(0o777)
            unsafe_state_mode = run("_validate_kilix_fork_stamp_path\n")
            self.assertNotEqual(unsafe_state_mode.returncode, 0)
            self.assertIn("mode 0700", unsafe_state_mode.stderr)
            kilix_state.chmod(0o700)

            source_storage = run(
                f"KILIX_STORAGE_HOME={kilix.resolve()!s}\n"
                f"KILIX_STATE_DIRECTORY={kilix.resolve() / 'state'!s}\n"
                "_validate_kilix_fork_stamp_path\n"
            )
            self.assertNotEqual(source_storage.returncode, 0)
            self.assertIn("Kilix checkout", source_storage.stderr)

            broad_storage = run(
                "KILIX_STORAGE_HOME=$HOME\n"
                "KILIX_STATE_DIRECTORY=$HOME/state\n"
                "_validate_kilix_fork_stamp_path\n"
            )
            self.assertNotEqual(broad_storage.returncode, 0)
            self.assertIn("too broad", broad_storage.stderr)

    def test_fork_build_stamp_is_canonical_kilix_state_not_checkout_or_pleb_state(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pleb_state = tmp / "xdg-state" / "pleb"
            kilix_state = tmp / "xdg-state" / "kilix"
            checkout = tmp / "kilix"
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                PLEB_STATE_HOME={pleb_state!s}
                KILIX_STATE_DIRECTORY={kilix_state!s}
                KILIX_DIR={checkout!s}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/update.sh"
                _kilix_fork_stamp
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                env=clean_env(tmp),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stdout.strip(), str(kilix_state / "fork-built-ref"))
            self.assertNotIn(str(checkout), result.stdout)
            self.assertNotIn(str(pleb_state), result.stdout)

    def test_go_build_check_requires_the_configured_exact_release(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake_bin = tmp / "bin"
            fake_bin.mkdir()
            write_executable(
                fake_bin / "go",
                "#!/bin/sh\necho 'go version go1.27.1 linux/amd64'\n",
            )
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/update.sh"
                export PLEBIAN_OS_KILIX_GO_MIN_VERSION=1.26
                export PLEBIAN_OS_KILIX_GO_VERSION=go1.26.4
                ! bash -c "$(_kilix_go_ok_script)"
                PLEBIAN_OS_KILIX_GO_VERSION=go1.27.1
                bash -c "$(_kilix_go_ok_script)"
                """
            )
            env = clean_env(tmp)
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            subprocess.run(
                ["bash", "-c", script],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

    def test_pinned_go_is_reinstalled_when_provenance_stamp_mismatches(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake_root = tmp / "fake-go"
            fake_bin = fake_root / "bin"
            fake_bin.mkdir(parents=True)
            install_root = tmp / "pleb-install"
            (install_root / "scripts").mkdir(parents=True)
            called = tmp / "install-called"
            expected = "d" * 64
            (fake_root / ".pleb-source").write_text(f"go1.26.4\namd64\n{'e' * 64}\n")
            write_executable(
                fake_bin / "go",
                f"""#!/bin/sh
case "${{1:-}}" in
  version) echo 'go version go1.26.4 linux/amd64' ;;
  env) [ "${{2:-}}" = GOROOT ] && echo {fake_root!s} ;;
  *) exit 1 ;;
esac
""",
            )
            # Unit tests run unprivileged. This stat shim models the root-owned,
            # read-only stamp that the real installer creates through sudo.
            write_executable(
                fake_bin / "stat",
                """#!/bin/sh
case "$2" in
  %u) echo 0 ;;
  %a) echo 444 ;;
  *) exec /usr/bin/stat "$@" ;;
esac
""",
            )
            write_executable(
                install_root / "scripts/install-go.sh",
                f"""#!/bin/sh
set -eu
printf '%s\n%s\n%s\n' "$GO_VERSION" amd64 "$GO_SHA256" >{fake_root / '.pleb-source'!s}
touch {called!s}
""",
            )
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/update.sh"
                PLEB_ROOT={install_root!s}
                GO_BIN_DIR={fake_bin!s}
                PLEBIAN_OS_KILIX_GO_VERSION=go1.26.4
                PLEBIAN_OS_KILIX_GO_SHA256_AMD64={expected}
                _ensure_go_for_kilix_build
                """
            )
            env = clean_env(tmp)
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            subprocess.run(
                ["bash", "-c", script],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertTrue(called.exists())
            self.assertEqual(
                (fake_root / ".pleb-source").read_text(),
                f"go1.26.4\namd64\n{expected}\n",
            )

    def test_pinned_go_rejects_a_malformed_sha_even_when_version_matches(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake_bin = tmp / "bin"
            fake_bin.mkdir()
            write_executable(
                fake_bin / "go",
                "#!/bin/sh\necho 'go version go1.26.4 linux/amd64'\n",
            )
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/update.sh"
                GO_BIN_DIR={fake_bin!s}
                PLEBIAN_OS_KILIX_GO_VERSION=go1.26.4
                PLEBIAN_OS_KILIX_GO_SHA256_AMD64=not-a-sha
                _ensure_go_for_kilix_build
                """
            )
            env = clean_env(tmp)
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            result = subprocess.run(
                ["bash", "-c", script],
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected 64 hexadecimal", result.stderr)

    def test_pinned_go_fetch_uses_supplied_arch_hash_without_live_metadata(self):
        machine = os.uname().machine
        if machine in ("x86_64", "amd64"):
            go_arch, suffix = "amd64", "AMD64"
        elif machine in ("aarch64", "arm64"):
            go_arch, suffix = "arm64", "ARM64"
        else:
            self.skipTest(f"unsupported Go test architecture: {machine}")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake_bin = tmp / "bin"
            cache = tmp / ".local/gpu_terminal/pleb/cache/go"
            fake_bin.mkdir()
            archive = tmp / "download"
            archive.write_bytes(f"archive-for-{machine}".encode())
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            curl_log = tmp / "curl.log"
            cache.mkdir(parents=True)
            (cache / f"go1.26.4.linux-{go_arch}.tar.gz").write_bytes(
                archive.read_bytes()
            )
            write_executable(
                fake_bin / "curl",
                """#!/bin/sh
printf '%s\n' "$*" >>"$CURL_LOG"
out=
while [ "$#" -gt 0 ]; do
    if [ "$1" = -o ]; then out=$2; shift 2; else shift; fi
done
[ -n "$out" ] || exit 91
cp "$GO_TEST_ARCHIVE" "$out"
""",
            )
            env = clean_env(tmp)
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "GO_CACHE": str(cache),
                    "GO_TEST_ARCHIVE": str(archive),
                    "CURL_LOG": str(curl_log),
                    "PLEBIAN_OS_KILIX_GO_VERSION": "go1.26.4",
                    f"PLEBIAN_OS_KILIX_GO_SHA256_{suffix}": checksum,
                }
            )
            subprocess.run(
                [str(ROOT / "scripts/install-go.sh"), "fetch"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            storage = tmp / ".local/gpu_terminal/pleb"
            for path in (
                storage,
                *(storage / name for name in ("config", "state", "cache", "session", "data")),
            ):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)
            self.assertFalse(curl_log.exists())

    def test_failed_go_swap_restores_tree_and_command_links(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cache = tmp / ".local/gpu_terminal/pleb/cache/go"
            install_dir = tmp / "local/go"
            bin_dir = tmp / "local/bin"
            fake_bin = tmp / "fake-bin"
            cache.mkdir(parents=True)
            (install_dir / "bin").mkdir(parents=True)
            bin_dir.mkdir(parents=True)
            fake_bin.mkdir()
            (install_dir / "old-marker").write_text("old toolchain\n")
            write_executable(install_dir / "bin/go", "#!/bin/sh\necho old-go\n")
            (bin_dir / "go").write_text("old go command\n")
            (bin_dir / "gofmt").write_text("old gofmt command\n")

            archive = cache / "go1.26.4.linux-amd64.tar.gz"
            make_go_archive(archive)
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            (cache / ".manifest").write_text(f"go1.26.4\namd64\n{checksum}\n")
            write_executable(fake_bin / "uname", "#!/bin/sh\necho x86_64\n")
            env = clean_env(tmp)
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "GO_CACHE": str(cache),
                    "GO_INSTALL_DIR": str(install_dir),
                    "GO_BIN_DIR": str(bin_dir),
                    "GO_VERSION": "go1.26.4",
                    "GO_SHA256": checksum,
                    "GO_INSTALL_SCRIPT": str(ROOT / "scripts/install-go.sh"),
                }
            )
            runner = """
set -euo pipefail
source "$GO_INSTALL_SCRIPT"
validate_install_destinations() { :; }
run_root() {
    [ "${1:-}" != ln ] || return 73
    "$SYSTEM_ENV" -i PATH="$TRUSTED_SYSTEM_PATH" HOME="$HOME" LC_ALL=C "$@"
}
do_install
"""
            result = subprocess.run(
                ["bash", "-c", runner],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((install_dir / "old-marker").read_text(), "old toolchain\n")
            self.assertEqual((bin_dir / "go").read_text(), "old go command\n")
            self.assertEqual((bin_dir / "gofmt").read_text(), "old gofmt command\n")
            self.assertEqual(list((tmp / "local").glob(".pleb-go-stage.*")), [])
            self.assertIn("restored the previous Go toolchain", result.stdout)

    def test_successful_go_swap_installs_validated_tree_and_links(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cache = tmp / ".local/gpu_terminal/pleb/cache/go"
            install_dir = tmp / "local/go"
            bin_dir = tmp / "local/bin"
            fake_bin = tmp / "fake-bin"
            cache.mkdir(parents=True)
            bin_dir.mkdir(parents=True)
            fake_bin.mkdir()
            archive = cache / "go1.26.4.linux-amd64.tar.gz"
            make_go_archive(archive)
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            (cache / ".manifest").write_text(f"go1.26.4\namd64\n{checksum}\n")
            write_executable(fake_bin / "uname", "#!/bin/sh\necho x86_64\n")
            env = clean_env(tmp)
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "GO_CACHE": str(cache),
                    "GO_INSTALL_DIR": str(install_dir),
                    "GO_BIN_DIR": str(bin_dir),
                    "GO_VERSION": "go1.26.4",
                    "GO_SHA256": checksum,
                    "GO_INSTALL_SCRIPT": str(ROOT / "scripts/install-go.sh"),
                }
            )
            runner = """
set -euo pipefail
source "$GO_INSTALL_SCRIPT"
validate_install_destinations() { :; }
run_root() {
    "$SYSTEM_ENV" -i PATH="$TRUSTED_SYSTEM_PATH" HOME="$HOME" LC_ALL=C "$@"
}
do_install
"""
            result = subprocess.run(
                ["bash", "-c", runner],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("go version go1.26.4 linux/amd64", result.stdout)
            self.assertEqual(
                subprocess.check_output([str(bin_dir / "go")], text=True).strip(),
                "go version go1.26.4 linux/amd64",
            )
            self.assertEqual(os.readlink(bin_dir / "go"), str(install_dir / "bin/go"))
            self.assertEqual(os.readlink(bin_dir / "gofmt"), str(install_dir / "bin/gofmt"))
            self.assertEqual(
                (install_dir / ".pleb-source").read_text(),
                f"go1.26.4\namd64\n{checksum}\n",
            )
            self.assertEqual((install_dir / ".pleb-source").stat().st_mode & 0o777, 0o444)
            self.assertEqual(list((tmp / "local").glob(".pleb-go-stage.*")), [])
            storage = tmp / ".local/gpu_terminal/pleb"
            for path in (
                storage,
                *(storage / name for name in ("config", "state", "cache", "session", "data")),
            ):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)

    def test_go_cache_preflight_rejects_traversal_and_unsafe_external_paths(self):
        # External-cache ancestry deliberately cannot live below /tmp: that
        # shared world-writable component is exactly what the preflight rejects.
        with tempfile.TemporaryDirectory(dir=Path.home()) as td:
            tmp = Path(td)

            def attempt(cache: Path | str) -> subprocess.CompletedProcess[str]:
                env = clean_env(tmp)
                env["GO_CACHE"] = str(cache)
                return subprocess.run(
                    [str(ROOT / "scripts/install-go.sh"), "install"],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    capture_output=True,
                )

            pleb_cache = tmp / ".local/gpu_terminal/pleb/cache"
            traversal = attempt(f"{pleb_cache}/../../outside")
            self.assertNotEqual(traversal.returncode, 0)
            self.assertIn("normalized absolute path", traversal.stderr)

            target = tmp / "external-target"
            target.mkdir(mode=0o700)
            linked = tmp / "external-link"
            linked.symlink_to(target, target_is_directory=True)
            symlinked = attempt(linked)
            self.assertNotEqual(symlinked.returncode, 0)
            self.assertIn("symlink component", symlinked.stderr)

            writable_parent = tmp / "writable-parent"
            writable_parent.mkdir(mode=0o700)
            writable_parent.chmod(0o777)
            nested = writable_parent / "cache"
            nested.mkdir(mode=0o700)
            writable = attempt(nested)
            self.assertNotEqual(writable.returncode, 0)
            self.assertIn("group/world-writable", writable.stderr)

            loose_leaf = tmp / "loose-cache"
            loose_leaf.mkdir(mode=0o755)
            loose = attempt(loose_leaf)
            self.assertNotEqual(loose.returncode, 0)
            self.assertIn("must have mode 0700", loose.stderr)

    def test_go_install_destinations_are_fixed_before_storage_or_sudo(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for variable, value in (
                ("GO_INSTALL_DIR", tmp / "user-owned/go"),
                ("GO_BIN_DIR", tmp / "user-owned/bin"),
            ):
                with self.subTest(variable=variable):
                    env = clean_env(tmp)
                    env[variable] = str(value)
                    result = subprocess.run(
                        [str(ROOT / "scripts/install-go.sh"), "install"],
                        cwd=ROOT,
                        env=env,
                        text=True,
                        capture_output=True,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("is fixed at /usr/local/", result.stderr)
                    self.assertFalse((tmp / ".local/gpu_terminal/pleb").exists())

    def test_go_installer_privileged_startup_ignores_bash_env_functions(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            marker = tmp / "poisoned-set-reached"
            bash_env = tmp / "poison-set.sh"
            bash_env.write_text(
                f"set() {{ /usr/bin/touch {marker!s}; builtin set \"$@\"; }}\n"
                "export -f set\n"
            )
            env = clean_env(tmp)
            env.update(
                {
                    "BASH_ENV": str(bash_env),
                    # This fails safely at the destination gate after startup.
                    "GO_INSTALL_DIR": str(tmp / "user-owned/go"),
                }
            )
            result = subprocess.run(
                [str(ROOT / "scripts/install-go.sh"), "install"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("fixed at /usr/local/go", result.stderr)
            self.assertFalse(marker.exists())

    def test_direct_go_install_rejects_path_and_startup_hook_poisoning(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cache = tmp / ".local/gpu_terminal/pleb/cache/go"
            fake_bin = tmp / "fake-bin"
            cache.mkdir(parents=True)
            fake_bin.mkdir()
            archive = cache / "go1.26.4.linux-amd64.tar.gz"
            make_go_archive(archive)
            fabricated = hashlib.sha256(archive.read_bytes()).hexdigest()
            (cache / ".manifest").write_text(f"go1.26.4\namd64\n{fabricated}\n")
            write_executable(fake_bin / "uname", "#!/bin/sh\necho x86_64\n")
            fake_tool_marker = tmp / "path-tool-reached"
            attacker_json = (
                '[{"version":"go1.26.4","files":['
                '{"filename":"go1.26.4.linux-amd64.tar.gz",'
                f'"sha256":"{fabricated}"}}]}}]'
            )
            write_executable(
                fake_bin / "curl",
                f"#!/bin/sh\ntouch {fake_tool_marker!s}\nprintf '%s\\n' '{attacker_json}'\n",
            )
            write_executable(
                fake_bin / "python3",
                f"#!/bin/sh\ntouch {fake_tool_marker!s}\nprintf '%s\\n' {fabricated}\n",
            )
            for tool in ("env", "id", "sudo", "sha256sum"):
                write_executable(
                    fake_bin / tool,
                    f"#!/bin/sh\ntouch {fake_tool_marker!s}\nexit 99\n",
                )

            function_marker = tmp / "exported-function-reached"
            bash_env = tmp / "poison-functions.sh"
            bash_env.write_text(
                f"""
curl() {{ /usr/bin/touch {function_marker!s}; /usr/bin/printf '%s\\n' '{attacker_json}'; }}
python3() {{ /usr/bin/touch {function_marker!s}; /usr/bin/printf '%s\\n' {fabricated}; }}
env() {{ /usr/bin/touch {function_marker!s}; /usr/bin/env "$@"; }}
id() {{ /usr/bin/touch {function_marker!s}; /usr/bin/id "$@"; }}
sudo() {{ /usr/bin/touch {function_marker!s}; return 99; }}
sha256sum() {{ /usr/bin/touch {function_marker!s}; /usr/bin/printf '%s  %s\\n' {fabricated} "$1"; }}
chmod() {{ /usr/bin/touch {function_marker!s}; want={fabricated}; _FETCHED_SHA={fabricated}; /usr/bin/chmod "$@"; }}
mv() {{ /usr/bin/touch {function_marker!s}; want={fabricated}; _FETCHED_SHA={fabricated}; /usr/bin/mv "$@"; }}
set() {{ /usr/bin/touch {function_marker!s}; builtin set "$@"; }}
export -f curl python3 env id sudo sha256sum chmod mv set
"""
            )
            curlrc_marker = tmp / "curlrc-reached"
            (tmp / ".curlrc").write_text(f"--trace-ascii {curlrc_marker!s}\n")
            python_hook_marker = tmp / "python-hook-reached"
            python_path = tmp / "python-poison"
            python_path.mkdir()
            (python_path / "sitecustomize.py").write_text(
                f"from pathlib import Path\nPath({str(python_hook_marker)!r}).touch()\n"
            )
            env = clean_env(tmp)
            for name in ("GO_VERSION", "GO_SHA256"):
                env.pop(name, None)
            root_staging_marker = tmp / "root-staging-reached"
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "GO_CACHE": str(cache),
                    "GO_INSTALL_DIR": str(tmp / "local/go"),
                    "GO_BIN_DIR": str(tmp / "local/bin"),
                    "BASH_ENV": str(bash_env),
                    "POISON_FUNCTIONS": str(bash_env),
                    "PYTHONPATH": str(python_path),
                    "GO_INSTALL_SCRIPT": str(ROOT / "scripts/install-go.sh"),
                    "ROOT_STAGING_MARKER": str(root_staging_marker),
                }
            )
            runner = """
set -euo pipefail
source "$POISON_FUNCTIONS"
# /bin/bash -p prevents inherited `set`; remove the explicitly sourced copy so
# install-go.sh can execute its first builtin statement and purge every other
# poison function before doing any work.
unset -f set
source "$GO_INSTALL_SCRIPT"
validate_install_destinations() { :; }
run_root() {
    /usr/bin/touch "$ROOT_STAGING_MARKER"
    return 99
}
do_install
"""
            result = subprocess.run(
                ["/bin/bash", "-p", "-c", runner],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("staging verified", result.stdout)
            self.assertFalse(fake_tool_marker.exists())
            self.assertFalse(function_marker.exists())
            self.assertFalse(curlrc_marker.exists())
            self.assertFalse(python_hook_marker.exists())
            self.assertFalse(root_staging_marker.exists())
            if "could not independently verify the cached Go archive" in result.stderr:
                self.skipTest("go.dev checksum metadata is unavailable")
            self.assertIn(
                "manifest does not match the independently trusted official checksum",
                result.stderr,
            )
            self.assertEqual(
                (ROOT / "scripts/install-go.sh").read_text().splitlines()[0],
                "#!/bin/bash -p",
            )


if __name__ == "__main__":
    unittest.main()
