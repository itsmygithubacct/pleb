"""The first Pleb session sets the microphone input to 45%, once.

The reference laptop's internal microphone opened at 25% (too quiet to
dictate reliably), and at 60% with its +12 dB boost it clipped. The owner chose
45% as the RC3 default. The session applies it once per user and records that,
so a level the user picks afterwards is kept. These tests drive
bin/pleb-session with a recording pactl stub.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import clean_env as _clean_env  # noqa: E402

SESSION = ROOT / "bin" / "pleb-session"


class MicDefaultTests(unittest.TestCase):
    def _run(self, home: Path, *, source="alsa_input.pci.analog-stereo", set_ok=True, **extra):
        stubs = home / "stubs"
        stubs.mkdir(exist_ok=True)
        record = home / "pactl.log"
        stub = stubs / "pactl"
        stub.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >>"{record}"\n'
            f'[ "$1" = get-default-source ] && {{ echo "{source}"; exit 0; }}\n'
            f'[ "$1" = set-source-volume ] && exit {0 if set_ok else 1}\n'
            "exit 0\n")
        stub.chmod(0o755)
        system = home / "system-bin"
        if not system.exists():
            system.mkdir()
            for directory in ("/usr/bin", "/bin"):
                for tool in os.scandir(directory):
                    if tool.name not in ("pactl", "autocutsel") and not os.path.lexists(system / tool.name):
                        (system / tool.name).symlink_to(tool.path)
        engine = home / "kilix"
        engine.write_text("#!/bin/sh\nexit 0\n")
        engine.chmod(0o755)
        env = _clean_env(home, GOTELEMETRY="off")
        env.update({"KILIX": str(engine), "PLEB_NO_FILL": "1",
                    "PATH": f"{stubs}{os.pathsep}{system}",
                    "PLEB_LOG": str(home / "state" / "session.log")})
        env.update(extra)
        result = subprocess.run([str(SESSION)], cwd=ROOT, env=env, text=True, capture_output=True)
        calls = record.read_text().splitlines() if record.exists() else []
        record.unlink(missing_ok=True)
        return result, calls, home / "state" / "mic-default-applied"

    def test_first_session_sets_45_percent_and_records_it(self):
        with tempfile.TemporaryDirectory() as td:
            result, calls, marker = self._run(Path(td))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("set-source-volume alsa_input.pci.analog-stereo 45%", calls)
            self.assertTrue(marker.exists())

    def test_later_sessions_keep_the_users_level(self):
        with tempfile.TemporaryDirectory() as td:
            self._run(Path(td))
            result, calls, _marker = self._run(Path(td))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse([c for c in calls if c.startswith("set-source-volume")])

    def test_a_failed_set_is_retried_next_session(self):
        with tempfile.TemporaryDirectory() as td:
            _result, _calls, marker = self._run(Path(td), set_ok=False)
            self.assertFalse(marker.exists())
            _result, calls, marker = self._run(Path(td))
            self.assertIn("set-source-volume alsa_input.pci.analog-stereo 45%", calls)
            self.assertTrue(marker.exists())

    def test_a_speaker_monitor_is_not_a_microphone(self):
        with tempfile.TemporaryDirectory() as td:
            _result, calls, marker = self._run(Path(td), source="alsa_output.pci.analog-stereo.monitor")
            self.assertFalse([c for c in calls if c.startswith("set-source-volume")])
            self.assertFalse(marker.exists())

    def test_the_level_is_configurable_and_can_be_turned_off(self):
        with tempfile.TemporaryDirectory() as td:
            _result, calls, _m = self._run(Path(td), PLEB_MIC_DEFAULT="30")
            self.assertIn("set-source-volume alsa_input.pci.analog-stereo 30%", calls)
        with tempfile.TemporaryDirectory() as td:
            _result, calls, marker = self._run(Path(td), PLEB_MIC_DEFAULT="off")
            self.assertEqual(calls, [])
            self.assertFalse(marker.exists())
        with tempfile.TemporaryDirectory() as td:
            _result, calls, _m = self._run(Path(td), PLEB_MIC_DEFAULT="loud; rm -rf /")
            self.assertFalse([c for c in calls if c.startswith("set-source-volume")])


if __name__ == "__main__":
    unittest.main()
