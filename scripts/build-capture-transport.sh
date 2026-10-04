#!/usr/bin/env bash
set -euo pipefail
[ "$#" = 1 ] || { echo 'usage: build-capture-transport.sh OUTPUT' >&2; exit 2; }
source_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
compiler=${CC:-cc}
command -v "$compiler" >/dev/null
flag_text=$(pkg-config --cflags --libs libpipewire-0.3)
read -r -a flags <<< "$flag_text"
"$compiler" -shared -fPIC -O2 -Wall -Wextra -Werror -Wno-unused-parameter \
    -fstack-protector-strong -D_FORTIFY_SOURCE=2 -Wl,-z,relro,-z,now \
    -o "$1" "$source_root/lib/capture_transport.c" "${flags[@]}"
