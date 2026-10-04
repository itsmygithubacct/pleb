import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import capture_sources as capture


class CaptureSourcesTests(unittest.TestCase):
    def test_private_application_requires_physical_context(self):
        with self.assertRaises(capture.CaptureError):
            capture.physical_environment({"DISPLAY": ":91", "KILIX_PRIVATE_XAPP": "1"})

    def test_physical_display_authentication_and_bus_replace_private_context(self):
        env = capture.physical_environment({"DISPLAY": ":91", "XAUTHORITY": "/private",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/private/bus", "KILIX_PRIVATE_XAPP": "1",
            "PLEB_DESKTOP_DISPLAY": ":0", "PLEB_DESKTOP_XAUTHORITY": "/physical",
            "PLEB_DESKTOP_BUS_ADDRESS": "unix:path=/physical/bus"})
        self.assertEqual((env["DISPLAY"], env["XAUTHORITY"], env["DBUS_SESSION_BUS_ADDRESS"]),
                         (":0", "/physical", "unix:path=/physical/bus"))

    def test_forwarded_or_missing_displays_are_refused(self):
        for display in ("", "localhost:10.0", "host:0", ":0; command", "wayland-0"):
            with self.subTest(display=display), self.assertRaises(capture.CaptureError):
                capture.physical_environment({"DISPLAY": display})

    def test_mixed_dpi_and_rotation_determine_capture_bounds(self):
        state = {"outputs": [dict(name="DP-1", enabled=True, safe=True, mode="3840x2160",
                    scale=2, rotation="normal", x=0, y=0),
                dict(name="DP-2", enabled=True, safe=True, mode="1920x1080",
                    scale=1, rotation="left", x=1920, y=0)]}
        sources = capture.monitor_sources(state)
        self.assertEqual([(s.x, s.y, s.width, s.height) for s in sources],
                         [(0, 0, 1920, 1080), (1920, 0, 1080, 1920)])

    def test_disabled_or_unsupported_output_is_not_offered(self):
        state = {"outputs": [dict(enabled=False, safe=True), dict(enabled=True, safe=False)]}
        self.assertEqual(capture.monitor_sources(state), [])

    def test_source_does_not_accept_coerced_or_unbounded_geometry(self):
        for values in ((-1, 0, 10, 10), (0, 0, 0, 10), (0, 0, 16385, 10),
                       (0, 0, 16384, 16384), (False, 0, 10, 10), (0, 0, 10.5, 10)):
            with self.subTest(values=values), self.assertRaises(capture.CaptureError):
                capture.Source("test", "Test", 1, *values).validate()
        with self.assertRaises(capture.CaptureError):
            capture.Source("window:0", "Test", 2, 0, 0, 10, 10).validate()

    def test_disappeared_or_changed_source_never_expands_to_desktop(self):
        before = capture.Source("monitor:DP-1", "Display", 1, 0, 0, 1920, 1080)
        for current in ([], [capture.Source("monitor:DP-2", "Other", 1, 0, 0, 1920, 1080)],
                        [capture.Source(before.key, before.label, 1, 0, 0, 3840, 2160)]):
            with mock.patch.object(capture, "_physical_sources", return_value=current), self.assertRaises(capture.CaptureError):
                capture.revalidate(before)

    def test_title_change_keeps_the_same_selected_source(self):
        before = capture.Source("window:10", "Old title", 2, 0, 0, 100, 100, 10)
        after = capture.Source(before.key, "New title", 2, 0, 0, 100, 100, 10)
        with mock.patch.object(capture, "_physical_sources", return_value=[after]):
            self.assertEqual(capture.revalidate(before), after)


if __name__ == "__main__":
    unittest.main()
