"""Which model a session may be switched to: one it can run now.

A session runs the model its composition pins unless its owner chose another, when it was
created or with a message (QM's composer sends the chosen model with every turn,
``plugins/web-ui/src/composer.ts``). A choice is accepted only when the model is offered now
(:meth:`ModelRouter.selectable_models`: a vendor with no key offers none), so a session is
never switched to a model it cannot call.
"""

from __future__ import annotations

from supervisor.models.service import model_router

__all__ = ["ModelNotOffered", "require_offered"]


class ModelNotOffered(ValueError):
    """The chosen model is not one a session can run now."""

    def __init__(self, model: str, reason: str) -> None:
        super().__init__(f"{model!r} {reason}")
        self.model = model


def require_offered(model: str) -> None:
    """Raise :class:`ModelNotOffered` unless ``model`` is selectable now."""
    router = model_router()
    if router is None:
        raise ModelNotOffered(model, "cannot be chosen: no model gateway is configured")
    if model not in {spec.id for spec in router.selectable_models()}:
        raise ModelNotOffered(
            model, "is not offered now: its provider may have no key, or it is not served"
        )
