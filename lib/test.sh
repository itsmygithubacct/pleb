#!/usr/bin/env bash
# lib/test.sh — exercise the Pleb session in a throwaway X server WITHOUT
# touching the live desktop. Sourced by `pleb`.
#
#   pleb test                 # auto: Xephyr if in a desktop, else spare VT
#   pleb test --xephyr        # nested window on the current $DISPLAY
#   pleb test --vt 9          # real X server on vt9 (view: Ctrl+Alt+F9)
#   pleb test --check         # non-interactive: WM, fullscreen, alt-tab, teardown
#   pleb test --vt 9 --check --secs 6

_T_GEOM=1280x800; _T_VT=9; _T_CHECK=0; _T_SECS=6; _T_MODE=auto
_T_ENTRY=""; _T_TESTLOG="$PLEB_STATE_HOME/test.log"
# --check state: one line per verified contract step, tallied into one verdict.
_T_STEPS=""; _T_PASS=0; _T_FAIL=0
_T_ND=""; _T_XPID=""; _T_SP=""      # this test's nested display, Xephyr, session
_T_PROBE_TITLE="pleb-test-probe"    # the second native client alt-tab needs
_T_KILIX_PAT='kilix|kitty'          # WM_CLASS/WM_NAME of the engine's window

_free_display() {  # echo a free ":N" starting at $1
    local n="$1"; while [ -e "/tmp/.X11-unix/X$n" ]; do n=$((n+1)); done; echo ":$n"
}

_test_step() {     # $1=ok(0/1) $2=what was checked — one line of the contract
    if [ "$1" = 1 ]; then
        _T_STEPS="$_T_STEPS  [ok]  $2"$'\n'; _T_PASS=$((_T_PASS + 1))
    else
        _T_STEPS="$_T_STEPS  [!!]  $2"$'\n'; _T_FAIL=$((_T_FAIL + 1))
    fi
}

# --check needs Xephyr, a window manager, xprop and XTEST to say anything at
# all. Without them the honest answer is "not verified" — never PASS.
_test_missing_tools() {
    local missing="" t
    for t in Xephyr openbox xprop xwininfo xterm pgrep python3; do
        command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
    done
    python3 -c 'from Xlib.ext import xtest' >/dev/null 2>&1 || missing="$missing python3-xlib"
    printf '%s\n' "${missing# }"
}

_test_skip() {     # $1=what is missing
    echo
    warn "SKIP — the nested-X session contract was NOT verified; missing: $1"
    warn "apt install xserver-xephyr openbox x11-utils xterm python3-xlib, then re-run"
}

_test_report() {   # $1=alive(0/1) [$2=verdict text]
    echo
    if [ -n "$_T_STEPS" ]; then echo "  --- session contract ---"; printf '%s' "$_T_STEPS"; fi
    if [ "$1" = 1 ]; then log "PASS — ${2:-kilix is running in the test session}"; else err "FAIL — ${2:-kilix did not come up}"; fi
    echo "  --- session log (tail) ---"
    tail -n 15 "$PLEB_STATE_HOME/session.log" 2>/dev/null | sed 's/^/  /'
    echo "  --- test log (tail) ---"
    tail -n 8 "$_T_TESTLOG" 2>/dev/null | sed 's/^/  /'
}

# --- nested-display probes ---------------------------------------------------
# Every one of these takes the display explicitly and passes it as DISPLAY on
# the query itself: this harness must never be able to read — or act on — the
# live kiosk's :0.

_test_wait() {     # $1=seconds $2...=predicate; 0 as soon as it holds
    local secs="$1"; shift
    local ticks
    case "$secs" in ''|*[!0-9]*) secs=6 ;; esac
    ticks=$((secs * 10))
    while [ "$ticks" -gt 0 ]; do
        if "$@" >/dev/null 2>&1; then return 0; fi
        sleep 0.1
        ticks=$((ticks - 1))
    done
    return 1
}

_test_display_ready() { DISPLAY="$1" xdpyinfo >/dev/null 2>&1; }

