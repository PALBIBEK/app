"""Built-in tools: one module per tool, each registered by its ``@tool`` decorator.

Importing this package registers all of them in ``default_registry()``. To add
a tool, add a module here and import it below.
"""

from supervisor.tools.builtin import (  # noqa: F401 — imported to register
    bash,
    echo,
    edit,
    find,
    git_commit,
    grep,
    ls,
    read,
    remember,
    write,
)
