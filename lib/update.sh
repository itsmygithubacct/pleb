#!/usr/bin/env bash
# lib/update.sh — update the kilix checkout to the latest from upstream, rebuild
# the fork if it changed, and restart only when an active kiosk opts in.
# Sourced by `pleb`. KILIX_DIR/KILIX_BRANCH come from common.sh.

_UPDATE_LOCK_FD=""
_UPDATE_LOCK_DIR=""
_UPDATE_LOCK_BORROWED=0
_UPDATE_TXN_DIR=""
_UPDATE_TXN_ACTIVE=0
_UPDATE_TXN_COMMITTED=0

_release_update_lock() {
    if [ -n "${_UPDATE_LOCK_FD:-}" ]; then
        if [ "${_UPDATE_LOCK_BORROWED:-0}" != 1 ]; then
            flock -u "$_UPDATE_LOCK_FD" 2>/dev/null || true
            exec {_UPDATE_LOCK_FD}>&-
        fi
        _UPDATE_LOCK_FD=""
        _UPDATE_LOCK_BORROWED=0
    fi
    if [ -n "${_UPDATE_LOCK_DIR:-}" ]; then
        rmdir "$_UPDATE_LOCK_DIR" 2>/dev/null || true
        _UPDATE_LOCK_DIR=""
    fi
}

_acquire_update_lock() {
    local inherited_path expected_path
    mkdir -p "$PLEB_STATE_HOME"
    if [ -n "${PLEB_UPDATE_LOCK_FD:-}" ]; then
        [[ "$PLEB_UPDATE_LOCK_FD" =~ ^[0-9]+$ ]] \
            || die "PLEB_UPDATE_LOCK_FD must be a numeric inherited file descriptor"
        [ -e "/proc/$$/fd/$PLEB_UPDATE_LOCK_FD" ] \
            || die "PLEB_UPDATE_LOCK_FD=$PLEB_UPDATE_LOCK_FD is not open in this process"
        inherited_path="$(readlink -f "/proc/$$/fd/$PLEB_UPDATE_LOCK_FD" 2>/dev/null || true)"
        expected_path="$(readlink -m "$PLEB_STATE_HOME/update.lock")"
        [ "$inherited_path" = "$expected_path" ] \
            || die "PLEB_UPDATE_LOCK_FD must refer to $expected_path (got: ${inherited_path:-unresolved})"
        command -v flock >/dev/null 2>&1 \
            || die "flock is required to validate PLEB_UPDATE_LOCK_FD"
        flock -n -x "$PLEB_UPDATE_LOCK_FD" \
            || die "could not acquire the inherited Pleb update lock on fd $PLEB_UPDATE_LOCK_FD"
        _UPDATE_LOCK_FD="$PLEB_UPDATE_LOCK_FD"
        _UPDATE_LOCK_BORROWED=1
    elif command -v flock >/dev/null 2>&1; then
        exec {_UPDATE_LOCK_FD}>>"$PLEB_STATE_HOME/update.lock"
        _UPDATE_LOCK_BORROWED=0
        flock -n "$_UPDATE_LOCK_FD" \
            || die "another 'pleb update' is already running (lock: $PLEB_STATE_HOME/update.lock)"
    else
        _UPDATE_LOCK_DIR="$PLEB_STATE_HOME/update.lock.d"
        mkdir "$_UPDATE_LOCK_DIR" 2>/dev/null \
            || die "another 'pleb update' is already running (lock: $_UPDATE_LOCK_DIR)"
    fi
    trap _release_update_lock EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
}

_snapshot_update_path() {
    local path="$1" key="$2"
    if [ -e "$path" ] || [ -L "$path" ]; then
        : >"$_UPDATE_TXN_DIR/$key.present"
        cp -a -- "$path" "$_UPDATE_TXN_DIR/$key"
    fi
}

_restore_update_path() {
    local path="$1" key="$2" failed=0
    rm -rf -- "$path" || failed=1
    if [ -f "$_UPDATE_TXN_DIR/$key.present" ]; then
        mkdir -p "$(dirname "$path")" || failed=1
        cp -a -- "$_UPDATE_TXN_DIR/$key" "$path" || failed=1
    fi
    return "$failed"
}

