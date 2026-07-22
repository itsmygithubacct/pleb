#!/usr/bin/env bash
# lib/install.sh — install/uninstall the Pleb LightDM session. Sourced by `pleb`.

_install_missing_apt_packages() {
    local label="$1"
    shift

    if [ "${PLEB_SKIP_DEPS:-0}" = 1 ]; then
        warn "skipping dependency install because PLEB_SKIP_DEPS=1"
        return 0
    fi
    if ! command -v apt-get >/dev/null 2>&1; then
        warn "automatic dependency install only supports apt-get; continuing"
        return 0
    fi

    local -a deps missing
    deps=("$@")
    missing=()
    if command -v dpkg-query >/dev/null 2>&1; then
        local pkg status
        for pkg in "${deps[@]}"; do
            status="$(dpkg-query -W -f='${Status}\n' "$pkg" 2>/dev/null || true)"
            if ! printf '%s\n' "$status" | grep -qx 'install ok installed'; then
                missing+=("$pkg")
            fi
        done
        if [ "${#missing[@]}" -eq 0 ]; then
            log "$label already installed"
            return 0
        fi
    else
        missing=("${deps[@]}")
    fi

    log "installing missing $label via apt-get: ${missing[*]}"
    run_root env DEBIAN_FRONTEND=noninteractive apt-get update
    run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
}

# ensure_system_deps — install the OS packages the Pleb session itself needs.
# Plebian-OS calls its own more complete dependency manifest before `pleb
# install`; this keeps standalone `pleb install` usable on fresh Debian/Ubuntu
# desktops too. Kilix owns the separate fork-build dependency manifest in
# scripts/install-build-deps.sh. Set PLEB_SKIP_DEPS=1 to skip package-manager
# changes.
ensure_system_deps() {
    local -a deps
    deps=(
        git curl tar sudo network-manager
        lightdm xinit x11-xserver-utils x11-utils xterm
        libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libxcb-xkb1
        fontconfig fonts-dejavu-core
        python3-pil python3-xlib python3-websockets
        pulseaudio pulseaudio-utils pulsemixer alsa-utils ffmpeg xauth zenity
        dbus-user-session dbus-x11 xdg-desktop-portal xdg-desktop-portal-gtk
        fluidsynth fluid-soundfont-gm
    )
    _install_missing_apt_packages "Pleb runtime dependencies" "${deps[@]}"
}

ensure_kilix_build_deps() {
    local installer="$KILIX_DIR/scripts/install-build-deps.sh"
    [ -f "$installer" ] && [ ! -L "$installer" ] && [ -x "$installer" ] \
        || die "Kilix build dependency installer is missing or unsafe: $installer"

    log "verifying Kilix-owned fork-build prerequisites"
    if "$installer" --verify; then
        log "Kilix fork-build prerequisites are ready"
        return 0
    fi
    if [ "${PLEB_SKIP_DEPS:-0}" = 1 ]; then
        die "Kilix build prerequisites are incomplete and PLEB_SKIP_DEPS=1; install the items reported above (including libxxhash when missing), then run '$installer --verify'"
    fi

    log "Kilix prerequisites are incomplete; running its authoritative installer"
    "$installer" \
        || die "Kilix build dependency installation failed: $installer"
    "$installer" --verify \
        || die "Kilix build prerequisites remain incomplete after installation (check the reported pkg-config modules, including libxxhash)"
    log "Kilix fork-build prerequisites are ready"
}

