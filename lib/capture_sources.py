"""Physical X11 capture sources. Client-provided window IDs are never grants."""
from dataclasses import asdict, dataclass
import os
import re
import subprocess

import capture_registry

class CaptureError(RuntimeError):
    pass


def bind_capture_parent():
    import ctypes
    import signal
    if os.getuid() == 0:
        raise CaptureError('Capture runs as the desktop user')
    parent = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent or parent == 1:
        raise CaptureError('Capture parent is unavailable')


def physical_environment(env=None):
    env = dict(os.environ if env is None else env)
    if env.get("KILIX_PRIVATE_XAPP") == "1" and not env.get("PLEB_DESKTOP_DISPLAY"):
        raise CaptureError("The physical desktop display is unavailable")
    display = env.get("PLEB_DESKTOP_DISPLAY") or env.get("DISPLAY", "")
    if not re.fullmatch(r":\d+(?:\.\d+)?", display):
        raise CaptureError("Capture requires a local X11 desktop")
    env["DISPLAY"] = display
    if env.get("PLEB_DESKTOP_XAUTHORITY"):
        env["XAUTHORITY"] = env["PLEB_DESKTOP_XAUTHORITY"]
    if env.get("PLEB_DESKTOP_BUS_ADDRESS"):
        env["DBUS_SESSION_BUS_ADDRESS"] = env["PLEB_DESKTOP_BUS_ADDRESS"]
    return env


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    kind: int
    x: int
    y: int
    width: int
    height: int
    xid: int = 0
    pane: str = ''

    def validate(self):
        if (not isinstance(self.pane, str) or self.pane and (
                not capture_registry.TOKEN.fullmatch(self.pane) or self.kind != 2
                or self.key != 'pane:' + self.pane or self.x != 0 or self.y != 0)):
            raise CaptureError("Invalid application capture source")
        if self.kind not in (1, 2) or type(self.xid) is not int or not 0 <= self.xid <= 0xffffffff:
            raise CaptureError("Invalid capture source")
        if any(type(v) is not int for v in (self.x, self.y, self.width, self.height)):
            raise CaptureError("Invalid capture dimensions")
        if min(self.x, self.y) < 0 or not 1 <= self.width <= 16384 or not 1 <= self.height <= 16384:
            raise CaptureError("Capture source is outside the supported desktop")
        if self.width * self.height > 67108864 or (self.kind == 2 and not self.xid):
            raise CaptureError("Unsupported capture source")
        return self

    def payload(self):
        return asdict(self.validate())


def monitor_sources(state):
    from displays import dimensions
    result = []
    for output in state["outputs"]:
        if not output["enabled"] or not output["safe"]:
            continue
        width, height = dimensions(output)
        result.append(Source("monitor:" + output["name"], output["name"], 1,
                             output["x"], output["y"], width, height).validate())
    return result


def _physical_sources(types=3, env=None):
    """Enumerate actual outputs and mapped EWMH application windows."""
    from displays import RandR
    from Xlib import X, Xatom
    from Xlib.display import Display
    env = physical_environment(env)
    # Only the physical server is queried by the consent event loop. Pane
    # metadata is checked here, while private X queries run in owned helpers.
    display = Display(env['DISPLAY'])
    try:
        root = display.screen().root
        desktop = root.get_geometry()
        sources = monitor_sources(RandR(env).query()) if types & 1 else []
        if types & 2:
            clients = root.get_full_property(display.intern_atom("_NET_CLIENT_LIST"), Xatom.WINDOW)
            for xid in list(clients.value)[:512] if clients is not None else []:
                try:
                    window = display.create_resource_object("window", int(xid))
                    if window.get_attributes().map_state != X.IsViewable:
                        continue
                    if window.get_wm_class() == ("pleb-capture", "Pleb-capture"):
                        continue
                    geometry = window.get_geometry()
                    position = root.translate_coords(window, 0, 0)
                    title = window.get_full_property(display.intern_atom("_NET_WM_NAME"), display.intern_atom("UTF8_STRING"))
                    label = bytes(title.value).decode("utf-8", "replace") if title is not None else window.get_wm_name()
                    label = " ".join(str(label or "Application window").split())[:200]
                    source = Source(f"window:{int(xid)}", label, 2, position.x, position.y,
                                    geometry.width, geometry.height, int(xid)).validate()
                    if source.x + source.width > desktop.width or source.y + source.height > desktop.height:
                        continue
                    sources.append(source)
                except Exception:
                    # A window can disappear between the property and geometry
                    # requests. Never replace it with a wider capture target.
                    continue
        return sources
    finally:
        display.close()


def _pane_source(record):
    return Source('pane:' + record['id'], record['label'], 2, 0, 0,
                  record['width'], record['height'], record['xid'], record['id']).validate()


def enumerate_sources(types=3, env=None):
    env = dict(physical_environment(env))
    sources = _physical_sources(types, env)
    if types & 2:
        for record in capture_registry.records(env):
            try:
                sources.append(_pane_source(record))
            except Exception:
                # A disappearing pane never becomes a physical desktop source.
                continue
    return sources


def source_environment(source, env=None):
    source.validate()
    env = dict(physical_environment(env))
    if source.pane:
        record = capture_registry.read(source.pane, env)
        if record is None:
            raise CaptureError('The selected application pane stopped')
        env['DISPLAY'], env['XAUTHORITY'] = record['display'], record['authority']
    return env


def verify_capture_geometry(source, env):
    if not source.pane:
        return
    try:
        result = subprocess.run(['/usr/bin/xrandr','--current'], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=.5)
        size = re.search(r'^Screen \d+:.*?current (\d+) x (\d+)', result.stdout, re.MULTILINE)
        if result.returncode or size is None or tuple(map(int,size.groups())) != (source.width,source.height):
            raise CaptureError('The selected application pane changed')
    except (OSError, subprocess.SubprocessError):
        raise CaptureError('The selected application pane stopped responding') from None


def revalidate(source, env=None):
    source.validate()
    if source.pane:
        record = capture_registry.read(source.pane, physical_environment(env))
        try:
            current = _pane_source(record) if record is not None else None
        except Exception:
            current = None
    else:
        current = next((item for item in _physical_sources(source.kind, env) if item.key == source.key), None)
    if current is None or (current.xid, current.x, current.y, current.width, current.height) != (
            source.xid, source.x, source.y, source.width, source.height):
        raise CaptureError("The selected source changed; choose it again")
    return current
