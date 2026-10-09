"""Native regressions for queued typing, real press cycles, history and retirement.

Uses the real frontend, production relay and the existing isolated X11 fixture.
No keyboard event or portal implementation is mocked.
"""
import argparse
import json
from pathlib import Path
import signal
import subprocess

from global_shortcuts_fixture import Fixture, BACKEND, IMPL


class FixFixture(Fixture):
    def close_session(self, app, session):
        start = len(app.messages)
        app.send(op='close_session', path=session)
        self.wait(lambda: any(m.get('done') == 'close_session' for m in app.messages[start:]))
        self.settle(.1)

    def listing(self, app, session):
        start = len(app.messages)
        app.send(op='list', session=session)
        self.wait(lambda: any('response' in m for m in app.messages[start:]))
        response = next(m for m in app.messages[start:] if 'response' in m)
        assert response['response'] == 0, response
        return response['results']['shortcuts']

    def tests(self):
        if self.args.retirement_only:
            self.retirement(timeout=True)
            return
        self.input_cycles()
        self.xi2_conflict()
        self.history()
        self.retirement(timeout=False)

    def input_cycles(self):
        from Xlib import X, XK, display
        from Xlib.ext import xtest
        connection = display.Display(self.physical)
        codes = {name: connection.keysym_to_keycode(XK.string_to_keysym(name))
                 for name in ('Control_L', 'Shift_L', 'F8', 'b')}
        a = self.app_a

        def event(kind, name):
            xtest.fake_input(connection, kind, codes[name])

        def burst(delayed=False):
            before = len(self.focus.messages)
            if delayed:
                self.backend.kill(signal.SIGSTOP)
            try:
                for name in ('Control_L', 'Shift_L', 'F8', 'b'):
                    event(X.KeyPress, name)
                for name in ('b', 'F8', 'Shift_L', 'Control_L'):
                    event(X.KeyRelease, name)
                connection.sync()
                if delayed:
                    self.settle(.1)  # Delay dispatch, never delay B after activation.
            finally:
                if delayed:
                    self.backend.kill(signal.SIGCONT)
            self.settle(.15)
            return sum(item.get('key') in (ord('b'), ord('B')) for item in self.focus.messages[before:])

        def cycles(gap):
            before, released = len(self.events(a)), len(self.events(a, 'Deactivated'))
            messages = len(a.messages)
            for name in ('Control_L', 'Shift_L'):
                event(X.KeyPress, name)
            connection.sync()
            try:
                for _ in range(10):
                    event(X.KeyPress, 'F8')
                    connection.sync()
                    if gap:
                        self.settle(gap)
                    event(X.KeyRelease, 'F8')
                    connection.sync()
                    if gap:
                        self.settle(gap)
            finally:
                for name in ('F8', 'Shift_L', 'Control_L'):
                    event(X.KeyRelease, name)
                connection.sync()
            self.settle(.2)
            events = [m for m in a.messages[messages:] if m.get('event') in ('Activated', 'Deactivated')]
            ordered = [m['event'] for m in events] == ['Activated', 'Deactivated'] * 10
            ordered &= all(m['session'] == self.input_session and m['ident'] == 'action' for m in events)
            return len(self.events(a)) - before, len(self.events(a, 'Deactivated')) - released, ordered

        try:
            self.click('Shortcut focus 1', 'Escape')
            self.check('unbound-queued-typing-ten-of-ten', sum(burst() for _ in range(10)) == 10)
            self.input_session = self.bind_new(a)
            self.click('Shortcut focus 1', 'Escape')
            self.check('bound-queued-typing-ten-of-ten', sum(burst() for _ in range(10)) == 10)
            self.check('queued-typing-survives-deliberate-backend-dispatch-delay', sum(burst(True) for _ in range(10)) == 10)
            for label, gap in (('fast-12ms', .012), ('slow-65ms', .065), ('queued-with-no-gap', 0)):
                self.check(label + '-ten-genuine-cycles-ten-pairs', cycles(gap) == (10, 10, True))
            before, released = len(self.events(a)), len(self.events(a, 'Deactivated'))
            for name in ('Control_L', 'Shift_L', 'F8'):
                event(X.KeyPress, name)
            connection.sync()
            self.settle(1.05)  # Exercise actual server autorepeat, not just a held sample.
            self.check('held-server-autorepeat-produces-one-activation',
                       len(self.events(a)) == before + 1 and len(self.events(a, 'Deactivated')) == released)
            event(X.KeyRelease, 'Control_L')
            connection.sync()
            self.wait(lambda: len(self.events(a, 'Deactivated')) == released + 1)
            self.check('modifier-first-release-ends-held-cycle', len(self.events(a)) == before + 1)
        finally:
            for name in ('F8', 'Shift_L', 'Control_L'):
                event(X.KeyRelease, name)
            connection.sync()
            connection.close()
        self.settle(.1)

    def xi2_conflict(self):
        from Xlib import X, XK, display
        from Xlib.ext import xinput
        connection = display.Display(self.physical)
        device = next(item.deviceid for item in connection.xinput_query_device(xinput.AllMasterDevices).devices
                      if item.use == xinput.MasterKeyboard)
        code = connection.keysym_to_keycode(XK.string_to_keysym('F11'))
        try:
            result = connection.screen().root.xinput_grab_keycode(device, X.CurrentTime, code,
                        xinput.GrabModeAsync, xinput.GrabModeAsync, False, xinput.KeyPressMask,
                        [X.ControlMask | X.ShiftMask])
            assert not result.modifiers
            session = self.create(self.app_b)
            request = self.bind(self.app_b, session, 'CTRL+SHIFT+F11', ident='xi2-conflict')
            self.pointer_allow()
            self.settle(.2)
            self.check('external-xi2-only-conflict-is-refused', not any(
                m.get('path') == request['handle'] and 'response' in m for m in self.app_b.messages))
            self.cancel_dialog()
            self.close_session(self.app_b, session)
        finally:
            connection.close()
        self.check('xi2-failure-rolls-back-core-reservation', self.free_grab('F11'))

    def history(self):
        a, b = self.app_a, self.app_b
        self.click('Pleb global shortcuts', 'alt+c')
        self.edit_trigger('Configure global shortcuts', 'CTRL+SHIFT+F10')
        self.click('Configure global shortcuts', 'alt+a')
        self.wait(lambda: bool(self.events(a, 'ShortcutsChanged')))
        self.check('configuration-list-describes-current-session',
                   self.listing(a, self.input_session)[0][1]['trigger_description'] == 'CTRL+SHIFT+F10')
        self.click('Pleb global shortcuts', 'alt+r')
        self.wait(lambda: any(m.get('event') == 'Closed' and m.get('session') == self.input_session for m in a.messages))
        self.check('revoke-frees-current-grant', self.free_grab('F10'))
        fresh = self.create(a)
        self.check('new-session-list-is-empty-despite-history', self.listing(a, fresh) == [])
        request = self.bind(a, fresh, 'CTRL+SHIFT+F9')
        self.window('Allow global shortcuts')
        self.check('pending-consent-list-has-no-registrations', self.listing(a, fresh) == [])
        before = len(self.events(a))
        self.click('Shortcut focus 1', 'Escape')
        self.key('ctrl+shift+F10')
        self.settle(.1)
        self.check('remembered-chord-needs-fresh-physical-consent', len(self.events(a)) == before and self.free_grab('F10'))
        result = self.approve(a, request['handle'])
        self.check('matching-id-prefills-previous-choice-in-consent-only',
                   result['results']['shortcuts'][0][1]['trigger_description'] == 'CTRL+SHIFT+F10')
        self.close_session(a, fresh)
        # Keep A's remembered matching ID present for this second-caller
        # control; changing A's ID first would make a shared-history bug pass.
        other = self.create(b)
        self.check('second-caller-new-session-list-is-empty', self.listing(b, other) == [])
        request = self.bind(b, other, 'CTRL+SHIFT+F11')
        result = self.approve(b, request['handle'])
        self.check('anonymous-second-caller-does-not-inherit-first-callers-choice',
                   result['results']['shortcuts'][0][1]['trigger_description'] == 'CTRL+SHIFT+F11')
        self.close_session(b, other)
        different = self.create(a)
        self.check('changed-id-fresh-session-list-is-empty', self.listing(a, different) == [])
        request = self.bind(a, different, 'CTRL+SHIFT+F9', ident='new-action')
        result = self.approve(a, request['handle'])
        self.check('changed-id-uses-current-proposal-without-historical-ids',
                   len(result['results']['shortcuts']) == 1 and result['results']['shortcuts'][0][0] == 'new-action'
                   and result['results']['shortcuts'][0][1]['trigger_description'] == 'CTRL+SHIFT+F9')
        previous = len(self.events(a, 'ShortcutsChanged'))
        self.click('Pleb global shortcuts', 'alt+c')
        self.edit_trigger('Configure global shortcuts', 'CTRL+SHIFT+F12')
        self.click('Configure global shortcuts', 'alt+a')
        self.wait(lambda: len(self.events(a, 'ShortcutsChanged')) > previous)
        listed = self.listing(a, different)
        self.check('reconfiguration-lists-current-id-and-current-chord-only', len(listed) == 1
                   and listed[0][0] == 'new-action' and listed[0][1]['trigger_description'] == 'CTRL+SHIFT+F12')
        self.current_session = different

    def retirement(self, timeout):
        if timeout:
            self.current_session = self.bind_new(self.app_a)
        record = next(Path(self.env['XDG_RUNTIME_DIR']).glob('pleb-shortcuts-*/sessions.json'))
        rows = json.loads(record.read_text())
        assert len(rows) == 1, rows
        path = rows[0][0]
        self.frontend.kill(signal.SIGSTOP)
        try:
            window = self.window('Pleb global shortcuts')
            self.run(['xdotool', 'windowactivate', '--sync', window])
            geometry = dict(line.split('=', 1) for line in self.run(
                ['xdotool', 'getwindowgeometry', '--shell', window]).splitlines())
            self.run(['xdotool', 'mousemove', '--sync', '--window', window,
                      str(int(geometry['WIDTH']) // 2), str(int(geometry['HEIGHT']) - 25)])
            self.run(['xdotool', 'click', '1'])
            # Prove the physical Revoke action was handled before inspecting
            # the acknowledgement object. A still-live object is not evidence
            # that retirement retained an acknowledgement target.
            self.wait(lambda: json.loads(record.read_text()) == [])
            introspect = ['gdbus', 'introspect', '--session', '--dest', BACKEND, '--object-path', path, '--xml']
            self.check('closed-session-acknowledgement-object-retained', IMPL.rsplit('.', 1)[0] + '.Session' in self.run(introspect))
            self.check('closed-session-retains-no-native-grabs', self.free_grab('F8' if timeout else 'F12'))
            denied = subprocess.run(['gdbus', 'call', '--session', '--dest', BACKEND, '--object-path', path,
                                     '--method', 'org.freedesktop.impl.portal.Session.Close'],
                                    env=self.env, capture_output=True, text=True, timeout=2)
            self.check('retirement-does-not-authorize-another-backend-caller',
                       denied.returncode != 0 and 'AccessDenied' in denied.stderr)
            if timeout:
                self.settle(2.15)
                self.check('unacknowledged-retirement-expires-within-short-bound',
                           'org.freedesktop.impl.portal.Session' not in self.run(introspect))
        finally:
            self.frontend.kill(signal.SIGCONT)
        self.wait(lambda: any(m.get('event') == 'Closed' and m.get('session') == self.current_session
                             for m in self.app_a.messages))
        if not timeout:
            self.settle(.2)
            self.check('frontend-close-acknowledgement-has-no-unknownmethod',
                       'UnknownMethod' not in (self.directory / 'frontend.log').read_text())
            self.settle(2.1)
            self.check('acknowledged-retirement-also-expires-within-short-bound',
                       'org.freedesktop.impl.portal.Session' not in self.run(introspect))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--relay', required=True)
    parser.add_argument('--retirement-only', action='store_true')
    args = parser.parse_args()
    fixture = FixFixture.__new__(FixFixture)
    fixture.processes, fixture.files, fixture.observations = [], [], []
    fixture.directory = Path(args.output)
    try:
        fixture.__init__(args)
        fixture.tests()
    finally:
        fixture.close()


if __name__ == '__main__':
    main()
