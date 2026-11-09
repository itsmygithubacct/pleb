# pleb — a kilix kiosk desktop session

How the Pleb login session starts Kilix, fills the screen, keeps graphical
applications in Kilix tabs and cleans up after itself. Desktop services such as
locking and screen capture are in [desktop-services.md](desktop-services.md).

**Pleb** turns [`kilix`](https://github.com/itsmygithubacct/kilix) (a Tilix-styled
[kitty](https://sw.kovidgoyal.net/kitty/) fork) into a **full desktop session**:
you log in and get a single screen-filling Kilix as the entire "desktop" — its
clickable page strip and pane controls stay visible, with no separate panel or
menus. A minimal Openbox underneath means browsers and
other GUI apps still get real, Alt-Tab-able windows. Rather than building a whole custom OS to
do that, Pleb adds **"Pleb" as one more choosable session** at your existing
LightDM login screen, so your normal desktop session is left untouched. The
session integration is removable; cloned checkouts and packages installed as
dependencies are intentionally retained for explicit cleanup.

The session tracks the window manager, engine, native-window observer, and
recovery terminal that it starts. Cleanup is armed before startup, including
no-WM kiosk mode. On normal exit or HUP/INT/TERM, it checks each recorded process
start time and direct-parent relationship, holds a Linux pidfd, requests TERM,
and escalates after two seconds before reporting any incomplete cleanup. Adopted
window managers and persistent broker sessions retain their existing lifetimes.
Owned-process supervision requires Python's Linux pidfd support. The native
observer must support `--parent-start-tick`; select the matching Kilix lifecycle
update with this session change. SIGKILL cannot run shell cleanup; private test
launchers additionally need process-namespace containment.

```
log out ──▶ LightDM greeter ──▶ pick "Pleb" ──▶ screen-filling Kilix
```

## How screen filling works (and the no-WM recovery detail)

Kilix uses kitty's native fullscreen state as an explicit **content-only**
mode: it hides the page strip and pane controls until F11 restores them. That
is useful on demand, but it makes a login session look like an ordinary shell.
Pleb therefore starts Kilix with
`--start-as=maximized -o hide_window_decorations=yes`: Kilix fills the display
without a redundant host title bar while its own clickable chrome remains
visible.

That is also what makes ordinary GUI applications usable. Without a WM nothing
owns focus, raising or stacking, so a screen-filling Kilix simply covers every other
client: a browser could be running, focused and permanently invisible. With
Openbox you get `Alt-Tab`, `Alt-Shift-Tab`, decorations for ordinary native
windows, `Alt-F4`, and a browser raised over Kilix.

New windows open on top but do not take the keyboard: whatever you are typing
keeps going to the window you were typing in, even when a background job, a
notification or a browser started by a script maps a window mid-keystroke.
Click a new window, or `Alt-Tab` to it, to type into it. An application that
explicitly asks to be activated can still take focus; Kilix itself does not do
that for a pane you are not using.

Pleb's Openbox is deliberately minimal — one desktop, no root menu, no panel,
no launcher or screenshot keys, and no rule pinning kilix `above` anything. The
profile lives at `share/openbox/rc.xml` and installs to
`/usr/local/share/pleb/openbox/rc.xml`.

Window decorations use Openbox's packaged dark Nightmare-02 theme. Notification
popups use xfce4-notifyd's dark Smoke theme with Tango blue action labels. The
installed override at `/usr/local/share/themes/Smoke/xfce-notify-4.0/gtk.css`
inherits the packaged theme without modifying it. At login, Pleb selects Smoke
for fresh notification settings and replaces the older `Default` setting; other
explicit notification theme choices are preserved. These defaults also apply
after an upgrade and the next Pleb login.

If Openbox is unavailable, `PLEB_WM=auto` (the default) falls back to the older
no-WM path: `pleb-session` sizes the kitty window to the whole screen in pixels
— a borderless window at `0,0` that fills the display. Verified: on a 1280×800
screen the window comes up `1280x800+0+0`. In that recovery mode a second
top-level window can still end up hidden behind kilix.

An EWMH window manager that is already running is always adopted, never
replaced — running `pleb session` on an existing desktop will not disturb it.

To make the screen-filling terminal feel larger or fit more rows/columns, adjust
Kilix's `font_size`:

```sh
pleb screen-size larger       # bigger text, fewer rows/columns
pleb screen-size smaller      # smaller text, more rows/columns
pleb screen-size reset
pleb screen-size set 13
```

### GUI applications stay in kilix tabs

Plain commands for installed graphical applications are routed through
`kilix run`, rendering each app inside a Kilix tab on its own private Xvfb:

```sh
chromium https://example.com            # automatically: kilix run chromium …
kilix run chromium https://example.com  # the equivalent explicit spelling
```

Kilix combines common Debian GUI names with visible non-terminal applications
from the installed XDG desktop catalogue. `KILIX_RUN_ALIASES=1` is the Pleb
default. Set it to `0` for native Openbox windows, extend the catalogue with
`KILIX_RUN_ALIAS_APPS="foo bar"`, or exempt commands with
`KILIX_RUN_ALIAS_EXCLUDE_APPS="foo"`.

At login Pleb prepends private command launchers to the session PATH, so scripts
and non-interactive shells get the same tab routing as interactive terminals.
This includes installed browsers, Evince, mpv, Kilix Amp, Cheese, GParted, XTerm,
and graphical QEMU. The PDF viewer's absolute Evince fallback is pointed at
the routed executable unless an explicit viewer override is set. Catalog GUI
applications also route to tabs even when their executable path is absolute.
Applications already inside a private Kilix display run
there directly; help/version, headless browser, headless QEMU and terminal mpv
modes retain their command-line behavior. Notifications, authentication agents
and power daemons remain desktop services.

Graphical QEMU `-daemonize` is converted to a foreground process owned by its
new tab, with a message explaining the change. Closing the tab ends the VM.
The launch command returns when the tab is created, not when the guest or its
PID file is ready; scripts requiring guest readiness must check it separately.
Headless QEMU retains its daemon flags.

The launchers are created on login and retained for persistent broker shells.
Newly installed applications enter the discovered catalogue on the next login.
An absolute executable path bypasses PATH routing; use `kilix run /path/to/app`
for such callers. Explicit native opt-outs remain supported. This policy does
not move existing native windows into tabs.

### Choosing the window manager

```sh
PLEB_WM=openbox pleb test    # require Pleb-managed Openbox
PLEB_WM=none pleb test       # no WM: the single-client screen-fill session
PLEB_WM=auto pleb test       # default: Openbox when available, else no WM
```

`PLEB_WM=openbox` is strict — a missing package, an unreadable profile or a
startup timeout fails the session rather than silently downgrading to the
hidden-window behaviour. A custom command (`PLEB_WM='evilwm -s'`) is still
supported and is held to the same readiness check.

Persist a setting in the session environment — `/etc/pleb/session.env` for the
whole machine, or your user `session.env` — rather than editing the installed
`/usr/local/bin/pleb-session`, which `pleb install` overwrites.

## Native-window diagnostic log

When a window manager is active, the session starts Kilix's
`scripts/native-window-log.py` using the system Python and stops it with the
session. The watcher writes private, rotating JSON lines under
`$PLEB_STATE_HOME/native-windows.jsonl` (normally
`~/.local/gpu_terminal/pleb/state/native-windows.jsonl`). Native window opens
and focus changes include available executable, argv, cwd, parent processes,
and WM_COMMAND so later versions can route those launch paths into Kilix.
It does not reroute or close windows. See Kilix's native-window routing
diagnostics documentation for capture limits and retention.
