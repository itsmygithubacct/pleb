# pleb — a kilix kiosk desktop session

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

```
log out ──▶ LightDM greeter ──▶ pick "Pleb" ──▶ screen-filling Kilix
```

## Layout

```
~/.local/gpu_terminal/sources/pleb/
├── bin/
│   ├── pleb            # the CLI: install / uninstall / test / settings / status / doctor
│   └── pleb-session    # the X session entrypoint (self-contained; installed to /usr/local/bin)
├── lib/
│   ├── common.sh       # shared helpers (paths, sudo, logging)
│   ├── storage.sh      # private writable-data validation/reconciliation
│   ├── install.sh      # install/uninstall the LightDM session
│   ├── autologin.sh    # kiosk autologin on/off
│   ├── kiosk.sh        # hard-kiosk session setting
│   ├── update.sh       # locked, fast-forward-only component updates
│   └── test.sh         # safe nested/spare-VT testing
├── share/
│   └── pleb.desktop.in # xsession entry template (installed to /usr/share/xsessions)
├── scripts/
│   ├── install-go.sh   # install/upgrade Go from go.dev (for the kilix fork build)
│   └── validate-artwork.py # exact wallpaper/attribution/license validator
├── docs/
│   └── RECOVERY.md     # dependency/update recovery commands
├── assets/             # approved Plebian wallpaper + attribution and GPL-2 text
└── README.md
```

The engine is a kilix checkout at `~/.local/gpu_terminal/sources/kilix`. You don't need to set it up
yourself — **`pleb install` clones kilix from upstream if it isn't already
there** and fetches a prebuilt kitty so it runs immediately. The clickable-button
**fork** (`~/.local/gpu_terminal/kilix/build/current/src/kitty/launcher/kitty`)
is built on demand later with `pleb update` or `~/.local/gpu_terminal/sources/kilix/kilix --build` (needs
Go ≥ 1.26). Generated binaries and objects never land in the Kilix source tree.

A standalone unpinned install prints the exact asset URL and asks for explicit
consent before accepting it without a checksum. Configured prebuilt pins are
revalidated even when an engine is already runnable. For automation, set
`KILIX_PREBUILT_VERSION` and `KILIX_PREBUILT_SHA256` together; Plebian-OS
manifests always supply a verified pair.

`pleb install` also initializes and builds Kilix's pinned persistent PTY broker,
then installs the single pinned `kilix-tui-utils` checkout (including Temps,
Memory, and the Music front end), the tmux-tui source closure, and the pinned
Media Player. Music drives kilix-amp over its control socket rather than
decoding anything itself, and starts a `kilix-amp --headless` backend when none
is listening.

The Media Player is built here so nobody waits for a compile the first time
they open it, but it is not a precondition for a working session: if the SDL,
libsndfile and FluidSynth development libraries are absent, `pleb install`
warns and Kilix builds the player on demand instead, from `kilix amp` or the
first time the Media Player or the Music tool is opened. The unified installer publishes
`kilix-tui` and its utility commands under `~/.local/bin`; Pleb also symlinks
`kilix`, `kilix-settings`, `kilix-temps`, `tmux-tui`, and tmux-cli's `tb.py` as `tb`
onto the system `PATH` (`/usr/local/bin` by default). The Kilix TUI
desktop, `kilix pty` session manager, dashboards, and Tmux Manager are
therefore ready when a desktop first uses them, without relying on separate
per-dashboard source caches.

The install also selects Kilix's immutable voice closure. The standalone
default, `PLEB_INSTALL_VOICE_MODEL=0`, installs its pinned read-aloud runtime
without the speech library or acoustic model. Setting
`PLEB_INSTALL_VOICE_MODEL=1` requires the complete checksum-pinned dictation
closure; a failed full install returns nonzero and never silently falls back to
read-aloud-only.

