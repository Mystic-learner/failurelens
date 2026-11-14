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


def test_card_rule_ignores_block_ids_and_timestamps() -> None:
    head = "081109 203807 222 INFO dfs.DataNode$PacketResponder: "
    for i in range(100):
        line = head + "PacketResponder 0 for block blk_-66709586223689879" + f"{i:02d} terminating"
        assert redact(line) == line
    for n in range(1000):
        line = f"081109 204{n:03d} 34 INFO dfs.FSNamesystem: BLOCK* NameSystem.addStoredBlock: ok"
        assert redact(line) == line


def test_card_still_matches_contiguous_and_grouped() -> None:
    assert "[REDACTED_CARD]" in redact("card=4111111111111111 declined")
    assert "[REDACTED_CARD]" in redact("charge 4111-1111-1111-1111 declined")


def test_long_token_keeps_path_and_distinct_ids() -> None:
    a = "54fadb412c4e40cdbaed9335e4c35a9e"
    b = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
    out = redact(f"GET /v2/{a}/servers/detail HTTP/1.1 id {a} other {b}")
    assert "/v2/[REDACTED_TOKEN#" in out and "/servers/detail" in out
    assert a not in out and b not in out
    ta, tb = redact(a), redact(b)
    assert ta != tb and out.count(ta) == 2 and out.count(tb) == 1


def test_long_token_keeps_filesystem_path() -> None:
    out = redact("image x at (/var/lib/nova/instances/_base/" + "9f" * 20 + "): checking")
    assert "/var/lib/nova/instances/_base/[REDACTED_TOKEN#" in out
