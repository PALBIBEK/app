"""Entrypoint: python -m memory_service"""

from __future__ import annotations

import os

import uvicorn

from memory_service.app import create_app


def main() -> None:
    url = os.environ.get("MEMORY_DATABASE_URL", "sqlite+aiosqlite:///./memory.db")
    host = os.environ.get("MEMORY_HOST", "0.0.0.0")
    port = int(os.environ.get("MEMORY_PORT", "8092"))
    app = create_app(database_url=url)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
