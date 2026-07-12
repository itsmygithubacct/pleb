#!/usr/bin/env bash
# lib/common.sh — shared helpers for the `pleb` CLI. Sourced, not executed.
# Several vars below are consumed by the other sourced modules (install.sh,
# test.sh, autologin.sh) and by bin/pleb, which shellcheck can't see here.
# shellcheck disable=SC2034

# --- paths -------------------------------------------------------------------
# PLEB_ROOT is the checkout dir (~/pleb). Resolve relative to this file.
PLEB_ROOT="${PLEB_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Load the same persistent session defaults as bin/pleb-session before deriving
# any paths below.  Explicit values in the caller's environment win, matching
# the session launcher's behaviour.  These files have always been shell env
# files (and are sourced by pleb-session); the CLI deliberately uses that same
# established contract rather than implementing a subtly different parser.
PLEB_ENV_SYSTEM="${PLEB_ENV_SYSTEM:-/etc/pleb/session.env}"
PLEB_ENV_USER="${PLEB_ENV_USER:-${XDG_CONFIG_HOME:-$HOME/.config}/pleb/session.env}"

_pleb_config_safe_to_source() {
    local cfg="$1" owner mode dir
    [ "$(id -u)" = 0 ] || return 0
    # A root CLI must not source through a symlink or a user-replaceable parent:
    # checking only the final target leaves a stat/source race in a user-owned
    # config directory. Root-managed /etc configuration passes this walk; a
    # per-user file is deliberately ignored when the CLI itself runs as root.
    case "$cfg" in /*) ;; *) return 1 ;; esac
    [ -f "$cfg" ] && [ ! -L "$cfg" ] || return 1
    owner="$(stat -c '%u' "$cfg" 2>/dev/null)" || return 1
    mode="$(stat -c '%a' "$cfg" 2>/dev/null)" || return 1
    if [ "$owner" != 0 ] || (( (8#$mode & 8#22) != 0 )); then
        printf '[pleb] refusing to source unsafe config as root: %s\n' "$cfg" >&2
        return 1
    fi
    dir="$(dirname "$cfg")"
    while [ "$dir" != / ]; do
        owner="$(stat -c '%u' "$dir" 2>/dev/null)" || return 1
        mode="$(stat -c '%a' "$dir" 2>/dev/null)" || return 1
        if [ "$owner" != 0 ] || (( (8#$mode & 8#22) != 0 )); then
            printf '[pleb] refusing config below an unsafe directory as root: %s\n' "$cfg" >&2
            return 1
        fi
        dir="$(dirname "$dir")"
    done
}

load_pleb_session_env() {
    local vars var cfg
    vars="KILIX_DIR KILIX KILIX_REPO KILIX_BRANCH KILIX_REF KILIX_ALLOW_MUTABLE_REF KILIX_PREBUILT_VERSION KILIX_PREBUILT_SHA256 PLEB_KILIX_ARGS PLEB_WM PLEB_NO_FILL PLEB_BG PLEB_LOG PLEB_RESPAWN PLEB_DESKTOP KILIX_DESKTOP_PROVIDER KILIX_DESKTOP_COMMAND KILIX_DESKTOP_NAME KILIX_DESKTOP_FLAVOR KILIX95_AUTO_INSTALL KILIX95_DIR KILIX95_REPO KILIX95_BRANCH KILIX95_REF KILIX95_ALLOW_MUTABLE_REF KILIX95_ALLOW_UNPINNED_INSTALL PLEB_INSTALL_KILIX95 PLEB_SKIP_DEPS PLEB_UPDATE_LOCK_FD PLEBIAN_OS_BUILD_KILIX_FORK PLEBIAN_OS_KILIX_GO_MIN_VERSION PLEBIAN_OS_KILIX_GO_VERSION PLEBIAN_OS_KILIX_GO_SHA256_AMD64 PLEBIAN_OS_KILIX_GO_SHA256_ARM64"
    declare -A had saved
    for var in $vars; do
        if [[ ${!var+x} ]]; then
            had[$var]=1
            saved[$var]="${!var}"
        else
            had[$var]=0
        fi
    done
    for cfg in "$PLEB_ENV_SYSTEM" "$PLEB_ENV_USER"; do
        # shellcheck source=/dev/null
        if [ -r "$cfg" ] && _pleb_config_safe_to_source "$cfg"; then
            . "$cfg"
        fi
    done
    for var in $vars; do
        if [ "${had[$var]}" = 1 ]; then
            printf -v "$var" '%s' "${saved[$var]}"
        elif [ "$var" = PLEB_UPDATE_LOCK_FD ]; then
            # An inherited descriptor is a process capability, never persisted
            # configuration. Ignore attempts to synthesize one in an env file.
            unset "$var"
        fi
    done
}
load_pleb_session_env

PLEB_BIN_SRC="$PLEB_ROOT/bin/pleb-session"
PLEB_DESKTOP_IN="$PLEB_ROOT/share/pleb.desktop.in"
PLEB_STATE_HOME="${PLEB_STATE_HOME:-${XDG_STATE_HOME:-$HOME/.local/state}/pleb}"

# install destinations (system-wide, so LightDM/other users can see them)
SESSION_BIN_DST="${SESSION_BIN_DST:-/usr/local/bin/pleb-session}"
XSESSION_DST="${XSESSION_DST:-/usr/share/xsessions/pleb.desktop}"
AUTOLOGIN_CONF="${AUTOLOGIN_CONF:-/etc/lightdm/lightdm.conf.d/50-pleb-autologin.conf}"
# `kilix` command on PATH (so `kilix desktop`, `kilix serve`, … work out of the
# box). /usr/local/bin is on PATH and FHS-correct for local installs.
KILIX_LINK="${KILIX_LINK:-/usr/local/bin/kilix}"
# `pleb` command itself on PATH, so `pleb update`/`pleb status`/… work anywhere.
PLEB_LINK="${PLEB_LINK:-/usr/local/bin/pleb}"

# kilix engine: where it lives, how to fetch it, and the launcher path.
KILIX_DIR="${KILIX_DIR:-$HOME/kilix}"
KILIX_DEFAULT="${KILIX:-$KILIX_DIR/kilix}"
KILIX_REPO="${KILIX_REPO:-https://github.com/itsmygithubacct/kilix.git}"
KILIX_BRANCH="${KILIX_BRANCH:-}"   # empty = the repo's default branch
KILIX_REF="${KILIX_REF:-}"         # optional full commit SHA
KILIX_ALLOW_MUTABLE_REF="${KILIX_ALLOW_MUTABLE_REF:-0}"
KILIX_PREBUILT_VERSION="${KILIX_PREBUILT_VERSION:-}" # empty = latest fallback
KILIX_PREBUILT_SHA256="${KILIX_PREBUILT_SHA256:-}"   # optional pinned checksum

# Desktop provider passed through to `kilix desktop`. Pleb defaults to the
# `auto` prefers an installed external Kilix 95 provider and otherwise uses the
# bundled compatible provider. Release manifests select/pin `external` exactly.
KILIX_DESKTOP_PROVIDER="${KILIX_DESKTOP_PROVIDER:-auto}"
KILIX_DESKTOP_COMMAND="${KILIX_DESKTOP_COMMAND:-}"
KILIX_DESKTOP_NAME="${KILIX_DESKTOP_NAME:-desktop}"

# Optional Kilix 95 desktop checkout. Plain Pleb shell sessions and custom
# desktop commands do not require it; install/update touch it only when the
# selected provider needs it or PLEB_INSTALL_KILIX95=1.
KILIX95_DIR="${KILIX95_DIR:-$HOME/kilix-95}"
KILIX95_REPO="${KILIX95_REPO:-https://github.com/itsmygithubacct/kilix-95.git}"
KILIX95_BRANCH="${KILIX95_BRANCH:-}"   # empty = the repo's default branch
KILIX95_REF="${KILIX95_REF:-}"         # optional full commit SHA
KILIX95_ALLOW_MUTABLE_REF="${KILIX95_ALLOW_MUTABLE_REF:-0}"
KILIX95_ALLOW_UNPINNED_INSTALL="${KILIX95_ALLOW_UNPINNED_INSTALL:-0}"

# --- pretty output -----------------------------------------------------------
if [ -t 1 ]; then
    _c_g=$'\033[1;32m'; _c_r=$'\033[1;31m'; _c_y=$'\033[1;33m'; _c_b=$'\033[1;34m'; _c_0=$'\033[0m'
else
    _c_g=; _c_r=; _c_y=; _c_b=; _c_0=
fi
log()  { printf '%s[pleb]%s %s\n'  "$_c_g" "$_c_0" "$*"; }
warn() { printf '%s[pleb]%s %s\n'  "$_c_y" "$_c_0" "$*" >&2; }
err()  { printf '%s[pleb]%s %s\n'  "$_c_r" "$_c_0" "$*" >&2; }
die()  { err "$*"; exit 1; }
info() { printf '  %s%s%s\n' "$_c_b" "$*" "$_c_0"; }
# ask PROMPT — green [pleb] prompt with no trailing newline, for a following `read`
ask()  { printf '%s[pleb]%s %s ' "$_c_g" "$_c_0" "$*"; }

# --- privilege ---------------------------------------------------------------
# run_root CMD... — run as root: directly if already root, else via sudo.
run_root() {
    if [ "$(id -u)" = 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        die "need root for: $* (no sudo available)"
    fi
}

# write_root DEST — read stdin, write it to DEST as root (via a temp file so we
# never need a root shell redirection).
write_root() {
    local dest="$1" tmp
    tmp="$(mktemp)" || die "mktemp failed"
    cat >"$tmp"
    run_root install -D -m 0644 "$tmp" "$dest"
    rm -f "$tmp"
}

# --- misc --------------------------------------------------------------------
# the user the pleb session/autologin should belong to (the invoking user, even
# under sudo).
target_user() { echo "${SUDO_USER:-$(id -un)}"; }

desktop_enabled() {
    case "${KILIX_DESKTOP_PROVIDER:-auto}" in
        none|off|disabled) return 1 ;;
    esac
    case "${PLEB_DESKTOP:-0}" in
        1|yes|true|on|desktop|kilix95|kilix-95|command|custom) return 0 ;;
        *) return 1 ;;
    esac
}

kilix95_required() {
    [ "${PLEB_INSTALL_KILIX95:-0}" = 1 ] && return 0
    desktop_enabled || return 1
    case "${KILIX_DESKTOP_PROVIDER:-auto}" in
        external) return 0 ;;
        auto) [ ! -f "$KILIX_DIR/desktop/main.py" ] ;;
        *) return 1 ;;
    esac
}

validate_checkout_origin() {
    local dir="$1" repo="$2" label="$3" remote
    [ -d "$dir/.git" ] || return 0
    remote="$(git -C "$dir" config --get remote.origin.url 2>/dev/null || true)"
    if [ -n "$remote" ] && [ "$remote" != "$repo" ] \
        && [ "${PLEB_TRUST_EXISTING_CHECKOUT:-${PLEBIAN_OS_TRUST_EXISTING_CHECKOUT:-0}}" != 1 ]; then
        die "$label checkout at $dir has origin '$remote', expected '$repo' (set PLEB_TRUST_EXISTING_CHECKOUT=1 to override)"
    fi
}

require_clean_checkout() {
    local dir="$1" label="$2" status
    [ -d "$dir/.git" ] || return 0
    status="$(git -C "$dir" status --porcelain --untracked-files=normal 2>/dev/null)" \
        || die "could not inspect $label checkout at $dir"
    if [ -n "$status" ]; then
        err "$label checkout at $dir has local changes; refusing to update it:"
        printf '%s\n' "$status" >&2
        die "commit, stash, or remove those changes, then re-run 'pleb update'"
    fi
}

require_immutable_ref() {
    local ref="$1" allow_mutable="$2" ref_name="$3" override_name="$4"
    [ -n "$ref" ] || return 0
    if ! [[ "$ref" =~ ^[0-9a-fA-F]{40}$ ]] && [ "$allow_mutable" != 1 ]; then
        die "$ref_name must be a full 40-character commit SHA (set $override_name=1 only to trust a mutable tag/branch)"
    fi
}

checkout_fetched_ref() {
    local dir="$1" ref="$2" label="$3" resolved actual
    log "fetching exact $label ref $ref from origin"
    git -C "$dir" fetch --no-tags origin "$ref" \
        || die "$label fetch failed for ref $ref"
    resolved="$(git -C "$dir" rev-parse --verify 'FETCH_HEAD^{commit}' 2>/dev/null)" \
        || die "fetched $label ref $ref did not resolve to a commit"
    git -C "$dir" checkout --detach "$resolved" \
        || die "could not check out fetched $label ref $ref ($resolved)"
    actual="$(git -C "$dir" rev-parse --verify HEAD 2>/dev/null)" \
        || die "could not verify $label HEAD after checkout"
    [ "$actual" = "$resolved" ] \
        || die "$label checkout verification failed (expected $resolved, got $actual)"
    log "$label pinned at $resolved"
}
