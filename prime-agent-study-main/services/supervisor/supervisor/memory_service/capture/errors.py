"""The capture queue's errors (G3), as Grok's ``V2CaptureError`` names them. Class names are
the error codes the API reports."""

from __future__ import annotations


class CaptureError(Exception):
    """Base for capture-queue errors; none is fixed by retrying the same call."""


class CaptureInvalid(CaptureError):
    """Input the queue refuses: a bad range, owner, lease duration or session."""


class CaptureConflict(CaptureError):
    """A job committed already with another outcome, or a job id reused for another burst."""


class StaleLease(CaptureError):
    """The lease is not this worker's any more (expired and claimed again, or released)."""

    def __init__(self) -> None:
        super().__init__("capture lease is stale or not owned by this worker")


class StaleResolution(CaptureError):
    """The notebook changed since the capture was resolved against it (S3): resolve again
    against what it holds now. The lease is kept, so the worker commits once it has."""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"the notebook changed since resolution ({expected} is now {actual})")
        self.expected, self.actual = expected, actual


class UnknownCaptureSession(CaptureError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"no capture queued for session {session_id}")
        self.session_id = session_id
