"""Cost estimator — pre-execution cost estimation for early rejection.

Enables rejecting requests before expensive model calls:
- Estimate cost based on input tokens and expected output
- Check if estimate would exceed budget
- Fail fast without burning API credits

Reference: QM's cost estimation pattern.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


__all__ = [
]


# Default estimated output ratio (output tokens as fraction of input)
DEFAULT_OUTPUT_RATIO = Decimal("0.5")

# Minimum estimated output tokens
MIN_ESTIMATED_OUTPUT = 100


