from pathlib import Path
import os
import sys
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import capture_portal as capture


class CaptureLifecycleTests(unittest.TestCase):
    def test_portal_wires_its_lock_guard_to_closing_every_session_and_request(self):
        # Every other lifecycle test builds the portal with object.__new__ and
        # a stub guard, so nothing bound the one line that makes a lock close
        # sharing: the guard's callback must be Portal.close.
        created = {}

        class Guard:
            def __init__(self, blocked, display, *, session_id=None):
                created.update(blocked=blocked, display=display, session_id=session_id)

        environment = {'PLEB_DESKTOP_DISPLAY': ':5', 'PLEB_DESKTOP_SESSION_ID': '42'}
        with mock.patch.object(capture.dbus.service, 'BusName'), \
                mock.patch.object(capture.Properties, '__init__', return_value=None), \
                mock.patch.object(capture, 'CaptureSessionGuard', Guard), \
                mock.patch.dict(os.environ, environment):
            portal = capture.Portal(mock.Mock())
        self.assertEqual((created['display'], created['session_id']), (':5', '42'))
        session, request = mock.Mock(), mock.Mock()
        portal.sessions['/session'] = session
        portal.requests['/request'] = request
        created['blocked']()
        session.close.assert_called_once_with()
        request.finish.assert_called_once_with(1)

    def test_locked_desktop_does_not_create_a_session_or_consent_request(self):
        portal = object.__new__(capture.Portal)
        portal.authenticate = mock.Mock()
        portal.guard = types.SimpleNamespace(can_capture=False)
        reply = mock.Mock()
        with mock.patch.object(capture, 'Session') as session, mock.patch.object(capture, 'Request') as request:
            self.assertEqual(portal.CreateSession('/request', '/session', 'app', {}), (2, {}))
            portal.Screenshot('/request', 'app', '', {}, reply, mock.Mock())
            portal.PickColor('/request', 'app', '', {}, reply, mock.Mock())
            session.assert_not_called()
            request.assert_not_called()
        self.assertEqual(reply.call_count, 2)
        for call in reply.call_args_list:
            self.assertEqual(int(call.args[0]), 2)

    def test_lock_between_consent_and_start_cannot_publish_a_producer(self):
        session = object.__new__(capture.Session)
        session.portal = types.SimpleNamespace(require_capture=mock.Mock(side_effect=capture.CaptureError('Locked')))
        session.producers = []
        session.close = mock.Mock()
        request = mock.Mock()
        with mock.patch.object(capture, 'Producer') as producer:
            session.start([mock.Mock()], request)
            producer.assert_not_called()
        request.finish.assert_called_once_with(2)
        session.close.assert_called_once_with()

    def test_lock_during_worker_startup_cannot_publish_a_ready_stream(self):
        session = object.__new__(capture.Session)
        session.closed = False
        session.producers = [types.SimpleNamespace(node={'node': 1, 'serial': 2})]
        session.portal = types.SimpleNamespace(require_capture=mock.Mock(side_effect=capture.CaptureError('Locked')))
        session.fail = mock.Mock()
        with mock.patch.object(capture.Gtk, 'Window') as window:
            session.ready(session.producers[0])
            window.assert_not_called()
        session.fail.assert_called_once_with()

    def test_worker_stop_notification_retires_an_already_ready_source(self):
        reader,writer=os.pipe();self.addCleanup(os.close,reader);self.addCleanup(os.close,writer)
        os.set_blocking(reader,False)
        producer=object.__new__(capture.Producer)
        producer.closed=False;producer.node=None;producer.buffer=b'';producer.deadline=time.monotonic()+10
        producer.process=types.SimpleNamespace(stdout=types.SimpleNamespace(fileno=lambda:reader),poll=lambda:None)
        producer.ready=mock.Mock();producer.failed=mock.Mock()
        os.write(writer,b'{"node":7,"serial":9}\n')
        self.assertTrue(producer.poll());self.assertEqual(producer.node,{'node':7,'serial':9})
        os.write(writer,b'{"closed":true}\n')
        self.assertFalse(producer.poll());producer.failed.assert_called_once()
        producer.ready.assert_called_once_with(producer)
    def test_backend_introspection_advertises_its_actual_properties(self):
        portal = object.__new__(capture.Portal)
        connection = mock.Mock()
        connection.list_exported_child_objects.return_value = []
        from xml.etree import ElementTree as ET
        xml = ET.fromstring(portal.Introspect(capture.ROOT, connection))
        screen = xml.find("./interface[@name='org.freedesktop.impl.portal.ScreenCast']")
        self.assertEqual({p.attrib["name"] for p in screen.findall("property")},
                         {"version", "AvailableSourceTypes", "AvailableCursorModes"})

    def test_backend_accepts_only_the_current_frontend_owner(self):
        portal = object.__new__(capture.Portal)
        portal.bus = mock.Mock()
        portal.bus.get_name_owner.return_value = ":1.100"
        portal.authenticate(":1.100")
        for sender in (None, ":1.101", capture.FRONTEND):
            with self.subTest(sender=sender), self.assertRaises(capture.dbus.exceptions.DBusException):
                portal.authenticate(sender)

    def test_session_lookup_does_not_allow_another_application(self):
        portal = object.__new__(capture.Portal)
        session = types.SimpleNamespace(app="app.one", closed=False)
        portal.sessions = {"/session": session}
        self.assertIs(portal.session("/session", "app.one"), session)
        with self.assertRaises(capture.CaptureError):
            portal.session("/session", "app.two")
        session.closed = True
        with self.assertRaises(capture.CaptureError):
            portal.session("/session", "app.one")

    def test_session_close_cancels_consent_and_stops_every_producer(self):
        session = object.__new__(capture.Session)
        session.closed, session.path = False, "/session"
        session.producers = [mock.Mock(), mock.Mock()]
        request, indicator = mock.Mock(), mock.Mock()
        session.pending, session.indicator = request, indicator
        session.portal = types.SimpleNamespace(sessions={session.path: session})
        session.Closed = mock.Mock()
        session.remove_from_connection = mock.Mock()
        session.close()
        session.close()
        for producer in session.producers:
            producer.close.assert_called_once_with()
        request.finish.assert_called_once_with(1)
        indicator.destroy.assert_called_once_with()
        session.Closed.assert_called_once_with()
        self.assertEqual(session.portal.sessions, {})

    def test_request_completion_is_one_shot_and_removes_the_dialog(self):
        request = object.__new__(capture.Request)
        request.done, request.path = False, "/request"
        request.success, request.dialog = mock.Mock(), mock.Mock()
        dialog = request.dialog
        request.portal = types.SimpleNamespace(requests={request.path: request})
        request.remove_from_connection = mock.Mock()
        request.finish(1)
        request.finish(0, {"uri": "file:///unexpected"})
        request.success.assert_called_once()
        self.assertEqual(int(request.success.call_args.args[0]), 1)
        dialog.destroy.assert_called_once_with()
        self.assertEqual(request.portal.requests, {})

    def test_capture_failure_is_reported_as_failure_and_stops_the_session(self):
        session = object.__new__(capture.Session)
        request = mock.Mock()
        session.pending = request
        session.close = mock.Mock()
        session.fail()
        request.finish.assert_called_once_with(2)
        session.close.assert_called_once_with()

    def test_disconnected_reply_does_not_interrupt_cleanup(self):
        request = object.__new__(capture.Request)
        request.done, request.path, request.dialog = False, "/request", None
        request.success = mock.Mock(side_effect=capture.dbus.exceptions.DBusException("Connection gone"))
        request.portal = types.SimpleNamespace(requests={request.path: request})
        request.remove_from_connection = mock.Mock()
        request.finish(1)
        self.assertTrue(request.done)
        self.assertEqual(request.portal.requests, {})

    def test_frontend_disappearance_closes_pending_and_active_operations(self):
        portal = object.__new__(capture.Portal)
        session, request = mock.Mock(), mock.Mock()
        portal.sessions, portal.requests = {"/session": session}, {"/request": request}
        portal.owner_changed(capture.FRONTEND, ":1.100", "")
        session.close.assert_called_once_with()
        request.finish.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
