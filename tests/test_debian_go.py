"""Exercise package installation and migration without touching the host apt tree."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DebianGoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.tool = self.root / 'go-1.27'
        (self.tool / 'bin').mkdir(parents=True)
        self.links = self.root / 'links'
        self.links.mkdir()
        self.log = self.root / 'apt.log'
        self.state = self.root / 'installed'
        self.script = self.root / 'install.sh'
        source = (ROOT / 'scripts/install-debian-go.sh').read_text()
        source = source.replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'PATH=' + str(self.bin) + ':/usr/bin:/bin')
        source = source.replace('/usr/lib/go-1.27', str(self.tool))
        source = source.replace('/usr/local/bin', str(self.links))
        self.script.write_text(source)
        self.write(self.bin / 'id', '#!/bin/sh\necho 0\n')
        self.write(self.bin / 'dpkg-query', '#!/bin/sh\ntest -f "$STATE" || exit 1\nprintf "installed 1.27.3-1~bpo13+1"\n')
        self.write(self.bin / 'dpkg', '#!/bin/sh\nif [ "$1" = --print-architecture ]; then echo amd64; fi\n')
        self.write(self.bin / 'apt-get', '#!/bin/sh\nprintf "%s\\n" "$*" >> "$APT_LOG"\n[ "${APT_FAIL:-0}" = 0 ] || exit 42\ncase "$*" in *install*) touch "$STATE";; esac\n')
        self.write(self.tool / 'bin/go', '#!/bin/sh\ntest -f "$STATE" || exit 1\necho "go version go1.27.3 linux/amd64"\n')
        self.env = dict(os.environ, PATH=str(self.bin) + ':/usr/bin:/bin',
                        STATE=str(self.state), APT_LOG=str(self.log))

    def write(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    def run_install(self, version='1.27.3-1~bpo13+1'):
        return subprocess.run(['bash', str(self.script), 'go1.27.3', version],
                              env=self.env, text=True, capture_output=True)

    def test_build_selects_debian_even_with_an_old_goroot(self):
        source = (ROOT / 'lib/update.sh').read_text()
        function = source.split('_ensure_go_for_kilix_build() {', 1)[1].split('\n}\n', 1)[0]
        scripts = self.root / 'scripts'
        scripts.mkdir()
        self.write(scripts / 'install-debian-go.sh', '#!/bin/sh\ntest "$1" = go1.27.3 && test "$2" = 1.27.3-1~bpo13+1\n')
        script = '_ensure_go_for_kilix_build() {' + function + '\n}\n'
        script += '_ensure_go_for_kilix_build; printf "%s %s %s" "$GOROOT" "$GOENV" "$GOTOOLCHAIN"'
        env = dict(self.env, PLEB_ROOT=str(self.root),
                   PLEBIAN_OS_KILIX_GO_VERSION='go1.27.3',
                   PLEBIAN_OS_KILIX_GO_DEBIAN_VERSION='1.27.3-1~bpo13+1',
                   GOROOT='/usr/local/go')
        result = subprocess.run(['bash', '-euc', script], env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '/usr/lib/go-1.27 off local')

    def test_fresh_install_pins_both_packages_and_links_compiler(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text()
        self.assertIn('-t trixie-backports', calls)
        self.assertIn('golang-1.27-go=1.27.3-1~bpo13+1', calls)
        self.assertIn('golang-1.27-src=1.27.3-1~bpo13+1', calls)
        self.assertIn('AllowUnauthenticated=false', calls)
        self.assertEqual((self.links / 'go').resolve(), self.tool / 'bin/go')

    def test_matching_install_skips_apt_and_migrates_old_archive_links(self):
        self.state.touch()
        (self.links / 'go').symlink_to('/usr/local/go/bin/go')
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual((self.links / 'go').resolve(), self.tool / 'bin/go')

    def test_failed_apt_keeps_old_command(self):
        (self.links / 'go').symlink_to('/usr/local/go/bin/go')
        self.env['APT_FAIL'] = '1'
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(os.readlink(self.links / 'go'), '/usr/local/go/bin/go')

    def test_mismatched_selection_never_calls_apt(self):
        result = self.run_install('1.27.2-1~bpo13+1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_custom_command_is_preserved(self):
        self.state.touch()
        (self.links / 'go').write_text('custom')
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.links / 'go').read_text(), 'custom')

    def test_changed_package_files_force_reinstall(self):
        self.state.touch()
        self.write(self.bin / 'dpkg', '#!/bin/sh\nif [ "$1" = --verify ] && [ ! -f "$APT_LOG" ]; then echo "??5?????? changed"; elif [ "$1" = --print-architecture ]; then echo amd64; fi\n')
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--reinstall', self.log.read_text())