For a standalone install, Pleb validates and copies the approved Plebian
wallpaper to
`~/.local/gpu_terminal/pleb/data/wallpapers/plebian-os.png`, with its exact
attribution and GPL version 2 text below the adjacent `data/doc/` tree. Install
atomically creates Pleb's isolated `$KILIX_DESKTOP_DIR/.state.json` only if it
is absent—even when desktop launch is currently disabled, so enabling it later
needs no reinstall. `KILIX_DESKTOP_DIR` defaults to
`$PLEB_DATA_HOME/desktop` and is forwarded to the external or bundled
Kilix-95-compatible provider;
provider-global `$KILIX_DATA_HOME/desktop` and `$KILIX95_DATA_HOME/desktop`
state is never touched. Existing Pleb state—including a symlink or custom
wallpaper—is never replaced. A Plebian-OS provisioned install passes
`PLEBIAN_OS_MANAGED_INSTALL=1` and instead owns the identical wallpaper and
desktop-state location as part of the distribution. Direct Kilix-95 XP sessions
therefore retain their kittens-fire wallpaper default.

## Requirements

- A **LightDM**-based Linux desktop with **Xorg** (`startx`/`xinit`).
- **git**, **curl**, **tar**, and a C build toolchain (to clone Kilix, build the
  small soft-raster library used by Kilix Temps, and fetch the terminal engine).
- `sudo` for `install` / `autologin` (system files only).
- On Debian/Ubuntu, `pleb install` installs Pleb's runtime packages with apt,
  including `build-essential`, NetworkManager's `nmtui` for the top-bar
  network/Wi-Fi widget, `pulsemixer` for its volume widget, and the
  FluidSynth/SoundFont runtime used by kilix-amp MIDI playback (the same
  runtime its headless backend decodes with).
  Before a fork build, `pleb update` runs Kilix's own complete cross-distro
  dependency verifier and installer (including the `libxxhash` pkg-config
  module). Set `PLEB_SKIP_DEPS=1` to prevent package-manager changes; an update
  then fails clearly if Kilix reports a missing build prerequisite.
- Optional: `xserver-xephyr` for nested `pleb test`; Go ≥ 1.26 for the fork.

## Quick start

```sh
~/.local/gpu_terminal/sources/pleb/bin/pleb install  # clone kilix + engine, add "Pleb" to LightDM (asks for sudo)
~/.local/gpu_terminal/sources/pleb/bin/pleb doctor   # check prerequisites (engine, X, greeter)
~/.local/gpu_terminal/sources/pleb/bin/pleb test     # try it in a throwaway X server — nothing permanent
```

Then **log out** of your current desktop, and at the LightDM greeter open the
session menu (the little badge/gear near the password box), choose **Pleb**, and
log in. To go back, log out and pick your usual session again.

> Add `~/.local/gpu_terminal/sources/pleb/bin` to your `PATH` (or alias
> `pleb=~/.local/gpu_terminal/sources/pleb/bin/pleb`) to drop the
> full path.

## The `pleb` CLI

