"""Checks on the suite's own environment sanitiser.

These exist because the sanitiser previously lived as four hand-maintained
copies and a fix reached only one of them. Every assertion here is about the
suite's ability to tell the truth about the product, so a regression here
invalidates results elsewhere rather than merely failing on its own.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import clean_env, world_writable_ancestor  # noqa: E402

PROBE = "plebtestfn"
CARRIER = f"BASH_FUNC_{PROBE}%%"
BODY = "() { echo ambient; }"


class SanitiserTests(unittest.TestCase):
    def test_an_exported_shell_function_cannot_reach_a_child_shell(self):
        # The control comes first, deliberately. An assertion that the function
        # is absent is only worth anything once the same probe has been shown
        # to find it when it IS there -- otherwise a typo in the probe passes
        # this test for the wrong reason.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with mock.patch.dict(os.environ, {CARRIER: BODY}):
                dirty = os.environ.copy()
                dirty["HOME"] = str(tmp)
                control = subprocess.run(
                    ["bash", "-c", f"type -t {PROBE} || echo absent"],
                    env=dirty, text=True, capture_output=True,
                )
                self.assertEqual(
                    control.stdout.strip(), "function",
                    "control failed: the probe cannot see an exported function "
                    "even when one is present, so it proves nothing below",
                )

                env = clean_env(tmp)
                self.assertNotIn(CARRIER, env)
                cleaned = subprocess.run(
                    ["bash", "-c", f"type -t {PROBE} || echo absent"],
                    env=env, text=True, capture_output=True,
                )
                self.assertEqual(cleaned.stdout.strip(), "absent")

    def test_the_project_s_own_configuration_is_stripped(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            noise = {
                "KILIX_STORAGE_HOME": "/somewhere/real",
                "GPU_TERMINAL_SOURCE_HOME": "/somewhere/real",
                "PLEB_ROOT": "/somewhere/real",
            }
            with mock.patch.dict(os.environ, noise):
                env = clean_env(tmp)
            for key in noise:
                self.assertNotEqual(env.get(key), "/somewhere/real", key)
            self.assertEqual(env["HOME"], str(tmp))

    def test_overrides_are_applied_after_stripping(self):
        # PLEB_ROOT starts with a stripped prefix; an override of it must
        # survive, or callers would silently get no value at all.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            env = clean_env(tmp, PLEB_ROOT=str(ROOT))
            self.assertEqual(env["PLEB_ROOT"], str(ROOT))

    def test_the_sanitiser_has_exactly_one_definition(self):
        # Four copies of this sanitiser drifted once already: the BASH_FUNC_*
        # fix was applied to one and the other three stayed subvertible. Four
        # copies agreeing is a promise. This is the check.
        here = Path(__file__).resolve().parent
        # Assembled rather than written literally: spelled out in one piece
        # this string matches the file it is written in, and the check reports
        # itself. It did exactly that on first run.
        signature = '.startswith((' + '"GPU_TERMINAL"'
        offenders = sorted(
            f.name for f in here.glob("*.py")
            if f.name != "_env_support.py" and signature in f.read_text()
        )
        self.assertEqual(
            offenders, [],
            "these modules define their own environment sanitiser instead of "
            "importing the shared one; that is how the last gap survived",
        )


class WorldWritableAncestorTests(unittest.TestCase):
    def test_it_names_the_offending_component(self):
        self.assertEqual(world_writable_ancestor(Path("/tmp")), Path("/tmp"))

    def test_a_private_chain_has_none(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as td:
            tmp = Path(td)
            tmp.chmod(0o700)
            if world_writable_ancestor(Path.home()) is not None:
                self.skipTest("$HOME itself sits below a world-writable component")
            self.assertIsNone(world_writable_ancestor(tmp))


if __name__ == "__main__":
    unittest.main()
