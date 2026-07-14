# Pleb update recovery

Use this when `pleb update` stops during the Kilix prerequisite check or fork
build with a message such as `pkg-config libxxhash: MISSING`. Your Pleb data is
not damaged, and no source or data reset is needed.

## Plebian-OS: preferred recovery

```sh
sudo /usr/local/sbin/plebian-os-install-deps
pleb update
```

The Plebian-OS helper installs the complete, release-matched Kilix build
dependency set—not only `libxxhash-dev`, but also the X11, Wayland, font,
graphics, SIMDe, SDL, audio, and FluidSynth development packages. This is the
preferred fix when more than one prerequisite is missing.

If you deliberately set `PLEB_SKIP_DEPS=1`, unset it before retrying, or install
every prerequisite printed by the verifier yourself.

## Debian/Ubuntu fallback for only libxxhash

```sh
sudo apt-get update
sudo apt-get install libxxhash-dev
pleb update
```

This fallback supplies the `libxxhash` pkg-config module only. If the retry
reports another missing module, use the full Plebian-OS helper above. On a
standalone non-Plebian Debian/Ubuntu install, Kilix's complete cross-distro
helper is `~/gpu_terminal/kilix/scripts/install-build-deps.sh`.

`pleb update` verifies the complete Kilix dependency manifest again before it
runs `kilix --build`, so it is safe to repeat after fixing the reported package.
