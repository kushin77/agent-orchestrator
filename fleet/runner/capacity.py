"""capacity.py — the runner's fan-out width, declared and backed off (issue #1343).

---knowledge---
module_id: fleet.runner.capacity
system: fleet
app: fleet
solution_class: pattern
patterns: []
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [default_capacity, declared_capacity, declared_mem_floor_gb, Width, effective_capacity, probe_host]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

The width is never a literal: it is `AO_RUNNER_CAPACITY` from the env contract
(default `min(8, nproc // 2)`, never below 1), and before each fan-out it is
reduced — never below 1 — when the box is already busy:

* 1-minute load average > nproc          -> `capacity-backoff:load:<load>/<nproc>`
* MemAvailable < `AO_RUNNER_MEM_FLOOR_GB` -> `capacity-backoff:memory:<gb>/<floor>`

Both readings are injected (`load1`, `mem_available_gb`, `nproc`), so the
tests drive `effective_capacity()` without touching /proc; `probe_host()` is
the one real reader.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CAPACITY_ENV = "AO_RUNNER_CAPACITY"
MEM_FLOOR_ENV = "AO_RUNNER_MEM_FLOOR_GB"
DEFAULT_MEM_FLOOR_GB = 8.0
MAX_DEFAULT_CAPACITY = 8


def default_capacity(nproc: int) -> int:
    return max(1, min(MAX_DEFAULT_CAPACITY, int(nproc) // 2))


def declared_capacity(env: dict[str, str] | None = None, *, nproc: int | None = None) -> int:
    """`AO_RUNNER_CAPACITY` when it is a positive integer, else the derived default."""
    source = os.environ if env is None else env
    raw = str(source.get(CAPACITY_ENV, "") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return default_capacity(nproc if nproc is not None else (os.cpu_count() or 2))


def declared_mem_floor_gb(env: dict[str, str] | None = None) -> float:
    source = os.environ if env is None else env
    raw = str(source.get(MEM_FLOOR_ENV, "") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MEM_FLOOR_GB
    return value if value >= 0 else DEFAULT_MEM_FLOOR_GB


@dataclass(frozen=True)
class Width:
    declared: int
    effective: int
    reason: str = ""  # "" when no backoff applied; else `capacity-backoff:<why>`


def effective_capacity(
    declared: int,
    *,
    load1: float | None,
    mem_available_gb: float | None,
    nproc: int,
    mem_floor_gb: float = DEFAULT_MEM_FLOOR_GB,
) -> Width:
    """Reduce the declared width when the host is loaded; never below 1."""
    width = max(1, int(declared))
    reasons: list[str] = []
    if load1 is not None and nproc > 0 and load1 > nproc:
        # Scale by how far over we are: halve, and halve again per full nproc over.
        over = max(1, int(load1 // nproc))
        width = max(1, width // (2 * over))
        reasons.append(f"load:{load1:.1f}/{nproc}")
    if mem_available_gb is not None and mem_available_gb < mem_floor_gb:
        width = max(1, width // 2)
        reasons.append(f"memory:{mem_available_gb:.1f}gb<{mem_floor_gb:g}gb")
    reason = f"capacity-backoff:{';'.join(reasons)}" if reasons else ""
    return Width(declared=max(1, int(declared)), effective=width, reason=reason)


def probe_host(proc: Path = Path("/proc")) -> tuple[float | None, float | None, int]:
    """(load1, MemAvailable in GB, nproc) from the real host; None when unreadable."""
    nproc = os.cpu_count() or 1
    load1: float | None
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        load1 = None
    mem: float | None = None
    try:
        for line in (proc / "meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                mem = int(line.split()[1]) / (1024 * 1024)
                break
    except (OSError, ValueError, IndexError):
        mem = None
    return load1, mem, nproc
