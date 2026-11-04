"""Release-hop entrypoint, publication lookup, and split-closure reporting."""

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLEB = ROOT / "bin/pleb"


class ReleaseHopTests(unittest.TestCase):
    def _release_remote(self, base: Path, versions=("0.2.0", "0.2.1", "0.2.2")) -> Path:
        remote = base / "plebian-os.git"
        work = base / "release-work"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        git = ["git", "-C", str(work)]
        subprocess.run(git + ["config", "user.name", "release test"], check=True)
        subprocess.run(
            git + ["config", "user.email", "release-test@example.invalid"],
            check=True,
        )
        subprocess.run(git + ["remote", "add", "origin", str(remote)], check=True)
        selector = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            printf '%s\n' "$*" >>"$PLEB_TEST_SELECTOR_LOG"
            exit "${PLEB_TEST_SELECTOR_STATUS:-0}"
            """
        )
        policy = textwrap.dedent(
            """\
            {
              "schema_version": 1,
              "default_supported_hop": "immediately_previous_published_release",
              "supported_upgrade_paths": []
            }
            """
        )
        for version in versions:
            (work / "provision").mkdir(exist_ok=True)
            (work / "releases").mkdir(exist_ok=True)
            (work / "VERSION").write_text(version + "\n")
            script = work / "provision/plebian-os-select-closure.sh"
            script.write_text(selector)
            script.chmod(0o755)
            (work / "releases" / f"{version}.env").write_text(
                f"PLEBIAN_OS_VERSION={version}\n"
            )
            (work / "releases/upgrade-policy.json").write_text(policy)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-qm", f"release {version}"], check=True)
            subprocess.run(git + ["tag", "-a", f"v{version}", "-m", f"v{version}"], check=True)
        subprocess.run(git + ["push", "-q", "origin", "HEAD:main", "--tags"], check=True)
        return remote

    def _env(self, base: Path, remote: Path) -> dict[str, str]:
        home = base / "home"
        data = home / ".local/gpu_terminal"
        storage = data / "pleb"
        config = storage / "config"
        sources = data / "sources"
        for path in (config, sources / "kilix", sources / "kilix-desktops/kilix-95"):
            path.mkdir(parents=True, exist_ok=True)
        session = config / "session.env"
        closure = config / "closure.env"
        session.write_text("PLEB_RESPAWN=0\n")
        closure.write_text(
            "PLEBIAN_OS_VERSION=0.2.0\n"
            f"PLEBIAN_OS_REPO={remote}\n"
            f"PLEB_REF={'1' * 40}\n"
            f"KILIX_REF={'2' * 40}\n"
            f"KILIX95_REF={'3' * 40}\n"
        )
        selector_log = base / "selector.log"
        updater_log = base / "updater.log"
        updater = base / "plebian-os-update"
        updater_marker = base / "updater-failed-once"
        updater.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >>{str(updater_log)!r}\n"
            f"if [ \"${{PLEB_TEST_UPDATER_FAIL_ONCE:-0}}\" = 1 ] "
            f"&& [ ! -e {str(updater_marker)!r} ]; then\n"
            f"  : >{str(updater_marker)!r}\n"
            "  exit 7\n"
            "fi\n"
        )
        updater.chmod(0o755)
        selector_dst = base / "plebian-os-select-closure"
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
            "PLEB_ENV_USER": str(session),
            "PLEB_CLOSURE_SYSTEM": str(base / "absent-system-closure.env"),
            "PLEB_CLOSURE_USER": str(closure),
            "KILIX_DIR": str(sources / "kilix"),
            "KILIX95_DIR": str(sources / "kilix-desktops/kilix-95"),
            "PLEBIAN_OS_MANAGED_INSTALL": "1",
            "PLEBIAN_OS_SELECTOR_DST": str(selector_dst),
            "PLEBIAN_OS_UPDATER_DST": str(updater),
            "PLEB_TEST_SELECTOR_LOG": str(selector_log),
        }

    def _rc_remote(self, base):
        remote = self._release_remote(base, ("0.2.0", "0.2.1"))
        git = ["git", "-C", str(base / "release-work")]
        subprocess.run(git + ["tag", "-d", "v0.2.1"], check=True, capture_output=True)
        subprocess.run(git + ["tag", "-a", "v0.2.1-rc1", "-m", "candidate"], check=True)
        subprocess.run(git + ["push", "-q", "origin", ":refs/tags/v0.2.1", "refs/tags/v0.2.1-rc1"], check=True)
        return remote

    def test_explicit_rc_online_offline_and_latest_stable_only(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            env = self._env(base, self._rc_remote(base))
            original = Path(env["PLEB_CLOSURE_USER"]).read_bytes()
            args = [str(PLEB), "update", "--to", "0.2.1", "--release-tag", "v0.2.1-rc1", "--dry-run"]
            for extra in ([], ["--offline"]):
                result = subprocess.run(args + extra, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--release-tag v0.2.1-rc1", (base / "selector.log").read_text())
                self.assertEqual(Path(env["PLEB_CLOSURE_USER"]).read_bytes(), original)
            (base / "selector.log").unlink()
            result = subprocess.run([str(PLEB), "update", "--latest", "--dry-run"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("already the latest", result.stdout)
            self.assertFalse((base / "selector.log").exists())
            self.assertFalse((base / "updater.log").exists())
            result = subprocess.run([str(PLEB), "update", "--to", "0.2.1", "--dry-run"], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_rc_rollback_cancellation_keeps_exact_tag_and_revalidates(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            env = self._env(base, self._rc_remote(base))
            result = subprocess.run([str(PLEB), "update", "--to", "0.2.1", "--release-tag", "v0.2.1-rc1", "--no-restart"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            state = Path(env["PLEB_STATE_HOME"]) / "release-hop"
            self.assertEqual((state / "active").read_text().splitlines()[-1], "v0.2.1-rc1")
            env["PLEB_TEST_UPDATER_FAIL_ONCE"] = "1"
            result = subprocess.run([str(PLEB), "update", "--rollback", "--no-restart"], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--release-tag v0.2.1-rc1", (base / "selector.log").read_text().splitlines()[-1])
            self.assertTrue(all(x == "--revalidate-current" for x in (base / "updater.log").read_text().splitlines()))
            (state / "phase").write_text("rolling-back\n")
            result = subprocess.run([str(PLEB), "update", "--rollback", "--no-restart"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (base / "selector.log").read_text().splitlines()
            self.assertIn("--release-tag v0.2.1-rc1", calls[-2])
            self.assertEqual(calls[-1], "--rollback")

    def test_legacy_four_line_stable_state_still_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            env = self._env(base, self._release_remote(base))
            result = subprocess.run([str(PLEB), "update", "--to", "0.2.1", "--no-restart"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            active = Path(env["PLEB_STATE_HOME"]) / "release-hop/active"
            active.write_text("\n".join(active.read_text().splitlines()[:4]) + "\n")
            result = subprocess.run([str(PLEB), "update", "--rollback", "--no-restart"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(active.exists())

    def test_invalid_rc_identity_refuses_before_lookup_or_selection(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            env = self._env(base, self._release_remote(base))
            for tag in ("v0.2.1-rc0", "v0.2.1-rc01", "v0.2.2-rc1", "v0.2.1-beta1", ""):
                with self.subTest(tag=tag):
                    result = subprocess.run([str(PLEB), "update", "--to", "0.2.1", "--release-tag", tag, "--dry-run"], env=env, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((base / "selector.log").exists())
            self.assertFalse((Path(env["PLEB_CACHE_HOME"]) / "release-hop").exists())

    def test_published_022_bootstrap_preserves_installed_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            env = self._env(base, self._rc_remote(base))
            installed = base / "installed-pleb"
            subprocess.run(["git", "clone", "-q", "--no-local", "--no-hardlinks", str(ROOT), str(installed)], check=True)
            old_ref = "2dd44b003b1d0d0ca4b06aa2721467590b5f71f7"
            subprocess.run(["git", "-C", str(installed), "checkout", "-q", "--detach", old_ref], check=True)
            driver = base / "driver"
            for name in ("bin/pleb-release-hop", "lib/update.sh", "lib/closure.sh", "lib/preserve.sh"):
                dst = driver / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes((ROOT / name).read_bytes())
                dst.chmod(0o755)
            git = ["git", "-C", str(driver)]
            subprocess.run(git + ["init", "-q"], check=True)
            subprocess.run(git + ["add", "."], check=True)
            subprocess.run(git + ["-c", "user.name=release test", "-c", "user.email=release-test@example.invalid", "commit", "-qm", "fixture driver"], check=True)
            ref = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
            hop = ["update", "--to", "0.2.1", "--release-tag", "v0.2.1-rc1", "--dry-run"]
            old = subprocess.run([str(installed / "bin/pleb"), *hop], env=env, capture_output=True, text=True)
            self.assertNotEqual(old.returncode, 0)
            self.assertIn("unknown update option", old.stderr)
            command = [str(driver / "bin/pleb-release-hop"), "--installed-pleb", str(installed / "bin/pleb"), "--driver-ref", ref, "--", *hop]
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--release-tag v0.2.1-rc1", (base / "selector.log").read_text())
            self.assertEqual(subprocess.check_output(["git", "-C", str(installed), "rev-parse", "HEAD"], text=True).strip(), old_ref)
            self.assertEqual(subprocess.check_output(["git", "-C", str(installed), "status", "--porcelain"], text=True), "")
            (base / "selector.log").unlink()
            with (driver / "lib/closure.sh").open("a") as stream:
                stream.write("\n# changed fixture\n")
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("driver modules differ", result.stderr)
            self.assertFalse((base / "selector.log").exists())

    def test_show_names_each_value_source_and_closure_path(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._release_remote(base)
            env = self._env(base, remote)
            result = subprocess.run(
                [str(PLEB), "update", "--show"], env=env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PLEBIAN_OS_VERSION=0.2.0", result.stdout)
            self.assertIn(env["PLEB_CLOSURE_USER"], result.stdout)
            self.assertIn("PLEB_REF=" + "1" * 40, result.stdout)

    def test_named_dry_run_executes_target_tag_selector_and_writes_no_stack(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._release_remote(base)
            env = self._env(base, remote)
            result = subprocess.run(
                [str(PLEB), "update", "--to", "0.2.1", "--dry-run"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            selector_call = (base / "selector.log").read_text()
            self.assertIn("0.2.1", selector_call)
            self.assertIn("--dry-run", selector_call)
            self.assertIn("--source", selector_call)
            self.assertFalse((base / "updater.log").exists())
            trust = Path(env["PLEB_CACHE_HOME"]) / "release-hop/tag-trust/v0.2.1"
            self.assertRegex(trust.read_text().strip(), r"^[0-9a-f]{40}$")

    def test_every_release_controlled_environment_override_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._release_remote(base)
            env = self._env(base, remote)
            # This optional-closure pin is deliberately outside the historic
            # four-component tuple. It must receive the same refusal as every
            # other release-controlled value or the child update can apply a
            # closure different from the one the selector just committed.
            env["KILIX_VOICE_REF"] = "4" * 40
            result = subprocess.run(
                [str(PLEB), "update", "--to", "0.2.1", "--dry-run"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(
                "KILIX_VOICE_REF is overridden by the process environment",
                result.stderr,
            )
            self.assertFalse((base / "selector.log").exists())

    def test_maintenance_hop_uses_target_selector_and_preserves_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._release_remote(base, ('0.2.2', '0.2.2.1'))
            env = self._env(base, remote)
            closure = Path(env['PLEB_CLOSURE_USER'])
            closure.write_text(closure.read_text().replace('0.2.0', '0.2.2'))
            before = closure.read_bytes()
            for arguments in (['--to', '0.2.2.1'], ['--latest']):
                result = subprocess.run([str(PLEB), 'update', *arguments, '--dry-run'],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('0.2.2.1', (base / 'selector.log').read_text())
                self.assertEqual(closure.read_bytes(), before)
                self.assertFalse((base / 'updater.log').exists())

    def test_latest_refuses_a_skip_and_names_the_adjacent_hop(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._release_remote(base)
            env = self._env(base, remote)
            result = subprocess.run(
                [str(PLEB), "update", "--latest", "--dry-run"], env=env,
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("unsupported release skip 0.2.0 -> 0.2.2", result.stderr)
            self.assertIn("pleb update --to 0.2.1", result.stderr)
            self.assertFalse((base / "selector.log").exists())

    def test_selected_release_is_applied_and_one_generation_rollback_runs(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._release_remote(base)
            env = self._env(base, remote)
            selected = subprocess.run(
                [str(PLEB), "update", "--to", "0.2.1", "--no-restart"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(selected.returncode, 0, selected.stderr)
            active = Path(env["PLEB_STATE_HOME"]) / "release-hop/active"
            self.assertTrue(active.is_file())
            self.assertEqual((base / "updater.log").read_text().splitlines(), ["--revalidate-current"])

            rolled_back = subprocess.run(
                [str(PLEB), "update", "--rollback", "--no-restart"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
            self.assertFalse(active.exists())
            calls = (base / "selector.log").read_text().splitlines()
            self.assertIn("--rollback", calls[-1])
            self.assertEqual(len((base / "updater.log").read_text().splitlines()), 2)

    def test_failed_stack_apply_restores_previous_generation(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._release_remote(base)
            env = self._env(base, remote)
            env["PLEB_TEST_UPDATER_FAIL_ONCE"] = "1"
            result = subprocess.run(
                [str(PLEB), "update", "--to", "0.2.1", "--no-restart"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("target stack apply failed with status 7", result.stderr)
            self.assertIn("previous closure and stack were restored", result.stderr)
            self.assertFalse(
                (Path(env["PLEB_STATE_HOME"]) / "release-hop/active").exists())
            self.assertEqual(len((base / "updater.log").read_text().splitlines()), 2)
            self.assertIn(
                "--rollback", (base / "selector.log").read_text().splitlines()[-1]
            )

    def test_failed_stack_rollback_reports_real_status(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._release_remote(base)
            env = self._env(base, remote)
            selected = subprocess.run(
                [str(PLEB), "update", "--to", "0.2.1", "--no-restart"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(selected.returncode, 0, selected.stderr)
            env["PLEB_TEST_UPDATER_FAIL_ONCE"] = "1"
            failed = subprocess.run(
                [str(PLEB), "update", "--rollback", "--no-restart"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("stack rollback failed with status 7", failed.stderr)
            self.assertIn("release 0.2.1 remains selected", failed.stderr)
            phase = Path(env["PLEB_STATE_HOME"]) / "release-hop/phase"
            self.assertEqual(phase.read_text(), "complete\n")

    def test_pending_selected_phase_is_recovered_before_new_work(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._release_remote(base)
            env = self._env(base, remote)
            selected = subprocess.run(
                [str(PLEB), "update", "--to", "0.2.1", "--no-restart"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(selected.returncode, 0, selected.stderr)
            state = Path(env["PLEB_STATE_HOME"]) / "release-hop"
            (state / "phase").write_text("selected\n")
            recovered = subprocess.run(
                [str(PLEB), "update", "--rollback", "--no-restart"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertIn("interrupted release hop restored", recovered.stdout)
            self.assertFalse((state / "active").exists())
            self.assertIn(
                "--rollback", (base / "selector.log").read_text().splitlines()[-1]
            )

    def test_newer_release_reporting_is_explicit_online_and_offline(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = self._release_remote(base)
            env = self._env(base, remote)
            command = textwrap.dedent(
                """\
                set -euo pipefail
                PLEB_ROOT="$1"
                . "$PLEB_ROOT/lib/common.sh"
                . "$PLEB_ROOT/lib/closure.sh"
                pleb_report_newer_release
                """
            )
            online = subprocess.run(
                ["bash", "-c", command, "bash", str(ROOT)], env=env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(online.returncode, 0, online.stderr)
            self.assertIn("newer published release 0.2.2 is available", online.stderr)
            self.assertIn("pleb update --latest", online.stderr)
            offline_env = dict(env, PLEB_RELEASE_OFFLINE="1")
            offline = subprocess.run(
                ["bash", "-c", command, "bash", str(ROOT)], env=offline_env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(offline.returncode, 0, offline.stderr)
            self.assertIn("could not check for a newer published release", offline.stderr)


if __name__ == "__main__":
    unittest.main()
