# Plebian-OS installer artwork attribution

This notice applies to the artwork in this directory:

- `logo.svg`
- `logo.png`
- `splash.png`
- `banner.png`
- `banner-dark.png`

It also applies to the matching desktop treatment at
`../desktop/plebian-os.png`.

The installer remains Debian-based. Plebian-OS and its artwork are not endorsed
by Debian, the Debian Installer Team, or the upstream Ceratopsian artist.

## What is whose, from 0.2.4

From 0.2.4 every pixel of `splash.png`, `banner.png`, `banner-dark.png` and
`../desktop/plebian-os.png` is drawn by [`build/draw-artwork.py`](../../build/draw-artwork.py)
in this repository, from two inputs only:

- the angular-P mark, read from `logo.svg` (drawn small in full colour, and
  enlarged and cropped as the faint outline in the background), and
- the lettering `Plebian-OS`, drawn by the Neuronte large desktop face
  (`neuronte-ui-large-latin1-v1`), a neural-network typeface of this project
  family, published under the MIT licence with Neuronte.

These four files no longer contain or derive from Debian's Ceratopsian
artwork or the `rootskel-gtk` banner. `logo.svg` and `logo.png` are unchanged.

Through 0.2.3 the backgrounds of those four files derived from Debian 13's
Ceratopsian installer artwork by Elise Couper, and the banner from Debian's
`rootskel-gtk`. Those releases remain published, and the angular-P mark was
first sketched as an edit of that artwork (see the editing record below), so
the upstream notices are kept here in full. They are a credit and a record;
the mark reproduces none of the Ceratopsian drawing or the Debian swirl.

## License selected for this set

The files listed above are distributed under the **GNU General Public License,
version 2 or (at your option) any later version** (`GPL-2.0-or-later`). For the
releases through 0.2.3 this selected the GPL-2.0+ option offered by the
Ceratopsian copyright holder, compatible with the GPL-2+ terms recorded for the
Debian `rootskel-gtk` source used by the graphical-installer banner. The 0.2.4
drawings keep the same licence, so the notices installed with the system and
their checks are unchanged in kind.

The upstream Ceratopsian work remains alternatively available from its
copyright holder under CC-BY-SA-4.0. That alternative upstream grant does not
change the GPL-2.0-or-later distribution choice for this Plebian-OS set.

Plebian-OS-specific drawing, tracing, layout, wordmark, and other
human-authored edits are offered under GPL-2.0-or-later to the extent copyright
or related rights apply. Model-assisted steps do not remove or replace the
upstream notices and license obligations below.

The complete GPL version 2 text is included in this repository at
[`../COPYING.GPL-2`](../COPYING.GPL-2).

## Debian 13 Ceratopsian source (releases through 0.2.3)

Through 0.2.3 the layered blue backgrounds in `splash.png`, `banner.png`, and
`banner-dark.png` derived from Debian 13's Ceratopsian installer artwork. The
standalone Plebian-OS angular-P mark is an original replacement identity; it
does not reproduce the Debian swirl.

The following upstream record is preserved verbatim from the Debian source
metadata used to prepare these release assets:

```text
# ceratopsian

Debian 13 theme

Full repo with all sources at https://github.com/pccouper/

# Author

Copyright 2024 Elise Couper, couperpc@gmail.com CC-BY-SA-4.0 or GNU
GPL-2.0+ (at recipient's choice)

# Concept
Organic forms inspired by Trixie's frill, with an obvious debt to
futurePrototype.

# Process
Paper and pencil, and then Inkscape.

## Tweaks for the d-i bootloader splash screen

Steve: moved the swirl, added the debian13 text
```

The Debian artwork page identified by the package metadata is
<https://wiki.debian.org/DebianArt/Themes/Ceratopsian>.

## Debian Installer `rootskel-gtk` source (releases through 0.2.3)

Through 0.2.3 the graphical-installer banner lineage included Debian `rootskel-gtk` 13.0.4,
as extracted from the pinned Debian 13.5 netinst source. The saved Debian
machine-readable copyright record says:

```text
Upstream-Name: rootskel-gtk
Source: https://salsa.debian.org/installer-team/rootskel-gtk

Files: *
Copyright: 2005-2024 Debian Installer Team <debian-boot@lists.debian.org>
           2005 Frans Pop <fjp@debian.org>
License: GPL-2+

Files: src/usr/share/graphics/logo_debian_emerald.svg
Comment: Source: https://wiki.debian.org/DebianArt/Themes/Ceratopsian
Copyright: 2024 Elise Couper <couperpc@gmail.com>
License: GPL-2+
```

The saved license text continues:

```text
This package is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This package is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this package; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
```

On Debian systems, the complete GPL version 2 text is available at
`/usr/share/common-licenses/GPL-2`. It is also published at
<https://www.gnu.org/licenses/old-licenses/gpl-2.0.html>.

## Plebian-OS generation and editing record

The Plebian-OS identity was prepared on 2026-07-13 through a
reference-guided, model-assisted editing workflow. Steps 1 to 3 are the
record of the mark, which stays. The banner and splash that step 4 made
shipped through 0.2.3 and were replaced in 0.2.4 by the drawings described
at the top of this notice:

1. The pinned Debian Ceratopsian splash and `rootskel-gtk` banner were edited
   to remove Debian's product mark and introduce an angular terminal-P concept.
2. A model-produced isolated-logo concept was retained. Its checkerboard was
   embedded pixels, not transparency, so the approved geometry was manually
   traced into the canonical `logo.svg`.
3. `logo.png` was rendered with real transparency from that canonical vector.
4. The banner and splash were edited again using the approved angular-P geek
   mark as a fixed reference, then deterministically resized and normalized to
   the Debian Installer runtime contracts.

The private authoring archive is not distributed with this repository. It
retains the exact prompts, full model outputs, selected intermediate images,
normalization commands, review notes, and SHA-256 values. Its corresponding
prompt files are:

```text
prompts/boot-b-terminal-p.prompt.txt
prompts/gtk-b-terminal-p.prompt.txt
prompts/logo-geek-angular-p.prompt.txt
prompts/banner-geek.prompt.txt
prompts/installer-splash-geek.prompt.txt
```

The retained record does not identify a model version, seed, or an exact
SVG-to-PNG renderer command. Consequently, the prompts are provenance records,
not a claim that model generation or every export is bit-for-bit reproducible.
Future artwork revisions must record those details when the tools expose them.

The 0.2.4 splash, banner and wallpaper have no such gap: `build/draw-artwork.py`
is their whole source, it uses no random numbers, and `--check` redraws them
and compares the bytes with the tracked files.
