"""The pinned Debian Go files survive Debian replacing the backports upload.

Each test builds real .deb files, serves them from fixture directories that
stand in for the live archive, snapshot.debian.org and the release mirror, and
runs the real installer with only apt, curl and the dpkg database faked.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.27.3-1~bpo13+1'
GO_FILE = f'golang-1.27-go_{VERSION}_amd64.deb'
SRC_FILE = f'golang-1.27-src_{VERSION}_all.deb'
SNAPSHOT = '20261114T023814Z'
MIRROR = 'https://github.com/example/plebian-os/releases/download/debian-go-test'

APT = r'''#!/bin/bash
printf '%s\n' "$*" >> "$APT_LOG"
case " $* " in
    *" update "*) exit "${APT_UPDATE_RC:-0}" ;;
    *" download "*)
        for spec in "$@"; do
            case "$spec" in
                golang-1.27-go=*) name="golang-1.27-go_${spec#*=}_amd64.deb" ;;
                golang-1.27-src=*) name="golang-1.27-src_${spec#*=}_all.deb" ;;
                *) continue ;;
            esac
            [ -f "$LIVE/$name" ] || { echo "E: Can't find a version for $spec" >&2; exit 100; }
            cp "$LIVE/$name" "./$name"
        done
        exit 0 ;;
    *" install "*)
        [ "${APT_FAIL:-0}" = 0 ] || exit 42
        for arg in "$@"; do
            case "$arg" in
                /*.deb) printf 'installed-file %s %s\n' "${arg##*/}" "$(sha256sum < "$arg" | cut -d' ' -f1)" >> "$APT_LOG" ;;
            esac
        done
        touch "$STATE"
        exit 0 ;;
esac
'''

CURL = r'''#!/bin/bash
out= url= max=
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) out=$2; shift 2 ;;
        --max-filesize) max=$2; shift 2 ;;
        --proto|--proto-redir|--connect-timeout|--speed-limit|--speed-time|--retry) shift 2 ;;
        --) url=$2; shift 2 ;;
        *) shift ;;
    esac
done
printf '%s\n' "$url" >> "$CURL_LOG"
case "$url" in
    "https://snapshot.debian.org/archive/debian/$SNAPSHOT_TS/pool/main/g/golang-1.27/"*)
        name=${url##*/}; name=${name//%2B/+}; file="$SNAPSHOT_DIR/$name" ;;
    "$MIRROR_BASE/"*) file="$MIRROR_DIR/${url##*/}" ;;
    *) exit 6 ;;
esac
[ -f "$file" ] || exit 22
if [ -n "$max" ] && [ "$(stat -c %s "$file")" -gt "$max" ]; then exit 63; fi
cp "$file" "$out"
'''


class DebianGoFallbackTests(unittest.TestCase):
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
        self.work = self.root / 'work'
        self.work.mkdir()
        self.apt_log = self.root / 'apt.log'
        self.curl_log = self.root / 'curl.log'
        self.state = self.root / 'installed'
        self.script = self.root / 'install.sh'
        source = (ROOT / 'scripts/install-debian-go.sh').read_text()
        source = source.replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'PATH=' + str(self.bin) + ':/usr/bin:/bin')
        source = source.replace('/usr/lib/go-1.27', str(self.tool))
        source = source.replace('/usr/local/bin', str(self.links))
        source = source.replace('/tmp/pleb-debian-go.', str(self.work) + '/pleb-debian-go.')
        self.script.write_text(source)
        self.write(self.bin / 'id', '#!/bin/sh\necho 0\n')
        self.write(self.bin / 'chown', '#!/bin/sh\nexit 0\n')
        self.write(self.bin / 'dpkg-query', '#!/bin/sh\ntest -f "$STATE" || exit 1\nprintf "installed 1.27.3-1~bpo13+1"\n')
        self.write(self.bin / 'dpkg', '#!/bin/sh\nif [ "$1" = --print-architecture ]; then echo "${ARCH:-amd64}"; fi\n')
        self.write(self.bin / 'apt-get', APT)
        self.write(self.bin / 'curl', CURL)
        self.write(self.tool / 'bin/go', '#!/bin/sh\ntest -f "$STATE" || exit 1\necho "go version go1.27.3 linux/amd64"\n')
        self.debs = self.root / 'debs'
        self.debs.mkdir()
        self.go = self.build_deb('golang-1.27-go', 'amd64', GO_FILE)
        self.src = self.build_deb('golang-1.27-src', 'all', SRC_FILE)
        self.live = self.root / 'live'
        self.snapshot = self.root / 'snapshot'
        self.mirror = self.root / 'mirror'
        for directory in (self.live, self.snapshot, self.mirror):
            directory.mkdir()
        self.env = dict(
            os.environ, PATH=str(self.bin) + ':/usr/bin:/bin', STATE=str(self.state),
            APT_LOG=str(self.apt_log), CURL_LOG=str(self.curl_log), LIVE=str(self.live),
            SNAPSHOT_DIR=str(self.snapshot), SNAPSHOT_TS=SNAPSHOT,
            MIRROR_DIR=str(self.mirror), MIRROR_BASE=MIRROR,
            PLEBIAN_OS_KILIX_GO_DEB_AMD64_FILE=GO_FILE,
            PLEBIAN_OS_KILIX_GO_DEB_AMD64_BYTES=str(self.go.stat().st_size),
            PLEBIAN_OS_KILIX_GO_DEB_AMD64_SHA256=self.digest(self.go),
            PLEBIAN_OS_KILIX_GO_DEB_SRC_FILE=SRC_FILE,
            PLEBIAN_OS_KILIX_GO_DEB_SRC_BYTES=str(self.src.stat().st_size),
            PLEBIAN_OS_KILIX_GO_DEB_SRC_SHA256=self.digest(self.src),
            PLEBIAN_OS_KILIX_GO_DEB_SNAPSHOT=SNAPSHOT,
            PLEBIAN_OS_KILIX_GO_DEB_MIRROR_URL=MIRROR)

    def write(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def build_deb(self, package, arch, name, payload='pinned'):
        tree = self.root / ('tree-' + name)
        (tree / 'DEBIAN').mkdir(parents=True)
        (tree / 'usr/share/doc' / package).mkdir(parents=True)
        (tree / 'usr/share/doc' / package / 'payload').write_text(payload + '\n')
        (tree / 'DEBIAN/control').write_text(
            f'Package: {package}\nVersion: {VERSION}\nArchitecture: {arch}\n'
            'Maintainer: Fixture <fixture@example.invalid>\nDescription: fixture\n')
        # dpkg-deb insists on these modes whatever the caller's umask is.
        for path in [tree, *tree.rglob('*')]:
            path.chmod(0o755 if path.is_dir() else 0o644)
        out = self.debs / name
        subprocess.run(['dpkg-deb', '--root-owner-group', '-Zgzip', '--build', str(tree), str(out)],
                       check=True, capture_output=True)
        return out

    def tampered(self, path):
        """The same size as the pinned file, one byte different."""
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        out = self.debs / ('tampered-' + path.name)
        out.write_bytes(bytes(data))
        return out

    def serve(self, directory, path, name=None):
        target = directory / (name or path.name)
        target.write_bytes(path.read_bytes())

    def serve_mirror(self, path):
        self.serve(self.mirror, path, path.name.replace('~', '.').replace('+', '.'))

    def run_install(self):
        return subprocess.run(['bash', str(self.script), 'go1.27.3', VERSION],
                              env=self.env, text=True, capture_output=True)

    def installed_files(self):
        if not self.apt_log.exists():
            return {}
        return dict(line.split()[1:3] for line in self.apt_log.read_text().splitlines()
                    if line.startswith('installed-file '))

    def assert_pinned_bytes_installed(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        # apt sees no index during the install, so it cannot substitute a copy
        # of the same version from the archive for the verified file.
        install = [line for line in self.apt_log.read_text().splitlines() if ' install ' in line]
        self.assertEqual(len(install), 1, install)
        self.assertRegex(install[0], r'Dir::Etc::sourcelist=\S*/local\.sources ')
        self.assertRegex(install[0], r'Dir::State::lists=\S*/local-lists ')
        self.assertNotIn('debian.sources', install[0])
        self.assertNotIn('trixie-backports', install[0])
        self.assertEqual(self.installed_files(),
                         {GO_FILE: self.digest(self.go), SRC_FILE: self.digest(self.src)})
        self.assertEqual((self.links / 'go').resolve(), self.tool / 'bin/go')
        self.assertEqual(list(self.work.iterdir()), [], 'the private work tree was left behind')

    def assert_nothing_installed(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.installed_files(), {})
        self.assertNotIn(' install ', ' ' + (self.apt_log.read_text() if self.apt_log.exists() else '') + ' ')
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.work.iterdir()), [], 'the private work tree was left behind')

    def curl_urls(self):
        return self.curl_log.read_text().splitlines() if self.curl_log.exists() else []

    def test_live_archive_supplies_verified_files_without_fallback(self):
        self.serve(self.live, self.go)
        self.serve(self.live, self.src)
        result = self.run_install()
        self.assert_pinned_bytes_installed(result)
        self.assertEqual(self.curl_urls(), [])
        log = self.apt_log.read_text()
        self.assertIn(f'download golang-1.27-go={VERSION} golang-1.27-src={VERSION}', log)
        self.assertIn('AllowUnauthenticated=false', log)
        self.assertIn('--no-install-recommends', log)
        self.assertIn('verified ' + GO_FILE + ' from the live trixie-backports archive', result.stderr)

    def test_replaced_live_upload_falls_back_to_snapshot(self):
        self.serve(self.snapshot, self.go)
        self.serve(self.snapshot, self.src)
        self.serve_mirror(self.go)
        self.serve_mirror(self.src)
        result = self.run_install()
        self.assert_pinned_bytes_installed(result)
        urls = self.curl_urls()
        self.assertEqual(len(urls), 2, urls)
        for url in urls:
            self.assertTrue(url.startswith(
                f'https://snapshot.debian.org/archive/debian/{SNAPSHOT}/pool/main/g/golang-1.27/'), url)
            self.assertIn('~bpo13%2B1_', url)
        self.assertIn('does not offer', result.stderr)

    def test_unavailable_snapshot_falls_back_to_the_release_mirror(self):
        self.serve_mirror(self.go)
        self.serve_mirror(self.src)
        result = self.run_install()
        self.assert_pinned_bytes_installed(result)
        urls = self.curl_urls()
        self.assertEqual(urls[-2:], [MIRROR + '/golang-1.27-go_1.27.3-1.bpo13.1_amd64.deb',
                                     MIRROR + '/golang-1.27-src_1.27.3-1.bpo13.1_all.deb'])

    def test_each_file_comes_from_the_first_source_that_verifies(self):
        self.serve(self.live, self.src)
        self.serve(self.snapshot, self.go)
        self.write(self.bin / 'apt-get', APT.replace(
            '[ -f "$LIVE/$name" ] || { echo',
            '[ -f "$LIVE/$name" ] || continue; [ -f "$LIVE/$name" ] || { echo'))
        result = self.run_install()
        self.assert_pinned_bytes_installed(result)
        self.assertEqual(len(self.curl_urls()), 1)

    def test_hash_mismatch_from_snapshot_is_refused_and_mirror_used(self):
        tampered = self.tampered(self.go)
        self.serve(self.snapshot, tampered, GO_FILE)
        self.serve(self.snapshot, self.src)
        self.serve_mirror(self.go)
        result = self.run_install()
        self.assert_pinned_bytes_installed(result)
        self.assertIn('refused ' + GO_FILE + ' from snapshot.debian.org', result.stderr)

    def test_hash_mismatch_everywhere_installs_nothing(self):
        (self.links / 'go').symlink_to('/usr/local/go/bin/go')
        tampered = self.tampered(self.go)
        self.serve(self.live, tampered, GO_FILE)
        self.serve(self.live, self.src)
        self.serve(self.snapshot, tampered, GO_FILE)
        self.serve(self.mirror, tampered, 'golang-1.27-go_1.27.3-1.bpo13.1_amd64.deb')
        result = self.run_install()
        self.assert_nothing_installed(result)
        self.assertIn('SHA-256', result.stderr)
        self.assertIn('nothing was installed', result.stderr)
        self.assertEqual(os.readlink(self.links / 'go'), '/usr/local/go/bin/go')

    def test_size_mismatch_is_refused(self):
        bigger = self.build_deb('golang-1.27-go', 'amd64', 'bigger-' + GO_FILE, payload='x' * 4096)
        self.serve(self.snapshot, self.src)
        self.serve(self.mirror, bigger, 'golang-1.27-go_1.27.3-1.bpo13.1_amd64.deb')
        smaller = self.build_deb('golang-1.27-go', 'amd64', 'smaller-' + GO_FILE, payload='')
        self.serve(self.snapshot, smaller, GO_FILE)
        result = self.run_install()
        self.assert_nothing_installed(result)
        self.assertIn('bytes, pinned', result.stderr)

    def test_wrong_package_with_pinned_digest_is_refused(self):
        other = self.build_deb('golang-1.27-src', 'all', 'other-' + GO_FILE)
        self.env['PLEBIAN_OS_KILIX_GO_DEB_AMD64_BYTES'] = str(other.stat().st_size)
        self.env['PLEBIAN_OS_KILIX_GO_DEB_AMD64_SHA256'] = self.digest(other)
        self.serve(self.snapshot, other, GO_FILE)
        self.serve(self.snapshot, self.src)
        result = self.run_install()
        self.assert_nothing_installed(result)
        self.assertIn('control fields', result.stderr)

    def test_all_sources_down_fails_cleanly(self):
        (self.links / 'go').symlink_to('/usr/local/go/bin/go')
        self.env['APT_UPDATE_RC'] = '100'
        result = self.run_install()
        self.assert_nothing_installed(result)
        self.assertEqual(len(self.curl_urls()), 4)
        self.assertIn('no source supplied verified files', result.stderr)
        self.assertEqual(os.readlink(self.links / 'go'), '/usr/local/go/bin/go')

    def test_failed_install_of_verified_files_keeps_old_command(self):
        (self.links / 'go').symlink_to('/usr/local/go/bin/go')
        self.serve(self.snapshot, self.go)
        self.serve(self.snapshot, self.src)
        self.env['APT_FAIL'] = '1'
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(os.readlink(self.links / 'go'), '/usr/local/go/bin/go')
        self.assertEqual(list(self.work.iterdir()), [])

    def test_incomplete_pins_are_refused_before_apt(self):
        del self.env['PLEBIAN_OS_KILIX_GO_DEB_SRC_SHA256']
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('incomplete package file pins', result.stderr)
        self.assertFalse(self.apt_log.exists())

    def test_pins_for_another_version_are_refused_before_apt(self):
        self.env['PLEBIAN_OS_KILIX_GO_DEB_AMD64_FILE'] = 'golang-1.27-go_1.27.4-1~bpo13+1_amd64.deb'
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('is not the amd64 package', result.stderr)
        self.assertFalse(self.apt_log.exists())

    def test_malformed_fallback_locations_are_refused_before_apt(self):
        for key, value in (('PLEBIAN_OS_KILIX_GO_DEB_SNAPSHOT', '2026-11-14'),
                           ('PLEBIAN_OS_KILIX_GO_DEB_MIRROR_URL', 'http://example.invalid/go'),
                           ('PLEBIAN_OS_KILIX_GO_DEB_MIRROR_URL', MIRROR + '/'),
                           ('PLEBIAN_OS_KILIX_GO_DEB_AMD64_SHA256', 'A' * 64),
                           ('PLEBIAN_OS_KILIX_GO_DEB_SRC_BYTES', '0')):
            with self.subTest(key=key, value=value):
                env = dict(self.env, **{key: value})
                result = subprocess.run(['bash', str(self.script), 'go1.27.3', VERSION],
                                        env=env, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.apt_log.exists())

    def test_ready_install_needs_no_source(self):
        self.state.touch()
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.apt_log.exists())
        self.assertEqual(self.curl_urls(), [])

    def test_unpinned_selection_keeps_the_archive_install(self):
        for key in list(self.env):
            if key.startswith('PLEBIAN_OS_KILIX_GO_DEB_'):
                del self.env[key]
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.apt_log.read_text()
        self.assertIn(f'-t trixie-backports golang-1.27-go={VERSION} golang-1.27-src={VERSION}', log)
        self.assertNotIn(' download ', log)
        self.assertEqual(self.curl_urls(), [])

    def test_build_passes_the_file_pins_to_the_installer(self):
        source = (ROOT / 'lib/update.sh').read_text()
        function = source.split('_ensure_go_for_kilix_build() {', 1)[1].split('\n}\n', 1)[0]
        scripts = self.root / 'scripts'
        scripts.mkdir()
        record = self.root / 'installer-env'
        self.write(scripts / 'install-debian-go.sh',
                   '#!/bin/sh\nenv | grep "^PLEBIAN_OS_KILIX_GO_DEB_" | sort > "$RECORD"\n')
        script = '_ensure_go_for_kilix_build() {' + function + '\n}\n'
        script += '_ensure_go_for_kilix_build'
        values = {key: value for key, value in self.env.items()
                  if key.startswith('PLEBIAN_OS_KILIX_GO_DEB_')}
        # Closure files are sourced, not exported: the build must pass them on.
        assignments = ''.join(f'{key}={value!r}\n' for key, value in values.items())
        env = {key: value for key, value in self.env.items()
               if not key.startswith('PLEBIAN_OS_KILIX_GO_DEB_')}
        env.update(PLEB_ROOT=str(self.root), RECORD=str(record),
                   PLEBIAN_OS_KILIX_GO_VERSION='go1.27.3',
                   PLEBIAN_OS_KILIX_GO_DEBIAN_VERSION=VERSION)
        result = subprocess.run(['bash', '-euc', assignments + script], env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(record.read_text(),
                         ''.join(f'{key}={values[key]}\n' for key in sorted(values)))

    def test_build_and_release_surface_name_the_same_file_pins(self):
        import re
        common = (ROOT / 'lib/common.sh').read_text()
        controlled = common.split('PLEB_RELEASE_CONTROLLED_KEYS="', 1)[1].split('"', 1)[0].split()
        update = (ROOT / 'lib/update.sh').read_text()
        function = update.split('_ensure_go_for_kilix_build() {', 1)[1].split('\n}\n', 1)[0]
        passed = re.findall(r'PLEBIAN_OS_KILIX_GO_DEB_[A-Z0-9_]+', function)
        script = (ROOT / 'scripts/install-debian-go.sh').read_text()
        read = set(re.findall(r'\$\{(PLEBIAN_OS_KILIX_GO_DEB_[A-Z0-9_]+):-\}', script))
        expected = [key for key in controlled if key.startswith('PLEBIAN_OS_KILIX_GO_DEB_')]
        self.assertEqual(len(expected), 8)
        self.assertEqual(passed, expected)
        self.assertEqual(read, set(expected))


if __name__ == '__main__':
    unittest.main()
