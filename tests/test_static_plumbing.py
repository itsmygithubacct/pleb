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
        ]
        subprocess.run(["bash", "-n", *scripts], cwd=ROOT, check=True)

    def test_session_uses_arrays_for_desktop_forwarding(self):
        text = (ROOT / "bin" / "pleb-session").read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env bash"))
        self.assertIn("DESKTOP_ARGS=(", text)
        self.assertIn("KILIX_DESKTOP_COMMAND=$KILIX_DESKTOP_COMMAND", text)
        self.assertIn("KILIX_DESKTOP_NAME=$KILIX_DESKTOP_NAME", text)
        self.assertIn("KILIX_REF=$KILIX_REF", text)
        self.assertNotIn("DESKTOP_CMD=", text)
        self.assertIn("none|off|disabled) return 1", text)

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
        self.assertIn("scripts/install-go.sh", text)
        self.assertIn("ensure_system_deps", text)
        self.assertIn(".kilix-fork-built-ref", text)
        self.assertIn('"$KILIX_DIR/kilix" --build || die "kilix fork build failed"', text)
        self.assertNotIn("fork build failed — keeping the previous engine binary", text)

    def test_install_includes_kilix_fork_build_deps(self):
        text = (ROOT / "lib" / "install.sh").read_text()
        self.assertIn("dpkg-query", text)
        self.assertIn('if [ "${#missing[@]}" -eq 0 ]; then', text)
        self.assertIn('"${missing[@]}"', text)
        for pkg in (
            "libxkbcommon-x11-dev",
            "libxkbcommon-dev",
            "libx11-dev",
            "libxrandr-dev",
            "libxinerama-dev",
            "libxcursor-dev",
            "libxi-dev",
            "libx11-xcb-dev",
            "libdbus-1-dev",
            "libgl1-mesa-dev",
            "libfontconfig-dev",
            "python3-dev",
        ):
            self.assertIn(pkg, text)

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
