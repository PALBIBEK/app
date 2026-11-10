"""Memory errors. Class names are the error kinds the API and workflows report."""

from __future__ import annotations

from collections.abc import Sequence


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


class NotebookArchived(NotebookError):
    """An archived notebook is read-only until it is unarchived; nothing was changed."""

    def __init__(self, ref: str) -> None:
        super().__init__(f"notebook {ref!r} is archived: unarchive it to change its facts")
        self.ref = ref


class EmptyFact(NotebookError):
    """An edit would leave a fact with no text (delete it instead); nothing was changed."""

    def __init__(self, ids: Sequence[str]) -> None:
        listed = ", ".join(f"#{i}" for i in ids)
        super().__init__(f"an edit would leave {listed} empty: delete a fact rather than blank it")
        self.ids = tuple(ids)


class InvalidExpiry(NotebookError):
    """An expiry that is not a day as YYYY-MM-DD; nothing was changed."""

    def __init__(self, value: str) -> None:
        super().__init__(f"an expiry is a day as YYYY-MM-DD, not {value!r}")
        self.value = value


class InvalidObservation(NotebookError):
    """An observation outside the store's bounds (``memory_service.observations``); nothing
    of its capture was kept."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"invalid observation: {reason}")
        self.reason = reason


class MemoryLimitExceeded(NotebookError):
    """A request beyond a limit of ``supervisor.memory_service.limits``: nothing was changed."""

    def __init__(self, what: str, limit: int, got: int) -> None:
        super().__init__(f"{what}: at most {limit}, got {got}")
        self.what, self.limit, self.got = what, limit, got


class RevisionOutOfRange(NotebookError):
    """A revision the notebook has not reached (or below 0): nothing was read or changed."""

    def __init__(self, revision: int, current: int) -> None:
        super().__init__(f"no revision {revision}: this notebook has revisions 0 to {current}")
        self.revision, self.current = revision, current


class RevisionConflict(NotebookError):
    """The notebook changed since it was read; the stale change was not applied."""

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"notebook changed (revision {actual}, expected {expected})")
        self.expected, self.actual = expected, actual
