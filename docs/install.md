# Installing Pleb

What `pleb install` sets up, what it needs, how to remove the session
integration, and known limitations. The short version is in the
[README](../README.md#quick-start).

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
Media Player and PDF Viewer. Music drives kilix-amp over its control socket
rather than decoding anything itself, and starts a `kilix-amp --headless`
backend when none is listening.

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

As a fallback for shells whose `PATH` misses those links, `pleb install` also
drops a `tb` alias into `~/.bash_aliases` (session shells read `~/.bashrc`,
which sources that file on Debian). The alias resolves tmux-cli's `tb.py`
inside the sibling `kilix-apps` checkout at use time — `KILIX_APPS_DIR` if
set, otherwise `kilix-apps` under the shared source root — so it follows the
source root wherever it is configured to live. If `tb` already exists on the
machine as a binary, alias, or function (including the command link above),
the current holder is reported and kept; the alias is only written when the
name is genuinely free, and never replaces or shadows anything.

The PDF Viewer is installed here so its catalog-pinned launcher and native
Poppler/Cairo renderer are ready before first use. If a standalone machine
lacks the development files, the install retains the launcher's Evince
fallback and reports that the native core was skipped. Plebian-OS supplies
both Evince and the native build prerequisites; the viewer always retains its
complete CPU path when GPU presentation is unavailable.

PDF Conversion follows the same catalog pinning contract but installs on first
use from `kilix pdf` or the desktop menu. Standalone `pleb install` includes
Debian's `python3-venv`, so its hash-locked Python 3.11 runtime does not depend
on `uv`. Plebian-OS separately installs an exact, checksum-verified `uv`
release as system tooling.

The install also selects Kilix's immutable voice closure. The standalone
default, `PLEB_INSTALL_VOICE_MODEL=0`, installs its pinned read-aloud runtime
without the speech library or acoustic model. Setting
`PLEB_INSTALL_VOICE_MODEL=1` requires the complete checksum-pinned dictation
closure; a failed full install returns nonzero and never silently falls back to
read-aloud-only.

The pinned Voice runtime owns the `kilix.speech.models/v1` catalog.
`kilix stt --models --json` reports its choices, installed/default state, and
explicit install-and-default argv without downloading. The CLI, dictation TUI,
Kilix 95 Settings, built-in WM Settings, and terminal microphone button use that
one control-plane contract; Pleb only installs or refreshes the immutable
runtime and does not maintain another model list.

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
  including `build-essential` and `cmake` (the model store compiles its CPU
  inference runtime locally), NetworkManager's `nmtui` for the top-bar
  network/Wi-Fi widget, `pulsemixer` for its volume widget, and the
  FluidSynth library and General MIDI SoundFont used by kilix-amp MIDI
  playback (the same runtime its headless backend decodes with). It installs
  `libfluidsynth3`, not the `fluidsynth` player package, whose per-user
  service Debian enables at every login and which then holds the sound card
  that dictation needs.
  Before a fork build, `pleb update` runs Kilix's own complete cross-distro
  dependency verifier and installer (including the `libxxhash` pkg-config
  module). Set `PLEB_SKIP_DEPS=1` to prevent package-manager changes; an update
  then fails clearly if Kilix reports a missing build prerequisite.
- Optional: `xserver-xephyr` for nested `pleb test`; Go ≥ 1.26 for the fork.

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
  [`scripts/install-go.sh`](../scripts/install-go.sh). If no fork binary is present,
  kilix falls back to a prebuilt kitty (a working terminal, no buttons); `pleb
  install` sets that up automatically, and `~/.local/gpu_terminal/sources/kilix/kilix --build` (or `pleb
  update`) produces the fork once Go is new enough. Immediately before
  `kilix --build`, `pleb update` delegates to Kilix's
  `scripts/install-build-deps.sh`: it verifies the complete current manifest,
  installs when verification fails, and verifies again. This avoids a stale
  duplicate package list in Pleb and covers newly added modules such as
  `libxxhash`. If an older install fails at that check, follow the concise
  [recovery guide](RECOVERY.md); `pleb install` also publishes it at
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