| Command | What it does |
|---|---|
| `pleb doctor` | Check the engine, X tools, and greeter are ready. |
| `pleb test [opts]` | Launch the session in a **throwaway** X server (see below). |
| `pleb install` | Clone Kilix + engine, install pinned unified terminal utilities, put their commands on `PATH`, and add "Pleb" to LightDM. *(sudo)* |
| `pleb uninstall` | Remove both, and any autologin config. *(sudo)* |
| `pleb autologin on [user]` | Boot straight into Pleb — no greeter (kiosk). *(sudo)* |
| `pleb autologin off` | Revert to the normal greeter. *(sudo)* |
| `pleb kiosk on` / `off` | Hard kiosk: respawn kilix if it exits (or don't). *(no sudo)* |
| `pleb update [-y] [--no-restart\|--restart]` | Update clean checkouts, rebuild the fork, and optionally restart an active kiosk. |
| `pleb status` | Show the effective persisted engine / desktop / install / autologin / kiosk state. |
| `pleb screen-size ...` | Show, increase, decrease, reset, or set Kilix terminal scale. |
| `pleb settings` | Toggle Kilix's top-bar widgets, pane-title buttons, and session logging. |
| `pleb session` | Exec the session now, against the current `$DISPLAY`. |

`install`, `uninstall`, and `autologin` need root; the CLI calls `sudo` only for
the specific file operations, so you'll be prompted once.

Set `PLEB_DESKTOP=1 KILIX_DESKTOP_PROVIDER=xp` to boot the Pleb login session
into Kilix XP. Use `cap` for the optional Kilix Cap mansion or `tui` for the
optional text-native Kilix TUI desktop, and `land` for the walkable Kilix Land
desktop. Kilix prepares each from its own immutable pin; the release-default
Kilix 95 provider remains available alongside them.

For a persistent per-user XP login desktop, put these two lines in
`~/.local/gpu_terminal/pleb/config/session.env`, run `pleb install` if
`pleb status` reports a stale launcher, then log out and select **Pleb**:

```sh
PLEB_DESKTOP=1
KILIX_DESKTOP_PROVIDER=xp
```

`KILIX_DESKTOP_FLAVOR=xp` in Kilix’s runtime config only chooses the first-run
look of a Kilix 95 provider that is already being launched. It does not enable
the Pleb desktop session; `PLEB_DESKTOP=1` is the switch that replaces the
plain Kilix shell with `kilix desktop`.

**Session logging is on by default.** Every pane's output is recorded by the
PTY broker to `~/.local/gpu_terminal/kilix/state/transcripts/<session>.log`
(`0600`, one bounded 8 MiB log per pane, kitty graphics payloads elided).
`pleb status` reports the current policy and how many logs exist; `kilix
transcript` lists and prints them. Turn it off with
`pleb settings --set transcript=off`, or in the Session logging section of the
settings TUI.

`pleb settings` and Kilix 95's Settings menu edit the same non-executable
`~/.local/gpu_terminal/settings.conf`. It is the source of truth for the
thermometer, volume, network, calendar, date/time, battery, font-size, four-way
split, maximize, and close controls; changes are reflected by running Kilix
windows. The thermometer defaults to off; enable **Thermal status** in the TUI
or run `pleb settings --set temperature=on`. It colors the hottest readable
sensor green/yellow/red and opens the installed or unified-source
`kilix-temps` in a new tab when clicked.

## Testing without risking your desktop

`pleb test` never touches your live session:

```sh
pleb test                 # auto: nested Xephyr window if you're in a desktop,
                          #       else a real X server on a spare VT
pleb test --xephyr        # force a nested window on the current $DISPLAY
pleb test --vt 9          # force a real X on vt9 — view it with Ctrl+Alt+F9
pleb test --check         # non-interactive: bring it up, verify kilix, tear down
pleb test --vt 9 --check --secs 8
```

- **Xephyr** mode opens kilix inside a window on your current desktop — safest,
  no VT switching. Needs `xserver-xephyr`.
- **VT** mode starts a second, independent X server on a spare virtual terminal
  (default vt9). Switch to it with `Ctrl+Alt+F9`; your desktop stays on its own VT.
- `--check` is what CI/verification uses: it confirms kilix comes up, prints a
  PASS/FAIL plus log tails, then cleans everything up.

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

Pleb's Openbox is deliberately minimal — one desktop, no root menu, no panel,
no launcher or screenshot keys, and no rule pinning kilix `above` anything. The
profile lives at `share/openbox/rc.xml` and installs to
`/usr/local/share/pleb/openbox/rc.xml`.

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

## Kiosk autologin

To make the machine boot straight into the Pleb desktop with no login prompt:

```sh
pleb install            # first
pleb autologin on       # writes /etc/lightdm/lightdm.conf.d/50-pleb-autologin.conf
                        # and adds you to the group LightDM's PAM autologin requires
# reboot, or: sudo systemctl restart lightdm
```

⚠️ **Keep an escape hatch.** With autologin on, if kilix ever misbehaves you can
still switch to a rescue console with `Ctrl+Alt+F2` (a plain getty) and run
`pleb autologin off`. Turn it off any time with:

```sh
pleb autologin off
```

## Hard kiosk (respawn on exit)

By default, when kilix exits the X session ends (LightDM returns to the greeter,
or re-autologins). For a **hard kiosk** where kilix is relaunched if it ever
exits — so there's no way to "escape" to a bare X server by closing it — turn on
respawn:

```sh
pleb kiosk on       # kilix respawns if it exits
pleb kiosk off      # back to: kilix exit ends the session
sudo systemctl restart lightdm   # apply now (or it takes effect next login)
```

`pleb kiosk on` (no sudo) writes `PLEB_RESPAWN=1` to
`~/.local/gpu_terminal/pleb/config/session.env`, which `pleb-session` sources at startup. `off`
writes an explicit `0`, so it can override a system-wide default. Values passed
in the real process environment still win (this keeps `pleb test`, which passes
`PLEB_RESPAWN=0`, deterministic). You can put any knob from the table below in
that file or in `/etc/pleb/session.env`; the CLI and session use the same system
then user precedence.

These are shell environment files, so only place trusted content in them. If
the CLI itself is invoked as root, it refuses to source a file that is not
root-owned or is writable by group/other users.

## Updating kilix

```sh
pleb update              # fetch latest kilix, ff-only, rebuild the fork, offer restart
pleb update -y           # ...and restart an active Pleb kiosk without asking
pleb update --restart    # explicitly restart an active kiosk; never prompts
pleb update --no-restart # update only; leave LightDM alone
```

`pleb update` fast-forwards or pins `~/.local/gpu_terminal/sources/kilix`, updates the optional
`~/.local/gpu_terminal/sources/kilix-desktops/kilix-95`
desktop checkout when the selected provider needs it, reconciles the Kilix-pinned
thermal dashboard and graphics libraries, installs the configured Go toolchain
when necessary, and rebuilds the fork. It only offers to restart
LightDM when Pleb is configured as the active kiosk/autologin session. Updates
are serialized with an XDG-state lock and refuse any checkout with tracked or
untracked local changes. Before changing either checkout, Pleb snapshots both
component positions, the dashboard executable/native library, the fork engine,
and their build stamps. Any component or build failure restores that coherent
pre-update state. Updates never
force-update: if a branch cannot
fast-forward, the command stops and tells you. A configured `KILIX_REF` or
`KILIX95_REF` must be a full 40-character commit SHA, is fetched from `origin`,
resolved through `FETCH_HEAD`, checked out detached, and verified; local tags
are never trusted as the source of a release pin. Mutable refs require the
corresponding explicit `*_ALLOW_MUTABLE_REF=1` trust override.

The Plebian-OS updater passes its already-held copy of this same lock through an
inherited file descriptor. Pleb validates and borrows that lock without
releasing the parent updater's ownership.

The canonical fork-build stamp is stored at
`~/.local/gpu_terminal/kilix/state/fork-built-ref`, beside the Kilix generation
it describes, and never inside a source checkout or duplicated under Pleb
state. Pleb snapshots and restores that same Kilix-owned stamp during updates,
and transactionally retires the legacy Pleb-side duplicate when encountered.
Pleb-owned configuration, cache, persistent state, live session files, and
durable data use the
`~/.local/gpu_terminal/pleb/{config,state,cache,session,data}` layout.
Install (including a Plebian-OS-managed install), update, testing, kiosk changes,
the Go fetcher, and every login reconcile the component root and all five
categories as user-owned, non-symlink `0700` directories while preserving their
contents. Path overrides must be normalized absolute paths and each category
must remain a strict descendant of `PLEB_STORAGE_HOME`; unsafe, broad, linked,
or escaped layouts are refused before any directory mode is changed. A missing
intermediate in a custom nested category path is created as `0700`; an existing
intermediate is validated as user-owned, non-linked, and not group/world
writable, but is not itself a configured category boundary and is not chmodded.
The login
launcher keeps `state/session.log` as `0600` and rotates a log larger than 1 MiB
to `session.log.1` at the next login.

## Environment and persisted configuration knobs

The session consumes the display/desktop values; `pleb install`, `update`, and
`status` also read their relevant values from the same persisted files.

| Variable | Default | Meaning |
|---|---|---|
| `GPU_TERMINAL_SOURCE_HOME` | `$HOME/.local/gpu_terminal/sources` | Shared root for source checkouts. |
| `GPU_TERMINAL_HOME` | `$HOME/.local/gpu_terminal` | Shared root for writable application data. |
| `GPU_TERMINAL_SETTINGS_FILE` | `$GPU_TERMINAL_HOME/settings.conf` | Shared clickable-chrome source of truth. |
| `PLEB_STORAGE_HOME` | `$GPU_TERMINAL_HOME/pleb` | Private Pleb configuration, state, cache, session, and data root. |
| `PLEB_CONFIG_HOME` | `$PLEB_STORAGE_HOME/config` | Pleb persisted configuration. |
| `PLEB_STATE_HOME` | `$PLEB_STORAGE_HOME/state` | Pleb logs, locks, and durable state. |
| `PLEB_CACHE_HOME` | `$PLEB_STORAGE_HOME/cache` | Pleb disposable cache. |
| `PLEB_SESSION_HOME` | `$PLEB_STORAGE_HOME/session` | Pleb live-session scratch space. |
| `PLEB_DATA_HOME` | `$PLEB_STORAGE_HOME/data` | Pleb-owned wallpaper, attribution, and other durable data. |
| `KILIX_STORAGE_HOME` | `$GPU_TERMINAL_HOME/kilix` | Kilix writable-data root. |
| `KILIX_CONFIG_HOME` | `$KILIX_STORAGE_HOME/config` | Kilix persisted configuration. |
| `KILIX_STATE_DIRECTORY` | `$KILIX_STORAGE_HOME/state` | Kilix durable state. |
| `KILIX_CACHE_HOME` | `$KILIX_STORAGE_HOME/cache` | Kilix disposable cache. |
| `KILIX_SESSION_HOME` | `$KILIX_STORAGE_HOME/session` | Kilix live-session scratch space. |
| `KILIX_DATA_HOME` | `$KILIX_STORAGE_HOME/data` | Bundled Kilix desktop data and state. |
| `KILIX_TRANSCRIPT_DIR` | `$KILIX_STATE_DIRECTORY/transcripts` | Per-pane session logs, mode `0700`/`0600`. |
| `KILIX_BUILD_DIRECTORY` | `$KILIX_STORAGE_HOME/build` | Generated Kilix fork builds. |
| `KILIX_PREBUILT_HOME` | `$KILIX_STORAGE_HOME/prebuilt/kitty.app` | Downloaded, verified Kilix engine bundle path. |
| `KILIX95_STORAGE_HOME` | `$GPU_TERMINAL_HOME/kilix-95` | Kilix 95 writable-data root. |
| `KILIX95_CONFIG_HOME` | `$KILIX95_STORAGE_HOME/config` | Kilix 95 persisted configuration. |
| `KILIX95_STATE_HOME` | `$KILIX95_STORAGE_HOME/state` | Kilix 95 durable state. |
| `KILIX95_CACHE_HOME` | `$KILIX95_STORAGE_HOME/cache` | Kilix 95 disposable cache. |
| `KILIX95_SESSION_HOME` | `$KILIX95_STORAGE_HOME/session` | Kilix 95 live-session scratch space. |
| `KILIX95_DATA_HOME` | `$KILIX95_STORAGE_HOME/data` | External Kilix-95 desktop data and state. |
| `KILIX_DESKTOP_DIR` | `$PLEB_DATA_HOME/desktop` | Pleb-isolated `.state.json` forwarded to the external or bundled Kilix-95-compatible provider. |
| `KILIX_DIR` | `$GPU_TERMINAL_SOURCE_HOME/kilix` | Kilix engine checkout. |
| `KILIX` | `$KILIX_DIR/kilix` | Path to the kilix launcher. |
| `KILIX_BRANCH` | *(repo default)* | Optional Kilix branch for install/update. |
| `KILIX_REF` | *(none)* | Optional full 40-character Kilix commit SHA for install/update. |
| `KILIX_ALLOW_MUTABLE_REF` | `0` | Explicitly trust a mutable tag/branch in `KILIX_REF`. |
| `KILIX_PREBUILT_VERSION` | *(latest)* | Optional exact fallback kitty version for Kilix bootstrap. |
| `KILIX_PREBUILT_SHA256` | *(none)* | Optional checksum for the pinned fallback kitty bundle. |
| `PLEBIAN_OS_KILIX_GO_MIN_VERSION` | `1.26` | Minimum Go version accepted for a Kilix fork build. |
| `PLEBIAN_OS_KILIX_GO_VERSION` | *(none)* | Exact Go release, such as `go1.26.4`; requires the matching architecture hash. |
| `PLEBIAN_OS_KILIX_GO_SHA256_AMD64` | *(none)* | Trusted SHA-256 for the pinned Linux amd64 Go archive. |
| `PLEBIAN_OS_KILIX_GO_SHA256_ARM64` | *(none)* | Trusted SHA-256 for the pinned Linux arm64 Go archive. |
| `PLEB_SKIP_DEPS` | `0` | If `1`, skip package installation during `pleb install`/`update`; update still verifies and fails if prerequisites are missing. |
| `PLEB_INSTALL_VOICE_MODEL` | `0` | `0` installs the pinned read-aloud-only closure; `1` requires the complete pinned dictation library and model and fails instead of degrading. |
| `PLEB_KILIX_ARGS` | auto | Args passed to Kilix; unset means maximized with host decorations hidden when a WM is present, fixed screen-fill sizing without one. |
| `PLEB_WM` | `auto` | Window-manager policy: `auto`, `openbox` (required), `none`/`off`, or a custom command. |
| `PLEB_OPENBOX_CONFIG` | `/usr/local/share/pleb/openbox/rc.xml` | Openbox profile Pleb starts Openbox with. |
| `PLEB_WM_TIMEOUT` | `5` | Seconds to wait for the window manager to own the display. |
| `KILIX_RUN_ALIASES` | `1` | `1` sends installed GUI commands to `kilix run`; `0` opts into native Openbox windows. |
| `KILIX_RUN_ALIAS_APPS` | *(none)* | Extra GUI command names to route through `kilix run`. |
| `KILIX_RUN_ALIAS_EXCLUDE_APPS` | *(none)* | GUI command names to leave as native X11 clients. |
| `PLEB_NO_FILL` | `0` | Skip the no-WM screen-fill sizing. |
| `PLEB_BG` | `#101010` | Root-window solid colour. |
| `PLEB_RESPAWN` | `0` | If `1`, relaunch kilix when it exits (hard kiosk). |
| `PLEB_DESKTOP` | `0` | If truthy, boot directly into `kilix desktop`; `0` gives a plain shell. |
| `KILIX_DESKTOP_PROVIDER` | `auto` | Prefer a compatible installed external provider, else bundled; or force `builtin`, `external`, `xp`, `cap`, `tui`, `land`, `command`, or `none`. |
| `KILIX_DESKTOP_COMMAND` | *(none)* | Shell command run by `kilix desktop` when provider is `command`. |
| `KILIX_DESKTOP_NAME` | `desktop` | Label/tab title for custom desktop providers. |
| `KILIX_DESKTOP_FLAVOR` | *(provider default)* | First-run Kilix 95 appearance, `95` or `xp`; this does not enable a Pleb desktop session by itself. |
| `KILIX_CAP_AUTO_INSTALL` | `1` | Lets Kilix download and build Kilix Cap on first launch. |
| `KILIX_CAP_DIR` | `$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-cap` | Kilix Cap source checkout. |
| `KILIX_CAP_REPO` | `https://github.com/itsmygithubacct/kilix-cap.git` | Reviewed Kilix Cap source remote. |
| `KILIX_CAP_REF` | *(Kilix-pinned commit)* | Optional exact Kilix Cap commit override. |
| `KILIX_CAP_TRUST_EXISTING_CHECKOUT` | `0` | Trust a nonstandard existing Cap checkout only when explicitly enabled. |
| `KILIX_CAP_ALLOW_MUTABLE_REF` | `0` | Trust a mutable Cap tag/branch only when explicitly enabled. |
| `KILIX_TUI_UTILS_AUTO_INSTALL` | `1` | Lets Kilix download and install Kilix TUI and its utilities on first use. |
| `KILIX_TUI_UTILS_DIR` | `$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-tui-utils` | Checkout containing the Kilix TUI desktop and terminal utilities. |
| `KILIX_TUI_UTILS_REPO` | `https://github.com/itsmygithubacct/kilix-tui-utils.git` | Reviewed Kilix TUI source remote. |
| `KILIX_TUI_UTILS_REF` | *(Kilix-pinned commit)* | Optional exact Kilix TUI utilities commit override. |
| `KILIX_TUI_UTILS_TRUST_EXISTING_CHECKOUT` | `0` | Trust a nonstandard existing TUI checkout only when explicitly enabled. |
| `KILIX_TUI_UTILS_ALLOW_MUTABLE_REF` | `0` | Trust a mutable TUI tag/branch only when explicitly enabled. |
| `KILIX_LAND_DESKTOP_AUTO_INSTALL` | `1` | Lets Kilix download and build Kilix Land on first launch. |
| `KILIX_LAND_DESKTOP_DIR` | `$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-land-desktop` | Kilix Land source checkout and runtime asset root. |
| `KILIX_LAND_DESKTOP_REPO` | `https://github.com/itsmygithubacct/kilix-land-desktop.git` | Reviewed Kilix Land source remote. |
| `KILIX_LAND_DESKTOP_REF` | *(Kilix-pinned commit)* | Optional exact Kilix Land commit override. |
| `KILIX_LAND_DESKTOP_TRUST_EXISTING_CHECKOUT` | `0` | Trust a nonstandard existing Land checkout only when explicitly enabled. |
| `KILIX_LAND_DESKTOP_ALLOW_MUTABLE_REF` | `0` | Trust a mutable Land tag/branch only when explicitly enabled. |
| `KILIX_LAND_DESKTOP_ASSETS` | *(checkout root)* | Optional runtime asset-root override. |
| `KILIX_LAND_DESKTOP_CONFIG_HOME` | *(XDG-derived)* | Optional absolute profile-store override. |
| `KILIX_LAND_DESKTOP_EXTERNAL_APPS` | `1` | Set to `0` to disable launches from the walkable desktop. |
| `KILIX_LAND_DESKTOP_AUDIO` | `1` | Set to `0` to mute the walkable desktop. |
| `KILIX95_AUTO_INSTALL` | `1` | Lets `kilix desktop` clone external Kilix 95 when needed. |
| `KILIX95_DIR` | `$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-95` | External Kilix 95 checkout used for desktop sessions. |
| `KILIX95_REPO` | `https://github.com/itsmygithubacct/kilix-95.git` | Repo cloned when Kilix 95 is needed. |
| `KILIX95_BRANCH` | *(repo default)* | Optional Kilix 95 branch. |
| `KILIX95_REF` | *(none)* | Optional exact Kilix 95 commit/tag. |
| `KILIX95_ALLOW_MUTABLE_REF` | `0` | Explicitly trust a mutable tag/branch in `KILIX95_REF`. |
| `KILIX95_ALLOW_UNPINNED_INSTALL` | `0` | Explicitly allow an automatic external-provider clone without `KILIX95_REF`. |
| `PLEB_LOG` | `~/.local/gpu_terminal/pleb/state/session.log` | Session log. |

Use `PLEB_DESKTOP=0` or `KILIX_DESKTOP_PROVIDER=none` for no desktop at all.
For example, boot directly into Kilix Land through the same named provider
facade:

```sh
PLEB_DESKTOP=1 \
KILIX_DESKTOP_PROVIDER=land
```

## Uninstall the session integration

```sh
pleb autologin off      # if you enabled it
pleb uninstall          # removes session integration and Pleb/Kilix command links
```

`~/.local/gpu_terminal/sources/pleb`, `~/.local/gpu_terminal/sources/kilix`, optional checkouts under
`~/.local/gpu_terminal/sources/kilix-desktops`, `~/.local/gpu_terminal`, and packages installed as
dependencies are left in place; remove them explicitly if you want them gone.

## Notes & limitations

- **Engine: the kilix fork** (with clickable `+ - ← ↑ ↓ → ▢ ✕` pane buttons) builds to
  `~/.local/gpu_terminal/kilix/build/current/src/kitty/launcher/kitty` and is
  what `kilix` uses. Building it needs
  **Go ≥ 1.26**; if your distro ships an older Go, install a newer toolchain with
  [`scripts/install-go.sh`](scripts/install-go.sh). If no fork binary is present,
  kilix falls back to a prebuilt kitty (a working terminal, no buttons); `pleb
  install` sets that up automatically, and `~/.local/gpu_terminal/sources/kilix/kilix --build` (or `pleb
  update`) produces the fork once Go is new enough. Immediately before
  `kilix --build`, `pleb update` delegates to Kilix's
  `scripts/install-build-deps.sh`: it verifies the complete current manifest,
  installs when verification fails, and verifies again. This avoids a stale
  duplicate package list in Pleb and covers newly added modules such as
  `libxxhash`. If an older install fails at that check, follow the concise
  [recovery guide](docs/RECOVERY.md); `pleb install` also publishes it at
  `/usr/local/share/doc/pleb/RECOVERY.md` for the desktop Help menu.
- **Upgrading Go later:** `~/.local/gpu_terminal/sources/pleb/scripts/install-go.sh` defaults to the latest
  stable release from go.dev, or accepts an exact version such as
  `install-go.sh go1.26.4`. For a reproducible install, set both `GO_VERSION`
  and `GO_SHA256`; a supplied hash is used directly without querying live
  checksum metadata. Both `fetch` and direct `install` validate the cache before
  use. The default cache is private below `PLEB_CACHE_HOME`; an explicit external
  `GO_CACHE` must be a normalized absolute path with trusted, non-symlink,
  non-group/world-writable ancestry and a user-owned `0700` leaf. `fetch` is
  unprivileged. Direct `install` independently checks the manifest against a
  configured trusted SHA-256 or the official checksum from go.dev. A configured
  `GO_SHA256` is an intentional operator-supplied trust anchor; offline use
  therefore requires that pin. Official metadata and privileged staging use
  isolated system tools and a sanitized command environment. It then copies the
  archive into a root-owned staging directory, re-verifies and validates it,
  and swaps it into `/usr/local/go`; a failed swap restores the previous tree
  and command links. The install and command-link destinations are deliberately
  fixed at `/usr/local/go` and `/usr/local/bin`; their parent chain must be
  root-owned, non-symlink, and not group/world writable before staging begins.
  The installed tree contains a
  root-owned `.pleb-source` provenance stamp; pinned Pleb updates reinstall Go if
  that exact version/architecture/hash record is absent or mismatched.
- Single-monitor sizing is captured at launch; if you hot-plug a monitor or
  change resolution, restart the session (or use `PLEB_WM` for dynamic sizing).

## Credits

Pleb is just glue around [**kilix**](https://github.com/itsmygithubacct/kilix) —
the Tilix-styled kitty fork that does the real work. The idea is to make kilix
the *whole* desktop instead of running it inside one, delivered here as a
**login-session option on an existing LightDM desktop** rather than as a custom
OS image.

## License

Pleb's code is released under the [MIT License](LICENSE). It is a standalone set of
scripts that *invoke* kilix at runtime — it does not include or link kilix/kitty
code (it clones kilix separately on install). kilix and kitty are licensed under
the **GPLv3** by their respective authors. The bundled Plebian wallpaper is a
separate GPL-2.0-or-later artwork distribution; its preserved notices and the
complete GPL version 2 text are in [`assets/`](assets/README.md).
