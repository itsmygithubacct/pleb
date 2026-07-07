#!/usr/bin/env bash
# lib/update.sh — update the kilix checkout to the latest from upstream, rebuild
# the fork if it changed, and offer to restart the kiosk on the new version.
# Sourced by `pleb`. KILIX_DIR/KILIX_BRANCH come from common.sh.

_do_restart() {
    log "restarting lightdm to relaunch the kiosk on the new kilix ..."
    run_root systemctl restart lightdm
}

# offer (or, with --yes, force) a kiosk restart to pick up the new engine
_offer_restart() {
    if [ ! -f "$XSESSION_DST" ]; then
        log "kiosk not installed — the new kilix is used next time you launch it."
        return 0
    fi
    if [ "${_UPDATE_YES:-0}" = 1 ]; then _do_restart; return; fi
    if [ ! -t 0 ]; then
        warn "non-interactive; not restarting. Apply with: sudo systemctl restart lightdm"
        return 0
    fi
    ask "Restart the kilix kiosk now to use the new version? [y/N]"
    read -r ans
    case "$ans" in
        y|Y|yes|YES) _do_restart ;;
        *) log "not restarting. Apply later with: sudo systemctl restart lightdm" ;;
    esac
}

_update_kilix95() {
    if [ ! -d "$KILIX95_DIR/.git" ]; then
        if [ "${PLEB_DESKTOP:-0}" = 1 ] || [ "${PLEB_INSTALL_KILIX95:-0}" = 1 ]; then
            ensure_kilix95
        else
            log "kilix 95 not installed; skipping optional desktop update"
        fi
        return 0
    fi

    local before after branch
    if ! git -C "$KILIX95_DIR" config --get remote.origin.url >/dev/null; then
        warn "kilix 95 checkout at $KILIX95_DIR has no origin remote; skipping update"
        return 0
    fi
    before="$(git -C "$KILIX95_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"

    if [ -n "$KILIX95_REF" ]; then
        log "fetching kilix 95 tags/refs from origin"
        git -C "$KILIX95_DIR" fetch --tags origin || die "kilix 95 fetch failed"
        git -C "$KILIX95_DIR" checkout --detach "$KILIX95_REF" \
            || die "could not check out KILIX95_REF=$KILIX95_REF"
    else
        branch="${KILIX95_BRANCH:-$(git -C "$KILIX95_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)}"
        { [ -n "$branch" ] && [ "$branch" != HEAD ]; } || branch=main
        log "fetching latest kilix 95 ($branch) from origin"
        git -C "$KILIX95_DIR" fetch --prune origin "$branch" || die "kilix 95 fetch failed"
        if ! git -C "$KILIX95_DIR" merge --ff-only "origin/$branch"; then
            warn "cannot fast-forward $branch (local commits/changes in $KILIX95_DIR?)."
            warn "resolve there and re-run 'pleb update'."
            return 1
        fi
    fi

    after="$(git -C "$KILIX95_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
    if [ "$before" = "$after" ]; then
        log "kilix 95 already up to date at ${after:0:12}."
    else
        log "kilix 95 updated: ${before:0:12} -> ${after:0:12}"
    fi
}

do_update() {
    _UPDATE_YES=0
    case "${1:-}" in -y|--yes) _UPDATE_YES=1 ;; esac

    [ -d "$KILIX_DIR/.git" ] || die "no kilix git checkout at $KILIX_DIR — run 'pleb install' first"
    local before after branch
    # track the checked-out branch (or KILIX_BRANCH if set); fall back to main
    branch="${KILIX_BRANCH:-$(git -C "$KILIX_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)}"
    { [ -n "$branch" ] && [ "$branch" != HEAD ]; } || branch=main
    before="$(git -C "$KILIX_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"

    log "fetching latest kilix ($branch) from origin"
    git -C "$KILIX_DIR" fetch --prune origin "$branch" || die "git fetch failed"

    # fast-forward only — never silently clobber local work
    if ! git -C "$KILIX_DIR" merge --ff-only "origin/$branch"; then
        warn "cannot fast-forward $branch (local commits/changes in $KILIX_DIR?)."
        warn "resolve there and re-run 'pleb update'."
        return 1
    fi
    git -C "$KILIX_DIR" submodule update --init --recursive || warn "submodule update had issues"
    after="$(git -C "$KILIX_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"

    if [ "$before" = "$after" ]; then
        log "kilix already up to date at ${after:0:12}."
    else
        log "kilix updated: ${before:0:12} -> ${after:0:12}"

        # rebuild the fork if we can; otherwise kilix falls back to prebuilt kitty
        if command -v go >/dev/null 2>&1; then
            log "rebuilding kilix fork (go $(go version 2>/dev/null | awk '{print $3}')) ..."
            if "$KILIX_DIR/kilix" --build; then log "fork rebuilt."
            else warn "fork build failed — keeping the previous engine binary"; fi
        else
            warn "no go toolchain found — not rebuilding the fork"
            info "install one with: ~/pleb/scripts/install-go.sh"
        fi
    fi
    log "engine now: $("$KILIX_DIR/kilix" --which 2>/dev/null | tail -1)"

    _update_kilix95

    _offer_restart
}
