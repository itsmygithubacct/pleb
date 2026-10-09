# Global shortcuts on Pleb's X11 desktop

Private application panes use the public GlobalShortcuts interface through
Kilix's existing per-caller portal relay. Pleb implements backend **version 1**,
matching xdg-desktop-portal 1.20.3's public version 1. CreateSession and the one
BindShortcuts attempt per session follow the public Request/Response protocol.
The physical GTK dialog identifies the requesting app, or explicitly says
"Unidentified application", and its connection. The user edits each proposed
XDG chord; **Cancel is the default**. No key is grabbed before Allow. Empty
fields disable shortcuts. Consent expires after 120 seconds; Request.Close,
session close and caller death remove it. IDs/descriptions are data, never
commands. A suitable example chord is `CTRL+SHIFT+F8`.

The **Pleb global shortcuts** window shows current grants. **Configure
shortcuts** applies changes atomically and emits ShortcutsChanged; a conflict
retains existing bindings. Blank fields disable bindings. **Revoke shortcuts**
or closing the window closes the session and releases grabs. Version 1 has no
public ConfigureShortcuts method; the desktop UI provides user configuration.
Bind/List results include only enabled grants and their descriptions/triggers.
Before binding, List is empty: it reports only registrations in the supplied
session. Previous choices held in backend memory may prefill matching requested
IDs in fresh consent, scoped to the frontend's app ID or the unique caller for
unidentified apps. These choices require new consent and disappear at restart.

Only the current public frontend can call implementation methods or Close.
Backend request/session handles must identify the same live desktop-user
caller. Backend signals are unicast to that frontend; the real frontend
unicasts public signals to the session owner. Kilix translates only the
caller's handles and relays signals only to that private client. Private X11
parent IDs are cleared by the existing relay, so consent is a standalone
physical window. Private displays never supply global input.

The service uses exact synchronous XI2 passive grabs for approved chords and
discovered lock modifier combinations, plus matching core-X11 reservations to
respect legacy clients' grabs. It subscribes to no root KeyPress stream or XI
raw input. The server freezes subsequent keyboard processing until dispatch;
the service ungrabs before thawing so already queued typing goes directly to
the focused window. If a core reservation activates during setup before XI2
is installed, it thaws input and closes the backend without emitting shortcut
events. Only granted XI2 presses deliver shortcut events. The server's
KeyRepeat flag suppresses autorepeat; a genuine new press completes any prior
cycle left active between state samples before starting its own activation.
Only while a granted chord is held, a bounded timer queries state and inspects its trigger and required
modifier bits to report release, including modifier-first release. Unrelated
key state is never retained or emitted. Event timestamps are monotonic
milliseconds. Mapping changes rebuild approved semantic chords using the
current key/modifier map. Unavailable, ambiguous or conflicting chords are
disabled and reported through ShortcutsChanged and the configuration UI.

CTRL/SHIFT/ALT/LOGO/NUM use the actual modifier map. Caps, Num and mapped Scroll
lock combinations are handled. Super+L, Ctrl+Alt+L, Alt+Tab, Alt+Shift+Tab and
Alt+F4 are reserved session actions. Ctrl+Alt+F1 through F12 and
Ctrl+Alt+BackSpace are reserved XKB/system actions. Other existing X grabs
produce a conflict rather than being replaced. The physical lock/active/sleep
guard closes sessions and consent; unlock never restores grants. Automatic
lock/lid policy and other capture portals are unchanged.

Caller disconnect, frontend replacement and graceful shutdown close sessions.
Closed implementation objects retain no grabs or consent but acknowledge Close
idempotently for two seconds, including after backend restart; at most 32
retirement objects are retained. Valid session creation refused while locked
or at capacity also provides this acknowledgement target for frontend cleanup.
Hard backend death releases grabs through X connection death. A private,
bounded XDG_RUNTIME_DIR record contains only session paths and frontend unique
names, allowing the replacement backend to close those stale public sessions.
It never restores grants or consent. Malformed or unsafe records fail closed.

Pleb installs `global_shortcuts.py` and `shortcut_keys.py` through its shared
portal module list, `pleb-shortcuts.portal`, and the activation service for
`org.freedesktop.impl.portal.desktop.pleb.shortcuts`. Its desktop preference
selects `GlobalShortcuts=pleb-shortcuts`, retaining GTK as the default. Serviced
logins supervise a directly launched backend with PID/start ownership, restart
at most once per five seconds, and reap it on exit. If activation already owns
the service, they verify its current bus owner, UID, PID/start, program path,
physical display/bus and a responsive version property instead of launching
duplicates. This external owner keeps its bus/physical-session lifetime and is
not adopted into direct-child cleanup. Lost or unresponsive activation is
rechecked before a supervised launch. D-Bus activation uses the published
physical environment. Existing python3-dbus,
python3-gi, python3-xlib, libxkbcommon0, GTK 3 and xdg-desktop-portal dependencies
cover the backend. OS provisioning and update rollback protect both modules,
the descriptor, activation service, and explicit frontend preference.

## Limits and verification

Support requires XInput 2, one enabled master keyboard, local X11, one XKB base
layout group, a uniquely mapped base key, and
a chord containing CTRL, ALT or LOGO. Multiple distinct groups, ambiguous
modifier slots, non-base key symbols, key sequences, pointer shortcuts and
Wayland are unsupported. Use base keys plus explicit modifiers. Limits are 16
live sessions, 16 requested shortcuts per session and 32 pending requests.
X11 does not isolate mutually hostile processes that already have direct
physical-display/session-bus access; this is scoped portal consent and event
delivery, not a new OS sandbox.

`tests/fixtures/global_shortcuts_fixture.py --output DIR --relay PATH` uses the
real public frontend, committed Kilix portal_bridge.py, authenticated owned
Xvfb displays, private buses, GTK consent, bare owned Openbox and native XTEST
input. Only logind is a protocol stand-in, on a disposable system bus. Set
PLEB_GLOBALSHORTCUTS_RELAY to a committed relay to enable this lane in unittest.
`global_shortcuts_fix1_fixture.py` adds queued chord/typing bursts, dispatch
delay, 12ms and zero-gap genuine press cycles, server autorepeat, history and
delayed acknowledgement controls. Its `--retirement-only` lane pauses the
owned frontend through the bounded acknowledgement timeout.
Real LightDM/Kilix guest installation, physical keyboard/lock/suspend, other
toolkits, screen readers and language workflows need separate acceptance;
Xvfb/layout checks establish none of those physical workflows.

Primary references: [v1 public interface](https://github.com/flatpak/xdg-desktop-portal/blob/1.20.3/data/org.freedesktop.portal.GlobalShortcuts.xml),
[v1 backend interface](https://github.com/flatpak/xdg-desktop-portal/blob/1.20.3/data/org.freedesktop.impl.portal.GlobalShortcuts.xml),
[frontend ownership and signal routing](https://github.com/flatpak/xdg-desktop-portal/blob/1.20.3/src/global-shortcuts.c),
[XDG shortcut syntax](https://specifications.freedesktop.org/shortcuts/latest/),
and [XI2 passive grabs and KeyRepeat](https://cgit.freedesktop.org/xorg/proto/inputproto/tree/specs/XI2proto.txt).
