"""The vault's form finder (``supervisor.hands.browser.vault_forms``): Hermes's login
classifier tests (``tests/tools/test_browser_vault.py`` ``TestClassifier`` and the code-box
cases of ``TestTwoFactor``), and the scripts' guarantees."""

from __future__ import annotations

from typing import Any

from supervisor.hands.browser.vault_forms import (
    ClassifiedLoginControl,
    LoginControl,
    build_fill_js,
    build_otp_fills,
    classify_checkout_control,
    classify_login_control,
    classify_otp_controls,
    select_checkout_fills,
    select_identifier_fill,
    select_password_fill,
)
from supervisor.vault.logins import PAYMENT_FIELDS


def _ctrl(**kw: Any) -> LoginControl:
    base: dict[str, Any] = {
        "autocomplete": "", "form_index": 0, "index": 0, "label": "", "name": "", "type": "text"
    }
    base.update(kw)
    return LoginControl(**base)


def test_an_exact_autocomplete_match_maps_its_token() -> None:
    for token in ("username", "email", "tel", "current-password"):
        res = classify_login_control(_ctrl(autocomplete=token))
        assert res is not None and res.token == token


def test_a_new_password_or_a_code_field_is_never_a_logins() -> None:
    assert classify_login_control(_ctrl(autocomplete="new-password", type="password")) is None
    assert classify_login_control(_ctrl(autocomplete="one-time-code")) is None
    for label in ("New password", "Confirm Password", "create-password", "Repeat  password"):
        assert classify_login_control(_ctrl(type="password", label=label)) is None, label


def test_types_and_label_heuristics_map_tokens() -> None:
    assert classify_login_control(_ctrl(type="password")).token == "current-password"  # type: ignore[union-attr]
    assert classify_login_control(_ctrl(type="email")).token == "email"  # type: ignore[union-attr]
    assert classify_login_control(_ctrl(type="tel")).token == "tel"  # type: ignore[union-attr]
    assert classify_login_control(_ctrl(label="E-mail address")).token == "email"  # type: ignore[union-attr]
    assert classify_login_control(_ctrl(name="mobile_number")).token == "tel"  # type: ignore[union-attr]
    assert classify_login_control(_ctrl(label="Username or account")).token == "username"  # type: ignore[union-attr]
    assert classify_login_control(_ctrl(label="Search the docs")) is None


def test_the_best_password_field_and_only_one_is_filled() -> None:
    user = ClassifiedLoginControl(_ctrl(index=0, autocomplete="username"), 100, "username")
    heuristic = ClassifiedLoginControl(_ctrl(index=1, type="password"), 90, "current-password")
    exact = ClassifiedLoginControl(
        _ctrl(index=3, autocomplete="current-password"), 100, "current-password"
    )
    second = ClassifiedLoginControl(_ctrl(index=4, type="password"), 90, "current-password")

    assert [
        (f["index"], f["token"]) for f in select_password_fill([user, heuristic, exact], "p")
    ] == [(3, "current-password")]
    assert select_password_fill([user], "p") == []
    assert [f["index"] for f in select_password_fill([heuristic, second], "p")] == [1]


def test_the_identifier_goes_into_the_best_identifier_field() -> None:
    email = ClassifiedLoginControl(_ctrl(index=0, type="email"), 85, "email")
    exact = ClassifiedLoginControl(_ctrl(index=2, autocomplete="username"), 100, "username")
    password = ClassifiedLoginControl(_ctrl(index=3, type="password"), 90, "current-password")

    assert select_identifier_fill([email, exact, password], "ada") == [
        {"index": 2, "token": "username", "value": "ada"}
    ]
    assert select_identifier_fill([password], "ada") == []


def test_a_card_maps_to_its_fields_and_a_combined_expiry_replaces_month_and_year() -> None:
    controls = [
        _ctrl(index=0, autocomplete="cc-number"),
        _ctrl(index=1, name="cvv"),
        _ctrl(index=2, label="Expiry (MM / YY)"),
        _ctrl(index=3, autocomplete="cc-exp-month"),
        _ctrl(index=4, type="password", name="card"),
    ]
    classified = [c for raw in controls if (c := classify_checkout_control(raw)) is not None]

    fills = select_checkout_fills(
        classified,
        {"card_number": "4242", "cvc": "731", "exp_month": "4", "exp_year": "2031"},
        PAYMENT_FIELDS,
    )

    assert {(f["token"], f["value"]) for f in fills} == {
        ("cc-number", "4242"),
        ("cc-csc", "731"),
        ("cc-exp", "04/31"),
    }
    assert all(f["index"] != 4 for f in fills), "a password input is never a checkout field"


def _otp(
    i: int, form: int = 0, maxlen: int | None = None, score: int = 70
) -> ClassifiedLoginControl:
    return ClassifiedLoginControl(
        LoginControl("", form, i, "", f"code{i}", "text", maxlen), score, "one-time-code"
    )


def test_a_code_goes_one_digit_per_box_only_into_an_unmistakable_digit_widget() -> None:
    """Hermes's reviewer case: code-like inputs scattered over a page (a promo code, a zip code,
    the real box) never get a digit each; only adjacent maxlength=1 boxes of one form do."""
    scattered = [_otp(0), _otp(3), _otp(7), _otp(9, form=1), _otp(12, score=100)]
    assert build_otp_fills(scattered, "246810") == [
        {"index": 12, "token": "one-time-code", "value": "246810"}
    ]
    assert len(build_otp_fills([_otp(i, form=i % 2, maxlen=1) for i in range(6)], "246810")) == 1
    assert len(build_otp_fills([_otp(i * 2, maxlen=1) for i in range(6)], "246810")) == 1
    assert len(build_otp_fills([_otp(i, maxlen=1) for i in range(5)], "246810")) == 1
    assert [
        f["value"] for f in build_otp_fills([_otp(i + 4, maxlen=1) for i in range(6)], "246810")
    ] == list("246810")


def test_code_fields_are_found_by_autocomplete_or_their_words() -> None:
    controls = [
        _ctrl(index=0, autocomplete="one-time-code"),
        _ctrl(index=1, label="Verification code"),
        _ctrl(index=2, label="Promo"),
        _ctrl(index=3, type="checkbox", name="otp"),
    ]
    assert [c.control.index for c in classify_otp_controls(controls)] == [0, 1]


def test_the_fill_script_binds_its_targets_to_its_inspection_and_leaves_no_marker() -> None:
    js = build_fill_js(
        [{"index": 0, "token": "current-password", "value": "x"}],
        expected_origin="https://example.com",
        nonce="n1",
    )

    assert "[data-githi-vault-slot=" in js and "nonce + ':' + f.index" in js
    assert 'f.token === "current-password" && el.type !== "password"' in js, (
        "a password never lands in a text box"
    )
    assert js.index('removeAttribute("data-githi-vault-slot")') > js.index("setter.set.call")


def test_the_fill_script_asserts_the_origin_before_any_write() -> None:
    js = build_fill_js(
        [{"index": 0, "token": "current-password", "value": "x"}],
        expected_origin="https://example.com",
    )

    assert '"https://example.com"' in js and "window.location.origin" in js
    assert js.index("origin_changed") < js.index("querySelectorAll")
