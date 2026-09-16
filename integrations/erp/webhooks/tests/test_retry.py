from __future__ import annotations

import pytest

from integrations.erp.webhooks.model import Refused
from integrations.erp.webhooks.retry import BackoffPolicy, run_with_retry


def test_backoff_delay_grows_and_caps():
    policy = BackoffPolicy(base_seconds=1.0, max_seconds=4.0, max_attempts=5)
    assert policy.delay(0) == 1.0
    assert policy.delay(1) == 2.0
    assert policy.delay(2) == 4.0
    assert policy.delay(3) == 4.0  # capped


def test_run_with_retry_succeeds_after_transient_failures():
    calls = {"n": 0}
    sleeps = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    result = run_with_retry(
        flaky, policy=BackoffPolicy(base_seconds=0.01, max_attempts=5), sleep=sleeps.append
    )
    assert result == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_run_with_retry_never_retries_refused():
    calls = {"n": 0}

    def always_refuses():
        calls["n"] += 1
        raise Refused("auth-failed", "nope")

    with pytest.raises(Refused):
        run_with_retry(always_refuses, policy=BackoffPolicy(max_attempts=5))
    assert calls["n"] == 1  # no retry on a Refused


def test_run_with_retry_exhausts_and_raises_last_transient_error():
    def always_flaky():
        raise TimeoutError("still down")

    with pytest.raises(TimeoutError):
        run_with_retry(always_flaky, policy=BackoffPolicy(base_seconds=0.0, max_attempts=3))
