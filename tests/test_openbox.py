"""Static guarantees for the Pleb-managed Openbox integration.

These are the invariants that have no single owner in the source and so rot
silently: the profile's reduced surface, the two copies of the trusted
session-variable list, and the two copies of the profile's default path.
"""
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RC_XML = ROOT / "share" / "openbox" / "rc.xml"
SESSION = ROOT / "bin" / "pleb-session"
COMMON = ROOT / "lib" / "common.sh"
NS = {"o": "http://openbox.org/3.4/rc"}

INSTALLED_PROFILE = "/usr/local/share/pleb/openbox/rc.xml"


def _var_list(text, assignment):
    """Extract a space-separated shell variable list by its assignment name."""
    m = re.search(rf'^\s*{assignment}="([^"]+)"', text, re.M)
    assert m, f"could not find {assignment}= list"
    return m.group(1).split()


class OpenboxProfileTests(unittest.TestCase):
    def setUp(self):
        self.tree = ET.parse(RC_XML)

    def test_profile_is_well_formed_xml(self):
        # A stray "--" inside an XML comment makes Openbox reject the file and
        # the session fall back to the hidden-window behaviour this replaces.
        self.assertTrue(self.tree.getroot().tag.endswith("openbox_config"))

    def test_single_desktop(self):
        self.assertEqual(
            [e.text for e in self.tree.findall(".//o:desktops/o:number", NS)], ["1"]
        )

    def test_focus_and_placement_policy(self):
        self.assertEqual(self.tree.findtext(".//o:focus/o:focusNew", None, NS), "yes")
        # click-to-focus, not focus-follows-mouse
        self.assertEqual(self.tree.findtext(".//o:focus/o:followMouse", None, NS), "no")
        self.assertEqual(self.tree.findtext(".//o:placement/o:policy", None, NS), "Smart")
        self.assertEqual(self.tree.findtext(".//o:placement/o:monitor", None, NS), "Primary")

    def test_packaged_theme(self):
        self.assertEqual(self.tree.findtext(".//o:theme/o:name", None, NS), "Clearlooks")

    def test_alt_tab_cycles_windows_in_both_directions(self):
        for key, action in (("A-Tab", "NextWindow"), ("A-S-Tab", "PreviousWindow")):
            bind = self.tree.find(f".//o:keybind[@key='{key}']", NS)
            self.assertIsNotNone(bind, f"missing {key} binding")
            self.assertIsNotNone(
                bind.find(f"./o:action[@name='{action}']", NS), f"{key} must run {action}"
            )
            finals = {
                a.get("name")
                for a in bind.findall(f".//o:finalactions/o:action", NS)
            }
            self.assertEqual(finals, {"Focus", "Raise", "Unshade"}, key)

    def test_clicking_a_client_focuses_and_raises_it(self):
        ctx = self.tree.find(".//o:context[@name='Client']", NS)
        self.assertIsNotNone(ctx)
        names = {
            a.get("name")
            for mb in ctx.findall("./o:mousebind[@action='Press']", NS)
            for a in mb.findall("./o:action", NS)
        }
        self.assertEqual(names, {"Focus", "Raise"})

    def test_no_root_menu(self):
        # A kiosk desktop must not sprout a right-click application menu.
        self.assertIsNone(self.tree.find(".//o:context[@name='Root']", NS))
        menus = [e.text for e in self.tree.findall(".//o:menu", NS) if e.text]
        self.assertNotIn("root-menu", menus)

    def test_no_desktop_switching_or_launchers_or_screenshots(self):
        actions = {a.get("name") for a in self.tree.findall(".//o:action", NS)}
        for forbidden in ("GoToDesktop", "SendToDesktop", "Execute",
                          "ToggleShowDesktop", "DirectionalCycleWindows"):
            self.assertNotIn(forbidden, actions, f"{forbidden} must not be bound")

    def test_no_application_rules_and_kilix_is_not_pinned_above(self):
        # Pinning Kilix to the 'above' layer would recreate the exact bug this
        # integration fixes: a focused browser permanently behind the terminal.
        self.assertEqual(self.tree.findall(".//o:applications/o:application", NS), [])
        # parsed elements only — the header comment discusses the 'above' layer
        # precisely to explain why it is not used
        layers = [e.text for e in self.tree.findall(".//o:layer", NS)]
        self.assertNotIn("above", layers)

    def test_retains_upstream_licence_attribution(self):
        text = RC_XML.read_text()
        self.assertIn("Openbox", text)
        self.assertIn("GNU General Public License", text)


