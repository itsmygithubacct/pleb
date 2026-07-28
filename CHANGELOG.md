# Changelog

## 0.1.5 — unreleased

Version numbers 0.1.3 and 0.1.4 were never coordinated stack releases; Pleb goes
0.1.2 → 0.1.5 alongside Plebian-OS, Kilix, and Kilix-95. See Plebian-OS
RELEASING.md.

- Install the shared clickable-chrome settings file at
  `~/.local/gpu_terminal/settings.conf` and symlink `kilix-settings` onto
  `PATH`, so `pleb settings`, `kilix settings`, and Kilix-95's Settings app all
  edit one file.
- Install `pulsemixer` for the top-bar volume widget, and document the thermal
  widget's integration with the shared settings contract.
- Build and install Kilix's exact pinned Kilix Temps dashboard and graphics
  closure during `pleb install`, so the page-strip thermometer resolves without
  a pre-existing developer checkout.
- Install Kilix's pinned `tmux-tui`/`tmux-cli` closure and publish Tmux Manager
  plus tmux-cli's `tb.py` as `tb` on `PATH`.
- Install the pinned persistent PTY session manager used for crash-persistent
  panes.
- Install `zstd`, which Kilix uses to archive older pane transcripts.
- Report Kilix's default-on session logging in `pleb status` — current policy
  plus how many pane transcripts exist — and accept `KILIX_TRANSCRIPT_DIR` in
  the persisted session environment.
- Snapshot and restore the content and presenter submodules — and any newly
  added Kilix submodule — during update rollback, so a failed update no longer
  leaves a partially advanced submodule state.
- Run a Pleb-managed **Openbox** window manager, so native application windows
  can be focused, raised, closed and reached with `Alt-Tab`. Previously a
  fullscreen kilix covered every other client permanently: a browser could be
  running and focused but invisible. Adds `openbox` to Pleb's runtime
  dependencies and installs a reduced, Pleb-owned profile at
  `/usr/local/share/pleb/openbox/rc.xml` (one desktop, no root menu, no panel,
  no launcher or screenshot keys, and no rule pinning kilix above other
  windows).
- Replace the best-effort `PLEB_WM` hook with a real lifecycle: the full
  `_NET_SUPPORTING_WM_CHECK` handshake — including validating that the check
  window points at itself, so a stale property from a dead WM is not mistaken
  for a live one — bounded readiness polling that also watches the WM process,
  and joint supervision of the WM and kilix. `PLEB_WM=openbox` now fails the
  session instead of silently downgrading, and a window manager that dies is
  fatal rather than leaving unmanaged clients behind. An already-running EWMH
  window manager is adopted, never replaced or killed.
- Default GUI commands to native windows when a window manager is present, via
  the new `KILIX_RUN_ALIASES` session variable; `kilix run <app>` remains the
  explicit way to render an application inside a kilix tab.
- Report the window-manager mode, Openbox availability, profile drift and
  active WM in `pleb status`, and check them in `pleb doctor` — which now also
  flags an installed `pleb-session` or Openbox profile that differs from the
  checkout, the drift that silently broke a deployed box.

## 0.1.2 — 2026-07-15

- Use `~/.local/gpu_terminal/sources` as the shared source-checkout root and
  `~/.local/gpu_terminal` as the shared writable-data root.
- Apply persisted storage and source-path settings before deriving dependent
  defaults, while preserving explicit process-environment precedence.
- Export the complete source/data path contract into Pleb and Kilix desktop
  sessions.
- Preserve explicit caller precedence for all coordinated Kilix and Kilix 95
  config, state, cache, session, and data paths while loading persisted session
  defaults, and export those category paths through the self-contained launcher.
- Keep session logs and persisted kiosk configuration private, safely rotate
  oversized session logs, and reject unsafe log/configuration targets.
- Reconcile the complete Pleb storage root and its config, state, cache,
  session, and data categories to user-owned `0700` directories during managed
  and standalone install, update, direct Go fetches, testing, kiosk changes,
  and login; preserve contents and reject broad, linked, or escaped overrides
  before changing modes.
- Apply the same private-cache preflight to Go fetch and direct install,
  canonicalize before containment checks, and reject linked, traversing,
  loosely permissioned, or unsafely owned external cache paths before sudo
  stages an archive.
- Require direct Go installs to authenticate a cached manifest against a pinned
  SHA-256 or a freshly derived official checksum before root stages or executes
  archive content; ignore caller PATH, shell/Python/curl startup hooks, and
  privileged-command environment when deriving or consuming that trust, and
  restrict staging/link destinations to validated root-owned `/usr/local`
  parents; fail closed with offline pinning advice.
- Keep generated Kilix fork artifacts outside the source checkout and include
  their state in update rollback.
- Coordinate Pleb, direct Kilix, Plebian-OS update, and first-boot builds
  through Kilix's private shared transaction lock. Treat Kilix's contained
  generation, exact `source-id`, and `state/fork-built-ref` as the single
  engine identity, retiring the older duplicate Pleb stamp only after commit.
- Preserve the exact pre-update `current` and `previous` generation links
  during rebuilds, protect an older rollback generation while a new one is
  promoted, and atomically restore both links and the canonical stamp after any
  later failure. Coherence checks now reject escaping generation topology,
  malformed source/stamp bytes, non-regular launchers, and a broken `kitten`
  probe before an update can commit.
- Bundle and exact-validate the approved Plebian wallpaper, attribution, and
  GPL text; standalone installs copy it under Pleb-owned data and atomically
  seed only an absent Pleb-isolated desktop state without changing provider
  defaults such as Kilix-95's XP wallpaper.
- Make Kilix's `scripts/install-build-deps.sh` the authoritative pre-build
  dependency gate, including its `libxxhash` pkg-config check; verify before
  installation and again before `kilix --build`.
- Reject unsafe standalone artwork storage roots and symlinked or non-private
  directory trees, use race-safe descriptor-based artwork reads, and restore
  the previous full bundle after a publication error.
- Install a concise dependency/update recovery guide at the stable,
  user-readable `/usr/local/share/doc/pleb/RECOVERY.md` path, including the
  preferred Plebian-OS helper and `libxxhash-dev` fallback.

## 0.1.1 — 2026-07-12

- Add exact, architecture-specific Go version and SHA-256 pinning for Plebian-OS.
- Stage, re-verify, validate, and rollback Go toolchain replacements on failure.
- Record root-owned Go source provenance and enforce it for pinned fork builds.
- Serialize updates and refuse dirty Kilix or Kilix 95 checkouts.
- Validate and borrow the parent Plebian-OS updater's inherited lock without
  releasing its ownership.
- Roll back both component positions, the fork engine, and its build stamp when
  any pre-commit update step fails.
- Resolve pinned component refs from the current remote fetch rather than
  trusting potentially stale or poisoned local tags.
- Require full component commit SHAs and an immutable ref for automatic external
  provider installs unless the matching mutable/unpinned trust override is set.
- Move the Kilix fork-build stamp from the checkout to XDG state.
- Make `pleb update --restart` restart an active kiosk without prompting.
- Make `pleb status` use the effective system and user session configuration.
- Make `pleb kiosk off` override a system-wide respawn default.
- Revalidate configured prebuilt pins, verify bootstrap postconditions, and show
  the unverified asset URL before asking for consent.
- Add behavioral coverage for pinning, rollback, locking, restart, persisted
  status, dirty checkouts, and state placement.

## 0.1.0 — 2026-07-10

- Initial Pleb LightDM session, kiosk controls, installer, updater, and Kilix
  desktop integration.
