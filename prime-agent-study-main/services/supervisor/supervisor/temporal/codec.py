"""Encrypted workflow history: an AES-GCM payload codec (pi-temporal's ``codec.ts``).

What a session's workflow carries through Temporal (messages the user sent, final
answers, error text, a run's request and result payloads) is stored by the server.
Coding work can put code or secrets there, so with ``TEMPORAL_CODEC_KEY`` set every
client and worker seals each payload before it leaves the process, and the server
stores only ciphertext. Failures' messages and stack traces are moved into a payload
too (``DefaultFailureConverterWithEncodedAttributes``), since a tool's or provider's
error can quote the task.

* A payload names the key that sealed it (the first 16 hex digits of the key's
  SHA-256), so keys rotate: set the new one as ``TEMPORAL_CODEC_KEY`` and keep the old
  ones in ``TEMPORAL_CODEC_OLD_KEYS`` (comma-separated) on every client and worker until
  every run that started before the rotation has closed and its retention has passed.
* A plain payload still reads, so history written before a key was set stays readable
  (and the release replay gate's recorded histories replay). This protects what the
  server stores; it is not a guard against someone who can write history directly.
* Search attributes are never encrypted, and a session's memo (its tenant and
  principal, which authorise reading it) is stored plain on purpose, so a key's
  rotation never locks a running session out (``sessions._plain_memo``).

Keys are 32 bytes (AES-256), given as base64 or 64 hex digits.

A process opens only what a key it holds sealed, so a key is added and made current in
two rolling restarts, never one:

1. Turning encryption on, or rotating: add the new key to ``TEMPORAL_CODEC_OLD_KEYS`` on
   every client and worker (with no ``TEMPORAL_CODEC_KEY``, a process opens sealed
   payloads but writes plain ones). Once every process has restarted, each can open it.
2. Make it ``TEMPORAL_CODEC_KEY`` everywhere (keeping the previous one in
   ``TEMPORAL_CODEC_OLD_KEYS``) and restart again. Only now is anything sealed with it.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import os
from collections.abc import Iterable, Sequence
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from temporalio.api.common.v1 import Payload
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.converter import (
    DataConverter,
    DefaultFailureConverterWithEncodedAttributes,
    PayloadCodec,
)

__all__ = ["ENCODING", "AesGcmCodec", "CodecKeyError", "data_converter", "parse_key"]

ENCODING: Final = b"binary/encrypted"
_KEY_ID: Final = "encryption-key-id"
_NONCE_BYTES: Final = 12


class CodecKeyError(ValueError):
    """A codec key that is not 32 bytes of base64 or hex."""


def parse_key(text: str) -> bytes:
    """A 32-byte key from base64 or 64 hex digits."""
    value = text.strip()
    candidates: list[bytes] = []
    if len(value) == 64:
        try:
            candidates.append(bytes.fromhex(value))
        except ValueError:
            pass
    try:  # validated: a stray character is an error, never a different key
        candidates.append(base64.b64decode(value + "=" * (-len(value) % 4), validate=True))
    except (binascii.Error, ValueError):
        pass
    for key in candidates:
        if len(key) == 32:
            return key
    raise CodecKeyError("a codec key must be 32 bytes, as base64 or 64 hex digits")


def key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


class AesGcmCodec(PayloadCodec):
    """Seals payloads with the current key; opens them with it or any old key. With no
    current key it only opens (step 1 of adding a key): what it writes stays plain."""

    def __init__(self, key: bytes | None, old_keys: Sequence[bytes] = ()) -> None:
        self._current = None if key is None else (key_id(key), AESGCM(key))
        self._keys = {key_id(k): AESGCM(k) for k in (*old_keys, *([key] if key else []))}

    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        if self._current is None:
            return list(payloads)
        kid, aead = self._current
        out: list[Payload] = []
        for payload in payloads:
            nonce = os.urandom(_NONCE_BYTES)
            sealed = aead.encrypt(nonce, payload.SerializeToString(), kid.encode())
            out.append(
                Payload(
                    metadata={"encoding": ENCODING, _KEY_ID: kid.encode()},
                    data=nonce + sealed,
                )
            )
        return out

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        out: list[Payload] = []
        for payload in payloads:
            if payload.metadata.get("encoding") != ENCODING:
                out.append(payload)  # written before a key was set: plain, still readable
                continue
            kid = payload.metadata.get(_KEY_ID, b"").decode()
            aead = self._keys.get(kid)
            if aead is None:
                raise CodecKeyError(
                    f"a payload was sealed with key {kid or '(unnamed)'}, which this process "
                    "does not have: set it in TEMPORAL_CODEC_OLD_KEYS"
                )
            nonce, sealed = payload.data[:_NONCE_BYTES], payload.data[_NONCE_BYTES:]
            opened = Payload()
            opened.ParseFromString(aead.decrypt(nonce, sealed, kid.encode()))
            out.append(opened)
        return out


def data_converter(key: str | None = None, old_keys: Iterable[str] = ()) -> DataConverter:
    """The converter every client and worker must share: pydantic's, sealed when a key is set.

    With a key, failures' messages and stack traces move into a sealed payload too. A
    process without a key still reads a plain failure, and reads an encoded one's
    message once it has the key. With old keys only, it opens what they sealed and
    writes plain; with neither, nothing changes from pydantic's.
    """
    olds = [parse_key(k) for k in old_keys if k.strip()]
    if not key and not olds:
        return pydantic_data_converter
    codec = AesGcmCodec(parse_key(key) if key else None, olds)
    if not key:
        return dataclasses.replace(pydantic_data_converter, payload_codec=codec)
    return dataclasses.replace(
        pydantic_data_converter,
        payload_codec=codec,
        failure_converter_class=DefaultFailureConverterWithEncodedAttributes,
    )
