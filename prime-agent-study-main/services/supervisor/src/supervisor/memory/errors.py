"""Memory errors. Class names are the error kinds the API and workflows report."""

from __future__ import annotations


class NotebookError(Exception):
    """Base for memory errors; none is fixed by retrying."""

    retryable = False


class NotebookNotFound(NotebookError):
    def __init__(self, ref: str) -> None:
        super().__init__(f"no notebook {ref!r} for this agent")
        self.ref = ref


class NotebookExists(NotebookError):
    def __init__(self, name: str) -> None:
        super().__init__(f"a notebook named {name!r} already exists for this agent")
        self.name = name


class MemoryFactsMissing(NotebookError):
    """Some ids are not facts this conversation can edit; nothing was changed."""

    def __init__(self, ids: list[str] | tuple[str, ...]) -> None:
        listed = ", ".join(f"#{i}" for i in ids)
        super().__init__(
            f"No fact {listed} in a notebook this conversation can edit. "
            "Nothing was changed; search again for current ids."
        )
        self.ids = tuple(ids)


class MemoryDisclosureDenied(NotebookError):
    def __init__(self) -> None:
        super().__init__(
            "This notebook isn't editable from this conversation. "
            "Edit it from an authorized conversation."
        )


class RevisionConflict(NotebookError):
    """The notebook changed since it was read; the stale change was not applied."""

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"notebook changed (revision {actual}, expected {expected})")
        self.expected, self.actual = expected, actual
