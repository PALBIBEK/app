"""Vendor keys set from the product, held by the gateway alone; and a local server's address.

A vendor's key comes from the environment (``OPENAI_API_KEY``, …) or is set through the
gateway's service-only API. A set key is sealed with AES-256-GCM and kept in one file the
gateway owns (``MODEL_GATEWAY_KEYS_FILE``); no other process stores or reads it. This
follows QM's model credential store (``src/model/model-credential-store.ts``):

- a set key wins over the environment's; removing it records the removal, so the vendor
  stays off even when the environment holds a key (``delete`` writes ``disabled: true``,
  ``resolve`` then returns nothing);
- a status says only whether a vendor is configured and where from (``admin`` /
  ``environment`` / ``absent``; here ``set`` / ``environment`` / ``absent``), never the key;
- the sealing key is derived from key material with HKDF for this one purpose
  (``deriveConnectorKey(material, "model-credentials")``): ``MODEL_GATEWAY_KEYS_SECRET``,
  else the gateway's service token.

The vendor id is the ciphertext's associated data, so a sealed key moved to another vendor's
entry does not open. Every gateway process sharing the file sees a change: a read re-loads the
file when it changed, and a write holds an exclusive lock and replaces the file atomically.

The same file keeps the address of a vendor whose address may be set from the product (only
a local server's, ``Vendor.address_settable``), in clear: an address is not a secret. A key is
checked against the address it is used with, so setting a new address drops a key set for the
old one: it must be set again, for the new server.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import secrets
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from supervisor.gateway.vendors import VENDORS, Vendor

__all__ = ["AddressStatus", "KeySource", "KeyStatus", "VendorKeys", "sealing_key"]

_FORMAT: Final = 1
_SEALED_PREFIX: Final = "v1:"
_NONCE_BYTES: Final = 12
_HKDF_INFO: Final = b"prime model-gateway vendor keys v1"

type KeySource = Callable[[], str]
"""A vendor's key as of now (empty when it has none): read on every call, so a key set or
removed through the API takes effect on the next request."""


def sealing_key(material: str) -> bytes:
    """The 32-byte AES key derived from ``material`` for vendor keys only."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        material.encode()
    )


@dataclass(frozen=True, slots=True)
class KeyStatus:
    vendor: str
    configured: bool
    source: Literal["set", "removed", "environment", "absent"]
    """``set``: a key set through the API; ``removed``: one removed through it (the
    environment's key is not used either); else where the environment left it."""
    updated_at: float | None = None
    updated_by: str | None = None


@dataclass(frozen=True, slots=True)
class AddressStatus:
    vendor: str
    base_url: str
    """The address now: the one set from the product, else the environment's (empty: none)."""
    source: Literal["set", "environment", "absent"]
    updated_at: float | None = None
    updated_by: str | None = None


@dataclass(frozen=True, slots=True)
class _Address:
    base_url: str
    updated_at: float
    updated_by: str


@dataclass(frozen=True, slots=True)
class _File:
    keys: dict[str, _Entry]
    addresses: dict[str, _Address]


@dataclass(frozen=True, slots=True)
class _Entry:
    sealed: str | None
    """``None`` when the key was removed."""
    updated_at: float
    updated_by: str


