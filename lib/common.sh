#!/usr/bin/env bash
# lib/common.sh — shared helpers for the `pleb` CLI. Sourced, not executed.
# Several vars below are consumed by the other sourced modules (install.sh,
# test.sh, autologin.sh) and by bin/pleb, which shellcheck can't see here.
# shellcheck disable=SC2034

# --- paths -------------------------------------------------------------------
# PLEB_ROOT is the checkout containing this CLI. Resolve the actual path so an
# installed /usr/local/bin/pleb symlink keeps working even when the source root
# is configured differently.
PLEB_ROOT="${PLEB_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Load the same persistent session defaults as bin/pleb-session before deriving
# any paths below.  Explicit values in the caller's environment win, matching
# the session launcher's behaviour.  These files have always been shell env
# files (and are sourced by pleb-session); the CLI deliberately uses that same
# established contract rather than implementing a subtly different parser.
PLEB_ENV_SYSTEM="${PLEB_ENV_SYSTEM:-/etc/pleb/session.env}"
# The user env file must be located before it can supply path defaults. An
# explicit environment path wins; otherwise locate it below the most specific
# inherited storage root, finally falling back to the canonical writable root.
if [[ ! ${PLEB_ENV_USER+x} ]]; then
    if [[ ${PLEB_CONFIG_HOME+x} ]]; then
        PLEB_ENV_USER="$PLEB_CONFIG_HOME/session.env"
    elif [[ ${PLEB_STORAGE_HOME+x} ]]; then
        PLEB_ENV_USER="$PLEB_STORAGE_HOME/config/session.env"
    elif [[ ${GPU_TERMINAL_HOME+x} ]]; then
        PLEB_ENV_USER="$GPU_TERMINAL_HOME/pleb/config/session.env"
    else
        PLEB_ENV_USER="$HOME/.local/gpu_terminal/pleb/config/session.env"
    fi
fi