# The complete _NET_SUPPORTING_WM_CHECK round trip, verified INDEPENDENTLY of
# the session's own copy of it: root -> child, and the child must point back at
# ITSELF, or a stale property left by a dead WM passes for a live one. Echoes
# the check window. Ids are compared numerically — 0x400003 and 0x0400003 are
# the same window.
_test_wm_window() {  # $1=display
    local nd="$1" root child
    root="$(DISPLAY="$nd" xprop -root -notype _NET_SUPPORTING_WM_CHECK 2>/dev/null)" || return 1
    case "$root" in *'# 0x'*) ;; *) return 1 ;; esac
    root="${root##*# }"
    child="$(DISPLAY="$nd" xprop -id "$root" -notype _NET_SUPPORTING_WM_CHECK 2>/dev/null)" || return 1
    case "$child" in *'# 0x'*) ;; *) return 1 ;; esac
    child="${child##*# }"
    [ "$(printf '%d' "$root" 2>/dev/null)" = "$(printf '%d' "$child" 2>/dev/null)" ] || return 1
    printf '%s\n' "$root"
}

_test_wm_name() {  # $1=display $2=window — echo _NET_WM_NAME
    local out
    out="$(DISPLAY="$1" xprop -id "$2" -notype _NET_WM_NAME 2>/dev/null)" || return 1
    case "$out" in *'"'*) ;; *) return 1 ;; esac
    out="${out#*\"}"
    printf '%s\n' "${out%\"*}"
}

_test_client_list() {  # $1=display — echo one _NET_CLIENT_LIST window per line
    local out
    out="$(DISPLAY="$1" xprop -root -notype _NET_CLIENT_LIST 2>/dev/null)" || return 1
    case "$out" in *'# 0x'*) ;; *) return 1 ;; esac
    printf '%s\n' "${out##*# }" | tr ',' '\n' | tr -d ' '
}

_test_find_client() {  # $1=display $2=WM_CLASS/WM_NAME pattern — echo the window
    local nd="$1" pat="$2" ids id desc
    ids="$(_test_client_list "$nd")" || return 1
    while read -r id; do
        [ -n "$id" ] || continue
        desc="$(DISPLAY="$nd" xprop -id "$id" -notype WM_CLASS WM_NAME 2>/dev/null)" || continue
        if printf '%s' "$desc" | grep -qiE "$pat"; then printf '%s\n' "$id"; return 0; fi
    done <<< "$ids"
    return 1
}

_test_no_client() { ! _test_find_client "$1" "$2" >/dev/null 2>&1; }

_test_has_state() {  # $1=display $2=window $3=_NET_WM_STATE_* atom
    DISPLAY="$1" xprop -id "$2" -notype _NET_WM_STATE 2>/dev/null | grep -qw "$3"
}

_test_active_window() {  # $1=display — echo _NET_ACTIVE_WINDOW as a decimal id
    local out
    out="$(DISPLAY="$1" xprop -root -notype _NET_ACTIVE_WINDOW 2>/dev/null)" || return 1
    case "$out" in *'# 0x'*) ;; *) return 1 ;; esac
    out="${out##*# }"; out="${out%%,*}"
    printf '%d\n' "$out" 2>/dev/null
}

_test_active_changed() {  # $1=display $2=previous id
    local now
    now="$(_test_active_window "$1")" || return 1
    [ -n "$now" ] && [ "$now" != 0 ] && [ "$now" != "$2" ]
}

_test_active_is() { [ "$(_test_active_window "$1" 2>/dev/null)" = "$2" ]; }

# Without a WM there is no _NET_CLIENT_LIST, so ask the X tree — the same
# question the VT check asks, scoped the same way.
_test_root_has_kilix() { DISPLAY="$1" xwininfo -root -tree 2>/dev/null | grep -qi kilix; }

_test_no_kilix() { ! _test_root_has_kilix "$1"; }

_test_kilix_geometry() {  # $1=display — echo the kilix window's "WxH"
    local line geom
    line="$(DISPLAY="$1" xwininfo -root -tree 2>/dev/null | grep -i kilix | head -n 1)" || return 1
    geom="$(printf '%s\n' "$line" | grep -oE '[0-9]+x[0-9]+\+[-0-9]+\+[-0-9]+' | head -n 1)" || return 1
    [ -n "$geom" ] || return 1
    printf '%s\n' "${geom%%+*}"
}

_test_fills_screen() {  # $1=w $2=h $3=screen w $4=screen h
    local n
    for n in "$@"; do case "$n" in ''|*[!0-9]*) return 1 ;; esac; done
    # kitty rounds its window to whole character cells, so require nearly all of
    # the screen rather than an exact match.
    [ "$1" -ge $(( $3 * 9 / 10 )) ] && [ "$2" -ge $(( $4 * 9 / 10 )) ]
}

