# Release notes from the README

These sections stood at the top of the README until 2026-11-09. The coordinated
release record (manifests, notes, known issues) is kept by
[Plebian-OS](https://github.com/itsmygithubacct/plebian-os#readme); Pleb's own
changes are in the [changelog](../CHANGELOG.md).

## 0.2.2.1 maintenance candidate

Release updates recognize an optional positive maintenance number, such as `0.2.2.1`, and preserve adjacent-release and exact-tag checks.

This source is a release candidate; installation-media and upgrade acceptance are pending.

## Release 0.2.2 — Linux

Pleb is selected by the Plebian-OS 0.2.2 Linux release published November 3,
2026. Its `v0.2.2` tag identifies the released source; this branch can contain
later development. The coordinated source includes desktop services, display
controls, frontend recovery and owned-process cleanup.

Linux installation and lifecycle qualification covers QEMU/KVM BIOS and EFI
virtual machines. Bare-metal USB installation and physical GPU operation were
not qualified. Windows remains at `windows-v0.2.2-preview.4`; ARM64 is not a
supported OS release target. See the [published release notes](https://github.com/itsmygithubacct/plebian-os/releases/tag/v0.2.2)
for known issues and deferred feature qualification.

On a managed Plebian-OS installation, use its coordinated update tools and
[upgrade policy](https://github.com/itsmygithubacct/plebian-os/blob/main/UPGRADING.md).
Release pins live in `/etc/pleb/closure.env`; operator session choices remain
in `/etc/pleb/session.env`. The selected 0.2.2 binary artifacts target AMD64;
standalone engine support for ARM64 does not qualify the complete OS release.
