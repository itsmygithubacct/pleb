# Configuration

Choosing the login desktop, session logging, the shared chrome settings file,
and every environment and persisted configuration knob.

## Choosing the login desktop

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

## Session logging and shared settings

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
| `PLEB_RECOVER_CRASHES` | `auto` | Restart failed frontends while preserving the session; `auto` requires owned WM and active desktop services, `on` enables explicitly, `off` disables. Clean exits still end normal sessions. |
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
