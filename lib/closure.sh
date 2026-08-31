#!/usr/bin/env bash
# lib/closure.sh — thin driver for the target Plebian-OS release selector.
#
# Validation and closure semantics stay in the selector extracted from the
# immutable target tag.  This file supplies the missing Pleb entrypoint, a
# private object cache for standalone installs, adjacent-hop policy, and the
# outer select/apply/undo sequence.

PLEBIAN_OS_REPO="${PLEBIAN_OS_REPO:-https://github.com/itsmygithubacct/plebian-os.git}"
PLEBIAN_OS_DIR="${PLEBIAN_OS_DIR:-$GPU_TERMINAL_SOURCE_HOME/plebian-os}"

_PLEB_HOP_CACHE=""
_PLEB_HOP_REPO=""
_PLEB_HOP_SELECTOR=""
_PLEB_HOP_TARGET=""
_PLEB_HOP_COMMIT=""
_PLEB_HOP_TAG_OBJECT=""
_PLEB_HOP_SELECTOR_SHA=""
_PLEB_HOP_PHASE=""
_PLEB_HOP_EMERGENCY_ROLLBACK=0
_PLEB_HOP_RECOVERY_RESULT=""

_pleb_release_version() {
    local version="${PLEBIAN_OS_VERSION:-${PLEBIAN_OS_RELEASE:-}}"
    if [ -z "$version" ]; then
        version="$(cat "$PLEB_ROOT/VERSION" 2>/dev/null || true)"
    fi
    if [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        printf '%s\n' "$version"
    else
        printf '%s\n' unknown
    fi
}

_pleb_remote_release_inventory() {
    local object ref version
    git ls-remote --refs --tags "$PLEBIAN_OS_REPO" 'refs/tags/v*' 2>/dev/null \
        | while IFS=$'\t' read -r object ref; do
            [[ "$object" =~ ^[0-9a-f]{40}$ ]] || continue
            version="${ref#refs/tags/v}"
            [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
            printf '%s\t%s\n' "$version" "$object"
        done \
        | sort -t $'\t' -k1,1V -u
}

_pleb_release_cache_prepare() {
    local remote
    ensure_pleb_private_storage
    _PLEB_HOP_CACHE="$PLEB_CACHE_HOME/release-hop"
    _PLEB_HOP_REPO="$_PLEB_HOP_CACHE/plebian-os.git"
    mkdir -p -- "$_PLEB_HOP_CACHE/selectors" "$_PLEB_HOP_CACHE/tag-trust"
    chmod 0700 -- "$_PLEB_HOP_CACHE" "$_PLEB_HOP_CACHE/selectors" \
        "$_PLEB_HOP_CACHE/tag-trust"
    if [ ! -d "$_PLEB_HOP_REPO" ]; then
        git init --bare --quiet "$_PLEB_HOP_REPO" \
            || die "could not create the private Plebian-OS release cache"
        git -C "$_PLEB_HOP_REPO" remote add origin "$PLEBIAN_OS_REPO" \
            || die "could not configure the Plebian-OS release cache"
    fi
    [ -d "$_PLEB_HOP_REPO" ] && [ ! -L "$_PLEB_HOP_REPO" ] \
        && [ "$(git -C "$_PLEB_HOP_REPO" rev-parse --is-bare-repository 2>/dev/null)" = true ] \
        || die "refusing unsafe Plebian-OS release cache: $_PLEB_HOP_REPO"
    remote="$(git -C "$_PLEB_HOP_REPO" config --get remote.origin.url 2>/dev/null || true)"
    [ "$remote" = "$PLEBIAN_OS_REPO" ] \
        || die "Plebian-OS release cache origin is '$remote', expected '$PLEBIAN_OS_REPO'"
}

_pleb_write_private_line() {
    local dst="$1" value="$2" tmp
    tmp="$(mktemp "$(dirname "$dst")/.pleb-hop.XXXXXX")" \
        || die "could not stage release-hop state"
    printf '%s\n' "$value" >"$tmp"
    chmod 0600 "$tmp"
    mv -fT -- "$tmp" "$dst"
}

_pleb_prepare_target_selector() {
    local target="$1" offline="$2" advertised="" local_object trust_file
    local selector_tmp selector_sha
    [[ "$target" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        || die "invalid release identifier: $target"
    _pleb_release_cache_prepare
    trust_file="$_PLEB_HOP_CACHE/tag-trust/v$target"
    if [ "$offline" = 1 ]; then
        [ -f "$trust_file" ] && [ ! -L "$trust_file" ] \
            || die "release tag v$target has no recorded public tag-object identity; --offline cannot trust it"
        advertised="$(cat "$trust_file")"
        [[ "$advertised" =~ ^[0-9a-f]{40}$ ]] \
            || die "release tag v$target has an invalid recorded tag-object identity"
    else
        advertised="$(git ls-remote --refs "$PLEBIAN_OS_REPO" "refs/tags/v$target" \
            2>/dev/null | awk 'NF == 2 {print $1}')"
        [[ "$advertised" =~ ^[0-9a-f]{40}$ ]] \
            || die "could not resolve one published tag object for v$target"
        log "fetching published Plebian-OS tag v$target into the private release cache"
        git -C "$_PLEB_HOP_REPO" fetch --quiet --force --no-tags origin \
            "refs/tags/v$target:refs/tags/v$target" \
            || die "could not fetch published release tag v$target"
    fi
    local_object="$(git -C "$_PLEB_HOP_REPO" rev-parse --verify \
        "refs/tags/v$target" 2>/dev/null || true)"
    [ "$local_object" = "$advertised" ] \
        || die "release tag v$target object is $local_object, not trusted public object $advertised"
    _PLEB_HOP_COMMIT="$(git -C "$_PLEB_HOP_REPO" rev-parse --verify \
        "refs/tags/v$target^{commit}" 2>/dev/null || true)"
    [[ "$_PLEB_HOP_COMMIT" =~ ^[0-9a-f]{40}$ ]] \
        || die "release tag v$target does not resolve to a commit"
    [ "$(git -C "$_PLEB_HOP_REPO" show "$_PLEB_HOP_COMMIT:VERSION" \
        2>/dev/null | tr -d '\n')" = "$target" ] \
        || die "release tag v$target disagrees with its VERSION file"
    git -C "$_PLEB_HOP_REPO" cat-file -e \
        "$_PLEB_HOP_COMMIT:releases/$target.env" 2>/dev/null \
        || die "release tag v$target has no releases/$target.env"

    _PLEB_HOP_SELECTOR="$_PLEB_HOP_CACHE/selectors/$_PLEB_HOP_COMMIT"
    selector_tmp="$(mktemp "$_PLEB_HOP_CACHE/selectors/.selector.XXXXXX")" \
        || die "could not stage the target release selector"
    git -C "$_PLEB_HOP_REPO" show \
        "$_PLEB_HOP_COMMIT:provision/plebian-os-select-closure.sh" >"$selector_tmp" \
        || die "release tag v$target has no closure selector"
    bash -n "$selector_tmp" \
        || die "release tag v$target contains an invalid closure selector"
    selector_sha="$(sha256sum "$selector_tmp" | awk '{print $1}')"
    if [ -f "$_PLEB_HOP_SELECTOR" ]; then
        [ ! -L "$_PLEB_HOP_SELECTOR" ] \
            && [ "$(sha256sum "$_PLEB_HOP_SELECTOR" | awk '{print $1}')" = "$selector_sha" ] \
            || die "cached selector for $_PLEB_HOP_COMMIT does not match the target tag"
        rm -f -- "$selector_tmp"
    else
        chmod 0700 "$selector_tmp"
        mv -fT -- "$selector_tmp" "$_PLEB_HOP_SELECTOR"
    fi
    [ "$offline" = 1 ] || _pleb_write_private_line "$trust_file" "$advertised"
    _PLEB_HOP_TARGET="$target"
    _PLEB_HOP_TAG_OBJECT="$advertised"
    _PLEB_HOP_SELECTOR_SHA="$selector_sha"
    warn "release tags are unsigned; trusting tag object $advertised advertised by $PLEBIAN_OS_REPO"
}

_pleb_policy_allows_skip() {
    local current="$1" target="$2"
    git -C "$_PLEB_HOP_REPO" show \
        "$_PLEB_HOP_COMMIT:releases/upgrade-policy.json" 2>/dev/null \
        | python3 -c '
import json, sys
current, target = sys.argv[1:]
try:
    policy = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(2)
for item in policy.get("supported_upgrade_paths", []):
    if isinstance(item, str) and item == f"{current}->{target}":
        raise SystemExit(0)
    if isinstance(item, dict) and item.get("from") == current and item.get("to") == target:
        raise SystemExit(0)
raise SystemExit(1)
' "$current" "$target"
}

_pleb_enforce_adjacent_hop() {
    local current="$1" target="$2" inventory="$3" next="" version
    [ "$current" != unknown ] \
        || die "the installed release is unknown; set PLEBIAN_OS_VERSION before selecting $target"
    [ "$current" != "$target" ] || return 0
    if [ "$(printf '%s\n%s\n' "$target" "$current" | sort -V | head -n 1)" = "$target" ]; then
        warn "requested release $target is older than installed release $current; component directions will be validated before this downgrade"
        return 0
    fi
    while IFS=$'\t' read -r version _; do
        [ -n "$version" ] || continue
        if [ "$(printf '%s\n%s\n' "$current" "$version" | sort -V | head -n 1)" = "$current" ] \
                && [ "$version" != "$current" ]; then
            next="$version"
            break
        fi
    done <<<"$inventory"
    [ -n "$next" ] || die "no published release follows installed release $current"
    if [ "$target" != "$next" ] && ! _pleb_policy_allows_skip "$current" "$target"; then
        die "unsupported release skip $current -> $target; the accepted adjacent hop is: pleb update --to $next"
    fi
}

_pleb_refuse_release_env_overrides() {
    local key
    for key in $PLEB_RELEASE_CONTROLLED_KEYS; do
        [ "$(_pleb_value_origin "$key")" != "the environment" ] \
            || die "cannot select a release while $key is overridden by the process environment"
    done
}

_pleb_selector_env() {
    local selector="$1"; shift
    local mode session closure recovery selector_dst updater_dst
    if [ "${PLEBIAN_OS_MANAGED_INSTALL:-0}" = 1 ]; then
        mode=image
        session="$PLEB_ENV_SYSTEM"
        closure="$PLEB_CLOSURE_SYSTEM"
        recovery="${PLEBIAN_OS_RECOVERY_BASE:-/var/lib/plebian-os}"
        selector_dst="${PLEBIAN_OS_SELECTOR_DST:-/usr/local/bin/plebian-os-select-closure}"
        updater_dst="${PLEBIAN_OS_UPDATER_DST:-/usr/local/bin/plebian-os-update}"
    else
        mode=standalone
        session="$PLEB_ENV_USER"
        closure="$PLEB_CLOSURE_USER"
        recovery="$PLEB_STATE_HOME/release-hop"
        selector_dst=""
        updater_dst=""
    fi
    env \
        "PLEBIAN_OS_SELECTOR_MODE=$mode" \
        "PLEBIAN_OS_CLOSURE_LAYOUT=split" \
        "PLEBIAN_OS_SESSION_ENV=$session" \
        "PLEBIAN_OS_CLOSURE_ENV=$closure" \
        "PLEBIAN_OS_RECOVERY_BASE=$recovery" \
        "PLEBIAN_OS_SELECTOR_DST=$selector_dst" \
        "PLEBIAN_OS_UPDATER_DST=$updater_dst" \
        "PLEBIAN_OS_COMPONENT_CACHE_HOME=$PLEB_CACHE_HOME/release-hop/components" \
        "PLEBIAN_OS_REPO=$PLEBIAN_OS_REPO" \
        "PLEBIAN_OS_PLEB_DIR=$PLEB_ROOT" \
        "PLEBIAN_OS_KILIX_DIR=$KILIX_DIR" \
        "PLEBIAN_OS_KILIX95_DIR=$KILIX95_DIR" \
        "PLEBIAN_OS_TRUSTED_TAG_OBJECT_SHA=$_PLEB_HOP_TAG_OBJECT" \
        bash "$selector" "$@"
}

_pleb_active_state_dir() {
    printf '%s\n' "$PLEB_STATE_HOME/release-hop"
}

_pleb_record_active_selector() {
    local phase="${1:-selecting}" state tmp
    state="$(_pleb_active_state_dir)"
    mkdir -p -- "$state"
    chmod 0700 -- "$state"
    tmp="$(mktemp "$state/.active.XXXXXX")" || die "could not stage release-hop state"
    {
        printf '%s\n' "$_PLEB_HOP_TARGET"
        printf '%s\n' "$_PLEB_HOP_COMMIT"
        printf '%s\n' "$_PLEB_HOP_TAG_OBJECT"
        printf '%s\n' "$_PLEB_HOP_SELECTOR_SHA"
    } >"$tmp"
    chmod 0600 "$tmp"
    mv -fT -- "$tmp" "$state/active"
    _pleb_write_private_line "$state/phase" "$phase"
}

_pleb_set_active_phase() {
    local phase="$1" state
    case "$phase" in selecting|selected|rolling-back|complete) ;; *) die "invalid release-hop phase: $phase" ;; esac
    state="$(_pleb_active_state_dir)"
    [ -f "$state/active" ] || die "cannot update release-hop phase without active state"
    _pleb_write_private_line "$state/phase" "$phase"
    _PLEB_HOP_PHASE="$phase"
}

_pleb_load_active_selector() {
    local state line_count actual
    state="$(_pleb_active_state_dir)/active"
    [ -f "$state" ] && [ ! -L "$state" ] \
        || die "no selected release closure is available to roll back"
    line_count="$(wc -l <"$state")"
    [ "$line_count" = 4 ] || die "release-hop active state is malformed"
    {
        IFS= read -r _PLEB_HOP_TARGET
        IFS= read -r _PLEB_HOP_COMMIT
        IFS= read -r _PLEB_HOP_TAG_OBJECT
        IFS= read -r _PLEB_HOP_SELECTOR_SHA
    } <"$state"
    [[ "$_PLEB_HOP_TARGET" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        && [[ "$_PLEB_HOP_COMMIT" =~ ^[0-9a-f]{40}$ ]] \
        && [[ "$_PLEB_HOP_TAG_OBJECT" =~ ^[0-9a-f]{40}$ ]] \
        && [[ "$_PLEB_HOP_SELECTOR_SHA" =~ ^[0-9a-f]{64}$ ]] \
        || die "release-hop active state contains an invalid identity"
    _pleb_release_cache_prepare
    _PLEB_HOP_SELECTOR="$_PLEB_HOP_CACHE/selectors/$_PLEB_HOP_COMMIT"
    [ -f "$_PLEB_HOP_SELECTOR" ] && [ ! -L "$_PLEB_HOP_SELECTOR" ] \
        || die "the cached selector for release $_PLEB_HOP_TARGET is missing"
    actual="$(sha256sum "$_PLEB_HOP_SELECTOR" | awk '{print $1}')"
    [ "$actual" = "$_PLEB_HOP_SELECTOR_SHA" ] \
        || die "the cached selector for release $_PLEB_HOP_TARGET changed"
    [ "$(git -C "$_PLEB_HOP_REPO" rev-parse --verify \
        "refs/tags/v$_PLEB_HOP_TARGET" 2>/dev/null || true)" = "$_PLEB_HOP_TAG_OBJECT" ] \
        || die "the cached tag object for release $_PLEB_HOP_TARGET changed"
    _PLEB_HOP_PHASE="$(cat "$(_pleb_active_state_dir)/phase" 2>/dev/null || true)"
    case "$_PLEB_HOP_PHASE" in selecting|selected|rolling-back|complete) ;;
        *) die "release-hop active phase is missing or invalid" ;;
    esac
}

_pleb_clear_active_selector() {
    rm -f -- "$(_pleb_active_state_dir)/active" "$(_pleb_active_state_dir)/phase"
    _PLEB_HOP_PHASE=""
}

pleb_recover_incomplete_release_hop() {
    local state rc=0
    state="$(_pleb_active_state_dir)"
    [ -f "$state/active" ] || return 0
    _PLEB_HOP_RECOVERY_RESULT=""
    _pleb_load_active_selector
    [ "$_PLEB_HOP_PHASE" != complete ] || return 0
    warn "recovering interrupted release-hop phase $_PLEB_HOP_PHASE for $_PLEB_HOP_TARGET"
    if [ "$_PLEB_HOP_PHASE" = rolling-back ]; then
        _pleb_selector_env "$_PLEB_HOP_SELECTOR" "$_PLEB_HOP_TARGET" \
            --source "$_PLEB_HOP_REPO" \
            || die "interrupted rollback is blocked: selected closure restoration failed; owner: F109 recovery operator"
        _pleb_set_active_phase complete
        _PLEB_HOP_RECOVERY_RESULT=cancelled
        log "interrupted rollback was cancelled safely; release $_PLEB_HOP_TARGET remains selected"
        return 0
    fi
    _pleb_selector_env "$_PLEB_HOP_SELECTOR" --rollback || rc=$?
    if [ "$rc" -ne 0 ]; then
        if [ "$_PLEB_HOP_PHASE" = selecting ] \
                && [ "$(_pleb_release_version)" != "$_PLEB_HOP_TARGET" ]; then
            warn "interrupted selector left no restorable selected closure; clearing its pre-selection marker"
            _pleb_clear_active_selector
            _PLEB_HOP_RECOVERY_RESULT=unchanged
            return 0
        fi
        die "interrupted release hop is blocked: closure rollback failed with status $rc; owner: F109 recovery operator"
    fi
    _pleb_restore_selected_stack 1 \
        || die "interrupted release hop is blocked: previous-stack reconciliation failed; owner: F109 recovery operator"
    _pleb_clear_active_selector
    _PLEB_HOP_RECOVERY_RESULT=rolled-back
    log "interrupted release hop restored the previous closure and stack"
}

pleb_release_hop_exit_cleanup() {
    local rc="$1" rollback_rc=0 stack_rc=0
    trap - EXIT INT TERM
    set +e
    if [ "${_PLEB_HOP_EMERGENCY_ROLLBACK:-0}" = 1 ]; then
        warn "release hop exited before commit; restoring the previous closure and stack"
        _PLEB_HOP_EMERGENCY_ROLLBACK=0
        _pleb_selector_env "$_PLEB_HOP_SELECTOR" --rollback || rollback_rc=$?
        if [ "$rollback_rc" = 0 ]; then
            _pleb_restore_selected_stack 1 || stack_rc=$?
        fi
        if [ "$rollback_rc" = 0 ] && [ "$stack_rc" = 0 ]; then
            _pleb_clear_active_selector
        else
            err "emergency release-hop recovery is incomplete: closure status $rollback_rc; stack status $stack_rc; owner: F109 recovery operator"
            rc=1
        fi
    elif [ "${_PLEB_HOP_EMERGENCY_ROLLBACK:-0}" = 2 ]; then
        warn "rollback exited before commit; restoring release $_PLEB_HOP_TARGET"
        _PLEB_HOP_EMERGENCY_ROLLBACK=0
        _pleb_selector_env "$_PLEB_HOP_SELECTOR" "$_PLEB_HOP_TARGET" \
            --source "$_PLEB_HOP_REPO" || rollback_rc=$?
        if [ "$rollback_rc" = 0 ]; then
            _pleb_set_active_phase complete
        else
            err "emergency rollback cancellation failed with status $rollback_rc; owner: F109 recovery operator"
            rc=1
        fi
    fi
    _release_update_lock
    exit "$rc"
}

_pleb_apply_selected_stack() {
    local yes="$1" restart="$2"
    if [ "${PLEBIAN_OS_MANAGED_INSTALL:-0}" = 1 ]; then
        local updater="${PLEBIAN_OS_UPDATER_DST:-/usr/local/bin/plebian-os-update}"
        [ -x "$updater" ] || die "target selector did not install a runnable updater at $updater"
        if [ "$restart" = yes ]; then
            "$updater" --restart
        else
            "$updater"
        fi
    else
        local -a args=()
        [ "$yes" = 1 ] && args+=(--yes)
        case "$restart" in
            yes) args+=(--restart) ;;
            no) args+=(--no-restart) ;;
        esac
        PLEB_RELEASE_HOP_APPLY=1 "$PLEB_ROOT/bin/pleb" update "${args[@]}"
    fi
}

