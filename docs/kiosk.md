# Kiosk mode

Two independent settings: autologin skips the greeter, and the hard kiosk
relaunches Kilix when it exits.

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

The table of knobs is in
[configuration.md](configuration.md#environment-and-persisted-configuration-knobs).
