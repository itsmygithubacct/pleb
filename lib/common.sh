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

# kilix engine: where it lives, how to fetch it, and the launcher path.
KILIX_DIR="${KILIX_DIR:-$HOME/kilix}"
KILIX_DEFAULT="${KILIX:-$KILIX_DIR/kilix}"
KILIX_REPO="${KILIX_REPO:-https://github.com/itsmygithubacct/kilix.git}"
KILIX_BRANCH="${KILIX_BRANCH:-}"   # empty = the repo's default branch

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
