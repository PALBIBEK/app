"""Read-only projections of session trees for the inline terminal."""

from __future__ import annotations

from dataclasses import dataclass

from .session import Session, SessionEntry


@dataclass(frozen=True)
class SessionLeaf:
    number: int
    id: str
    active: bool
    timestamp: str
    preview: str
    labels: tuple[str, ...] = ()


def leaves(session: Session) -> list[SessionLeaf]:
    """Return stable, compact branch-tip rows without leaking Session internals."""
    active = session._current_leaf()
    return [
        SessionLeaf(
            number=index,
            id=entry.id,
            active=entry.id == active,
            timestamp=entry.timestamp,
            preview=_preview(entry),
            labels=tuple(
                str(ancestor.payload["text"])
                for ancestor in session.get_branch(entry.id)
                if ancestor.type == "label" and ancestor.payload.get("text")
            ),
        )
        for index, entry in enumerate(session.leaves(), start=1)
    ]


def format_tree(session: Session) -> str:
    rows = leaves(session)
    if not rows:
        return "[session is empty]"
    rendered = ["[session branches]"]
    for row in rows:
        marker = "*" if row.active else " "
        label = f" · {', '.join(row.labels)}" if row.labels else ""
        rendered.append(f"{marker} {row.number}. {row.id[:8]}  {row.preview}{label}")
    return "\n".join(rendered)


def resolve_leaf(session: Session, target: str) -> str:
    """Resolve `/rewind`'s numbered leaf or unambiguous entry-id prefix."""
    target = target.strip()
    if target.isdigit():
        for row in leaves(session):
            if row.number == int(target):
                return row.id
        raise ValueError(f"no branch numbered {target}")

    matches = [entry.id for entry in session.entries() if entry.id.startswith(target)]
    if not matches:
        raise ValueError(f"no session entry matches {target!r}")
    if len(matches) > 1:
        raise ValueError(f"session entry prefix {target!r} is ambiguous")
    return matches[0]


def _preview(entry: SessionEntry) -> str:
    if entry.message is not None:
        content = getattr(entry.message, "content", "") or ""
        text = " ".join(str(content).split())
        return text[:60] + ("…" if len(text) > 60 else "")
    if entry.type == "label":
        return f"label: {entry.payload.get('text', '')}"
    return entry.type.replace("_", " ")
