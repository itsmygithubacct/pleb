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

# ensure_system_deps — install the OS packages Pleb/Kilix commonly needs.
# Plebian-OS calls its own more complete dependency manifest before `pleb
# install`; this keeps standalone `pleb install` usable on fresh Debian/Ubuntu
# desktops too. Set PLEB_SKIP_DEPS=1 to skip package-manager changes.
ensure_system_deps() {
    local -a deps
    deps=(
        git curl tar sudo
        lightdm xinit x11-xserver-utils x11-utils xterm
        libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libxcb-xkb1
        fontconfig fonts-dejavu-core
        python3-pil python3-xlib python3-websockets
        pulseaudio pulseaudio-utils alsa-utils ffmpeg xauth zenity
        dbus-user-session dbus-x11 xdg-desktop-portal xdg-desktop-portal-gtk
        build-essential pkg-config python3-dev zlib1g-dev
        libx11-dev libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev
        libxkbcommon-dev libxkbcommon-x11-dev libx11-xcb-dev libxcb-xkb-dev libdbus-1-dev
        libgl1-mesa-dev libfontconfig-dev libsdl2-dev libsdl2-image-dev
        libsndfile1-dev libfluidsynth-dev fluidsynth fluid-soundfont-gm
    )
    _install_missing_apt_packages "Pleb/Kilix dependencies" "${deps[@]}"
}

ensure_kilix_build_deps() {
    local -a deps
    deps=(
        build-essential pkg-config python3-dev zlib1g-dev
        libx11-dev libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev
        libxkbcommon-dev libxkbcommon-x11-dev libx11-xcb-dev libxcb-xkb-dev
        libdbus-1-dev libgl1-mesa-dev libfontconfig-dev
    )
    _install_missing_apt_packages "Kilix build dependencies" "${deps[@]}"
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
    local answer
    local -a bootstrap_args=()
    if "$KILIX_DIR/kilix" --which >/dev/null 2>&1; then
        log "engine: $("$KILIX_DIR/kilix" --which 2>/dev/null | tail -1)"
        return 0
    fi
    [ -x "$KILIX_DIR/bootstrap.sh" ] || die "no engine and no bootstrap.sh in $KILIX_DIR"
    if { [ -n "$KILIX_PREBUILT_VERSION" ] && [ -z "$KILIX_PREBUILT_SHA256" ]; } \
        || { [ -z "$KILIX_PREBUILT_VERSION" ] && [ -n "$KILIX_PREBUILT_SHA256" ]; }; then
        die "KILIX_PREBUILT_VERSION and KILIX_PREBUILT_SHA256 must be set together"
    fi
    if [ -z "$KILIX_PREBUILT_VERSION" ]; then
        [ -t 0 ] || die "non-interactive engine install requires pinned KILIX_PREBUILT_VERSION and KILIX_PREBUILT_SHA256"
        warn "no kitty bundle checksum is pinned; Plebian-OS release installs always pin one"
        ask "Explicitly allow bootstrap.sh to download the displayed unverified asset? [y/N]"
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

    ensure_system_deps
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

# do_uninstall — remove the system session files and command links. Checkouts,
# caches/state, and packages installed as dependencies are intentionally kept.
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
