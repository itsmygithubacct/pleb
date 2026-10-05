from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import capture_session as session


def desktop(**changes):
    props = dict(User=(1001, '/user'), Type='x11', Class='user', Remote=False,
                 Display=':71', Active=True, LockedHint=False)
    props.update(changes)
    return props


class Bus:
    def __init__(self, props):
        self.props = props
        self.PreparingForSleep = False
        self.matches = []

    def get_object(self, _name, path):
        return self if path == session.ROOT else self.props[path]

    seatless = frozenset()

    def ListSessions(self, **_kwargs):
        return [(str(i), props['User'][0], 'fixture', '' if path in self.seatless else 'seat0', path)
                for i, (path, props) in enumerate(self.props.items())]

    def GetAll(self, _interface, **_kwargs):
        return {'PreparingForSleep': self.PreparingForSleep}

    def add_signal_receiver(self, callback, **kwargs):
        match = Mock()
        self.matches.append((callback, kwargs, match))
        return match

    def call_on_disconnection(self, callback):
        self.disconnect = callback


class CaptureSessionTests(unittest.TestCase):
    def setUp(self):
        self.props = desktop()
        self.bus = Bus({'/physical': self.props})
        self.closed = Mock()
        def interface(obj, _name):
            if isinstance(obj, dict):
                return Mock(GetAll=lambda *_args, **_kwargs: obj.copy())
            return obj
        patcher = patch.object(session.dbus, 'Interface', side_effect=interface)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.guard = session.CaptureSessionGuard(self.closed, ':71.0', bus=self.bus, uid=1001)
        self.addCleanup(self.guard.close)

    def test_matching_active_desktop_allows_capture(self):
        self.assertTrue(self.guard.can_capture)
        self.assertEqual(self.guard.path, '/physical')
        self.closed.assert_not_called()

    def test_lock_closes_capture_and_unlock_only_allows_fresh_requests(self):
        self.guard.changed(session.SESSION, {'LockedHint': True}, [], path='/physical')
        self.assertFalse(self.guard.can_capture)
        self.closed.assert_called_once_with()
        self.guard.changed(session.SESSION, {'LockedHint': False}, [], path='/physical')
        self.assertTrue(self.guard.can_capture)
        self.closed.assert_called_once_with()

    def test_inactive_session_closes_capture(self):
        self.guard.changed(session.SESSION, {'Active': False}, [], path='/physical')
        self.assertFalse(self.guard.can_capture)
        self.closed.assert_called_once_with()

    def test_sleep_closes_capture_and_resume_does_not_restart_it(self):
        self.guard.prepare_sleep(True)
        self.assertFalse(self.guard.can_capture)
        self.guard.prepare_sleep(False)
        self.assertTrue(self.guard.can_capture)
        self.closed.assert_called_once_with()

    def test_invalidated_lock_property_is_read_from_logind(self):
        self.props['LockedHint'] = True
        self.guard.changed(session.SESSION, {}, ['LockedHint'], path='/physical')
        self.assertFalse(self.guard.can_capture)
        self.closed.assert_called_once_with()

    def test_logind_owner_loss_and_bus_disconnect_fail_closed(self):
        self.guard.owner_changed(session.LOGIN, ':1.20', '')
        self.assertFalse(self.guard.can_capture)
        self.bus.disconnect(self.bus)
        self.closed.assert_called_once_with()

    def test_replacement_login_closes_old_consent_even_if_new_session_is_active(self):
        self.bus.props = {'/replacement': desktop()}
        self.guard.sessions_changed('new', '/replacement')
        self.assertTrue(self.guard.can_capture)
        self.assertEqual(self.guard.path, '/replacement')
        self.closed.assert_called_once_with()

    def test_removed_desktop_cannot_turn_into_an_unmanaged_capture_session(self):
        self.bus.props = {}
        self.guard.sessions_changed('old', '/physical')
        self.assertFalse(self.guard.can_capture)
        self.closed.assert_called_once_with()

    def test_ambiguous_matching_sessions_fail_closed(self):
        self.bus.props['/another'] = desktop()
        self.guard.refresh()
        self.assertFalse(self.guard.can_capture)
        self.closed.assert_called_once_with()

    def test_display_identity_change_closes_capture(self):
        self.props['Display'] = ':72'
        self.guard.changed(session.SESSION, {'Display': ':72'}, [], path='/physical')
        self.assertFalse(self.guard.can_capture)
        self.closed.assert_called_once_with()

    def test_another_user_or_display_cannot_block_the_selected_desktop(self):
        self.bus.props['/other-user'] = desktop(User=(1002, '/user2'), LockedHint=True)
        self.bus.props['/other-display'] = desktop(Display=':72', LockedHint=True)
        self.guard.refresh()
        self.guard.changed(session.SESSION, {'LockedHint': True}, [], path='/other-display')
        self.assertTrue(self.guard.can_capture)
        self.closed.assert_not_called()

    def test_no_matching_login_supports_a_nested_x_server(self):
        guard = session.CaptureSessionGuard(self.closed, ':99', bus=self.bus, uid=1001)
        self.addCleanup(guard.close)
        self.assertTrue(guard.can_capture)
        self.assertFalse(guard.managed)

    def test_published_startx_session_is_managed_and_lock_closes_capture(self):
        # startx: logind Type=tty with no Display, but xss-lock still reports
        # LockedHint on it. pleb-session publishes its id; the guard follows it.
        self.bus.props['/tty'] = desktop(Type='tty', Display='')
        guard = session.CaptureSessionGuard(self.closed, ':0', session_id='1',
                                            bus=self.bus, uid=1001)
        self.addCleanup(guard.close)
        self.assertTrue(guard.managed)
        self.assertEqual(guard.path, '/tty')
        self.assertTrue(guard.can_capture)
        guard.changed(session.SESSION, {'LockedHint': True}, [], path='/tty')
        self.assertFalse(guard.can_capture)
        self.closed.assert_called_once_with()

    def test_published_session_that_disappears_fails_closed(self):
        self.bus.props['/tty'] = desktop(Type='tty', Display='')
        guard = session.CaptureSessionGuard(self.closed, ':0', session_id='1',
                                            bus=self.bus, uid=1001)
        self.addCleanup(guard.close)
        self.assertTrue(guard.can_capture)
        del self.bus.props['/tty']
        guard.sessions_changed('1', '/tty')
        self.assertTrue(guard.managed)
        self.assertFalse(guard.can_capture)
        self.closed.assert_called_once_with()

    def test_stale_published_id_from_an_earlier_login_is_ignored(self):
        # No current session has id '7': the x11 desktop on :71 is still
        # matched and guarded, and a nested server stays usable.
        guard = session.CaptureSessionGuard(self.closed, ':71', session_id='7',
                                            bus=self.bus, uid=1001)
        self.addCleanup(guard.close)
        self.assertFalse(guard.bound)
        self.assertEqual(guard.path, '/physical')
        self.assertTrue(guard.can_capture)
        guard.changed(session.SESSION, {'LockedHint': True}, [], path='/physical')
        self.assertFalse(guard.can_capture)
        nested = session.CaptureSessionGuard(self.closed, ':99', session_id='7',
                                             bus=self.bus, uid=1001)
        self.addCleanup(nested.close)
        self.assertTrue(nested.can_capture)

    def test_published_session_without_a_seat_is_not_bound(self):
        # Seatless sessions (su -l, machinectl shell) report Active forever.
        self.bus.props['/tty'] = desktop(Type='tty', Display='')
        self.bus.seatless = {'/tty'}
        guard = session.CaptureSessionGuard(self.closed, ':0', session_id='1',
                                            bus=self.bus, uid=1001)
        self.addCleanup(guard.close)
        self.assertFalse(guard.bound)

    def test_published_session_ignores_every_other_login(self):
        # Session '0' is the x11 desktop on :71; bound to '1', its state is
        # irrelevant, and an x11 session id on another display is refused.
        self.bus.props['/tty'] = desktop(Type='tty', Display='')
        guard = session.CaptureSessionGuard(self.closed, ':71', session_id='1',
                                            bus=self.bus, uid=1001)
        self.addCleanup(guard.close)
        guard.changed(session.SESSION, {'LockedHint': True}, [], path='/physical')
        self.assertTrue(guard.can_capture)
        other = session.CaptureSessionGuard(self.closed, ':72', session_id='0',
                                            bus=self.bus, uid=1001)
        self.addCleanup(other.close)
        self.assertFalse(other.can_capture)

    def test_locked_at_start_rejects_capture_without_waiting_for_a_signal(self):
        self.props['LockedHint'] = True
        guard = session.CaptureSessionGuard(self.closed, ':71', bus=self.bus, uid=1001)
        self.addCleanup(guard.close)
        self.assertFalse(guard.can_capture)

    def test_inactive_physical_desktop_at_start_is_not_a_nested_x_server(self):
        self.props['Active'] = False
        guard = session.CaptureSessionGuard(self.closed, ':71', bus=self.bus, uid=1001)
        self.addCleanup(guard.close)
        self.assertTrue(guard.managed)
        self.assertFalse(guard.can_capture)

    def test_desktop_inactive_at_start_becomes_capturable_when_logind_activates_it(self):
        self.props['Active'] = False
        guard = session.CaptureSessionGuard(self.closed, ':71', bus=self.bus, uid=1001)
        self.addCleanup(guard.close)
        self.assertFalse(guard.can_capture)
        self.props['Active'] = True
        guard.changed(session.SESSION, {'Active': True}, [], path='/physical')
        self.assertEqual(guard.path, '/physical')
        self.assertTrue(guard.can_capture)

    def test_sleeping_at_start_rejects_capture(self):
        self.bus.PreparingForSleep = True
        guard = session.CaptureSessionGuard(self.closed, ':71', bus=self.bus, uid=1001)
        self.addCleanup(guard.close)
        self.assertFalse(guard.can_capture)

    def test_failed_authoritative_snapshot_closes_capture(self):
        self.bus.ListSessions = Mock(side_effect=session.dbus.exceptions.DBusException('Unavailable'))
        self.guard.refresh()
        self.assertFalse(self.guard.can_capture)
        self.closed.assert_called_once_with()

    def test_only_authoritative_system_service_signals_are_subscribed(self):
        for _callback, kwargs, _match in self.bus.matches:
            expected = 'org.freedesktop.DBus' if kwargs['signal_name'] == 'NameOwnerChanged' else session.LOGIN
            self.assertEqual(kwargs['bus_name'], expected)


if __name__ == '__main__':
    unittest.main()
