"""The suite's one environment sanitiser.

One definition, deliberately. This sanitiser used to exist as four
hand-maintained copies across the test modules. When the ``BASH_FUNC_*`` gap
was fixed, it was fixed in one of them, and the other three stayed subvertible
-- the fix looked complete because the copy in front of the author was correct.

Four copies agreeing is a promise; one definition is a check. Extras that
genuinely differ per module are passed in as ``overrides`` rather than
recreating the sanitising core.
"""

import os
from pathlib import Path

#: This project's own configuration. A developer's real session must never be
#: what a test reads its configuration from.
PROJECT_PREFIXES = ("GPU_TERMINAL", "KILIX", "PLEB")

#: ``BASH_FUNC_*`` is bash's carrier for *exported shell functions*: every
#: child shell inherits them and ``type -t`` reports them as functions. Left in
#: place, an operator who happens to carry ``export -f tb`` changes what the
#: probes under test can see, so the suite passes or fails on ambient state it
#: never declared -- which RELEASING.md counts as a release failure in itself.
AMBIENT_PREFIXES = ("BASH_FUNC_",)

STRIPPED_PREFIXES = PROJECT_PREFIXES + AMBIENT_PREFIXES


def clean_env(home: Path, **overrides: str) -> dict[str, str]:
    """A child-process environment that depends on *home*, not the operator."""
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(STRIPPED_PREFIXES):
            env.pop(key)
    # Set after stripping: these names start with a stripped prefix themselves.
    env["HOME"] = str(home)
    env["PLEB_ENV_SYSTEM"] = str(home / "missing-system.env")
    env["PLEB_ENV_USER"] = str(home / "missing-user.env")
    env.update(overrides)
    return env


def world_writable_ancestor(path: Path) -> "Path | None":
    """First ancestor of *path* (inclusive) that is group/world-writable.

    Preflights that walk ancestry refuse at the first such component, so a test
    asserting a *leaf* rule only reaches that rule when every ancestor above it
    is already clean. A test whose own ``$HOME`` sits below one is testing a
    precondition it did not mean to test.
    """
    import stat as _stat

    for candidate in [path, *path.parents]:
        try:
            mode = candidate.stat().st_mode
        except OSError:
            continue
        if mode & (_stat.S_IWGRP | _stat.S_IWOTH):
            return candidate
    return None
