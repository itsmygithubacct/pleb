"""What `pleb install` asks apt for must not hold the sound card at login.

The defect this guards: Kilix Amp needs the FluidSynth *library* and a General
MIDI SoundFont. On Debian and Ubuntu the package called ``fluidsynth`` is the
standalone player, and its package enables a per-user service for every login
that opens the default sound card -- the card dictation records from. Pleb
used to install that package by name.

These tests assert the capability, never a package string:

* they run the real ``ensure_system_deps`` and record the apt transactions it
  would perform on a machine with nothing installed;
* they hand exactly those transactions to apt's own resolver in simulation
  (``apt-get install -s``), once against an empty package state (the whole
  closure a fresh machine would receive) and once against this machine's real
  state (what a re-install here would remove);
* they reconstruct the systemd user units the fresh closure ships and ask
  whether any enabled one holds the sound card, whether any shipped program is
  a standalone synthesiser, and whether Amp still gets its library and a
  SoundFont.

A package that brings the player in *as a dependency* fails this exactly as
naming it would, which is the point: listing ``libfluidsynth-dev`` instead
would bring the player back through its versioned hard ``Depends``.

Reach, stated rather than assumed. The resolver arms need apt and its package
lists. Reading what a package ships uses this machine's dpkg file lists, so a
package in the closure that is not installed here is invisible to the unit
model; the tests report those names and carry a positive control that must
fire, so "found nothing" cannot be mistaken for "looked at nothing". Where the
control cannot be built here (the defect's package is not installed on this
machine) the unit arms skip and say why. Everything here is Debian-family.
Nothing is installed, removed, enabled or disabled: apt runs with ``-s`` and
the machine is only read.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from _env_support import clean_env

ROOT = Path(__file__).resolve().parent.parent

#: A unit that declares it wants the sound stack.
SOUND_STACK_DEPENDENCY = re.compile(
    r"^(?:Wants|Requires|Requisite|BindsTo|PartOf|Upholds)=.*"
    r"(?:sound\.target|pipewire|pulseaudio|jack)",
    re.M,
)
#: A program linked against an audio client library opens the card itself.
AUDIO_CLIENT_LIBRARY = re.compile(r"\blib(?:asound|pulse|pipewire-[\d.]+|jack)\.so")
#: The sound server is supposed to own the card; clients reach it through one
#: of these socket endpoints. A service activated from such a socket is the
#: server, not a client holding the card behind the server's back.
SOUND_SERVER_ENDPOINT = re.compile(r"^ListenStream=.*(?:/pulse/native|/pipewire-\d+)\s*$", re.M)
#: The synthesiser library Kilix Amp loads (``ldd kilix-amp``).
SYNTH_LIBRARY = re.compile(r"libfluidsynth\.so\.\d+")
#: Where Debian-family SoundFont packages install General MIDI banks.
SOUNDFONT = re.compile(r"^/usr/share/sounds/sf[23]/[^/]+\.sf[23]$")
#: Enablement keys `deb-systemd-helper` / `systemctl --global enable` act on.
ENABLEMENT = re.compile(r"^(?:WantedBy|RequiredBy|UpheldBy)=\s*\S", re.M)
SUMMARY = re.compile(
    r"^(\d+) upgraded, (\d+) newly installed, (\d+) to remove and (\d+) not upgraded\.$",
    re.M,
)
INST = re.compile(r"^Inst (\S+) (?:\[[^\]]*\] )?\((\S+) .*\[([^\]]+)\]\)", re.M)


def parse_removals(output: str) -> int:
    """`N to remove` from an apt simulation; a missing summary is an error."""
    found = SUMMARY.findall(output)
    if len(found) != 1:
        raise AssertionError(f"apt simulation printed {len(found)} summary lines:\n{output[-2000:]}")
    return int(found[0][2])


def recorded_apt_transactions(home: Path) -> list[list[str]]:
    """The `apt-get install` argv lists the real ensure_system_deps would run.

    The machine is presented as fresh: `dpkg-query` reports nothing installed,
    so every name pleb asks for is passed through. `run_root` records instead
    of executing, and an `apt-get` stub fails loudly if anything reaches it.
    """
    stubs = home / "stubs"
    stubs.mkdir()
    (stubs / "dpkg-query").write_text("#!/bin/sh\nexit 1\n")
    (stubs / "apt-get").write_text("#!/bin/sh\necho 'apt-get reached' >&2\nexit 97\n")
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    record = home / "apt-record"
    script = textwrap.dedent(
        f"""
        set -euo pipefail
        PLEB_CODE_ROOT={str(ROOT)!r}
        PLEB_ROOT="$PLEB_CODE_ROOT"
        . "$PLEB_ROOT/lib/common.sh"
        . "$PLEB_ROOT/lib/install.sh"
        run_root() {{ printf '%s\\n' "$@" '--END--' >> {str(record)!r}; }}
        ensure_system_deps
        """
    )
    env = clean_env(home)
    env["PATH"] = f"{stubs}:{env.get('PATH', '/usr/bin:/bin')}"
    result = subprocess.run(
        ["bash", "-c", script], cwd=ROOT, env=env, text=True,
        capture_output=True, stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise AssertionError(f"ensure_system_deps failed:\n{result.stderr}")
    calls, current = [], []
    for line in record.read_text().splitlines() if record.exists() else []:
        if line == "--END--":
            calls.append(current)
            current = []
        else:
            current.append(line)
    installs = []
    for argv in calls:
        if "apt-get" not in argv:
            continue
        apt = argv[argv.index("apt-get"):]
        if len(apt) > 1 and "install" in apt[1:]:
            installs.append(apt)
    return installs


def simulate(apt_argv: list[str], status_file: Path | None) -> subprocess.CompletedProcess[str]:
    """Run pleb's own apt argv under `-s`, optionally against a given dpkg state."""
    argv = ["apt-get", "-s"]
    if status_file is not None:
        argv += ["-o", f"Dir::State::status={status_file}"]
    argv += apt_argv[1:]
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
    return subprocess.run(argv, env=env, text=True, capture_output=True, stdin=subprocess.DEVNULL)