# "gone" has to include a child that has exited but not been wait()ed for yet:
# kill -0 still succeeds on a zombie.
_test_pid_gone() {  # $1=pid
    [ -n "${1:-}" ] || return 0
    kill -0 "$1" 2>/dev/null || return 0
    grep -qs '^State:[[:space:]]*Z' "/proc/$1/status"
}

# Alt-Tab has to reach Openbox's keyboard grab, so it is injected through XTEST
# (python3-xlib is already a Pleb runtime dependency). A synthetic XSendEvent
# key press is ignored by the grab and would prove nothing.
_test_alt_tab() {  # $1=display [$2=1 for Alt-Shift-Tab, i.e. PreviousWindow]
    DISPLAY="$1" python3 - "${2:-0}" <<'PY'
import sys
import time

from Xlib import X, XK, display
from Xlib.ext import xtest

back = sys.argv[1] == "1"
d = display.Display()


def code(name):
    return d.keysym_to_keycode(XK.string_to_keysym(name))


alt, shift, tab = code("Alt_L"), code("Shift_L"), code("Tab")
seq = [(X.KeyPress, alt)]
if back:
    seq.append((X.KeyPress, shift))
seq += [(X.KeyPress, tab), (X.KeyRelease, tab)]
if back:
    seq.append((X.KeyRelease, shift))
seq.append((X.KeyRelease, alt))
for event, keycode in seq:
    xtest.fake_input(d, event, keycode)
    d.sync()
    time.sleep(0.05)
PY
}

# --- nested session plumbing -------------------------------------------------

_test_xephyr_start() {  # start a throwaway X server; sets _T_ND and _T_XPID
    _T_ND="$(_free_display 7)"
    Xephyr "$_T_ND" -screen "$_T_GEOM" -title "pleb-test $_T_ND — close to exit" -resizeable \
        >>"$_T_TESTLOG" 2>&1 &
    _T_XPID=$!
    if ! _test_wait 10 _test_display_ready "$_T_ND"; then
        kill "$_T_XPID" 2>/dev/null || true
        wait "$_T_XPID" 2>/dev/null || true
        _T_XPID=""
        return 1
    fi
}

# One place that knows how to hand this checkout's engine to pleb-session on a
# nested display; --check launches it twice (managed, then no-WM) and two
# drifting copies of a twelve-variable env block stop testing the same thing.
# PLEB_OPENBOX_CONFIG is pinned to the checkout so the run never depends on
# what happens to be installed under /usr/local. Sets _T_SP.
_test_launch_session() {  # $1=display $2=PLEB_WM value
    DISPLAY="$1" PLEB_WM="$2" PLEB_OPENBOX_CONFIG="$OPENBOX_CONFIG_SRC" \
        KILIX_DIR="$KILIX_DIR" KILIX="$KILIX_DEFAULT" \
        KILIX_REF="$KILIX_REF" KILIX_DESKTOP_PROVIDER="$KILIX_DESKTOP_PROVIDER" \
        KILIX_DESKTOP_COMMAND="$KILIX_DESKTOP_COMMAND" KILIX_DESKTOP_NAME="$KILIX_DESKTOP_NAME" \
        KILIX95_DIR="$KILIX95_DIR" KILIX95_REPO="$KILIX95_REPO" \
        KILIX95_BRANCH="$KILIX95_BRANCH" KILIX95_REF="$KILIX95_REF" \
        PLEB_RESPAWN=0 "$_T_ENTRY" &
    _T_SP=$!
}

# Teardown, scoped to the PIDs this test started. When pleb owns the window
# manager the backgrounded pleb-session PID is the SUPERVISOR, not kitty: its
# TERM trap is what reaps kilix and the Openbox it launched. Nothing here is
# ever matched by name, so a live kiosk on :0 can never be a candidate.
_test_teardown() {  # $1=pleb-session pid $2=Xephyr pid (either may be empty)
    if [ -n "${1:-}" ]; then kill "$1" 2>/dev/null || true; wait "$1" 2>/dev/null || true; fi
    if [ -n "${2:-}" ]; then kill "$2" 2>/dev/null || true; wait "$2" 2>/dev/null || true; fi
}

