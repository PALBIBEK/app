"""What the browser may be sent to: Hermes's URL policy for ``browser_exec`` and ``browser_cdp``
(``tools/browser_url_policy.py``, with the floors of ``tools/url_safety.py`` it uses).

Before a call runs, the URL literals in its code (or a raw CDP command's parameters) are
read, as Hermes does (a ``fetch`` to a private host never shows in ``location.href``), and
judged by ordered floors:

1. **cloud metadata is refused, always** (169.254.169.254 and the other IMDS addresses, the
   whole link-local range, ``metadata.google.internal``, in IPv4-mapped and numeric
   spellings too): a browser on a cloud VM reaches the host's credentials there, whatever
   browser it is;
2. **a URL with an API key or token in it is refused**: an injected instruction could
   otherwise send a secret out in a URL;
3. **a private address** (loopback, LAN, ``*.local``, CGNAT) **on a hosted browser** is
   Hermes's hybrid routing: such a call runs on the sandbox's own browser instead (a
   provider cannot reach this network, and must not be pointed into it), which reaches what
   the sandbox's network allows.

The sandbox's own browser reaches only what its profile's network allows, so a private
address there is the network's decision, not this policy's.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "CodeVerdict",
    "cdp_refusal",
    "code_verdict",
    "literal_targets_metadata",
    "targets_metadata",
    "url_is_private",
    "url_literals",
]

logger = logging.getLogger(__name__)

_BLOCKED_HOSTNAMES: Final = frozenset({"metadata.google.internal", "metadata.goog"})
_METADATA_V4: Final = (
    "169.254.169.254",  # AWS / GCP / Azure / DigitalOcean / Oracle metadata
    "169.254.170.2",  # AWS ECS task metadata (task IAM credentials)
    "169.254.169.253",  # Azure IMDS wire server
    "100.100.100.200",  # Alibaba Cloud metadata
)
_ALWAYS_BLOCKED_IPS: Final = frozenset(
    {ipaddress.ip_address(ip) for ip in _METADATA_V4}
    | {ipaddress.ip_address("::ffff:" + ip) for ip in _METADATA_V4}
    | {ipaddress.ip_address("fd00:ec2::254")}  # AWS metadata (IPv6)
)
_ALWAYS_BLOCKED_NETWORKS: Final = tuple(
    ipaddress.ip_network(n) for n in ("169.254.0.0/16", "::ffff:169.254.0.0/112")
)
_CGNAT: Final = ipaddress.ip_network("100.64.0.0/10")
_PRIVATE_HOST_SUFFIXES: Final = (".localhost", ".local", ".lan", ".internal")

# URL-shaped literals in code (http/https only); a literal ends at a quote, whitespace, ``)``,
# ``]`` or ``<>``, except that one ``[...]`` segment (a bracketed IPv6 host) is read whole.
_URL_BODY: Final = r"""(?=[^\s'"`)\]<>])(?:[^\s'"`)\[\]<>]*\[[^\s'"`)\[\]<>]*\])?[^\s'"`)\]<>]*"""
_URL_LITERAL_RE: Final = re.compile(r"https?://" + _URL_BODY, re.IGNORECASE)
# Plus scheme-relative ``//host``, ``///host`` or ``\\host`` (browsers fetch each with the page's
# scheme and read ``\`` as ``/``); not ``a//b`` or ``// note``.
_URL_OR_SCHEME_RELATIVE_RE: Final = re.compile(
    r"""(?:https?://|(?<![\w:/\\])[/\\]{2,}(?=[^/\\\s]))""" + _URL_BODY, re.IGNORECASE
)
_URL_BODY_RE: Final = re.compile(_URL_BODY)
_USERINFO_TAIL_RE: Final = re.compile(r"""[^\s'"`/?#\\]*@""")
_LITERAL_HEAD_RE: Final = re.compile(r"(?:https?:)?[/\\]+", re.IGNORECASE)
_NUMERIC_HOST_RE: Final = re.compile(r"[0-9a-fx.]+")

