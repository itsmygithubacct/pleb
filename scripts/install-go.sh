#!/bin/bash -p
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
# Cache dir: $GO_CACHE (default ~/.local/gpu_terminal/pleb/cache/go).
# 'install' copies and re-verifies the archive in a root-owned staging directory,
# validates it, then swaps it into place with rollback on any failure.
set -euo pipefail

# Non-interactive Bash imports exported functions before reading this file.
# Purge them with builtins before any lookup or mutation: otherwise a caller
# can shadow commands such as curl, chmod, or mv and alter the archive/hash
# variables through Bash's dynamic function scope immediately before sudo.
while builtin read -r _ _ _inherited_function; do
    builtin unset -f -- "$_inherited_function"
done < <(builtin declare -F)
builtin unset _inherited_function

readonly TRUSTED_SYSTEM_PATH=/usr/sbin:/usr/bin:/sbin:/bin
PATH="$TRUSTED_SYSTEM_PATH"
export PATH

GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
PLEB_STORAGE_HOME="${PLEB_STORAGE_HOME:-$GPU_TERMINAL_HOME/pleb}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/gpu_terminal}"
PLEB_CONFIG_HOME="${PLEB_CONFIG_HOME:-$PLEB_STORAGE_HOME/config}"
PLEB_STATE_HOME="${PLEB_STATE_HOME:-$PLEB_STORAGE_HOME/state}"
PLEB_CACHE_HOME="${PLEB_CACHE_HOME:-$PLEB_STORAGE_HOME/cache}"
PLEB_SESSION_HOME="${PLEB_SESSION_HOME:-$PLEB_STORAGE_HOME/session}"
PLEB_DATA_HOME="${PLEB_DATA_HOME:-$PLEB_STORAGE_HOME/data}"
GO_CACHE="${GO_CACHE:-$PLEB_CACHE_HOME/go}"
GO_INSTALL_DIR="${GO_INSTALL_DIR:-/usr/local/go}"
GO_BIN_DIR="${GO_BIN_DIR:-/usr/local/bin}"
OS=linux
case "$(/usr/bin/uname -m)" in
    x86_64|amd64)  ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) echo "unsupported arch: $(/usr/bin/uname -m)" >&2; exit 1 ;;
esac

log() { printf '\033[1;32m[go]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[go]\033[0m %s\n' "$*" >&2; exit 1; }

# This script eventually promotes a downloaded archive into a root-owned
# location.  Never let the caller's PATH, curl configuration, Python startup
# hooks, or privileged-command environment participate in that trust chain.
readonly SYSTEM_ENV=/usr/bin/env
readonly SYSTEM_ID=/usr/bin/id
readonly SYSTEM_CURL=/usr/bin/curl
readonly SYSTEM_PYTHON3=/usr/bin/python3
readonly SYSTEM_SHA256SUM=/usr/bin/sha256sum
if [ -x /usr/bin/sudo ]; then
    SYSTEM_SUDO=/usr/bin/sudo
else
    SYSTEM_SUDO=
fi
readonly SYSTEM_SUDO

