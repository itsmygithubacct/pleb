import importlib.util
import json
import os
from pathlib import Path
import select
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest import mock

from test_displays import Backend

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import displays_gui as gui


@unittest.skipUnless(shutil.which("Xvfb"), "private X server required")
class GraphicalDisplaysTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        readfd, writefd = os.pipe()
        cls.server = subprocess.Popen(
            ["Xvfb", "-displayfd", str(writefd), "-screen", "0", "1200x800x24", "-nolisten", "tcp", "-noreset"],
            pass_fds=(writefd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(writefd)
        try:
            if not select.select([readfd], [], [], 5)[0]:
                cls.server.terminate()
                cls.server.wait(timeout=5)
                raise RuntimeError("Private X server startup timed out")
            cls.display = ":" + os.read(readfd, 32).decode().strip()
        finally:
            os.close(readfd)

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {
            "PATH": "/usr/bin:/bin", "HOME": self.tmp.name,
            "DISPLAY": self.display, "XAUTHORITY": self.tmp.name + "/authority",
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.root = gui.tk.Tk()
        self.addCleanup(self.close_root)
        self.config = gui.core.private_dir(Path(self.tmp.name)/"config")
        self.state = gui.core.private_dir(Path(self.tmp.name)/"state")
        self.app = gui.Arranger(self.root, Backend(), self.config, self.state)
        self.worker_patch = mock.patch.object(gui.core, "start_worker", side_effect=self.start_test_worker)
        self.worker_patch.start()
        self.addCleanup(self.worker_patch.stop)
        self.root.update()

    def start_test_worker(self, backend, layout, config, state, timeout):
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        thread = threading.Thread(target=gui.core.transaction, args=(child, backend, layout, config, state, timeout))
        thread.start()
        worker = mock.Mock()
        worker.poll.side_effect = lambda: None if thread.is_alive() else 0
        worker.wait.side_effect = lambda timeout: thread.join(timeout)
        return parent, worker

    def close_root(self):
        if self.app.channel:
            self.app.channel.close()
        if self.app.worker:
            self.app.worker.wait(timeout=5)
            self.app.worker = None
        self.root.destroy()

    def pump_until(self, condition):
        deadline = time.monotonic()+5
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.assertTrue(condition())

    def test_arrange_primary_and_resolution_controls_only_edit_draft(self):
        before = self.app.backend.query()
        self.app.mirror()
        self.assertEqual([o["x"] for o in self.app.layout["outputs"]], [0, 0])
        self.app.extend()
        self.assertEqual([o["x"] for o in self.app.layout["outputs"]], [0, 1920])
        self.app.select(1)
        self.app.make_primary()
        self.app.rate.set("74.97")
        self.app.edit()
        self.assertTrue(self.app.layout["outputs"][1]["primary"])
        self.assertEqual(self.app.layout["outputs"][1]["rate"], "74.97")
        self.assertEqual(self.app.backend.query(), before)

    def test_drag_can_move_left_of_origin_then_normalizes(self):
        self.app.select(1)
        self.app.drag = (0, 0, 1920, 0)
        self.app.motion(mock.Mock(x=-4000*self.app.scale, y=0))
        self.app.release(None)
        self.assertEqual(self.app.layout["outputs"][1]["x"], 0)
        self.assertGreater(self.app.layout["outputs"][0]["x"], 0)

    def test_mixed_scaling_changes_logical_cards_and_positions_only_in_the_draft(self):
        before = self.app.backend.query()
        self.app.zoom.set("150%")
        self.app.edit()
        self.app.extend()
        self.assertEqual(self.app.layout["outputs"][0]["scale"], 1.5)
        self.assertEqual(gui.dimensions(self.app.layout["outputs"][0]), (1280, 720))
        self.assertEqual(self.app.layout["outputs"][1]["x"], 1280)
        self.assertEqual(self.app.backend.query(), before)

    def test_scale_controls_and_preview_action_are_visible_at_the_minimum_window_size(self):
        self.root.geometry('850x680')
        self.root.update()
        right = self.root.winfo_rootx() + self.root.winfo_width()
        bottom = self.root.winfo_rooty() + self.root.winfo_height()
        for widget in (*self.app.boxes, self.app.apply_button):
            with self.subTest(widget=widget):
                self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right)
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom)

    def test_scaled_graphical_confirmation_persists_the_selected_zoom(self):
        self.app.zoom.set("125%")
        self.app.edit()
        self.app.extend()
        self.app.apply()
        self.pump_until(lambda: self.app.preview_deadline is not None)
        self.app.reply(b"confirm")
        self.pump_until(lambda: self.app.channel is None and self.app.worker is None)
        profile = json.loads(next(self.config.glob("*.json")).read_text())
        self.assertEqual(profile["version"], 2)
        self.assertEqual(profile["outputs"][0]["scale"], 1.25)
        self.assertEqual(profile["outputs"][1]["x"], 1536)

    def test_third_monitor_can_be_primary(self):
        import copy
        third = copy.deepcopy(self.app.backend.state["outputs"][0])
        third.update(name="DP-3", identity="third-monitor", primary=False, x=3840)
        self.app.backend.state["outputs"].append(third)
        self.app.reload()
        self.app.select(2)
        self.app.make_primary()
        self.assertEqual([o["primary"] for o in self.app.layout["outputs"]], [False, False, True])
        gui.core.validate(self.app.layout, self.app.backend.query())

    def test_graphical_confirmation_saves_profile(self):
        self.app.rate.set("74.97")
        self.app.edit()
        self.app.apply()
        self.pump_until(lambda: self.app.preview_deadline is not None)
        self.app.reply(b"confirm")
        self.pump_until(lambda: self.app.channel is None and self.app.worker is None)
        files = list(self.config.glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text())["outputs"][0]["rate"], "74.97")
        self.assertEqual(self.app.status.get(), "Layout confirmed and saved.")

    def test_graphical_rejection_does_not_save(self):
        self.app.apply()
        self.pump_until(lambda: self.app.preview_deadline is not None)
        self.app.reply(b"revert")
        self.pump_until(lambda: self.app.channel is None and self.app.worker is None)
        self.assertEqual(list(self.config.glob("*.json")), [])
        self.assertEqual(self.app.status.get(), "Previous layout restored.")

    def test_real_worker_uses_private_physical_server_and_reverts_on_ui_disconnect(self):
        self.worker_patch.stop()
        backend = gui.core.RandR()
        before = gui.core.snapshot(backend.query())
        layout = gui.core.snapshot(backend.query())
        layout["outputs"][0]["primary"] = True
        parent, worker = gui.core.start_worker(backend, layout, self.config, self.state, 5)
        parent.settimeout(5)
        try:
            self.assertEqual(parent.recv(65536), b"ready")
            self.assertTrue(gui.core.matches(layout, backend.query()))
        finally:
            parent.close()
            worker.wait(timeout=5)
        self.assertTrue(gui.core.matches(before, backend.query()))
        self.assertEqual(list(self.config.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
