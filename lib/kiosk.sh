#!/usr/bin/env bash
# lib/kiosk.sh — toggle "hard kiosk" mode (respawn kilix if it exits) by writing
# the user env file that pleb-session sources. No sudo. Sourced by `pleb`.
#
# pleb-session preserves values supplied in its real process environment after
# loading this file, so persisted direct assignments are deterministic while an
# explicit `PLEB_RESPAWN=0 pleb session` still wins.

PLEB_ENV_USER="${PLEB_ENV_USER:-${PLEB_CONFIG_HOME:-${PLEB_STORAGE_HOME:-${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}/pleb}/config}/session.env}"

# common.sh has already loaded the system and user session env using the same
# precedence rules as pleb-session, so report the effective value rather than
# guessing from the presence of a line (PLEB_RESPAWN=0 must remain off).
kiosk_is_on() {
    case "${PLEB_RESPAWN:-0}" in
        1|yes|true|on) return 0 ;;
        *) return 1 ;;
    esac
}

_write_respawn() {
    local value="$1" comment="$2" dir tmp rc
    case "$PLEB_ENV_USER" in
        /*) ;;
        *) die "Pleb configuration path must be absolute: $PLEB_ENV_USER" ;;
    esac
    dir="$(dirname "$PLEB_ENV_USER")"
    umask 077
    mkdir -p -- "$dir" || die "could not create $dir"
    [ -d "$dir" ] && [ ! -L "$dir" ] \
        && [ "$(stat -c '%u' "$dir" 2>/dev/null)" = "$(id -u)" ] \
        || die "refusing unsafe Pleb configuration directory: $dir"
    chmod 0700 -- "$dir" || die "could not make $dir private"

    if [ -e "$PLEB_ENV_USER" ] || [ -L "$PLEB_ENV_USER" ]; then
        [ -f "$PLEB_ENV_USER" ] && [ ! -L "$PLEB_ENV_USER" ] \
            && [ "$(stat -c '%u' "$PLEB_ENV_USER" 2>/dev/null)" = "$(id -u)" ] \
            && [ "$(stat -c '%h' "$PLEB_ENV_USER" 2>/dev/null)" = 1 ] \
            || die "refusing unsafe Pleb configuration file: $PLEB_ENV_USER"
    fi

    tmp="$(mktemp "$dir/.session.env.XXXXXX")" || die "could not stage $PLEB_ENV_USER"
    if [ -f "$PLEB_ENV_USER" ]; then
        if grep -vE '^[[:space:]]*(export[[:space:]]+)?PLEB_RESPAWN=' \
            "$PLEB_ENV_USER" >"$tmp"; then
            :
        else
            rc=$?
            [ "$rc" = 1 ] \
                || { rm -f -- "$tmp"; die "could not read $PLEB_ENV_USER"; }
        fi
    fi
    printf 'PLEB_RESPAWN=%s   # %s\n' "$value" "$comment" >>"$tmp" \
        || { rm -f -- "$tmp"; die "could not write $PLEB_ENV_USER"; }
    chmod 0600 -- "$tmp" \
        || { rm -f -- "$tmp"; die "could not protect $PLEB_ENV_USER"; }
    mv -f -- "$tmp" "$PLEB_ENV_USER" \
        || { rm -f -- "$tmp"; die "could not publish $PLEB_ENV_USER"; }
}

kiosk_on() {
    [ "$(id -u)" != 0 ] || die "'pleb kiosk' is per-user; run it without sudo"
    _write_respawn 1 "hard kiosk: respawn kilix if it exits"
    PLEB_RESPAWN=1
    log "hard-kiosk ON — kilix will respawn if it exits"
    info "written: $PLEB_ENV_USER"
    warn "takes effect on next session start. Apply now with:"
    info "sudo systemctl restart lightdm"
}

kiosk_off() {
    [ "$(id -u)" != 0 ] || die "'pleb kiosk' is per-user; run it without sudo"
    # Keep an explicit user-level off value so a system-wide default of 1 is
    # actually reversible without editing /etc.
    _write_respawn 0 "hard kiosk disabled by pleb kiosk off"
    PLEB_RESPAWN=0
    log "hard-kiosk OFF — kilix exiting will end the session"
    warn "takes effect on next session start (sudo systemctl restart lightdm)"
}
