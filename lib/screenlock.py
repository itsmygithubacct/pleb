"""Shared per-user automatic screen-lock preferences and terminal settings UI."""
import argparse
import curses
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

POLICIES = {"session": "Follow session defaults", "on": "Enabled", "off": "Disabled"}
TIMING = "Changes apply at your next login."
DETAIL = "Disabled stops automatic locks on idle, display power-off and sleep. Manual locking remains available."


def settings_path():
    if os.environ.get("PLEB_ENV_USER"):
        return Path(os.environ["PLEB_ENV_USER"]).parent / "screen-lock.json"
    if "PLEB_CONFIG_HOME" in os.environ:
        return Path(os.environ["PLEB_CONFIG_HOME"]) / "screen-lock.json"
    storage = Path(os.environ.get("PLEB_STORAGE_HOME", str(
        Path(os.environ.get("GPU_TERMINAL_HOME", str(Path.home() / ".local/gpu_terminal"))) / "pleb")))
    return storage / "config/screen-lock.json"


def validate(data):
    if (not isinstance(data, dict) or set(data) != {"version", "policy", "idle_seconds"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["policy"], str) or data["policy"] not in POLICIES
            or type(data["idle_seconds"]) is not int or not 0 <= data["idle_seconds"] <= 86400):
        raise ValueError("Invalid screen-lock settings; choose a policy and an idle timeout from 0 to 86400 seconds")
    return data


def load():
    path = settings_path()
    if not path.is_absolute():
        raise ValueError("Screen-lock settings require an absolute configuration directory")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {"version": 1, "policy": "session", "idle_seconds": 600}
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 4096:
            raise ValueError("Screen-lock settings must be a small file owned by the desktop user")
        return validate(json.loads(stream.read(4097)))


def save(policy, idle_seconds):
    data = validate({"version": 1, "policy": policy, "idle_seconds": idle_seconds})
    path = settings_path()
    if not path.is_absolute():
        raise ValueError("Screen-lock settings require an absolute configuration directory")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Screen-lock settings cannot be a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".screen-lock-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return data


def tui(screen):
    data = load()
    selected, message = 0, TIMING
    fields = ("Automatic locking", "Idle timeout", "Save settings")
    while True:
        screen.erase()
        height, width = screen.getmaxyx()

        def put(row, text, selected=False):
            if row < height - 1 and width > 2:
                try:
                    screen.addnstr(row, 1, text, width - 2, curses.A_REVERSE if selected else 0)
                except curses.error:
                    pass

        put(0, "KILIX TUI — Screen Lock")
        for row, text in enumerate((DETAIL, "Idle timeout: 0 disables the idle timer; sleep locking follows the policy.", TIMING), 2):
            put(row, text)
        values = (POLICIES[data["policy"]], f'{data["idle_seconds"]} seconds', "Enter to save")
        for index, (label, value) in enumerate(zip(fields, values)):
            put(index + 6, f"{label}: {value}", selected == index)
        put(10, message)
        put(height - 2, "Up/Down select · Left/Right change (timeout: 60s) · Enter save · q quit")
        screen.refresh()
        key = screen.getch()
        if key in (ord("q"), 27):
            return 0
        if key in (curses.KEY_UP, curses.KEY_DOWN):
            selected = (selected + (1 if key == curses.KEY_DOWN else -1)) % 3
        elif key in (curses.KEY_LEFT, curses.KEY_RIGHT):
            step = 1 if key == curses.KEY_RIGHT else -1
            if selected == 0:
                policies = list(POLICIES)
                data["policy"] = policies[(policies.index(data["policy"]) + step) % len(policies)]
            elif selected == 1:
                data["idle_seconds"] = max(0, min(86400, data["idle_seconds"] + step * 60))
            message = "Unsaved changes. Press Enter to save."
        elif key in (10, 13, curses.KEY_ENTER):
            try:
                save(data["policy"], data["idle_seconds"])
                message = "Saved. " + TIMING
            except (OSError, ValueError) as error:
                message = str(error)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Screen-lock settings. " + TIMING)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tui", action="store_true", help="open terminal settings (the default)")
    mode.add_argument("--show", action="store_true", help="print saved settings as JSON")
    mode.add_argument("--session-values", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--set", choices=POLICIES, metavar="session|on|off", help="save the automatic locking policy")
    parser.add_argument("--idle-seconds", type=int, help="idle timeout, 0..86400 seconds; 0 disables the idle timer")
    args = parser.parse_args(argv)
    if args.idle_seconds is not None and args.set is None:
        parser.error("--idle-seconds requires --set")
    try:
        if args.set is not None:
            idle = args.idle_seconds if args.idle_seconds is not None else load()["idle_seconds"]
            save(args.set, idle)
            print("Saved. " + TIMING)
        elif args.show:
            print(json.dumps(load(), sort_keys=True))
        elif args.session_values:
            data = load()
            print(data["policy"], data["idle_seconds"])
        else:
            return curses.wrapper(tui)
        return 0
    except (OSError, ValueError, curses.error) as error:
        print(f"Screen Lock: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
