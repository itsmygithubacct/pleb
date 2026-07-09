#!/usr/bin/env bash
# lib/update.sh — update the kilix checkout to the latest from upstream, rebuild
# the fork if it changed, and restart only when an active kiosk opts in.
# Sourced by `pleb`. KILIX_DIR/KILIX_BRANCH come from common.sh.

_do_restart() {
    log "restarting lightdm to relaunch the kiosk on the new kilix ..."
    run_root systemctl restart lightdm
}

_pleb_autologin_enabled() {
    [ -f "$AUTOLOGIN_CONF" ] \
        && grep -qsE '^autologin-session=pleb$' "$AUTOLOGIN_CONF"
}

# offer (or, with --yes, force) a kiosk restart to pick up the new engine
_offer_restart() {
    if [ ! -f "$XSESSION_DST" ]; then
        log "kiosk not installed — the new kilix is used next time you launch it."
        return 0
    fi
    if ! _pleb_autologin_enabled && ! kiosk_is_on; then
        log "Pleb is installed but not configured as an active kiosk; not restarting LightDM."
        return 0
    fi
    if [ "${_UPDATE_RESTART:-ask}" = "no" ]; then
        log "not restarting (--no-restart). Apply later with: sudo systemctl restart lightdm"
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
        if kilix95_required; then
            ensure_kilix95
        else
            log "kilix 95 not installed; skipping optional desktop update"
        fi
        return 0
    fi

    local before after branch current
    if ! git -C "$KILIX95_DIR" config --get remote.origin.url >/dev/null; then
        warn "kilix 95 checkout at $KILIX95_DIR has no origin remote; skipping update"
        return 0
    fi
    validate_checkout_origin "$KILIX95_DIR" "$KILIX95_REPO" "kilix 95"
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
        if [ -n "$KILIX95_BRANCH" ]; then
            current="$(git -C "$KILIX95_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
            if [ "$current" != "$KILIX95_BRANCH" ]; then
                if git -C "$KILIX95_DIR" show-ref --verify --quiet "refs/heads/$KILIX95_BRANCH"; then
                    git -C "$KILIX95_DIR" checkout "$KILIX95_BRANCH" \
                        || die "could not check out KILIX95_BRANCH=$KILIX95_BRANCH"
                else
                    git -C "$KILIX95_DIR" checkout --track -b "$KILIX95_BRANCH" "origin/$KILIX95_BRANCH" \
                        || die "could not track KILIX95_BRANCH=$KILIX95_BRANCH"
                fi
            fi
        fi
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

_kilix_go_ok_script() {
    cat <<'EOF'
command -v go >/dev/null 2>&1 || exit 1
min="${PLEBIAN_OS_KILIX_GO_MIN_VERSION:-1.26}"
ver="$(go version 2>/dev/null | awk '{print $3}' | sed 's/^go//')"
[ -n "$ver" ] || exit 1
awk -v have="$ver" -v min="$min" '
function splitver(v, out) {
    gsub(/[^0-9.].*$/, "", v)
    n = split(v, parts, ".")
    out[1] = (n >= 1 && parts[1] != "") ? parts[1] + 0 : 0
    out[2] = (n >= 2 && parts[2] != "") ? parts[2] + 0 : 0
    out[3] = (n >= 3 && parts[3] != "") ? parts[3] + 0 : 0
}
BEGIN {
    splitver(have, h)
    splitver(min, m)
    for (i = 1; i <= 3; i++) {
        if (h[i] > m[i]) exit 0
        if (h[i] < m[i]) exit 1
    }
    exit 0
}'
EOF
}

_ensure_go_for_kilix_build() {
    local min="${PLEBIAN_OS_KILIX_GO_MIN_VERSION:-1.26}"
    if env "PLEBIAN_OS_KILIX_GO_MIN_VERSION=$min" bash -lc "$(_kilix_go_ok_script)"; then
        log "Go is ready: $(go version 2>/dev/null || true)"
        return 0
    fi
    [ -x "$PLEB_ROOT/scripts/install-go.sh" ] \
        || die "Go >= $min is required to rebuild the kilix fork, and $PLEB_ROOT/scripts/install-go.sh is missing"
    log "installing/upgrading Go for kilix fork build (>= $min)"
    "$PLEB_ROOT/scripts/install-go.sh" all \
        || die "Go toolchain install failed"
    env "PLEBIAN_OS_KILIX_GO_MIN_VERSION=$min" bash -lc "$(_kilix_go_ok_script)" \
        || die "Go toolchain is still below $min after install"
    log "Go is ready: $(go version 2>/dev/null || true)"
}

_kilix_fork_enabled() {
    case "${PLEBIAN_OS_BUILD_KILIX_FORK:-1}" in
        1|yes|true|on) return 0 ;;
        0|no|false|off) return 1 ;;
        *) die "invalid PLEBIAN_OS_BUILD_KILIX_FORK=${PLEBIAN_OS_BUILD_KILIX_FORK:-} (expected 0/1)" ;;
    esac
}

_kilix_fork_head() {
    git -C "$KILIX_DIR/src" rev-parse HEAD 2>/dev/null || true
}

_kilix_fork_stamp() {
    printf '%s\n' "$KILIX_DIR/.kilix-fork-built-ref"
}