_pleb_config_safe_to_source() {
    local cfg="$1" owner mode dir
    [ "$(id -u)" = 0 ] || return 0
    # A root CLI must not source through a symlink or a user-replaceable parent:
    # checking only the final target leaves a stat/source race in a user-owned
    # config directory. Root-managed /etc configuration passes this walk; a
    # per-user file is deliberately ignored when the CLI itself runs as root.
    case "$cfg" in /*) ;; *) return 1 ;; esac
    [ -f "$cfg" ] && [ ! -L "$cfg" ] || return 1
    owner="$(stat -c '%u' "$cfg" 2>/dev/null)" || return 1
    mode="$(stat -c '%a' "$cfg" 2>/dev/null)" || return 1
    if [ "$owner" != 0 ] || (( (8#$mode & 8#22) != 0 )); then
        printf '[pleb] refusing to source unsafe config as root: %s\n' "$cfg" >&2
        return 1
    fi
    dir="$(dirname "$cfg")"
    while [ "$dir" != / ]; do
        owner="$(stat -c '%u' "$dir" 2>/dev/null)" || return 1
        mode="$(stat -c '%a' "$dir" 2>/dev/null)" || return 1
        if [ "$owner" != 0 ] || (( (8#$mode & 8#22) != 0 )); then
            printf '[pleb] refusing config below an unsafe directory as root: %s\n' "$cfg" >&2
            return 1
        fi
        dir="$(dirname "$dir")"
    done
}

# Where each session-managed value came from. A pinned ref that moves a
# component is only understandable if the answer to "who decided that?" is
# printed beside the move: an override is per-run, so the next plain update
# silently reinstates whatever the persisted pin says.
declare -A PLEB_ENV_ORIGIN=()

# _pleb_value_origin NAME — the file (or "the environment") that supplied NAME,
# for reporting only. Never used to decide precedence.
_pleb_value_origin() {
    printf '%s\n' "${PLEB_ENV_ORIGIN[$1]:-the built-in default}"
}

load_pleb_session_env() {
    local vars var cfg
    vars="PLEB_DIR PLEB_REPO PLEB_BRANCH PLEB_REF PLEB_ALLOW_MUTABLE_REF PLEB_SELF_UPDATE GPU_TERMINAL_SOURCE_HOME GPU_TERMINAL_HOME GPU_TERMINAL_SETTINGS_FILE PLEB_STORAGE_HOME PLEB_CONFIG_HOME PLEB_STATE_HOME PLEB_CACHE_HOME PLEB_SESSION_HOME PLEB_DATA_HOME KILIX_STORAGE_HOME KILIX_CONFIG_HOME KILIX_STATE_DIRECTORY KILIX_CACHE_HOME KILIX_SESSION_HOME KILIX_DATA_HOME KILIX_TRANSCRIPT_DIR KILIX_BUILD_DIRECTORY KILIX_PREBUILT_HOME KILIX95_STORAGE_HOME KILIX95_CONFIG_HOME KILIX95_STATE_HOME KILIX95_CACHE_HOME KILIX95_SESSION_HOME KILIX95_DATA_HOME KILIX_DESKTOP_DIR KILIX_DIR KILIX KILIX_REPO KILIX_BRANCH KILIX_REF KILIX_ALLOW_MUTABLE_REF KILIX_PREBUILT_VERSION KILIX_PREBUILT_SHA256 PLEB_KILIX_ARGS PLEB_WM PLEB_OPENBOX_CONFIG PLEB_WM_TIMEOUT KILIX_RUN_ALIASES KILIX_RUN_ALIAS_APPS KILIX_RUN_ALIAS_EXCLUDE_APPS PLEB_NO_FILL PLEB_BG PLEB_LOG PLEB_RESPAWN PLEB_DESKTOP KILIX_DESKTOP_PROVIDER KILIX_DESKTOP_COMMAND KILIX_DESKTOP_NAME KILIX_DESKTOP_FLAVOR KILIX_CAP_AUTO_INSTALL KILIX_CAP_DIR KILIX_CAP_REPO KILIX_CAP_REF KILIX_CAP_TRUST_EXISTING_CHECKOUT KILIX_CAP_ALLOW_MUTABLE_REF KILIX_TUI_UTILS_AUTO_INSTALL KILIX_TUI_UTILS_DIR KILIX_TUI_UTILS_REPO KILIX_TUI_UTILS_REF KILIX_TUI_UTILS_TRUST_EXISTING_CHECKOUT KILIX_TUI_UTILS_ALLOW_MUTABLE_REF KILIX_LAND_DESKTOP_AUTO_INSTALL KILIX_LAND_DESKTOP_DIR KILIX_LAND_DESKTOP_REPO KILIX_LAND_DESKTOP_REF KILIX_LAND_DESKTOP_TRUST_EXISTING_CHECKOUT KILIX_LAND_DESKTOP_ALLOW_MUTABLE_REF KILIX_LAND_DESKTOP_ASSETS KILIX_LAND_DESKTOP_CONFIG_HOME KILIX_LAND_DESKTOP_EXTERNAL_APPS KILIX_LAND_DESKTOP_AUDIO KILIX95_AUTO_INSTALL KILIX95_DIR KILIX95_REPO KILIX95_BRANCH KILIX95_REF KILIX95_ALLOW_MUTABLE_REF KILIX95_ALLOW_UNPINNED_INSTALL PLEB_INSTALL_KILIX95 PLEB_SKIP_DEPS PLEBIAN_OS_MANAGED_INSTALL PLEB_UPDATE_LOCK_FD KILIX_TRANSACTION_LOCK_FD KILIX_TRANSACTION_LOCK_PATH PLEBIAN_OS_BUILD_KILIX_FORK PLEBIAN_OS_KILIX_GO_MIN_VERSION PLEBIAN_OS_KILIX_GO_VERSION PLEBIAN_OS_KILIX_GO_SHA256_AMD64 PLEBIAN_OS_KILIX_GO_SHA256_ARM64"
    declare -A had saved
    for var in $vars; do
        if [[ ${!var+x} ]]; then
            had[$var]=1
            saved[$var]="${!var}"
        else
            had[$var]=0
        fi
    done
    for cfg in "$PLEB_ENV_SYSTEM" "$PLEB_ENV_USER"; do
        # shellcheck source=/dev/null
        if [ -r "$cfg" ] && _pleb_config_safe_to_source "$cfg"; then
            . "$cfg"
            # Record the last file that supplied each value. The caller's
            # environment is restored below and wins, exactly as before; this
            # only remembers which file would otherwise have decided.
            for var in $vars; do
                if [ "${had[$var]}" = 0 ] && [[ ${!var+x} ]]; then
                    PLEB_ENV_ORIGIN[$var]="$cfg"
                fi
            done
        fi
    done
    for var in $vars; do
        if [ "${had[$var]}" = 1 ]; then
            printf -v "$var" '%s' "${saved[$var]}"
            PLEB_ENV_ORIGIN[$var]="the environment"
        elif [ "$var" = PLEB_UPDATE_LOCK_FD ] \
            || [ "$var" = KILIX_TRANSACTION_LOCK_FD ] \
            || [ "$var" = KILIX_TRANSACTION_LOCK_PATH ]; then
            # Inherited transaction capabilities (and their asserted path) are
            # process-local, never persisted configuration. Ignore attempts to
            # synthesize them in an env file.
            unset "$var"
        fi
    done
}
load_pleb_session_env

# Derive defaults only after persisted configuration has loaded. This is what
# lets a path stored in session.env affect both the CLI and the login session;
# assigning these defaults before load_pleb_session_env would incorrectly make
# them look like explicit caller overrides.
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
GPU_TERMINAL_SETTINGS_FILE="${GPU_TERMINAL_SETTINGS_FILE:-$GPU_TERMINAL_HOME/settings.conf}"
PLEB_STORAGE_HOME="${PLEB_STORAGE_HOME:-$GPU_TERMINAL_HOME/pleb}"
PLEB_CONFIG_HOME="${PLEB_CONFIG_HOME:-$PLEB_STORAGE_HOME/config}"
PLEB_STATE_HOME="${PLEB_STATE_HOME:-$PLEB_STORAGE_HOME/state}"
PLEB_CACHE_HOME="${PLEB_CACHE_HOME:-$PLEB_STORAGE_HOME/cache}"
PLEB_SESSION_HOME="${PLEB_SESSION_HOME:-$PLEB_STORAGE_HOME/session}"
PLEB_DATA_HOME="${PLEB_DATA_HOME:-$PLEB_STORAGE_HOME/data}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_CONFIG_HOME="${KILIX_CONFIG_HOME:-$KILIX_STORAGE_HOME/config}"
KILIX_STATE_DIRECTORY="${KILIX_STATE_DIRECTORY:-$KILIX_STORAGE_HOME/state}"
KILIX_CACHE_HOME="${KILIX_CACHE_HOME:-$KILIX_STORAGE_HOME/cache}"
KILIX_SESSION_HOME="${KILIX_SESSION_HOME:-$KILIX_STORAGE_HOME/session}"
KILIX_DATA_HOME="${KILIX_DATA_HOME:-$KILIX_STORAGE_HOME/data}"
KILIX_BUILD_DIRECTORY="${KILIX_BUILD_DIRECTORY:-$KILIX_STORAGE_HOME/build}"
KILIX_PREBUILT_HOME="${KILIX_PREBUILT_HOME:-$KILIX_STORAGE_HOME/prebuilt/kitty.app}"
KILIX95_STORAGE_HOME="${KILIX95_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix-95}"
KILIX95_CONFIG_HOME="${KILIX95_CONFIG_HOME:-$KILIX95_STORAGE_HOME/config}"
KILIX95_STATE_HOME="${KILIX95_STATE_HOME:-$KILIX95_STORAGE_HOME/state}"
KILIX95_CACHE_HOME="${KILIX95_CACHE_HOME:-$KILIX95_STORAGE_HOME/cache}"
KILIX95_SESSION_HOME="${KILIX95_SESSION_HOME:-$KILIX95_STORAGE_HOME/session}"
KILIX95_DATA_HOME="${KILIX95_DATA_HOME:-$KILIX95_STORAGE_HOME/data}"
KILIX_DESKTOP_DIR="${KILIX_DESKTOP_DIR:-$PLEB_DATA_HOME/desktop}"
export GPU_TERMINAL_SOURCE_HOME GPU_TERMINAL_HOME GPU_TERMINAL_SETTINGS_FILE
export PLEB_STORAGE_HOME PLEB_CONFIG_HOME PLEB_STATE_HOME PLEB_CACHE_HOME
export PLEB_SESSION_HOME PLEB_DATA_HOME KILIX_STORAGE_HOME KILIX_CONFIG_HOME
export KILIX_STATE_DIRECTORY KILIX_CACHE_HOME KILIX_SESSION_HOME KILIX_DATA_HOME
export KILIX_BUILD_DIRECTORY KILIX_PREBUILT_HOME KILIX95_STORAGE_HOME KILIX95_CONFIG_HOME
export KILIX95_STATE_HOME KILIX95_CACHE_HOME KILIX95_SESSION_HOME KILIX95_DATA_HOME
export KILIX_DESKTOP_DIR
export PLEB_ENV_SYSTEM PLEB_ENV_USER

PLEB_BIN_SRC="$PLEB_ROOT/bin/pleb-session"
PLEB_DESKTOP_IN="$PLEB_ROOT/share/pleb.desktop.in"
PLEB_RECOVERY_DOC_SRC="$PLEB_ROOT/docs/RECOVERY.md"
OPENBOX_CONFIG_SRC="$PLEB_ROOT/share/openbox/rc.xml"

# install destinations (system-wide, so LightDM/other users can see them)
SESSION_BIN_DST="${SESSION_BIN_DST:-/usr/local/bin/pleb-session}"
XSESSION_DST="${XSESSION_DST:-/usr/share/xsessions/pleb.desktop}"
AUTOLOGIN_CONF="${AUTOLOGIN_CONF:-/etc/lightdm/lightdm.conf.d/50-pleb-autologin.conf}"
# `kilix` command on PATH (so `kilix desktop`, `kilix serve`, … work out of the
# box). /usr/local/bin is on PATH and FHS-correct for local installs.
KILIX_LINK="${KILIX_LINK:-/usr/local/bin/kilix}"
# `pleb` command itself on PATH, so `pleb update`/`pleb status`/… work anywhere.
PLEB_LINK="${PLEB_LINK:-/usr/local/bin/pleb}"
# Shared status-widget and pane-button TUI supplied by the Kilix checkout.
KILIX_SETTINGS_LINK="${KILIX_SETTINGS_LINK:-/usr/local/bin/kilix-settings}"
# kilix-tui-utils installs both dashboards under the invoking user's local
# prefix; Pleb publishes the thermal command for desktop/login PATHs.
KILIX_TEMPS_BIN="$HOME/.local/bin/kilix-temps"
KILIX_MEMORY_BIN="$HOME/.local/bin/kilix-memory"
# The Music front end is a client of kilix-amp's control socket rather than a
# player of its own, so it is the one utility whose usefulness depends on a
# second repository. Checked at install time for that reason.
KILIX_MUSIC_BIN="$HOME/.local/bin/kilix-music"
# Kilix Amp is catalog content, so Kilix's own data directory owns it and its
# commit comes from the pinned content catalog rather than a ref here.
KILIX_AMP_BIN="$KILIX_DATA_HOME/desktop-apps/kilix-amp/kilix-amp"
# The PDF viewer follows the same catalog-owned layout. Its shell launcher is
# always usable when Evince is present; the native Poppler core appears beside
# it when the target has the required development files.
KILIX_PDF_VIEWER_BIN="$KILIX_DATA_HOME/desktop-apps/kilix-pdf/kilix-pdf-viewer"
KILIX_PDF_CORE_BIN="$KILIX_DATA_HOME/desktop-apps/kilix-pdf/build/kilix-pdf-core"
KILIX_TEMPS_LINK="${KILIX_TEMPS_LINK:-/usr/local/bin/kilix-temps}"
# Kilix installs its pinned tmux-tui/tmux-cli source closure under the same
# user prefix. Pleb publishes both commands for login shells and system menus.
TMUX_TUI_BIN="$HOME/.local/bin/tmux-tui"
TMUX_CLI_BIN="$HOME/.local/bin/tb"
TMUX_TUI_STAMP="$KILIX_STATE_DIRECTORY/tmux-tui-install.refs"
TMUX_TUI_LINK="${TMUX_TUI_LINK:-/usr/local/bin/tmux-tui}"
TMUX_CLI_LINK="${TMUX_CLI_LINK:-/usr/local/bin/tb}"
# Kilix installs its pinned read-aloud/dictation closure under the same user
# prefix; both TUIs come out of one install, alongside the arbiter daemon the
# tab-bar widgets talk to. Voice is allowed to be absent only under the
# read-aloud-only policy — see install_kilix_voice — so these paths are checked
# before they are published rather than asserted afterwards.
KILIX_VOICE_TTS_BIN="$HOME/.local/bin/kilix-tts"
KILIX_VOICE_STT_BIN="$HOME/.local/bin/kilix-stt"
KILIX_VOICE_TTS_LINK="${KILIX_VOICE_TTS_LINK:-/usr/local/bin/kilix-tts}"
KILIX_VOICE_STT_LINK="${KILIX_VOICE_STT_LINK:-/usr/local/bin/kilix-stt}"
# Kilix owns the pinned broker source and its private native build. Pleb exposes
# the manager through the already-published `kilix pty` command instead of a raw
# broker link whose default runtime could diverge from the active Kilix session.
KILIX_PTY_BROKER_BUILD="$KILIX_BUILD_DIRECTORY/libraries/kitty-pty-broker"
KILIX_PTY_BROKER_BIN="$KILIX_PTY_BROKER_BUILD/kitty-pty-broker"
# Stable, user-readable documentation path consumed by the Kilix-95 Help menu.
PLEB_RECOVERY_DOC_DST="${PLEB_RECOVERY_DOC_DST:-/usr/local/share/doc/pleb/RECOVERY.md}"
# Openbox profile the session launcher passes to `openbox --config-file`, kept
# system-wide so every user's session reads the same managed copy. This default
# is a sync pair with PLEB_OPENBOX_CONFIG in bin/pleb-session: the launcher is
# self-contained and cannot source this file, so the two literals must match.
OPENBOX_CONFIG_DST="${OPENBOX_CONFIG_DST:-/usr/local/share/pleb/openbox/rc.xml}"

# pleb itself. `pleb update` moves this checkout like any other component, so a
# release that bumps Pleb reaches an installed machine through the same command
# that delivers Kilix instead of needing the root-only OS-layer updater.
# PLEB_DIR is the provisioned location and must resolve to the checkout this CLI
# is actually running from before a self-update is attempted.
PLEB_DIR="${PLEB_DIR:-$PLEB_ROOT}"
PLEB_REPO="${PLEB_REPO:-https://github.com/itsmygithubacct/pleb.git}"
PLEB_BRANCH="${PLEB_BRANCH:-}"   # empty = the repo's default branch
PLEB_REF="${PLEB_REF:-}"         # optional full commit SHA
PLEB_ALLOW_MUTABLE_REF="${PLEB_ALLOW_MUTABLE_REF:-0}"
PLEB_SELF_UPDATE="${PLEB_SELF_UPDATE:-1}"
export PLEB_DIR

# kilix engine: where it lives, how to fetch it, and the launcher path.
KILIX_DIR="${KILIX_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix}"
KILIX_DEFAULT="${KILIX:-$KILIX_DIR/kilix}"
KILIX_REPO="${KILIX_REPO:-https://github.com/itsmygithubacct/kilix.git}"
KILIX_BRANCH="${KILIX_BRANCH:-}"   # empty = the repo's default branch
KILIX_REF="${KILIX_REF:-}"         # optional full commit SHA
KILIX_ALLOW_MUTABLE_REF="${KILIX_ALLOW_MUTABLE_REF:-0}"
KILIX_PREBUILT_VERSION="${KILIX_PREBUILT_VERSION:-}" # empty = latest fallback
KILIX_PREBUILT_SHA256="${KILIX_PREBUILT_SHA256:-}"   # optional pinned checksum

# Desktop provider passed through to `kilix desktop`. Pleb defaults to the
# `auto` prefers an installed external Kilix 95 provider and otherwise uses the
# bundled compatible provider. Release manifests select/pin `external` exactly.
KILIX_DESKTOP_PROVIDER="${KILIX_DESKTOP_PROVIDER:-auto}"
KILIX_DESKTOP_COMMAND="${KILIX_DESKTOP_COMMAND:-}"
KILIX_DESKTOP_NAME="${KILIX_DESKTOP_NAME:-desktop}"

# Optional native Kilix Cap desktop. Kilix owns its first-use clone/build; Pleb
# only carries these reviewed source-selection knobs into the login session.
KILIX_CAP_AUTO_INSTALL="${KILIX_CAP_AUTO_INSTALL:-1}"
KILIX_CAP_DIR="${KILIX_CAP_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-cap}"
KILIX_CAP_REPO="${KILIX_CAP_REPO:-https://github.com/itsmygithubacct/kilix-cap.git}"
KILIX_CAP_REF="${KILIX_CAP_REF:-}"
KILIX_CAP_TRUST_EXISTING_CHECKOUT="${KILIX_CAP_TRUST_EXISTING_CHECKOUT:-0}"
KILIX_CAP_ALLOW_MUTABLE_REF="${KILIX_CAP_ALLOW_MUTABLE_REF:-0}"

# Optional Kilix TUI text desktop. Same arrangement as Cap: Kilix owns the
# first-use clone/install; Pleb only carries the selection knobs.
KILIX_TUI_UTILS_AUTO_INSTALL="${KILIX_TUI_UTILS_AUTO_INSTALL:-1}"
KILIX_TUI_UTILS_DIR="${KILIX_TUI_UTILS_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-tui-utils}"
KILIX_TUI_UTILS_REPO="${KILIX_TUI_UTILS_REPO:-https://github.com/itsmygithubacct/kilix-tui-utils.git}"
KILIX_TUI_UTILS_REF="${KILIX_TUI_UTILS_REF:-}"
KILIX_TUI_UTILS_TRUST_EXISTING_CHECKOUT="${KILIX_TUI_UTILS_TRUST_EXISTING_CHECKOUT:-0}"
KILIX_TUI_UTILS_ALLOW_MUTABLE_REF="${KILIX_TUI_UTILS_ALLOW_MUTABLE_REF:-0}"

# Optional Kilix Land walkable desktop. Kilix owns its pinned clone, recursive
# dependency checkout, and build; Pleb carries both install and runtime knobs.
KILIX_LAND_DESKTOP_AUTO_INSTALL="${KILIX_LAND_DESKTOP_AUTO_INSTALL:-1}"
KILIX_LAND_DESKTOP_DIR="${KILIX_LAND_DESKTOP_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-land-desktop}"
KILIX_LAND_DESKTOP_REPO="${KILIX_LAND_DESKTOP_REPO:-https://github.com/itsmygithubacct/kilix-land-desktop.git}"
KILIX_LAND_DESKTOP_REF="${KILIX_LAND_DESKTOP_REF:-}"
KILIX_LAND_DESKTOP_TRUST_EXISTING_CHECKOUT="${KILIX_LAND_DESKTOP_TRUST_EXISTING_CHECKOUT:-0}"
KILIX_LAND_DESKTOP_ALLOW_MUTABLE_REF="${KILIX_LAND_DESKTOP_ALLOW_MUTABLE_REF:-0}"
KILIX_LAND_DESKTOP_ASSETS="${KILIX_LAND_DESKTOP_ASSETS:-}"
KILIX_LAND_DESKTOP_CONFIG_HOME="${KILIX_LAND_DESKTOP_CONFIG_HOME:-}"
KILIX_LAND_DESKTOP_EXTERNAL_APPS="${KILIX_LAND_DESKTOP_EXTERNAL_APPS:-}"
KILIX_LAND_DESKTOP_AUDIO="${KILIX_LAND_DESKTOP_AUDIO:-}"

# Optional Kilix 95 desktop checkout. Plain Pleb shell sessions and custom
# desktop commands do not require it; install/update touch it only when the
# selected provider needs it or PLEB_INSTALL_KILIX95=1.
KILIX95_DIR="${KILIX95_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-95}"
KILIX95_REPO="${KILIX95_REPO:-https://github.com/itsmygithubacct/kilix-95.git}"
KILIX95_BRANCH="${KILIX95_BRANCH:-}"   # empty = the repo's default branch
KILIX95_REF="${KILIX95_REF:-}"         # optional full commit SHA
KILIX95_ALLOW_MUTABLE_REF="${KILIX95_ALLOW_MUTABLE_REF:-0}"
KILIX95_ALLOW_UNPINNED_INSTALL="${KILIX95_ALLOW_UNPINNED_INSTALL:-0}"

# Carry old login environments across the workspace move without masking a
# real checkout or overriding a custom provider path.
_pleb_rehome_legacy_desktop_dir() {
    local variable="$1" legacy="$2" canonical="$3" value
    value="${!variable:-}"
    if [ "$value" = "$legacy" ] && [ ! -e "$legacy" ] && [ ! -L "$legacy" ]; then
        printf -v "$variable" '%s' "$canonical"
    fi
}
_pleb_rehome_legacy_desktop_dir \
    KILIX95_DIR "$GPU_TERMINAL_SOURCE_HOME/kilix-95" \
    "$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-95"
_pleb_rehome_legacy_desktop_dir \
    KILIX_CAP_DIR "$GPU_TERMINAL_SOURCE_HOME/kilix-cap" \
    "$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-cap"
_pleb_rehome_legacy_desktop_dir \
    KILIX_TUI_UTILS_DIR "$GPU_TERMINAL_SOURCE_HOME/kilix-tui-utils" \
    "$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-tui-utils"
_pleb_rehome_legacy_desktop_dir \
    KILIX_LAND_DESKTOP_DIR "$GPU_TERMINAL_SOURCE_HOME/kilix-land-desktop" \
    "$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-land-desktop"
unset -f _pleb_rehome_legacy_desktop_dir
export KILIX_DIR KILIX_DEFAULT KILIX95_DIR KILIX_CAP_DIR KILIX_TUI_UTILS_DIR
export KILIX_LAND_DESKTOP_DIR
export KILIX_TEMPS_BIN KILIX_MEMORY_BIN
export TMUX_TUI_BIN TMUX_CLI_BIN KILIX_PTY_BROKER_BUILD KILIX_PTY_BROKER_BIN

# --- pretty output -----------------------------------------------------------
if [ -t 1 ]; then
    _c_g=$'\033[1;32m'; _c_r=$'\033[1;31m'; _c_y=$'\033[1;33m'; _c_b=$'\033[1;34m'; _c_0=$'\033[0m'
else
    _c_g=; _c_r=; _c_y=; _c_b=; _c_0=
fi
log()  { printf '%s[pleb]%s %s\n'  "$_c_g" "$_c_0" "$*"; }
warn() { printf '%s[pleb]%s %s\n'  "$_c_y" "$_c_0" "$*" >&2; }
err()  { printf '%s[pleb]%s %s\n'  "$_c_r" "$_c_0" "$*" >&2; }
die()  { err "$*"; exit 1; }
info() { printf '  %s%s%s\n' "$_c_b" "$*" "$_c_0"; }
# ask PROMPT — green [pleb] prompt with no trailing newline, for a following `read`
ask()  { printf '%s[pleb]%s %s ' "$_c_g" "$_c_0" "$*"; }

# shellcheck source=storage.sh
. "$PLEB_ROOT/lib/storage.sh"

# --- privilege ---------------------------------------------------------------
# run_root CMD... — run as root: directly if already root, else via sudo.
run_root() {
    if [ "$(id -u)" = 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        die "need root for: $* (no sudo available)"
    fi
}

# write_root DEST — read stdin, write it to DEST as root (via a temp file so we
# never need a root shell redirection).
write_root() {
    local dest="$1" tmp
    ensure_pleb_private_storage
    tmp="$(mktemp "$PLEB_SESSION_HOME/root-write.XXXXXX")" || die "mktemp failed"
    cat >"$tmp"
    run_root install -D -m 0644 "$tmp" "$dest"
    rm -f "$tmp"
}

# --- misc --------------------------------------------------------------------
# the user the pleb session/autologin should belong to (the invoking user, even
# under sudo).
target_user() { echo "${SUDO_USER:-$(id -un)}"; }

desktop_enabled() {
    case "${KILIX_DESKTOP_PROVIDER:-auto}" in
        none|off|disabled) return 1 ;;
    esac
    case "${PLEB_DESKTOP:-0}" in
        1|yes|true|on|desktop|kilix95|kilix-95|command|custom) return 0 ;;
        *) return 1 ;;
    esac
}

kilix95_required() {
    [ "${PLEB_INSTALL_KILIX95:-0}" = 1 ] && return 0
    desktop_enabled || return 1
    case "${KILIX_DESKTOP_PROVIDER:-auto}" in
        external|xp|kilix-xp) return 0 ;;
        auto) [ ! -f "$KILIX_DIR/desktop/main.py" ] ;;
        *) return 1 ;;
    esac
}

validate_checkout_origin() {
    local dir="$1" repo="$2" label="$3" remote
    [ -d "$dir/.git" ] || return 0
    remote="$(git -C "$dir" config --get remote.origin.url 2>/dev/null || true)"
    if [ -n "$remote" ] && [ "$remote" != "$repo" ] \
        && [ "${PLEB_TRUST_EXISTING_CHECKOUT:-${PLEBIAN_OS_TRUST_EXISTING_CHECKOUT:-0}}" != 1 ]; then
        die "$label checkout at $dir has origin '$remote', expected '$repo' (set PLEB_TRUST_EXISTING_CHECKOUT=1 to override)"
    fi
}

require_clean_checkout() {
    local dir="$1" label="$2" status
    [ -d "$dir/.git" ] || return 0
    status="$(git -C "$dir" status --porcelain --untracked-files=normal 2>/dev/null)" \
        || die "could not inspect $label checkout at $dir"
    if [ -n "$status" ]; then
        err "$label checkout at $dir has local changes; refusing to update it:"
        printf '%s\n' "$status" >&2
        die "commit, stash, or remove those changes, then re-run 'pleb update'"
    fi
}

require_immutable_ref() {
    local ref="$1" allow_mutable="$2" ref_name="$3" override_name="$4"
    [ -n "$ref" ] || return 0
    if ! [[ "$ref" =~ ^[0-9a-fA-F]{40}$ ]] && [ "$allow_mutable" != 1 ]; then
        die "$ref_name must be a full 40-character commit SHA (set $override_name=1 only to trust a mutable tag/branch)"
    fi
}

# announce_component_move DIR LABEL BEFORE AFTER REF_NAME — say a pinned move
# out loud, and shout when it walks an installed component BACKWARDS.
#
# Ref overrides are per-run by design and that precedence is deliberate: the
# persisted pin is the machine's declared state. What makes it a foot-gun is
# silence — a later plain update reinstates the pin and a delivered fix vanishes
# with no trace in the log. So every pinned move names both ends and the thing
# that decided them, and a rewind says so in as many words.
announce_component_move() {
    local dir="$1" label="$2" before="$3" after="$4" ref_name="${5:-}" origin
    [ -n "$before" ] && [ -n "$after" ] && [ "$before" != "$after" ] || return 0
    origin="$(_pleb_value_origin "$ref_name")"
    if git -C "$dir" merge-base --is-ancestor "$after" "$before" >/dev/null 2>&1; then
        warn "$label: ${before:0:12} -> ${after:0:12} (DOWNGRADE, pinned by $origin)"
        warn "$label: the installed commit was newer; export ${ref_name:-the ref} to keep it"
    else
        log "$label: ${before:0:12} -> ${after:0:12} (pinned by $origin)"
    fi
}

checkout_fetched_ref() {
    local dir="$1" ref="$2" label="$3" ref_name="${4:-}" resolved actual before
    before="$(git -C "$dir" rev-parse --verify HEAD 2>/dev/null || true)"
    log "fetching exact $label ref $ref from origin"
    git -C "$dir" fetch --no-tags origin "$ref" \
        || die "$label fetch failed for ref $ref"
    resolved="$(git -C "$dir" rev-parse --verify 'FETCH_HEAD^{commit}' 2>/dev/null)" \
        || die "fetched $label ref $ref did not resolve to a commit"
    # Component-specific reconciliation happens after the parent move.  Do not
    # let a repository's persistent recursive-submodule policy traverse a new
    # gitlink before its caller has had a chance to initialize it safely.
    git -C "$dir" -c submodule.recurse=false checkout --detach "$resolved" \
        || die "could not check out fetched $label ref $ref ($resolved)"
    actual="$(git -C "$dir" rev-parse --verify HEAD 2>/dev/null)" \
        || die "could not verify $label HEAD after checkout"
    [ "$actual" = "$resolved" ] \
        || die "$label checkout verification failed (expected $resolved, got $actual)"
    log "$label pinned at $resolved"
    announce_component_move "$dir" "$label" "$before" "$actual" "$ref_name"
}

# reconcile_kilix_submodules DIR — initialize the exact Kilix closure and leave
# ordinary future checkout/reset commands recursive.  Plebian-OS 0.1.8's outer
# updater predates some 0.1.9 Kilix submodules, so it cannot snapshot them by
# name.  Its rollback can still restore changed and newly introduced gitlinks
# when the target Pleb installer persists this repository-local contract.
reconcile_kilix_submodules() {
    local dir="$1" recurse
    git -C "$dir" config --local --type=bool submodule.recurse true \
        || die "could not enable recursive Kilix submodule transactions"
    recurse="$(git -C "$dir" config --local --type=bool --get submodule.recurse \
        2>/dev/null || true)"
    [ "$recurse" = true ] \
        || die "recursive Kilix submodule transaction policy did not persist"
    git -C "$dir" submodule update --init --recursive \
        || die "kilix submodule update failed"
}
