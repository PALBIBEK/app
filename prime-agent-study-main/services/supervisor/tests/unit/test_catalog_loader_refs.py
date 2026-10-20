"""A shipped composition naming an artifact that does not exist fails with DigestMissing.

(It raised TypeError: the error was built with a positional message it does not take.)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.catalog.core.errors import DigestMissing
from supervisor.catalog.loading.loader import load_composition_catalog


def test_an_unknown_reference_is_digest_missing(tmp_path: Path) -> None:
    (tmp_path / "compositions").mkdir()
    (tmp_path / "compositions" / "broken.json").write_text(json.dumps({
        "name": "broken",
        "semver": "1.0.0",
        "protocol_version": 1,
        "status": "offered",
        "harness_version": "githi-v1",
        "prompt_pack": {"id": "no-such-pack", "digest": "sha256:REPLACE"},
        "tool_set": {"id": "no-such-tools", "digest": "sha256:REPLACE"},
    }))
    with pytest.raises(DigestMissing) as missing:
        load_composition_catalog(tmp_path)
    assert "no-such-pack" in str(missing.value)