# ensure_kilix — make sure a kilix checkout with a runnable engine exists,
# cloning it fresh from upstream if it isn't there yet. A plain (non-recursive)
# clone + prebuilt kitty is enough to run; the clickable-button fork is built
# later on demand (`kilix --build` / `pleb update`, which init the submodule).
ensure_kilix() {
    require_immutable_ref "$KILIX_REF" "$KILIX_ALLOW_MUTABLE_REF" \
        KILIX_REF KILIX_ALLOW_MUTABLE_REF
    if [ -d "$KILIX_DIR/.git" ] && [ -x "$KILIX_DIR/kilix" ]; then
        validate_checkout_origin "$KILIX_DIR" "$KILIX_REPO" "kilix"
        log "kilix present at $KILIX_DIR (use 'pleb update' to update it)"
    else
        [ -e "$KILIX_DIR" ] && [ ! -d "$KILIX_DIR/.git" ] \
            && die "$KILIX_DIR exists but isn't a kilix checkout — move it aside first"
        command -v git >/dev/null 2>&1 || die "git is required to clone kilix"
        log "cloning kilix -> $KILIX_DIR"
        local -a clone_args=()
        [ -n "$KILIX_BRANCH" ] && clone_args=(--branch "$KILIX_BRANCH")
        git clone "${clone_args[@]}" "$KILIX_REPO" "$KILIX_DIR" \
            || die "git clone failed ($KILIX_REPO)"
    fi
    if [ -n "$KILIX_REF" ]; then
        require_clean_checkout "$KILIX_DIR" "kilix"
        checkout_fetched_ref "$KILIX_DIR" "$KILIX_REF" "kilix"
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
    require_immutable_ref "$KILIX95_REF" "$KILIX95_ALLOW_MUTABLE_REF" \
        KILIX95_REF KILIX95_ALLOW_MUTABLE_REF

    if [ -d "$KILIX95_DIR/.git" ] && [ -f "$KILIX95_DIR/main.py" ]; then
        validate_checkout_origin "$KILIX95_DIR" "$KILIX95_REPO" "kilix 95"
        log "kilix 95 present at $KILIX95_DIR (use 'pleb update' to update it)"
    else
        if [ -z "$KILIX95_REF" ] && [ "$KILIX95_ALLOW_UNPINNED_INSTALL" != 1 ]; then
            die "automatic Kilix 95 install requires an immutable KILIX95_REF commit SHA (set KILIX95_ALLOW_UNPINNED_INSTALL=1 only to allow an unpinned clone)"
        fi
        [ -e "$KILIX95_DIR" ] && [ ! -d "$KILIX95_DIR/.git" ] \
            && die "$KILIX95_DIR exists but isn't a kilix 95 checkout — move it aside first"
        command -v git >/dev/null 2>&1 || die "git is required to clone kilix 95"
        log "cloning kilix 95 -> $KILIX95_DIR"
        local -a clone_args=()
        [ -n "$KILIX95_BRANCH" ] && clone_args=(--branch "$KILIX95_BRANCH")
        git clone "${clone_args[@]}" "$KILIX95_REPO" "$KILIX95_DIR" \
            || die "git clone failed ($KILIX95_REPO)"
    fi

    if [ -n "$KILIX95_REF" ]; then
        require_clean_checkout "$KILIX95_DIR" "kilix 95"
        checkout_fetched_ref "$KILIX95_DIR" "$KILIX95_REF" "kilix 95"
    fi
}