# The whole session contract on the nested display: WM identity, kilix in
# native fullscreen, a second native client, alt-tab in both directions, and a
# dead window manager ending the session instead of stranding kilix on an
# unmanaged display. PLEB_WM=openbox is the REQUIRED mode on purpose — a
# missing binary or an unreadable profile must fail here, never downgrade
# quietly into the very hidden-window failure this integration exists to fix.
_test_openbox_contract() {  # $1=nested display
    local nd="$1"
    local sp="" wmwin="" name="" kwin="" pwin="" probe="" before="" after="" wmpid=""

    _test_step 1 "clean nested display $nd ($_T_GEOM)"
    if [ -r "$OPENBOX_CONFIG_SRC" ]; then
        _test_step 1 "openbox profile from this checkout: $OPENBOX_CONFIG_SRC"
    else
        _test_step 0 "openbox profile missing from this checkout: $OPENBOX_CONFIG_SRC"
    fi

    _test_launch_session "$nd" openbox
    sp="$_T_SP"
    _test_step 1 "pleb-session started (pid $sp) with PLEB_WM=openbox"

    if _test_wait "$_T_SECS" _test_wm_window "$nd"; then
        wmwin="$(_test_wm_window "$nd")"
        _test_step 1 "_NET_SUPPORTING_WM_CHECK round-trip ok (window $wmwin)"
    else
        _test_step 0 "no valid _NET_SUPPORTING_WM_CHECK on $nd"
        _test_teardown "$sp" ""
        return 1
    fi

    name="$(_test_wm_name "$nd" "$wmwin")" || name=""
    if [ "$name" = Openbox ]; then
        _test_step 1 "_NET_WM_NAME is Openbox"
    else
        _test_step 0 "_NET_WM_NAME is '${name:-unset}', expected Openbox"
    fi

    if _test_wait "$_T_SECS" _test_find_client "$nd" "$_T_KILIX_PAT"; then
        kwin="$(_test_find_client "$nd" "$_T_KILIX_PAT")"
        _test_step 1 "kilix is in _NET_CLIENT_LIST (window $kwin)"
    else
        _test_step 0 "kilix never joined _NET_CLIENT_LIST"
        _test_teardown "$sp" ""
        return 1
    fi

    if _test_wait "$_T_SECS" _test_has_state "$nd" "$kwin" _NET_WM_STATE_FULLSCREEN; then
        _test_step 1 "kilix has _NET_WM_STATE_FULLSCREEN"
    else
        _test_step 0 "kilix never got _NET_WM_STATE_FULLSCREEN"
    fi

    # A second native top-level client — the thing an unmanaged session can
    # neither focus nor raise. It lives and dies on this display only.
    DISPLAY="$nd" xterm -title "$_T_PROBE_TITLE" -geometry 40x10 \
        -e sh -c 'exec sleep 600' >>"$_T_TESTLOG" 2>&1 &
    probe=$!
    _test_step 1 "xterm probe started (pid $probe) on $nd"

    if _test_wait "$_T_SECS" _test_find_client "$nd" "$_T_PROBE_TITLE"; then
        pwin="$(_test_find_client "$nd" "$_T_PROBE_TITLE")"
        _test_step 1 "probe joined _NET_CLIENT_LIST (window $pwin)"
    else
        _test_step 0 "probe never joined _NET_CLIENT_LIST"
    fi

    before="$(_test_active_window "$nd")" || before=""
    if [ -n "$before" ] && [ "$before" != 0 ]; then
        _test_step 1 "_NET_ACTIVE_WINDOW recorded ($before)"
    else
        _test_step 0 "_NET_ACTIVE_WINDOW is unset — nothing holds focus"
        before=""
    fi

    if [ -n "$before" ] && _test_alt_tab "$nd" \
        && _test_wait "$_T_SECS" _test_active_changed "$nd" "$before"; then
        after="$(_test_active_window "$nd")" || after=""
        _test_step 1 "alt-tab moved _NET_ACTIVE_WINDOW $before -> $after"
    else
        _test_step 0 "alt-tab did not move _NET_ACTIVE_WINDOW (still ${before:-unset})"
    fi
    if [ -n "$before" ] && _test_alt_tab "$nd" 1 \
        && _test_wait "$_T_SECS" _test_active_is "$nd" "$before"; then
        _test_step 1 "alt-shift-tab moved _NET_ACTIVE_WINDOW back to $before"
    else
        _test_step 0 "alt-shift-tab did not restore _NET_ACTIVE_WINDOW ${before:-unset}"
    fi

    kill "$probe" 2>/dev/null || true
    wait "$probe" 2>/dev/null || true
    if _test_wait "$_T_SECS" _test_no_client "$nd" "$_T_PROBE_TITLE"; then
        _test_step 1 "probe terminated and left _NET_CLIENT_LIST"
    else
        _test_step 0 "probe is still in _NET_CLIENT_LIST after terminating it"
    fi

    # A window manager that dies must take the session with it. The openbox to
    # kill is found as a CHILD of this test's own pleb-session, never by name.
    wmpid="$(pgrep -P "$sp" -x openbox 2>/dev/null | head -n 1)" || wmpid=""
    if [ -z "$wmpid" ]; then
        _test_step 0 "no pleb-owned openbox below pleb-session ($sp)"
    else
        kill "$wmpid" 2>/dev/null || true
        if _test_wait "$_T_SECS" _test_pid_gone "$sp"; then
            _test_step 1 "killing openbox ($wmpid) ended the supervised session"
        else
            _test_step 0 "killing openbox ($wmpid) left pleb-session ($sp) running"
        fi
        if _test_wait "$_T_SECS" _test_no_kilix "$nd"; then
            _test_step 1 "kilix did not outlive the window manager"
        else
            _test_step 0 "kilix is still mapped on $nd after openbox died"
        fi
    fi

    _test_teardown "$sp" ""
}

