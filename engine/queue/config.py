"""engine/queue.config — offline tuning knobs for the job queue.

Mirror of the gateway/health YAML-config convention: a ``QueueConfig`` with
sane defaults, optionally overridden from ``queue.yaml`` via ``load_config``.
No network, no containers — everything runs on a caller-supplied clock.

---knowledge---
module_id: engine.queue.config
system: engine
app: queue
solution_class: class
patterns: [read-only-optional-input, schema-validated-loader]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [QueueConfig, default_config, load_config]
invariants: "a missing config file yields the declared defaults"
gotchas: ""
related: ["#22"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(_HERE, "queue.yaml")

ENV_STORE_PATH = "AO_QUEUE_STORE"


def env_store_path() -> str:
    """Return the CLI's state-file path: ``$AO_QUEUE_STORE`` or ``<cwd>/.ao-queue.json``.

    Declared here (issue #1915) so the raw env read lives in the queue's
    config module rather than inline in ``cli.py``.
    """
    return os.environ.get(ENV_STORE_PATH) or os.path.join(os.getcwd(), ".ao-queue.json")

_DEFAULTS = None  # set below once the frozen dataclass exists


@dataclass(frozen=True)
class QueueConfig:
    """Tuning knobs for :class:`engine.queue.queue.JobQueue`.

    Parameters
    ----------
    lease_seconds:
        How long a claim stays valid without ``renew`` (heartbeat). After this
        elapses the claim is stale and the orphan reaper may reclaim the task.
    max_attempts:
        Delivery attempts before a task is dead-lettered (``FAILED``/``DEAD``).
    age_bump_seconds:
        Once a ``PENDING`` task is older than this it earns a +1 selection
        rank bump, so priority *and* age both order the queue (the CMR fleet
        queue doctrine).
    per_tenant_max_pending:
        Backpressure bound — the maximum number of open
        (``PENDING``+``CLAIMED``+``RUNNING``) tasks admitted per tenant.
        Further enqueues are rejected with :class:`BackpressureError`.
    """

    lease_seconds: float = 300.0
    max_attempts: int = 5
    age_bump_seconds: float = 86400.0
    per_tenant_max_pending: int = 100


_DEFAULTS = QueueConfig()


def default_config() -> QueueConfig:
    """Return the built-in defaults (identical to an absent/empty queue.yaml)."""
    return _DEFAULTS


def load_config(path: Optional[str] = None) -> QueueConfig:
    """Load queue tuning from ``path`` (default ``engine/queue/queue.yaml``).

    A missing or empty file yields the defaults. Values not present in the
    YAML keep their defaults. Returns a fresh :class:`QueueConfig`.
    """
    src = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(src):
        return default_config()
    with open(src, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    q = raw.get("queue") or {}
    return QueueConfig(
        lease_seconds=float(q.get("lease_seconds", _DEFAULTS.lease_seconds)),
        max_attempts=int(q.get("max_attempts", _DEFAULTS.max_attempts)),
        age_bump_seconds=float(
            q.get("age_bump_seconds", _DEFAULTS.age_bump_seconds)
        ),
        per_tenant_max_pending=int(
            q.get("per_tenant_max_pending", _DEFAULTS.per_tenant_max_pending)
        ),
    )
