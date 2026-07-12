import fcntl
import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def clean_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("KILIX", "PLEB")):
            env.pop(key)
    env["HOME"] = str(home)
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
            state = tmp / "state"
            state.mkdir()
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

    def test_fork_build_stamp_is_xdg_state_not_checkout_state(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = tmp / "xdg-state" / "pleb"
            checkout = tmp / "kilix"
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                PLEB_ROOT={ROOT!s}
                PLEB_STATE_HOME={state!s}
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
            self.assertEqual(result.stdout.strip(), str(state / "kilix-fork-built-ref"))
            self.assertNotIn(str(checkout), result.stdout)

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

    def test_pinned_go_fetch_uses_supplied_arch_hash_without_live_metadata(self):
        cases = (
            ("x86_64", "AMD64"),
            ("aarch64", "ARM64"),
        )
        for machine, suffix in cases:
            with self.subTest(machine=machine), tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                fake_bin = tmp / "bin"
                cache = tmp / "cache"
                fake_bin.mkdir()
                archive = tmp / "download"
                archive.write_bytes(f"archive-for-{machine}".encode())
                checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
                curl_log = tmp / "curl.log"
                write_executable(fake_bin / "uname", f"#!/bin/sh\necho {machine}\n")
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
                calls = curl_log.read_text().splitlines()
                self.assertEqual(len(calls), 1)
                self.assertIn("https://go.dev/dl/go1.26.4.linux-", calls[0])
                self.assertNotIn("VERSION?m=text", calls[0])
                self.assertNotIn("mode=json", calls[0])

    def test_failed_go_swap_restores_tree_and_command_links(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cache = tmp / "cache"
            install_dir = tmp / "local/go"
            bin_dir = tmp / "local/bin"
            fake_bin = tmp / "fake-bin"
            cache.mkdir()
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
            write_executable(
                fake_bin / "sudo",
                """#!/bin/sh
[ "${1:-}" = -- ] && shift
[ "${1:-}" = ln ] && exit 73
exec "$@"
""",
            )
            env = clean_env(tmp)
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "GO_CACHE": str(cache),
                    "GO_INSTALL_DIR": str(install_dir),
                    "GO_BIN_DIR": str(bin_dir),
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
            self.assertEqual((install_dir / "old-marker").read_text(), "old toolchain\n")
            self.assertEqual((bin_dir / "go").read_text(), "old go command\n")
            self.assertEqual((bin_dir / "gofmt").read_text(), "old gofmt command\n")
            self.assertEqual(list((tmp / "local").glob(".pleb-go-stage.*")), [])
            self.assertIn("restored the previous Go toolchain", result.stdout)

    def test_successful_go_swap_installs_validated_tree_and_links(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cache = tmp / "cache"
            install_dir = tmp / "local/go"
            bin_dir = tmp / "local/bin"
            fake_bin = tmp / "fake-bin"
            cache.mkdir()
            bin_dir.mkdir(parents=True)
            fake_bin.mkdir()
            archive = cache / "go1.26.4.linux-amd64.tar.gz"
            make_go_archive(archive)
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            (cache / ".manifest").write_text(f"go1.26.4\namd64\n{checksum}\n")
            write_executable(fake_bin / "uname", "#!/bin/sh\necho x86_64\n")
            write_executable(
                fake_bin / "sudo",
                """#!/bin/sh
[ "${1:-}" = -- ] && shift
exec "$@"
""",
            )
            env = clean_env(tmp)
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "GO_CACHE": str(cache),
                    "GO_INSTALL_DIR": str(install_dir),
                    "GO_BIN_DIR": str(bin_dir),
                }
            )
            result = subprocess.run(
                [str(ROOT / "scripts/install-go.sh"), "install"],
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
            self.assertEqual(list((tmp / "local").glob(".pleb-go-stage.*")), [])


if __name__ == "__main__":
    unittest.main()
