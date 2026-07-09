# pleb — a kilix kiosk desktop session

**Pleb** turns [`kilix`](https://github.com/itsmygithubacct/kilix) (a Tilix-styled
[kitty](https://sw.kovidgoyal.net/kitty/) fork) into a **full desktop session**:
you log in and get a single fullscreen kilix as the entire "desktop" — no panel,
no window manager, just the terminal. Rather than building a whole custom OS to
do that, Pleb adds **"Pleb" as one more choosable session** at your existing
LightDM login screen, so your normal desktop session is left completely
untouched and everything here is reversible.

```
log out ──▶ LightDM greeter ──▶ pick "Pleb" ──▶ fullscreen kilix
```

## Layout

```
~/pleb/
├── bin/
│   ├── pleb            # the CLI: install / uninstall / test / autologin / status / doctor
│   └── pleb-session    # the X session entrypoint (self-contained; installed to /usr/local/bin)
├── lib/
│   ├── common.sh       # shared helpers (paths, sudo, logging)
│   ├── install.sh      # install/uninstall the LightDM session
│   ├── autologin.sh    # kiosk autologin on/off
│   └── test.sh         # safe nested/spare-VT testing
├── share/
│   └── pleb.desktop.in # xsession entry template (installed to /usr/share/xsessions)
├── scripts/
│   └── install-go.sh   # install/upgrade Go from go.dev (for the kilix fork build)
└── README.md
```

The engine is a kilix checkout at `~/kilix`. You don't need to set it up
yourself — **`pleb install` clones kilix from upstream if it isn't already
there** and fetches a prebuilt kitty so it runs immediately. The clickable-button
**fork** (`src/kitty/launcher/kitty`) is built on demand later with `pleb update`
or `~/kilix/kilix --build` (needs Go ≥ 1.26).

`pleb install` also symlinks `kilix` onto your `PATH` (`/usr/local/bin/kilix`, or
`$KILIX_LINK`), so `kilix desktop`, `kilix serve`, and friends work from anywhere
out of the box.

## Requirements

- A **LightDM**-based Linux desktop with **Xorg** (`startx`/`xinit`).
- **git**, **curl**, **tar** (to clone kilix and fetch its prebuilt engine).
- `sudo` for `install` / `autologin` (system files only).
- On Debian/Ubuntu, `pleb install` installs the needed runtime/build packages
  with apt, including the FluidSynth/SoundFont packages used by kilix-amp MIDI
  playback. Set `PLEB_SKIP_DEPS=1` to skip package-manager changes.
- Optional: `xserver-xephyr` for nested `pleb test`; Go ≥ 1.26 for the fork.

## Quick start

```sh
~/pleb/bin/pleb install         # clone kilix + engine, add "Pleb" to LightDM (asks for sudo)
~/pleb/bin/pleb doctor          # check prerequisites (engine, X, greeter)
~/pleb/bin/pleb test            # try it in a throwaway X server — nothing permanent
```

Then **log out** of your current desktop, and at the LightDM greeter open the
session menu (the little badge/gear near the password box), choose **Pleb**, and
log in. To go back, log out and pick your usual session again.

> Add `~/pleb/bin` to your `PATH` (or `alias pleb=~/pleb/bin/pleb`) to drop the
> full path.

## The `pleb` CLI

| Command | What it does |
|---|---|
| `pleb doctor` | Check the engine, X tools, and greeter are ready. |
| `pleb test [opts]` | Launch the session in a **throwaway** X server (see below). |
| `pleb install` | Clone kilix (if missing) + engine, put `kilix` on `PATH`, add "Pleb" to the LightDM session menu. *(sudo)* |
| `pleb uninstall` | Remove both, and any autologin config. *(sudo)* |
| `pleb autologin on [user]` | Boot straight into Pleb — no greeter (kiosk). *(sudo)* |
| `pleb autologin off` | Revert to the normal greeter. *(sudo)* |
| `pleb kiosk on` / `off` | Hard kiosk: respawn kilix if it exits (or don't). *(no sudo)* |
| `pleb update [-y] [--no-restart]` | Pull latest kilix, rebuild the fork, offer to restart the kiosk when Pleb is active. |
| `pleb status` | Show engine / install / autologin / kiosk state. |
| `pleb screen-size ...` | Show, increase, decrease, reset, or set Kilix terminal scale. |
| `pleb session` | Exec the session now, against the current `$DISPLAY`. |

`install`, `uninstall`, and `autologin` need root; the CLI calls `sudo` only for
the specific file operations, so you'll be prompted once.

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

## How fullscreen works (and the no-WM detail)

kitty's `--start-as=fullscreen` relies on `_NET_WM_STATE_FULLSCREEN`, which only
works when a **window manager** is running. Pleb runs **no WM** by default, so
`pleb-session` detects that there's no WM and instead sizes the kitty window to
the whole screen in pixels — a borderless window at `0,0`
that fills the display. Verified: on a 1280×800 screen the window comes up
`1280x800+0+0`.

To make the fullscreen terminal feel larger or fit more rows/columns, adjust
Kilix's `font_size`:

```sh
pleb screen-size larger       # bigger text, fewer rows/columns
pleb screen-size smaller      # smaller text, more rows/columns
pleb screen-size reset
pleb screen-size set 13
```

If you'd rather have a real WM (native fullscreen, `F11`, multi-monitor):

```sh
PLEB_WM=openbox pleb test        # or matchbox-window-manager, evilwm, etc.
```

Then install with the same env, or set it permanently by editing the installed
`/usr/local/bin/pleb-session` (or exporting `PLEB_WM` in the session).

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

`pleb kiosk on` (no sudo) writes `: "${PLEB_RESPAWN:=1}"` to
`~/.config/pleb/session.env`, which `pleb-session` sources at startup. Because it
uses a default-only assignment, an explicit `PLEB_RESPAWN` in the environment
still wins (this is what keeps `pleb test` — which passes `PLEB_RESPAWN=0` —
deterministic). You can put any knob from the table below in that file (or in a
system-wide `/etc/pleb/session.env`).

## Updating kilix

```sh
pleb update              # fetch latest kilix, ff-only, rebuild the fork, offer restart
pleb update -y           # ...and restart an active Pleb kiosk without asking
pleb update --no-restart # update only; leave LightDM alone
```

`pleb update` fast-forwards or pins `~/kilix`, updates the optional `~/kilix-95`
desktop checkout when the selected provider needs it, rebuilds the fork if a Go toolchain is present
(else keeps the current engine), and only offers to restart LightDM when Pleb is
configured as the active kiosk/autologin session. It never force-updates: if a
branch can't fast-forward (local commits), it stops and tells you.

## Environment knobs (`pleb-session`)

| Variable | Default | Meaning |
|---|---|---|
| `KILIX_DIR` | `$HOME/kilix` | Kilix engine checkout. |
| `KILIX` | `$KILIX_DIR/kilix` | Path to the kilix launcher. |
| `KILIX_BRANCH` | *(repo default)* | Optional Kilix branch for install/update. |
| `KILIX_REF` | *(none)* | Optional exact Kilix commit/tag for install/update. |
| `KILIX_PREBUILT_VERSION` | *(latest)* | Optional exact fallback kitty version for Kilix bootstrap. |
| `KILIX_PREBUILT_SHA256` | *(none)* | Optional checksum for the pinned fallback kitty bundle. |
| `PLEB_SKIP_DEPS` | `0` | If `1`, skip apt dependency installation during `pleb install`. |
| `PLEB_KILIX_ARGS` | auto | Args passed to kilix; unset means native fullscreen with a WM, screen-fill sizing without one. |
| `PLEB_WM` | *(none)* | Window manager to run before kilix (enables native fullscreen). |
| `PLEB_NO_FILL` | `0` | Skip the no-WM screen-fill sizing. |
| `PLEB_BG` | `#101010` | Root-window solid colour. |
| `PLEB_RESPAWN` | `0` | If `1`, relaunch kilix when it exits (hard kiosk). |
| `PLEB_DESKTOP` | `0` | If truthy, boot directly into `kilix desktop`; `0` gives a plain shell. |
| `KILIX_DESKTOP_PROVIDER` | `external` | `auto`, `builtin`, `external`, `command`, or `none`. |
| `KILIX_DESKTOP_COMMAND` | *(none)* | Shell command run by `kilix desktop` when provider is `command`. |
| `KILIX_DESKTOP_NAME` | `desktop` | Label/tab title for custom desktop providers. |
| `KILIX95_AUTO_INSTALL` | `1` | Lets `kilix desktop` clone external Kilix 95 when needed. |
| `KILIX95_DIR` | `$HOME/kilix-95` | External Kilix 95 checkout used for desktop sessions. |
| `KILIX95_REPO` | `https://github.com/itsmygithubacct/kilix-95.git` | Repo cloned when Kilix 95 is needed. |
| `KILIX95_BRANCH` | *(repo default)* | Optional Kilix 95 branch. |
| `KILIX95_REF` | *(none)* | Optional exact Kilix 95 commit/tag. |
| `PLEB_LOG` | `~/.local/share/pleb/session.log` | Session log. |

Use `PLEB_DESKTOP=0` or `KILIX_DESKTOP_PROVIDER=none` for no desktop at all. To
supply a different desktop through the same Kilix facade:

```sh
PLEB_DESKTOP=1 \
KILIX_DESKTOP_PROVIDER=command \
KILIX_DESKTOP_COMMAND='exec /path/to/desktop'
```

## Uninstall / reverse everything

```sh
pleb autologin off      # if you enabled it
pleb uninstall          # removes /usr/local/bin/pleb-session + the xsession entry
```

`~/pleb` and `~/kilix` are left in place; delete them by hand if you want them
gone.

## Notes & limitations

- **Engine: the kilix fork** (with clickable `→ ↓ ▢ ✕` pane buttons) builds to
  `~/kilix/src/kitty/launcher/kitty` and is what `kilix` uses. Building it needs
  **Go ≥ 1.26**; if your distro ships an older Go, install a newer toolchain with
  [`scripts/install-go.sh`](scripts/install-go.sh). If no fork binary is present,
  kilix falls back to a prebuilt kitty (a working terminal, no buttons); `pleb
  install` sets that up automatically, and `~/kilix/kilix --build` (or `pleb
  update`) produces the fork once Go is new enough. `pleb update` also re-runs
  the dependency installer before building so older installs pick up newly added
  build packages such as `libxkbcommon-x11-dev`.
- **Upgrading Go later:** `~/pleb/scripts/install-go.sh` (default: latest stable
  from go.dev; or pass a version, e.g. `install-go.sh go1.27.0`). `fetch` is
  unprivileged; `install` needs sudo only to extract into `/usr/local`.
- Single-monitor sizing is captured at launch; if you hot-plug a monitor or
  change resolution, restart the session (or use `PLEB_WM` for dynamic sizing).

## Credits

Pleb is just glue around [**kilix**](https://github.com/itsmygithubacct/kilix) —
the Tilix-styled kitty fork that does the real work. The idea is to make kilix
the *whole* desktop instead of running it inside one, delivered here as a
**login-session option on an existing LightDM desktop** rather than as a custom
OS image.

## License

Pleb is released under the [MIT License](LICENSE). It is a standalone set of
scripts that *invoke* kilix at runtime — it does not include or link kilix/kitty
code (it clones kilix separately on install). kilix and kitty are licensed under
the **GPLv3** by their respective authors.
