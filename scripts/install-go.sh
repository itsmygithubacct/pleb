#!/usr/bin/env bash
# install-go.sh — install/upgrade the Go toolchain from the official go.dev
# binary tarball into /usr/local/go, independent of any Go your distro's package
# manager installed. Symlinks /usr/local/bin/{go,gofmt} so it wins on PATH.
#
#   install-go.sh [fetch|install|all] [VERSION]
#     fetch     download + sha256-verify the tarball into the cache   (no sudo)
#     install   extract to /usr/local + symlink                       (needs sudo)
#     all       fetch then install                                    (default)
#   VERSION     e.g. go1.26.4   (default: latest stable from go.dev)
#
# Cache dir: $GO_CACHE (default ~/.cache/pleb/go). 'fetch' is safe to run
# unprivileged ahead of time; 'install' only extracts the verified tarball.
set -euo pipefail

GO_CACHE="${GO_CACHE:-$HOME/.cache/pleb/go}"
OS=linux
case "$(uname -m)" in
    x86_64)        ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac

log() { printf '\033[1;32m[go]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[go]\033[0m %s\n' "$*" >&2; exit 1; }

resolve_version() {   # echo goX.Y.Z (arg overrides; else latest stable)
    if [ -n "${1:-}" ]; then echo "$1"
    else curl -fsSL "https://go.dev/VERSION?m=text" | head -1; fi
}

expected_sha() {      # $1=version -> sha256 for this os/arch from the dl json
    curl -fsSL "https://go.dev/dl/?mode=json&include=all" | python3 -c '
import sys, json
v, osn, arch = sys.argv[1], sys.argv[2], sys.argv[3]
fn = "%s.%s-%s.tar.gz" % (v, osn, arch)
for rel in json.load(sys.stdin):
    if rel.get("version") == v:
        for a in rel.get("files", []):
            if a.get("filename") == fn:
                print(a["sha256"]); sys.exit(0)
sys.exit("no checksum found for " + fn)
' "$1" "$OS" "$ARCH"
}

do_fetch() {
    local v file url out want
    v="$(resolve_version "${1:-}")"
    file="$v.$OS-$ARCH.tar.gz"; url="https://go.dev/dl/$file"
    mkdir -p "$GO_CACHE"; out="$GO_CACHE/$file"
    want="$(expected_sha "$v")" || die "could not get checksum for $v"
    if [ -f "$out" ] && printf '%s  %s\n' "$want" "$out" | sha256sum -c - >/dev/null 2>&1; then
        log "cached + verified: $out"
    else
        log "downloading $url"
        curl -fL --progress-bar "$url" -o "$out.tmp"
        printf '%s  %s\n' "$want" "$out.tmp" | sha256sum -c - >/dev/null 2>&1 \
            || { rm -f "$out.tmp"; die "sha256 MISMATCH — refusing to install"; }
        mv "$out.tmp" "$out"
        log "verified sha256 OK -> $out"
    fi
    printf '%s\n' "$out" > "$GO_CACHE/.latest"
    printf '%s\n' "$v"   > "$GO_CACHE/.version"
}

do_install() {
    local out ver
    out="$(cat "$GO_CACHE/.latest"  2>/dev/null || true)"
    ver="$(cat "$GO_CACHE/.version" 2>/dev/null || true)"
    if [ -z "$out" ] || [ ! -f "$out" ]; then die "no fetched tarball — run: $0 fetch"; fi
    log "installing $ver to /usr/local/go (needs sudo)"
    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf "$out"
    sudo ln -sf /usr/local/go/bin/go    /usr/local/bin/go
    sudo ln -sf /usr/local/go/bin/gofmt /usr/local/bin/gofmt
    hash -r 2>/dev/null || true
    log "installed: $(/usr/local/go/bin/go version)"
    log "on PATH:   $(command -v go) -> $(go version 2>/dev/null)"
}

cmd="${1:-all}"; ver="${2:-}"
case "$cmd" in
    fetch)   do_fetch "$ver" ;;
    install) do_install ;;
    all)     do_fetch "$ver"; do_install ;;
    go[0-9]*) do_fetch "$cmd"; do_install ;;   # allow: install-go.sh go1.26.4
    -h|--help|help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) die "usage: $0 [fetch|install|all] [VERSION]" ;;
esac