# Hermes's vendor key prefixes (``agent/redact.py``): a key in a URL is a key leaving.
_KEY_PATTERNS: Final = (
    r"sk-[A-Za-z0-9_-](?:\.?[A-Za-z0-9_-]){9,}",
    r"ghp_[A-Za-z0-9]{10,}",
    r"github_pat_[A-Za-z0-9_]{10,}",
    r"gh[ousr]_[A-Za-z0-9]{10,}",
    r"xapp-\d+-[A-Za-z0-9-]{10,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"AIza[A-Za-z0-9_-]{30,}",
    r"AKIA[A-Z0-9]{16}",
    r"sk_(?:live|test)_[A-Za-z0-9]{10,}",
    r"rk_live_[A-Za-z0-9]{10,}",
    r"SG\.[A-Za-z0-9_-]{10,}",
    r"hf_[A-Za-z0-9]{10,}",
    r"npm_[A-Za-z0-9]{10,}",
    r"pypi-[A-Za-z0-9_-]{10,}",
    r"glpat-[A-Za-z0-9_\-]{10,}",
    r"gsk_[A-Za-z0-9]{10,}",
    r"xai-[A-Za-z0-9]{30,}",
    r"bb_live_[A-Za-z0-9_-]{10,}",
    r"fc-[A-Za-z0-9]{10,}",
    r"pplx-[A-Za-z0-9]{10,}",
    r"dop_v1_[A-Za-z0-9]{10,}",
)
_KEY_RE: Final = re.compile(
    r"(?<![A-Za-z0-9_-])(" + "|".join(_KEY_PATTERNS) + r")(?![A-Za-z0-9_-])"
)


def _embedded_ipv4(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """The IPv4 address an IPv4-mapped or -translated IPv6 address carries, else ``ip``."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _always_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip in _ALWAYS_BLOCKED_IPS
        or _embedded_ipv4(ip) in _ALWAYS_BLOCKED_IPS
        or any(_embedded_ipv4(ip) in net or ip in net for net in _ALWAYS_BLOCKED_NETWORKS)
    )


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """``host`` as an IP address, browsers' numeric IPv4 spellings included (decimal, hex,
    octal, short: ``0xa9fea9fe``, ``2852039166``); None for a name."""
    host = host.strip("[]")
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _NUMERIC_HOST_RE.fullmatch(host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except (OSError, ValueError):
            return None
    return None


def _hostname(url: str) -> str:
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return urllib.parse.unquote(host).strip().lower().rstrip(".")


def targets_metadata(url: str) -> bool:
    """Whether ``url`` reaches the cloud-metadata floor, by its host as written or as it
    resolves (a name that answers 169.254.169.254 is refused too)."""
    host = _hostname(url)
    if not host:
        return False
    if host in _BLOCKED_HOSTNAMES:
        return True
    ip = _literal_ip(host)
    if ip is not None:
        return _always_blocked_ip(ip)
    try:
        answers = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    return any(
        _always_blocked_ip(ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0]))
        for *_, sockaddr in answers
    )


def literal_targets_metadata(literal: str) -> bool:
    """Whether a URL literal hits the metadata floor; a scheme-relative one (``//host``) by its
    literal host only, never resolved, so a ``//TODO`` comment costs no DNS lookup."""
    if not literal.startswith(("/", "\\")):
        return targets_metadata(literal)
    host = _hostname("http://" + literal.lstrip("/\\").replace("\\", "/"))
    ip = _literal_ip(host)
    return host in _BLOCKED_HOSTNAMES or (ip is not None and _always_blocked_ip(ip))


def _private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    ip = _embedded_ipv4(ip)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip in _CGNAT


def url_is_private(url: str) -> bool:
    """Whether the URL's host is (or resolves to) a private, LAN, loopback or CGNAT address.
    A name that does not resolve is not private: the browser reports the failure itself."""
    host = _hostname(url)
    if not host:
        return False
    ip = _literal_ip(host)
    if ip is not None:
        return _private_ip(ip)
    if host == "localhost" or host.endswith(_PRIVATE_HOST_SUFFIXES):
        return True
    try:
        answers = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    return any(
        _private_ip(ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])) for *_, sockaddr in answers
    )


