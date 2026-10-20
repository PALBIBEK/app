"""AES-256-GCM with a keyring, so keys can rotate without re-encrypting at once.

``VAULT_KEYS`` holds ``key_id:base64-of-32-bytes`` pairs separated by commas;
the first key encrypts, every listed key decrypts. Rotation: put a new key first,
keep the old ones until every row written with them has been rewritten.

Without ``VAULT_KEYS`` a development key derived from a fixed label is used, and
only outside production and staging; there the vault refuses to start.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

__all__ = ["Keyring", "Sealed", "VaultKeyError"]

_NONCE_BYTES: Final = 12
_DEV_KEY_ID: Final = "dev"
_STRICT_ENVS: Final = frozenset({"production", "prod", "staging"})


class VaultKeyError(RuntimeError):
    """The vault has no usable key, or a ciphertext names a key it does not hold."""


@dataclass(frozen=True, slots=True)
class Sealed:
    key_id: str
    nonce: bytes
    ciphertext: bytes


class Keyring:
    """Named AES-256-GCM keys; ``active`` seals, any of them opens."""

    __slots__ = ("_keys", "active")

    def __init__(self, keys: dict[str, bytes], *, active: str) -> None:
        if active not in keys:
            raise VaultKeyError(f"active vault key {active!r} is not in the keyring")
        for key_id, key in keys.items():
            if len(key) != 32:
                raise VaultKeyError(f"vault key {key_id!r} must be 32 bytes, got {len(key)}")
        self._keys = {key_id: AESGCM(key) for key_id, key in keys.items()}
        self.active = active

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Keyring:
        env = os.environ if environ is None else environ
        spec = (env.get("VAULT_KEYS") or "").strip()
        if not spec:
            deployment = (env.get("ENV") or env.get("ENVIRONMENT") or "").lower()
            if deployment in _STRICT_ENVS:
                raise VaultKeyError("VAULT_KEYS must be set in production and staging")
            logger.warning("VAULT_KEYS is not set: using the development vault key")
            dev = hashlib.sha256(b"yal-supervisor development vault key").digest()
            return cls({_DEV_KEY_ID: dev}, active=_DEV_KEY_ID)
        keys: dict[str, bytes] = {}
        order: list[str] = []
        for part in spec.split(","):
            key_id, sep, encoded = part.strip().partition(":")
            if not sep or not key_id:
                raise VaultKeyError("VAULT_KEYS entries are key_id:base64key")
            try:
                keys[key_id] = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise VaultKeyError(f"vault key {key_id!r} is not valid base64") from exc
            order.append(key_id)
        return cls(keys, active=order[0])

    @staticmethod
    def new_key() -> str:
        """A fresh key, base64, for ``VAULT_KEYS``."""
        return base64.b64encode(secrets.token_bytes(32)).decode()

    def seal(self, plaintext: bytes, *, binding: bytes) -> Sealed:
        """Encrypt ``plaintext``, authenticated together with ``binding`` (its slot)."""
        nonce = secrets.token_bytes(_NONCE_BYTES)
        sealed = self._keys[self.active].encrypt(nonce, plaintext, binding)
        return Sealed(self.active, nonce, sealed)

    def open(self, sealed: Sealed, *, binding: bytes) -> bytes:
        """Decrypt; raises if the key is unknown or the ciphertext or binding was changed."""
        key = self._keys.get(sealed.key_id)
        if key is None:
            raise VaultKeyError(f"vault key {sealed.key_id!r} is not in the keyring")
        return key.decrypt(sealed.nonce, sealed.ciphertext, binding)
