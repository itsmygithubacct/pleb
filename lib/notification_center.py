#!/usr/bin/env python3
"""Pleb's single, downward-growing desktop notification window.

Speaks org.freedesktop.Notifications. The icon invokes the notification's
default action; Kilix maps that action back to the originating pane.
"""
import argparse
import os
import signal
import stat
import sys
import time

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

NAME = 'org.freedesktop.Notifications'
PATH = '/org/freedesktop/Notifications'
XML = '''<node><interface name="org.freedesktop.Notifications">
<method name="GetCapabilities"><arg type="as" direction="out"/></method>
<method name="GetServerInformation">
 <arg type="s" direction="out"/><arg type="s" direction="out"/>
 <arg type="s" direction="out"/><arg type="s" direction="out"/>
</method>
<method name="Notify">
 <arg type="s" direction="in"/><arg type="u" direction="in"/>
 <arg type="s" direction="in"/><arg type="s" direction="in"/>
 <arg type="s" direction="in"/><arg type="as" direction="in"/>
 <arg type="a{sv}" direction="in"/><arg type="i" direction="in"/>
 <arg type="u" direction="out"/>
</method>
<method name="CloseNotification"><arg type="u" direction="in"/></method>
<signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
<signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
</interface></node>'''

CSS = b'''
#pleb-notifications { background: #20242c; color: #f3f4f6; }
.notification { padding: 12px; border-bottom: 1px solid #434955; }
.notification-title { font-weight: bold; }
.notification button { color: #f3f4f6; background: transparent;
  border: none; box-shadow: none; padding: 4px; min-height: 0; min-width: 0; }
.notification button:hover { background: #424b5a; }
.notification button:focus { outline: 1px solid #a8c7fa; }
'''


def bus_call(bus, method, arguments, result_type):
    return bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                         'org.freedesktop.DBus', method, arguments,
                         GLib.VariantType.new(result_type),
                         Gio.DBusCallFlags.NONE, 1000, None).unpack()


def load_icon(name, hints):
    """Bound image decoding and never open a FIFO or follow a file link."""
    raw = hints.get('image-data', hints.get('image_data'))
    if isinstance(raw, (tuple, list)) and len(raw) == 7:
        width, height, stride, alpha, bits, channels, data = raw
        if (all(isinstance(v, int) for v in (width, height, stride, bits, channels))
                and isinstance(data, (bytes, list, tuple)) and 0 < width <= 1024 and 0 < height <= 1024 and bits == 8
                and channels == (4 if alpha else 3)
                and width * channels <= stride <= 8192
                and len(data) >= (height - 1) * stride + width * channels
                and len(data) <= 8 * 1024 * 1024):
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(bytes(data)), GdkPixbuf.Colorspace.RGB,
                bool(alpha), bits, width, height, stride)
            return Gtk.Image.new_from_pixbuf(pixbuf.scale_simple(48, 48, GdkPixbuf.InterpType.BILINEAR))
    name = hints.get('image-path', hints.get('image_path', name))
    if not isinstance(name, str):
        name = ''
    if name.startswith('file://'):
        name = Gio.File.new_for_uri(name).get_path() or ''
    if name.startswith('/'):
        fd = None
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode) and info.st_size <= 4 * 1024 * 1024:
                with os.fdopen(fd, 'rb') as stream:
                    fd = None
                    data = stream.read(4 * 1024 * 1024 + 1)
                if len(data) <= 4 * 1024 * 1024:
                    loader = GdkPixbuf.PixbufLoader.new()
                    loader.connect('size-prepared', lambda decoder, w, h: decoder.set_size(48, 48))
                    loader.write(data)
                    loader.close()
                    return Gtk.Image.new_from_pixbuf(loader.get_pixbuf())
        except (OSError, GLib.Error):
            pass
        finally:
            if fd is not None:
                os.close(fd)
        name = ''
    image = Gtk.Image.new_from_icon_name(name or 'dialog-information', Gtk.IconSize.DIALOG)
    image.set_pixel_size(48)
    return image