class OpenboxWiringTests(unittest.TestCase):
    def test_session_variable_lists_stay_in_sync(self):
        # bin/pleb-session's list must remain a strict, order-preserving subset
        # of lib/common.sh's, or the CLI and the session disagree about whether
        # an explicit environment value beats /etc/pleb/session.env.
        session = _var_list(SESSION.read_text(), "_pleb_vars")
        common = _var_list(COMMON.read_text(), "vars")
        missing = [v for v in session if v not in common]
        self.assertEqual(missing, [], "in pleb-session but not common.sh")
        positions = [common.index(v) for v in session]
        self.assertEqual(positions, sorted(positions), "relative order diverged")

    def test_new_knobs_are_trusted_in_both_lists(self):
        session = _var_list(SESSION.read_text(), "_pleb_vars")
        common = _var_list(COMMON.read_text(), "vars")
        for name in ("PLEB_WM", "PLEB_OPENBOX_CONFIG", "PLEB_WM_TIMEOUT",
                     "KILIX_RUN_ALIASES"):
            self.assertIn(name, session, f"{name} missing from _pleb_vars")
            self.assertIn(name, common, f"{name} missing from common.sh vars")

    def test_profile_default_path_matches_install_destination(self):
        # The launcher is self-contained and cannot read common.sh, so these two
        # literals are a sync pair with nothing else enforcing them.
        session = SESSION.read_text()
        common = COMMON.read_text()
        self.assertIn(f'PLEB_OPENBOX_CONFIG="{INSTALLED_PROFILE}"', session)
        self.assertIn(f'OPENBOX_CONFIG_DST="${{OPENBOX_CONFIG_DST:-{INSTALLED_PROFILE}}}"',
                      common)

    def test_session_starts_bare_openbox_with_the_managed_profile(self):
        text = SESSION.read_text()
        self.assertIn('PLEB_WM_ARGV=(openbox --config-file "$PLEB_OPENBOX_CONFIG" --sm-disable)',
                      text)
        # openbox-session would run the user's or the system's autostart,
        # dragging in panels and XDG autostart entries; --replace would stomp an
        # existing desktop's window manager instead of adopting it. Both are
        # named in the surrounding comments, so only check executable lines.
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn("openbox-session", code)
        self.assertNotIn("--replace", code)

    def test_wm_detection_validates_the_full_ewmh_handshake(self):
        text = SESSION.read_text()
        # A bare "the root property exists" test accepts a stale property left
        # by a WM that died. The child must point back at itself.
        self.assertIn("_pleb_wm_window()", text)
        self.assertNotIn("grep -q '0x'", text)
        self.assertIn('xprop -id "$root" -notype _NET_SUPPORTING_WM_CHECK', text)

    def test_readiness_watches_the_wm_process_each_iteration(self):
        text = SESSION.read_text()
        self.assertIn('kill -0 "$PLEB_WM_PID"', text)
        self.assertIn("PLEB_WM_TIMEOUT", text)

    def test_cleanup_only_kills_a_wm_this_instance_started(self):
        text = SESSION.read_text()
        self.assertIn('[ "$PLEB_WM_OWNED" = 1 ]', text)
        for sig in ("EXIT", "HUP", "INT", "TERM"):
            self.assertRegex(text, rf"trap .*{sig}")

    def test_fullscreen_strategy_is_decided_after_wm_readiness(self):
        text = SESSION.read_text()
        wm_block = text.index("# --- window manager ---")
        strategy = text.index("# --- fullscreen strategy ---")
        self.assertLess(wm_block, strategy,
                        "the WM must be ready before the fullscreen strategy is chosen")


if __name__ == "__main__":
    unittest.main()
