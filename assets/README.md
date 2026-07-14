# Pleb artwork bundle

This directory mirrors the approved Plebian-OS desktop artwork distribution
files byte-for-byte:

- `desktop/plebian-os.png`
- `desktop/README.md`
- `installer/ATTRIBUTION.md`
- `COPYING.GPL-2`

The Plebian wallpaper and its derivative artwork lineage are distributed under
GPL-2.0-or-later as described in `installer/ATTRIBUTION.md`; this is separate
from Pleb's MIT-licensed shell and Python code.

`pleb install` validates the exact hashes, complete PNG structure, attribution,
and license before copying the files to `$PLEB_DATA_HOME`. The source
`desktop/README.md` describes the Plebian-OS distribution path; standalone Pleb
uses `$PLEB_DATA_HOME/wallpapers/plebian-os.png` instead.

Do not replace or regenerate any one of these files without updating the whole
provenance bundle, its validator hashes, tests, and release documentation.