def closure_of(output: str) -> dict[str, str]:
    """{package: architecture} for every `Inst` line of a simulation."""
    return {m.group(1): m.group(3).split()[-1] for m in INST.finditer(output)}


def shipped_files(package: str, arch: str) -> list[str] | None:
    """What an installed package ships, from dpkg's own list; None if unknown."""
    info = Path("/var/lib/dpkg/info")
    for name in (f"{package}:{arch}.list", f"{package}.list"):
        path = info / name
        if path.is_file():
            return [line for line in path.read_text(errors="replace").splitlines() if line]
    return None


def linkage(program: str) -> str:
    """`ldd`'s report for a dynamically linked ELF program; empty otherwise."""
    path = Path(program)
    try:
        if not path.is_file():
            return ""
        with path.open("rb") as handle:
            if handle.read(4) != b"\x7fELF":
                return ""
    except OSError:
        return ""
    result = subprocess.run(["ldd", program], text=True, capture_output=True,
                            stdin=subprocess.DEVNULL)
    return result.stdout


def installed_file_matching(predicate) -> bool:
    """Whether any package installed on this machine ships a matching path."""
    for listing in Path("/var/lib/dpkg/info").glob("*.list"):
        if any(predicate(line) for line in listing.read_text(errors="replace").splitlines()):
            return True
    return False


def links_audio_client(program: str) -> bool:
    return bool(AUDIO_CLIENT_LIBRARY.search(linkage(program)))


def exec_program(unit_text: str) -> str:
    match = re.search(r"^ExecStart=([-@:+!]*)(\S+)", unit_text, re.M)
    return match.group(2) if match else ""


