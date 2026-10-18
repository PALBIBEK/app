"""WebFetch fetch pipeline -- URL safety, redirects, cache, HTML→markdown.

Port of Claude Code's webfetch_utils.ts. Sync httpx; injectable client for
tests. Domain preflight talks to Anthropic's public domain_info endpoint
unless skipped.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import httpx

from .preapproved import is_preapproved_host
from .prompt import make_secondary_model_prompt

MAX_URL_LENGTH = 2000
MAX_HTTP_CONTENT_LENGTH = 10 * 1024 * 1024
FETCH_TIMEOUT_MS = 60_000
DOMAIN_CHECK_TIMEOUT_MS = 10_000
MAX_REDIRECTS = 10
MAX_MARKDOWN_LENGTH = 100_000
CACHE_TTL_S = 15 * 60
MAX_CACHE_SIZE_BYTES = 50 * 1024 * 1024
DOMAIN_CHECK_TTL_S = 5 * 60

USER_AGENT = "githi-webfetch/0.1"


class DomainBlockedError(Exception):
    def __init__(self, domain: str) -> None:
        super().__init__(f"Claude Code is unable to fetch from {domain}")
        self.domain = domain
        self.name = "DomainBlockedError"


class DomainCheckFailedError(Exception):
    def __init__(self, domain: str) -> None:
        super().__init__(
            f"Unable to verify if domain {domain} is safe to fetch. "
            "This may be due to network restrictions or enterprise security "
            "policies blocking claude.ai."
        )
        self.domain = domain
        self.name = "DomainCheckFailedError"


class EgressBlockedError(Exception):
    def __init__(self, domain: str) -> None:
        import json
        super().__init__(json.dumps({
            "error_type": "EGRESS_BLOCKED",
            "domain": domain,
            "message": (
                f"Access to {domain} is blocked by the network egress proxy."
            ),
        }))
        self.domain = domain
        self.name = "EgressBlockedError"


@dataclass
class _CacheEntry:
    bytes: int
    code: int
    code_text: str
    content: str
    content_type: str
    persisted_path: str | None = None
    persisted_size: int | None = None
    expires_at: float = 0.0
    size: int = 1


class _SizedTTLCache:
    """URL-keyed cache with TTL and approximate byte budget (LRU eviction)."""

    def __init__(self, max_size: int, ttl_s: float) -> None:
        self._max_size = max_size
        self._ttl_s = ttl_s
        self._data: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._bytes = 0

    def clear(self) -> None:
        self._data.clear()
        self._bytes = 0

    def get(self, key: str) -> _CacheEntry | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            self._data.pop(key, None)
            self._bytes -= entry.size
            return None
        self._data.move_to_end(key)
        return entry

    def set(self, key: str, entry: _CacheEntry) -> None:
        if key in self._data:
            self._bytes -= self._data[key].size
            del self._data[key]
        entry.expires_at = time.monotonic() + self._ttl_s
        entry.size = max(1, entry.size)
        self._data[key] = entry
        self._bytes += entry.size
        while self._bytes > self._max_size and self._data:
            _, old = self._data.popitem(last=False)
            self._bytes -= old.size


class _DomainCheckCache:
    def __init__(self, max_items: int, ttl_s: float) -> None:
        self._max = max_items
        self._ttl_s = ttl_s
        self._data: OrderedDict[str, float] = OrderedDict()

    def clear(self) -> None:
        self._data.clear()

    def has(self, domain: str) -> bool:
        exp = self._data.get(domain)
        if exp is None:
            return False
        if time.monotonic() > exp:
            self._data.pop(domain, None)
            return False
        self._data.move_to_end(domain)
        return True

    def add(self, domain: str) -> None:
        self._data[domain] = time.monotonic() + self._ttl_s
        self._data.move_to_end(domain)
        while len(self._data) > self._max:
            self._data.popitem(last=False)


_URL_CACHE = _SizedTTLCache(MAX_CACHE_SIZE_BYTES, CACHE_TTL_S)
_DOMAIN_CHECK_CACHE = _DomainCheckCache(128, DOMAIN_CHECK_TTL_S)


def clear_web_fetch_cache() -> None:
    _URL_CACHE.clear()
    _DOMAIN_CHECK_CACHE.clear()


def is_preapproved_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return is_preapproved_host(parsed.hostname or "", parsed.path or "/")
    except Exception:
        return False


def validate_url(url: str) -> bool:
    if len(url) > MAX_URL_LENGTH:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.username or parsed.password:
        return False
    hostname = parsed.hostname or ""
    if len(hostname.split(".")) < 2:
        return False
    return True


@dataclass
class DomainCheckResult:
    status: str  # allowed | blocked | check_failed
    error: Exception | None = None


def check_domain_blocklist(
    domain: str,
    *,
    client: httpx.Client | None = None,
) -> DomainCheckResult:
    if _DOMAIN_CHECK_CACHE.has(domain):
        return DomainCheckResult(status="allowed")
    owns = client is None
    client = client or httpx.Client(timeout=DOMAIN_CHECK_TIMEOUT_MS / 1000)
    try:
        response = client.get(
            "https://api.anthropic.com/api/web/domain_info",
            params={"domain": domain},
            timeout=DOMAIN_CHECK_TIMEOUT_MS / 1000,
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("can_fetch") is True:
                _DOMAIN_CHECK_CACHE.add(domain)
                return DomainCheckResult(status="allowed")
            return DomainCheckResult(status="blocked")
        return DomainCheckResult(
            status="check_failed",
            error=Exception(
                f"Domain check returned status {response.status_code}"),
        )
    except Exception as exc:  # noqa: BLE001
        return DomainCheckResult(status="check_failed", error=exc)
    finally:
        if owns:
            client.close()


def is_permitted_redirect(original_url: str, redirect_url: str) -> bool:
    try:
        original = urlparse(original_url)
        redirect = urlparse(redirect_url)
    except Exception:
        return False
    if redirect.scheme != original.scheme:
        return False
    if redirect.port != original.port:
        return False
    if redirect.username or redirect.password:
        return False

    def strip_www(hostname: str) -> str:
        return re.sub(r"^www\.", "", hostname or "")

    return strip_www(original.hostname or "") == strip_www(
        redirect.hostname or "")


RedirectInfo = dict[str, Any]


def get_with_permitted_redirects(
    url: str,
    *,
    client: httpx.Client | None = None,
    redirect_checker: Callable[[str, str], bool] = is_permitted_redirect,
    depth: int = 0,
) -> httpx.Response | RedirectInfo:
    if depth > MAX_REDIRECTS:
        raise RuntimeError(f"Too many redirects (exceeded {MAX_REDIRECTS})")

    owns = client is None
    client = client or httpx.Client(timeout=FETCH_TIMEOUT_MS / 1000)
    try:
        response = client.get(
            url,
            follow_redirects=False,
            timeout=FETCH_TIMEOUT_MS / 1000,
            headers={
                "Accept": "text/markdown, text/html, */*",
                "User-Agent": USER_AGENT,
            },
        )
        if response.status_code in (301, 302, 307, 308):
            location = response.headers.get("location")
            if not location:
                raise RuntimeError("Redirect missing Location header")
            redirect_url = urljoin(url, location)
            if redirect_checker(url, redirect_url):
                return get_with_permitted_redirects(
                    redirect_url,
                    client=client,
                    redirect_checker=redirect_checker,
                    depth=depth + 1,
                )
            return {
                "type": "redirect",
                "originalUrl": url,
                "redirectUrl": redirect_url,
                "statusCode": response.status_code,
            }
        if (
            response.status_code == 403
            and response.headers.get("x-proxy-error") == "blocked-by-allowlist"
        ):
            raise EgressBlockedError(urlparse(url).hostname or url)
        response.raise_for_status()
        if len(response.content) > MAX_HTTP_CONTENT_LENGTH:
            raise RuntimeError(
                f"Response exceeds {MAX_HTTP_CONTENT_LENGTH} byte limit")
        return response
    finally:
        if owns:
            client.close()


class _HTMLToMarkdown(HTMLParser):
    """Minimal HTML→markdown (stdlib stand-in for turndown)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._in_pre = False
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in ("script", "style", "noscript"):
            self._skip += 1
            return
        if self._skip:
            return
        attr = dict(attrs)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self.parts.append("\n" + "#" * level + " ")
        elif tag == "p":
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "a":
            self._href = attr.get("href")
            self.parts.append("[")
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("em", "i"):
            self.parts.append("*")
        elif tag == "code" and not self._in_pre:
            self.parts.append("`")
        elif tag == "pre":
            self._in_pre = True
            self.parts.append("\n```\n")
        elif tag == "blockquote":
            self.parts.append("\n> ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p"):
            self.parts.append("\n")
        elif tag == "a":
            if self._href:
                self.parts.append(f"]({self._href})")
            else:
                self.parts.append("]")
            self._href = None
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("em", "i"):
            self.parts.append("*")
        elif tag == "code" and not self._in_pre:
            self.parts.append("`")
        elif tag == "pre":
            self._in_pre = False
            self.parts.append("\n```\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_pre:
            self.parts.append(data)
        else:
            self.parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()


def html_to_markdown(html: str) -> str:
    parser = _HTMLToMarkdown()
    parser.feed(html)
    parser.close()
    return parser.text()


def _is_binary_content_type(content_type: str) -> bool:
    ct = content_type.lower()
    if any(t in ct for t in ("text/", "json", "xml", "javascript", "svg")):
        return False
    return any(t in ct for t in (
        "octet-stream", "pdf", "zip", "image/", "audio/", "video/",
        "application/pdf", "application/zip",
    ))


def get_url_markdown_content(
    url: str,
    *,
    client: httpx.Client | None = None,
    skip_preflight: bool = False,
    persist_binary: Callable[[bytes, str, str], tuple[str, int] | None] | None = None,
) -> dict[str, Any]:
    """Fetch URL → markdown content dict, or redirect info dict."""
    if not validate_url(url):
        raise ValueError("Invalid URL")

    cached = _URL_CACHE.get(url)
    if cached is not None:
        return {
            "bytes": cached.bytes,
            "code": cached.code,
            "codeText": cached.code_text,
            "content": cached.content,
            "contentType": cached.content_type,
            "persistedPath": cached.persisted_path,
            "persistedSize": cached.persisted_size,
        }

    parsed = urlparse(url)
    upgraded = url
    if parsed.scheme == "http":
        upgraded = parsed._replace(scheme="https").geturl()

    hostname = urlparse(upgraded).hostname or ""
    owns = client is None
    client = client or httpx.Client(timeout=FETCH_TIMEOUT_MS / 1000)
    try:
        if not skip_preflight:
            check = check_domain_blocklist(hostname, client=client)
            if check.status == "blocked":
                raise DomainBlockedError(hostname)
            if check.status == "check_failed":
                raise DomainCheckFailedError(hostname)

        response = get_with_permitted_redirects(upgraded, client=client)
        if isinstance(response, dict) and response.get("type") == "redirect":
            return response

        assert isinstance(response, httpx.Response)
        raw = response.content
        content_type = response.headers.get("content-type", "")
        bytes_len = len(raw)

        persisted_path: str | None = None
        persisted_size: int | None = None
        if _is_binary_content_type(content_type) and persist_binary is not None:
            persist_id = f"webfetch-{int(time.time() * 1000)}"
            saved = persist_binary(raw, content_type, persist_id)
            if saved:
                persisted_path, persisted_size = saved

        text = raw.decode("utf-8", errors="replace")
        if "text/html" in content_type:
            markdown = html_to_markdown(text)
            content_bytes = len(markdown.encode("utf-8"))
        else:
            markdown = text
            content_bytes = bytes_len

        entry = _CacheEntry(
            bytes=bytes_len,
            code=response.status_code,
            code_text=response.reason_phrase or "",
            content=markdown,
            content_type=content_type,
            persisted_path=persisted_path,
            persisted_size=persisted_size,
            size=max(1, content_bytes),
        )
        _URL_CACHE.set(url, entry)
        return {
            "bytes": entry.bytes,
            "code": entry.code,
            "codeText": entry.code_text,
            "content": entry.content,
            "contentType": entry.content_type,
            "persistedPath": entry.persisted_path,
            "persistedSize": entry.persisted_size,
        }
    finally:
        if owns:
            client.close()


def apply_prompt_to_markdown(
    prompt: str,
    markdown_content: str,
    *,
    is_preapproved_domain: bool,
    apply_prompt: Callable[..., str] | None = None,
    llm: Any = None,
    secondary_model: str | None = None,
) -> str:
    truncated = markdown_content
    if len(truncated) > MAX_MARKDOWN_LENGTH:
        truncated = (
            markdown_content[:MAX_MARKDOWN_LENGTH]
            + "\n\n[Content truncated due to length...]"
        )

    if apply_prompt is not None:
        return apply_prompt(
            prompt, truncated, is_preapproved=is_preapproved_domain)

    model_prompt = make_secondary_model_prompt(
        truncated, prompt, is_preapproved_domain)

    if llm is not None:
        from ...types import UserMessage
        completion = llm.complete(
            [UserMessage(content=model_prompt)],
            tools=[],
            model=secondary_model,
        )
        text = (completion.message.content or "").strip()
        return text or "No response from model"

    return truncated
