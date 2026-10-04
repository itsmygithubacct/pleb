#!/usr/bin/python3
"""Capture a selected application pane in its own authenticated process."""
import json
import os
import sys

from capture_sources import (CaptureError, Source, bind_capture_parent, physical_environment,
                             revalidate, source_environment, verify_capture_geometry)


def main():
    bind_capture_parent()
    source = Source(**json.loads(sys.argv[1])).validate()
    if not source.pane:
        raise CaptureError('An application-pane source is required')
    desktop = physical_environment()
    revalidate(source, desktop)
    os.environ.update(source_environment(source, desktop))
    verify_capture_geometry(source, os.environ)
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    gi.require_version('GdkX11', '3.0')
    from gi.repository import Gdk, GdkX11, Gtk
    Gtk.init([])
    display = Gdk.Display.get_default()
    window = GdkX11.X11Window.foreign_new_for_display(display, source.xid)
    image = Gdk.pixbuf_get_from_window(window, 0, 0, source.width, source.height)
    if image is None:
        raise CaptureError('The selected application pane stopped')
    success, data = image.save_to_bufferv('png', [], [])
    if not success or len(data) > 67108864:
        raise CaptureError('Could not encode the selected application pane')
    revalidate(source, desktop)
    verify_capture_geometry(source, os.environ)
    sys.stdout.buffer.write(data)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('pleb capture: the selected application screenshot failed', file=sys.stderr)
        sys.exit(1)
