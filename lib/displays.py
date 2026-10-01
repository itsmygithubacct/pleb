#!/usr/bin/env python3
"""Pleb's X11 display layouts. No root privileges or shell command strings."""
from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time


class DisplayError(Exception):
    pass


def parse_query(text):
    """Parse verbose RandR output, retaining only ordinary unscaled CRTCs."""
    outputs = []
    current = mode = None
    edid = []
    reading_edid = False
    transforms = []
    maximum = [0, 0]
    for line in text.splitlines():
        match = re.search(r"maximum (\d+) x (\d+)", line)
        if match:
            maximum = list(map(int, match.groups()))
        match = re.match(r"^(\S+) (connected|disconnected)\b(.*)", line)
        if match:
            if current:
                current["edid"] = "".join(edid)
            edid, transforms = [], []
            reading_edid = False
            mode = None
            current = None
            if match[2] == "disconnected":
                continue
            rest = match[3]
            geometry = re.search(r" (\d+)x(\d+)\+(-?\d+)\+(-?\d+)", rest)
            rotation = re.search(r"\) (normal|left|right|inverted)\b", rest)
            current = dict(name=match[1], primary=" primary " in rest + " ",
                           enabled=bool(geometry), x=int(geometry[3]) if geometry else 0,
                           y=int(geometry[4]) if geometry else 0,
                           rotation=rotation[1] if rotation else "normal",
                           mode=None, rate=None, modes={}, safe=True, edid="")
            if geometry and re.search(r"\) (?:normal|left|right|inverted).*?\b[xy] axis\b", rest.split("(normal")[0]):
                current["safe"] = False
            outputs.append(current)
            continue
        if current is None:
            continue
        if line.strip() == "EDID:":
            reading_edid = True
            continue
        if reading_edid:
            if re.fullmatch(r"\s+[0-9a-fA-F]{32}", line):
                edid.append(line.strip().lower())
                continue
            reading_edid = False
        if "Transform:" in line:
            transforms = line.split("Transform:", 1)[1].split()
        elif transforms and len(transforms) < 9:
            transforms += line.split()
        if len(transforms) == 9:
            if transforms != ["1.000000", "0.000000", "0.000000", "0.000000", "1.000000", "0.000000", "0.000000", "0.000000", "1.000000"]:
                current["safe"] = False
            transforms = []
        if re.search(r"(?:Panning|panning):", line) and not re.search(r"\b0x0\b", line):
            current["safe"] = False
        match = re.match(r"^  (\S+) \(0x[0-9a-f]+\) .+", line)
        if match:
            mode = (match[1], "*current" in line)
            current["modes"].setdefault(mode[0], [])
        elif mode and re.match(r"\s+v:", line):
            rate = re.search(r"clock\s+([0-9.]+)Hz", line)
            if rate:
                current["modes"][mode[0]].append(rate[1])
                if mode[1]:
                    current["mode"], current["rate"] = mode[0], rate[1]
            mode = None
    if current:
        current["edid"] = "".join(edid)
    if not outputs or not all(maximum):
        raise DisplayError("No connected RandR outputs or unrecognized server response")
    for output in outputs:
        output["identity"] = hashlib.sha256(bytes.fromhex(output.pop("edid"))).hexdigest()
        if output["enabled"] and output["mode"] is None:
            raise DisplayError("Cannot read the current mode for " + output["name"])
    return {"outputs": outputs, "maximum": maximum}


class RandR:
    def __init__(self, env=None):
        self.env = dict(os.environ if env is None else env)

    def run(self, args):
        result = subprocess.run(["xrandr", *args], capture_output=True, text=True,
                                timeout=8, env={**self.env, "LC_ALL": "C"})
        if result.returncode:
            raise DisplayError(result.stderr.strip() or "xrandr failed")
        return result.stdout

    def query(self):
        return parse_query(self.run(["--verbose"]))

    def apply(self, layout):
        # --noprimary is GLOBAL, unlike the per-output --primary switch.
        # Emitting it for a secondary output clears the selected primary.
        args = [] if any(o["enabled"] and o["primary"] for o in layout["outputs"]) else ["--noprimary"]
        for output in layout["outputs"]:
            args += ["--output", output["name"]]
            if not output["enabled"]:
                args += ["--off"]
            else:
                args += ["--mode", output["mode"], "--rate", output["rate"],
                         "--pos", f'{output["x"]}x{output["y"]}',
                         "--rotate", output["rotation"]]
                if output["primary"]:
                    args += ["--primary"]
        self.run(args)


FIELDS = ("name", "identity", "enabled", "primary", "mode", "rate", "x", "y", "rotation")


def snapshot(state):
    return {"version": 1, "outputs": [{key: o[key] for key in FIELDS} for o in state["outputs"]]}


