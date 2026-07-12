#!/usr/bin/env bash
# install-go.sh — install/upgrade the Go toolchain from the official go.dev
# binary tarball into /usr/local/go, independent of any Go your distro's package
# manager installed. Symlinks /usr/local/bin/{go,gofmt} so it wins on PATH.
#
#   install-go.sh [fetch|install|all] [VERSION]
#     fetch     download + sha256-verify the tarball into the cache   (no sudo)
#     install   extract to /usr/local + symlink                       (needs sudo)
#     all       fetch then install                                    (default)
#   VERSION     e.g. go1.26.4   (default: $GO_VERSION, then latest stable)
#
# Reproducible installs can set GO_VERSION plus GO_SHA256. Plebian-OS supplies
# those through PLEBIAN_OS_KILIX_GO_VERSION and the architecture-specific
# PLEBIAN_OS_KILIX_GO_SHA256_AMD64 / _ARM64 variables. When a checksum is
# supplied this script never consults the live checksum API.
#
# Cache dir: $GO_CACHE (default ~/.cache/pleb/go). 'fetch' is unprivileged;
# 'install' copies and re-verifies the archive in a root-owned staging directory,
# validates it, then swaps it into place with rollback on any failure.
set -euo pipefail

GO_CACHE="${GO_CACHE:-$HOME/.cache/pleb/go}"
GO_INSTALL_DIR="${GO_INSTALL_DIR:-/usr/local/go}"
GO_BIN_DIR="${GO_BIN_DIR:-/usr/local/bin}"
OS=linux
case "$(uname -m)" in
    x86_64|amd64)  ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac

log() { printf '\033[1;32m[go]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[go]\033[0m %s\n' "$*" >&2; exit 1; }

run_root() {
    if [ "$(id -u)" = 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo -- "$@"
    else
        die "need root to install Go (sudo is not available)"
    fi
}

normalize_version() {
    local version="$1"
    case "$version" in go*) ;; *) version="go$version" ;; esac
    [[ "$version" =~ ^go[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        || die "invalid Go version '$version' (expected an exact release such as go1.26.4)"
    printf '%s\n' "$version"
}

resolve_latest_version() {
    local version
    version="$(curl -fsSL "https://go.dev/VERSION?m=text" | sed -n '1p')" \
        || die "could not resolve the latest Go release"
    normalize_version "$version"
}

configured_sha() {
    if [ -n "${GO_SHA256:-}" ]; then
        printf '%s\n' "$GO_SHA256"
        return
    fi
    case "$ARCH" in
        amd64) printf '%s\n' "${GO_SHA256_AMD64:-${PLEBIAN_OS_KILIX_GO_SHA256_AMD64:-}}" ;;
        arm64) printf '%s\n' "${GO_SHA256_ARM64:-${PLEBIAN_OS_KILIX_GO_SHA256_ARM64:-}}" ;;
    esac
}

