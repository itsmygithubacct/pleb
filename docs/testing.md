# Testing without risking your desktop

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
