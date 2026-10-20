"""Base class for tool argument models."""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

__all__ = ["ToolArgs", "path_field"]


class ToolArgs(BaseModel):
    """Arguments a model sends to a tool.

    Field docstrings become the property descriptions in the tool's JSON
    schema. Unknown keys are ignored rather than rejected: models add stray
    fields, and refusing the whole call over one costs a round trip for nothing.
    """

    model_config = ConfigDict(
        use_attribute_docstrings=True,
        extra="ignore",
        frozen=True,
    )


def path_field(primary: str, *aliases: str, default: str | None = None) -> Any:
    """A path argument published as ``primary`` that also accepts ``aliases``.

    Tool sets pinned before the registry existed call the same argument by
    different names (``path`` in ``coding-tools``, ``file_path`` in
    ``githi-tools``); accepting both keeps every pinned schema callable.
    """
    choices = AliasChoices(primary, *aliases)
    if default is None:
        return Field(validation_alias=choices)
    return Field(default=default, validation_alias=choices)
