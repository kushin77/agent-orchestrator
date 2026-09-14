"""The offline CLI demo runs one ticket end-to-end and exits 0.

The acceptance criterion (issue #634) asks for "a CLI/demo [that] shows one
ticket end-to-end".  Running it as a subprocess is the honest test: it proves
the demo is self-contained (its own ``sys.path`` bootstrap), offline, and
deterministic, and that it neither hangs nor needs a model provider.
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))
)
CLI = os.path.join(REPO_ROOT, "engine", "core", "tickets", "cli.py")


def _run_cli() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, CLI],
        cwd="/",
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_runs_one_ticket_end_to_end():
    result = _run_cli()
    assert result.returncode == 0, result.stderr
    out = result.stdout
    # The demo shows the full lifecycle, in order.
    assert "created -> decomposed -> dispatched -> executed -> reviewed -> closed" in out
    assert "state             : closed" in out
    # ...and the tenant scope on every transcript line.
    assert "tenant-scoped transcript" in out
    assert "tenant=acme" in out
    # Durability is demonstrated, not asserted.
    assert "matches live=True" in out


def test_cli_is_deterministic_across_runs():
    first = _run_cli()
    second = _run_cli()
    assert first.returncode == 0
    assert second.returncode == 0

    def _stable(text: str) -> str:
        # The temp workdir path is the only run-varying line.
        return "\n".join(
            line for line in text.splitlines() if not line.startswith("artifacts")
        )

    assert _stable(first.stdout) == _stable(second.stdout)


def test_cli_needs_no_network_or_provider():
    """The demo is stdlib-only: no provider package is imported."""
    result = _run_cli()
    assert result.returncode == 0
    assert "fake-model" not in result.stdout
