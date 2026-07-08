#!/usr/bin/env bash
# lib/install.sh — install/uninstall the Pleb LightDM session. Sourced by `pleb`.

# ensure_kilix — make sure a kilix checkout with a runnable engine exists,
# cloning it fresh from upstream if it isn't there yet. A plain (non-recursive)
# clone + prebuilt kitty is enough to run; the clickable-button fork is built
# later on demand (`kilix --build` / `pleb update`, which init the submodule).
ensure_kilix() {
    if [ -d "$KILIX_DIR/.git" ] && [ -x "$KILIX_DIR/kilix" ]; then
        validate_checkout_origin "$KILIX_DIR" "$KILIX_REPO" "kilix"
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
    if [ -n "$KILIX_REF" ]; then
        log "checking out kilix ref $KILIX_REF"
        git -C "$KILIX_DIR" fetch --tags origin >/dev/null 2>&1 || true
        git -C "$KILIX_DIR" checkout --detach "$KILIX_REF" \
            || die "could not check out KILIX_REF=$KILIX_REF"
        git -C "$KILIX_DIR" submodule update --init --recursive \
            || die "kilix submodule update failed"
    fi
    ensure_engine
}

# ensure_kilix95 — make sure the optional Kilix 95 desktop checkout exists
# when this Pleb install is configured to boot directly into the desktop.
ensure_kilix95() {
    if ! kilix95_required; then
        return 0
    fi

    if [ -d "$KILIX95_DIR/.git" ] && [ -f "$KILIX95_DIR/main.py" ]; then
        validate_checkout_origin "$KILIX95_DIR" "$KILIX95_REPO" "kilix 95"
        log "kilix 95 present at $KILIX95_DIR (use 'pleb update' to update it)"
    else
        [ -e "$KILIX95_DIR" ] && [ ! -d "$KILIX95_DIR/.git" ] \
            && die "$KILIX95_DIR exists but isn't a kilix 95 checkout — move it aside first"
        command -v git >/dev/null 2>&1 || die "git is required to clone kilix 95"
        log "cloning kilix 95 -> $KILIX95_DIR"
        # shellcheck disable=SC2086  # optional --branch is intentionally unquoted-split
        git clone ${KILIX95_BRANCH:+--branch "$KILIX95_BRANCH"} "$KILIX95_REPO" "$KILIX95_DIR" \
            || die "git clone failed ($KILIX95_REPO)"
    fi

    if [ -n "$KILIX95_REF" ]; then
        log "checking out kilix 95 ref $KILIX95_REF"
        git -C "$KILIX95_DIR" fetch --tags origin >/dev/null 2>&1 || true
        git -C "$KILIX95_DIR" checkout --detach "$KILIX95_REF" \
            || die "could not check out KILIX95_REF=$KILIX95_REF"
    fi
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

link_command() {
    local target="$1" dest="$2" label="$3" existing=""
    if [ -L "$dest" ]; then
        existing="$(readlink "$dest")"
        if [ "$existing" = "$target" ]; then
            log "$label command already linked at $dest"
            return 0
        fi
    elif [ -e "$dest" ]; then
        existing="$dest"
    fi
    if [ -n "$existing" ] && [ "${PLEB_INSTALL_FORCE_LINKS:-0}" != 1 ]; then
        die "$dest already exists and is not the expected $label symlink (set PLEB_INSTALL_FORCE_LINKS=1 to replace it)"
    fi
    [ -n "$existing" ] && warn "replacing existing $dest"
    run_root ln -sfn "$target" "$dest"
}

# do_install — ensure kilix is present, copy pleb-session to /usr/local/bin, and
# drop the xsession entry so LightDM lists "Pleb" as a choosable session.
do_install() {
    [ -f "$PLEB_BIN_SRC" ]    || die "missing $PLEB_BIN_SRC"
    [ -f "$PLEB_DESKTOP_IN" ] || die "missing $PLEB_DESKTOP_IN"

    ensure_kilix   # fresh-clone kilix + set up an engine if not already present
    ensure_kilix95 # optional: external Kilix 95 when the selected provider needs it

    log "installing session launcher -> $SESSION_BIN_DST"
    run_root install -D -m 0755 "$PLEB_BIN_SRC" "$SESSION_BIN_DST"

    log "installing xsession entry -> $XSESSION_DST"
    sed "s#@SESSION_BIN@#$SESSION_BIN_DST#g" "$PLEB_DESKTOP_IN" | write_root "$XSESSION_DST"

    # put `kilix` on PATH so `kilix desktop` / `kilix serve` etc. work anywhere
    log "linking kilix command -> $KILIX_LINK"
    link_command "$KILIX_DIR/kilix" "$KILIX_LINK" "kilix"

    # and `pleb` itself, so `pleb update` / `pleb status` etc. work anywhere
    # (bin/pleb resolves its checkout through the symlink via readlink -f)
    log "linking pleb command -> $PLEB_LINK"
    link_command "$PLEB_ROOT/bin/pleb" "$PLEB_LINK" "pleb"

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
    # remove the kilix command symlink, but only if it points at our checkout
    if [ -L "$KILIX_LINK" ] && [ "$(readlink "$KILIX_LINK")" = "$KILIX_DIR/kilix" ]; then
        log "removing kilix command symlink $KILIX_LINK"
        run_root rm -f "$KILIX_LINK"
        removed=1
    fi
    # likewise the pleb command symlink, only if it points at our checkout
    if [ -L "$PLEB_LINK" ] && [ "$(readlink "$PLEB_LINK")" = "$PLEB_ROOT/bin/pleb" ]; then
        log "removing pleb command symlink $PLEB_LINK"
        run_root rm -f "$PLEB_LINK"
        removed=1
    fi
    # also drop autologin if it points at pleb
    if [ -f "$AUTOLOGIN_CONF" ]; then
        log "removing autologin config $AUTOLOGIN_CONF"
        run_root rm -f "$AUTOLOGIN_CONF"
        removed=1
    fi
    if [ "$removed" = 1 ]; then log "uninstalled."; else warn "nothing installed."; fi
}