expected_sha_live() { # $1=version -> sha256 for this os/arch from go.dev JSON
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

validate_sha() {
    local sha="${1,,}"
    [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || die "invalid SHA-256 '$1'"
    printf '%s\n' "$sha"
}

file_sha() {
    sha256sum "$1" | awk '{print tolower($1)}'
}

write_manifest() {
    local version="$1" sha="$2" tmp
    tmp="$(mktemp "$GO_CACHE/.manifest.tmp.XXXXXX")" || die "could not create cache manifest"
    chmod 0600 "$tmp"
    printf '%s\n%s\n%s\n' "$version" "$ARCH" "$sha" >"$tmp"
    mv "$tmp" "$GO_CACHE/.manifest"
}

do_fetch() {
    local requested v file url out want tmp actual
    requested="${1:-${GO_VERSION:-${PLEBIAN_OS_KILIX_GO_VERSION:-}}}"
    want="$(configured_sha)"
    if [ -z "$requested" ] && [ -n "$want" ]; then
        die "a pinned checksum requires an exact GO_VERSION"
    fi
    if [ -n "$requested" ]; then
        v="$(normalize_version "$requested")"
    else
        v="$(resolve_latest_version)"
    fi
    if [ -n "$want" ]; then
        want="$(validate_sha "$want")"
    else
        want="$(expected_sha_live "$v")" || die "could not get checksum for $v"
        want="$(validate_sha "$want")"
    fi
    file="$v.$OS-$ARCH.tar.gz"; url="https://go.dev/dl/$file"
    mkdir -p "$GO_CACHE"; out="$GO_CACHE/$file"
    actual="$(file_sha "$out" 2>/dev/null || true)"
    if [ -f "$out" ] && [ "$actual" = "$want" ]; then
        log "cached + verified: $out"
    else
        log "downloading $url"
        tmp="$(mktemp "$GO_CACHE/.download.XXXXXX")" || die "could not create download file"
        if ! curl -fL --progress-bar "$url" -o "$tmp"; then
            rm -f "$tmp"
            die "download failed: $url"
        fi
        actual="$(file_sha "$tmp" 2>/dev/null || true)"
        if [ "$actual" != "$want" ]; then
            rm -f "$tmp"
            die "sha256 MISMATCH — refusing to install (expected $want, got ${actual:-<unreadable>})"
        fi
        chmod 0600 "$tmp"
        mv "$tmp" "$out"
        log "verified sha256 OK -> $out"
    fi
    _FETCHED_VERSION="$v"
    _FETCHED_SHA="$want"
    write_manifest "$v" "$want"
}

_INSTALL_STAGE=""
_INSTALL_BACKUP=""
_INSTALL_HAD_OLD=0
_INSTALL_NEW=0
_INSTALL_HAD_GO_LINK=0
_INSTALL_HAD_GOFMT_LINK=0
_INSTALL_NEW_GO_LINK=0
_INSTALL_NEW_GOFMT_LINK=0
_INSTALL_COMMITTED=0
_FETCHED_VERSION=""
_FETCHED_SHA=""

_install_cleanup() {
    local rc=$? restore_ok=1
    trap - EXIT INT TERM
    set +e
    if [ "${_INSTALL_COMMITTED:-0}" != 1 ]; then
        if [ "${_INSTALL_NEW:-0}" = 1 ]; then
            run_root rm -rf "$GO_INSTALL_DIR" || restore_ok=0
        fi
        if [ "${_INSTALL_HAD_OLD:-0}" = 1 ] && [ -n "${_INSTALL_BACKUP:-}" ]; then
            run_root mv "$_INSTALL_BACKUP" "$GO_INSTALL_DIR" || restore_ok=0
        fi
        if [ -n "${_INSTALL_STAGE:-}" ]; then
            if [ "${_INSTALL_HAD_GO_LINK:-0}" = 1 ]; then
                run_root rm -f "$GO_BIN_DIR/go" || restore_ok=0
                run_root mv "$_INSTALL_STAGE/previous-go-link" "$GO_BIN_DIR/go" || restore_ok=0
            elif [ "${_INSTALL_NEW_GO_LINK:-0}" = 1 ]; then
                run_root rm -f "$GO_BIN_DIR/go" || restore_ok=0
            fi
            if [ "${_INSTALL_HAD_GOFMT_LINK:-0}" = 1 ]; then
                run_root rm -f "$GO_BIN_DIR/gofmt" || restore_ok=0
                run_root mv "$_INSTALL_STAGE/previous-gofmt-link" "$GO_BIN_DIR/gofmt" || restore_ok=0
            elif [ "${_INSTALL_NEW_GOFMT_LINK:-0}" = 1 ]; then
                run_root rm -f "$GO_BIN_DIR/gofmt" || restore_ok=0
            fi
        fi
        if [ "$restore_ok" = 1 ]; then
            [ "$rc" -eq 0 ] || log "installation failed; restored the previous Go toolchain"
        else
            printf '\033[1;31m[go]\033[0m rollback was incomplete; recovery files remain at %s\n' \
                "${_INSTALL_STAGE:-<unknown>}" >&2
        fi
    fi
    if [ "$restore_ok" = 1 ] && [ -n "${_INSTALL_STAGE:-}" ]; then
        run_root rm -rf "$_INSTALL_STAGE"
    fi
    exit "$rc"
}

do_install() {
    local -a manifest=()
    local ver arch want out actual stage_parent staged_version configured_version configured_hash
    [ -f "$GO_CACHE/.manifest" ] || die "no fetched tarball manifest — run: $0 fetch"
    mapfile -t manifest <"$GO_CACHE/.manifest"
    [ "${#manifest[@]}" -eq 3 ] || die "invalid Go cache manifest"
    ver="$(normalize_version "${manifest[0]}")"
    arch="${manifest[1]}"
    want="$(validate_sha "${manifest[2]}")"
    [ "$arch" = "$ARCH" ] || die "cached Go archive is for $arch, this machine is $ARCH"
    if [ -n "${_FETCHED_VERSION:-}" ]; then
        [ "$ver" = "$_FETCHED_VERSION" ] && [ "$want" = "$_FETCHED_SHA" ] \
            || die "Go cache manifest changed after fetch — refusing to install"
    fi
    configured_version="${GO_VERSION:-${PLEBIAN_OS_KILIX_GO_VERSION:-}}"
    if [ -n "$configured_version" ]; then
        configured_version="$(normalize_version "$configured_version")"
        [ "$ver" = "$configured_version" ] \
            || die "cache contains $ver, but configured Go version is $configured_version"
    fi
    configured_hash="$(configured_sha)"
    if [ -n "$configured_hash" ]; then
        configured_hash="$(validate_sha "$configured_hash")"
        [ "$want" = "$configured_hash" ] \
            || die "cache checksum does not match the configured trusted SHA-256"
    fi
    out="$GO_CACHE/$ver.$OS-$arch.tar.gz"
    [ -f "$out" ] || die "cached tarball is missing — run: $0 fetch"
    actual="$(file_sha "$out" 2>/dev/null || true)"
    [ "$actual" = "$want" ] || die "cached tarball checksum mismatch — run: $0 fetch"
    case "$GO_INSTALL_DIR:$GO_BIN_DIR" in
        /*:/*) ;;
        *) die "GO_INSTALL_DIR and GO_BIN_DIR must be absolute paths" ;;
    esac
    [ "$(basename "$GO_INSTALL_DIR")" = go ] \
        || die "GO_INSTALL_DIR must name a 'go' directory"
    [ "$GO_BIN_DIR" != / ] || die "GO_BIN_DIR must not be /"

    log "staging verified $ver for $GO_INSTALL_DIR (needs sudo)"
    run_root mkdir -p "$(dirname "$GO_INSTALL_DIR")" "$GO_BIN_DIR"
    stage_parent="$(dirname "$GO_INSTALL_DIR")"
    _INSTALL_STAGE="$(run_root mktemp -d "$stage_parent/.pleb-go-stage.XXXXXX")" \
        || die "could not create root-owned Go staging directory"
    trap _install_cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    run_root install -m 0600 "$out" "$_INSTALL_STAGE/archive.tar.gz"
    actual="$(run_root sha256sum "$_INSTALL_STAGE/archive.tar.gz" | awk '{print tolower($1)}')"
    [ "$actual" = "$want" ] || die "staged tarball checksum mismatch — refusing to install"
    run_root mkdir "$_INSTALL_STAGE/extract"
    run_root tar --no-same-owner --no-same-permissions \
        -C "$_INSTALL_STAGE/extract" -xzf "$_INSTALL_STAGE/archive.tar.gz"
    run_root test -x "$_INSTALL_STAGE/extract/go/bin/go" \
        || die "archive did not contain an executable go/bin/go"
    run_root test -x "$_INSTALL_STAGE/extract/go/bin/gofmt" \
        || die "archive did not contain an executable go/bin/gofmt"
    staged_version="$(run_root "$_INSTALL_STAGE/extract/go/bin/go" version)"
    case "$staged_version" in
        "go version $ver $OS/$ARCH"*) ;;
        *) die "staged toolchain identity mismatch: $staged_version" ;;
    esac
    # This root-owned stamp lets Pleb distinguish the exact verified official
    # archive from an unrelated binary that merely prints the requested version.
    printf '%s\n%s\n%s\n' "$ver" "$ARCH" "$want" \
        | run_root tee "$_INSTALL_STAGE/extract/go/.pleb-source" >/dev/null
    run_root chmod 0444 "$_INSTALL_STAGE/extract/go/.pleb-source"

    if [ -e "$GO_BIN_DIR/go" ] || [ -L "$GO_BIN_DIR/go" ]; then
        run_root mv "$GO_BIN_DIR/go" "$_INSTALL_STAGE/previous-go-link"
        _INSTALL_HAD_GO_LINK=1
    fi
    if [ -e "$GO_BIN_DIR/gofmt" ] || [ -L "$GO_BIN_DIR/gofmt" ]; then
        run_root mv "$GO_BIN_DIR/gofmt" "$_INSTALL_STAGE/previous-gofmt-link"
        _INSTALL_HAD_GOFMT_LINK=1
    fi
    if [ -e "$GO_INSTALL_DIR" ] || [ -L "$GO_INSTALL_DIR" ]; then
        _INSTALL_BACKUP="$_INSTALL_STAGE/previous-go-tree"
        run_root mv "$GO_INSTALL_DIR" "$_INSTALL_BACKUP"
        _INSTALL_HAD_OLD=1
    fi
    run_root mv "$_INSTALL_STAGE/extract/go" "$GO_INSTALL_DIR"
    _INSTALL_NEW=1
    run_root ln -s "$GO_INSTALL_DIR/bin/go" "$GO_BIN_DIR/go"
    _INSTALL_NEW_GO_LINK=1
    run_root ln -s "$GO_INSTALL_DIR/bin/gofmt" "$GO_BIN_DIR/gofmt"
    _INSTALL_NEW_GOFMT_LINK=1
    staged_version="$(run_root "$GO_INSTALL_DIR/bin/go" version)"
    case "$staged_version" in
        "go version $ver $OS/$ARCH"*) ;;
        *) die "installed toolchain validation failed: $staged_version" ;;
    esac

    _INSTALL_COMMITTED=1
    run_root rm -rf "$_INSTALL_STAGE"
    _INSTALL_STAGE=""
    trap - EXIT INT TERM
    hash -r 2>/dev/null || true
    log "installed: $staged_version"
    if command -v go >/dev/null 2>&1; then
        log "on PATH:   $(command -v go) -> $(go version 2>/dev/null)"
    fi
}

cmd="${1:-all}"; ver="${2:-}"
case "$cmd" in
    fetch)   do_fetch "$ver" ;;
    install) do_install ;;
    all)     do_fetch "$ver"; do_install ;;
    go[0-9]*|[0-9]*) do_fetch "$cmd"; do_install ;; # allow: install-go.sh go1.26.4
    -h|--help|help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) die "usage: $0 [fetch|install|all] [VERSION]" ;;
esac
