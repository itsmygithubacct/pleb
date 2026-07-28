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
helper is `~/.local/gpu_terminal/sources/kilix/scripts/install-build-deps.sh`.

`pleb update` verifies the complete Kilix dependency manifest again before it
runs `kilix --build`, so it is safe to repeat after fixing the reported package.

## No speech / no microphone

Read-aloud and dictation are optional. When something they need is missing their
two top-bar widgets dim, Kilix starts and behaves exactly as before, and

```sh
kilix voice doctor
```

names what is absent — it inspects the engines, the audio server, the speech
library and the model without ever opening the microphone. Nothing is captured
until the microphone widget is clicked, and dictated text is inserted into the
pane without being submitted for you.

Nothing spoken at all usually means the synthesizer is missing:

```sh
sudo apt-get install espeak-ng
```

`espeak-ng` is the default engine and the only package read-aloud requires. Its
optional `mbrola` quality tier lives in Debian's contrib component and its voice
databases in non-free, so it installs only where both are enabled:

```sh
sudo apt-get install mbrola mbrola-us1
kilix settings --set tts_engine=mbrola
```

Read-aloud that stays silent with `espeak-ng` installed is an audio-server
problem rather than a voice one: `pactl info` shows whether a server is
reachable, and `pulsemixer` selects the output device.

A dimmed microphone means the pinned speech library or the acoustic model is
missing — a first boot without a usable network leaves read-aloud working and
dictation unavailable, which is the intended degradation, not a broken install:

```sh
kilix voice install
```

Both downloads are checksum-verified and land under
`~/.local/gpu_terminal/kilix/data/voice`, never in a source tree, so re-running
the install is the whole fix. `kilix voice install --without-dictation`
reinstalls read-aloud alone when the model is not wanted. If dictation starts but
hears nothing, the input source is muted or is the wrong one: `pulsemixer`
selects it, and `kilix-stt`'s level meter shows whether audio is arriving.

`pleb status` reports all of this in one line: the two widgets, the engines, the
model, and whether the voice daemon is running.
