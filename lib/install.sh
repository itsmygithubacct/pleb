#!/usr/bin/env bash
# lib/install.sh — install/uninstall the Pleb LightDM session. Sourced by `pleb`.

# ensure_kilix — make sure a kilix checkout with a runnable engine exists,
# cloning it fresh from upstream if it isn't there yet. A plain (non-recursive)
# clone + prebuilt kitty is enough to run; the clickable-button fork is built
# later on demand (`kilix --build` / `pleb update`, which init the submodule).
ensure_kilix() {
    if [ -d "$KILIX_DIR/.git" ] && [ -x "$KILIX_DIR/kilix" ]; then
        log "kilix present at $KILIX_DIR (use 'pleb update' to update it)"
    else
        [ -e "$KILIX_DIR" ] && [ ! -d "$KILIX_DIR/.git" ] \
            && die "$KILIX_DIR exists but isn't a kilix checkout — move it aside first"
        command -v git >/dev/null 2>&1 || die "git is required to clone kilix"
        log "cloning kilix -> $KILIX_DIR"
        # shellcheck disable=SC2086  # optional --branch is intentionally unquoted-split
        git clone ${KILIX_BRANCH:+--branch "$KILIX_BRANCH"} "$KILIX_REPO" "$KILIX_DIR" \
            || die "git clone failed ($KILIX_REPO)"
    fi
    ensure_engine
}

# ensure_engine — make sure kilix has a runnable kitty; if not, fetch the
# prebuilt one (needs only git/curl/tar). The fork (buttons) needs Go >= 1.26.
ensure_engine() {
    if "$KILIX_DIR/kilix" --which >/dev/null 2>&1; then
        log "engine: $("$KILIX_DIR/kilix" --which 2>/dev/null | tail -1)"
        return 0
    fi
    [ -x "$KILIX_DIR/bootstrap.sh" ] || die "no engine and no bootstrap.sh in $KILIX_DIR"
    log "fetching the prebuilt kilix engine ..."
    "$KILIX_DIR/bootstrap.sh" || die "kilix engine bootstrap failed"
    log "engine ready: $("$KILIX_DIR/kilix" --which 2>/dev/null | tail -1)"
}

# do_install — ensure kilix is present, copy pleb-session to /usr/local/bin, and
# drop the xsession entry so LightDM lists "Pleb" as a choosable session.
do_install() {
    [ -f "$PLEB_BIN_SRC" ]    || die "missing $PLEB_BIN_SRC"
    [ -f "$PLEB_DESKTOP_IN" ] || die "missing $PLEB_DESKTOP_IN"

    ensure_kilix   # fresh-clone kilix + set up an engine if not already present

    log "installing session launcher -> $SESSION_BIN_DST"
    run_root install -D -m 0755 "$PLEB_BIN_SRC" "$SESSION_BIN_DST"

    log "installing xsession entry -> $XSESSION_DST"
    sed "s#@SESSION_BIN@#$SESSION_BIN_DST#g" "$PLEB_DESKTOP_IN" | write_root "$XSESSION_DST"

    log "done. Log out, then at the LightDM greeter pick the session"
    info "menu (gear/badge near the login box) -> \"Pleb\" -> log in."
    warn "verify the engine first:  pleb doctor"
}

# do_uninstall — remove everything do_install created. Leaves ~/kilix and ~/pleb.
do_uninstall() {
    local removed=0
    for f in "$XSESSION_DST" "$SESSION_BIN_DST"; do
        if [ -e "$f" ]; then
            log "removing $f"
            run_root rm -f "$f"
            removed=1
        fi
    done
    # also drop autologin if it points at pleb
    if [ -f "$AUTOLOGIN_CONF" ]; then
        log "removing autologin config $AUTOLOGIN_CONF"
        run_root rm -f "$AUTOLOGIN_CONF"
        removed=1
    fi
    if [ "$removed" = 1 ]; then log "uninstalled."; else warn "nothing installed."; fi
}
