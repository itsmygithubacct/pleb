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
        log "already up to date at ${after:0:12}."
        return 0
    fi
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
    log "engine now: $("$KILIX_DIR/kilix" --which 2>/dev/null | tail -1)"

    _offer_restart
}
