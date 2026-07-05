#!/usr/bin/env bash
# lib/kiosk.sh — toggle "hard kiosk" mode (respawn kilix if it exits) by writing
# the user env file that pleb-session sources. No sudo. Sourced by `pleb`.
#
# The env file uses `: "${PLEB_RESPAWN:=1}"` (default-only assignment) so an
# explicit PLEB_RESPAWN in the real environment still wins — which keeps
# `pleb test` (it passes PLEB_RESPAWN=0) deterministic regardless of this file.

PLEB_ENV_USER="${PLEB_ENV_USER:-${XDG_CONFIG_HOME:-$HOME/.config}/pleb/session.env}"

# respawn enabled in either env file? (an uncommented PLEB_RESPAWN line == on;
# we only ever write it when enabling and strip it when disabling).
# NB: single `grep -q` — with -q it returns 0 on a match even if one of the
# files is missing (a two-grep pipe would leak grep's exit-2 under pipefail).
kiosk_is_on() {
    grep -qsE '^[^#]*PLEB_RESPAWN' /etc/pleb/session.env "$PLEB_ENV_USER" 2>/dev/null
}

# drop any PLEB_RESPAWN line from the user env file (leaves other knobs intact)
_strip_respawn() {
    [ -f "$PLEB_ENV_USER" ] || return 0
    grep -v 'PLEB_RESPAWN' "$PLEB_ENV_USER" > "$PLEB_ENV_USER.tmp" 2>/dev/null || true
    mv "$PLEB_ENV_USER.tmp" "$PLEB_ENV_USER"
}

kiosk_on() {
    mkdir -p "$(dirname "$PLEB_ENV_USER")"
    _strip_respawn
    # shellcheck disable=SC2016  # the ${...} must be written literally, not expanded
    printf '%s\n' ': "${PLEB_RESPAWN:=1}"   # hard kiosk: respawn kilix if it exits' >> "$PLEB_ENV_USER"
    log "hard-kiosk ON — kilix will respawn if it exits"
    info "written: $PLEB_ENV_USER"
    warn "takes effect on next session start. Apply now with:"
    info "sudo systemctl restart lightdm"
}

kiosk_off() {
    _strip_respawn
    # remove the file if it's now empty
    [ -f "$PLEB_ENV_USER" ] && [ ! -s "$PLEB_ENV_USER" ] && rm -f "$PLEB_ENV_USER"
    log "hard-kiosk OFF — kilix exiting will end the session"
    warn "takes effect on next session start (sudo systemctl restart lightdm)"
}
