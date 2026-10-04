#!/usr/bin/python3
"""One consented X11 source per owned PipeWire producer process."""
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from capture_sources import CaptureError, Source, physical_environment, revalidate


def main():
    if os.getuid() == 0:
        raise CaptureError("Capture runs as the desktop user")
    parent = os.getppid()
    # Kill the producer on backend failure too. Check the parent after prctl
    # so a death between fork and this setup cannot leave a capture running.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent or parent == 1:
        raise CaptureError("Capture parent is unavailable")
    source = Source(**json.loads(sys.argv[1])).validate()
    cursor_mode = int(sys.argv[2])
    if cursor_mode not in (1, 2):
        raise CaptureError("Unsupported cursor mode")
    os.environ.update(physical_environment())
    revalidate(source)
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst
    Gst.init(None)
    pipeline = Gst.Pipeline.new("capture-stream")
    elements = [Gst.ElementFactory.make(factory, name) for factory, name in (
        ("ximagesrc", "capture"), ("capsfilter", "rate"), ("queue", "queue"),
        ("videoconvert", "convert"), ("capsfilter", "format"), ("appsink", "frames"))]
    if any(element is None for element in elements):
        raise CaptureError("Capture plugins are missing; run pleb install")
    capture, rate, queue, _convert, pixel_format, sink = elements
    if source.kind == 2:
        # Linking probes caps and opens the display. Set the XID before any
        # link or display-name assignment, or ximagesrc silently uses root.
        capture.set_property("xid", source.xid)
    capture.set_property("display-name", os.environ["DISPLAY"])
    capture.set_property("use-damage", False)
    capture.set_property("show-pointer", cursor_mode == 2)
    if source.kind == 1:
        for key, value in (("startx", source.x), ("starty", source.y),
                           ("endx", source.x + source.width - 1), ("endy", source.y + source.height - 1)):
            capture.set_property(key, value)
    rate.set_property("caps", Gst.Caps.from_string("video/x-raw,framerate=30/1"))
    pixel_format.set_property("caps", Gst.Caps.from_string(
        f"video/x-raw,format=BGRx,width={source.width},height={source.height}"))
    for key, value in (("max-size-buffers", 2), ("max-size-bytes", 0), ("max-size-time", 0), ("leaky", 2)):
        queue.set_property(key, value)
    for key, value in (("sync", False), ("async", False), ("enable-last-sample", False),
                       ("emit-signals", True), ("max-buffers", 1), ("drop", True)):
        sink.set_property(key, value)
    name = "pleb-capture-" + uuid.uuid4().hex
    # Debian's GStreamer PipeWire sink can publish pointer-only buffers even
    # when a browser requests descriptors. Keep X11 acquisition in GStreamer,
    # and negotiate shareable buffers explicitly in our small transport.
    transport = ctypes.CDLL(str(Path(__file__).with_name("capture_transport.so")))
    transport.pleb_capture_open.argtypes = (ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32)
    transport.pleb_capture_open.restype = ctypes.c_void_p
    transport.pleb_capture_push.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t)
    transport.pleb_capture_push.restype = ctypes.c_int
    transport.pleb_capture_close.argtypes = (ctypes.c_void_p,)
    transport.pleb_capture_close.restype = None
    for element in elements:
        pipeline.add(element)
    for first, second in zip(elements, elements[1:]):
        if not first.link(second):
            raise CaptureError("Could not connect capture plugins")
    loop = GLib.MainLoop()
    failure = []
    producer = None
    def frame(reader):
        sample = reader.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        try:
            if not ok or transport.pleb_capture_push(producer, mapped.data, len(mapped.data)) < 0:
                failure.append("The selected capture transport stopped")
                GLib.idle_add(loop.quit)
                return Gst.FlowReturn.ERROR
        finally:
            if ok:
                buffer.unmap(mapped)
        return Gst.FlowReturn.OK
    sink.connect("new-sample", frame)
    def message(_bus, msg):
        if msg.type in (Gst.MessageType.ERROR, Gst.MessageType.EOS):
            failure.append("The selected capture source stopped")
            loop.quit()
    pipeline.get_bus().add_signal_watch()
    pipeline.get_bus().connect("message", message)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (loop.quit(), False)[1])
    def check_source():
        try:
            revalidate(source)
            return True
        except CaptureError:
            failure.append("The selected capture source changed")
            loop.quit()
            return False
    try:
        producer = transport.pleb_capture_open(name.encode(), source.width, source.height)
        if not producer:
            raise CaptureError("Could not start the capture transport")
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise CaptureError("Could not start the capture source")
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            output = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=3, check=True)
            nodes = [item for item in json.loads(output.stdout) if item.get("type") == "PipeWire:Interface:Node"
                     and item.get("info", {}).get("props", {}).get("node.name") == name]
            if len(nodes) == 1:
                props = nodes[0]["info"]["props"]
                print(json.dumps({"node": int(nodes[0]["id"]), "serial": int(props["object.serial"])}), flush=True)
                break
            time.sleep(.1)
        else:
            raise CaptureError("PipeWire did not publish the selected source")
        GLib.timeout_add_seconds(1, check_source)
        loop.run()
        if failure:
            raise CaptureError(failure[0])
    finally:
        pipeline.set_state(Gst.State.NULL)
        transport.pleb_capture_close(producer)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"pleb capture: {error}", file=sys.stderr)
        sys.exit(1)
