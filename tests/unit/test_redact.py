from app.services.redact import redact


def test_email_and_ip_redacted() -> None:
    out = redact("login failed for jane.doe@example.com from 10.12.3.45")
    assert "jane.doe" not in out and "10.12.3.45" not in out
    assert "[REDACTED_EMAIL]" in out and "[REDACTED_IP]" in out


def test_key_value_secrets_redacted() -> None:
    out = redact("connect failed password=hunter2 api_key: abc123 token='xyz'")
    assert "hunter2" not in out and "abc123" not in out and "xyz" not in out


def test_bearer_and_jwt_redacted() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123"
    out = redact(f"Authorization: Bearer {jwt} sent")
    assert jwt not in out and "eyJhbGci" not in out


def test_aws_key_and_long_token_redacted() -> None:
    out = redact("key AKIAIOSFODNN7EXAMPLE and blob " + "a1b2c3d4" * 6)
    assert "AKIAIOSFODNN7EXAMPLE" not in out and "a1b2c3d4a1b2c3d4" not in out


def test_valid_card_redacted_but_epoch_millis_kept() -> None:
    assert "[REDACTED_CARD]" in redact("charge 4111 1111 1111 1111 declined")
    assert "1700000000000" in redact("ts=1700000000000 job started")


def test_ordinary_log_lines_unchanged() -> None:
    lines = [
        "2026-11-14 05:16:24.381916 ERROR digest run failed: empty body",
        "db.pool timeout waiting for a free connection, pool exhausted (50 of 50 in use)",
        "token verification failed: signing key not found in key store",
        "config reload: PAYMENT_URL now points to pay-old.internal (decommissioned host)",
    ]
    for line in lines:
        assert redact(line) == line


def test_line_count_preserved() -> None:
    text = "a@b.com\nplain\n10.0.0.1"
    assert len(redact(text).splitlines()) == 3