class VendorKeys:
    """Each vendor's key: the one set through the API, else the environment's."""

    def __init__(
        self,
        environment: Mapping[str, str],
        *,
        path: Path | None = None,
        secret: bytes | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        if (path is None) != (secret is None):
            raise ValueError("a key file needs a sealing key, and a sealing key a file")
        self._environment = {v: k.strip() for v, k in environment.items()}
        self._path = path
        self._aead = AESGCM(secret) if secret is not None else None
        self._now = now
        self._lock = threading.Lock()
        self._file = _File({}, {})
        self._seen: tuple[int, int, int] | None = None

    @property
    def editable(self) -> bool:
        """Whether keys can be set through the API (a key file is configured)."""
        return self._path is not None

    # -- reading ---------------------------------------------------------------------------

    def key(self, vendor: str) -> str:
        """The vendor's key now, or empty."""
        entry = self._current().keys.get(vendor)
        if entry is None:
            return self._environment.get(vendor, "")
        return "" if entry.sealed is None else self._open(vendor, entry.sealed)

    def source(self, vendor: str) -> KeySource:
        return lambda: self.key(vendor)

    def keyed(self) -> set[str]:
        """The vendors that have a key now."""
        return {vendor for vendor in VENDORS if self.key(vendor)}

    def status(self, vendor: str) -> KeyStatus:
        entry = self._current().keys.get(vendor)
        if entry is not None:
            return KeyStatus(
                vendor,
                configured=entry.sealed is not None,
                source="set" if entry.sealed is not None else "removed",
                updated_at=entry.updated_at,
                updated_by=entry.updated_by,
            )
        if self._environment.get(vendor):
            return KeyStatus(vendor, configured=True, source="environment")
        return KeyStatus(vendor, configured=False, source="absent")

    # -- writing ---------------------------------------------------------------------------

    def set(self, vendor: Vendor, key: str, *, by: str) -> KeyStatus:
        key = key.strip()
        if not key:
            raise ValueError("the key is empty")
        self._require_file()
        self._write(vendor.id, _Entry(self._seal(vendor.id, key), self._now(), by))
        return self.status(vendor.id)

    def remove(self, vendor: Vendor, *, by: str) -> KeyStatus:
        self._write(vendor.id, _Entry(None, self._now(), by))
        return self.status(vendor.id)

    # -- a local server's address ------------------------------------------------------------

    def address(self, vendor: Vendor, environment: str) -> AddressStatus:
        """``vendor``'s address now: the one set here, else ``environment`` (the deployment's)."""
        entry = self._current().addresses.get(vendor.id) if vendor.address_settable else None
        if entry is not None:
            return AddressStatus(vendor.id, entry.base_url, "set", entry.updated_at, entry.updated_by)
        return AddressStatus(vendor.id, environment, "environment" if environment else "absent")

    def address_source(self, vendor: Vendor, environment: str) -> Callable[[], str]:
        return lambda: self.address(vendor, environment).base_url

    def set_address(self, vendor: Vendor, base_url: str, *, by: str) -> None:
        """Set ``vendor``'s address (a normalized http(s) URL; empty turns it off). A key set
        for its old address is dropped: it was checked against another server."""
        if not vendor.address_settable:
            raise PermissionError(f"{vendor.label}'s address is the deployment's, not set here")
        path = self._require_file()
        with self._lock, _exclusive(path):
            current = _parse(path.read_bytes()) if path.exists() else _File({}, {})
            before = current.addresses.get(vendor.id)
            if before is None or before.base_url != base_url:
                current.keys.pop(vendor.id, None)
            current.addresses[vendor.id] = _Address(base_url, self._now(), by)
            self._store(path, current)

    # -- the file --------------------------------------------------------------------------

    def _current(self) -> _File:
        """The file's entries, re-read when it changed since the last read."""
        if self._path is None:
            return _File({}, {})
        with self._lock:
            try:
                stat = self._path.stat()
            except FileNotFoundError:
                self._file, self._seen = _File({}, {}), None
                return self._file
            seen = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if seen != self._seen:
                self._file, self._seen = _parse(self._path.read_bytes()), seen
            return self._file

    def _write(self, vendor: str, entry: _Entry) -> None:
        path = self._require_file()
        with self._lock, _exclusive(path):
            current = _parse(path.read_bytes()) if path.exists() else _File({}, {})
            current.keys[vendor] = entry
            self._store(path, current)

    def _store(self, path: Path, current: _File) -> None:
        """Replace the file with ``current`` (under the lock), and keep it as what was read."""
        _replace(path, _dump(current))
        stat = path.stat()
        self._file, self._seen = current, (stat.st_ino, stat.st_mtime_ns, stat.st_size)

    def _require_file(self) -> Path:
        if self._path is None:
            raise PermissionError(
                "vendor keys are set in the environment (MODEL_GATEWAY_KEYS_FILE is unset)"
            )
        return self._path

    def _seal(self, vendor: str, key: str) -> str:
        assert self._aead is not None
        nonce = secrets.token_bytes(_NONCE_BYTES)
        ciphertext = self._aead.encrypt(nonce, key.encode(), vendor.encode())
        return _SEALED_PREFIX + base64.b64encode(nonce + ciphertext).decode()

    def _open(self, vendor: str, sealed: str) -> str:
        assert self._aead is not None
        if not sealed.startswith(_SEALED_PREFIX):
            raise ValueError(f"the key file's entry for {vendor} is not a sealed key")
        raw = base64.b64decode(sealed.removeprefix(_SEALED_PREFIX))
        try:
            return self._aead.decrypt(
                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], vendor.encode()
            ).decode()
        except InvalidTag:
            raise ValueError(
                f"the key file's entry for {vendor} does not open with this gateway's sealing key"
            ) from None


def _parse(raw: bytes) -> _File:
    """The file's keys and addresses (a file of keys alone, as earlier gateways wrote, reads
    as having no address set)."""
    document: Any = json.loads(raw) if raw.strip() else {"format": _FORMAT, "vendors": {}}
    if not isinstance(document, dict) or document.get("format") != _FORMAT:
        raise ValueError("the vendor key file is not in a format this gateway reads")
    keys: dict[str, _Entry] = {}
    for vendor, row in (document.get("vendors") or {}).items():
        if vendor in VENDORS and isinstance(row, dict):
            keys[vendor] = _Entry(row.get("sealed"), float(row["updated_at"]), str(row["updated_by"]))
    addresses: dict[str, _Address] = {}
    for vendor, row in (document.get("addresses") or {}).items():
        if vendor in VENDORS and VENDORS[vendor].address_settable and isinstance(row, dict):
            addresses[vendor] = _Address(str(row["base_url"]), float(row["updated_at"]), str(row["updated_by"]))
    return _File(keys, addresses)


def _dump(current: _File) -> bytes:
    vendors = {
        vendor: {"sealed": e.sealed, "updated_at": e.updated_at, "updated_by": e.updated_by}
        for vendor, e in sorted(current.keys.items())
    }
    document: dict[str, Any] = {"format": _FORMAT, "vendors": vendors}
    if current.addresses:
        document["addresses"] = {
            vendor: {"base_url": a.base_url, "updated_at": a.updated_at, "updated_by": a.updated_by}
            for vendor, a in sorted(current.addresses.items())
        }
    return json.dumps(document, indent=2).encode()


@contextmanager
def _exclusive(path: Path) -> Iterator[None]:
    """Hold the key file's write lock (a sibling ``.lock`` file) across processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _replace(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, readable by this user only."""
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
