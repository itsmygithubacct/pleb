"""Kill real portal/X11 guard mutations in disposable copies, never shared trees."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ('backend-authentication', 'global_shortcuts.py',
     'if sender and sender == self.bus.get_name_owner(FRONTEND):', 'if sender:',
     'direct-backend-bypass-refused', None),
    ('physical-consent', 'global_shortcuts.py',
     'session.edit(items, request)',
     "session.apply(items)\n            request.finish(0, {'shortcuts': shortcut_array(session.shortcuts)})\n            session.show_indicator()",
     'no-events-before-consent', None),
    ('backend-signal-destination', 'global_shortcuts.py',
     'message.set_destination(destination)', 'pass  # Deliberate broadcast mutation',
     'backend-events-are-not-broadcast-to-other-desktop-clients', None),
    ('cross-session-grab-conflict', 'shortcut_keys.py',
     'if pair in desired or (pair in self.grabs and self.grabs[pair][0] is not owner):', 'if pair in desired:',
     'conflicting-grant-is-refused-without-replacing-first-client', None),
    ('other-native-client-grab-conflict', 'shortcut_keys.py',
     'if failures:', 'if False:',
     'other-native-client-grab-produces-real-badaccess', None),
    ('session-grab-release', 'global_shortcuts.py',
     'self.portal.keys.remove(self)', 'pass  # Deliberate retained-grab mutation',
     'session-close-releases-native-grabs', None),
    ('lock-modifier-variants', 'shortcut_keys.py',
     'ignored = self.locks & ~mask', 'ignored = 0',
     'Timed out', 'modifier-release-first-deactivates-without-stuck-state'),
    ('active-keyboard-grab-release', 'shortcut_keys.py',
     'self.display.xinput_ungrab_device(self.keyboard, X.CurrentTime)', 'pass  # Deliberate keyboard-wide grab mutation',
     'Timed out', 'public-create-bind-and-physical-consent'),
    ('keyboard-map-rebinding', 'shortcut_keys.py',
     'self.remapped()', 'pass  # Deliberate lost-rebinding mutation',
     'Timed out', 'modifier-release-first-deactivates-without-stuck-state'),
    ('desktop-key-reservation', 'shortcut_keys.py',
     'if sym == self.symbol(reserved_key) and (mask & ~self.locks) == reserved_mask:', 'if False:',
     'desktop-lock-chord-reserved-even-with-wm-absent', None),
    ('backend-restart-session-record', 'global_shortcuts.py',
     'for path, frontend in previous:', 'for path, frontend in []:',
     'Timed out', 'backend-hard-death-releases-server-grabs'),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--relay', required=True)
    parser.add_argument('--jobs', type=int, default=2, choices=(1, 2))
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    hashes = {name: hashlib.sha256((ROOT / 'lib' / name).read_bytes()).hexdigest()
              for name in ('global_shortcuts.py', 'shortcut_keys.py')}

    def run(case):
        name, module, before, after, expected, last = case
        directory = destination / name
        source = directory / 'source'
        shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        if module:
            path = source / 'lib' / module
            text = path.read_text()
            expected_count = 2 if name == 'backend-restart-session-record' else 1
            assert text.count(before) == expected_count, (name, text.count(before))
            path.write_text(text.replace(before, after))
            (directory / 'mutation.json').write_text(json.dumps(dict(module=module, before=before, after=after), indent=2))
        environment = {'PATH': '/usr/bin:/bin', 'HOME': str(directory), 'LANG': 'C.UTF-8', 'PYTHONNOUSERSITE': '1'}
        log = directory / 'run.log'
        with log.open('w') as stream:
            result = subprocess.run(['/usr/bin/python3', str(source / 'tests/fixtures/global_shortcuts_fixture.py'),
                                     '--output', str(directory / 'runtime'), '--relay', str(Path(args.relay).resolve())],
                                    env=environment, stdout=stream, stderr=subprocess.STDOUT)
        content = log.read_text()
        records = [json.loads(line) for line in content.splitlines() if line.startswith('{')]
        checks = [item['check'] for item in records if item.get('passed')]
        cleanup = any(item.get('cleanup') for item in records)
        if module:
            passed = result.returncode != 0 and expected in content and cleanup and (last is None or checks[-1:] == [last])
        else:
            passed = result.returncode == 0 and cleanup
        record = dict(name=name, passed=passed, exit_code=result.returncode, expected_failure=expected,
                      last_passed=checks[-1:] or [], passed_checks=len(checks), cleanup=cleanup, log=str(log), source=str(source))
        print(json.dumps(record), flush=True)
        return record

    # A successful unchanged control binds every negative result to the same
    # real frontend, relay, native input and UI environment as its mutant.
    control = run(('control', None, None, None, None, None))
    if not control['passed']:
        raise SystemExit('The unmutated native integration control failed')
    results = [control]
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = [executor.submit(run, mutation) for mutation in MUTATIONS]
        for future in as_completed(futures):
            results.append(future.result())
    summary = dict(source=str(ROOT), source_hashes=hashes, results=results,
                   killed=sum(item['passed'] for item in results[1:]), total=len(MUTATIONS))
    (destination / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    if not all(item['passed'] for item in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
