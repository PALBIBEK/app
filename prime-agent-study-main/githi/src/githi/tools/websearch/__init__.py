"""WebSearch tool package."""

from .tool import websearch_tool
from .prompt import WEB_SEARCH_TOOL_NAME, get_web_search_prompt

__all__ = [
    "WEB_SEARCH_TOOL_NAME",
    "get_web_search_prompt",
    "websearch_tool",
]