class NotificationCenter:
    def __init__(self, bus):
        self.bus = bus
        self.items = {}
        self.next_id = 1
        self.layout_source = 0
        self.area = None
        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_name('pleb-notifications')
        self.window.set_title('Pleb notifications')
        self.window.set_wmclass('pleb-notifications', 'PlebNotifications')
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_keep_above(True)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.connect('delete-event', lambda *_: True)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.add(self.box)
        self.window.add(self.scroll)
        style = Gtk.CssProvider()
        style.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), style,
                                                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.registration = bus.register_object(
            PATH, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self.method, None, None)

    def emit(self, item, member, signature, values):
        # Directed signals keep a private-bus relay and each sending terminal
        # bound to their own IDs, including when other apps reuse a client ID.
        self.bus.emit_signal(item['sender'], PATH, NAME, member,
                             GLib.Variant(signature, values))

    def method(self, connection, sender, path, interface, method, arguments, call):
        try:
            if method == 'Notify':
                result = self.notify(sender, *arguments.unpack())
                call.return_value(GLib.Variant('(u)', (result,)))
            elif method == 'CloseNotification':
                number, = arguments.unpack()
                item = self.items.get(number)
                if item is None or item['sender'] != sender:
                    call.return_dbus_error(NAME + '.Error.InvalidId', 'No notification with that ID')
                else:
                    self.close(number, 3)
                    call.return_value(None)
            elif method == 'GetCapabilities':
                call.return_value(GLib.Variant('(as)', (['actions', 'body', 'icon-static', 'persistence'],)))
            elif method == 'GetServerInformation':
                call.return_value(GLib.Variant('(ssss)', ('Pleb notifications', 'Pleb', '1', '1.2')))
        except (ValueError, TypeError, OverflowError, GLib.Error) as error:
            call.return_dbus_error(NAME + '.Error.InvalidArguments', str(error))

    def notify(self, sender, app, replaces, icon, title, body, actions, hints, timeout):
        if len(actions) % 2:
            raise ValueError('Actions must be key/label pairs')
        icon_widget = load_icon(icon, hints)
        existing = self.items.get(replaces)
        if existing is not None and existing['sender'] == sender:
            number = replaces
            position = list(self.items).index(number)
            self.remove_row(existing)
        else:
            if len(self.items) >= 128:
                self.close(next(iter(self.items)), 4)
            while self.next_id in self.items:
                self.next_id = (self.next_id % 0xffffffff) + 1
            number = self.next_id
            self.next_id = (number % 0xffffffff) + 1
            position = len(self.items)
        item = {'sender': sender, 'timer': 0, 'resident': hints.get('resident') is True}
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class('notification')
        item['row'] = row
        self.items[number] = item
        pairs = list(zip(actions[::2], actions[1::2]))[:32]
        if any(key == 'default' for key, label in pairs):
            cat = Gtk.Button()
            cat.set_relief(Gtk.ReliefStyle.NONE)
            cat.set_tooltip_text('Focus this notification’s pane')
            cat.get_accessible().set_name('Open ' + title[:128])
            cat.add(icon_widget)
            cat.connect('clicked', lambda *_: self.activate(number, 'default'))
            item['default_button'] = cat
        else:
            cat = icon_widget
        cat.set_valign(Gtk.Align.START)
        row.pack_start(cat, False, False, 0)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.set_hexpand(True)
        for text, is_title in ((title[:512], True), (body[:8192], False)):
            if not text:
                continue
            label = Gtk.Label(label=text, xalign=0)
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_max_width_chars(42)
            label.set_width_chars(1)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_lines(2 if is_title else 8)
            if is_title:
                label.get_style_context().add_class('notification-title')
            content.pack_start(label, False, False, 0)
        other_actions = [(key, label) for key, label in pairs if key != 'default']
        if other_actions:
            buttons = Gtk.FlowBox()
            buttons.set_selection_mode(Gtk.SelectionMode.NONE)
            for key, label in other_actions:
                button = Gtk.Button(label=label[:80])
                button.connect('clicked', lambda _, action=key: self.activate(number, action))
                buttons.add(button)
            content.pack_start(buttons, False, False, 0)
        row.pack_start(content, True, True, 0)
        dismiss = Gtk.Button(label='×')
        dismiss.set_tooltip_text('Dismiss notification')
        dismiss.set_valign(Gtk.Align.START)
        dismiss.connect('clicked', lambda *_: self.close(number, 2))
        row.pack_end(dismiss, False, False, 0)
        self.box.pack_start(row, False, False, 0)
        self.box.reorder_child(row, position)
        row.show_all()
        if timeout < 0:
            timeout = 0 if hints.get('urgency') == 2 else 8000
        if timeout > 0:
            item['timer'] = GLib.timeout_add(min(timeout, 0x7fffffff), self.expire, number)
        self.relayout()
        return number

    def expire(self, number):
        item = self.items.get(number)
        if item:
            item['timer'] = 0
            self.close(number, 1)
        return False

    def activate(self, number, action):
        item = self.items.get(number)
        if item:
            self.emit(item, 'ActionInvoked', '(us)', (number, action))
            if not item['resident']:
                self.close(number, 2)

    def remove_row(self, item):
        if item['timer']:
            GLib.source_remove(item['timer'])
        item['row'].destroy()

    def close(self, number, reason):
        item = self.items.pop(number, None)
        if item:
            self.remove_row(item)
            self.emit(item, 'NotificationClosed', '(uu)', (number, reason))
            self.relayout()

    def relayout(self):
        if not self.layout_source:
            self.layout_source = GLib.idle_add(self.layout)

    def layout(self):
        self.layout_source = 0
        if not self.items:
            self.window.hide()
            self.area = None
            return False
        if self.area is None:
            display = self.window.get_display()
            _, x, y = display.get_default_seat().get_pointer().get_position()
            self.area = display.get_monitor_at_point(x, y).get_workarea()
        area = self.area
        width = min(420, max(160, area.width - 24))
        self.box.set_size_request(width, -1)
        self.scroll.show_all()
        natural = self.box.get_preferred_height_for_width(width)[1]
        height = min(natural, max(80, area.height - 24))
        self.scroll.set_max_content_height(-1)
        self.scroll.set_min_content_height(height)
        self.scroll.set_max_content_height(height)
        self.scroll.set_size_request(width, height)
        self.window.resize(width, height)
        self.window.move(area.x + area.width - width - 12, area.y + 12)
        self.window.show_all()
        return False

    def stop(self):
        for item in self.items.values():
            self.remove_row(item)
        self.items.clear()
        if self.layout_source:
            GLib.source_remove(self.layout_source)
            self.layout_source = 0
        self.bus.unregister_object(self.registration)
        self.window.destroy()


def wait_ready(pid):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            owner, = bus_call(bus, 'GetNameOwner', GLib.Variant('(s)', (NAME,)), '(s)')
            actual, = bus_call(bus, 'GetConnectionUnixProcessID', GLib.Variant('(s)', (owner,)), '(u)')
            if actual != pid:
                print('Pleb notifications: preserving the existing notification service', file=sys.stderr)
            return 0
        except GLib.Error:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(.05)
    print('Pleb notifications: service did not become ready', file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait-ready', type=int)
    args = parser.parse_args()
    if args.wait_ready:
        return wait_ready(args.wait_ready)
    Gtk.init([])
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    center = NotificationCenter(bus)
    result, = bus_call(bus, 'RequestName', GLib.Variant('(su)', (NAME, 4)), '(u)')
    if result != 1:
        center.stop()
        return 0  # Another desktop owns this bus; never displace it.
    bus.connect('closed', lambda *_: Gtk.main_quit())
    for signum in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, lambda: (Gtk.main_quit(), False)[1])
    try:
        Gtk.main()
    finally:
        center.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
