"""The session package defines each name once.

It used to carry a second copy of its errors (and registry, recovery,
fan-out) in sub-packages; the API caught `SessionNotFound` from one copy while
the manager raised the other, so on the in-process path those handlers could
never match.
"""

from __future__ import annotations

from supervisor import session
from supervisor.session import errors, manager


def test_the_errors_callers_catch_are_the_ones_the_manager_raises() -> None:
    for name in errors.__all__:
        assert getattr(session, name) is getattr(errors, name) is getattr(manager, name), name


def test_every_exported_name_is_defined_in_the_package() -> None:
    for name in session.__all__:
        assert getattr(session, name).__module__.startswith("supervisor.session."), name
