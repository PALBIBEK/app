"""D5 §6 D — product honesty: no NEXT_PUBLIC gateway/vendor secrets; Settings not theater."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]

_SECRETISH = re.compile(
    r"NEXT_PUBLIC_(?:MODEL_)?GATEWAY|NEXT_PUBLIC_.*(?:OPENAI|OPENROUTER|ANTHROPIC).*KEY|"
    r"NEXT_PUBLIC_.*(?:API_KEY|GATEWAY_TOKEN)",
    re.IGNORECASE,
)

_FAKE_KEY_THEATER = re.compile(
    r"""(?:api[_-]?key|openai|openrouter).{0,40}sk-[A-Za-z0-9]{8,}""",
    re.IGNORECASE,
)


def _iter_product_files() -> list[Path]:
    roots = [
        REPO / "frontend",
        REPO / "system",
    ]
    exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".env", ".example", ".md", ".json"}
    out: list[Path] = []
    skip_parts = {"node_modules", "dist", ".next", "coverage", "__pycache__"}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(p in skip_parts for p in path.parts):
                continue
            if path.suffix.lower() in exts or path.name.startswith(".env"):
                out.append(path)
    return out


def test_d_no_next_public_gateway_or_vendor_secrets() -> None:
    """§6 D: no NEXT_PUBLIC gateway token / vendor API key material in FE/system."""
    offenders: list[str] = []
    for path in _iter_product_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _SECRETISH.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{i}:{line.strip()[:120]}")
    assert not offenders, "NEXT_PUBLIC gateway/vendor secrets forbidden:\n" + "\n".join(
        offenders[:40]
    )


def test_d_settings_ui_does_not_claim_mock_keys_drive_chat() -> None:
    """§6 D: Settings must not present mock sk-… keys as driving chat unless BYOK is real."""
    settings_hits: list[str] = []
    for path in _iter_product_files():
        rel = str(path.relative_to(REPO))
        if "setting" not in rel.lower() and "api-key" not in rel.lower() and "apikey" not in rel.lower():
            # Still scan placeholders / i18n that mention API keys.
            if path.suffix not in {".tsx", ".ts", ".jsx", ".js"}:
                continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "Settings" not in text and "API Key" not in text and "apiKeys" not in text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _FAKE_KEY_THEATER.search(line) or (
                "sk-" in line and ("mock" in line.lower() or "fake" in line.lower())
            ):
                settings_hits.append(f"{rel}:{i}:{line.strip()[:120]}")
    assert not settings_hits, (
        "Settings UI must not claim mock provider keys drive chat "
        "(ops-configured or real BYOK only):\n" + "\n".join(settings_hits[:40])
    )
