#!/usr/bin/env bash
# lib/autologin.sh — toggle LightDM autologin into the Pleb session ("kiosk"
# boot: straight to fullscreen kilix, no greeter). Sourced by `pleb`.

_pleb_session_name() { basename "$XSESSION_DST" .desktop; }   # -> "pleb"

# autologin_on [USER] — configure LightDM to auto-log USER (default: invoking
# user) straight into the Pleb session.
autologin_on() {
    [ -f "$XSESSION_DST" ] || die "session not installed yet — run: pleb install"
    local user="${1:-$(target_user)}" session
    session="$(_pleb_session_name)"
    id "$user" >/dev/null 2>&1 || die "no such user: $user"

    log "enabling autologin: $user -> session '$session'"
    write_root "$AUTOLOGIN_CONF" <<EOF
# Managed by \`pleb autologin\`. Remove with: pleb autologin off
[Seat:*]
autologin-user=$user
autologin-user-timeout=0
autologin-session=$session
EOF

    # Some lightdm/PAM setups gate autologin behind a group check in
    # /etc/pam.d/lightdm-autologin ("... user ingroup <grp>"). Only when such a
    # requirement actually exists do we ensure the group + membership. Modern
    # Debian, for example, checks shell/non-root instead, so no group is needed.
    # NB: the grep is inside the `if` condition so a no-match can't trip set -e.
    local pam=/etc/pam.d/lightdm-autologin grp
    if [ -f "$pam" ] && grp="$(grep -oE 'ingroup[[:space:]]+[A-Za-z0-9_-]+' "$pam" | awk '{print $NF}' | head -1)" && [ -n "$grp" ]; then
        getent group "$grp" >/dev/null 2>&1 || { log "creating group '$grp'"; run_root groupadd "$grp"; }
        if ! id -nG "$user" 2>/dev/null | tr ' ' '\n' | grep -qx "$grp"; then
            log "adding $user to group '$grp'"
            run_root usermod -aG "$grp" "$user"
        fi
    else
        log "PAM autologin requires no special group on this system"
    fi

    log "autologin enabled. Reboot (or restart lightdm) to boot into Pleb."
    warn "keep a rescue console (Ctrl+Alt+F2 -> getty) in case kilix misbehaves."
}

# autologin_off — remove the autologin config (revert to the normal greeter).
autologin_off() {
    if [ -f "$AUTOLOGIN_CONF" ]; then
        log "disabling autologin ($AUTOLOGIN_CONF)"
        run_root rm -f "$AUTOLOGIN_CONF"
        log "autologin disabled. LightDM will show the greeter again."
    else
        warn "autologin was not enabled."
    fi
}
