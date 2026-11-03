# Journal event samples: the read-compatibility gate

The journal keeps every event for as long as the session lives, and every build must read
what earlier builds wrote. Each file here is one release's stored payload of every event type
(`r<commit>.json`). `tests/unit/test_event_samples.py` decodes every one of them with the
current code:

- a change that makes an old payload unreadable (a new field with no default, a renamed or
  retyped field) fails there before it merges;
- the newest file must have a sample of every event type this build knows, so adding a type
  means recording a new file:

      python -m tests.support.event_samples tests/fixtures/events/r$(git rev-parse --short HEAD).json

Keep the older files. A type a build does not know at all (a newer build wrote it during a
rolling deploy) is read as `UnknownEvent` and passed through, not refused.
