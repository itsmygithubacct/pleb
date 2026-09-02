"""The session holds the X selections, so copied content outlives its app.

X has no clipboard store: a selection belongs to a live client and is
transferred on demand, so it ceases to exist when that client exits. Without a
manager, copying in a browser and closing it loses the content. These tests
drive bin/pleb-session with a recording stub in place of autocutsel.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import clean_env as _clean_env  # noqa: E402

SESSION = ROOT / "bin" / "pleb-session"


def clean_env(home: Path) -> dict[str, str]:
    return _clean_env(home, GOTELEMETRY="off")


class ClipboardOwnershipTests(unittest.TestCase):
    def _run(self, home: Path, *, with_autocutsel: bool, **extra: str):
        """Run a session to completion.

        Returns (result, recorded invocations, session log). The session
        redirects its own output to PLEB_LOG, so result.stdout is empty by
        design and the log is where anything it says actually lands.
        """
        stubs = home / "stubs"
        stubs.mkdir()
        record = home / "autocutsel.log"
        if with_autocutsel:
            stub = stubs / "autocutsel"
            stub.write_text(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >>"{record}"\n'
                "exit 0\n"
            )
            stub.chmod(0o755)
        engine = home / "kilix"
        engine.write_text("#!/bin/sh\nexit 0\n")
        engine.chmod(0o755)
        env = clean_env(home)
        env.update({
            "KILIX": str(engine),
            "PLEB_NO_FILL": "1",
            "PATH": f"{stubs}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "PLEB_LOG": str(home / "session.log"),
        })
        env.update(extra)
        result = subprocess.run(
            [str(SESSION)], cwd=ROOT, env=env, text=True, capture_output=True,
        )
        calls = record.read_text().splitlines() if record.exists() else []
        log_path = home / "session.log"
        log = log_path.read_text() if log_path.exists() else ""
        return result, calls, log

    def test_by_default_both_selections_are_held(self):
        with tempfile.TemporaryDirectory() as td:
            result, calls, log = self._run(Path(td), with_autocutsel=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("-selection CLIPBOARD -fork", calls)
            self.assertIn("-selection PRIMARY -fork", calls)

    def test_clipboard_only_leaves_primary_independent(self):
        # Holding both also keeps them in step through CUTBUFFER0, so a mouse
        # selection becomes the paste buffer. This is the opt-out for anyone
        # who wants the two kept separate.
        with tempfile.TemporaryDirectory() as td:
            result, calls, log = self._run(
                Path(td), with_autocutsel=True, PLEB_CLIPBOARD="clipboard")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("-selection CLIPBOARD -fork", calls)
            self.assertNotIn("-selection PRIMARY -fork", calls)

    def test_off_holds_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            result, calls, log = self._run(
                Path(td), with_autocutsel=True, PLEB_CLIPBOARD="off")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls, [])

    def test_a_missing_autocutsel_is_reported_not_fatal(self):
        # Every install made before autocutsel joined the desktop package group
        # comes through this path, so it must not end the session -- but it
        # must say so, because the symptom it causes is otherwise inexplicable.
        with tempfile.TemporaryDirectory() as td:
            result, calls, log = self._run(Path(td), with_autocutsel=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls, [])
            self.assertIn("autocutsel absent", log)

    def test_the_control_the_stub_records_when_it_is_called(self):
        # Without this, "autocutsel was not called" and "the stub never
        # recorded anything" are the same observation.
        with tempfile.TemporaryDirectory() as td:
            _, calls, _log = self._run(Path(td), with_autocutsel=True)
            self.assertTrue(calls, "the recording stub captured nothing at all")


class AlreadyRunningCheckTests(unittest.TestCase):
    """The session must recognise a holder it started last login."""

    def _pattern(self):
        text = SESSION.read_text()
        start = text.index('pgrep -u "$(id -u)" -f "')
        pattern = text[start:].split('"')[3]     # [1] is $(id -u); the pattern follows -f
        # In the script the dollar is escaped for the shell (\\$); pgrep sees $.
        return pattern.replace("$_sel", "CLIPBOARD").replace("\\$", "$")

    def test_the_check_uses_a_pattern_match_not_an_exact_line(self):
        self.assertNotIn('-fx "autocutsel', SESSION.read_text())

    def test_the_pattern_matches_a_forked_holder_as_ps_shows_it(self):
        pattern = self._pattern()
        for line in ("autocutsel -selection CLIPBOARD -fork",
                     "/usr/bin/autocutsel -selection CLIPBOARD -fork",
                     "autocutsel -selection CLIPBOARD"):
            self.assertIsNotNone(re.search(pattern, line), (pattern, line))

    def test_the_control_the_other_selection_does_not_match(self):
        pattern = self._pattern()
        self.assertIsNone(re.search(pattern, "autocutsel -selection PRIMARY -fork"))
        self.assertIsNone(re.search(pattern, "autocutsel -selection CLIPBOARDX -fork"))

    def test_a_shell_merely_mentioning_the_holder_is_not_the_holder(self):
        # pgrep -f sees every process's whole command line. An editor or a
        # test harness running a script that contains these words must not
        # count as a running holder, or the session would never start one.
        # It did, once: the first version of this check matched the very
        # harness running these tests.
        pattern = self._pattern()
        self.assertIsNone(re.search(
            pattern, "bash -c 'grep autocutsel -selection CLIPBOARD -fork x'"))


class PackagingTests(unittest.TestCase):
    def test_the_session_documents_the_knob_it_reads(self):
        text = SESSION.read_text()
        self.assertIn("PLEB_CLIPBOARD", text.split("set -u")[0],
                      "PLEB_CLIPBOARD is read but not in the documented knobs")


if __name__ == "__main__":
    unittest.main()
