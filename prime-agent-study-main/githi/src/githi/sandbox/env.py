"""Building the child process environment.

Credential removal is the enforcement mechanism behind `allow_network` for
shell commands. Blocking `curl` by name would be theatre -- `python3 -c
"import urllib..."` defeats it in one line, and every blocklist is one
unlisted binary away from being bypassed. Removing the credentials instead
makes network access *possible and useless*: a command can still reach an
API, but with nothing to authenticate as it cannot read anything private or
write anything at all.
"""

from __future__ import annotations

import os

from .policy import SandboxPolicy

_ALWAYS_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TMPDIR", "LANG")


def build_child_env(policy: SandboxPolicy) -> dict[str, str]:
    """The parent environment minus anything that looks like a credential."""
    env = {k: v for k, v in os.environ.items() if not policy.is_secret(k)}

    # Re-add the handful a shell genuinely needs, in case a pattern caught one
    # by accident, and pin the locale: `sort`/`uniq` order is locale-dependent,
    # so leaving it unset makes tool output differ between machines for no
    # reason the model could ever understand.
    for key in _ALWAYS_KEEP:
        value = os.environ.get(key)
        if value and key not in env:
            env[key] = value
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("HOME", "/tmp")
    env["LC_ALL"] = "C.UTF-8"

    # Defence in depth against the same secret being exported under a second,
    # innocuous name (`MY_COPY=$GITHUB_TOKEN`). Scrubbing keys alone would
    # leave that copy in place, so drop any variable whose *value* matches one
    # we removed. Short values are ignored -- a 3-character secret would match
    # half the environment by coincidence and is not a realistic credential.
    removed_values = {v for k, v in os.environ.items()
                      if policy.is_secret(k) and len(v) >= 8}
    if removed_values:
        env = {k: v for k, v in env.items() if v not in removed_values}

    return env