_kilix_fork_needs_rebuild() {
    _kilix_fork_enabled || return 1
    local fork kitten head stamped engine
    fork="$KILIX_DIR/src/kitty/launcher/kitty"
    kitten="$KILIX_DIR/src/kitty/launcher/kitten"
    [ -x "$fork" ] && [ -x "$kitten" ] || return 0
    head="$(_kilix_fork_head)"
    [ -n "$head" ] || return 0
    stamped="$(cat "$(_kilix_fork_stamp)" 2>/dev/null || true)"
    [ "$stamped" = "$head" ] || return 0
    engine="$("$KILIX_DIR/kilix" --which 2>/dev/null | head -1 || true)"
    [ "$engine" = "$fork" ] || return 0
    return 1
}

_rebuild_kilix_fork() {
    local fork engine head
    _kilix_fork_enabled || {
        warn "PLEBIAN_OS_BUILD_KILIX_FORK=${PLEBIAN_OS_BUILD_KILIX_FORK:-0}; not rebuilding the fork"
        return 0
    }
    _ensure_go_for_kilix_build
    log "rebuilding kilix fork (go $(go version 2>/dev/null | awk '{print $3}')) ..."
    "$KILIX_DIR/kilix" --build || die "kilix fork build failed"
    fork="$KILIX_DIR/src/kitty/launcher/kitty"
    [ -x "$fork" ] || die "kilix fork build did not produce $fork"
    engine="$("$KILIX_DIR/kilix" --which 2>/dev/null | head -1 || true)"
    [ "$engine" = "$fork" ] \
        || die "kilix is not using the fork engine after build (got: ${engine:-<empty>})"
    head="$(_kilix_fork_head)"
    [ -n "$head" ] && printf '%s\n' "$head" > "$(_kilix_fork_stamp)"
    log "fork rebuilt."
}

do_update() {
    _UPDATE_YES=0
    _UPDATE_RESTART=ask
    while [ $# -gt 0 ]; do
        case "$1" in
            -y|--yes) _UPDATE_YES=1; shift ;;
            --no-restart) _UPDATE_RESTART=no; shift ;;
            --restart) _UPDATE_RESTART=yes; shift ;;
            -h|--help) info "usage: pleb update [-y|--yes] [--no-restart|--restart]"; return 0 ;;
            *) die "unknown update option: $1" ;;
        esac
    done

    [ -d "$KILIX_DIR/.git" ] || die "no kilix git checkout at $KILIX_DIR — run 'pleb install' first"
    validate_checkout_origin "$KILIX_DIR" "$KILIX_REPO" "kilix"
    local before after branch current src_before src_after
    # track the checked-out branch (or KILIX_BRANCH if set); fall back to main
    branch="${KILIX_BRANCH:-$(git -C "$KILIX_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)}"
    { [ -n "$branch" ] && [ "$branch" != HEAD ]; } || branch=main
    before="$(git -C "$KILIX_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
    src_before="$(git -C "$KILIX_DIR/src" rev-parse HEAD 2>/dev/null || echo none)"

    if [ -n "$KILIX_REF" ]; then
        log "checking out kilix ref $KILIX_REF"
        git -C "$KILIX_DIR" fetch --tags origin || die "git fetch failed"
        git -C "$KILIX_DIR" checkout --detach "$KILIX_REF" \
            || die "could not check out KILIX_REF=$KILIX_REF"
    else
        log "fetching latest kilix ($branch) from origin"
        git -C "$KILIX_DIR" fetch --prune origin "$branch" || die "git fetch failed"
        if [ -n "$KILIX_BRANCH" ]; then
            current="$(git -C "$KILIX_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
            if [ "$current" != "$KILIX_BRANCH" ]; then
                if git -C "$KILIX_DIR" show-ref --verify --quiet "refs/heads/$KILIX_BRANCH"; then
                    git -C "$KILIX_DIR" checkout "$KILIX_BRANCH" \
                        || die "could not check out KILIX_BRANCH=$KILIX_BRANCH"
                else
                    git -C "$KILIX_DIR" checkout --track -b "$KILIX_BRANCH" "origin/$KILIX_BRANCH" \
                        || die "could not track KILIX_BRANCH=$KILIX_BRANCH"
                fi
            fi
        fi

        # fast-forward only — never silently clobber local work
        if ! git -C "$KILIX_DIR" merge --ff-only "origin/$branch"; then
            warn "cannot fast-forward $branch (local commits/changes in $KILIX_DIR?)."
            warn "resolve there and re-run 'pleb update'."
            return 1
        fi
    fi
    git -C "$KILIX_DIR" submodule update --init --recursive || die "submodule update failed"
    after="$(git -C "$KILIX_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
    src_after="$(git -C "$KILIX_DIR/src" rev-parse HEAD 2>/dev/null || echo none)"

    if [ "$before" = "$after" ] && [ "$src_before" = "$src_after" ]; then
        log "kilix already up to date at ${after:0:12}."
    else
        log "kilix updated: ${before:0:12}/${src_before:0:12} -> ${after:0:12}/${src_after:0:12}"
    fi
    if _kilix_fork_enabled; then
        if _kilix_fork_needs_rebuild; then
            _rebuild_kilix_fork
        else
            log "kilix fork already built for ${src_after:0:12}."
        fi
    else
        warn "PLEBIAN_OS_BUILD_KILIX_FORK=${PLEBIAN_OS_BUILD_KILIX_FORK:-0}; not rebuilding the fork"
    fi
    log "engine now: $("$KILIX_DIR/kilix" --which 2>/dev/null | tail -1)"

    _update_kilix95

    _offer_restart
}