def topology(layout):
    # Connector is deliberately part of the identity: no guessing when two
    # monitors have identical/missing EDIDs or the cabling has changed.
    return sorted((o["name"], o["identity"]) for o in layout["outputs"])


def validate(layout, state):
    if not isinstance(layout, dict) or layout.get("version") != 1:
        raise DisplayError("Unsupported layout format")
    outputs = layout.get("outputs")
    if not isinstance(outputs, list) or not outputs or any(not isinstance(o, dict) or set(o) != set(FIELDS) for o in outputs):
        raise DisplayError("Invalid output records")
    if topology(layout) != topology(state):
        raise DisplayError("Connected monitors differ from the saved layout")
    actual = {o["name"]: o for o in state["outputs"]}
    if len({o["name"] for o in outputs}) != len(outputs):
        raise DisplayError("Duplicate output")
    active = []
    for o in outputs:
        if type(o["enabled"]) is not bool or type(o["primary"]) is not bool:
            raise DisplayError("Output flags must be booleans")
        if not actual[o["name"]]["safe"]:
            raise DisplayError("Scaling, reflection or panning is active; this layout cannot be safely previewed")
        if not o["enabled"]:
            if o["primary"]:
                raise DisplayError("A disabled output cannot be primary")
            continue
        active.append(o)
        if o["rotation"] not in ("normal", "left", "right", "inverted"):
            raise DisplayError("Invalid rotation")
        if not isinstance(o["mode"], str) or not re.fullmatch(r"[0-9]+x[0-9]+", o["mode"]):
            raise DisplayError("Only standard width-by-height modes are supported")
        if o["rate"] not in actual[o["name"]]["modes"].get(o["mode"], []):
            raise DisplayError("Unsupported mode/rate for " + o["name"])
        if any(type(o[k]) is not int or o[k] < 0 for k in ("x", "y")):
            raise DisplayError("Positions must be nonnegative integers")
        width, height = map(int, o["mode"].split("x"))
        if o["rotation"] in ("left", "right"):
            width, height = height, width
        if o["x"] + width > state["maximum"][0] or o["y"] + height > state["maximum"][1]:
            raise DisplayError("Layout exceeds the server framebuffer limit")
    if not active or sum(o["primary"] for o in active) != 1:
        raise DisplayError("Select at least one enabled output and exactly one primary")


def matches(layout, state):
    if topology(layout) != topology(state):
        return False
    actual = {o["name"]: o for o in state["outputs"]}
    return all(all(o[k] == actual[o["name"]][k] for k in
                   (("enabled", "primary", "mode", "rate", "x", "y", "rotation") if o["enabled"] else ("enabled",)))
               for o in layout["outputs"])


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    stat = path.lstat()
    if path.is_symlink() or not path.is_dir() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise DisplayError(f"Display storage must be an owned private directory: {path}")
    return path


def paths():
    base = Path(os.environ.get("PLEB_STORAGE_HOME", str(Path.home() / ".local/gpu_terminal/pleb")))
    config = Path(os.environ.get("PLEB_CONFIG_HOME", str(base / "config"))) / "displays"
    state = Path(os.environ.get("PLEB_STATE_HOME", str(base / "state"))) / "displays"
    return private_dir(config), private_dir(state)