def url_literals(text: str, *, protocol_relative: bool = False) -> list[str]:
    """Every ``http(s)://`` literal in free text (code, an HTML snippet, a CDP parameter); with
    ``protocol_relative``, each scheme-relative ``//host/...`` one too. A literal cut inside its
    authority with an ``@`` ahead is also returned as the host a browser reads after that ``@``
    (``http://x)@host/`` -> ``http://host/``)."""
    if not isinstance(text, str):
        return []
    found: list[str] = []
    for m in (_URL_OR_SCHEME_RELATIVE_RE if protocol_relative else _URL_LITERAL_RE).finditer(text):
        found.append(m.group())
        head_match = _LITERAL_HEAD_RE.match(m.group())
        head = head_match.group() if head_match else ""
        if any(c in "/?#\\" for c in m.group()[len(head) :]):
            continue  # the authority ended inside the literal: its host is fixed
        tail = _USERINFO_TAIL_RE.match(text, m.end())
        host = tail and _URL_BODY_RE.match(text, tail.end())
        if host:
            found.append(head + host.group())
    return [literal.rstrip(".,;") for literal in found]


def _key_in(url: str) -> bool:
    return bool(_KEY_RE.search(url) or _KEY_RE.search(urllib.parse.unquote(url)))


@dataclass(frozen=True, slots=True)
class CodeVerdict:
    """What one pass over a call's URL literals decided."""

    refused: str | None = None
    """Why the call must not run (a floor it hits), or None."""
    local: bool = False
    """Whether it must run on the sandbox's own browser (it reaches a private address and the
    session's browser is hosted)."""


def code_verdict(code: str, *, hosted: bool) -> CodeVerdict:
    """Hermes's ``_code_url_verdict``: one pass over the code's URL literals. Each URL's
    private-address check runs ONCE and decides both the floor and the routing: it resolves
    DNS, and a rebinding name read twice could pass one and fail the other."""
    local = False
    for url in url_literals(code):
        if _key_in(url):
            return CodeVerdict(
                "Blocked: the URL holds what looks like an API key or token. Secrets must not be sent in URLs.",
                local,
            )
        if targets_metadata(url):
            return CodeVerdict(f"Blocked: the URL targets a cloud metadata endpoint ({url})", local)
        if hosted and url_is_private(url):
            local = True
    return CodeVerdict(None, local)


# browser_cdp ---------------------------------------------------------------------------------

_TEXT_INPUT_PREFIX: Final = "Input."
_NON_FETCHING_METHODS: Final = frozenset(
    {
        "Network.setBlockedURLs",
        "Network.setRequestInterception",
        "Fetch.enable",
        "Page.handleJavaScriptDialog",
        "Network.setCookie",
        "Network.setCookies",
        "Network.deleteCookies",
        "Storage.setCookies",
    }
)
"""Methods whose URL parameters are patterns or scopes, never fetched (Hermes's list)."""
_NAVIGATING: Final = frozenset({"Page.navigate", "Target.createTarget", "Runtime.evaluate"})


def _param_urls(value: Any, key: str = "") -> list[str]:
    """Every URL a CDP command's parameters name: each ``url`` value whole, and the
    ``http(s)://`` and ``//host`` literals in every string at any depth."""
    if isinstance(value, str):
        return ([value.strip()] if key == "url" and value.strip() else []) + url_literals(
            value, protocol_relative=True
        )
    if isinstance(value, dict):
        return [u for k, v in value.items() for u in _param_urls(v, str(k))]
    if isinstance(value, (list, tuple)):
        return [u for v in value for u in _param_urls(v, key)]
    return []


def cdp_refusal(method: str, params: dict[str, Any], *, hosted: bool) -> str | None:
    """Why a raw CDP command must not be sent (Hermes's ``_browser_cdp_private_guard``), or
    None: raw CDP shares ``browser_exec``'s floors and must not be their way around. Every URL
    the command could make the browser fetch is held to the metadata floor; on a hosted
    browser, a navigation (or any fetch) to a private address is refused (the sandbox's own
    browser serves those, through ``browser_exec``)."""
    if method.startswith(_TEXT_INPUT_PREFIX) or method in _NON_FETCHING_METHODS:
        return None
    urls = list(dict.fromkeys(_param_urls(params)))
    if metadata := next((u for u in urls if literal_targets_metadata(u)), None):
        return f"Blocked: CDP {method} targets a cloud metadata endpoint ({metadata})."
    if not hosted:
        return None
    private = next(
        (
            u
            for u in urls
            if u.lower().startswith(("http://", "https://"))
            and u.lower() != "about:blank"
            and url_is_private(u)
        ),
        None,
    )
    if private:
        what = "target is" if method in _NAVIGATING else "params target"
        return f"Blocked: CDP {method} {what} a private or internal address ({private}) the hosted browser must not be pointed at."
    return None
