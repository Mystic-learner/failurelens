import string

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.redaction import Redactor

ALNUM = string.ascii_letters + string.digits
FRAGMENTS = [
    "password=",
    "token: ",
    "Bearer ",
    "sk-",
    "AKIA",
    "ghp_",
    "eyJ",
    "@",
    ".",
    ":",
    "::",
    "10.0.0.1",
    "4111111111111111",
    "\n",
    " ",
    "<IP_1>",
    "<SECRET>",
    '"',
    "'",
    "abc",
    "123",
    "secret=",
    "api_key=",
]
noisy_text = st.lists(st.sampled_from(FRAGMENTS) | st.text(max_size=8), max_size=25).map("".join)


@settings(max_examples=300, deadline=None)
@given(noisy_text)
def test_redaction_is_idempotent(text: str) -> None:
    once = Redactor().redact(text)
    assert Redactor().redact(once) == once


@settings(max_examples=300, deadline=None)
@given(st.text(alphabet=ALNUM, min_size=20, max_size=60))
def test_sk_key_never_survives(body: str) -> None:
    out = Redactor().redact(f"before sk-{body} after")
    assert body not in out


@settings(max_examples=300, deadline=None)
@given(st.text(alphabet=ALNUM, min_size=16, max_size=40))
def test_akia_and_ghp_never_survive(body: str) -> None:
    out = Redactor().redact(f"x ghp_{body}{body[:4]} y")
    assert body not in out
    upper = "".join(c for c in body.upper() if c.isalnum())[:16].ljust(16, "A")
    assert upper not in Redactor().redact(f"id AKIA{upper} z")


@settings(max_examples=300, deadline=None)
@given(
    st.sampled_from(["password", "secret", "token", "api_key", "client_secret"]),
    st.sampled_from(["=", ": ", "="]),
    st.text(alphabet=ALNUM + "!#$%^*", min_size=1, max_size=30),
)
def test_kv_secret_value_replaced(key: str, sep: str, value: str) -> None:
    assert Redactor().redact(f"{key}{sep}{value}") == f"{key}{sep}<SECRET>"


@settings(max_examples=200, deadline=None)
@given(st.text(alphabet=ALNUM, min_size=20, max_size=40), st.integers(min_value=4, max_value=15))
def test_split_sk_token_never_survives(body: str, cut: int) -> None:
    first, second = body[:cut], body[cut:]
    out = Redactor().redact(f"sk-{first}\n{second}\nINFO done")
    assert second not in out
    assert "INFO done" in out
