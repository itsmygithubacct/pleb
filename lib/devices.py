"""Desktop device controls, rendered in a pane and connected to host services."""
import argparse
import os
import shutil
import sys

TOOLS = {
    "power": ("xfce4-power-manager-settings",),
    "bluetooth": ("blueman-manager",),
    "storage": ("gnome-disks",),
    "printers": ("system-config-printer",),
    "audio": ("pavucontrol",),
    "input-method": ("ibus-setup",),
    "accessibility": ("orca", "--setup"),
}


def command(tool, native=False):
    argv = list(TOOLS[tool])
    executable = shutil.which(argv[0])
    if not executable:
        raise FileNotFoundError(f"{argv[0]} is missing; run pleb install")
    argv[0] = executable
    bus = os.environ.get("PLEB_DESKTOP_BUS_ADDRESS") or os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if os.environ.get("KILIX_PRIVATE_XAPP") == "1" and not os.environ.get("PLEB_DESKTOP_BUS_ADDRESS"):
        raise RuntimeError("The physical desktop session bus is unavailable")
    if not bus:
        raise RuntimeError("A desktop session bus is required")
    # The app runner creates a bus per app to isolate singleton applications.
    # These controls manage host services, so restore the desktop bus inside
    # that wrapper while keeping its private DISPLAY and X authentication.
    assignments = [f"DBUS_SESSION_BUS_ADDRESS={bus}"]
    if os.environ.get("IBUS_ADDRESS"):
        assignments.append(f"IBUS_ADDRESS={os.environ['IBUS_ADDRESS']}")
    app = [shutil.which("env") or "/usr/bin/env", *assignments, *argv]
    if native:
        return app
    kilix = shutil.which("kilix")
    if not kilix:
        raise FileNotFoundError("kilix is missing; run pleb install")
    return [kilix, "run", "--size", "1000x720", *app]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manage desktop devices and preferences")
    parser.add_argument("tool", choices=TOOLS)
    parser.add_argument("--native", action="store_true", help="open a native window on the current display")
    args = parser.parse_args(argv)
    try:
        if os.getuid() == 0:
            raise RuntimeError("Run as the desktop user, without sudo")
        target = command(args.tool, args.native)
        os.execv(target[0], target)
    except (OSError, RuntimeError) as error:
        print(f"pleb devices: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