_record_checkout_position() {
    local dir="$1" key="$2"
    git -C "$dir" rev-parse --verify HEAD >"$_UPDATE_TXN_DIR/$key.head" \
        || die "could not record the pre-update $key commit"
    git -C "$dir" symbolic-ref --quiet --short HEAD >"$_UPDATE_TXN_DIR/$key.branch" \
        || : >"$_UPDATE_TXN_DIR/$key.branch"
}

_restore_checkout_position() {
    local dir="$1" key="$2" head branch
    [ -f "$_UPDATE_TXN_DIR/$key.head" ] || return 0
    head="$(cat "$_UPDATE_TXN_DIR/$key.head")"
    branch="$(cat "$_UPDATE_TXN_DIR/$key.branch")"
    if [ -n "$branch" ]; then
        git -C "$dir" checkout -f "$branch" >/dev/null 2>&1 \
            && git -C "$dir" reset --hard "$head" >/dev/null 2>&1
    else
        git -C "$dir" checkout -f --detach "$head" >/dev/null 2>&1 \
            && git -C "$dir" reset --hard "$head" >/dev/null 2>&1
    fi
}

_update_transaction_rollback() {
    local failed=0 fork kitten stamp
    fork="$KILIX_DIR/src/kitty/launcher/kitty"
    kitten="$KILIX_DIR/src/kitty/launcher/kitten"
    stamp="$(_kilix_fork_stamp)"
    warn "update failed; restoring the previous coherent Kilix/Kilix 95 state"

    _restore_checkout_position "$KILIX_DIR" kilix || failed=1
    if [ "$(cat "$_UPDATE_TXN_DIR/kilix-src.initialized" 2>/dev/null || echo 1)" = 0 ]; then
        git -C "$KILIX_DIR" submodule deinit -f -- src >/dev/null 2>&1 || failed=1
    elif [ -f "$_UPDATE_TXN_DIR/kilix-src.head" ]; then
        _restore_checkout_position "$KILIX_DIR/src" kilix-src || failed=1
    fi
    if [ "$(cat "$_UPDATE_TXN_DIR/kilix95.existed" 2>/dev/null || echo 1)" = 0 ]; then
        rm -rf -- "$KILIX95_DIR" || failed=1
    elif [ -f "$_UPDATE_TXN_DIR/kilix95.head" ]; then
        _restore_checkout_position "$KILIX95_DIR" kilix95 || failed=1
    fi
    _restore_update_path "$fork" fork-kitty || failed=1
    _restore_update_path "$kitten" fork-kitten || failed=1
    _restore_update_path "$stamp" fork-stamp || failed=1

    if [ "$failed" = 0 ]; then
        log "restored the pre-update component commits and fork engine"
    else
        err "automatic update rollback was incomplete; inspect $_UPDATE_TXN_DIR before retrying"
    fi
    return "$failed"
}

_update_cleanup() {
    local rc=$? rollback_ok=1
    trap - EXIT INT TERM
    set +e
    if [ "${_UPDATE_TXN_ACTIVE:-0}" = 1 ] \
        && [ "${_UPDATE_TXN_COMMITTED:-0}" != 1 ]; then
        [ "$rc" -ne 0 ] || rc=1
        if ! _update_transaction_rollback; then
            rc=1
            rollback_ok=0
        fi
    fi
    if [ -n "${_UPDATE_TXN_DIR:-}" ] && [ "$rollback_ok" = 1 ]; then
        rm -rf -- "$_UPDATE_TXN_DIR"
        _UPDATE_TXN_DIR=""
    elif [ -n "${_UPDATE_TXN_DIR:-}" ]; then
        err "rollback recovery data retained at $_UPDATE_TXN_DIR"
    fi
    _release_update_lock
    exit "$rc"
}

