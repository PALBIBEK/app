"""WebFetch -- fetch a URL, convert to markdown, apply an extraction prompt."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..base import Tool, ToolContext, ToolResult, fail, ok
from .prompt import WEB_FETCH_TOOL_NAME, tool_description
from .utils import (
    MAX_MARKDOWN_LENGTH,
    DomainBlockedError,
    DomainCheckFailedError,
    EgressBlockedError,
    apply_prompt_to_markdown,
    get_url_markdown_content,
    is_preapproved_url,
)

_STATUS_TEXT = {
    301: "Moved Permanently",
    302: "Found",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
}


def _format_file_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _persist_binary(root: Path, data: bytes, content_type: str, persist_id: str):
    ext = ".bin"
    ct = content_type.lower()
    if "pdf" in ct:
        ext = ".pdf"
    elif "png" in ct:
        ext = ".png"
    elif "jpeg" in ct or "jpg" in ct:
        ext = ".jpg"
    elif "zip" in ct:
        ext = ".zip"
    folder = root / ".githi" / "webfetch"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{persist_id}{ext}"
    path.write_bytes(data)
    return str(path), len(data)


def webfetch_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = str(args.get("url") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        if not url:
            return fail("WebFetch requires a non-empty `url`.")
        if not prompt:
            return fail("WebFetch requires a non-empty `prompt`.")

        try:
            urlparse(url)
        except Exception:
            return fail(
                f'Error: Invalid URL "{url}". The URL provided could not be parsed.')

        # Cheap parse check -- utils.validate_url does the real filter.
        from .utils import validate_url
        if not validate_url(url) and "://" not in url:
            return fail(
                f'Error: Invalid URL "{url}". The URL provided could not be parsed.')

        start = time.monotonic()
        client = ctx.extra.get("http_client")
        skip_preflight = bool(ctx.extra.get("skip_web_fetch_preflight", False))

        def persist(data: bytes, content_type: str, persist_id: str):
            return _persist_binary(Path(ctx.policy.root), data, content_type, persist_id)

        try:
            response = get_url_markdown_content(
                url,
                client=client,
                skip_preflight=skip_preflight,
                persist_binary=persist,
            )
        except ValueError:
            return fail(
                f'Error: Invalid URL "{url}". The URL provided could not be parsed.')
        except DomainBlockedError as exc:
            return fail(str(exc))
        except DomainCheckFailedError as exc:
            return fail(str(exc))
        except EgressBlockedError as exc:
            return fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            return fail(f"WebFetch failed: {exc}")

        duration_ms = int((time.monotonic() - start) * 1000)

        if response.get("type") == "redirect":
            status = int(response["statusCode"])
            status_text = _STATUS_TEXT.get(status, "Found")
            message = (
                "REDIRECT DETECTED: The URL redirects to a different host.\n\n"
                f"Original URL: {response['originalUrl']}\n"
                f"Redirect URL: {response['redirectUrl']}\n"
                f"Status: {status} {status_text}\n\n"
                "To complete your request, I need to fetch content from the "
                "redirected URL. Please use WebFetch again with these parameters:\n"
                f'- url: "{response["redirectUrl"]}"\n'
                f'- prompt: "{prompt}"'
            )
            return ok(
                message,
                bytes=len(message.encode()),
                code=status,
                codeText=status_text,
                durationMs=duration_ms,
                url=url,
            )

        content = response["content"]
        content_type = response.get("contentType") or ""
        is_preapproved = is_preapproved_url(url)

        if (
            is_preapproved
            and "text/markdown" in content_type
            and len(content) < MAX_MARKDOWN_LENGTH
        ):
            result = content
        else:
            result = apply_prompt_to_markdown(
                prompt,
                content,
                is_preapproved_domain=is_preapproved,
                apply_prompt=ctx.extra.get("apply_prompt"),
                llm=ctx.extra.get("llm"),
                secondary_model=ctx.extra.get("secondary_model"),
            )

        persisted_path = response.get("persistedPath")
        if persisted_path:
            size = response.get("persistedSize") or response["bytes"]
            result += (
                f"\n\n[Binary content ({content_type}, "
                f"{_format_file_size(int(size))}) also saved to {persisted_path}]"
            )

        return ok(
            result,
            bytes=response["bytes"],
            code=response["code"],
            codeText=response.get("codeText") or "",
            durationMs=duration_ms,
            url=url,
        )

    return Tool(
        name=WEB_FETCH_TOOL_NAME,
        description=tool_description(),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch content from",
                },
                "prompt": {
                    "type": "string",
                    "description": "The prompt to run on the fetched content",
                },
            },
            "required": ["url", "prompt"],
        },
        run=run,
    )