_pleb_restore_selected_stack() {
    local yes="$1"
    if [ "${PLEBIAN_OS_MANAGED_INSTALL:-0}" = 1 ]; then
        local updater="${PLEBIAN_OS_UPDATER_DST:-/usr/local/bin/plebian-os-update}"
        [ -x "$updater" ] && "$updater"
    else
        local -a args=(--no-restart)
        [ "$yes" = 1 ] && args+=(--yes)
        PLEB_RELEASE_HOP_APPLY=1 "$PLEB_ROOT/bin/pleb" update "${args[@]}"
    fi
}

pleb_release_show() {
    local key value origin set_count=0
    log "release closure resolved by this Pleb process:"
    for key in $PLEB_RELEASE_CONTROLLED_KEYS; do
        if [[ ${!key+x} ]]; then
            value="${!key}"
            origin="$(_pleb_value_origin "$key")"
            printf '  %s=%s  [%s]\n' "$key" "$value" "$origin"
            set_count=$((set_count + 1))
        fi
    done
    log "$set_count release-controlled value(s) are set; system closure: $PLEB_CLOSURE_SYSTEM; user closure: $PLEB_CLOSURE_USER"
}

pleb_release_hop() {
    local mode="$1" requested="$2" dry_run="$3" offline="$4" yes="$5" restart="$6"
    local current inventory target latest selector_rc=0 apply_rc=0 recovered=0
    _pleb_refuse_release_env_overrides
    current="$(_pleb_release_version)"
    if [ -f "$(_pleb_active_state_dir)/active" ] \
            && [ "$(cat "$(_pleb_active_state_dir)/phase" 2>/dev/null || true)" != complete ]; then
        pleb_recover_incomplete_release_hop
        [ "$_PLEB_HOP_RECOVERY_RESULT" = cancelled ] || recovered=1
    fi
    if [ "$mode" = rollback ]; then
        if [ "$recovered" = 1 ]; then
            log "the interrupted release hop was rolled back"
            return 0
        fi
        _pleb_load_active_selector
        log "rolling release $_PLEB_HOP_TARGET back one selected closure"
        _pleb_set_active_phase rolling-back
        _PLEB_HOP_EMERGENCY_ROLLBACK=2
        trap 'pleb_release_hop_exit_cleanup $?' EXIT
        trap 'exit 130' INT
        trap 'exit 143' TERM
        _pleb_selector_env "$_PLEB_HOP_SELECTOR" --rollback || selector_rc=$?
        if [ "$selector_rc" != 0 ]; then
            _PLEB_HOP_EMERGENCY_ROLLBACK=0
            _pleb_set_active_phase complete
            die "closure rollback failed with status $selector_rc; the selected stack was not moved"
        fi
        if _pleb_restore_selected_stack "$yes"; then
            _PLEB_HOP_EMERGENCY_ROLLBACK=0
            _pleb_clear_active_selector
            log "rollback completed; the previous closure and stack are active"
            return 0
        fi
        apply_rc=$?
        warn "stack rollback failed with status $apply_rc; restoring the selected closure"
        if _pleb_selector_env "$_PLEB_HOP_SELECTOR" "$_PLEB_HOP_TARGET" \
                --source "$_PLEB_HOP_REPO"; then
            _PLEB_HOP_EMERGENCY_ROLLBACK=0
            _pleb_set_active_phase complete
            die "stack rollback failed with status $apply_rc; release $_PLEB_HOP_TARGET remains selected"
        fi
        die "stack rollback failed with status $apply_rc and the selected closure could not be restored; recovery records remain under $(_pleb_active_state_dir)"
    fi

    if [ "$offline" = 1 ]; then
        _pleb_release_cache_prepare
        inventory="$(git -C "$_PLEB_HOP_REPO" for-each-ref \
            --format='%(refname:short)%09%(objectname)' 'refs/tags/v*' \
            | while IFS=$'\t' read -r ref object; do
                version="${ref#v}"
                [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
                printf '%s\t%s\n' "$version" "$object"
            done | sort -t $'\t' -k1,1V -u)"
        [ -n "$inventory" ] \
            || die "the private release cache contains no semantic tags; --offline cannot select a target"
    else
        inventory="$(_pleb_remote_release_inventory)" \
            || die "could not list published Plebian-OS releases"
        [ -n "$inventory" ] || die "the Plebian-OS remote advertises no semantic release tags"
    fi
    case "$mode" in
        to) target="$requested" ;;
        latest)
            latest="$(tail -n 1 <<<"$inventory" | cut -f1)"
            [ -n "$latest" ] || die "could not determine the latest published release"
            if [ "$latest" = "$current" ]; then
                log "release $current is already the latest published release"
                return 0
            fi
            target="$latest"
            ;;
        *) die "internal release-hop mode is invalid: $mode" ;;
    esac
    _pleb_prepare_target_selector "$target" "$offline"
    _pleb_enforce_adjacent_hop "$current" "$target" "$inventory"
    local -a selector_args=("$target" --source "$_PLEB_HOP_REPO")
    [ "$offline" = 1 ] && selector_args+=(--offline)
    [ "$dry_run" = 1 ] && selector_args+=(--dry-run)
    if [ "$dry_run" = 1 ]; then
        _pleb_selector_env "$_PLEB_HOP_SELECTOR" "${selector_args[@]}"
        return 0
    fi
    _pleb_record_active_selector selecting
    _pleb_selector_env "$_PLEB_HOP_SELECTOR" "${selector_args[@]}" \
        || selector_rc=$?
    if [ "$selector_rc" -ne 0 ]; then
        _pleb_clear_active_selector
        die "target closure selection failed with status $selector_rc; the installed closure and stack were not moved"
    fi
    _pleb_set_active_phase selected
    _PLEB_HOP_EMERGENCY_ROLLBACK=1
    trap 'pleb_release_hop_exit_cleanup $?' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    if _pleb_apply_selected_stack "$yes" "$restart"; then
        _pleb_set_active_phase complete
        _PLEB_HOP_EMERGENCY_ROLLBACK=0
        log "release hop $current -> $target completed"
        return 0
    fi
    apply_rc=$?
    _PLEB_HOP_EMERGENCY_ROLLBACK=0
    warn "target stack apply failed with status $apply_rc; restoring the previous closure"
    if ! _pleb_selector_env "$_PLEB_HOP_SELECTOR" --rollback; then
        die "target stack apply failed with status $apply_rc and closure rollback failed; recovery records remain under $(_pleb_active_state_dir)"
    fi
    if _pleb_restore_selected_stack "$yes"; then
        _pleb_clear_active_selector
        die "target stack apply failed with status $apply_rc; the previous closure and stack were restored"
    fi
    die "target stack apply failed with status $apply_rc; closure rollback succeeded but previous-stack reconciliation failed"
}

pleb_report_newer_release() {
    local current inventory latest
    current="$(_pleb_release_version)"
    if [ "${PLEB_RELEASE_OFFLINE:-0}" = 1 ]; then
        warn "could not check for a newer published release: PLEB_RELEASE_OFFLINE=1"
        return 0
    fi
    inventory="$(_pleb_remote_release_inventory)" || {
        warn "could not check for a newer published release; the installed closure was revalidated only"
        return 0
    }
    latest="$(tail -n 1 <<<"$inventory" | cut -f1)"
    if [ "$current" = unknown ]; then
        warn "could not compare published release $latest with an unknown installed release"
    elif [ -n "$latest" ] && [ "$latest" != "$current" ] \
            && [ "$(printf '%s\n%s\n' "$current" "$latest" | sort -V | head -n 1)" = "$current" ]; then
        warn "newer published release $latest is available; this bare update did not select it"
        warn "move by an accepted hop with: pleb update --latest"
    else
        log "installed release $current is the newest published release"
    fi
}
