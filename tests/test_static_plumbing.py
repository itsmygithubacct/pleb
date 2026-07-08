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


if __name__ == "__main__":
    unittest.main()
