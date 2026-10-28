"""The payload codec: what the server stores is ciphertext; keys rotate; plain still reads."""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.exceptions import InvalidTag
from temporalio.api.common.v1 import Payload
from temporalio.converter import DefaultFailureConverterWithEncodedAttributes

from supervisor.temporal.codec import (
    ENCODING,
    AesGcmCodec,
    CodecKeyError,
    data_converter,
    parse_key,
)
from supervisor.temporal.contracts import Message


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


async def test_a_sealed_payload_holds_no_plaintext_and_opens_again() -> None:
    converter = data_converter(_key())
    message = Message("c1", "deploy with the token sk-live-123", trace_id=None)
    payloads = await converter.encode([message])
    assert all(p.metadata["encoding"] == ENCODING for p in payloads)
    assert b"sk-live-123" not in b"".join(p.SerializeToString() for p in payloads)
    (back,) = await converter.decode(payloads, [Message])
    assert back == message


async def test_old_keys_open_what_they_sealed_and_new_payloads_use_the_new_key() -> None:
    old, new = _key(), _key()
    sealed = await data_converter(old).encode(["before the rotation"])
    rotated = data_converter(new, [old])
    assert await rotated.decode(sealed, [str]) == ["before the rotation"]
    resealed = await rotated.encode(["after"])
    with pytest.raises(CodecKeyError, match="TEMPORAL_CODEC_OLD_KEYS"):
        await data_converter(old).decode(resealed, [str])  # the old key alone cannot open it


async def test_a_plain_payload_still_reads() -> None:
    plain = await data_converter().encode(["written before a key was set"])
    assert all(p.metadata.get("encoding") != ENCODING for p in plain)
    assert await data_converter(_key()).decode(plain, [str]) == ["written before a key was set"]


def test_without_a_key_nothing_changes_and_with_one_failures_are_encoded() -> None:
    from temporalio.contrib.pydantic import pydantic_data_converter

    assert data_converter() is pydantic_data_converter
    assert data_converter(None, [" "]) is pydantic_data_converter
    with_key = data_converter(_key())
    assert isinstance(with_key.payload_codec, AesGcmCodec)
    assert isinstance(with_key.failure_converter, DefaultFailureConverterWithEncodedAttributes)


def test_keys_are_32_bytes_as_base64_or_hex() -> None:
    raw = os.urandom(32)
    assert parse_key(base64.b64encode(raw).decode()) == raw
    assert parse_key(raw.hex()) == raw
    for bad in ("short", base64.b64encode(os.urandom(16)).decode(), ""):
        with pytest.raises(CodecKeyError):
            parse_key(bad)


async def test_a_tampered_payload_is_refused() -> None:
    converter = data_converter(_key())
    (payload,) = await converter.encode(["x"])
    data = bytearray(payload.data)
    data[-1] ^= 1
    with pytest.raises(InvalidTag):
        await converter.decode([Payload(metadata=dict(payload.metadata), data=bytes(data))], [str])


async def test_with_old_keys_only_it_opens_sealed_payloads_and_writes_plain() -> None:
    """Step 1 of adding a key: every process can open it before any process seals with it."""
    key = _key()
    sealed = await data_converter(key).encode(["sealed by a process that has it"])
    opening = data_converter(None, [key])
    assert (await opening.decode(sealed, [str])) == ["sealed by a process that has it"]
    written = await opening.encode(["still plain"])
    assert all(p.metadata["encoding"] != ENCODING for p in written)
    assert opening.failure_converter_class is not DefaultFailureConverterWithEncodedAttributes


def test_a_key_with_a_stray_character_is_refused_not_read_as_another_key() -> None:
    key = _key()
    with pytest.raises(CodecKeyError):
        parse_key(key[:20] + "*" + key[20:])


def test_the_settings_never_show_the_keys() -> None:
    from supervisor.config import TemporalSettings

    key = _key()
    shown = repr(TemporalSettings(codec_key=key, codec_old_keys=(key,), api_key="secret-api-key"))
    assert key not in shown and "secret-api-key" not in shown


async def test_a_sealed_failure_is_read_with_the_key() -> None:
    from temporalio.api.failure.v1 import Failure
    from temporalio.exceptions import ApplicationError

    from supervisor.temporal.sessions import failure_text

    converter = data_converter(_key())
    failure = Failure()
    await converter.encode_failure(ApplicationError("the sandbox could not start"), failure)
    assert failure.message == "Encoded failure"  # what the server holds
    assert await failure_text(converter, failure) == "the sandbox could not start"
    assert await failure_text(data_converter(), failure) is not None  # no key: no crash
