#!/usr/bin/env bash
# lib/common.sh — shared helpers for the `pleb` CLI. Sourced, not executed.
# Several vars below are consumed by the other sourced modules (install.sh,
# test.sh, autologin.sh) and by bin/pleb, which shellcheck can't see here.
# shellcheck disable=SC2034

# --- paths -------------------------------------------------------------------
# PLEB_ROOT is the checkout dir (~/pleb). Resolve relative to this file.
PLEB_ROOT="${PLEB_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PLEB_BIN_SRC="$PLEB_ROOT/bin/pleb-session"
PLEB_DESKTOP_IN="$PLEB_ROOT/share/pleb.desktop.in"

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
KILIX_REF="${KILIX_REF:-}"         # optional exact commit/tag

# Desktop provider passed through to `kilix desktop`. Pleb defaults to the
# external Kilix 95 provider for desktop sessions, but callers can select
# builtin, auto, command, or none.
KILIX_DESKTOP_PROVIDER="${KILIX_DESKTOP_PROVIDER:-external}"
KILIX_DESKTOP_COMMAND="${KILIX_DESKTOP_COMMAND:-}"
KILIX_DESKTOP_NAME="${KILIX_DESKTOP_NAME:-desktop}"

# Optional Kilix 95 desktop checkout. Plain Pleb shell sessions and custom
# desktop commands do not require it; install/update touch it only when the
# selected provider needs it or PLEB_INSTALL_KILIX95=1.
KILIX95_DIR="${KILIX95_DIR:-$HOME/kilix-95}"
KILIX95_REPO="${KILIX95_REPO:-https://github.com/itsmygithubacct/kilix-95.git}"
KILIX95_BRANCH="${KILIX95_BRANCH:-}"   # empty = the repo's default branch
KILIX95_REF="${KILIX95_REF:-}"         # optional exact commit/tag

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
    case "${PLEB_DESKTOP:-0}" in
        1|yes|true|on|desktop|kilix95|kilix-95|command|custom) return 0 ;;
        *) return 1 ;;
    esac
}

kilix95_required() {
    [ "${PLEB_INSTALL_KILIX95:-0}" = 1 ] && return 0
    desktop_enabled || return 1
    case "${KILIX_DESKTOP_PROVIDER:-external}" in
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
