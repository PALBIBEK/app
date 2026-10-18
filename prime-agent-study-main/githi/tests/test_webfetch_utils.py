"""WebFetch utils -- URL safety, redirects, cache, HTML→markdown.

Ported from Claude Code's webfetch_utils.ts. Tests the *mechanics* with
httpx MockTransport so no network is required.
"""

from __future__ import annotations

import httpx
import pytest

from githi.tools.webfetch.utils import (
    MAX_MARKDOWN_LENGTH,
    DomainBlockedError,
    DomainCheckFailedError,
    clear_web_fetch_cache,
    check_domain_blocklist,
    get_url_markdown_content,
    get_with_permitted_redirects,
    is_permitted_redirect,
    is_preapproved_url,
    make_secondary_model_prompt,
    validate_url,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_web_fetch_cache()
    yield
    clear_web_fetch_cache()


class TestValidateURL:
    def test_accepts_ordinary_https(self):
        assert validate_url("https://example.com/path") is True

    def test_rejects_overlong_url(self):
        assert validate_url("https://example.com/" + "a" * 2000) is False

    def test_rejects_credentials_in_url(self):
        assert validate_url("https://user:pass@example.com/") is False

    def test_rejects_single_label_hostname(self):
        assert validate_url("https://localhost/") is False

    def test_rejects_garbage(self):
        assert validate_url("not a url") is False


class TestPermittedRedirect:
    def test_same_host_path_change_is_ok(self):
        assert is_permitted_redirect(
            "https://example.com/a", "https://example.com/b") is True

    def test_adding_www_is_ok(self):
        assert is_permitted_redirect(
            "https://example.com/", "https://www.example.com/") is True

    def test_removing_www_is_ok(self):
        assert is_permitted_redirect(
            "https://www.example.com/", "https://example.com/") is True

    def test_cross_host_is_not_followed(self):
        assert is_permitted_redirect(
            "https://example.com/", "https://evil.com/") is False

    def test_protocol_change_is_not_ok(self):
        assert is_permitted_redirect(
            "https://example.com/", "http://example.com/") is False

    def test_credentials_on_redirect_are_not_ok(self):
        assert is_permitted_redirect(
            "https://example.com/", "https://u:p@example.com/") is False


class TestPreapproved:
    def test_docs_python_is_preapproved(self):
        assert is_preapproved_url("https://docs.python.org/3/") is True

    def test_random_host_is_not(self):
        assert is_preapproved_url("https://evil.example/") is False

    def test_github_anthropics_path_is_preapproved(self):
        assert is_preapproved_url(
            "https://github.com/anthropics/claude-code") is True

    def test_other_github_org_is_not(self):
        assert is_preapproved_url("https://github.com/torvalds/linux") is False


class TestSecondaryPrompt:
    def test_preapproved_allows_excerpts(self):
        text = make_secondary_model_prompt("# Hi", "summarize", True)
        assert "Web page content:" in text
        assert "summarize" in text
        assert "code examples" in text

    def test_non_preapproved_enforces_quote_cap(self):
        text = make_secondary_model_prompt("# Hi", "extract", False)
        assert "125-character" in text


class TestDomainBlocklist:
    def test_allowed_is_cached(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"can_fetch": True})

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        assert check_domain_blocklist("example.com", client=client).status == "allowed"
        assert check_domain_blocklist("example.com", client=client).status == "allowed"
        assert calls["n"] == 1

    def test_blocked_raises_status(self):
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"can_fetch": False}))
        client = httpx.Client(transport=transport)
        assert check_domain_blocklist("bad.com", client=client).status == "blocked"

    def test_network_failure_is_check_failed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert check_domain_blocklist("x.com", client=client).status == "check_failed"


class TestFetchRedirects:
    def test_same_host_redirect_is_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(
                    301, headers={"Location": "https://example.com/new"})
            return httpx.Response(
                200, content=b"# ok",
                headers={"content-type": "text/markdown"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        resp = get_with_permitted_redirects(
            "https://example.com/old", client=client)
        assert not isinstance(resp, dict)
        assert resp.status_code == 200

    def test_cross_host_redirect_is_returned_as_info(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302, headers={"Location": "https://other.com/x"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        resp = get_with_permitted_redirects(
            "https://example.com/a", client=client)
        assert isinstance(resp, dict)
        assert resp["type"] == "redirect"
        assert resp["redirectUrl"] == "https://other.com/x"
        assert resp["statusCode"] == 302


class TestGetURLMarkdownContent:
    def _client(self, handler):
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_html_is_converted_to_markdown(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "domain_info" in str(request.url):
                return httpx.Response(200, json={"can_fetch": True})
            return httpx.Response(
                200,
                content=b"<html><body><h1>Title</h1><p>Hello</p></body></html>",
                headers={"content-type": "text/html; charset=utf-8"},
            )

        result = get_url_markdown_content(
            "https://example.com/page",
            client=self._client(handler),
            skip_preflight=True,
        )
        assert "type" not in result or result.get("type") != "redirect"
        assert "Title" in result["content"]
        assert result["code"] == 200

    def test_http_is_upgraded_to_https(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(
                200, content=b"ok",
                headers={"content-type": "text/plain"})

        get_url_markdown_content(
            "http://example.com/x",
            client=self._client(handler),
            skip_preflight=True,
        )
        assert any(u.startswith("https://") for u in seen)

    def test_cache_avoids_second_fetch(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(
                200, content=b"body",
                headers={"content-type": "text/plain"})

        client = self._client(handler)
        a = get_url_markdown_content(
            "https://example.com/cached", client=client, skip_preflight=True)
        b = get_url_markdown_content(
            "https://example.com/cached", client=client, skip_preflight=True)
        assert a["content"] == b["content"] == "body"
        assert calls["n"] == 1

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            get_url_markdown_content("https://localhost/", skip_preflight=True)

    def test_blocked_domain_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"can_fetch": False})

        with pytest.raises(DomainBlockedError):
            get_url_markdown_content(
                "https://blocked.example/x",
                client=self._client(handler),
                skip_preflight=False,
            )

    def test_failed_preflight_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        with pytest.raises(DomainCheckFailedError):
            get_url_markdown_content(
                "https://example.com/x",
                client=self._client(handler),
                skip_preflight=False,
            )

    def test_markdown_truncation_constant(self):
        assert MAX_MARKDOWN_LENGTH == 100_000