# The recovery path every WM mode falls back to: PLEB_WM=none runs no window
# manager at all, so kilix gets the fixed screen-fill geometry instead of
# native EWMH fullscreen. It needs its own Xephyr — the managed display still
# carries Openbox's root properties.
_test_no_wm_recovery() {
    local nd xpid sp geom sw sh

    if ! _test_xephyr_start; then
        _test_step 0 "PLEB_WM=none: nested display did not start"
        return 1
    fi
    nd="$_T_ND"; xpid="$_T_XPID"
    _test_step 1 "PLEB_WM=none: clean nested display $nd ($_T_GEOM)"

    _test_launch_session "$nd" none
    sp="$_T_SP"

    if _test_wait "$_T_SECS" _test_root_has_kilix "$nd"; then
        _test_step 1 "PLEB_WM=none: kilix is mapped on $nd"
    else
        _test_step 0 "PLEB_WM=none: kilix never came up on $nd"
        _test_teardown "$sp" "$xpid"
        return 1
    fi

    if _test_wm_window "$nd" >/dev/null 2>&1; then
        _test_step 0 "PLEB_WM=none: something is managing $nd"
    else
        _test_step 1 "PLEB_WM=none: no window manager owns $nd"
    fi

    geom="$(_test_kilix_geometry "$nd")" || geom=""
    sw="${_T_GEOM%%x*}"; sh="${_T_GEOM#*x}"; sh="${sh%%x*}"
    if _test_fills_screen "${geom%%x*}" "${geom#*x}" "$sw" "$sh"; then
        _test_step 1 "PLEB_WM=none: kilix fills the screen ($geom of ${sw}x${sh})"
    else
        _test_step 0 "PLEB_WM=none: kilix geometry ${geom:-unknown} does not fill ${sw}x${sh}"
    fi

    _test_teardown "$sp" "$xpid"
}

_test_xephyr() {
    local missing
    if [ "$_T_CHECK" = 1 ]; then
        missing="$(_test_missing_tools)"
        if [ -n "$missing" ]; then _test_skip "$missing"; return 0; fi
    fi
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
        # The managed contract first, then the no-WM recovery path on a display
        # of its own. Neither leaves anything behind: with a pleb-owned WM the
        # backgrounded PID is the SUPERVISOR, not kitty, so teardown goes
        # through its TERM trap, which reaps kilix and openbox for us.
        local total
        _test_openbox_contract "$nd" || true
        kill "$xpid" 2>/dev/null || true
        wait "$xpid" 2>/dev/null || true
        _test_no_wm_recovery || true
        total=$((_T_PASS + _T_FAIL))
        if [ "$_T_FAIL" = 0 ]; then
            _test_report 1 "kilix is running in the test session; $total/$total contract steps verified"
        else
            _test_report 0 "$_T_FAIL of $total session-contract steps failed"
        fi
        [ "$_T_FAIL" = 0 ]
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
    ensure_pleb_private_storage
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