@contextlib.contextmanager
def locked(state, display=None):
    # :0 and :0.0 refer to the same X server; remote servers are excluded below.
    display = (display if display is not None else os.environ.get("DISPLAY", "")).split(".")[0]
    name = hashlib.sha256(display.encode()).hexdigest()[:20]
    fd = os.open(state / (name + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError:
        raise DisplayError("Another display operation is running") from None
    finally:
        os.close(fd)


def save(config, layout):
    key = hashlib.sha256(json.dumps(topology(layout)).encode()).hexdigest()
    fd, filename = tempfile.mkstemp(dir=config)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(layout, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, config / (key + ".json"))
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def rollback(backend, before):
    state = backend.query()
    if topology(before) == topology(state):
        backend.apply(before)
    else:
        # A cable can disappear during a preview. Preserve remaining baseline
        # outputs, but ensure there is an active monitor even if it was off.
        old = {o["name"]: o for o in before["outputs"]}
        available = snapshot(state)
        for i, o in enumerate(available["outputs"]):
            previous = old.get(o["name"])
            if previous and previous["identity"] == o["identity"]:
                available["outputs"][i] = copy.deepcopy(previous)
        active = [o for o in available["outputs"] if o["enabled"]]
        if not active:
            backend.run(["--output", state["outputs"][0]["name"], "--auto", "--pos", "0x0", "--primary"])
            return
        min_x, min_y = min(o["x"] for o in active), min(o["y"] for o in active)
        for o in available["outputs"]:
            o["primary"] = o is active[0]
            if o["enabled"]:
                o["x"] -= min_x
                o["y"] -= min_y
        backend.apply(available)


def transaction(channel, backend, layout, config, state, timeout):
    """Worker owns both apply and rollback. EOF from a dead UI also reverts."""
    before = None
    committed = False
    error = None
    try:
        with locked(state, getattr(backend, "env", {}).get("DISPLAY")):
            current = backend.query()
            validate(layout, current)
            before = snapshot(current)
            # Preserve baseline before any RandR request for recovery diagnostics.
            save(state, before)
            try:
                backend.apply(layout)
                if not matches(layout, backend.query()):
                    raise DisplayError("The X server did not apply the requested layout")
                channel.send(b"ready")
                ready, _, _ = select.select([channel], [], [], timeout)
                if ready and channel.recv(32) == b"confirm":
                    if not matches(layout, backend.query()):
                        raise DisplayError("Layout changed before confirmation")
                    save(config, layout)
                    committed = True
            finally:
                if not committed:
                    rollback(backend, before)
    except Exception as exc:
        error = str(exc)
    with contextlib.suppress(OSError):
        channel.send(json.dumps({"confirmed": committed, "error": error}).encode())
    channel.close()


def start_worker(backend, layout, config, state, timeout=20):
    """Exec a fresh interpreter: Tk may already have native helper threads."""
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_worker", str(child.fileno())],
            pass_fds=(child.fileno(),), env=backend.env, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        parent.close()
        child.close()
        raise
    child.close()
    try:
        parent.send(json.dumps({"layout": layout, "config": str(config), "state": str(state), "timeout": timeout}).encode())
    except Exception:
        parent.close()
        process.terminate()
        process.wait(timeout=5)
        raise
    return parent, process


def worker_main(fd):
    channel = socket.socket(fileno=fd)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        request = json.loads(channel.recv(65536))
        if os.getuid() == 0 or not re.fullmatch(r":\d+(?:\.\d+)?", os.environ.get("DISPLAY", "")):
            raise DisplayError("Preview requires the desktop user and a local X display")
        timeout = request["timeout"]
        if type(timeout) is not int or not 5 <= timeout <= 60:
            raise DisplayError("Invalid confirmation timeout")
        transaction(channel, RandR(), request["layout"], private_dir(Path(request["config"])),
                    private_dir(Path(request["state"])), timeout)
    except Exception as exc:
        with contextlib.suppress(OSError):
            channel.send(json.dumps({"confirmed": False, "error": str(exc)}).encode())
        channel.close()


def preview(layout, config, state, timeout=15):
    if not sys.stdin.isatty():
        raise DisplayError("Preview needs an interactive terminal for confirmation")
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    pid = os.fork()
    if pid == 0:
        parent.close()
        os.setsid()
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        transaction(child, RandR(), layout, config, state, timeout)
        os._exit(0)
    child.close()
    try:
        parent.settimeout(timeout + 40)
        message = parent.recv(65536)
        if message == b"ready":
            print(f'Keep this layout? Type yes then Enter within {timeout}s: ', end="", flush=True)
            ready, _, _ = select.select([sys.stdin, parent], [], [], timeout)
            if sys.stdin in ready:
                parent.send(b"confirm" if sys.stdin.readline().strip().lower() == "yes" else b"revert")
            else:
                with contextlib.suppress(OSError):
                    parent.send(b"revert")
            message = parent.recv(65536)
        result = json.loads(message)
        if result["error"]:
            raise DisplayError(result["error"])
        print("\nLayout confirmed and saved." if result["confirmed"] else "\nPrevious layout restored.")
        return 0 if result["confirmed"] else 1
    finally:
        parent.close()
        os.waitpid(pid, 0)


def configure(state):
    layout = snapshot(state)
    active = []
    for o in layout["outputs"]:
        source = next(s for s in state["outputs"] if s["name"] == o["name"])
        print(f'\n{o["name"]}: {o["mode"] or "off"} @ {o["rate"] or "-"} Hz')
        if input("Enable? [Y/n] ").strip().lower() == "n":
            o["enabled"], o["primary"] = False, False
            continue
        choices = [(mode, rate) for mode, rates in source["modes"].items() for rate in rates if re.fullmatch(r"\d+x\d+", mode)]
        for i, (mode, rate) in enumerate(choices, 1):
            print(f"  {i}: {mode} @ {rate} Hz")
        default = next((i for i, pair in enumerate(choices, 1) if pair == (o["mode"], o["rate"])), 1)
        choice = int(input(f"Mode [{default}]: ").strip() or default)
        if not 1 <= choice <= len(choices):
            raise DisplayError("Invalid mode choice")
        o["mode"], o["rate"] = choices[choice - 1]
        o["enabled"], o["primary"] = True, False
        o["rotation"] = input(f'Rotation normal/left/right/inverted [{o["rotation"]}]: ').strip() or o["rotation"]
        o["x"] = int(input(f'Horizontal position [{o["x"]}]: ').strip() or o["x"])
        o["y"] = int(input(f'Vertical position [{o["y"]}]: ').strip() or o["y"])
        active.append(o)
    if not active:
        raise DisplayError("At least one monitor must stay enabled")
    print("\nEnabled: " + ", ".join(o["name"] for o in active))
    primary = input(f'Primary [{active[0]["name"]}]: ').strip() or active[0]["name"]
    for o in active:
        o["primary"] = o["name"] == primary
    validate(layout, state)
    return layout


def restore(backend, config, state):
    with locked(state):
        current = backend.query()
        if any(o["identity"] == hashlib.sha256(b"").hexdigest() for o in current["outputs"]):
            # Connector alone cannot identify a replacement monitor reliably.
            return
        for path in sorted(config.glob("*.json")):
            layout = json.loads(path.read_text())
            if topology(layout) != topology(current):
                continue
            validate(layout, current)
            if matches(layout, current):
                return
            before = snapshot(current)
            try:
                backend.apply(layout)
                if not matches(layout, backend.query()):
                    raise DisplayError("Saved layout was not applied")
            except Exception:
                rollback(backend, before)
                raise
            return


def process_identity(pid):
    try:
        # comm may itself contain spaces and parentheses.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def watch(parent_pid, backend, config, state):
    """Follow one session and one X connection, never reconnect to a new server."""
    from Xlib.display import Display
    identity = process_identity(parent_pid)
    if not identity or not list(config.glob("*.json")):
        return
    connection = Display()
    previous = None
    try:
        while process_identity(parent_pid) == identity:
            # A server reset/disconnect ends this watcher; no stale session
            # process can restore a layout on the next user's X server.
            connection.sync()
            current = topology(backend.query())
            if current != previous:
                try:
                    restore(backend, config, state)
                except DisplayError as exc:
                    print(f"pleb displays watch: {exc}", file=sys.stderr, flush=True)
                previous = current
            time.sleep(2)
    except Exception as exc:
        raise DisplayError(f"Session display watcher stopped: {exc}") from exc
    finally:
        with contextlib.suppress(Exception):
            connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manage Pleb X11 monitors (RC5)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="print detected outputs and available modes as JSON")
    sub.add_parser("snapshot", help="print an editable layout as JSON")
    sub.add_parser("configure", help="choose a layout and preview it with timed rollback")
    sub.add_parser("gui", help="open the graphical arranger inside a Kilix tab")
    p = sub.add_parser("preview", help="preview a layout JSON file; save only on confirmation")
    p.add_argument("layout", type=Path)
    p.add_argument("--timeout", type=int, choices=range(5, 61), default=15, metavar="5..60")
    sub.add_parser("restore", help="restore a confirmed layout only for matching monitors")
    p = sub.add_parser("watch", help="restore confirmed layouts on session start and topology changes")
    p.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r":\d+(?:\.\d+)?", os.environ.get("DISPLAY", "")):
            raise DisplayError("Use a local X11 session (DISPLAY=:N); SSH forwarding and Wayland are unsupported")
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            raise DisplayError("Native Wayland monitor configuration is not supported")
        if os.getuid() == 0:
            raise DisplayError("Run as the desktop user, without sudo")
        backend = RandR()
        if args.command in ("list", "snapshot"):
            current = backend.query()
            print(json.dumps(current if args.command == "list" else snapshot(current), indent=2))
            return 0
        config, state = paths()
        if args.command == "gui":
            os.execvp("kilix", ["kilix", "run", "--size", "1100x740", "python3",
                      str(Path(__file__).with_name("displays_gui.py")),
                      "--target-display", os.environ["DISPLAY"],
                      "--target-authority", os.environ.get("XAUTHORITY", str(Path.home()/".Xauthority")),
                      "--config-dir", str(config), "--state-dir", str(state)])
        if args.command == "watch":
            if args.parent_pid <= 1:
                raise DisplayError("A session process is required")
            watch(args.parent_pid, backend, config, state)
            return 0
        if args.command == "restore":
            restore(backend, config, state)
            return 0
        layout = configure(backend.query()) if args.command == "configure" else json.loads(args.layout.read_text())
        return preview(layout, config, state, getattr(args, "timeout", 15))
    except (DisplayError, OSError, ValueError, KeyError, TypeError, EOFError, ImportError, subprocess.TimeoutExpired) as exc:
        print(f"pleb displays: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled; any unconfirmed preview is reverted by its worker.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "_worker":
        worker_main(int(sys.argv[2]))
    else:
        sys.exit(main())
