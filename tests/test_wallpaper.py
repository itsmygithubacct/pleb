import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/validate-artwork.py"
WALLPAPER = ROOT / "assets/desktop/plebian-os.png"
DESKTOP_README = ROOT / "assets/desktop/README.md"
ATTRIBUTION = ROOT / "assets/installer/ATTRIBUTION.md"
GPL2 = ROOT / "assets/COPYING.GPL-2"


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
            "PLEB_ROOT": str(ROOT),
        }
    )
    return env


def load_validator_module():
    spec = importlib.util.spec_from_file_location("pleb_artwork_validator", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlebWallpaperTests(unittest.TestCase):
    def run_helpers(self, body: str, env: dict[str, str]):
        script = f"""
set -euo pipefail
. "$PLEB_ROOT/lib/common.sh"
. "$PLEB_ROOT/lib/install.sh"
{body}
"""
        return subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )

    def artwork_env(self, tmp: Path, provider: str = "builtin"):
        env = clean_env(tmp)
        source = tmp / "source"
        kilix = source / "kilix"
        kilix95 = source / "kilix-95"
        (kilix / "desktop").mkdir(parents=True)
        (kilix / "desktop/main.py").touch()
        env.update(
            {
                "GPU_TERMINAL_HOME": str(tmp / "data"),
                "PLEB_STORAGE_HOME": str(tmp / "data/pleb"),
                "PLEB_DATA_HOME": str(tmp / "data/pleb/data"),
                "KILIX_STORAGE_HOME": str(tmp / "data/kilix"),
                "KILIX_DATA_HOME": str(tmp / "data/kilix/data"),
                "KILIX95_STORAGE_HOME": str(tmp / "data/kilix-95"),
                "KILIX95_DATA_HOME": str(tmp / "data/kilix-95/data"),
                "KILIX_DIR": str(kilix),
                "KILIX95_DIR": str(kilix95),
                "KILIX_DESKTOP_PROVIDER": provider,
                "PLEB_DESKTOP": "1",
            }
        )
        return env

    def test_tracked_bundle_has_the_approved_exact_hashes_and_contract(self):
        expected = {
            WALLPAPER: "60f63c37f054f7ffd061b47e09a3c22fbf595eec6f161c13e95344ca1a724778",
            DESKTOP_README: "650b64787bb1ad6073bad24dd51faec08e7ef0a17bfdbffe121076f0c8c71c10",
            ATTRIBUTION: "5216b6ee1ef154dab56cc5d0a026d28f67ed50feec4129d4fedd6ae2fc2b2fb6",
            GPL2: "8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643",
        }
        for path, digest in expected.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        result = subprocess.run(
            ["python3", str(VALIDATOR), *(str(path) for path in expected)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bundle valid", result.stdout)

    def test_validator_rejects_substitution_symlinks_and_malformed_png(self):
        validator = load_validator_module()
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            substituted = tmp / "wallpaper.png"
            substituted.write_bytes(WALLPAPER.read_bytes() + b"x")
            result = subprocess.run(
                [
                    "python3", str(VALIDATOR), str(substituted),
                    str(DESKTOP_README), str(ATTRIBUTION), str(GPL2),
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SHA-256 mismatch", result.stderr)

            link = tmp / "linked.png"
            link.symlink_to(WALLPAPER)
            linked = subprocess.run(
                [
                    "python3", str(VALIDATOR), str(link),
                    str(DESKTOP_README), str(ATTRIBUTION), str(GPL2),
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(linked.returncode, 0)
            self.assertIn("non-symlink", linked.stderr)

            changed_license = tmp / "COPYING.GPL-2"
            changed_license.write_bytes(GPL2.read_bytes().replace(b"GNU", b"Gnu", 1))
            license_result = subprocess.run(
                [
                    "python3", str(VALIDATOR), str(WALLPAPER),
                    str(DESKTOP_README), str(ATTRIBUTION), str(changed_license),
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(license_result.returncode, 0)
            self.assertIn("GPL version 2 license SHA-256 mismatch", license_result.stderr)

            malformed = bytearray(WALLPAPER.read_bytes())
            malformed[-1] ^= 0x01
            with self.assertRaises(validator.ValidationError):
                validator.validate_png(bytes(malformed))

            fifo = tmp / "wallpaper.fifo"
            os.mkfifo(fifo)
            fifo_result = subprocess.run(
                [
                    "python3", str(VALIDATOR), str(fifo),
                    str(DESKTOP_README), str(ATTRIBUTION), str(GPL2),
                ],
                text=True,
                capture_output=True,
                timeout=2,
            )
            self.assertNotEqual(fifo_result.returncode, 0)
            self.assertIn("regular non-symlink", fifo_result.stderr)

            raced = tmp / "raced.png"
            raced.write_bytes(WALLPAPER.read_bytes())
            original_open = validator.os.open

            def swap_after_open(path, flags):
                descriptor = original_open(path, flags)
                Path(path).unlink()
                Path(path).symlink_to(fifo)
                return descriptor

            with mock.patch.object(validator.os, "open", side_effect=swap_after_open):
                raced_data = validator.regular_bytes(
                    str(raced), validator.MAX_WALLPAPER_BYTES, "Plebian wallpaper"
                )
            self.assertEqual(raced_data, WALLPAPER.read_bytes())

    def test_install_copies_validated_bundle_under_private_pleb_data(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            env = self.artwork_env(tmp)
            result = self.run_helpers("install_pleb_artwork_bundle", env)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = Path(env["PLEB_DATA_HOME"])
            installed = (
                data / "wallpapers/plebian-os.png",
                data / "doc/desktop/README.md",
                data / "doc/installer/ATTRIBUTION.md",
                data / "doc/COPYING.GPL-2",
            )
            for source, destination in zip(
                (WALLPAPER, DESKTOP_README, ATTRIBUTION, GPL2), installed
            ):
                self.assertEqual(destination.read_bytes(), source.read_bytes())
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o644)
            for directory in (
                Path(env["PLEB_STORAGE_HOME"]), data, data / "wallpapers",
                data / "doc", data / "doc/desktop", data / "doc/installer",
            ):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

            installed[0].write_bytes(b"substituted")
            repaired = self.run_helpers("install_pleb_artwork_bundle", env)
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            self.assertEqual(installed[0].read_bytes(), WALLPAPER.read_bytes())

    def test_install_refuses_a_symlink_artwork_destination(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            env = self.artwork_env(tmp)
            wallpaper_dir = Path(env["PLEB_DATA_HOME"]) / "wallpapers"
            initial = self.run_helpers("install_pleb_artwork_bundle", env)
            self.assertEqual(initial.returncode, 0, initial.stderr)
            target = tmp / "must-not-change"
            target.write_text("sentinel\n")
            destination = wallpaper_dir / "plebian-os.png"
            destination.unlink()
            destination.symlink_to(target)
            result = self.run_helpers("install_pleb_artwork_bundle", env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe Pleb artwork destination", result.stderr)
            self.assertEqual(target.read_text(), "sentinel\n")

    def test_publish_failure_restores_the_entire_previous_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            env = self.artwork_env(tmp)
            first = self.run_helpers("install_pleb_artwork_bundle", env)
            self.assertEqual(first.returncode, 0, first.stderr)
            data = Path(env["PLEB_DATA_HOME"])
            installed = (
                data / "wallpapers/plebian-os.png",
                data / "doc/desktop/README.md",
                data / "doc/installer/ATTRIBUTION.md",
                data / "doc/COPYING.GPL-2",
            )
            previous = []
            for index, path in enumerate(installed):
                content = f"previous-{index}\n".encode()
                path.write_bytes(content)
                previous.append(content)
            result = self.run_helpers(
                """
mv_calls=0
mv() {
    mv_calls=$((mv_calls + 1))
    [ "$mv_calls" != 2 ] || return 1
    command mv "$@"
}
install_pleb_artwork_bundle
""",
                env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("could not publish Pleb artwork", result.stderr)
            self.assertEqual([path.read_bytes() for path in installed], previous)

    def test_unsafe_or_broad_roots_and_symlink_parents_are_refused_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            env = self.artwork_env(tmp)
            broad = tmp / "broad"
            broad.mkdir(mode=0o755)
            sentinel = broad / "sentinel"
            sentinel.write_text("unchanged\n")
            env.update(
                {
                    "GPU_TERMINAL_HOME": str(broad),
                    "PLEB_STORAGE_HOME": str(broad),
                    "PLEB_DATA_HOME": str(broad / "data"),
                }
            )
            refused = self.run_helpers("install_pleb_artwork_bundle", env)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("strict descendant", refused.stderr)
            self.assertEqual(stat.S_IMODE(broad.stat().st_mode), 0o755)
            self.assertEqual(sentinel.read_text(), "unchanged\n")

            outside = tmp / "outside"
            outside.mkdir(mode=0o700)
            target_sentinel = outside / "sentinel"
            target_sentinel.write_text("unchanged\n")
            data_root = tmp / "safe-data"
            data_root.mkdir(mode=0o700)
            (data_root / "pleb").symlink_to(outside, target_is_directory=True)
            env.update(
                {
                    "GPU_TERMINAL_HOME": str(data_root),
                    "PLEB_STORAGE_HOME": str(data_root / "pleb"),
                    "PLEB_DATA_HOME": str(data_root / "pleb/data"),
                }
            )
            linked = self.run_helpers("install_pleb_artwork_bundle", env)
            self.assertNotEqual(linked.returncode, 0)
            self.assertIn("symlink component", linked.stderr)
            self.assertEqual(target_sentinel.read_text(), "unchanged\n")
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o700)

            env.update(
                {
                    "GPU_TERMINAL_HOME": "/",
                    "PLEB_STORAGE_HOME": "/tmp/pleb-must-not-create",
                    "PLEB_DATA_HOME": "/tmp/pleb-must-not-create/data",
                }
            )
            root = self.run_helpers("install_pleb_artwork_bundle", env)
            self.assertNotEqual(root.returncode, 0)
            self.assertIn("too broad", root.stderr)

    def test_builtin_provider_gets_isolated_state_and_existing_state_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            env = self.artwork_env(tmp, "builtin")
            result = self.run_helpers("install_standalone_pleb_wallpaper", env)
            self.assertEqual(result.returncode, 0, result.stderr)
            state = Path(env["PLEB_DATA_HOME"]) / "desktop/.state.json"
            wallpaper = Path(env["PLEB_DATA_HOME"]) / "wallpapers/plebian-os.png"
            self.assertEqual(
                json.loads(state.read_text()),
                {
                    "wall_image": str(wallpaper),
                    "wall_mode": "stretch",
                    "wall_custom": True,
                },
            )
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
            self.assertFalse(
                (Path(env["KILIX_DATA_HOME"]) / "desktop/.state.json").exists()
            )
            self.assertFalse(
                (Path(env["KILIX95_DATA_HOME"]) / "desktop/.state.json").exists()
            )

            original = b'{"wall_image":"/home/me/custom.png","wall_mode":"tile"}\n'
            state.write_bytes(original)
            state.chmod(0o640)
            rerun = self.run_helpers("install_standalone_pleb_wallpaper", env)
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            self.assertEqual(state.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o640)

            target = tmp / "chosen.json"
            target.write_bytes(original)
            state.unlink()
            state.symlink_to(target)
            symlink_rerun = self.run_helpers("install_standalone_pleb_wallpaper", env)
            self.assertEqual(symlink_rerun.returncode, 0, symlink_rerun.stderr)
            self.assertEqual(target.read_bytes(), original)
            self.assertTrue(state.is_symlink())

    def test_external_and_auto_provider_seed_only_pleb_owned_state(self):
        for provider in ("external", "auto"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                env = self.artwork_env(tmp, provider)
                kilix95 = Path(env["KILIX95_DIR"])
                kilix95.mkdir(parents=True)
                (kilix95 / "main.py").touch()
                result = self.run_helpers("install_standalone_pleb_wallpaper", env)
                self.assertEqual(result.returncode, 0, result.stderr)
                state = Path(env["PLEB_DATA_HOME"]) / "desktop/.state.json"
                self.assertTrue(state.is_file())
                self.assertFalse(
                    (Path(env["KILIX_DATA_HOME"]) / "desktop/.state.json").exists()
                )
                self.assertFalse(
                    (Path(env["KILIX95_DATA_HOME"]) / "desktop/.state.json").exists()
                )

    @unittest.skipUnless(
        (ROOT.parent / "kilix-95/main.py").is_file(),
        "sibling Kilix-95 checkout is unavailable",
    )
    def test_direct_xp_provider_keeps_its_kittens_wallpaper_after_pleb_install(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            env = self.artwork_env(tmp, "external")
            kilix95 = Path(env["KILIX95_DIR"])
            kilix95.mkdir(parents=True)
            (kilix95 / "main.py").touch()
            installed = self.run_helpers("install_standalone_pleb_wallpaper", env)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            provider_state = Path(env["KILIX95_DATA_HOME"]) / "desktop/.state.json"
            self.assertFalse(provider_state.exists())

            direct_env = env.copy()
            direct_env.pop("KILIX_DESKTOP_DIR", None)
            direct_env["KILIX_DESKTOP_FLAVOR"] = "xp"
            actual_kilix95 = ROOT.parent / "kilix-95"
            direct = subprocess.run(
                [
                    "python3",
                    "-c",
                    (
                        "import json,sys; "
                        f"sys.path.insert(0, {str(actual_kilix95)!r}); "
                        "import main; d=main.Desk(term=None,size=(800,600)); "
                        "print(json.dumps(d.shell.state))"
                    ),
                ],
                env=direct_env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(direct.returncode, 0, direct.stderr)
            state = json.loads(direct.stdout)
            self.assertFalse(state["wall_custom"])
            self.assertTrue(
                state["wall_image"].endswith(
                    "assets/wallpapers/kilix-xp-kittens-fire.png"
                )
            )

    def test_managed_install_skips_bundle_and_state_for_plebian_os(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            env = self.artwork_env(tmp)
            env["PLEBIAN_OS_MANAGED_INSTALL"] = "1"
            result = self.run_helpers("install_standalone_pleb_wallpaper", env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("manages the system wallpaper", result.stdout)
            storage = Path(env["PLEB_STORAGE_HOME"])
            for category in ("config", "state", "cache", "session", "data"):
                path = storage / category
                self.assertTrue(path.is_dir())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            self.assertEqual(list(Path(env["PLEB_DATA_HOME"]).iterdir()), [])
            self.assertFalse(Path(env["KILIX_DATA_HOME"]).exists())

    def test_desktop_mode_off_still_seeds_the_selected_provider(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            env = self.artwork_env(tmp, "builtin")
            env["PLEB_DESKTOP"] = "0"
            result = self.run_helpers("install_standalone_pleb_wallpaper", env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                (Path(env["PLEB_DATA_HOME"]) / "desktop/.state.json").is_file()
            )

    def test_custom_or_disabled_provider_installs_artwork_without_state(self):
        for provider in ("command", "none"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                env = self.artwork_env(tmp, provider)
                if provider == "command":
                    env["KILIX_DESKTOP_COMMAND"] = "true"
                result = self.run_helpers("install_standalone_pleb_wallpaper", env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(
                    (Path(env["PLEB_DATA_HOME"]) / "wallpapers/plebian-os.png").is_file()
                )
                self.assertFalse(
                    (Path(env["KILIX_DATA_HOME"]) / "desktop/.state.json").exists()
                )
                self.assertFalse(
                    (Path(env["KILIX95_DATA_HOME"]) / "desktop/.state.json").exists()
                )


if __name__ == "__main__":
    unittest.main()
