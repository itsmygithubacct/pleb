import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PlebPlumbingTests(unittest.TestCase):
    def test_shell_scripts_parse(self):
        scripts = [
            "bin/pleb",
            "bin/pleb-session",
            *(str(p.relative_to(ROOT)) for p in sorted((ROOT / "lib").glob("*.sh"))),
            *(str(p.relative_to(ROOT)) for p in sorted((ROOT / "scripts").glob("*.sh"))),
        ]
        subprocess.run(["bash", "-n", *scripts], cwd=ROOT, check=True)

    def test_session_uses_arrays_for_desktop_forwarding(self):
        text = (ROOT / "bin" / "pleb-session").read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env bash"))
        self.assertIn("DESKTOP_ARGS=(", text)
        self.assertIn("KILIX_DESKTOP_COMMAND=$KILIX_DESKTOP_COMMAND", text)
        self.assertIn("KILIX_DESKTOP_NAME=$KILIX_DESKTOP_NAME", text)
        self.assertIn("KILIX_DESKTOP_FLAVOR=$KILIX_DESKTOP_FLAVOR", text)
        self.assertIn("KILIX_REF=$KILIX_REF", text)
        self.assertIn("KILIX_ALLOW_MUTABLE_REF=$KILIX_ALLOW_MUTABLE_REF", text)
        self.assertIn("KILIX95_REF=$KILIX95_REF", text)
        self.assertIn("KILIX95_ALLOW_MUTABLE_REF=$KILIX95_ALLOW_MUTABLE_REF", text)
        self.assertIn("KILIX95_ALLOW_UNPINNED_INSTALL=$KILIX95_ALLOW_UNPINNED_INSTALL", text)
        self.assertIn("GPU_TERMINAL_SOURCE_HOME=$GPU_TERMINAL_SOURCE_HOME", text)
        self.assertIn("PLEB_DATA_HOME=$PLEB_DATA_HOME", text)
        self.assertIn("KILIX_DATA_HOME=$KILIX_DATA_HOME", text)
        self.assertIn("KILIX_BUILD_DIRECTORY=$KILIX_BUILD_DIRECTORY", text)
        self.assertIn("KILIX95_DATA_HOME=$KILIX95_DATA_HOME", text)
        self.assertIn("KILIX_DESKTOP_DIR=$KILIX_DESKTOP_DIR", text)
        self.assertNotIn("DESKTOP_CMD=", text)
        self.assertIn("none|off|disabled) return 1", text)

    def test_source_and_storage_roots_are_separate_and_session_logs_are_private(self):
        common = (ROOT / "lib" / "common.sh").read_text()
        session = (ROOT / "bin" / "pleb-session").read_text()
        readme = (ROOT / "README.md").read_text()
        for text in (common, session):
            self.assertIn('${GPU_TERMINAL_SOURCE_HOME:-$HOME/gpu_terminal}', text)
            self.assertIn('${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}', text)
            self.assertIn('$GPU_TERMINAL_SOURCE_HOME/kilix', text)
            self.assertIn('$GPU_TERMINAL_SOURCE_HOME/kilix-95', text)
        self.assertIn('chmod 0600 -- "$PLEB_LOG"', session)
        self.assertIn('mv -- "$PLEB_LOG" "$PLEB_LOG.1"', session)
        self.assertIn("`GPU_TERMINAL_SOURCE_HOME`", readme)

    def test_kilix95_requirement_follows_provider(self):
        checks = [
            ("external", "kilix95_required"),
            ("command", "! kilix95_required"),
            ("none", "! kilix95_required"),
            ("builtin", "! kilix95_required"),
        ]
        for provider, predicate in checks:
            with self.subTest(provider=provider):
                cmd = (
                    ". lib/common.sh; "
                    "PLEB_DESKTOP=1; "
                    f"KILIX_DESKTOP_PROVIDER={provider}; "
                    f"{predicate}"
                )
                subprocess.run(["bash", "-c", cmd], cwd=ROOT, check=True)

    def test_provider_none_disables_desktop_even_when_desktop_requested(self):
        cmd = (
            ". lib/common.sh; "
            "PLEB_DESKTOP=1; "
            "KILIX_DESKTOP_PROVIDER=none; "
            "! desktop_enabled"
        )
        subprocess.run(["bash", "-c", cmd], cwd=ROOT, check=True)

    def test_install_clone_branches_use_arrays(self):
        text = (ROOT / "lib" / "install.sh").read_text()
        self.assertIn("local -a clone_args=()", text)
        self.assertIn('git clone "${clone_args[@]}" "$KILIX_REPO"', text)
        self.assertIn('git clone "${clone_args[@]}" "$KILIX95_REPO"', text)
        self.assertNotIn("${KILIX_BRANCH:+", text)
        self.assertNotIn("${KILIX95_BRANCH:+", text)

    def test_update_explicit_branches_are_checked_out_before_merge(self):
        text = (ROOT / "lib" / "update.sh").read_text()
        self.assertIn('checkout "$KILIX_BRANCH"', text)
        self.assertIn('checkout --track -b "$KILIX_BRANCH"', text)
        self.assertIn('checkout "$KILIX95_BRANCH"', text)
        self.assertIn('checkout --track -b "$KILIX95_BRANCH"', text)

    def test_update_rebuilds_or_fails_stale_fork(self):
        text = (ROOT / "lib" / "update.sh").read_text()
        self.assertIn("PLEBIAN_OS_BUILD_KILIX_FORK", text)
        self.assertIn("PLEBIAN_OS_KILIX_GO_MIN_VERSION", text)
        self.assertIn("PLEBIAN_OS_KILIX_GO_VERSION", text)
        self.assertIn("PLEBIAN_OS_KILIX_GO_SHA256_AMD64", text)
        self.assertIn("PLEBIAN_OS_KILIX_GO_SHA256_ARM64", text)
        self.assertIn("scripts/install-go.sh", text)
        self.assertIn("ensure_kilix_build_deps", text)
        self.assertIn("$PLEB_STATE_HOME/kilix-fork-built-ref", text)
        self.assertNotIn("$KILIX_DIR/.kilix-fork-built-ref", text)
        self.assertIn('"$KILIX_DIR/kilix" --build || die "kilix fork build failed"', text)
        self.assertNotIn("fork build failed — keeping the previous engine binary", text)

    def test_update_restart_uses_transient_systemd_unit(self):
        text = (ROOT / "lib" / "update.sh").read_text()
        self.assertIn("systemd-run", text)
        self.assertIn("pleb-restart-lightdm-$$", text)
        self.assertIn("systemctl stop \"$svc\" --no-block", text)
        self.assertIn("systemctl kill -s KILL \"$svc\"", text)
        self.assertIn("systemctl start \"$svc\"", text)

    def test_update_delegates_the_complete_build_manifest_to_kilix(self):
        text = (ROOT / "lib" / "install.sh").read_text()
        update = (ROOT / "lib" / "update.sh").read_text()
        self.assertIn('$KILIX_DIR/scripts/install-build-deps.sh', text)
        self.assertGreaterEqual(text.count('"$installer" --verify'), 2)
        self.assertIn('${PLEB_SKIP_DEPS:-0}', text)
        self.assertIn("libxxhash", text)
        for stale_pkg in ("libxxhash-dev", "libx11-dev", "python3-dev"):
            self.assertNotIn(stale_pkg, text)
        verify = update.index("ensure_kilix_build_deps")
        build = update.index('"$KILIX_DIR/kilix" --build', verify)
        self.assertLess(verify, build)

    def test_standalone_install_owns_validated_wallpaper_but_managed_os_skips_it(self):
        install = (ROOT / "lib" / "install.sh").read_text()
        validator = (ROOT / "scripts" / "validate-artwork.py").read_text()
        self.assertIn("install_standalone_pleb_wallpaper", install)
        self.assertIn('${PLEBIAN_OS_MANAGED_INSTALL:-0}', install)
        self.assertIn("$KILIX_DESKTOP_DIR", install)
        self.assertNotIn('$KILIX_DATA_HOME/desktop', install)
        self.assertNotIn('$KILIX95_DATA_HOME/desktop', install)
        self.assertIn("os.link(temporary, state_path, follow_symlinks=False)", install)
        self.assertIn("WALLPAPER_SHA256", validator)
        self.assertIn("validate_png", validator)

    def test_recovery_doc_has_stable_install_and_help_contract(self):
        common = (ROOT / "lib" / "common.sh").read_text()
        install = (ROOT / "lib" / "install.sh").read_text()
        recovery = (ROOT / "docs" / "RECOVERY.md").read_text()
        changelog = (ROOT / "CHANGELOG.md").read_text()
        stable = "/usr/local/share/doc/pleb/RECOVERY.md"
        self.assertIn(f"PLEB_RECOVERY_DOC_DST:-{stable}", common)
        self.assertGreaterEqual(install.count("install_recovery_document"), 2)
        self.assertIn("install -D -m 0644", install)
        self.assertIn('"$PLEB_RECOVERY_DOC_DST"', install)
        self.assertIn(
            '"$XSESSION_DST" "$SESSION_BIN_DST" "$PLEB_RECOVERY_DOC_DST"',
            install,
        )
        self.assertIn("sudo /usr/local/sbin/plebian-os-install-deps", recovery)
        self.assertIn("sudo apt-get update", recovery)
        self.assertIn("sudo apt-get install libxxhash-dev", recovery)
        self.assertIn("complete, release-matched Kilix build", recovery)
        self.assertIn(stable, changelog)

    def test_screen_size_passthrough_and_new_env_knobs_are_documented(self):
        cli = (ROOT / "bin" / "pleb").read_text()
        common = (ROOT / "lib" / "common.sh").read_text()
        readme = (ROOT / "README.md").read_text()
        self.assertIn("screen-size|font-size)", cli)
        self.assertIn('"$KILIX_DEFAULT" screen-size "$@"', cli)
        self.assertIn("KILIX_PREBUILT_VERSION", common)
        self.assertIn("KILIX_PREBUILT_SHA256", common)
        self.assertIn("PLEB_SKIP_DEPS", readme)
        self.assertIn("pleb screen-size larger", readme)

    def test_session_disables_x_keyboard_bell(self):
        text = (ROOT / "bin" / "pleb-session").read_text()
        self.assertIn("xset b off", text)
        self.assertIn("xset -b", text)

    def test_session_only_uses_native_fullscreen_with_wm_by_default(self):
        text = (ROOT / "bin" / "pleb-session").read_text()
        self.assertIn("_PLEB_KILIX_ARGS_DEFAULT", text)
        self.assertIn("HAVE_WM=0", text)
        self.assertIn('[ "$HAVE_WM" = 1 ] && KILIX_ARGV=(--start-as=fullscreen)', text)
        self.assertNotIn('PLEB_KILIX_ARGS="${PLEB_KILIX_ARGS:---start-as=fullscreen}"', text)


if __name__ == "__main__":
    unittest.main()