_update_transaction_begin() {
    local fork kitten stamp
    _UPDATE_TXN_DIR="$(mktemp -d "$PLEB_STATE_HOME/update-rollback.XXXXXX")" \
        || die "could not create update rollback state"
    _UPDATE_TXN_ACTIVE=0
    _UPDATE_TXN_COMMITTED=0
    # Replace the lock-only EXIT trap. This cleanup always releases that same
    # lock, and rolls back only after the complete snapshot has been captured.
    trap _update_cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    chmod 0700 "$_UPDATE_TXN_DIR"

    _record_checkout_position "$KILIX_DIR" kilix
    if git -C "$KILIX_DIR/src" rev-parse --verify HEAD >/dev/null 2>&1; then
        printf '%s\n' 1 >"$_UPDATE_TXN_DIR/kilix-src.initialized"
        _record_checkout_position "$KILIX_DIR/src" kilix-src
    else
        printf '%s\n' 0 >"$_UPDATE_TXN_DIR/kilix-src.initialized"
    fi
    if [ -e "$KILIX95_DIR" ] || [ -L "$KILIX95_DIR" ]; then
        printf '%s\n' 1 >"$_UPDATE_TXN_DIR/kilix95.existed"
        if [ -d "$KILIX95_DIR/.git" ]; then
            _record_checkout_position "$KILIX95_DIR" kilix95
        fi
    else
        printf '%s\n' 0 >"$_UPDATE_TXN_DIR/kilix95.existed"
    fi
    fork="$KILIX_DIR/src/kitty/launcher/kitty"
    kitten="$KILIX_DIR/src/kitty/launcher/kitten"
    stamp="$(_kilix_fork_stamp)"
    _snapshot_update_path "$fork" fork-kitty
    _snapshot_update_path "$kitten" fork-kitten
    _snapshot_update_path "$stamp" fork-stamp
    _UPDATE_TXN_ACTIVE=1
}

_update_transaction_commit() {
    _UPDATE_TXN_COMMITTED=1
    _UPDATE_TXN_ACTIVE=0
    rm -rf -- "$_UPDATE_TXN_DIR"
    _UPDATE_TXN_DIR=""
}

_do_restart() {
    log "restarting lightdm to relaunch the kiosk on the new kilix ..."
    if command -v systemd-run >/dev/null 2>&1; then
        run_root systemd-run --unit="pleb-restart-lightdm-$$" --collect \
            --description="Restart LightDM for Pleb update" \
            /bin/sh -c '
svc=lightdm
systemctl stop "$svc" --no-block >/dev/null 2>&1 || true
sleep 2
systemctl kill -s KILL "$svc" >/dev/null 2>&1 || true
systemctl reset-failed "$svc" >/dev/null 2>&1 || true
systemctl start "$svc"
'
    else
        warn "systemd-run not found; falling back to blocking LightDM restart"
        run_root systemctl restart lightdm
    fi
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
    if [ "${_UPDATE_RESTART:-ask}" = "yes" ] || [ "${_UPDATE_YES:-0}" = 1 ]; then
        _do_restart
        return
    fi
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
        if kilix95_required; then
            die "required kilix 95 checkout at $KILIX95_DIR has no origin remote"
        fi
        warn "optional kilix 95 checkout at $KILIX95_DIR has no origin; skipping it"
        return 0
    fi
    validate_checkout_origin "$KILIX95_DIR" "$KILIX95_REPO" "kilix 95"
    before="$(git -C "$KILIX95_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"

    if [ -n "$KILIX95_REF" ]; then
        checkout_fetched_ref "$KILIX95_DIR" "$KILIX95_REF" "kilix 95"
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
exact="${PLEBIAN_OS_KILIX_GO_VERSION:-}"
expected_arch="${PLEBIAN_OS_KILIX_GO_EXPECTED_ARCH:-}"
expected_sha="${PLEBIAN_OS_KILIX_GO_EXPECTED_SHA:-}"
ver="$(go version 2>/dev/null | awk '{print $3}' | sed 's/^go//')"
[ -n "$ver" ] || exit 1
if [ -n "$exact" ]; then
    case "$exact" in go*) ;; *) exact="go$exact" ;; esac
    [ "go$ver" = "$exact" ] || exit 1
