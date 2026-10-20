"""CLI entry: ``python -m model_gateway``."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("PORT", "8090"))
    uvicorn.run("model_gateway.app:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
