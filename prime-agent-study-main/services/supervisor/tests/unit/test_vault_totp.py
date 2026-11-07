"""One-time codes from a saved authenticator key: Hermes's RFC 6238 test vectors."""

from __future__ import annotations

import pytest

from supervisor.vault.totp import InvalidOtpSecret, normalize_otp_secret, totp_now


def test_totp_matches_the_rfc6238_vectors_and_the_seed_is_normalized() -> None:
    seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
    assert totp_now(seed, digits=8, at=59) == "94287082"
    assert totp_now(seed, at=1111111109) == "081804"
    assert (
        normalize_otp_secret("otpauth://totp/GitHub:tek?secret=jbsw y3dp ehpk3pxp&issuer=GitHub")
        == "JBSWY3DPEHPK3PXP"
    )
    with pytest.raises(InvalidOtpSecret):
        normalize_otp_secret("not base32!")


def test_an_otpauth_links_own_parameters_are_kept_and_honoured() -> None:
    """RFC 6238's SHA-256, 8-digit vector at T=59: dropping the link's parameters would give wrong codes."""
    stored = normalize_otp_secret(
        f"otpauth://totp/x?secret={'GEZDGNBVGY3TQOJQ' * 2}&digits=8&period=30&algorithm=SHA256"
    )
    assert stored.endswith("|8|30|SHA256")
    sha256_seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZA"
    assert totp_now(sha256_seed + "|8|30|SHA256", at=59) == "46119246"
    assert totp_now("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ|6|60|SHA1", at=119) == totp_now(
        "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", period=60, at=119
    )


def test_counter_based_codes_and_odd_parameters_are_refused() -> None:
    with pytest.raises(InvalidOtpSecret, match="HOTP"):
        normalize_otp_secret("otpauth://hotp/x?secret=JBSWY3DPEHPK3PXP&counter=1")
    with pytest.raises(InvalidOtpSecret, match="unsupported"):
        normalize_otp_secret("otpauth://totp/x?secret=JBSWY3DPEHPK3PXP&digits=4")
    assert normalize_otp_secret("") == ""
