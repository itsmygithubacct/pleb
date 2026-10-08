"""Approved X11 chords on the physical display, without a global key listener."""
import ctypes
from dataclasses import dataclass
import itertools
import re
import time

from Xlib import X, display, error
from gi.repository import GLib


class ShortcutError(ValueError):
    pass


@dataclass(frozen=True)
class Chord:
    trigger: str
    keycode: int
    modifiers: int
    masks: tuple


class ShortcutKeys:
    def __init__(self, activated, deactivated, remapped, failed, display_name=None):
        self.activated, self.deactivated = activated, deactivated
        self.remapped, self.failed = remapped, failed
        self.display = display.Display(display_name)
        self.root = self.display.screen().root
        self.grabs, self.active = {}, {}
        self.closed = False
        self.keysyms = ctypes.CDLL('libxkbcommon.so.0')
        self.keysyms.xkb_keysym_from_name.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.keysyms.xkb_keysym_from_name.restype = ctypes.c_uint32
        self.refresh()
        self.watch = GLib.io_add_watch(self.display.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.events)
        self.timer = GLib.timeout_add(40, self.releases)

    def symbol(self, name):
        return self.keysyms.xkb_keysym_from_name(name.encode('ascii'), 0)

    def refresh(self):
        info = self.display.display.info
        self.mapping = self.display.get_keyboard_mapping(info.min_keycode, info.max_keycode - info.min_keycode + 1)
        self.first = info.min_keycode
        self.modifier_keys = self.display.get_modifier_mapping()
        self.modifiers = {}
        self.locks = 0
        for name, symbols in (('CTRL', ('Control_L', 'Control_R')), ('SHIFT', ('Shift_L', 'Shift_R')),
                              ('ALT', ('Alt_L', 'Alt_R')), ('LOGO', ('Super_L', 'Super_R')),
                              ('NUM', ('Num_Lock',)), ('CAPS', ('Caps_Lock', 'Shift_Lock')),
                              ('SCROLL', ('Scroll_Lock',))):
            wanted = {self.symbol(item) for item in symbols}
            slots = [i for i, codes in enumerate(self.modifier_keys)
                     if any(code and any(sym in wanted for sym in self.mapping[code - self.first]) for code in codes)]
            if len(slots) == 1:
                self.modifiers[name] = 1 << slots[0]
        self.locks = self.modifiers.get('CAPS', 0) | self.modifiers.get('NUM', 0) | self.modifiers.get('SCROLL', 0)

    def chord(self, trigger):
        if not isinstance(trigger, str) or len(trigger) > 64 or not re.fullmatch(r'[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*', trigger):
            raise ShortcutError('Use an XDG chord such as CTRL+SHIFT+F8, or leave it empty to disable it.')
        parts = trigger.split('+')
        modifiers, key = parts[:-1], parts[-1]
        if len(set(modifiers)) != len(modifiers) or any(name not in ('CTRL', 'ALT', 'SHIFT', 'NUM', 'LOGO') for name in modifiers):
            raise ShortcutError('Unsupported or repeated modifier.')
        if not set(modifiers) & {'CTRL', 'ALT', 'LOGO'}:
            raise ShortcutError('A global shortcut requires CTRL, ALT or LOGO.')
        if any(name not in self.modifiers for name in modifiers):
            raise ShortcutError('That modifier is unavailable or ambiguous in this keyboard mapping.')
        for name in modifiers:
            if any(other != name and value == self.modifiers[name] for other, value in self.modifiers.items()):
                raise ShortcutError('That modifier shares an ambiguous modifier slot.')
        # Distinct XKB groups require XKB group-aware grabs, which core XGrabKey
        # cannot express. Fail closed instead of binding a different character.
        if any(len(row) > 2 and row[2] and row[2] != row[0] for row in self.mapping):
            raise ShortcutError('Use one XKB layout group for global shortcuts.')
        sym = self.symbol(key)
        codes = [self.first + i for i, row in enumerate(self.mapping) if sym and row[0] == sym]
        if len(codes) != 1:
            raise ShortcutError('The key must occur once in the base layer of this keyboard layout.')
        mask = 0
        for name in modifiers:
            mask |= self.modifiers[name]
        # Protect the shipped session bindings even before the WM has grabbed
        # them; BadAccess below also protects other clients' existing grabs.
        reserved = (({'LOGO'}, 'l'), ({'CTRL', 'ALT'}, 'l'), ({'ALT'}, 'Tab'),
                    ({'ALT', 'SHIFT'}, 'Tab'), ({'ALT'}, 'F4'))
        reserved += tuple(({'CTRL', 'ALT'}, 'F' + str(number)) for number in range(1, 13))
        reserved += (({'CTRL', 'ALT'}, 'BackSpace'),)
        for names, reserved_key in reserved:
            reserved_mask = 0
            for name in names:
                reserved_mask |= self.modifiers.get(name, 0)
            if sym == self.symbol(reserved_key) and (mask & ~self.locks) == reserved_mask:
                raise ShortcutError('That shortcut is reserved by the desktop.')
        ignored = self.locks & ~mask
        bits = [1 << i for i in range(8) if ignored & (1 << i)]
        masks = tuple(sorted({mask | sum(combo) for size in range(len(bits) + 1)
                              for combo in itertools.combinations(bits, size)}))
        canonical = '+'.join([name for name in ('CTRL', 'ALT', 'SHIFT', 'NUM', 'LOGO') if name in modifiers] + [key])
        return Chord(canonical, codes[0], mask, masks)

    def replace(self, owner, bindings):
        """Atomically acquire new grabs, retaining the old set on any conflict."""
        desired = {}
        for ident, chord in bindings.items():
            for mask in chord.masks:
                pair = (chord.keycode, mask)
                if pair in desired or (pair in self.grabs and self.grabs[pair][0] is not owner):
                    raise ShortcutError('The shortcut conflicts with another granted shortcut.')
                desired[pair] = (owner, ident, chord)
        added = []
        try:
            for pair in desired:
                if pair in self.grabs:
                    continue
                failures = []
                self.root.grab_key(pair[0], pair[1], False, X.GrabModeAsync, X.GrabModeAsync,
                                   onerror=lambda err, _request: failures.append(err))
                self.display.sync()
                if failures:
                    raise ShortcutError('The shortcut is already used by the desktop or another application.')
                added.append(pair)
        except Exception:
            for code, mask in added:
                self.root.ungrab_key(code, mask)
            self.display.sync()
            raise
        self.end_active(owner)
        for pair, value in list(self.grabs.items()):
            if value[0] is owner and pair not in desired:
                self.root.ungrab_key(*pair)
                del self.grabs[pair]
        self.grabs.update(desired)
        self.display.flush()

    def remove(self, owner):
        try:
            self.replace(owner, {})
        except (error.XError, error.ConnectionClosedError, OSError):
            # A dead X server has already released its grabs. Session and
            # D-Bus request cleanup must still run to completion.
            self.end_active(owner)
            self.grabs = {pair: value for pair, value in self.grabs.items() if value[0] is not owner}

    def end_active(self, owner=None):
        for key, (session, ident, _chord) in list(self.active.items()):
            if owner is None or owner is session:
                del self.active[key]
                self.deactivated(session, ident, self.timestamp())

    @staticmethod
    def timestamp():
        return time.monotonic_ns() // 1000000

    def events(self, _fd, condition):
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            self.failed()
            return False
        try:
            for _ in range(128):
                if not self.display.pending_events():
                    break
                event = self.display.next_event()
                if event.type == X.MappingNotify:
                    self.display.refresh_keyboard_mapping(event)
                    self.end_active()
                    for code, mask in self.grabs:
                        self.root.ungrab_key(code, mask)
                    self.grabs.clear()
                    self.refresh()
                    self.remapped()
                elif event.type == X.KeyPress:
                    # A passive grab becomes a keyboard-wide active grab until
                    # release. End it immediately so intervening typing reaches
                    # the focused window, never this service.
                    self.display.ungrab_keyboard(X.CurrentTime)
                    self.display.flush()
                    value = self.grabs.get((event.detail, event.state & 255))
                    if value is not None:
                        session, ident, chord = value
                        key = (session, ident)
                        if key not in self.active:
                            self.active[key] = value
                            self.activated(session, ident, self.timestamp())
            # Reply reads may queue MappingNotify; the release timer drains it.
        except (error.XError, error.ConnectionClosedError, OSError):
            self.failed()
            return False
        return not self.closed

    def releases(self):
        if self.closed:
            return False
        try:
            if self.display.pending_events():
                self.events(self.display.fileno(), GLib.IO_IN)
            if self.active:
                # Query only while an approved chord is held. Inspect only its
                # trigger and required modifiers, never emit or retain typing.
                bitmap = self.display.query_keymap()
                def down(code):
                    return bool(bitmap[code // 8] & (1 << (code % 8)))
                for key, (session, ident, chord) in list(self.active.items()):
                    held = down(chord.keycode)
                    for i, codes in enumerate(self.modifier_keys):
                        if chord.modifiers & (1 << i) and not self.locks & (1 << i):
                            held &= any(down(code) for code in codes if code)
                    if not held:
                        del self.active[key]
                        self.deactivated(session, ident, self.timestamp())
        except (error.XError, error.ConnectionClosedError, OSError):
            self.failed()
            return False
        return not self.closed

    def close(self):
        if self.closed:
            return
        self.closed = True
        GLib.source_remove(self.watch)
        GLib.source_remove(self.timer)
        self.end_active()
        try:
            self.display.close()  # X server releases every passive/active grab.
        except (error.XError, error.ConnectionClosedError, OSError):
            pass