# ensure_engine — make sure kilix has a runnable kitty; if not, fetch the
# prebuilt one (needs only git/curl/tar). The fork (buttons) needs Go >= 1.26.
ensure_engine() {
    local answer engine
    local -a bootstrap_args=()
    if { [ -n "$KILIX_PREBUILT_VERSION" ] && [ -z "$KILIX_PREBUILT_SHA256" ]; } \
        || { [ -z "$KILIX_PREBUILT_VERSION" ] && [ -n "$KILIX_PREBUILT_SHA256" ]; }; then
        die "KILIX_PREBUILT_VERSION and KILIX_PREBUILT_SHA256 must be set together"
    fi
    # A configured version/checksum pair is an installation invariant, not just
    # a download hint.  Let bootstrap.sh revalidate the fallback even when some
    # engine (including a previously built fork) is already runnable.
    if [ -z "$KILIX_PREBUILT_VERSION" ] \
        && "$KILIX_DIR/kilix" --which >/dev/null 2>&1; then
        log "engine: $("$KILIX_DIR/kilix" --which 2>/dev/null | tail -1)"
        return 0
    fi
    [ -x "$KILIX_DIR/bootstrap.sh" ] || die "no engine and no bootstrap.sh in $KILIX_DIR"
    if [ -z "$KILIX_PREBUILT_VERSION" ]; then
        [ -t 0 ] || die "non-interactive engine install requires pinned KILIX_PREBUILT_VERSION and KILIX_PREBUILT_SHA256"
        warn "no kitty bundle checksum is pinned; Plebian-OS release installs always pin one"
        # First run without the override. bootstrap.sh refuses before download
        # and prints the exact release URL so consent can be informed.
        env KILIX_PREBUILT_VERSION= KILIX_PREBUILT_SHA256= \
            KILIX_ALLOW_UNVERIFIED_PREBUILT=0 \
            "$KILIX_DIR/bootstrap.sh" || true
        ask "Allow bootstrap.sh to download that unverified asset? [y/N]"
        read -r answer
        case "$answer" in
            y|Y|yes|YES) bootstrap_args=(--allow-unverified) ;;
            *) die "engine install cancelled; set the prebuilt version and checksum, then retry" ;;
        esac
    fi
    log "fetching the prebuilt kilix engine ..."
    env "KILIX_PREBUILT_VERSION=$KILIX_PREBUILT_VERSION" \
        "KILIX_PREBUILT_SHA256=$KILIX_PREBUILT_SHA256" \
        "$KILIX_DIR/bootstrap.sh" "${bootstrap_args[@]}" \
        || die "kilix engine bootstrap failed"
    engine="$("$KILIX_DIR/kilix" --which 2>/dev/null | head -1 || true)"
    [ -n "$engine" ] && "$KILIX_DIR/kilix" --which >/dev/null 2>&1 \
        || die "kilix engine bootstrap reported success, but no runnable engine is available"
    log "engine ready: $engine"
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

_pleb_artwork_sources() {
    PLEB_WALLPAPER_SRC="$PLEB_ROOT/assets/desktop/plebian-os.png"
    PLEB_DESKTOP_README_SRC="$PLEB_ROOT/assets/desktop/README.md"
    PLEB_ARTWORK_ATTRIBUTION_SRC="$PLEB_ROOT/assets/installer/ATTRIBUTION.md"
    PLEB_ARTWORK_GPL2_SRC="$PLEB_ROOT/assets/COPYING.GPL-2"
}

_pleb_artwork_destinations() {
    PLEB_WALLPAPER_DST="$PLEB_DATA_HOME/wallpapers/plebian-os.png"
    PLEB_DESKTOP_README_DST="$PLEB_DATA_HOME/doc/desktop/README.md"
    PLEB_ARTWORK_ATTRIBUTION_DST="$PLEB_DATA_HOME/doc/installer/ATTRIBUTION.md"
    PLEB_ARTWORK_GPL2_DST="$PLEB_DATA_HOME/doc/COPYING.GPL-2"
}

validate_pleb_artwork_bundle() {
    local validator="$PLEB_ROOT/scripts/validate-artwork.py"
    _pleb_artwork_sources
    [ -f "$validator" ] && [ ! -L "$validator" ] \
        || die "missing Pleb artwork validator: $validator"
    command -v python3 >/dev/null 2>&1 \
        || die "python3 is required to validate the Plebian wallpaper"
    python3 "$validator" \
        "$PLEB_WALLPAPER_SRC" \
        "$PLEB_DESKTOP_README_SRC" \
        "$PLEB_ARTWORK_ATTRIBUTION_SRC" \
        "$PLEB_ARTWORK_GPL2_SRC" >/dev/null \
        || die "Plebian wallpaper bundle validation failed"
}

_pleb_artwork_destination_safe() {
    local destination="$1"
    if [ -e "$destination" ] || [ -L "$destination" ]; then
        [ -f "$destination" ] && [ ! -L "$destination" ] \
            || die "refusing unsafe Pleb artwork destination: $destination"
        [ "$(stat -c '%u' "$destination" 2>/dev/null)" = "$(id -u)" ] \
            || die "Pleb artwork destination is not owned by the current user: $destination"
    fi
}

