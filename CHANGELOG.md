# Changelog

## 0.1.1 — 2026-07-12

- Add exact, architecture-specific Go version and SHA-256 pinning for Plebian-OS.
- Stage, re-verify, validate, and rollback Go toolchain replacements on failure.
- Serialize updates and refuse dirty Kilix or Kilix 95 checkouts.
- Resolve pinned component refs from the current remote fetch rather than
  trusting potentially stale or poisoned local tags.
- Require full component commit SHAs and an immutable ref for automatic external
  provider installs unless the matching mutable/unpinned trust override is set.
- Move the Kilix fork-build stamp from the checkout to XDG state.
- Make `pleb update --restart` restart an active kiosk without prompting.
- Make `pleb status` use the effective system and user session configuration.
- Make `pleb kiosk off` override a system-wide respawn default.
- Add behavioral coverage for pinning, rollback, locking, restart, persisted
  status, dirty checkouts, and state placement.

## 0.1.0 — 2026-07-10

- Initial Pleb LightDM session, kiosk controls, installer, updater, and Kilix
  desktop integration.