validate_system_binary() { # $1=absolute binary
    local path="$1" target owner mode
    [ -x "$path" ] || die "required trusted system command is unavailable: $path"
    target="$(/usr/bin/readlink -f -- "$path")" \
        || die "could not resolve trusted system command: $path"
    case "$target" in /*) ;; *) die "trusted system command did not resolve absolutely: $path" ;; esac
    owner="$(/usr/bin/stat -c '%u' -- "$target")" \
        || die "could not inspect trusted system command owner: $target"
    [ "$owner" = 0 ] || die "trusted system command is not root-owned: $target"
    mode="$(/usr/bin/stat -c '%a' -- "$target")" \
        || die "could not inspect trusted system command mode: $target"
    (( (8#$mode & 8#22) == 0 )) \
        || die "trusted system command is group/world-writable: $target"
}

for _system_binary in \
    "$SYSTEM_ENV" "$SYSTEM_ID" "$SYSTEM_CURL" "$SYSTEM_PYTHON3" "$SYSTEM_SHA256SUM"
do
    validate_system_binary "$_system_binary"
done
[ -z "$SYSTEM_SUDO" ] || validate_system_binary "$SYSTEM_SUDO"
unset _system_binary

trusted_curl() {
    # -q must be curl's first option to suppress ~/.curlrc.  The empty
    # environment also excludes caller-supplied proxy/CA/config variables, so
    # go.dev is authenticated with curl's compiled-in system CA policy.
    "$SYSTEM_ENV" -i PATH="$TRUSTED_SYSTEM_PATH" HOME=/nonexistent LC_ALL=C \
        "$SYSTEM_CURL" -q --proto '=https' --proto-redir '=https' --tlsv1.2 "$@"
}

trusted_metadata_curl() {
    trusted_curl --connect-timeout 10 --max-time 30 "$@"
}

_SCRIPT_DIR="${BASH_SOURCE[0]%/*}"
[ "$_SCRIPT_DIR" != "${BASH_SOURCE[0]}" ] || _SCRIPT_DIR=.
_PLEB_ROOT="$(builtin cd -- "$_SCRIPT_DIR/.." && builtin pwd -P)"
unset _SCRIPT_DIR
# shellcheck source=../lib/storage.sh
. "$_PLEB_ROOT/lib/storage.sh"

run_root() {
    if [ "$("$SYSTEM_ID" -u)" = 0 ]; then
        "$SYSTEM_ENV" -i PATH="$TRUSTED_SYSTEM_PATH" HOME=/root LC_ALL=C "$@"
    elif [ -n "$SYSTEM_SUDO" ]; then
        "$SYSTEM_SUDO" -- "$SYSTEM_ENV" -i \
            PATH="$TRUSTED_SYSTEM_PATH" HOME=/root LC_ALL=C "$@"
    else
        die "need root to install Go (sudo is not available)"
    fi
}

validate_install_destinations() {
    local path owner mode
    [ "$GO_INSTALL_DIR" = /usr/local/go ] \
        || die "GO_INSTALL_DIR is fixed at /usr/local/go for safe root staging"
    [ "$GO_BIN_DIR" = /usr/local/bin ] \
        || die "GO_BIN_DIR is fixed at /usr/local/bin for safe command links"
    for path in / /usr /usr/local /usr/local/bin; do
        [ ! -L "$path" ] || die "trusted Go install parent must not be a symlink: $path"
        [ -d "$path" ] || die "trusted Go install parent is missing or not a directory: $path"
        owner="$(/usr/bin/stat -c '%u' -- "$path")" \
            || die "could not inspect trusted Go install parent owner: $path"
        [ "$owner" = 0 ] || die "trusted Go install parent is not root-owned: $path"
        mode="$(/usr/bin/stat -c '%a' -- "$path")" \
            || die "could not inspect trusted Go install parent mode: $path"
        (( (8#$mode & 8#22) == 0 )) \
            || die "trusted Go install parent is group/world-writable: $path"
    done
}

normalize_version() {
    local version="$1"
    case "$version" in go*) ;; *) version="go$version" ;; esac
    [[ "$version" =~ ^go[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        || die "invalid Go version '$version' (expected an exact release such as go1.26.4)"
    printf '%s\n' "$version"
}

resolve_latest_version() {
    local response version
    response="$(trusted_metadata_curl -fsSL "https://go.dev/VERSION?m=text")" \
        || die "could not resolve the latest Go release"
    version="${response%%$'\n'*}"
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
    trusted_metadata_curl -fsSL "https://go.dev/dl/?mode=json&include=all" \
        | "$SYSTEM_ENV" -i PATH="$TRUSTED_SYSTEM_PATH" HOME=/nonexistent LC_ALL=C \
            "$SYSTEM_PYTHON3" -I -c '
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
    local line hash
    line="$("$SYSTEM_SHA256SUM" -- "$1")" || return 1
    hash="${line%%[[:space:]]*}"
    printf '%s\n' "${hash,,}"
}

write_manifest() {
    local version="$1" sha="$2" tmp
    tmp="$(mktemp "$GO_CACHE/.manifest.tmp.XXXXXX")" || die "could not create cache manifest"
    chmod 0600 "$tmp"
    printf '%s\n%s\n%s\n' "$version" "$ARCH" "$sha" >"$tmp"
    mv "$tmp" "$GO_CACHE/.manifest"
}

_external_go_cache_chain_safe() {
    local path="$1" current="" component owner mode user_id
    local -a components
    user_id="$(id -u)"
    IFS=/ read -r -a components <<<"${path#/}"
    for component in "${components[@]}"; do
        [ -n "$component" ] || continue
        current="$current/$component"
        [ ! -L "$current" ] || die "refusing external GO_CACHE with a symlink component: $current"
        if [ -e "$current" ]; then
            [ -d "$current" ] || die "external GO_CACHE component is not a directory: $current"
            owner="$(stat -c '%u' "$current" 2>/dev/null)" \
                || die "could not inspect external GO_CACHE component: $current"
            case "$owner" in
                0|"$user_id") ;;
                *) die "external GO_CACHE component has an unsafe owner: $current" ;;
            esac
            mode="$(stat -c '%a' "$current" 2>/dev/null)" \
                || die "could not inspect external GO_CACHE mode: $current"
            (( (8#$mode & 8#22) == 0 )) \
                || die "external GO_CACHE component is group/world-writable: $current"
        fi
    done
}

prepare_go_cache() {
    local input normalized owner mode
    ensure_pleb_private_storage

    normalized="$(_pleb_normalized_absolute_path "$GO_CACHE")"
    _pleb_assert_no_symlink_components "$GO_CACHE"
    input="${GO_CACHE%/}"
    [ -n "$input" ] || input=/
    [ "$input" = "$normalized" ] \
        || die "GO_CACHE must be a normalized absolute path: $GO_CACHE"
    GO_CACHE="$normalized"

    case "$GO_CACHE" in
        "$PLEB_CACHE_HOME"/*)
            _pleb_private_data_dir "$GO_CACHE"
            ;;
        *)
            # An explicit cache outside Pleb's private cache category remains
            # operator-managed: validate it, but never chmod an existing path.
            # Every ancestor must be trusted because direct `install` later
            # copies and extracts its archive through sudo.
            _external_go_cache_chain_safe "$GO_CACHE"
            if [ ! -e "$GO_CACHE" ]; then
                ( umask 077; mkdir -p -- "$GO_CACHE" ) \
                    || die "could not create GO_CACHE: $GO_CACHE"
            fi
            _external_go_cache_chain_safe "$GO_CACHE"
            owner="$(stat -c '%u' "$GO_CACHE" 2>/dev/null)" \
                || die "could not inspect GO_CACHE owner: $GO_CACHE"
            [ "$owner" = "$(id -u)" ] \
                || die "external GO_CACHE is not owned by the current user: $GO_CACHE"
            mode="$(stat -c '%a' "$GO_CACHE" 2>/dev/null)" \
                || die "could not inspect GO_CACHE mode: $GO_CACHE"
            [ "$mode" = 700 ] \
                || die "external GO_CACHE must have mode 0700: $GO_CACHE"
            ;;
    esac
}

do_fetch() {
    local requested v file url out want tmp actual
    prepare_go_cache
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
    out="$GO_CACHE/$file"
    actual="$(file_sha "$out" 2>/dev/null || true)"
    if [ -f "$out" ] && [ "$actual" = "$want" ]; then
        log "cached + verified: $out"
    else
        log "downloading $url"
        tmp="$(mktemp "$GO_CACHE/.download.XXXXXX")" || die "could not create download file"
        if ! trusted_curl -fL --progress-bar "$url" -o "$tmp"; then
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
    local ver arch want trusted_hash out actual stage_parent staged_version configured_version configured_hash
    validate_install_destinations
    prepare_go_cache
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
    if [ -n "${_FETCHED_VERSION:-}" ]; then
        trusted_hash="$_FETCHED_SHA"
    elif [ -n "$configured_hash" ]; then
        trusted_hash="$configured_hash"
    else
        trusted_hash="$(expected_sha_live "$ver")" \
            || die "could not independently verify the cached Go archive; connect to go.dev or set GO_SHA256 to the trusted official checksum"
        trusted_hash="$(validate_sha "$trusted_hash")"
    fi
    [ "$want" = "$trusted_hash" ] \
        || die "Go cache manifest does not match the independently trusted official checksum"
    out="$GO_CACHE/$ver.$OS-$arch.tar.gz"
    [ -f "$out" ] || die "cached tarball is missing — run: $0 fetch"
    actual="$(file_sha "$out" 2>/dev/null || true)"
    [ "$actual" = "$want" ] || die "cached tarball checksum mismatch — run: $0 fetch"
    log "staging verified $ver for $GO_INSTALL_DIR (needs sudo)"
    run_root mkdir -p "$(dirname "$GO_INSTALL_DIR")" "$GO_BIN_DIR"
    stage_parent="$(dirname "$GO_INSTALL_DIR")"
    _INSTALL_STAGE="$(run_root mktemp -d "$stage_parent/.pleb-go-stage.XXXXXX")" \
        || die "could not create root-owned Go staging directory"
    trap _install_cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    run_root install -m 0600 "$out" "$_INSTALL_STAGE/archive.tar.gz"
    actual="$(run_root "$SYSTEM_SHA256SUM" "$_INSTALL_STAGE/archive.tar.gz")" \
        || die "could not checksum the staged Go archive"
    actual="${actual%%[[:space:]]*}"
    actual="${actual,,}"
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

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 0
fi

cmd="${1:-all}"; ver="${2:-}"
case "$cmd" in
    fetch)   do_fetch "$ver" ;;
    install) do_install ;;
    all)     do_fetch "$ver"; do_install ;;
    go[0-9]*|[0-9]*) do_fetch "$cmd"; do_install ;; # allow: install-go.sh go1.26.4
    -h|--help|help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) die "usage: $0 [fetch|install|all] [VERSION]" ;;
esac
