"""Run the real session initialization block with a private XDG home."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('xdg-user-dirs-update'), 'xdg-user-dirs required')
class UserDirectoryTests(unittest.TestCase):
    def initialize(self, home):
        source = (ROOT/'bin/pleb-session').read_text()
        start = source.index('if command -v xdg-user-dirs-update')
        end = source.index('\nfi', start) + len('\nfi')
        return subprocess.run(['bash', '-eu', '-c', source[start:end]],
            env={'HOME': str(home), 'XDG_CONFIG_HOME': str(home/'.config'),
                 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, capture_output=True, text=True)

    def test_first_session_creates_downloads_before_desktop_helpers(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            result = self.initialize(home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((home/'Downloads').is_dir())
            self.assertIn('XDG_DOWNLOAD_DIR="$HOME/Downloads"',
                          (home/'.config/user-dirs.dirs').read_text())

    def test_existing_download_directory_and_contents_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            downloads = home/'My Downloads'
            downloads.mkdir()
            (downloads/'keep').write_text('existing file')
            (home/'.config').mkdir()
            config = home/'.config/user-dirs.dirs'
            config.write_text('XDG_DOWNLOAD_DIR="$HOME/My Downloads"\n')
            for _ in range(2):
                result = self.initialize(home)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('XDG_DOWNLOAD_DIR="$HOME/My Downloads"', config.read_text())
            self.assertEqual((downloads/'keep').read_text(), 'existing file')