class Image:
    """The fresh closure, modelled from what its packages ship."""

    def __init__(self, closure: dict[str, str]):
        self.closure = closure
        self.unreadable: list[str] = []
        self.units: dict[str, tuple[str, str]] = {}  # unit -> (package, text)
        self.programs: list[tuple[str, str]] = []
        self.files: list[str] = []
        for package, arch in sorted(closure.items()):
            files = shipped_files(package, arch)
            if files is None:
                self.unreadable.append(package)
                continue
            self.files.extend(files)
            for path in files:
                if re.match(r"^(?:/usr)?/lib/systemd/user/[^/]+\.(?:service|socket)$", path):
                    if Path(path).is_file():
                        self.units[Path(path).name] = (package, Path(path).read_text(errors="replace"))
                elif re.match(r"^/usr/s?bin/[^/]+$", path):
                    self.programs.append((package, path))

    def enabled_services(self) -> dict[str, str]:
        """{service: package} for every service a package enables for logins.

        A socket that is enabled starts its service on first connection, so it
        enables that service too.
        """
        enabled = {}
        for name, (package, text) in self.units.items():
            if not ENABLEMENT.search(text):
                continue
            if name.endswith(".socket"):
                service = re.search(r"^Service=(\S+)", text, re.M)
                name = service.group(1) if service else name[: -len(".socket")] + ".service"
            if name in self.units:
                enabled[name] = self.units[name][0]
        return enabled

    def sound_servers(self) -> set[str]:
        servers = set()
        for name, (_package, text) in self.units.items():
            if name.endswith(".socket") and SOUND_SERVER_ENDPOINT.search(text):
                service = re.search(r"^Service=(\S+)", text, re.M)
                servers.add(service.group(1) if service else name[: -len(".socket")] + ".service")
        return servers

    def holds_card(self, service: str) -> bool:
        text = self.units[service][1]
        return bool(SOUND_STACK_DEPENDENCY.search(text)) or links_audio_client(exec_program(text))

    def card_holders(self) -> tuple[list[str], list[str]]:
        """(clients holding the card at login, sound servers holding it)."""
        servers = self.sound_servers()
        holders = sorted(s for s in self.enabled_services() if self.holds_card(s))
        return [s for s in holders if s not in servers], [s for s in holders if s in servers]

    def synthesiser_programs(self) -> list[str]:
        """Shipped programs that link the synth library: a standalone player."""
        found = []
        for package, program in self.programs:
            if SYNTH_LIBRARY.search(linkage(program)):
                found.append(f"{program} ({package})")
        return found


def apt_is_usable() -> bool:
    if shutil.which("apt-get") is None or shutil.which("apt-cache") is None:
        return False
    names = subprocess.run(["apt-cache", "pkgnames"], text=True, capture_output=True,
                           stdin=subprocess.DEVNULL)
    return names.returncode == 0 and bool(names.stdout.strip())