fi
if [ -n "$expected_sha" ]; then
    root="$(go env GOROOT 2>/dev/null)"
    binary="$(readlink -f "$(command -v go)" 2>/dev/null)"
    stamp="$root/.pleb-source"
    [ -n "$root" ] && [ "$binary" = "$root/bin/go" ] && [ -f "$stamp" ] || exit 1
    for trusted_path in "$root" "$root/bin" "$binary" "$stamp"; do
        owner="$(stat -c '%u' "$trusted_path" 2>/dev/null)" || exit 1
        mode="$(stat -c '%a' "$trusted_path" 2>/dev/null)" || exit 1
        [ "$owner" = 0 ] && (( (8#$mode & 8#22) == 0 )) || exit 1
    done
    source_version="$(sed -n '1p' "$stamp")"
    source_arch="$(sed -n '2p' "$stamp")"
    source_sha="$(sed -n '3p' "$stamp")"
    [ "$source_version" = "$exact" ] \
        && [ "$source_arch" = "$expected_arch" ] \
        && [ "${source_sha,,}" = "${expected_sha,,}" ] || exit 1
fi
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
    local version="${PLEBIAN_OS_KILIX_GO_VERSION:-}" arch sha=""
    local go_bin_dir="${GO_BIN_DIR:-/usr/local/bin}"
    case "$(uname -m)" in
        x86_64|amd64)
            arch=amd64
            sha="${PLEBIAN_OS_KILIX_GO_SHA256_AMD64:-}"
            ;;
        aarch64|arm64)
            arch=arm64
            sha="${PLEBIAN_OS_KILIX_GO_SHA256_ARM64:-}"
            ;;
        *) die "unsupported architecture for Go toolchain: $(uname -m)" ;;
    esac
    if [ -n "$version" ] && ! [[ "$version" =~ ^(go)?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        die "invalid PLEBIAN_OS_KILIX_GO_VERSION=$version (expected an exact release such as go1.26.4)"
    fi
    if [ -n "$version" ] && [ -z "$sha" ]; then
        die "PLEBIAN_OS_KILIX_GO_VERSION=$version requires PLEBIAN_OS_KILIX_GO_SHA256_${arch^^}"
    fi
    if [ -z "$version" ] && [ -n "$sha" ]; then
        die "a pinned Go checksum requires PLEBIAN_OS_KILIX_GO_VERSION"
    fi
    if [ -n "$sha" ]; then
        sha="${sha,,}"
        [[ "$sha" =~ ^[0-9a-f]{64}$ ]] \
            || die "invalid PLEBIAN_OS_KILIX_GO_SHA256_${arch^^} (expected 64 hexadecimal characters)"
    fi
    export PATH="$go_bin_dir:$PATH"
    if env \
        "PLEBIAN_OS_KILIX_GO_MIN_VERSION=$min" \
        "PLEBIAN_OS_KILIX_GO_VERSION=$version" \
        "PLEBIAN_OS_KILIX_GO_EXPECTED_ARCH=$arch" \
        "PLEBIAN_OS_KILIX_GO_EXPECTED_SHA=$sha" \
        bash -c "$(_kilix_go_ok_script)"; then
        log "Go is ready: $(go version 2>/dev/null || true)"
        return 0
    fi
    [ -x "$PLEB_ROOT/scripts/install-go.sh" ] \
        || die "Go >= $min is required to rebuild the kilix fork, and $PLEB_ROOT/scripts/install-go.sh is missing"
    if [ -n "$version" ]; then
        log "installing pinned Go $version ($arch) for kilix fork build"
    else
        log "installing/upgrading Go for kilix fork build (>= $min; latest stable)"
    fi
    GO_VERSION="$version" GO_SHA256="$sha" "$PLEB_ROOT/scripts/install-go.sh" all \
        || die "Go toolchain install failed"
    hash -r 2>/dev/null || true
    env "PLEBIAN_OS_KILIX_GO_MIN_VERSION=$min" \
        "PLEBIAN_OS_KILIX_GO_VERSION=$version" \
        "PLEBIAN_OS_KILIX_GO_EXPECTED_ARCH=$arch" \
        "PLEBIAN_OS_KILIX_GO_EXPECTED_SHA=$sha" \
        bash -c "$(_kilix_go_ok_script)" \
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
    printf '%s\n' "$PLEB_STATE_HOME/kilix-fork-built-ref"
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
    [ "$stamped" = "$KILIX_DIR"$'\t'"$head" ] || return 0
    engine="$("$KILIX_DIR/kilix" --which 2>/dev/null | head -1 || true)"
    [ "$engine" = "$fork" ] || return 0
    return 1
}

_rebuild_kilix_fork() {
    local fork engine head stamp stamp_tmp
    _kilix_fork_enabled || {
        warn "PLEBIAN_OS_BUILD_KILIX_FORK=${PLEBIAN_OS_BUILD_KILIX_FORK:-0}; not rebuilding the fork"
        return 0
    }
    ensure_kilix_build_deps
    _ensure_go_for_kilix_build
    log "rebuilding kilix fork (go $(go version 2>/dev/null | awk '{print $3}')) ..."
    "$KILIX_DIR/kilix" --build || die "kilix fork build failed"
    fork="$KILIX_DIR/src/kitty/launcher/kitty"
    [ -x "$fork" ] || die "kilix fork build did not produce $fork"
    engine="$("$KILIX_DIR/kilix" --which 2>/dev/null | head -1 || true)"
    [ "$engine" = "$fork" ] \
        || die "kilix is not using the fork engine after build (got: ${engine:-<empty>})"
    head="$(_kilix_fork_head)"
    if [ -n "$head" ]; then
        stamp="$(_kilix_fork_stamp)"
        mkdir -p "$(dirname "$stamp")"
        stamp_tmp="$(mktemp "${stamp}.tmp.XXXXXX")" || die "could not create fork-build stamp"
        printf '%s\t%s\n' "$KILIX_DIR" "$head" >"$stamp_tmp"
        mv "$stamp_tmp" "$stamp"
    fi
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

    _acquire_update_lock
    [ -d "$KILIX_DIR/.git" ] || die "no kilix git checkout at $KILIX_DIR — run 'pleb install' first"
    require_immutable_ref "$KILIX_REF" "$KILIX_ALLOW_MUTABLE_REF" \
        KILIX_REF KILIX_ALLOW_MUTABLE_REF
    require_immutable_ref "$KILIX95_REF" "$KILIX95_ALLOW_MUTABLE_REF" \
        KILIX95_REF KILIX95_ALLOW_MUTABLE_REF
    if [ ! -d "$KILIX95_DIR/.git" ] && kilix95_required \
        && [ -z "$KILIX95_REF" ] && [ "$KILIX95_ALLOW_UNPINNED_INSTALL" != 1 ]; then
        die "automatic Kilix 95 install requires an immutable KILIX95_REF commit SHA (set KILIX95_ALLOW_UNPINNED_INSTALL=1 only to allow an unpinned clone)"
    fi
    validate_checkout_origin "$KILIX_DIR" "$KILIX_REPO" "kilix"
    require_clean_checkout "$KILIX_DIR" "kilix"
    if [ -d "$KILIX95_DIR/.git" ]; then
        validate_checkout_origin "$KILIX95_DIR" "$KILIX95_REPO" "kilix 95"
        require_clean_checkout "$KILIX95_DIR" "kilix 95"
    fi
    local before after branch current src_before src_after current_engine
    # track the checked-out branch (or KILIX_BRANCH if set); fall back to main
    branch="${KILIX_BRANCH:-$(git -C "$KILIX_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)}"
    { [ -n "$branch" ] && [ "$branch" != HEAD ]; } || branch=main
    before="$(git -C "$KILIX_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
    src_before="$(git -C "$KILIX_DIR/src" rev-parse HEAD 2>/dev/null || echo none)"
    _update_transaction_begin

    if [ -n "$KILIX_REF" ]; then
        checkout_fetched_ref "$KILIX_DIR" "$KILIX_REF" "kilix"
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
            die "resolve there and re-run 'pleb update'."
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

    # Bring the provider to its requested commit before building the engine. If
    # either component or the build fails, the EXIT transaction restores both.
    _update_kilix95 || die "kilix 95 update failed"

    if _kilix_fork_enabled; then
        if _kilix_fork_needs_rebuild; then
            _rebuild_kilix_fork
        else
            log "kilix fork already built for ${src_after:0:12}."
        fi
    else
        warn "PLEBIAN_OS_BUILD_KILIX_FORK=${PLEBIAN_OS_BUILD_KILIX_FORK:-0}; not rebuilding the fork"
    fi
    current_engine="$("$KILIX_DIR/kilix" --which 2>/dev/null | head -1 || true)"
    [ -n "$current_engine" ] && "$KILIX_DIR/kilix" --which >/dev/null 2>&1 \
        || die "updated Kilix has no runnable engine"
    log "engine now: $current_engine"

    # Software state is now coherent. Restart failures must not undo a valid
    # update, but the lock remains held until restart handling finishes.
    _update_transaction_commit
    _offer_restart
    _release_update_lock
    trap - EXIT INT TERM
}
