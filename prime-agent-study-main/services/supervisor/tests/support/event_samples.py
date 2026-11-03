"""Record one stored payload of every journal event type, as this build writes it.

    python -m tests.support.event_samples tests/fixtures/events/r$(git rev-parse --short HEAD).json

``tests/unit/test_event_samples.py`` decodes every recorded release's samples with the
current code (see tests/fixtures/events/README.md).
"""

from __future__ import annotations

import dataclasses
import json
import sys
import types
import typing
from typing import Any

from supervisor.agent import events as ev
from supervisor.journal.serde import _EVENT_TYPES, encode_event


def _value(annotation: Any) -> Any:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        options = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _value(options[0]) if options else None
    if origin is typing.Literal:
        return typing.get_args(annotation)[0]
    if annotation is str:
        return "sample"
    if annotation is int:
        return 1
    if annotation is float:
        return 0.5
    if annotation is bool:
        return True
    if origin in (list, tuple) or annotation in (list, tuple):
        return []
    if origin is dict or annotation is dict:
        return {}
    if dataclasses.is_dataclass(annotation):
        return _instance(annotation)
    return "sample"


def _instance(cls: type) -> Any:
    hints = typing.get_type_hints(cls, vars(ev))
    values = {
        f.name: _value(hints[f.name])
        for f in dataclasses.fields(cls)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    return cls(**values)


def samples() -> dict[str, dict[str, Any]]:
    return {name: encode_event(_instance(cls)) for name, cls in sorted(_EVENT_TYPES.items())}


if __name__ == "__main__":
    with open(sys.argv[1], "w") as fh:
        json.dump(samples(), fh, indent=1, sort_keys=True)
        fh.write("\n")
