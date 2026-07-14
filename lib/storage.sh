#!/usr/bin/env bash
# lib/storage.sh — validate and reconcile Pleb's private writable-data layout.
# Sourced by common.sh and by scripts/install-go.sh; callers provide die().

_pleb_normalized_absolute_path() {
    local path="$1"
    case "$path" in
        /*) ;;
        *) die "Pleb writable-data path must be absolute: $path" ;;
    esac
    [[ "$path" != *$'\n'* && "$path" != *$'\r'* ]] \
        || die "Pleb writable-data path contains a line break"
    realpath -m -- "$path" 2>/dev/null \
        || die "could not normalize Pleb writable-data path: $path"
}

_pleb_assert_no_symlink_components() {
    local path="$1" current="" component
    local -a components
    IFS=/ read -r -a components <<<"${path#/}"
    for component in "${components[@]}"; do
        [ -n "$component" ] || continue
        current="$current/$component"
        [ ! -L "$current" ] \
            || die "refusing Pleb writable-data path with a symlink component: $current"
        if [ -e "$current" ]; then
            [ -d "$current" ] \
                || die "Pleb writable-data parent is not a directory: $current"
        fi
    done
}

_pleb_assert_safe_storage_parent_chain() {
    local path="$1" parent current="" component mode owner user_id
    local -a components
    user_id="$(id -u)"
    parent="$(dirname "$path")"
    IFS=/ read -r -a components <<<"${parent#/}"
    for component in "${components[@]}"; do
        [ -n "$component" ] || continue
        current="$current/$component"
        [ ! -L "$current" ] \
            || die "refusing Pleb writable-data path with a symlink component: $current"
        if [ -e "$current" ]; then
            [ -d "$current" ] \
                || die "Pleb writable-data parent is not a directory: $current"
            owner="$(stat -c '%u' "$current" 2>/dev/null)" \
                || die "could not inspect Pleb writable-data parent owner: $current"
            case "$owner" in
                0|"$user_id") ;;
                *) die "Pleb writable-data parent has an unsafe owner: $current" ;;
            esac
            mode="$(stat -c '%a' "$current" 2>/dev/null)" \
                || die "could not inspect Pleb writable-data parent: $current"
            if [ "$current" != "$PLEB_STORAGE_HOME" ] \
                && (( (8#$mode & 8#22) != 0 )); then
                case "$current" in
                    "$PLEB_STORAGE_HOME"/*)
                        die "refusing Pleb writable data below a group/world-writable parent: $current" ;;
                    *)
                        (( (8#$mode & 8#1000) != 0 )) \
                            || die "refusing Pleb writable data below a group/world-writable parent: $current" ;;
                esac
            fi
        fi
    done
}

_pleb_storage_path_input() {
    local path="${1%/}"
    [ -n "$path" ] || path=/
    printf '%s\n' "$path"
}

_pleb_validate_private_storage_layout() {
    local data_root storage source input normalized var path current component owner
    local -a categories components

    [ "$(id -u)" != 0 ] \
        || die "Pleb private storage must be managed as the desktop user; run Pleb without sudo"

    data_root="$(_pleb_normalized_absolute_path "$GPU_TERMINAL_HOME")"
    storage="$(_pleb_normalized_absolute_path "$PLEB_STORAGE_HOME")"
    source="$(_pleb_normalized_absolute_path "$GPU_TERMINAL_SOURCE_HOME")"
    _pleb_assert_no_symlink_components "$GPU_TERMINAL_HOME"
    _pleb_assert_no_symlink_components "$PLEB_STORAGE_HOME"
    input="$(_pleb_storage_path_input "$GPU_TERMINAL_HOME")"
    [ "$input" = "$data_root" ] \
        || die "GPU_TERMINAL_HOME must be a normalized absolute path: $GPU_TERMINAL_HOME"
    input="$(_pleb_storage_path_input "$PLEB_STORAGE_HOME")"
    [ "$input" = "$storage" ] \
        || die "PLEB_STORAGE_HOME must be a normalized absolute path: $PLEB_STORAGE_HOME"

    case "$data_root" in
        /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/media|/mnt|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var)
            die "GPU_TERMINAL_HOME is too broad for writable application data: $data_root" ;;
    esac
    case "$data_root" in
        "$source"|"$source"/*)
            die "GPU_TERMINAL_HOME must not place writable data in the source tree: $source" ;;
    esac
    case "$storage" in
        "$data_root"/*) ;;
        *) die "PLEB_STORAGE_HOME must be a strict descendant of GPU_TERMINAL_HOME ($data_root): $storage" ;;
    esac
    case "$storage" in
        /|"${HOME%/}") die "PLEB_STORAGE_HOME is too broad for Pleb writable data: $storage" ;;
    esac

    categories=(
        PLEB_CONFIG_HOME PLEB_STATE_HOME PLEB_CACHE_HOME
        PLEB_SESSION_HOME PLEB_DATA_HOME
    )
    for var in "${categories[@]}"; do
        path="${!var}"
        normalized="$(_pleb_normalized_absolute_path "$path")"
        _pleb_assert_no_symlink_components "$path"
        input="$(_pleb_storage_path_input "$path")"
        [ "$input" = "$normalized" ] \
            || die "$var must be a normalized absolute path: $path"
        case "$normalized" in
            "$storage"/*) ;;
            *) die "$var must be a strict descendant of PLEB_STORAGE_HOME ($storage): $normalized" ;;
        esac
        printf -v "$var" '%s' "$normalized"
    done

    # Validate the complete layout before changing any modes. Thus a broad or
    # linked category cannot make us chmod even an otherwise valid root.
    _pleb_assert_safe_storage_parent_chain "$storage"
    for path in "$PLEB_CONFIG_HOME" "$PLEB_STATE_HOME" "$PLEB_CACHE_HOME" \
        "$PLEB_SESSION_HOME" "$PLEB_DATA_HOME"; do
        _pleb_assert_safe_storage_parent_chain "$path"
    done
    for path in "$storage" \
        "$PLEB_CONFIG_HOME" "$PLEB_STATE_HOME" "$PLEB_CACHE_HOME" \
        "$PLEB_SESSION_HOME" "$PLEB_DATA_HOME"; do
        _pleb_assert_no_symlink_components "$path"
        if [ -e "$path" ] || [ -L "$path" ]; then
            [ -d "$path" ] && [ ! -L "$path" ] \
                || die "refusing unsafe Pleb writable-data directory: $path"
            owner="$(stat -c '%u' "$path" 2>/dev/null)" \
                || die "could not inspect Pleb writable-data directory: $path"
            [ "$owner" = "$(id -u)" ] \
                || die "Pleb writable-data directory is not owned by the current user: $path"
        fi
    done

    # Any pre-existing intermediate directories below the component root must
    # also belong to this user. We never chown or chmod someone else's path.
    for path in "$PLEB_CONFIG_HOME" "$PLEB_STATE_HOME" "$PLEB_CACHE_HOME" \
        "$PLEB_SESSION_HOME" "$PLEB_DATA_HOME"; do
        current="$storage"
        IFS=/ read -r -a components <<<"${path#"$storage"/}"
        for component in "${components[@]}"; do
            [ -n "$component" ] || continue
            current="$current/$component"
            [ -e "$current" ] || continue
            owner="$(stat -c '%u' "$current" 2>/dev/null)" \
                || die "could not inspect Pleb writable-data directory: $current"
            [ "$owner" = "$(id -u)" ] \
                || die "Pleb writable-data directory is not owned by the current user: $current"
        done
    done

    GPU_TERMINAL_HOME="$data_root"
    PLEB_STORAGE_HOME="$storage"
}

_pleb_private_data_dir() {
    local dir="$1" owner
    case "$dir" in
        "$PLEB_STORAGE_HOME"|"$PLEB_STORAGE_HOME"/*) ;;
        *) die "Pleb writable-data directory escapes PLEB_STORAGE_HOME: $dir" ;;
    esac
    _pleb_assert_safe_storage_parent_chain "$dir"
    _pleb_assert_no_symlink_components "$dir"
    ( umask 077; mkdir -p -- "$dir" ) \
        || die "could not create Pleb writable-data directory: $dir"
    _pleb_assert_safe_storage_parent_chain "$dir"
    _pleb_assert_no_symlink_components "$dir"
    [ -d "$dir" ] && [ ! -L "$dir" ] \
        || die "refusing unsafe Pleb writable-data directory: $dir"
    owner="$(stat -c '%u' "$dir" 2>/dev/null)" \
        || die "could not inspect Pleb writable-data directory: $dir"
    [ "$owner" = "$(id -u)" ] \
        || die "Pleb writable-data directory is not owned by the current user: $dir"
    chmod 0700 -- "$dir" \
        || die "could not make Pleb writable-data directory private: $dir"
}

ensure_pleb_private_storage() {
    _pleb_validate_private_storage_layout
    _pleb_private_data_dir "$PLEB_STORAGE_HOME"
    _pleb_private_data_dir "$PLEB_CONFIG_HOME"
    _pleb_private_data_dir "$PLEB_STATE_HOME"
    _pleb_private_data_dir "$PLEB_CACHE_HOME"
    _pleb_private_data_dir "$PLEB_SESSION_HOME"
    _pleb_private_data_dir "$PLEB_DATA_HOME"
}
