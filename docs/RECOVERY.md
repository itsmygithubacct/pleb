# Pleb update recovery

Use this when `pleb update` stops during the Kilix prerequisite check or fork
build with a message such as `pkg-config libxxhash: MISSING`. Your Pleb data is
not damaged, and no source or data reset is needed.

## Preserved checkout data

Before an update moves Pleb, Kilix, Kilix 95, or an initialized Kilix
submodule, it saves every modified tracked and untracked path under
`~/.local/gpu_terminal/pleb/state/update-preserve/`. Each completed directory
contains `STATUS`, `METADATA.json`, and `MANIFEST.sha256`; verify one with:

```sh
cd ~/.local/gpu_terminal/pleb/state/update-preserve/<snapshot>
sha256sum -c MANIFEST.sha256
```

The updater verifies and fsyncs the snapshot before it moves local paths. It
also takes a distinct `rollback` snapshot of edits made during the run before a
forced restore. Preservation directories remain after both success and failure;
the last ten verified snapshots per checkout are retained. To back up local
checkout work without attempting an update, run:

```sh
pleb update --preserve-only
```

On success, untracked operator files return to their original names, with an
incoming release collision beside them as `.from-<short-sha>`. For modified
tracked files, the release version stays installed and the operator copy is
left as `.local`. The command reports every such path.

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

## The update moved Pleb itself

`pleb update` updates the Pleb checkout too, last, after Kilix and the desktop
provider are coherent. Two consequences are worth knowing before they surprise
you.

The new code runs from the next `pleb` command onwards, not the one that
installed it. Run `pleb update` a second time when a release note says the
updater itself changed.

A moved checkout is verified before it is accepted: it must parse and answer
`pleb version`. One that does not is put back on the previous commit and the
run reports `pleb self-update rolled back; the previous version is still
installed`, so the machine keeps a working installation. A failed automatic
restore names the preservation root and retains its recovery record for
inspection. To update the other components without moving Pleb, use

```sh
PLEB_SELF_UPDATE=0 pleb update
```

updates everything except Pleb.

The commit chosen is `PLEB_REF` when one is set, exactly like `KILIX_REF`, and
the persisted pin in `/etc/pleb/session.env` applies to every run that does not
override it. That is deliberate — the pin is the machine's declared state — so
an update that walks a component backwards is reported as a `DOWNGRADE` naming
the file responsible. Seeing one means the delivered commit was never written
into the pin; export the variable again for this run, and move the pin for good.

## "refusing unsafe Kilix previous generation entry"

Every update snapshots the two Kilix engine generation entries — `current` and
`previous`, under `~/.local/gpu_terminal/kilix/build` — so a failed run can put
them back. An entry pointing at a generation directory that is no longer there
fails that check, and up to 0.1.8 both `pleb update` and `plebian-os-update`
refused to start against one:

```
[pleb] refusing unsafe Kilix previous generation entry: .../kilix/build/previous
```

A `previous` in that state references nothing, so there is no rollback left to
protect. Updates now retire the stale entry and carry on, reporting

```
[pleb] previous Kilix generation generations/build.XXXXXX is gone; retiring the stale entry
```

Nothing is lost but the ability to roll back to a generation that was already
deleted. An older machine still stuck on the refusal is repaired by removing
that one entry — `rm ~/.local/gpu_terminal/kilix/build/previous` — and running
the update again. Leave `current` alone: that entry is the engine you are
running.

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
missing. The 0.1.7 closure pins both the `libvosk` release and the small English
model by checksum, but Pleb keeps the amount installed under an explicit
policy:

- `PLEB_INSTALL_VOICE_MODEL=0` is the standalone default. It installs the
  pinned read-aloud runtime without the library or model, so a dim microphone
  is expected and does not fail `pleb install`.
- `PLEB_INSTALL_VOICE_MODEL=1` requires read-aloud and dictation. A missing
  installer or failed source, library, or model fetch makes `pleb install`
  return nonzero; it does not retry as read-aloud-only and claim success.

After fixing the reported network, checksum, or storage problem, retry the
required closure with:

```sh
PLEB_INSTALL_VOICE_MODEL=1 pleb install
```

The library and model downloads are checksum-verified and land under
`~/.local/gpu_terminal/kilix/data/voice`, never in a source tree, so re-running
the install is the whole fix. `kilix voice install --without-dictation`
reinstalls read-aloud alone when the model is not wanted. If dictation starts
but hears nothing, the input source is muted or is the wrong one: `pulsemixer`
selects it, and `kilix-stt`'s level meter shows whether audio is arriving.

`pleb status` reports all of this in one line: the two widgets, the engines, the
model, and whether the voice daemon is running.
