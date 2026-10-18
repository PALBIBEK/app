"""
File operation tools for Githi runtime.

Tools:
- read: Read file contents
- write: Write/overwrite file
- edit: String replacement edit
- edit_diff: Diff-based edit
- ls: List directory
- grep: Search files

Pattern: Tool implementations (PYTHON_PATTERNS_MASTERCLASS.md)
"""

from .grep import GrepTool
from .ls import LsTool
from .read import ReadTool
from .write import WriteTool

__all__ = [
    "ReadTool",
    "WriteTool",
    "LsTool",
    "GrepTool",
    # "EditTool",  # TODO
    # "EditDiffTool",  # TODO
]
