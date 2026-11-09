# Updating the stack

How `pleb update` revalidates or moves the coordinated release closure, what it
preserves, and how it rolls back. On a Plebian-OS image the operator procedure
and upgrade policy are defined by Plebian-OS
[UPGRADING.md](https://github.com/itsmygithubacct/plebian-os/blob/main/UPGRADING.md);
recovery commands are in [RECOVERY.md](RECOVERY.md).

```sh
pleb update              # revalidate the selected closure; never drift to a newer release
pleb update --show       # print every selected release value and the file that supplied it
pleb update --to 0.2.2 --dry-run # validate and report the exact published target; write no installed state
pleb update --to 0.2.2   # select and apply one named, supported release hop
pleb update --latest     # select the newest release only when that is an accepted adjacent hop
pleb update --rollback   # restore the immediately previous closure and stack (one generation)
pleb update -y           # ...and restart an active Pleb kiosk without asking
pleb update --restart    # explicitly restart an active kiosk; never prompts
pleb update --no-restart # update only; leave LightDM alone
pleb update --preserve-only # verified snapshots only; move no checkout
```

For an explicitly chosen release candidate, use
`pleb update --to 0.2.3 --release-tag v0.2.3-rc1 --dry-run`, then repeat without
`--dry-run` to apply it. The tag must match the numeric target exactly; only a
stable tag or a positive numbered `-rcN` suffix is accepted. `--latest` remains
stable-only. The exact tag identity is retained for rollback and interrupted
operation recovery, and image updates revalidate the selected closure.

Older installations, including 0.2.2, do not understand `--release-tag`. They
need an explicit migration driver from a separately obtained, verified Pleb
source commit containing `bin/pleb-release-hop`. Check the source provenance
before executing it, and use the full commit SHA from that release's source
instructions:

```sh
/path/to/verified-driver/bin/pleb-release-hop \
  --installed-pleb "$(command -v pleb)" --driver-ref "$DRIVER_REF" -- \
  update --to 0.2.3 --release-tag v0.2.3-rc1 --dry-run
```

The driver compares actual regular-file module bytes and executable modes
with the requested Git blobs, independently of index flags, and runs a private
verified snapshot using the installed configuration and checkout paths. It does not replace the installed
CLI before selection. Repeat without `--dry-run` only after reviewing the
reported closure. This is a distinct legacy migration entrypoint; it does not
make the old CLI support the new option. Qualification reports identify the
bootstrap source commit separately. Both routes require the target tag and its
component sources to have actually been published.

`--to` and `--latest` execute the selector extracted from the target's exact
published Plebian-OS tag. They never read closure bytes from a branch or from a
working tree. Pleb records and checks the remote tag-object identity, validates
the manifest with that target selector, proves each exact component commit is
reachable from an advertised public head or tag (fetch-by-SHA alone is not
publication), compares component direction, and reports `DOWNGRADE` or `DIVERGED`
without hiding it behind a rising release number. Release tags are currently
unsigned; the explicit trust anchor is the tag object advertised by the
configured HTTPS Git remote. Offline selection is allowed only after that
exact object identity has been recorded in Pleb's private cache.

The default supported path is one published release at a time. If `--latest`
would skip a published release, it refuses and prints the exact adjacent
`pleb update --to ...` command it will accept. A direct skip is allowed only
when `upgrade-policy.json` names that start/target pair. A bare `pleb update`
keeps its historical revalidation behavior; when a newer release exists it
says so and names `--latest`, and when the publication check is offline it says
the check was unknown instead of reporting the installation as current.

Release-controlled pins no longer share an editable file with operator choices.
On a Plebian-OS image, the selector migrates pins to root-owned mode-0644
`/etc/pleb/closure.env`; on a standalone install they move to user-owned
mode-0600 `~/.local/gpu_terminal/pleb/config/closure.env`. The adjacent
`session.env` keeps comments, unknown keys, paths, kiosk settings, and other
operator choices. Both the CLI and login session read the same pair, with an
explicit process environment still taking precedence. Migration and rollback
replace the pair transactionally, retain one prior generation, and leave a
durable recovery record before the first write. Hand-editing `closure.env` is
unsupported; use `pleb update` so the complete coordinated closure moves as a
unit.

The same command serves both installation shapes. A managed Plebian-OS image
delegates the selected closure to the target `plebian-os-update`; a standalone
Debian installation uses a private bare Plebian-OS object cache under
`$PLEB_CACHE_HOME`, deploys no OS updater, and applies the selected Pleb, Kilix,
and optional provider refs through Pleb's existing preservation transaction.
No `/etc/pleb/session.env`, Plebian-OS source checkout, or installed
`plebian-os-*` command is required for the standalone shape.

`pleb update` fast-forwards or pins `~/.local/gpu_terminal/sources/kilix`, updates the optional
`~/.local/gpu_terminal/sources/kilix-desktops/kilix-95`
desktop checkout when the selected provider needs it, reconciles the Kilix-pinned
thermal dashboard and graphics libraries, installs the configured Go toolchain
when necessary, and rebuilds the fork. It only offers to restart
LightDM when Pleb is configured as the active kiosk/autologin session. Updates
are serialized with an XDG-state lock. Before changing any participating
checkout, Pleb copies every modified tracked and untracked path to
`~/.local/gpu_terminal/pleb/state/update-preserve/`, records the checkout,
commit, and verbatim porcelain status in `STATUS`, writes `MANIFEST.sha256`,
verifies the copy, and fsyncs the files and containing directories. The update
stops without moving a checkout if preservation or verification fails. A
snapshot is limited to 1 GiB and requires a further 64 MiB of free space; the
last ten verified snapshots for each checkout are retained. `--preserve-only`
takes those snapshots and exits without fetching or moving anything.

After a successful move, an original untracked path wins its pathname. If the
release introduced the same path, the release copy is retained beside it as
`<name>.from-<short-sha>`. A modified tracked path has the opposite default:
the release version wins and the operator's exact copy is left beside it as
`<name>.local`. Pleb never attempts a textual merge and never invokes
Git's destructive clean operation; every conflict is reported and the durable
snapshot is retained.
Edits made during a failing update are preserved again with a `rollback` phase
label before any forced restore.

Pleb also snapshots both component positions, the dashboard
executable/native library, the fork engine, and their build stamps. Any
component or build failure restores that coherent pre-update state. Updates never
force-update: if a branch cannot
fast-forward, the command stops and tells you. A configured `KILIX_REF` or
`KILIX95_REF` must be a full 40-character commit SHA, is fetched from `origin`,
resolved through `FETCH_HEAD`, checked out detached, and verified; local tags
are never trusted as the source of a release pin. Mutable refs require the
corresponding explicit `*_ALLOW_MUTABLE_REF=1` trust override.

Kilix parent commits move before their pinned submodules are reconciled. Pleb
then persists recursive submodule handling in that managed checkout, so an
outer Plebian-OS rollback can restore changed or newly introduced submodules
without having to know their names in advance.

A pinned move is always reported as `component: <before> -> <after> (pinned by
<source>)`, naming the environment or the exact `session.env` that decided it.
When a pin moves an installed component **backwards**, the line says `DOWNGRADE`
and repeats the variable to export to keep the newer commit. Precedence is
unchanged — a persisted pin is the machine's declared state and an environment
override is deliberately per-run — but a rewind is never silent.

`pleb update` also updates **Pleb itself**, last, after every other component is
coherent. `PLEB_REF`, `PLEB_BRANCH` and `PLEB_REPO` follow the same rules as
their Kilix equivalents, and the move only happens when `PLEB_DIR` resolves to
the checkout the running `pleb` came from. If the moved checkout does not parse
or cannot answer `pleb version`, the previous commit is restored and the update
reports the rollback, so a failed self-update always leaves a working
installation. The new code takes effect from the next `pleb` command; when the
system session launcher becomes stale, the update says so and names
`pleb install` (which needs root) as the fix. Set `PLEB_SELF_UPDATE=0` to keep
the checkout where it is.

Kilix Voice stays lazily installed: an update **refreshes** the voice closure to
its pinned commit when the tools are already installed, and never installs them
as a side effect when they are not. A read-aloud-only machine is refreshed
read-aloud-only, so an update cannot enrol it in the recognition closure.

The Plebian-OS updater passes its already-held copy of this same lock through an
inherited file descriptor. Pleb validates and borrows that lock without
releasing the parent updater's ownership; that run owns the Pleb checkout, so
the self-update step stands down for it.

The canonical fork-build stamp is stored at
`~/.local/gpu_terminal/kilix/state/fork-built-ref`, beside the Kilix generation
it describes, and never inside a source checkout or duplicated under Pleb
state. Pleb snapshots and restores that same Kilix-owned stamp during updates,
and transactionally retires the legacy Pleb-side duplicate when encountered.
Pleb-owned configuration, cache, persistent state, live session files, and
durable data use the
`~/.local/gpu_terminal/pleb/{config,state,cache,session,data}` layout.
Install (including a Plebian-OS-managed install), update, testing, kiosk changes,
the Go fetcher, and every login reconcile the component root and all five
categories as user-owned, non-symlink `0700` directories while preserving their
contents. Path overrides must be normalized absolute paths and each category
must remain a strict descendant of `PLEB_STORAGE_HOME`; unsafe, broad, linked,
or escaped layouts are refused before any directory mode is changed. A missing
intermediate in a custom nested category path is created as `0700`; an existing
intermediate is validated as user-owned, non-linked, and not group/world
writable, but is not itself a configured category boundary and is not chmodded.
The login
launcher keeps `state/session.log` as `0600` and rotates a log larger than 1 MiB
to `session.log.1` at the next login.
