import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Keep directory-mode assertions deterministic when invoked from Kilix, whose
# runtime shell uses a private umask.
os.umask(0o022)
CATEGORIES = ("config", "state", "cache", "session", "data")


def clean_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("GPU_TERMINAL", "KILIX", "PLEB")):
            env.pop(key)
    env.update(
        {
            "HOME": str(home),
            "PLEB_ENV_SYSTEM": str(home / "missing-system.env"),
            "PLEB_ENV_USER": str(home / "missing-user.env"),
        }
    )
    return env


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class PlebStorageTests(unittest.TestCase):
    def run_common(
        self, home: Path, body: str, *, env_updates: dict[str, str] | None = None, umask: int = 0o022
    ) -> subprocess.CompletedProcess[str]:
        env = clean_env(home)
        if env_updates:
            env.update(env_updates)
        script = textwrap.dedent(
            f"""
            set -euo pipefail
            umask {umask:03o}
            PLEB_ROOT={ROOT!s}
            . "$PLEB_ROOT/lib/common.sh"
            {body}
            """
        )
        return subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )

    def assert_private_layout(self, storage: Path) -> None:
        for path in (storage, *(storage / name for name in CATEGORIES)):
            self.assertTrue(path.is_dir(), path)
            self.assertFalse(path.is_symlink(), path)
            self.assertEqual(path.stat().st_uid, os.getuid(), path)
            self.assertEqual(mode(path), 0o700, path)

    def test_fresh_layout_is_private_under_common_umasks(self):
        for requested_umask in (0o022, 0o002):
            with self.subTest(umask=oct(requested_umask)), tempfile.TemporaryDirectory() as td:
                home = Path(td)
                result = self.run_common(
                    home, "ensure_pleb_private_storage", umask=requested_umask
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_private_layout(home / ".local/gpu_terminal/pleb")

    def test_existing_modes_are_repaired_without_losing_contents(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            storage = home / ".local/gpu_terminal/pleb"
            for index, path in enumerate(
                (storage, *(storage / name for name in CATEGORIES))
            ):
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(0o755 if index % 2 else 0o777)
                (path / f"sentinel-{index}").write_text(f"preserve-{index}\n")

            result = self.run_common(home, "ensure_pleb_private_storage", umask=0o002)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_private_layout(storage)
            for index, path in enumerate(
                (storage, *(storage / name for name in CATEGORIES))
            ):
                self.assertEqual(
                    (path / f"sentinel-{index}").read_text(), f"preserve-{index}\n"
                )

    def test_out_of_tree_broad_and_symlink_layouts_are_refused_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            data_root = home / "data"
            storage = data_root / "pleb"
            outside = home / "outside"
            storage.mkdir(parents=True, mode=0o755)
            outside.mkdir(mode=0o755)
            (storage / "sentinel").write_text("storage\n")
            (outside / "sentinel").write_text("outside\n")

            escaped = self.run_common(
                home,
                "ensure_pleb_private_storage",
                env_updates={
                    "GPU_TERMINAL_HOME": str(data_root),
                    "PLEB_STORAGE_HOME": str(storage),
                    "PLEB_CACHE_HOME": str(outside),
                },
            )
            self.assertNotEqual(escaped.returncode, 0)
            self.assertIn("strict descendant", escaped.stderr)
            self.assertEqual(mode(storage), 0o755)
            self.assertEqual(mode(outside), 0o755)
            self.assertEqual((storage / "sentinel").read_text(), "storage\n")
            self.assertEqual((outside / "sentinel").read_text(), "outside\n")

            broad = self.run_common(
                home,
                "ensure_pleb_private_storage",
                env_updates={
                    "GPU_TERMINAL_HOME": str(storage),
                    "PLEB_STORAGE_HOME": str(storage),
                },
            )
            self.assertNotEqual(broad.returncode, 0)
            self.assertIn("strict descendant", broad.stderr)
            self.assertEqual(mode(storage), 0o755)

            (storage / "cache").symlink_to(outside, target_is_directory=True)
            linked = self.run_common(
                home,
                "ensure_pleb_private_storage",
                env_updates={
                    "GPU_TERMINAL_HOME": str(data_root),
                    "PLEB_STORAGE_HOME": str(storage),
                },
            )
            self.assertNotEqual(linked.returncode, 0)
            self.assertIn("symlink component", linked.stderr)
            self.assertEqual(mode(storage), 0o755)
            self.assertEqual(mode(outside), 0o755)
            self.assertEqual((outside / "sentinel").read_text(), "outside\n")

    def test_unrelated_owner_in_parent_chain_is_refused_before_creation(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            data_root = home / "data"
            data_root.mkdir(mode=0o755)
            result = self.run_common(
                home,
                textwrap.dedent(
                    f"""
                    stat() {{
                        if [ "$1" = -c ] && [ "$2" = %u ] && [ "$3" = {data_root!s} ]; then
                            printf '%s\n' 424242
                        else
                            command stat "$@"
                        fi
                    }}
                    ensure_pleb_private_storage
                    """
                ),
                env_updates={"GPU_TERMINAL_HOME": str(data_root)},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("parent has an unsafe owner", result.stderr)
            self.assertFalse((data_root / "pleb").exists())

    def test_update_reconciles_every_category_before_locking(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            storage = home / ".local/gpu_terminal/pleb"
            for path in (storage, *(storage / name for name in CATEGORIES)):
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(0o755)
            sentinel = storage / "data/keep"
            sentinel.write_text("kept\n")

            result = self.run_common(
                home,
                '. "$PLEB_ROOT/lib/update.sh"; _acquire_update_lock; '
                '_release_update_lock; trap - EXIT INT TERM',
                umask=0o022,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_private_layout(storage)
            self.assertEqual(sentinel.read_text(), "kept\n")
            self.assertTrue((storage / "state/update.lock").is_file())

    def test_managed_install_reconciles_before_dependency_or_artwork_work(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            storage = home / ".local/gpu_terminal/pleb"
            for path in (storage, *(storage / name for name in CATEGORIES)):
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(0o755)
            sentinel = storage / "data/keep"
            sentinel.write_text("kept\n")

            result = self.run_common(
                home,
                '. "$PLEB_ROOT/lib/install.sh"; '
                'ensure_system_deps() { return 73; }; do_install',
                env_updates={"PLEBIAN_OS_MANAGED_INSTALL": "1"},
                umask=0o022,
            )
            self.assertEqual(result.returncode, 73, result.stderr)
            self.assert_private_layout(storage)
            self.assertEqual(sentinel.read_text(), "kept\n")

    def test_nested_category_intermediates_are_validated_not_rewritten(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            storage = home / ".local/gpu_terminal/pleb"
            intermediate = storage / "custom"
            intermediate.mkdir(parents=True)
            storage.chmod(0o755)
            intermediate.chmod(0o755)
            cache = intermediate / "cache"
            result = self.run_common(
                home,
                "ensure_pleb_private_storage",
                env_updates={"PLEB_CACHE_HOME": str(cache)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(mode(storage), 0o700)
            self.assertEqual(mode(intermediate), 0o755)
            self.assertEqual(mode(cache), 0o700)

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            storage = home / ".local/gpu_terminal/pleb"
            intermediate = storage / "custom"
            intermediate.mkdir(parents=True)
            storage.chmod(0o755)
            intermediate.chmod(0o777)
            rejected = self.run_common(
                home,
                "ensure_pleb_private_storage",
                env_updates={"PLEB_CACHE_HOME": str(intermediate / "cache")},
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("group/world-writable parent", rejected.stderr)
            self.assertEqual(mode(storage), 0o755)
            self.assertEqual(mode(intermediate), 0o777)

    def test_self_contained_session_creates_nested_categories_privately(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            storage = home / ".local/gpu_terminal/pleb"
            cache = storage / "custom/nested/cache"
            engine = home / "kilix"
            engine.write_text("#!/bin/sh\nexit 0\n")
            engine.chmod(0o755)
            env = clean_env(home)
            env.update(
                {
                    "KILIX": str(engine),
                    "PLEB_CACHE_HOME": str(cache),
                    "PLEB_NO_FILL": "1",
                }
            )
            result = subprocess.run(
                [str(ROOT / "bin/pleb-session")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                preexec_fn=lambda: os.umask(0o022),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for path in (storage / "custom", storage / "custom/nested", cache):
                self.assertEqual(mode(path), 0o700, path)

    def test_self_contained_session_repairs_layout_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            storage = home / ".local/gpu_terminal/pleb"
            for path in (storage, *(storage / name for name in CATEGORIES)):
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(0o755)
            sentinel = storage / "cache/keep"
            sentinel.write_text("kept\n")
            engine = home / "kilix"
            engine.write_text("#!/bin/sh\nexit 0\n")
            engine.chmod(0o755)
            env = clean_env(home)
            env.update({"KILIX": str(engine), "PLEB_NO_FILL": "1"})
            result = subprocess.run(
                [str(ROOT / "bin/pleb-session")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                preexec_fn=lambda: os.umask(0o022),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_private_layout(storage)
            self.assertEqual(sentinel.read_text(), "kept\n")
            self.assertEqual(mode(storage / "state/session.log"), 0o600)

            outside = home / "outside"
            outside.mkdir(mode=0o755)
            marker = home / "engine-ran"
            engine.write_text(f"#!/bin/sh\ntouch {marker!s}\n")
            storage.chmod(0o755)
            env["PLEB_CACHE_HOME"] = str(outside)
            rejected = subprocess.run(
                [str(ROOT / "bin/pleb-session")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("strict descendant", rejected.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual(mode(storage), 0o755)
            self.assertEqual(mode(outside), 0o755)


if __name__ == "__main__":
    unittest.main()