@unittest.skipUnless(apt_is_usable(), "needs apt-get with package lists (Debian-family only)")
class PlebAudioDependencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        home = base / "home"
        home.mkdir()
        cls.transactions = recorded_apt_transactions(home)
        cls.empty_status = base / "empty-status"
        cls.empty_status.write_text("")
        cls._closures = {}
        cls._images = {}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def fresh_closure(self, extra: list[str] | None = None) -> dict[str, str]:
        """The packages a fresh machine receives from pleb's transactions.

        The first transaction is the required runtime set and must resolve;
        a later one pleb tolerates failing (`|| warn`) contributes nothing when
        apt cannot resolve it, exactly as on a real machine.
        """
        key = tuple(extra or ())
        if key in self._closures:
            return self._closures[key]
        closure: dict[str, str] = {}
        for index, argv in enumerate(self.transactions):
            argv = argv + (extra or []) if index == 0 else argv
            result = simulate(argv, self.empty_status)
            if result.returncode != 0:
                self.assertNotEqual(index, 0, f"pleb's required apt set does not resolve:\n{result.stderr}")
                continue
            self.assertEqual(parse_removals(result.stdout), 0)
            closure.update(closure_of(result.stdout))
        self._closures[key] = closure
        return closure

    def image(self, extra: list[str] | None = None) -> "Image":
        key = tuple(extra or ())
        if key not in self._images:
            self._images[key] = Image(self.fresh_closure(extra))
        return self._images[key]

    def test_the_removal_count_parser_reads_apt_and_refuses_silence(self):
        self.assertEqual(parse_removals("0 upgraded, 2 newly installed, 2 to remove and 0 not upgraded.\n"), 2)
        self.assertEqual(parse_removals("0 upgraded, 0 newly installed, 0 to remove and 3 not upgraded.\n"), 0)
        with self.assertRaises(AssertionError):
            parse_removals("E: Unable to locate package nothing\n")

    def test_ensure_system_deps_hands_apt_a_real_transaction(self):
        # Absent is not invisible: an empty recording would make every arm
        # below vacuous.
        self.assertGreaterEqual(len(self.transactions), 1)
        self.assertGreater(len([a for a in self.transactions[0] if not a.startswith("-")]), 10)

    def test_installing_pleb_deps_here_removes_nothing(self):
        """SR-14: a dependency change is gated on `0 to remove`, never on a name."""
        for argv in self.transactions:
            result = simulate(argv, None)
            if result.returncode != 0:
                self.assertIsNot(argv, self.transactions[0], result.stderr)
                continue
            removed = [line for line in result.stdout.splitlines() if line.startswith("Remv ")]
            self.assertEqual(parse_removals(result.stdout), 0,
                             "pleb's apt request would remove packages on this machine:\n"
                             + "\n".join(removed))

    def test_a_fresh_install_gives_kilix_amp_its_library_and_a_soundfont(self):
        image = self.image()
        for what, found in (
            (f"the synth library ({SYNTH_LIBRARY.pattern})", lambda f: SYNTH_LIBRARY.search(f)),
            ("a General MIDI SoundFont", lambda f: SOUNDFONT.match(f)),
        ):
            with self.subTest(what):
                if any(found(f) for f in image.files):
                    continue
                # Absent or invisible? Only a machine that has such a file
                # installed can tell which package ships it; one that has none
                # cannot judge an unreadable closure member either way.
                if image.unreadable and not installed_file_matching(found):
                    self.skipTest(f"cannot tell here whether {image.unreadable} ship {what}")
                self.fail(f"a fresh pleb install leaves Kilix Amp without {what} "
                          f"(unreadable here: {image.unreadable})")

    def test_a_fresh_install_leaves_no_login_unit_holding_the_sound_card(self):
        image = self.image()
        self.assertGreater(len(image.closure), 0)
        self.assertGreater(len(image.enabled_services()), 0, "the model saw no enabled unit at all")
        control = self.defect_control()
        clients, _servers = image.card_holders()
        self.assertEqual(
            clients, [],
            "pleb's install would enable a login unit that holds the default sound card; "
            f"closure {len(image.closure)} packages, unreadable here: {image.unreadable}; "
            f"the control saw {control}",
        )

    def test_a_fresh_install_ships_no_standalone_synthesiser(self):
        image = self.image()
        self.defect_control()
        self.assertEqual(image.synthesiser_programs(), [],
                         "pleb's install ships a standalone synthesiser program")

    def test_the_model_sees_the_defect_when_it_is_present(self):
        self.assertTrue(self.defect_control())

    def defect_control(self) -> list[str]:
        """Positive control: the model must see the defect when it is present.

        The known shape is a development package that hard-depends on the
        player. Resolved on top of pleb's own request, the model must report a
        client holding the card and a synthesiser program; if it cannot (the
        player is not installed on this machine, so its unit is unreadable),
        the unit arms skip rather than pass blind.
        """
        defect = "libfluidsynth-dev"
        image = self.image([defect])
        clients, servers = image.card_holders()
        if not clients:
            unreadable_new = sorted(set(image.unreadable) - set(self.image().unreadable))
            if unreadable_new:
                self.skipTest(f"cannot model the defect here: {unreadable_new} not installed on this machine")
            self.fail(f"the model is blind: adding {defect} enabled no card-holding unit "
                      f"(servers seen: {servers})")
        self.assertTrue(image.synthesiser_programs(), "the model cannot see a synthesiser program")
        self.assertTrue(servers, "the model did not recognise the sound server as holding the card")
        return clients


if __name__ == "__main__":
    unittest.main()