install_pleb_artwork_bundle() {
    local validator="$PLEB_ROOT/scripts/validate-artwork.py" index destination
    local failure="" published=0
    local -a sources destinations staged backups
    _pleb_artwork_sources
    ensure_pleb_private_storage
    _pleb_artwork_destinations
    validate_pleb_artwork_bundle
    umask 077
    for destination in \
        "$PLEB_STORAGE_HOME" \
        "$PLEB_DATA_HOME" \
        "$PLEB_DATA_HOME/wallpapers" \
        "$PLEB_DATA_HOME/doc" \
        "$PLEB_DATA_HOME/doc/desktop" \
        "$PLEB_DATA_HOME/doc/installer"; do
        _pleb_private_data_dir "$destination"
    done

    sources=(
        "$PLEB_WALLPAPER_SRC"
        "$PLEB_DESKTOP_README_SRC"
        "$PLEB_ARTWORK_ATTRIBUTION_SRC"
        "$PLEB_ARTWORK_GPL2_SRC"
    )
    destinations=(
        "$PLEB_WALLPAPER_DST"
        "$PLEB_DESKTOP_README_DST"
        "$PLEB_ARTWORK_ATTRIBUTION_DST"
        "$PLEB_ARTWORK_GPL2_DST"
    )
    staged=()
    backups=()
    for ((index=0; index<${#destinations[@]}; index++)); do
        destination="${destinations[$index]}"
        _pleb_artwork_destination_safe "$destination"
        staged[$index]="$(mktemp "$(dirname "$destination")/.${destination##*/}.XXXXXX")" \
            || die "could not stage Pleb artwork: $destination"
        if ! cp -- "${sources[$index]}" "${staged[$index]}" \
            || ! chmod 0600 -- "${staged[$index]}"; then
            rm -f -- "${staged[@]}"
            die "could not copy Pleb artwork into private staging"
        fi
        backups[$index]=""
        if [ -e "$destination" ]; then
            backups[$index]="$(mktemp "$(dirname "$destination")/.${destination##*/}.backup.XXXXXX")" \
                || die "could not stage Pleb artwork rollback copy: $destination"
            if ! cp -p -- "$destination" "${backups[$index]}"; then
                rm -f -- "${staged[@]}" "${backups[$index]}"
                die "could not preserve existing Pleb artwork before replacement: $destination"
            fi
        fi
    done

    if ! python3 "$validator" "${staged[@]}" >/dev/null; then
        rm -f -- "${staged[@]}"
        for destination in "${backups[@]}"; do
            [ -z "$destination" ] || rm -f -- "$destination"
        done
        die "staged Pleb artwork bundle validation failed"
    fi
    for ((index=0; index<${#destinations[@]}; index++)); do
        if ! chmod 0644 -- "${staged[$index]}" \
            || ! mv -fT -- "${staged[$index]}" "${destinations[$index]}"; then
            failure="could not publish Pleb artwork: ${destinations[$index]}"
            break
        fi
        published=$((published + 1))
    done
    if [ -z "$failure" ] \
        && ! python3 "$validator" "${destinations[@]}" >/dev/null; then
        failure="installed Pleb artwork bundle failed post-copy validation"
    fi
    if [ -n "$failure" ]; then
        # Each rename is atomic. If a later rename or the final validation
        # fails, restore every earlier destination before returning failure so
        # a normal I/O error cannot leave a mixed old/new bundle.
        for ((index=published-1; index>=0; index--)); do
            if [ -n "${backups[$index]}" ]; then
                command mv -fT -- "${backups[$index]}" "${destinations[$index]}" \
                    || warn "could not restore ${destinations[$index]} after artwork failure"
                backups[$index]=""
            else
                rm -f -- "${destinations[$index]}" \
                    || warn "could not remove ${destinations[$index]} after artwork failure"
            fi
        done
        rm -f -- "${staged[@]:$published}"
        for destination in "${backups[@]}"; do
            [ -z "$destination" ] || rm -f -- "$destination"
        done
        die "$failure"
    fi
    for destination in "${backups[@]}"; do
        [ -z "$destination" ] || rm -f -- "$destination"
    done
    log "installed Plebian wallpaper -> $PLEB_WALLPAPER_DST"
    info "artwork attribution: $PLEB_ARTWORK_ATTRIBUTION_DST"
}

_pleb_desktop_state_dir() {
    local state_dir data_root
    case "${KILIX_DESKTOP_PROVIDER:-auto}" in
        builtin|external) ;;
        auto)
            if [ -f "$KILIX95_DIR/main.py" ]; then
                :
            elif [ -f "$KILIX_DIR/desktop/main.py" ]; then
                :
            else
                return 1
            fi ;;
        none|off|disabled|command|custom)
            return 1 ;;
        *)
            die "unknown KILIX_DESKTOP_PROVIDER=${KILIX_DESKTOP_PROVIDER:-}" ;;
    esac
    state_dir="$(_pleb_normalized_absolute_path "$KILIX_DESKTOP_DIR")"
    data_root="$(_pleb_normalized_absolute_path "$PLEB_DATA_HOME")"
    [ "${KILIX_DESKTOP_DIR%/}" = "$state_dir" ] \
        || die "KILIX_DESKTOP_DIR must be a normalized absolute path: $KILIX_DESKTOP_DIR"
    case "$state_dir" in
        "$data_root"/*) ;;
        *) die "standalone Pleb requires KILIX_DESKTOP_DIR below PLEB_DATA_HOME ($data_root): $state_dir" ;;
    esac
    _pleb_assert_no_symlink_components "$state_dir"
    printf '%s\n' "$state_dir"
}

seed_pleb_wallpaper_state() {
    local validator="$PLEB_ROOT/scripts/validate-artwork.py"
    local state_dir state_path owner rc
    _pleb_artwork_destinations
    state_dir="$(_pleb_desktop_state_dir)" || {
        log "no compatible Kilix desktop provider selected; wallpaper state not seeded"
        return 0
    }
    state_path="$state_dir/.state.json"
    if [ -e "$state_path" ] || [ -L "$state_path" ]; then
        log "preserving existing Kilix desktop state (including wallpaper): $state_path"
        return 0
    fi
    python3 "$validator" \
        "$PLEB_WALLPAPER_DST" \
        "$PLEB_DESKTOP_README_DST" \
        "$PLEB_ARTWORK_ATTRIBUTION_DST" \
        "$PLEB_ARTWORK_GPL2_DST" >/dev/null \
        || die "installed Pleb artwork is invalid; refusing to seed desktop state"
    _pleb_private_data_dir "$state_dir"
    owner="$(stat -c '%u' "$state_dir" 2>/dev/null)" \
        || die "could not inspect Kilix desktop state directory: $state_dir"
    [ "$owner" = "$(id -u)" ] \
        || die "Kilix desktop state directory is not owned by the current user: $state_dir"

    if python3 - "$state_dir" "$state_path" "$PLEB_WALLPAPER_DST" <<'PY'
import json
import os
import sys
import tempfile

state_dir, state_path, wallpaper = sys.argv[1:]
fd, temporary = tempfile.mkstemp(prefix=".state.json.pleb.", dir=state_dir)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({
            "wall_image": wallpaper,
            "wall_mode": "stretch",
            "wall_custom": True,
        }, stream, indent=1)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, state_path, follow_symlinks=False)
    except FileExistsError:
        raise SystemExit(17)
    directory_fd = os.open(state_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
    then
        log "new Kilix desktop will use $PLEB_WALLPAPER_DST"
    else
        rc=$?
        [ "$rc" = 17 ] \
            || die "could not seed Plebian wallpaper state: $state_path"
        log "Kilix desktop state appeared concurrently; preserving it"
    fi
}

install_standalone_pleb_wallpaper() {
    # Privacy is part of Pleb's storage contract, not part of artwork ownership.
    # Plebian-OS manages the files below data/, but must not bypass mode repair.
    ensure_pleb_private_storage
    if [ "${PLEBIAN_OS_MANAGED_INSTALL:-0}" = 1 ]; then
        log "Plebian-OS manages the system wallpaper; skipping standalone Pleb artwork"
        return 0
    fi
    install_pleb_artwork_bundle
    # Seed the selected compatible provider even when PLEB_DESKTOP is currently
    # off, so enabling the desktop later does not require reinstalling Pleb.
    # Providers `none` and `command` have no Kilix state contract and are
    # deliberately skipped by _pleb_desktop_state_dir.
    seed_pleb_wallpaper_state
}

validate_recovery_document() {
    if [ ! -f "$PLEB_RECOVERY_DOC_SRC" ] || [ -L "$PLEB_RECOVERY_DOC_SRC" ]; then
        die "missing or unsafe Pleb recovery document: $PLEB_RECOVERY_DOC_SRC"
    fi
}

install_recovery_document() {
    validate_recovery_document
    case "$PLEB_RECOVERY_DOC_DST" in
        /*) ;;
        *) die "PLEB_RECOVERY_DOC_DST must be absolute: $PLEB_RECOVERY_DOC_DST" ;;
    esac
    log "installing Pleb recovery guide -> $PLEB_RECOVERY_DOC_DST"
    run_root install -D -m 0644 -- \
        "$PLEB_RECOVERY_DOC_SRC" "$PLEB_RECOVERY_DOC_DST"
}

# do_install — ensure kilix is present, copy pleb-session to /usr/local/bin, and
# drop the xsession entry so LightDM lists "Pleb" as a choosable session.
do_install() {
    [ -f "$PLEB_BIN_SRC" ]    || die "missing $PLEB_BIN_SRC"
    [ -f "$PLEB_DESKTOP_IN" ] || die "missing $PLEB_DESKTOP_IN"
    validate_recovery_document

    # This is required for both standalone and Plebian-OS-managed installs.
    # Managed installs skip artwork publication, never storage privacy.
    ensure_pleb_private_storage

    ensure_system_deps
    ensure_kilix   # fresh-clone kilix + set up an engine if not already present
    if [ ! -f "$KILIX_DIR/kilix-settings" ] \
            || [ -L "$KILIX_DIR/kilix-settings" ]; then
        die "missing or unsafe Kilix settings TUI: $KILIX_DIR/kilix-settings"
    fi
    python3 "$KILIX_DIR/kilix-settings" --ensure \
        || die "could not initialize $GPU_TERMINAL_SETTINGS_FILE"
    ensure_kilix95 # optional: external Kilix 95 when the selected provider needs it
    install_standalone_pleb_wallpaper
    install_recovery_document

    log "installing session launcher -> $SESSION_BIN_DST"
    run_root install -D -m 0755 "$PLEB_BIN_SRC" "$SESSION_BIN_DST"

    log "installing xsession entry -> $XSESSION_DST"
    sed "s#@SESSION_BIN@#$SESSION_BIN_DST#g" "$PLEB_DESKTOP_IN" | write_root "$XSESSION_DST"

    # put `kilix` on PATH so `kilix desktop` / `kilix serve` etc. work anywhere
    log "linking kilix command -> $KILIX_LINK"
    link_command "$KILIX_DIR/kilix" "$KILIX_LINK" "kilix"

    log "linking kilix-settings command -> $KILIX_SETTINGS_LINK"
    link_command "$KILIX_DIR/kilix-settings" "$KILIX_SETTINGS_LINK" "kilix-settings"

    # and `pleb` itself, so `pleb update` / `pleb status` etc. work anywhere
    # (bin/pleb resolves its checkout through the symlink via readlink -f)
    log "linking pleb command -> $PLEB_LINK"
    link_command "$PLEB_ROOT/bin/pleb" "$PLEB_LINK" "pleb"

    log "done. Log out, then at the LightDM greeter pick the session"
    info "menu (gear/badge near the login box) -> \"Pleb\" -> log in."
    warn "verify the engine first:  pleb doctor"
}

# do_uninstall — remove the system session files and command links. Checkouts,
# caches/state, and packages installed as dependencies are intentionally kept.
do_uninstall() {
    local removed=0
    for f in "$XSESSION_DST" "$SESSION_BIN_DST" "$PLEB_RECOVERY_DOC_DST"; do
        if [ -e "$f" ] || [ -L "$f" ]; then
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
    if [ -L "$KILIX_SETTINGS_LINK" ] \
            && [ "$(readlink "$KILIX_SETTINGS_LINK")" = "$KILIX_DIR/kilix-settings" ]; then
        log "removing kilix-settings command symlink $KILIX_SETTINGS_LINK"
        run_root rm -f "$KILIX_SETTINGS_LINK"
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
