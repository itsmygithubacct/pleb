# Plebian-OS desktop artwork

`plebian-os.png` is the approved 1920x1080 desktop treatment of the
Plebian-OS angular-P geek identity. It is tracked as a stable product asset;
Debian Installer does not consume it.

The wallpaper uses the same Ceratopsian-derived background and
GPL-2.0-or-later distribution choice documented in
[`../installer/ATTRIBUTION.md`](../installer/ATTRIBUTION.md). The complete GPL
version 2 text is included at [`../COPYING.GPL-2`](../COPYING.GPL-2).

Runtime contract:

```text
1920x1080, 8-bit RGB PNG, no alpha, non-interlaced
SHA-256: 60f63c37f054f7ffd061b47e09a3c22fbf595eec6f161c13e95344ca1a724778
```

Keep release versions out of the bitmap. The remaster installs it at:

```text
/usr/local/share/plebian-os/wallpapers/plebian-os.png
```

The provisioner writes that path into the selected provider's new desktop state:
`~/.local/gpu_terminal/kilix-95/data/desktop/.state.json` for the external
Kilix-95 provider, or `~/.local/gpu_terminal/kilix/data/desktop/.state.json` for
the builtin provider. `auto` chooses the external tree when Kilix-95 is
installed and otherwise chooses builtin Kilix. Both use the common `wall_image`
and `wall_mode=stretch` contract. The state is created atomically as the target
user only when no state file exists; reprovisioning never rewrites an existing
state or wallpaper choice. `plebian-os-update` deploys later asset
revisions inside the same rollback-safe OS-layer transaction and seeds absent
state only after that transaction commits. The ISO build records the exact
bytes as `PLEBIAN_OS_DESKTOP_WALLPAPER_SHA256` in
`/etc/plebian-os/build-info.env`.

The immutable v0.1.1 updater knows a fixed seven-file OS-layer manifest, so an
upgrade from v0.1.1 needs two `plebian-os-update` invocations: the first deploys
the new scripts, and the second uses their ten-file manifest to install this
asset plus the attribution and GPL text, then seed a new desktop. Do not substitute a bare
`sudo plebian-os-provision` between those runs because sudo does not preserve
the coordinated install settings. For configuration-preserving reprovisioning
orchestration, the provisioner has a defensive fallback that accepts the asset
only from the target user's clean, origin-checked Plebian-OS checkout, binds the
working file to the tracked `HEAD` blob, and still enforces the exact hash.
