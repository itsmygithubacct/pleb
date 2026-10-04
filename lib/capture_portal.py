#!/usr/bin/python3
"""Consent UI and X11 ScreenCast/Screenshot backend for xdg-desktop-portal."""
import json
import os
from pathlib import Path
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk

from capture_sources import CaptureError, enumerate_sources, physical_environment, revalidate

FRONTEND = "org.freedesktop.portal.Desktop"
BUS_NAME = "org.freedesktop.impl.portal.desktop.pleb"
IMPL = "org.freedesktop.impl.portal."
PROPERTIES = "org.freedesktop.DBus.Properties"
ROOT = "/org/freedesktop/portal/desktop"


class Properties(dbus.service.Object):
    properties = {}

    @dbus.service.method("org.freedesktop.DBus.Introspectable", in_signature="", out_signature="s",
                         path_keyword="object_path", connection_keyword="connection")
    def Introspect(self, object_path, connection):
        node = ET.fromstring(super().Introspect(object_path, connection))
        for interface, properties in self.properties.items():
            item = node.find(f"./interface[@name='{interface}']")
            if item is None:
                item = ET.SubElement(node, "interface", name=interface)
            for name in properties:
                ET.SubElement(item, "property", name=name, type="u", access="read")
        return ET.tostring(node, encoding="unicode")

    @dbus.service.method(PROPERTIES, in_signature="ss", out_signature="v")
    def Get(self, interface, name):
        try:
            return self.properties[str(interface)][str(name)]
        except KeyError:
            raise dbus.exceptions.DBusException("Unknown property", name="org.freedesktop.DBus.Error.InvalidArgs")

    @dbus.service.method(PROPERTIES, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.properties.get(str(interface), {})


class Request(dbus.service.Object):
    def __init__(self, portal, path, success, cancel=None):
        if not str(path).startswith(ROOT + "/request/") or str(path) in portal.requests or len(portal.requests) >= 32:
            raise CaptureError("Invalid or duplicate portal request")
        super().__init__(portal.bus, path)
        self.portal, self.path, self.success = portal, str(path), success
        self.cancel, self.dialog, self.done = cancel, None, False
        self.job = None
        portal.requests[self.path] = self

    @dbus.service.method(IMPL + "Request", in_signature="", out_signature="", sender_keyword="sender")
    def Close(self, sender=None):
        self.portal.authenticate(sender)
        if self.cancel:
            self.cancel()
        self.finish(1)

    def finish(self, response, results=None):
        if self.done:
            return
        self.done = True
        if getattr(self, 'job', None) is not None:
            self.job.close()
            self.job = None
        if self.dialog:
            self.dialog.destroy()
            self.dialog = None
        self.portal.requests.pop(self.path, None)
        self.remove_from_connection()
        try:
            self.success(dbus.UInt32(response), dbus.Dictionary(results or {}, signature="sv"))
        except dbus.exceptions.DBusException:
            # A disappeared frontend cannot receive a reply. Cleanup still
            # must reach every other request, session, and owned producer.
            pass


class Producer:
    """Startup and shutdown never block the D-Bus or consent event loop."""
    def __init__(self, source, cursor, ready, failed):
        self.ready, self.failed, self.closed, self.node = ready, failed, False, None
        self.buffer = b""
        self.deadline = time.monotonic() + 10
        self.process = subprocess.Popen(
            ["/usr/bin/python3", str(Path(__file__).with_name("capture_worker.py")),
             json.dumps(source.payload()), str(cursor)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, env=physical_environment())
        os.set_blocking(self.process.stdout.fileno(), False)
        self.timer = GLib.timeout_add(100, self.poll)

    def poll(self):
        if self.closed:
            return False
        try:
            chunk = os.read(self.process.stdout.fileno(), 8192)
            self.buffer += chunk
            if len(self.buffer) > 8192:
                raise CaptureError("Invalid capture worker response")
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                data = json.loads(line)
                if self.node is not None:
                    raise CaptureError('The selected capture source stopped')
                if set(data) != {"node", "serial"} or any(type(v) is not int or v <= 0 for v in data.values()):
                    raise CaptureError("Invalid PipeWire source identity")
                self.node = data
                self.ready(self)
        except BlockingIOError:
            pass
        except Exception:
            self.failed()
            return False
        if self.process.poll() is not None or (self.node is None and time.monotonic() >= self.deadline):
            self.failed()
            return False
        return not self.closed

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            self.process.terminate()
        deadline = time.monotonic() + 2
        def reap():
            if self.process.poll() is None:
                if time.monotonic() >= deadline:
                    self.process.kill()
                return True
            self.process.stdout.close()
            return False
        GLib.timeout_add(50, reap)


class ScreenshotJob:
    """A frozen private display cannot block consent, cancellation or sharing."""
    def __init__(self, source, request):
        self.source, self.request, self.closed = revalidate(source), request, False
        self.buffer = bytearray()
        self.deadline = time.monotonic() + 8
        self.process = subprocess.Popen(
            ['/usr/bin/python3', str(Path(__file__).with_name('capture_screenshot.py')),
             json.dumps(self.source.payload())], env=physical_environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        request.job = self
        os.set_blocking(self.process.stdout.fileno(), False)
        self.timer = GLib.timeout_add(100, self.poll)

    def poll(self):
        if self.closed:
            return False
        try:
            # Bound work per event-loop turn as well as the complete image.
            eof = False
            for _ in range(16):
                try:
                    chunk = os.read(self.process.stdout.fileno(), 65536)
                except BlockingIOError:
                    break
                if not chunk:
                    eof = True
                    break
                self.buffer.extend(chunk)
                if len(self.buffer) > 67108864:
                    raise CaptureError('Screenshot exceeds the capture limit')
            status = self.process.poll()
            if status is not None and eof:
                if (status or len(self.buffer) < 24 or self.buffer[:8] != b'\x89PNG\r\n\x1a\n'
                        or self.buffer[12:16] != b'IHDR'
                        or struct.unpack('!II', self.buffer[16:24]) != (self.source.width,self.source.height)):
                    raise CaptureError('The selected application screenshot failed')
                revalidate(self.source)
                self.request.finish(0, {'uri': dbus.String(write_screenshot(self.buffer))})
                return False
            if time.monotonic() >= self.deadline:
                raise CaptureError('The selected application pane stopped responding')
        except Exception:
            self.request.finish(2)
            return False
        return True

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            self.process.terminate()
        deadline = time.monotonic() + 2
        def reap():
            if self.process.poll() is None:
                if time.monotonic() >= deadline:
                    self.process.kill()
                return True
            self.process.stdout.close()
            return False
        GLib.timeout_add(50, reap)


class Session(Properties):
    properties = {IMPL + "Session": {"version": dbus.UInt32(1)}}

    def __init__(self, portal, path, app):
        super().__init__(portal.bus, path)
        self.portal, self.path, self.app = portal, str(path), str(app)
        self.types, self.multiple, self.cursor = 1, False, 1
        self.selected, self.started, self.closed = False, False, False
        self.producers, self.sources, self.pending, self.indicator = [], [], None, None

    @dbus.service.method(IMPL + "Session", in_signature="", out_signature="", sender_keyword="sender")
    def Close(self, sender=None):
        self.portal.authenticate(sender)
        self.close()
        self.retire()

    @dbus.service.signal(IMPL + "Session", signature="")
    def Closed(self):
        pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        for producer in self.producers:
            producer.close()
        if self.pending:
            self.pending.finish(1)
            self.pending = None
        if self.indicator:
            self.indicator.destroy()
            self.indicator = None
        try:
            self.Closed()
        except dbus.exceptions.DBusException:
            pass
        self.portal.sessions.pop(self.path, None)
        # The frontend acknowledges implementation-initiated Closed by calling
        # Close itself. Keep the object briefly so that call can succeed.
        GLib.timeout_add_seconds(2, self.retire)

    def retire(self):
        try:
            self.remove_from_connection()
        except LookupError:
            pass
        return False

    def ready(self, _producer):
        if self.closed or not all(producer.node for producer in self.producers):
            return
        streams = []
        for source, producer in zip(self.sources, self.producers):
            props = {"size": dbus.Struct((dbus.Int32(source.width), dbus.Int32(source.height)), signature="ii"),
                     "source_type": dbus.UInt32(source.kind), "pipewire-serial": dbus.UInt64(producer.node["serial"])}
            if source.kind == 1:
                props["position"] = dbus.Struct((dbus.Int32(source.x), dbus.Int32(source.y)), signature="ii")
            streams.append(dbus.Struct((dbus.UInt32(producer.node["node"]), dbus.Dictionary(props, signature="sv")), signature="ua{sv}"))
        self.indicator = Gtk.Window(title="Pleb screen sharing")
        self.indicator.set_wmclass("pleb-capture", "Pleb-capture")
        self.indicator.set_keep_above(True)
        self.indicator.set_default_size(380, 100)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(12)
        text = Gtk.Label(label="Sharing: " + ", ".join(source.label for source in self.sources))
        text.set_line_wrap(True)
        box.pack_start(text, True, True, 0)
        stop = Gtk.Button.new_with_mnemonic("_Stop sharing")
        stop.connect("clicked", lambda _button: self.close())
        box.pack_start(stop, False, False, 0)
        self.indicator.add(box)
        self.indicator.connect("delete-event", lambda *_args: (self.close(), True)[1])
        self.indicator.show_all()
        self.pending.finish(0, {"streams": dbus.Array(streams, signature="(ua{sv})"),
                                "persist_mode": dbus.UInt32(0)})
        self.pending = None

    def start(self, sources, request):
        self.sources, self.pending, self.started = sources, request, True
        try:
            for source in sources:
                revalidate(source)
                self.producers.append(Producer(source, self.cursor, self.ready, self.fail))
        except Exception:
            self.fail()

    def fail(self):
        if self.pending:
            self.pending.finish(2)
            self.pending = None
        self.close()


def source_picker(request, app, types, multiple, action, accept):
    sources = enumerate_sources(types)
    if not sources:
        raise CaptureError("No usable capture source is available")
    dialog = Gtk.Dialog(title="Choose what to " + action, modal=True)
    dialog.set_wmclass("pleb-capture", "Pleb-capture")
    dialog.set_default_size(560, 400)
    dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("_Share" if action == "share" else "_Take screenshot", Gtk.ResponseType.OK)
    dialog.set_response_sensitive(Gtk.ResponseType.OK, False)
    request.dialog = dialog
    area = dialog.get_content_area()
    area.set_border_width(12)
    name = " ".join(str(app).split())[:200] or "An application"
    label = Gtk.Label(label=name + " requests access. Select a source to continue.")
    label.set_line_wrap(True)
    area.pack_start(label, False, False, 8)
    model = Gtk.ListStore(str, str, int)
    for index, source in enumerate(sources):
        prefix = "Application pane: " if source.pane else "Display: " if source.kind == 1 else "Window: "
        model.append([prefix + source.label,
                      f"{source.width} × {source.height}", index])
    tree = Gtk.TreeView(model=model)
    tree.get_accessible().set_name("Capture sources")
    for index, name in enumerate(("Source", "Size")):
        tree.append_column(Gtk.TreeViewColumn(name, Gtk.CellRendererText(), text=index))
    selection = tree.get_selection()
    selection.set_mode(Gtk.SelectionMode.MULTIPLE if multiple else Gtk.SelectionMode.SINGLE)
    selection.connect("changed", lambda selected: dialog.set_response_sensitive(
        Gtk.ResponseType.OK, 1 <= selected.count_selected_rows() <= 16))
    scroll = Gtk.ScrolledWindow()
    scroll.add(tree)
    area.pack_start(scroll, True, True, 8)
    def response(_dialog, code):
        if request.done:
            return
        if code != Gtk.ResponseType.OK:
            if request.cancel:
                request.cancel()
            request.finish(1)
            return
        _model, paths = selection.get_selected_rows()
        if not 1 <= len(paths) <= (16 if multiple else 1):
            return
        chosen = [sources[model[path][2]] for path in paths]
        dialog.destroy()
        request.dialog = None
        # Let the physical X server remove the consent window before capture.
        GLib.timeout_add(150, lambda: (accept(chosen), False)[1])
    dialog.connect("response", response)
    dialog.show_all()
    tree.grab_focus()


def screenshot(source):
    source = revalidate(source)
    if source.pane:
        raise CaptureError('Application screenshots use an owned capture job')
    display = Gdk.Display.get_default()
    window = GdkX11.X11Window.foreign_new_for_display(display, source.xid) if source.kind == 2 else Gdk.get_default_root_window()
    image = Gdk.pixbuf_get_from_window(window, 0 if source.kind == 2 else source.x,
                                     0 if source.kind == 2 else source.y, source.width, source.height)
    if image is None:
        raise CaptureError("Could not capture the selected source")
    success, contents = image.save_to_bufferv("png", [], [])
    if not success:
        raise CaptureError("Could not encode the screenshot")
    return write_screenshot(contents)


def write_screenshot(contents):
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", ""))
    info = runtime.lstat()
    if not runtime.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise CaptureError("A private session runtime directory is required")
    directory = runtime / "pleb-capture"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise CaptureError("Unsafe screenshot directory")
    fd, path = tempfile.mkstemp(prefix="screenshot-", suffix=".png", dir=directory)
    try:
        with os.fdopen(fd, "wb") as output:
            fd = -1
            output.write(contents)
        return Path(path).as_uri()
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise
    finally:
        if fd != -1:
            os.close(fd)


class Portal(Properties):
    properties = {
        IMPL + "ScreenCast": {"version": dbus.UInt32(6), "AvailableSourceTypes": dbus.UInt32(3),
                              "AvailableCursorModes": dbus.UInt32(3)},
        IMPL + "Screenshot": {"version": dbus.UInt32(2)},
    }

    def __init__(self, bus):
        self.bus = bus
        self.name = dbus.service.BusName(BUS_NAME, bus=bus, do_not_queue=True)
        super().__init__(bus, ROOT)
        self.sessions, self.requests = {}, {}
        bus.add_signal_receiver(self.owner_changed, signal_name="NameOwnerChanged",
                                dbus_interface="org.freedesktop.DBus", arg0=FRONTEND)
        bus.call_on_disconnection(lambda _connection: (self.close(), Gtk.main_quit()))

    def authenticate(self, sender):
        if not sender or sender != self.bus.get_name_owner(FRONTEND):
            raise dbus.exceptions.DBusException("Use xdg-desktop-portal", name="org.freedesktop.DBus.Error.AccessDenied")

    def owner_changed(self, _name, old, new):
        if old and old != new:
            self.close()

    def close(self):
        for session in list(self.sessions.values()):
            session.close()
        for request in list(self.requests.values()):
            request.finish(1)

    def session(self, path, app):
        session = self.sessions.get(str(path))
        if session is None or session.closed or session.app != str(app):
            raise CaptureError("Unknown capture session")
        return session

    @dbus.service.method(IMPL + "ScreenCast", in_signature="oosa{sv}", out_signature="ua{sv}", sender_keyword="sender")
    def CreateSession(self, handle, session_handle, app_id, options, sender=None):
        self.authenticate(sender)
        if not str(session_handle).startswith(ROOT + "/session/") or str(session_handle) in self.sessions or len(self.sessions) >= 16:
            return 2, {}
        self.sessions[str(session_handle)] = Session(self, session_handle, app_id)
        return 0, {}

    @dbus.service.method(IMPL + "ScreenCast", in_signature="oosa{sv}", out_signature="ua{sv}", sender_keyword="sender")
    def SelectSources(self, handle, session_handle, app_id, options, sender=None):
        self.authenticate(sender)
        try:
            session = self.session(session_handle, app_id)
            if session.selected or session.started:
                raise CaptureError("Capture sources have already been selected")
            types, cursor = int(options.get("types", 1)), int(options.get("cursor_mode", 1))
            if not types & 3 or types & ~3 or cursor not in (1, 2):
                raise CaptureError("Unsupported capture options")
            session.types, session.cursor = types, cursor
            session.multiple, session.selected = bool(options.get("multiple", False)), True
            # Always ask again. No stored token can silently grant capture.
            return 0, {}
        except CaptureError:
            if str(session_handle) in self.sessions:
                self.sessions[str(session_handle)].close()
            return 2, {}

    @dbus.service.method(IMPL + "ScreenCast", in_signature="oossa{sv}", out_signature="ua{sv}",
                         async_callbacks=("success", "failure"), sender_keyword="sender")
    def Start(self, handle, session_handle, app_id, parent_window, options, success, failure, sender=None):
        self.authenticate(sender)
        request = None
        try:
            session = self.session(session_handle, app_id)
            if not session.selected or session.started or session.pending:
                raise CaptureError("Capture session is not ready")
            request = Request(self, handle, success, session.close)
            session.pending = request
            source_picker(request, app_id, session.types, session.multiple, "share",
                          lambda sources: None if request.done else session.start(sources, request))
        except Exception:
            if str(session_handle) in self.sessions:
                self.sessions[str(session_handle)].close()
            if request:
                request.finish(2)
            else:
                success(dbus.UInt32(2), dbus.Dictionary({}, signature="sv"))

    @dbus.service.method(IMPL + "Screenshot", in_signature="ossa{sv}", out_signature="ua{sv}",
                         async_callbacks=("success", "failure"), sender_keyword="sender")
    def Screenshot(self, handle, app_id, parent_window, options, success, failure, sender=None):
        self.authenticate(sender)
        request = None
        try:
            request = Request(self, handle, success)
            def accept(sources):
                if request.done:
                    return
                try:
                    if sources[0].pane:
                        ScreenshotJob(sources[0], request)
                    else:
                        request.finish(0, {"uri": dbus.String(screenshot(sources[0]))})
                except Exception:
                    request.finish(2)
            source_picker(request, app_id, 3, False, "capture", accept)
        except Exception:
            if request:
                request.finish(2)
            else:
                success(dbus.UInt32(2), dbus.Dictionary({}, signature="sv"))

    @dbus.service.method(IMPL + "Screenshot", in_signature="ossa{sv}", out_signature="ua{sv}",
                         async_callbacks=("success", "failure"), sender_keyword="sender")
    def PickColor(self, handle, app_id, parent_window, options, success, failure, sender=None):
        self.authenticate(sender)
        # The chooser asks first; the pixel is read only after the user clicks
        # the desktop. Escape and Request.Close both abandon the operation.
        request = Request(self, handle, success)
        dialog = Gtk.Dialog(title="Pick a desktop color", modal=True)
        dialog.set_wmclass("pleb-capture", "Pleb-capture")
        dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("_Pick pixel", Gtk.ResponseType.OK)
        dialog.get_content_area().add(Gtk.Label(label="Choose pixel, then click a point on the desktop. Escape cancels."))
        request.dialog = dialog
        def response(_dialog, code):
            if code != Gtk.ResponseType.OK:
                request.finish(1)
                return
            dialog.destroy()
            request.dialog = None
            GLib.timeout_add(150, lambda: (pixel_picker(request), False)[1])
        dialog.connect("response", response)
        dialog.show_all()


def pixel_picker(request):
    if request.done:
        return
    gi.require_foreign("cairo")
    root = Gdk.get_default_root_window()
    image = Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())
    if image is None:
        request.finish(2)
        return
    window = Gtk.Window(title="Choose a desktop pixel")
    window.set_wmclass("pleb-capture", "Pleb-capture")
    window.set_decorated(False)
    window.set_skip_taskbar_hint(True)
    window.set_keep_above(True)
    window.move(0, 0)
    window.set_default_size(image.get_width(), image.get_height())
    canvas = Gtk.DrawingArea()
    canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)
    canvas.set_can_focus(True)
    position = [0, 0]
    def draw(_canvas, context):
        Gdk.cairo_set_source_pixbuf(context, image, 0, 0)
        context.paint()
        for color, width in ((0, 3), (1, 1)):
            context.set_source_rgb(color, color, color)
            context.set_line_width(width)
            context.move_to(position[0] - 8, position[1])
            context.line_to(position[0] + 8, position[1])
            context.move_to(position[0], position[1] - 8)
            context.line_to(position[0], position[1] + 8)
            context.stroke()
    def choose(x, y):
        x, y = int(x), int(y)
        if not 0 <= x < image.get_width() or not 0 <= y < image.get_height():
            return
        offset = y * image.get_rowstride() + x * image.get_n_channels()
        channels = image.get_pixels()[offset:offset + 3]
        request.finish(0, {"color": dbus.Struct(tuple(dbus.Double(channel / 255) for channel in channels), signature="ddd")})
    def click(_canvas, event):
        if event.button == 1:
            choose(event.x, event.y)
        else:
            request.finish(1)
        return True
    def key(_canvas, event):
        if event.keyval == Gdk.KEY_Escape:
            request.finish(1)
        elif event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            choose(*position)
        elif event.keyval in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
            axis = 0 if event.keyval in (Gdk.KEY_Left, Gdk.KEY_Right) else 1
            step = -1 if event.keyval in (Gdk.KEY_Left, Gdk.KEY_Up) else 1
            bound = image.get_width() if axis == 0 else image.get_height()
            position[axis] = max(0, min(bound - 1, position[axis] + step))
            canvas.get_accessible().set_name(f"Choose pixel at {position[0]}, {position[1]}")
            canvas.queue_draw()
        return True
    canvas.connect("draw", draw)
    canvas.connect("button-press-event", click)
    canvas.connect("key-press-event", key)
    window.connect("key-press-event", key)
    window.add(canvas)
    request.dialog = window
    window.connect("destroy", lambda *_args: Gdk.Display.get_default().get_default_seat().ungrab())
    window.show_all()
    window.present()
    canvas.grab_focus()
    deadline = time.monotonic() + 2
    def grab():
        if request.done:
            return False
        status = Gdk.Display.get_default().get_default_seat().grab(
            window.get_window(), Gdk.SeatCapabilities.ALL_POINTING | Gdk.SeatCapabilities.KEYBOARD,
            False, Gdk.Cursor.new_from_name(Gdk.Display.get_default(), "crosshair"), None, None, None)
        if status == Gdk.GrabStatus.SUCCESS:
            window.get_window().focus(Gdk.CURRENT_TIME)
            return False
        if time.monotonic() >= deadline:
            request.finish(2)
            return False
        return True
    # Wait for the window manager to map/reparent the overlay. Grabbing in
    # show_all's call stack races that work and returns NOT_VIEWABLE.
    GLib.timeout_add(40, grab)


def main():
    if os.getuid() == 0:
        raise CaptureError("Run the capture portal as the desktop user")
    os.environ.update(physical_environment())
    os.umask(0o077)
    DBusGMainLoop(set_as_default=True)
    Gtk.init([])
    portal = Portal(dbus.SessionBus())
    def stop():
        portal.close()
        Gtk.main_quit()
        return False
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    Gtk.main()
    portal.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"pleb capture portal: {error}", file=sys.stderr)
        sys.exit(1)
