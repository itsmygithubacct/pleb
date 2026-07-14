#!/usr/bin/env bash
# lib/test.sh — exercise the Pleb session in a throwaway X server WITHOUT
# touching the live desktop. Sourced by `pleb`.
#
#   pleb test                 # auto: Xephyr if in a desktop, else spare VT
#   pleb test --xephyr        # nested window on the current $DISPLAY
#   pleb test --vt 9          # real X server on vt9 (view: Ctrl+Alt+F9)
#   pleb test --check         # non-interactive: bring it up, verify, tear down
#   pleb test --vt 9 --check --secs 6

_T_GEOM=1280x800; _T_VT=9; _T_CHECK=0; _T_SECS=6; _T_MODE=auto
_T_ENTRY=""; _T_TESTLOG="$PLEB_STATE_HOME/test.log"

_free_display() {  # echo a free ":N" starting at $1
    local n="$1"; while [ -e "/tmp/.X11-unix/X$n" ]; do n=$((n+1)); done; echo ":$n"
}

_test_report() {   # $1=alive(0/1)
    echo
    if [ "$1" = 1 ]; then log "PASS — kilix is running in the test session"; else err "FAIL — kilix did not come up"; fi
    echo "  --- session log (tail) ---"
    tail -n 15 "$PLEB_STATE_HOME/session.log" 2>/dev/null | sed 's/^/  /'
    echo "  --- test log (tail) ---"
    tail -n 8 "$_T_TESTLOG" 2>/dev/null | sed 's/^/  /'
}

_test_xephyr() {
    command -v Xephyr >/dev/null 2>&1 || die "Xephyr missing (apt install xserver-xephyr)"
    [ -n "${DISPLAY:-}" ] || die "no host DISPLAY for Xephyr; try: pleb test --vt $_T_VT"
    local nd; nd="$(_free_display 7)"
    log "Xephyr $nd on host $DISPLAY ($_T_GEOM); close the window to exit"
    Xephyr "$nd" -screen "$_T_GEOM" -title "pleb-test $nd — close to exit" -resizeable \
        >"$_T_TESTLOG" 2>&1 &
    local xpid=$!
    sleep 2
    if ! kill -0 "$xpid" 2>/dev/null; then _test_report 0; die "Xephyr failed to start (see $_T_TESTLOG)"; fi
    if [ "$_T_CHECK" = 1 ]; then
        # PLEB_RESPAWN=0 forces pleb-session to exec kilix, so $! becomes the
        # kitty PID we can kill *surgically* — never the live kiosk's kilix.
        DISPLAY="$nd" KILIX_DIR="$KILIX_DIR" KILIX="$KILIX_DEFAULT" \
            KILIX_REF="$KILIX_REF" KILIX_DESKTOP_PROVIDER="$KILIX_DESKTOP_PROVIDER" \
            KILIX_DESKTOP_COMMAND="$KILIX_DESKTOP_COMMAND" KILIX_DESKTOP_NAME="$KILIX_DESKTOP_NAME" \
            KILIX95_DIR="$KILIX95_DIR" KILIX95_REPO="$KILIX95_REPO" \
            KILIX95_BRANCH="$KILIX95_BRANCH" KILIX95_REF="$KILIX95_REF" \
            PLEB_RESPAWN=0 "$_T_ENTRY" &
        local sp=$!
        sleep "$_T_SECS"
        local alive=0; kill -0 "$sp" 2>/dev/null && alive=1
        kill "$sp" 2>/dev/null || true
        kill "$xpid" 2>/dev/null || true
        _test_report "$alive"; [ "$alive" = 1 ]
    else
        log "launching pleb-session in $nd (Ctrl+C here to stop)"
        DISPLAY="$nd" KILIX_DIR="$KILIX_DIR" KILIX="$KILIX_DEFAULT" \
            KILIX_REF="$KILIX_REF" KILIX_DESKTOP_PROVIDER="$KILIX_DESKTOP_PROVIDER" \
            KILIX_DESKTOP_COMMAND="$KILIX_DESKTOP_COMMAND" KILIX_DESKTOP_NAME="$KILIX_DESKTOP_NAME" \
            KILIX95_DIR="$KILIX95_DIR" KILIX95_REPO="$KILIX95_REPO" \
            KILIX95_BRANCH="$KILIX95_BRANCH" KILIX95_REF="$KILIX95_REF" \
            PLEB_RESPAWN=0 "$_T_ENTRY"
        kill "$xpid" 2>/dev/null || true
    fi
}

_test_vt() {
    command -v startx >/dev/null 2>&1 || die "startx missing (apt install xinit)"
    local nd; nd="$(_free_display 3)"
    log "nested X on vt$_T_VT ($nd) — view with Ctrl+Alt+F$_T_VT"
    KILIX_DIR="$KILIX_DIR" KILIX="$KILIX_DEFAULT" \
        KILIX_REF="$KILIX_REF" KILIX_DESKTOP_PROVIDER="$KILIX_DESKTOP_PROVIDER" \
        KILIX_DESKTOP_COMMAND="$KILIX_DESKTOP_COMMAND" KILIX_DESKTOP_NAME="$KILIX_DESKTOP_NAME" \
        KILIX95_DIR="$KILIX95_DIR" KILIX95_REPO="$KILIX95_REPO" \
        KILIX95_BRANCH="$KILIX95_BRANCH" KILIX95_REF="$KILIX95_REF" \
        PLEB_RESPAWN=0 startx "$_T_ENTRY" -- "$nd" "vt$_T_VT" >"$_T_TESTLOG" 2>&1 &
    local sxpid=$!
    sleep "$([ "$_T_CHECK" = 1 ] && echo "$_T_SECS" || echo 3)"
    if [ "$_T_CHECK" = 1 ]; then
        # a mapped window on this test display == kilix came up (scoped to $nd,
        # so it can't see — or kill — a live kiosk on :0)
        local alive=0
        DISPLAY="$nd" xwininfo -root -tree 2>/dev/null | grep -qi kilix && alive=1
        kill "$sxpid" 2>/dev/null || true
        pkill -f "Xorg.*$nd\b" 2>/dev/null || true    # only this test's X server
        _test_report "$alive"; [ "$alive" = 1 ]
    else
        log "session is up on vt$_T_VT. Switch there (Ctrl+Alt+F$_T_VT) to use it."
        log "to stop: exit kilix, or close the session's X (Ctrl+Alt+Backspace if enabled)"
        wait "$sxpid" 2>/dev/null || true
    fi
}

run_test() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --xephyr)     _T_MODE=xephyr ;;
            --vt)         _T_MODE=vt; _T_VT="$2"; shift ;;
            --vt=*)       _T_MODE=vt; _T_VT="${1#*=}" ;;
            --check)      _T_CHECK=1 ;;
            --secs)       _T_SECS="$2"; shift ;;
            --secs=*)     _T_SECS="${1#*=}" ;;
            --geometry)   _T_GEOM="$2"; shift ;;
            --geometry=*) _T_GEOM="${1#*=}" ;;
            *) warn "ignoring unknown test arg: $1" ;;
        esac
        shift
    done
    _T_ENTRY="$PLEB_BIN_SRC"
    [ -x "$_T_ENTRY" ] || die "missing/!executable: $_T_ENTRY"
    mkdir -p "$(dirname "$_T_TESTLOG")" 2>/dev/null || true
    [ "$_T_MODE" = auto ] && { [ -n "${DISPLAY:-}" ] && _T_MODE=xephyr || _T_MODE=vt; }
    case "$_T_MODE" in
        xephyr) _test_xephyr ;;
        vt)     _test_vt ;;
    esac
}
