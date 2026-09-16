from __future__ import annotations

import pytest

from integrations.erp.webhooks import auth
from integrations.erp.webhooks.model import Refused

SECRET = "s3cret"
BODY = b'{"eventId":"evt-1"}'


def test_verify_accepts_correct_signature():
    header = auth.sign(SECRET, BODY)
    auth.verify(SECRET, BODY, header)  # does not raise


def test_verify_rejects_missing_header():
    with pytest.raises(Refused) as exc:
        auth.verify(SECRET, BODY, None)
    assert exc.value.code == "auth-failed"


def test_verify_rejects_unlabeled_header():
    with pytest.raises(Refused) as exc:
        auth.verify(SECRET, BODY, "deadbeef")
    assert exc.value.code == "auth-failed"


def test_verify_rejects_wrong_secret():
    header = auth.sign("other-secret", BODY)
    with pytest.raises(Refused) as exc:
        auth.verify(SECRET, BODY, header)
    assert exc.value.code == "auth-failed"


def test_verify_rejects_tampered_body():
    header = auth.sign(SECRET, BODY)
    with pytest.raises(Refused):
        auth.verify(SECRET, BODY + b"tampered", header)


def test_verify_rejects_no_secret_configured():
    header = auth.sign(SECRET, BODY)
    with pytest.raises(Refused) as exc:
        auth.verify("", BODY, header)
    assert exc.value.code == "auth-failed"
