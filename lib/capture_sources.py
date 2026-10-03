"""Physical X11 capture sources. Client-provided window IDs are never grants."""
from dataclasses import asdict, dataclass
import os
import re


class CaptureError(RuntimeError):
    pass


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

    def validate(self):
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


def enumerate_sources(types=3, env=None):
    """Enumerate actual outputs and mapped EWMH application windows."""
    from displays import RandR
    from Xlib import X, Xatom
    from Xlib.display import Display
    env = physical_environment(env)
    # The backend sets this once before starting its event loop; opening a
    # display must not race a temporary process-global XAUTHORITY assignment.
    display = Display(env["DISPLAY"])
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


def revalidate(source, env=None):
    current = next((item for item in enumerate_sources(source.kind, env) if item.key == source.key), None)
    if current is None or (current.xid, current.x, current.y, current.width, current.height) != (
            source.xid, source.x, source.y, source.width, source.height):
        raise CaptureError("The selected source changed; choose it again")
    return current
