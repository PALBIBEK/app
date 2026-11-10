"""JSON forms of the notebook types: what the memory service sends and the supervisor reads.

One module for both ends, so a field added on one side cannot be missed on the
other. Unknown fields are ignored on read (an older reader keeps working).
"""

from __future__ import annotations

from typing import Any

from supervisor.memory_service.notebooks.types import (
    CaptureMetadata,
    Edit,
    Fact,
    Notebook,
    NotebookStats,
    Provenance,
    Revision,
    Sensitivity,
    Typed,
)
from supervisor.memory_service.observations import (
    ObservationDraft,
    ObservationInvalid,
    ObservationType,
)


def provenance_out(p: Provenance) -> dict[str, Any]:
    return {"sensitivity": p.sensitivity.value, "sources": list(p.sources), "source_unknown": p.source_unknown}


def provenance_in(d: dict[str, Any]) -> Provenance:
    return Provenance(
        Sensitivity.parse(d.get("sensitivity")), tuple(d.get("sources") or ()), bool(d.get("source_unknown"))
    )


def notebook_out(n: Notebook) -> dict[str, Any]:
    return {
        "id": n.id,
        "tenant_id": n.tenant_id,
        "owner": n.owner,
        "agent_pin": n.agent_pin,
        "agent": n.agent,
        "name": n.name,
        "label": n.label,
        "revision": n.revision,
        "created_at": n.created_at,
        "archived_at": n.archived_at,
        "last_tidied_at": n.last_tidied_at,
    }


def notebook_in(d: dict[str, Any]) -> Notebook:
    return Notebook(
        id=str(d["id"]),
        tenant_id=str(d["tenant_id"]),
        owner=str(d["owner"]),
        agent_pin=str(d["agent_pin"]),
        name=str(d["name"]),
        label=d.get("label"),
        revision=int(d["revision"]),
        created_at=int(d["created_at"]),
        archived_at=d.get("archived_at"),
        last_tidied_at=d.get("last_tidied_at"),
        agent=d.get("agent"),
    )


def fact_out(f: Fact) -> dict[str, Any]:
    return {
        "id": f.id,
        "text": f.text,
        "provenance": provenance_out(f.provenance),
        "created_at": f.created_at,
        "updated_at": f.updated_at,
        "author": f.author,
        "expires_on": f.expires_on,
        "typed": typed_out(f.typed),
    }


def fact_in(d: dict[str, Any]) -> Fact:
    return Fact(
        id=str(d["id"]),
        text=str(d["text"]),
        provenance=provenance_in(d["provenance"]),
        created_at=int(d["created_at"]),
        updated_at=int(d["updated_at"]),
        author=d.get("author"),
        expires_on=d.get("expires_on"),
        typed=typed_in(d.get("typed")),  # absent from a service before G2
    )


def typed_out(t: Typed | None) -> dict[str, Any] | None:
    if t is None:
        return None
    return {
        "kind": t.kind,
        "topic": t.topic,
        "keywords": list(t.keywords),
        "aliases": list(t.aliases),
        "body": t.body,
        "extraction_model": t.extraction_model,
        "prompt_version": t.prompt_version,
    }


def typed_in(d: dict[str, Any] | None) -> Typed | None:
    if d is None:
        return None
    return Typed(
        kind=str(d["kind"]),
        topic=d.get("topic"),
        keywords=tuple(d.get("keywords") or ()),
        aliases=tuple(d.get("aliases") or ()),
        body=d.get("body"),
        extraction_model=d.get("extraction_model"),
        prompt_version=d.get("prompt_version"),
    )


def observation_out(o: ObservationDraft) -> dict[str, Any]:
    return {
        "type": o.observation_type.value,
        "topic_hint": o.topic_hint,
        "statement": o.statement,
        "keywords": list(o.keywords),
        "aliases": list(o.aliases),
        "body": o.body,
        "extraction_model": o.extraction_model,
        "prompt_version": o.prompt_version,
        "created_at": o.created_at,
    }


def observation_in(d: dict[str, Any]) -> ObservationDraft:
    """An observation as sent, unchecked (``ObservationDraft.validate`` checks it); a
    field of the wrong shape is ``ObservationInvalid``."""
    try:
        return ObservationDraft(
            observation_type=ObservationType.parse(str(d["type"])),
            topic_hint=_optional_str(d.get("topic_hint")),
            statement=str(d["statement"]),
            keywords=tuple(str(k) for k in d.get("keywords") or ()),
            aliases=tuple(str(a) for a in d.get("aliases") or ()),
            extraction_model=str(d["extraction_model"]),
            prompt_version=str(d["prompt_version"]),
            created_at=int(d["created_at"]),
            body=_optional_str(d.get("body")),
        )
    except ObservationInvalid:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ObservationInvalid(f"malformed observation: {exc}") from None


def typed_of(o: ObservationDraft) -> Typed:
    """What a fact keeps of the observation it came from."""
    return Typed(
        kind=o.observation_type.value,
        topic=o.topic_hint,
        keywords=o.keywords,
        aliases=o.aliases,
        body=o.body,
        extraction_model=o.extraction_model,
        prompt_version=o.prompt_version,
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def revision_out(r: Revision) -> dict[str, Any]:
    return {
        "revision": r.revision,
        "fact_id": r.fact_id,
        "op": r.op,
        "text": r.text,
        "provenance": provenance_out(r.provenance),
        "at": r.at,
        "author": r.author,
    }


def revision_in(d: dict[str, Any]) -> Revision:
    return Revision(
        revision=int(d["revision"]),
        fact_id=str(d["fact_id"]),
        op=str(d["op"]),
        text=str(d["text"]),
        provenance=provenance_in(d["provenance"]),
        at=int(d["at"]),
        author=d.get("author"),
    )


def stats_out(s: NotebookStats) -> dict[str, Any]:
    return {"facts": s.facts, "bytes": s.bytes, "updated_at": s.updated_at}


def stats_in(d: dict[str, Any]) -> NotebookStats:
    return NotebookStats(int(d["facts"]), int(d["bytes"]), d.get("updated_at"))


def edit_out(e: Edit) -> dict[str, Any]:
    return {"id": e.id, "text": e.text}


def edit_in(d: dict[str, Any]) -> Edit:
    return Edit(str(d["id"]), str(d["text"]))


def context_out(c: CaptureMetadata | None) -> dict[str, Any] | None:
    """A capture's runtime context: only the fields provenance is computed from.

    ``inherited`` keeps its two meanings: ``None`` (not tracked: the capture's
    provenance is unknown) and a list (tracked, possibly empty).
    """
    if c is None:
        return None
    return {
        "source": c.source,
        "session_id": c.session_id,
        "sensitivity": c.sensitivity.value if c.sensitivity is not None else None,
        "inherited": None if c.inherited is None else [provenance_out(p) for p in c.inherited],
        "expires_on": c.expires_on,
    }


def context_in(d: dict[str, Any] | None) -> CaptureMetadata | None:
    if d is None:
        return None
    inherited = d.get("inherited")
    return CaptureMetadata(
        source=d.get("source"),
        session_id=d.get("session_id"),
        sensitivity=Sensitivity.parse(d["sensitivity"]) if d.get("sensitivity") else None,
        inherited=None if inherited is None else tuple(provenance_in(p) for p in inherited),
        expires_on=d.get("expires_on"),
    )
