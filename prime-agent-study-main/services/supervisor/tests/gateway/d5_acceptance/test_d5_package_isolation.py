"""The gateway stays a self-contained part of the supervisor.

It runs in the supervisor's process, but its code imports nothing from the rest
of the supervisor, so it can keep holding vendor keys behind its own interface.
"""

from __future__ import annotations

from tests.support.layering import imports_outside

GATEWAY = "supervisor.gateway"


def test_the_gateway_imports_nothing_else_of_the_supervisor() -> None:
    assert imports_outside(GATEWAY) == []


def test_the_gateway_package_imports() -> None:
    import supervisor.gateway.app
    import supervisor.gateway.config
    import supervisor.gateway.virtual_keys  # noqa: F401
