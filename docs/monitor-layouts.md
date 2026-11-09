# Monitor layouts

Pleb owns physical display configuration; **Kilix Settings → Tools → Displays**
opens `pleb displays gui`. The graphical arranger runs inside a Kilix app tab,
with draggable monitor cards, edge snapping, primary selection, resolution,
refresh rate, rotation and per-monitor scale controls. Its private UI display is separate from
the physical X server it controls. This feature targets local X11 sessions using
XRandR. Run it as the desktop user, without sudo.

```sh
pleb displays list                      # outputs, resolutions and refresh rates
pleb displays gui                       # graphical arranger in a Kilix tab
pleb displays configure                 # interactive layout editor
pleb displays snapshot > layout.json     # optional JSON editing workflow
pleb displays preview layout.json       # 15-second confirmation deadline
pleb displays restore                   # restore a previously confirmed layout
pleb displays save-current              # remember the layout already on screen
pleb displays launch DP-1               # a separate Kilix terminal on DP-1
pleb displays launch DP-1 -- kilix nvr view doorway
```

An extended output does not automatically get a second Kilix window. `launch`
opens a new native window on the named active output, then maximizes it using
the window manager. The original desktop stays where it is. An optional command
after `--` runs inside that terminal, so a camera view can occupy the second
screen while the first remains a desktop. Closing that window closes its app;
this does not create an automatic login application. Startup errors are retained
in a private log under `$PLEB_STATE_HOME/displays`.

`save-current` remembers the already-active layout without applying a new one.
It requires monitor identities and refuses a layout that changes while being
read. Use the timed `preview` or graphical arranger when changing outputs.

Choose enabled outputs, resolution, refresh rate, rotation, scale from 50% to
400%, horizontal/vertical position and one primary monitor. A larger scale
makes content larger. Positions and monitor cards use logical framebuffer
pixels: a 3840×2160 output at 200% occupies 1920×1080, so an adjoining monitor
starts at `1920,0`. **Extend →** calculates these positions for you. Equal
positions mirror outputs. Fractional scaling uses a bilinear RandR filter;
existing nearest filters are preserved in rollback baselines. Nonuniform or
projective transforms, reflection and panning are refused; native Wayland and
forwarded SSH displays are unsupported. New JSON profiles are version 2 and
include `scale` (1.25 means 125%) and `filter`. Existing version 1 profiles load
at 100% without rewriting them; confirming a layout saves version 2.

Choose **Keep this layout** within 20 seconds in the graphical arranger (or type
`yes` within 15 seconds in the terminal editor) to save it. Timeout, cancellation, a closed
settings terminal or a failed apply restores the previous layout through an
independent worker. Hardware removal during preview triggers recovery on the
remaining outputs. As with any X11 tool, recovery requires a responsive X server
and a working driver; power loss or killing the worker cannot be recovered in
that session. The baseline is retained under `$PLEB_STATE_HOME/displays`.

Confirmed profiles live in `$PLEB_CONFIG_HOME/displays` (private JSON files).
They match both EDID identity and connector, with no guessing after recabling.
Automatic restore is skipped when an output has no EDID. The installed
Pleb session restores matching profiles at login and on connector/monitor changes;
the watcher exits with its session or X server. Ordinary manual layout changes
do not trigger it. Unknown monitor combinations are left as configured by X11.
Lock contention or disconnected outputs before an apply are retried. A rejected
layout is not repeatedly applied: retry it explicitly with `pleb displays restore`
or reconnect the monitors to trigger a new topology transition.
This sets the RandR primary output; existing application windows are not moved.

The installed session uses `/usr/local/lib/pleb/displays.py`, published by
`pleb install`; changing a source checkout alone does not enable the login hook.
No release pins or version labels are changed by this feature.
