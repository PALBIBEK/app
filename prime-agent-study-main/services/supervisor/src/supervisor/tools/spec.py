"""``ToolSpec``: one tool's contract with the model, bound to its implementation.

A tool is an ``async def`` taking a pydantic model of its arguments and a
``ToolContext``. Everything the model is shown is derived from that function,
so the schema can never drift from the code that parses it:

* the JSON schema comes from the arguments model (field docstrings become
  property descriptions);
* the description is the first paragraph of the function's docstring, and the
  rest of the docstring stays developer documentation.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast, get_type_hints

from pydantic import BaseModel, ValidationError
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments, ToolError, ToolTimeout

__all__ = ["Handler", "ToolClass", "ToolSpec", "json_schema"]

type Handler[ArgsT: BaseModel] = Callable[[ArgsT, ToolContext], Awaitable[Any]]
"""``async def name(args: ArgsModel, ctx: ToolContext) -> payload``."""


class ToolClass(StrEnum):
    """How safely a call may be re-run after a crash; harness ``recovery`` keys on it."""

    PURE = "pure"
    IDEMPOTENT = "idempotent"
    TRANSACTIONAL = "transactional"
    NON_ATOMIC = "non_atomic"


class _ModelFacingSchema(GenerateJsonSchema):
    """JSON schema as a model should read it: no titles, no ``null`` unions, no defaults.

    Optional arguments are simply not ``required``; spelling them as
    ``anyOf[..., null]`` only invites a model to send ``null``. Defaults belong
    in the field description, where the model actually reads them.
    """

    def field_title_should_be_set(self, schema: Any) -> bool:
        return False

    def model_schema(self, schema: core_schema.ModelSchema) -> JsonSchemaValue:
        generated = super().model_schema(schema)
        generated.pop("title", None)
        generated.pop("description", None)  # the tool's description says it once
        return generated

    def nullable_schema(self, schema: core_schema.NullableSchema) -> JsonSchemaValue:
        return self.generate_inner(schema["schema"])

    def default_schema(self, schema: core_schema.WithDefaultSchema) -> JsonSchemaValue:
        return self.generate_inner(schema["schema"])


@functools.cache
def _cached_schema(model: type[BaseModel]) -> dict[str, Any]:
    generated = model.model_json_schema(by_alias=True, schema_generator=_ModelFacingSchema)
    generated.setdefault("required", [])
    return generated


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model-facing JSON schema for an arguments model (a fresh copy)."""
    return copy.deepcopy(_cached_schema(model))


@dataclass(frozen=True, slots=True)
class ToolSpec[ArgsT: BaseModel]:
    name: str
    description: str
    args_model: type[ArgsT]
    handler: Handler[ArgsT]
    tool_class: ToolClass

    @classmethod
    def from_handler(
        cls,
        handler: Handler[ArgsT],
        *,
        name: str | None = None,
        tool_class: ToolClass,
    ) -> ToolSpec[ArgsT]:
        """Derive a spec from a handler's signature and docstring; reject malformed ones."""
        qualname = getattr(handler, "__qualname__", repr(handler))
        if not inspect.iscoroutinefunction(handler):
            raise TypeError(f"tool {qualname} must be an async function")
        params = list(inspect.signature(handler).parameters)
        if len(params) != 2:
            raise TypeError(f"tool {qualname} must take exactly (args, ctx)")
        args_model = get_type_hints(handler).get(params[0])
        if not (isinstance(args_model, type) and issubclass(args_model, BaseModel)):
            raise TypeError(f"tool {qualname}: annotate {params[0]!r} with a pydantic model")
        description = _summary(inspect.getdoc(handler))
        if not description:
            raise TypeError(
                f"tool {qualname} needs a docstring; its first paragraph is shown to the model"
            )
        return cls(
            name=name or handler.__name__,
            description=description,
            args_model=cast("type[ArgsT]", args_model),
            handler=handler,
            tool_class=ToolClass(tool_class),
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return json_schema(self.args_model)

    @property
    def origin(self) -> str:
        """Where the implementation lives, e.g. ``supervisor.tools.builtin.read:read``."""
        return f"{self.handler.__module__}:{self.handler.__qualname__}"

    def definition(self) -> dict[str, Any]:
        """The catalog ``ToolDefinition`` body this implementation publishes."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "tool_class": self.tool_class.value,
        }

    def parse(self, raw: Mapping[str, Any]) -> ArgsT:
        try:
            return self.args_model.model_validate(dict(raw))
        except ValidationError as exc:
            raise InvalidArguments(_explain(self.name, exc)) from None

    async def run(self, raw: Mapping[str, Any], ctx: ToolContext) -> Any:
        """Validate ``raw`` and run the handler within the context's time budget."""
        args = self.parse(raw)
        try:
            return await asyncio.wait_for(self.handler(args, ctx), ctx.timeout_s)
        except ToolError:
            raise
        except TimeoutError:
            raise ToolTimeout(self.name, ctx.timeout_s) from None


def _summary(doc: str | None) -> str:
    """First paragraph of a docstring, as one line."""
    if not doc:
        return ""
    return " ".join(doc.split("\n\n", 1)[0].split())


def _explain(name: str, exc: ValidationError) -> str:
    problems = "; ".join(
        f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}" for err in exc.errors()
    )
    return f"invalid arguments for {name}: {problems}"
