import time

from app.services.redaction import Redactor, redact


def test_empty_and_whitespace_unchanged() -> None:
    assert redact("") == ""
    assert redact("   \n\t ") == "   \n\t "


def test_email() -> None:
    out = redact("user alice.smith+tag@example.co.uk failed login")
    assert "alice" not in out
    assert "<EMAIL>" in out


def test_api_keys() -> None:
    out = redact(
        "a sk-proj1234567890abcdefghij b AKIAIOSFODNN7EXAMPLE c ghp_" + "a1" * 15
    )
    assert "sk-proj" not in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "ghp_a1" not in out
    assert out.count("<API_KEY>") == 3


def test_jwt() -> None:
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PsFYnUgvvfEk"
    )
    out = redact(f"token was {jwt} here")
    assert "eyJ" not in out
    assert "<JWT>" in out


def test_bearer_keeps_prefix() -> None:
    out = redact("Authorization: Bearer abc123def456ghi789")
    assert "abc123def456ghi789" not in out
    assert "Bearer <TOKEN>" in out


def test_key_value_and_json() -> None:
    assert redact("password=hunter2") == "password=<SECRET>"
    assert redact("db_password: hunter2 next") == "db_password: <SECRET> next"
    assert redact('{"client_secret": "s3cr3t value", "ok": 1}') == (
        '{"client_secret": "<SECRET>", "ok": 1}'
    )
    assert redact("api_key='abc 123'") == "api_key='<SECRET>'"


def test_card_luhn() -> None:
    assert redact("card 4111 1111 1111 1111 declined") == "card <CARD> declined"
    assert redact("id 4111111111111112") == "id 4111111111111112"


def test_ipv4_stable_placeholders() -> None:
    out = redact("10.0.0.1 -> 10.0.0.2 -> 10.0.0.1")
    assert out == "<IP_1> -> <IP_2> -> <IP_1>"
    assert redact("999.1.1.1") == "999.1.1.1"


def test_ip_map_stable_across_calls() -> None:
    r = Redactor()
    assert r.redact("a 1.2.3.4") == "a <IP_1>"
    assert r.redact("b 5.6.7.8 c 1.2.3.4") == "b <IP_2> c <IP_1>"


def test_ipv6() -> None:
    for addr in ("2001:db8::1", "::1", "fe80::1ff:fe23:4567:890a"):
        out = redact(f"conn from {addr} closed")
        assert addr not in out
        assert "<IP_1>" in out


def test_timestamps_and_hashes_untouched() -> None:
    for s in (
        "12:30:45 started",
        "2024-01-01T10:20:30.123Z INFO ok",
        "sha " + "ab12" * 16,
        "aa:bb:cc:dd:ee:ff",
        "/usr/local/lib/python3.11/site-packages/AbCdEf/module.py",
    ):
        assert redact(s) == s


def test_base64_looking_secret() -> None:
    blob = "dGhpcyBpcyBhIHNlY3JldCB0b2tlbiB2YWx1ZQ1AbCdEfGhIjKlMnOp"
    out = redact(f"blob={blob}")
    assert blob not in out


def test_token_split_across_two_lines() -> None:
    text = "key sk-abcdefgh\nijklmnopqrstu\nINFO next"
    out = redact(text)
    assert "ijklmnopqrstu" not in out
    assert "abcdefgh" not in out
    assert "INFO next" in out
    assert out.count("\n") == text.count("\n")


def test_split_does_not_swallow_normal_next_line() -> None:
    text = "sk-short\nTraceback (most recent call last):"
    assert redact(text) == text


def test_idempotent_examples() -> None:
    text = (
        "user a@b.com password=x Bearer abc123def456 ip 10.1.1.1 "
        "sk-abcdefghijklmnopqrst 4111111111111111"
    )
    once = redact(text)
    assert redact(once) == once


def test_unicode_and_null_bytes_do_not_crash() -> None:
    s = "héllo 🔥 \x00 password=abc 日本語"
    out = redact(s)
    assert "abc" not in out
    assert "🔥" in out


def test_single_1mb_line_is_fast() -> None:
    for line in ("a" * 1_000_000, "1" * 1_000_000, "aB1" * 333_333, "a." * 500_000):
        start = time.perf_counter()
        redact(line)
        assert time.perf_counter() - start < 5.0