#!/usr/bin/env python3
"""F109 P1 checkout-preservation contract tests."""

import hashlib
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PRESERVE = ROOT / "lib" / "preserve.sh"


def git(repo, *args, text=True):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=text
    ).strip() if text else subprocess.check_output(
        ["git", "-C", os.fsencode(repo), *(os.fsencode(arg) for arg in args)]
    ).rstrip(b"\n")


class PreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.checkout = self.base / "sources" / "kilix"
        self.state = self.base / "data" / "pleb" / "state"
        self.checkout.mkdir(parents=True)
        self.state.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.checkout)], check=True)
        subprocess.run(
            ["git", "-C", str(self.checkout), "config", "user.name", "Test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.checkout), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        (self.checkout / "tracked.txt").write_bytes(b"release base\n")
        subprocess.run(["git", "-C", str(self.checkout), "add", "tracked.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(self.checkout), "commit", "-qm", "base"], check=True
        )

    def tearDown(self):
        self.temp.cleanup()

    def shell(self, body, *, check=True):
        script = f"""
set -euo pipefail
log() {{ printf '[test] %s\\n' "$*"; }}
warn() {{ printf '[test] %s\\n' "$*" >&2; }}
die() {{ printf '[test] %s\\n' "$*" >&2; exit 1; }}
_pleb_private_data_dir() {{ mkdir -p -- "$1"; chmod 0700 -- "$1"; }}
PLEB_STATE_HOME={shlex.quote(str(self.state))}
PLEB_ROOT={shlex.quote(str(self.base / 'sources' / 'pleb'))}
PLEB_DIR="$PLEB_ROOT"
KILIX_DIR={shlex.quote(str(self.checkout))}
KILIX95_DIR={shlex.quote(str(self.base / 'sources' / 'kilix-95'))}
GPU_TERMINAL_SOURCE_HOME={shlex.quote(str(self.base / 'sources'))}
. {shlex.quote(str(PRESERVE))}
{body}
"""
        result = subprocess.run(
            ["bash", "-c", script], text=True, capture_output=True, check=False
        )
        if check and result.returncode:
            self.fail(
                f"preservation shell failed ({result.returncode})\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def snapshots(self):
        root = self.state / "update-preserve"
        if not root.exists():
            return []
        return sorted(path for path in root.iterdir() if not path.name.startswith("."))

    def test_success_restore_is_asymmetric_and_checksum_verified(self):
        tracked = self.checkout / "tracked.txt"
        tracked.write_bytes(b"operator tracked edit\n")
        tracked.chmod(0o750)
        os.utime(tracked, ns=(1_700_000_000_123_456_789,) * 2)
        untracked = self.checkout / "camera script\nlocal.sh"
        untracked.write_bytes(b"operator camera\n")
        untracked.chmod(0o700)
        os.utime(untracked, ns=(1_700_000_001_123_456_789,) * 2)
        before = {
            "tracked": tracked.read_bytes(),
            "tracked_mode": stat.S_IMODE(tracked.stat().st_mode),
            "tracked_mtime": tracked.stat().st_mtime_ns,
            "untracked": untracked.read_bytes(),
            "untracked_mode": stat.S_IMODE(untracked.stat().st_mode),
            "untracked_mtime": untracked.stat().st_mtime_ns,
        }
        target = git(self.checkout, "rev-parse", "HEAD")
        self.shell(
            f"""
preserve_checkout "$KILIX_DIR" kilix forward
snapshot="$PLEB_PRESERVE_RESULT"
[ -n "$snapshot" ]
prepare_preserved_checkout "$KILIX_DIR" "$snapshot"
printf '%s' 'incoming release' > {shlex.quote(str(untracked))}
restore_preserved_checkout "$KILIX_DIR" "$snapshot" success {shlex.quote(target)}
"""
        )
        snapshot = self.snapshots()[0]
        subprocess.run(
            ["sha256sum", "-c", "MANIFEST.sha256"], cwd=snapshot, check=True,
            stdout=subprocess.DEVNULL,
        )
        self.assertEqual(untracked.read_bytes(), before["untracked"])
        self.assertEqual(stat.S_IMODE(untracked.stat().st_mode), before["untracked_mode"])
        self.assertEqual(untracked.stat().st_mtime_ns, before["untracked_mtime"])
        incoming = Path(str(untracked) + f".from-{target[:12]}")
        self.assertEqual(incoming.read_bytes(), b"incoming release")
        self.assertEqual(tracked.read_bytes(), b"release base\n")
        local = Path(str(tracked) + ".local")
        self.assertEqual(local.read_bytes(), before["tracked"])
        self.assertEqual(stat.S_IMODE(local.stat().st_mode), before["tracked_mode"])
        self.assertEqual(local.stat().st_mtime_ns, before["tracked_mtime"])
        status = (snapshot / "STATUS").read_bytes()
        self.assertIn(b"checkout: ", status)
        self.assertIn(b"porcelain-v1:\n", status)

    def test_existing_local_sidecar_name_is_never_overwritten(self):
        tracked = self.checkout / "tracked.txt"
        tracked.write_bytes(b"operator tracked edit\n")
        existing_sidecar = self.checkout / "tracked.txt.local"
        existing_sidecar.write_bytes(b"operator pre-existing sidecar\n")
        target = git(self.checkout, "rev-parse", "HEAD")
        self.shell(
            f"""
preserve_checkout "$KILIX_DIR" kilix forward
snapshot="$PLEB_PRESERVE_RESULT"
prepare_preserved_checkout "$KILIX_DIR" "$snapshot"
restore_preserved_checkout "$KILIX_DIR" "$snapshot" success {shlex.quote(target)}
"""
        )
        self.assertEqual(
            existing_sidecar.read_bytes(), b"operator pre-existing sidecar\n"
        )
        self.assertEqual(
            (self.checkout / "tracked.txt.local.1").read_bytes(),
            b"operator tracked edit\n",
        )

    def test_rollback_reproduces_worktree_and_staged_index(self):
        tracked = self.checkout / "tracked.txt"
        tracked.write_bytes(b"staged version\n")
        subprocess.run(["git", "-C", str(self.checkout), "add", "tracked.txt"], check=True)
        tracked.write_bytes(b"working version\n")
        untracked = self.checkout / "untracked.bin"
        untracked.write_bytes(b"\x00operator\xff")
        status_before = subprocess.check_output(
            ["git", "-C", str(self.checkout), "status", "--porcelain=v1", "--untracked-files=all"]
        )
        self.shell(
            """
preserve_checkout "$KILIX_DIR" kilix forward
snapshot="$PLEB_PRESERVE_RESULT"
prepare_preserved_checkout "$KILIX_DIR" "$snapshot"
restore_preserved_checkout "$KILIX_DIR" "$snapshot" rollback "$(git -C "$KILIX_DIR" rev-parse HEAD)"
"""
        )
        self.assertEqual(tracked.read_bytes(), b"working version\n")
        self.assertEqual(untracked.read_bytes(), b"\x00operator\xff")
        self.assertEqual(git(self.checkout, "show", ":tracked.txt", text=False), b"staged version")
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(self.checkout), "status", "--porcelain=v1", "--untracked-files=all"]
            ),
            status_before,
        )

    def test_corrupt_snapshot_refuses_restore_without_touching_origin(self):
        untracked = self.checkout / "sentinel"
        untracked.write_bytes(b"keep me")
        self.shell('preserve_checkout "$KILIX_DIR" kilix preserve-only')
        snapshot = self.snapshots()[0]
        preserved = snapshot / "files" / "sentinel"
        preserved.write_bytes(b"corrupt")
        result = self.shell(
            f'restore_preserved_checkout "$KILIX_DIR" {shlex.quote(str(snapshot))} success deadbeef',
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(untracked.read_bytes(), b"keep me")

    def test_size_ceiling_refuses_before_checkout_mutation(self):
        untracked = self.checkout / "too-large"
        untracked.write_bytes(b"12345")
        result = self.shell(
            '_PLEB_PRESERVE_MAX_BYTES=4; preserve_checkout "$KILIX_DIR" kilix forward',
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("preservation ceiling", result.stderr)
        self.assertEqual(untracked.read_bytes(), b"12345")
        self.assertEqual(self.snapshots(), [])

    def test_free_space_reserve_refuses_before_checkout_mutation(self):
        untracked = self.checkout / "needs-space"
        untracked.write_bytes(b"operator")
        result = self.shell(
            '_PLEB_PRESERVE_FREE_RESERVE=999999999999999999; '
            'preserve_checkout "$KILIX_DIR" kilix forward',
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("including reserve", result.stderr)
        self.assertEqual(untracked.read_bytes(), b"operator")
        self.assertEqual(self.snapshots(), [])

    def test_preservation_root_overlap_is_rejected_before_writing(self):
        overlapping = self.checkout / "state"
        result = self.shell(
            f'PLEB_STATE_HOME={shlex.quote(str(overlapping))}; preserve_checkout "$KILIX_DIR" kilix forward',
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside participating checkouts", result.stderr)
        self.assertFalse(overlapping.exists())

    def test_manifest_hashes_metadata_that_covers_symlink_target(self):
        os.symlink("outside-target", self.checkout / "operator-link")
        self.shell('preserve_checkout "$KILIX_DIR" kilix preserve-only')
        snapshot = self.snapshots()[0]
        metadata = (snapshot / "METADATA.json").read_bytes()
        manifest = (snapshot / "MANIFEST.sha256").read_text()
        self.assertIn(hashlib.sha256(metadata).hexdigest(), manifest)
        self.assertIn('"kind":"symlink"', metadata.decode())
        self.assertEqual(os.readlink(snapshot / "files" / "operator-link"), "outside-target")

    def test_non_utf8_path_round_trips_without_text_parsing(self):
        checkout = os.fsencode(self.checkout)
        relative = b"operator-\xff.bin"
        path = checkout + b"/" + relative
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        try:
            os.write(fd, b"non-utf8 pathname\n")
        finally:
            os.close(fd)
        self.shell(
            """
preserve_checkout "$KILIX_DIR" kilix forward
snapshot="$PLEB_PRESERVE_RESULT"
prepare_preserved_checkout "$KILIX_DIR" "$snapshot"
restore_preserved_checkout "$KILIX_DIR" "$snapshot" rollback "$(git -C "$KILIX_DIR" rev-parse HEAD)"
"""
        )
        with open(path, "rb") as restored:
            self.assertEqual(restored.read(), b"non-utf8 pathname\n")
        snapshot = self.snapshots()[0]
        subprocess.run(
            ["sha256sum", "-c", "MANIFEST.sha256"],
            cwd=snapshot,
            check=True,
            stdout=subprocess.DEVNULL,
        )

    def test_staged_rename_round_trips_without_losing_either_path_state(self):
        subprocess.run(
            ["git", "-C", str(self.checkout), "mv", "tracked.txt", "renamed.txt"],
            check=True,
        )
        (self.checkout / "renamed.txt").write_bytes(b"working rename edit\n")
        status_before = subprocess.check_output(
            ["git", "-C", str(self.checkout), "status", "--porcelain=v1"]
        )
        self.shell(
            """
preserve_checkout "$KILIX_DIR" kilix forward
snapshot="$PLEB_PRESERVE_RESULT"
prepare_preserved_checkout "$KILIX_DIR" "$snapshot"
restore_preserved_checkout "$KILIX_DIR" "$snapshot" rollback "$(git -C "$KILIX_DIR" rev-parse HEAD)"
"""
        )
        self.assertFalse((self.checkout / "tracked.txt").exists())
        self.assertEqual(
            (self.checkout / "renamed.txt").read_bytes(), b"working rename edit\n"
        )
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(self.checkout), "status", "--porcelain=v1"]
            ),
            status_before,
        )

    def test_retention_keeps_ten_verified_snapshots_per_checkout(self):
        changing = self.checkout / "operator-state"
        for index in range(11):
            changing.write_text(f"snapshot {index}\n")
            self.shell('preserve_checkout "$KILIX_DIR" kilix preserve-only')
        snapshots = self.snapshots()
        self.assertEqual(len(snapshots), 10)
        payloads = {
            (snapshot / "files/operator-state").read_text()
            for snapshot in snapshots
        }
        self.assertNotIn("snapshot 0\n", payloads)
        self.assertIn("snapshot 10\n", payloads)
        for snapshot in snapshots:
            subprocess.run(
                ["sha256sum", "-c", "MANIFEST.sha256"],
                cwd=snapshot,
                check=True,
                stdout=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    unittest.main()
