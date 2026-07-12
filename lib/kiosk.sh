#!/usr/bin/env bash
# lib/kiosk.sh — toggle "hard kiosk" mode (respawn kilix if it exits) by writing
# the user env file that pleb-session sources. No sudo. Sourced by `pleb`.
#
# pleb-session preserves values supplied in its real process environment after
# loading this file, so persisted direct assignments are deterministic while an
# explicit `PLEB_RESPAWN=0 pleb session` still wins.

PLEB_ENV_USER="${PLEB_ENV_USER:-${XDG_CONFIG_HOME:-$HOME/.config}/pleb/session.env}"

# common.sh has already loaded the system and user session env using the same
# precedence rules as pleb-session, so report the effective value rather than
# guessing from the presence of a line (PLEB_RESPAWN=0 must remain off).
kiosk_is_on() {
    case "${PLEB_RESPAWN:-0}" in
        1|yes|true|on) return 0 ;;
        *) return 1 ;;
    esac
}

# drop any PLEB_RESPAWN line from the user env file (leaves other knobs intact)
_strip_respawn() {
    [ -f "$PLEB_ENV_USER" ] || return 0
    grep -v 'PLEB_RESPAWN' "$PLEB_ENV_USER" > "$PLEB_ENV_USER.tmp" 2>/dev/null || true
    mv "$PLEB_ENV_USER.tmp" "$PLEB_ENV_USER"
}

kiosk_on() {
    [ "$(id -u)" != 0 ] || die "'pleb kiosk' is per-user; run it without sudo"
    mkdir -p "$(dirname "$PLEB_ENV_USER")"
    _strip_respawn
    printf '%s\n' 'PLEB_RESPAWN=1   # hard kiosk: respawn kilix if it exits' >> "$PLEB_ENV_USER"
    PLEB_RESPAWN=1
    log "hard-kiosk ON — kilix will respawn if it exits"
    info "written: $PLEB_ENV_USER"
    warn "takes effect on next session start. Apply now with:"
    info "sudo systemctl restart lightdm"
}

kiosk_off() {
    [ "$(id -u)" != 0 ] || die "'pleb kiosk' is per-user; run it without sudo"
    _strip_respawn
    mkdir -p "$(dirname "$PLEB_ENV_USER")"
    # Keep an explicit user-level off value so a system-wide default of 1 is
    # actually reversible without editing /etc.
    printf '%s\n' 'PLEB_RESPAWN=0   # hard kiosk disabled by pleb kiosk off' >> "$PLEB_ENV_USER"
    PLEB_RESPAWN=0
    log "hard-kiosk OFF — kilix exiting will end the session"
    warn "takes effect on next session start (sudo systemctl restart lightdm)"
}
