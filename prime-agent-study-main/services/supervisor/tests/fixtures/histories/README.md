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

## Every patched path is recorded

`test_every_patched_path_has_a_recording` lists the patch ids the workflows branch on and
fails when one has no recorded history that took it. So a PR that adds a
`workflow.patched("…")` path also records a release once it merges, as above. The recorder
tells histories apart by the patch ids they took, the steps they scheduled and the signals
they got, and it keeps sessions that were still running when the suite ended (held messages
and stops usually end there).

Two kinds of change are beyond any replay check: reordering calls of the same activity type,
and changing an activity's arguments. Temporal compares only each step's type and sequence.
