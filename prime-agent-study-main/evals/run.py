"""CI entrypoint for agent-platform evals."""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    tasks = Path(__file__).parent / "tasks"
    raise NotImplementedError(f"run evals from {tasks}")


if __name__ == "__main__":
    main()
