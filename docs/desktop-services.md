# Desktop services

The services a normal Pleb login starts: the consent-based screen-capture
portal, locking, power and device controls, and frontend crash recovery.

Pleb supplies a consent-based X11 Screenshot and ScreenCast backend for
`xdg-desktop-portal`. Every capture asks the user to select a display or window;
screen sharing shows a **Stop sharing** control. Cancellation publishes no
video source, and closing the portal session tears down its producer. Saved
permissions are not accepted as automatic capture grants. In a logind desktop,
locking, switching away from the session or preparing for sleep closes active
capture and pending consent. Unlocking permits a new consent request and does
not restart sharing. A lost lock-state connection blocks capture. With desktop
services on, the guard follows the exact login session Pleb's locker reports
for, including a `startx` login, and blocks capture if that session is gone;
a nested X server without its own login is not lock-guarded.

Managed local application panes from the matching Kilix candidate also appear
as individual **Application pane** sources. Each includes that application's
private display and dialogs. Registration is bound to the live pane owner,
application, authenticated X server and physical desktop; it grants no capture
permission. Pane screenshots use an owned asynchronous helper, and capture
workers report source failure before native shutdown, so a frozen private
display cannot hold up cancellation or Stop sharing. Network broadcast sources
are not registered.

Plebian-OS supplies PipeWire and WirePlumber's `video-only` profile for this
transport, while PulseAudio continues to handle sound. Standalone Pleb uses
the host's existing PipeWire session manager; it installs the capture backend
without replacing another desktop's audio policy. The pane application's
private D-Bus connection uses the matching Kilix candidate's desktop portal
bridge. Native capture and private-app integration have component VM evidence;
Firefox ESR and Chromium also have bounded installed default-desktop evidence:
changing frames from a minimized application pane, cancellation, browser and
physical Stop sharing, and source closure. Chromium's shared pane includes its
native dialogs. Chromium requests both a preview session and a fresh capture
session; each has a system consent picker, so answer the second picker after
the browser's Share button. An owned single-output VM also passed manual lock,
pending-consent cancellation, normal PAM unlock and real ACPI S3 checks. Cold
activation on an inactive VT refused capture. Two selected application panes
delivered frames for two minutes; Stop, lock and source closure removed both
streams. A physical output mode change closed its old capture, and restoring
the mode did not resume sharing. These bounded checks leave mixed-DPI and
connector hotplug, longer browser sessions, physical hardware, selected-release
integration and fresh-install daily-use acceptance pending.
The capture transport is built locally by `pleb install` using
`libpipewire-0.3-dev` and `pkg-config`. It supplies descriptor-backed video
buffers for browser consumers; X11 acquisition remains in GStreamer.

Normal login sessions start supervised lock, idle-inhibition, power, PolicyKit,
Bluetooth, removable-media and input-method services. `pleb install` supplies
their Debian dependencies. Plebian-OS enables them for the normal desktop;
standalone Pleb's `PLEB_SESSION_SERVICES=auto` enables them in a logind login
session. Nested tests and hard kiosks retain their explicit session policy.
`PLEB_SESSION_SERVICES=on` requires the services, and `off` disables their startup.

Normal login sessions that own their window manager and run these services
restart a failed Kilix frontend while preserving the X session and service
processes. The persistent PTY broker can then reattach surviving jobs. A clean
frontend exit still ends the session. `PLEB_RECOVER_CRASHES=off` disables this
behavior; `on` explicitly enables it, and the default `auto` preserves the exit
behavior when desktop services are off or the window manager is adopted. Retry delays
increase after rapid failures, and five failures under ten seconds stop the
loop and offer a recovery terminal. With the matching native Kilix candidate,
an ordinary single-page startup reuses its surviving initial PTY instead of
launching a second default desktop. The association lasts for one login and
repeated crash retries; custom startup sessions retain their explicit commands.
Owned Debian VM checks of the coordinated candidates cover two native frontend
crashes with one surviving Kilix 95 desktop, exact unsaved-text saving, a live
PTY job and an interactive Terminal. Provider/application crashes, broader GUI
workflows and selected-release acceptance remain required. This does not
checkpoint application data; see [the recovery guide](RECOVERY.md).

**Super-L**, **Ctrl-Alt-L**, and `pleb lock` lock the physical session using
Debian's PAM-backed i3lock. A request from a private app display targets the
physical desktop. xss-lock obtains a logind sleep-delay inhibitor before the
window manager, the power manager or the workspace starts, so their lock keys
and the suspend lock all go through it and report the session as locked; if a
required service dies, the session ends. Idle locking
defaults to 600 seconds; `PLEB_IDLE_LOCK_SECONDS=0` disables the idle timeout.
The power manager's lock command is seeded only if the operator has not chosen one.
That setting persists in the user's Xfce configuration; outside a serviced Pleb
session `pleb-lock` runs i3lock directly rather than asking a coordinator that
is not there.

Display Settings and Kilix Power Controls both include a **Screen Lock** page. It
controls automatic locking on idle, display power-off and suspend/hibernate.
Choose **Disabled** to turn these automatic locks off; manual lock commands
remain available. Settings are per user and apply at the next login.
**Follow session defaults** keeps the existing session policy and idle timeout.
An explicit Enabled or Disabled choice also sets the power manager's sleep-lock
policy on login. The idle timeout is in seconds; 0 disables just the idle timer.

The same preferences are available in the terminal:

```sh
kilix tui screen-lock       # interactive settings
kilix tui screen-lock --show
kilix tui screen-lock --set off
kilix tui screen-lock --set on --idle-seconds 600
kilix tui screen-lock --set session  # follow the existing session defaults
```

`pleb screen-lock` offers the same TUI and options. Preferences are stored as
`screen-lock.json` in the user's Pleb configuration directory, alongside
`session.env`.

Kilix Settings → Tools and Kilix 95's Control Panel expose the same controls:

```sh
pleb devices power          # Kilix Power Controls: lid, battery, display and sleep policy
pleb devices bluetooth      # pairing and connections
pleb devices storage        # mount, unmount and eject
pleb devices printers       # physical printer setup and queues
pleb devices audio          # output devices and microphones
pleb devices input-method   # IBus languages and typing preferences
pleb devices accessibility  # Orca preferences
```

Controls render inside a Kilix pane while connecting to the physical desktop's
session bus. `--native` opens the control on the current display. IBus is the
default input method; `PLEB_INPUT_METHOD=auto` preserves a different selected
input framework, and `off` leaves input-method startup to the operator.
Orca's presence does not establish screen-reader support for the custom desktop
widgets; that integration and hardware acceptance remain open.
