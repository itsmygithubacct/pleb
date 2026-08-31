#!/usr/bin/env python3
"""Kill the Pleb-side F109 release-hop and preservation guard mutants."""

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = "tests.test_release_hop.ReleaseHopTests."
BEHAVIOR = "tests.test_behavior.PlebBehaviorTests."
PRESERVE = "tests.test_preserve.PreservationTests."


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]
    occurrences: int = 1


MUTATIONS = (
    Mutation(
        "adjacent-hop refusal",
        "lib/closure.sh",
        '    if [ "$target" != "$next" ] && ! _pleb_policy_allows_skip "$current" "$target"; then',
        "    if false; then # mutation: unsupported skip accepted",
        (RELEASE + "test_latest_refuses_a_skip_and_names_the_adjacent_hop",),
    ),
    Mutation(
        "all release-controlled environment overrides",
        "lib/closure.sh",
        '        [ "$(_pleb_value_origin "$key")" != "the environment" ] \\\n            || die "cannot select a release while $key is overridden by the process environment"',
        "        : # mutation: release override accepted",
        (RELEASE + "test_every_release_controlled_environment_override_is_refused",),
    ),
    Mutation(
        "failed-apply closure compensation",
        "lib/closure.sh",
        '    if ! _pleb_selector_env "$_PLEB_HOP_SELECTOR" --rollback; then',
        "    if false; then # mutation: closure rollback skipped",
        (RELEASE + "test_failed_stack_apply_restores_previous_generation",),
    ),
    Mutation(
        "interrupted-hop closure compensation",
        "lib/closure.sh",
        '    _pleb_selector_env "$_PLEB_HOP_SELECTOR" --rollback || rc=$?',
        "    : # mutation: interrupted closure rollback skipped",
        (RELEASE + "test_pending_selected_phase_is_recovered_before_new_work",),
    ),
    Mutation(
        "durable selecting-phase record",
        "lib/closure.sh",
        "    _pleb_record_active_selector selecting",
        "    : # mutation: no pre-selection state record",
        (RELEASE + "test_selected_release_is_applied_and_one_generation_rollback_runs",),
    ),
    Mutation(
        "CLI split-closure reader",
        "lib/common.sh",
        '    for cfg in "$PLEB_ENV_SYSTEM" "$PLEB_ENV_USER" \\\n            "$PLEB_CLOSURE_SYSTEM" "$PLEB_CLOSURE_USER"; do',
        '    for cfg in "$PLEB_ENV_SYSTEM" "$PLEB_ENV_USER"; do # mutation',
        (BEHAVIOR + "test_cli_and_session_resolve_the_same_split_closure",),
    ),
    Mutation(
        "session split-closure reader",
        "bin/pleb-session",
        'for _pleb_cfg in "$PLEB_ENV_SYSTEM" "$PLEB_ENV_USER" \\\n        "$PLEB_CLOSURE_SYSTEM" "$PLEB_CLOSURE_USER"; do',
        'for _pleb_cfg in "$PLEB_ENV_SYSTEM" "$PLEB_ENV_USER"; do # mutation',
        (BEHAVIOR + "test_cli_and_session_resolve_the_same_split_closure",),
    ),
    Mutation(
        "preservation size ceiling",
        "lib/preserve.sh",
        "    if total > max_bytes:",
        "    if False:  # mutation: size ceiling disabled",
        (PRESERVE + "test_size_ceiling_refuses_before_checkout_mutation",),
    ),
    Mutation(
        "preservation free-space reserve",
        "lib/preserve.sh",
        "    if free < required_free:",
        "    if False:  # mutation: free-space reserve disabled",
        (PRESERVE + "test_free_space_reserve_refuses_before_checkout_mutation",),
    ),
    Mutation(
        "managed-source rule zero",
        "lib/update.sh",
        "    _require_managed_source_layout",
        "    : # mutation: managed source layout not checked",
        (BEHAVIOR + "test_update_refuses_a_checkout_reached_through_a_symlink",),
        occurrences=4,
    ),
)


def run_tests(root: Path, tests: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["TMPDIR"] = "/home/pleb/scratch-workers"
    return subprocess.run(
        [sys.executable, "-m", "unittest", *tests],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    unique_tests = tuple(dict.fromkeys(test for item in MUTATIONS for test in item.tests))
    baseline = run_tests(ROOT, unique_tests)
    if baseline.returncode != 0:
        sys.stderr.write("baseline controls failed; mutation result is invalid\n")
        sys.stderr.write(baseline.stdout + baseline.stderr)
        return 2

    killed = 0
    scratch = os.environ.get("TMPDIR", "/home/pleb/scratch-workers")
    for index, mutation in enumerate(MUTATIONS, 1):
        with tempfile.TemporaryDirectory(dir=scratch) as td:
            mutant = Path(td) / "pleb"
            shutil.copytree(
                ROOT,
                mutant,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )
            path = mutant / mutation.path
            source = path.read_text()
            observed = source.count(mutation.old)
            if observed != mutation.occurrences:
                print(
                    f"[INVALID {index}/{len(MUTATIONS)}] {mutation.name}: "
                    f"expected {mutation.occurrences} mutation sites, found {observed}",
                    file=sys.stderr,
                )
                return 2
            path.write_text(source.replace(mutation.old, mutation.new))
            result = run_tests(mutant, mutation.tests)
            if result.returncode == 0:
                print(
                    f"[SURVIVED {index}/{len(MUTATIONS)}] {mutation.name}",
                    file=sys.stderr,
                )
                return 1
            killed += 1
            print(f"[KILLED {index}/{len(MUTATIONS)}] {mutation.name}")
    print(f"F109 Pleb mutations killed: {killed}/{len(MUTATIONS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
