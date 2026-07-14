# Changelog

## 0.1.2 — 2026-07-14

- Use `~/gpu_terminal` as the shared source-checkout root and
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
