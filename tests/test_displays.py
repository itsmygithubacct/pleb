import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pleb_displays", ROOT / "lib/displays.py")
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


def fixture():
    text = "Screen 0: minimum 8 x 8, current 3840 x 1080, maximum 32767 x 32767\n"
    for name, position, primary, edid in (("DP-1", 0, " primary", "11"), ("HDMI-1", 1920, "", "22")):
        text += f"""{name} connected{primary} 1920x1080+{position}+0 (0x1bd) normal (normal left inverted right x axis y axis) 530mm x 290mm
    Transform: 1.000000 0.000000 0.000000
               0.000000 1.000000 0.000000
               0.000000 0.000000 1.000000
               filter:
    EDID:
        {edid * 16}
  1920x1080 (0x1bd) 148.500MHz +HSync +VSync *current +preferred
        h: width 1920 start 2008 end 2052 total 2200 skew 0 clock 67.50KHz
        v: height 1080 start 1084 end 1089 total 1125 clock 60.00Hz
  1920x1080 (0x1be) 185.625MHz +HSync +VSync
        h: width 1920 start 2008 end 2052 total 2200 skew 0 clock 84.37KHz
        v: height 1080 start 1084 end 1089 total 1125 clock 74.97Hz
"""
    return text + "DP-2 disconnected (normal left inverted right x axis y axis)\n"


class Backend:
    def __init__(self):
        self.state = d.parse_query(fixture())
        self.applied = []
        self.fail = False

    def query(self):
        return copy.deepcopy(self.state)

    def apply(self, layout):
        self.applied.append(copy.deepcopy(layout))
        for desired in layout["outputs"]:
            next(o for o in self.state["outputs"] if o["name"] == desired["name"]).update(desired)
        if self.fail:
            self.fail = False
            raise d.DisplayError("partial apply failure")


class DisplaysTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = d.private_dir(Path(self.tmp.name) / "config")
        self.state = d.private_dir(Path(self.tmp.name) / "state")
        self.backend = Backend()
        self.before = d.snapshot(self.backend.query())
        self.target = copy.deepcopy(self.before)
        self.target["outputs"][0]["rate"] = "74.97"

    def test_parser_preserves_refresh_primary_geometry_and_edid(self):
        outputs = self.backend.query()["outputs"]
        self.assertEqual(len(outputs), 2)
        self.assertEqual(outputs[0]["modes"], {"1920x1080": ["60.00", "74.97"]})
        self.assertTrue(outputs[0]["primary"])
        self.assertFalse(outputs[1]["primary"])
        self.assertEqual(outputs[1]["x"], 1920)
        self.assertNotEqual(outputs[0]["identity"], outputs[1]["identity"])
        self.assertTrue(all(o["safe"] for o in outputs))
        d.validate(self.target, self.backend.query())

    def test_refuses_all_off_unsupported_modes_and_oversized_framebuffer(self):
        mutations = (
            lambda x: [o.update(enabled=False, primary=False) for o in x["outputs"]],
            lambda x: x["outputs"][0].update(rate="999"),
            lambda x: x["outputs"][0].update(x=32767),
            lambda x: x["outputs"][0].update(x=-1),
            lambda x: x["outputs"][0].update(primary=False),
            lambda x: x["outputs"][0].update(rotation="--off"),
            lambda x: x["outputs"][0].update(identity="foreign"),
            lambda x: x["outputs"][0].update(enabled="false"),
        )
        for mutate in mutations:
            layout = copy.deepcopy(self.target)
            mutate(layout)
            with self.assertRaises(d.DisplayError):
                d.validate(layout, self.backend.query())

    def test_refuses_scaling_and_panning(self):
        for text in (fixture().replace("1.000000", "1.250000", 1),
                     fixture().replace("    Transform:", "    Panning: 3840x1080+0+0\n    Transform:", 1)):
            with self.assertRaises(d.DisplayError):
                d.validate(self.target, d.parse_query(text))

    def exchange(self, reply=b"confirm", timeout=0.04):
        parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        thread = threading.Thread(target=d.transaction, args=(worker, self.backend, self.target, self.config, self.state, timeout))
        thread.start()
        parent.settimeout(2)
        message = parent.recv(65536)
        if message == b"ready":
            if reply is not None:
                parent.send(reply)
            message = parent.recv(65536)
        parent.close()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        return json.loads(message)

    def test_confirmation_persists_private_profile(self):
        self.assertTrue(self.exchange()["confirmed"])
        self.assertEqual(d.snapshot(self.backend.query()), self.target)
        files = list(self.config.glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(files[0].read_text()), self.target)

    def test_timeout_and_rejection_restore_without_saving(self):
        for reply in (None, b"revert"):
            self.assertFalse(self.exchange(reply)["confirmed"])
            self.assertEqual(d.snapshot(self.backend.query()), self.before)
            self.assertEqual(list(self.config.iterdir()), [])

    def test_partial_failure_is_rolled_back(self):
        self.backend.fail = True
        result = self.exchange()
        self.assertIn("partial apply", result["error"])
        self.assertEqual(d.snapshot(self.backend.query()), self.before)

    def test_client_disconnect_reverts_in_independent_process(self):
        parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        result_path = Path(self.tmp.name) / "worker-result.json"
        pid = os.fork()
        if pid == 0:
            parent.close()
            d.transaction(worker, self.backend, self.target, self.config, self.state, 2)
            result_path.write_text(json.dumps(d.snapshot(self.backend.query())))
            os._exit(0)
        worker.close()
        parent.settimeout(3)
        try:
            self.assertEqual(parent.recv(65536), b"ready")
        finally:
            # EOF is exactly what the worker observes when its UI is killed.
            parent.close()
            _, status = os.waitpid(pid, 0)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(result_path.read_text()), self.before)
        self.assertEqual(list(self.config.iterdir()), [])

    def test_lock_prevents_second_apply(self):
        with d.locked(self.state):
            result = self.exchange()
        self.assertIn("Another display operation", result["error"])
        self.assertEqual(self.backend.applied, [])

    def test_restore_ignores_other_monitors_and_restores_matching_profile(self):
        d.save(self.config, self.target)
        self.backend.state["outputs"][0]["identity"] = "foreign"
        d.restore(self.backend, self.config, self.state)
        self.assertEqual(self.backend.applied, [])
        self.backend = Backend()
        d.restore(self.backend, self.config, self.state)
        self.assertEqual(d.snapshot(self.backend.query()), self.target)

    def test_no_shell_interpretation_in_backend(self):
        with mock.patch.object(d.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run:
            d.RandR().apply(self.target)
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], "xrandr")
        self.assertNotIn("shell", kwargs)
        self.assertIn("--primary", args[0])
        self.assertEqual(kwargs["timeout"], 8)

    def test_embedded_gui_backend_targets_physical_display(self):
        with mock.patch.dict(os.environ, {"DISPLAY": ":99", "XAUTHORITY": "/private/ui.auth"}), \
                mock.patch.object(d.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=fixture(), stderr="")) as run:
            d.RandR({"DISPLAY": ":0", "XAUTHORITY": "/physical/auth"}).query()
        self.assertEqual(run.call_args.kwargs["env"]["DISPLAY"], ":0")
        self.assertEqual(run.call_args.kwargs["env"]["XAUTHORITY"], "/physical/auth")

    def test_primary_command_never_clears_primary_for_secondary_outputs(self):
        for index in (0, 1):
            layout = copy.deepcopy(self.target)
            for i, output in enumerate(layout["outputs"]):
                output["primary"] = i == index
            backend = d.RandR()
            with mock.patch.object(backend, "run") as run:
                backend.apply(layout)
            args = run.call_args.args[0]
            self.assertNotIn("--noprimary", args)
            self.assertEqual(args.count("--primary"), 1)
            preceding = args[:args.index("--primary")]
            last_output = max(i for i, arg in enumerate(preceding) if arg == "--output")
            self.assertEqual(preceding[last_output+1], layout["outputs"][index]["name"])

    def test_baseline_without_primary_uses_global_clear_once(self):
        for output in self.target["outputs"]:
            output["primary"] = False
        backend = d.RandR()
        with mock.patch.object(backend, "run") as run:
            backend.apply(self.target)
        args = run.call_args.args[0]
        self.assertEqual(args[0], "--noprimary")
        self.assertEqual(args.count("--noprimary"), 1)
        self.assertNotIn("--primary", args)

    def test_storage_refuses_symlinks_and_shared_directories(self):
        link = Path(self.tmp.name) / "link"
        link.symlink_to(self.config, target_is_directory=True)
        with self.assertRaises(d.DisplayError):
            d.private_dir(link)
        self.config.chmod(0o755)
        with self.assertRaises(d.DisplayError):
            d.private_dir(self.config)

    def test_process_identity_disappears_when_parent_does(self):
        self.assertIsNotNone(d.process_identity(os.getpid()))
        self.assertIsNone(d.process_identity(999999999))

    def test_watcher_only_restores_on_topology_change_and_stops_on_pid_reuse(self):
        d.save(self.config, self.target)
        connection = mock.Mock()
        module = types.ModuleType("Xlib.display")
        module.Display = mock.Mock(return_value=connection)
        with mock.patch.dict(sys.modules, {"Xlib": types.ModuleType("Xlib"), "Xlib.display": module}), \
                mock.patch.object(d, "process_identity", side_effect=["start", "start", "start", "reused"]), \
                mock.patch.object(d.time, "sleep"), \
                mock.patch.object(d, "restore") as restore:
            d.watch(123, self.backend, self.config, self.state)
        self.assertEqual(restore.call_count, 1)
        connection.close.assert_called_once()

    def test_missing_edid_never_automatically_restores(self):
        self.backend.state["outputs"][0]["identity"] = d.hashlib.sha256(b"").hexdigest()
        layout = d.snapshot(self.backend.query())
        layout["outputs"][0]["rate"] = "74.97"
        d.save(self.config, layout)
        d.restore(self.backend, self.config, self.state)
        self.assertEqual(self.backend.applied, [])

    def test_disconnect_keeps_remaining_output_visible(self):
        self.backend.state["outputs"].pop(0)
        d.rollback(self.backend, self.before)
        output = self.backend.state["outputs"][0]
        self.assertTrue(output["enabled"])
        self.assertTrue(output["primary"])
        self.assertEqual((output["x"], output["y"]), (0, 0))

    def test_changed_layout_cannot_be_confirmed(self):
        parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        thread = threading.Thread(target=d.transaction, args=(worker, self.backend, self.target, self.config, self.state, 1))
        thread.start()
        parent.settimeout(2)
        self.assertEqual(parent.recv(65536), b"ready")
        self.backend.state["outputs"][0]["x"] = 100
        parent.send(b"confirm")
        result = json.loads(parent.recv(65536))
        parent.close()
        thread.join(2)
        self.assertIn("changed before confirmation", result["error"])
        self.assertEqual(d.snapshot(self.backend.query()), self.before)
        self.assertEqual(list(self.config.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
