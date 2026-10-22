# Release histories: the replay gate

Workflow histories recorded by earlier releases. `tests/unit/test_release_replay.py` replays
every one against the current workflow code: a change that would break runs started on an
older release (a step added, removed or reordered without `workflow.patched`) fails here
before it merges.

Each folder is one release (`r<commit>`). To add the release you are about to ship, record
its histories once it is merged, and keep the older folders:

    RECORD_HISTORIES=tests/fixtures/histories/r$(git rev-parse --short HEAD) \
      pytest -q tests/integration

Remove a folder only when no workflow that release started can still be running (sessions
continue-as-new, so a folder older than your longest session is safe to drop). Before a
deploy, `python -m supervisor rollout check` replays what is running in production the same
way.
