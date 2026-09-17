#!/usr/bin/env python3
"""The capacity gate — max-agents as the DEFAULT fan-out, bounded by three real
limits (epic #707, lane F3 / issue #718).

THE CONTRACT
------------
The fleet's fan-out defaults to the MAXIMUM number of agents, but "maximum" is
not a number anybody gets to pick: it is resolved, every cycle, as the minimum
of three bounds that can each genuinely be smaller than the pool.

    effective = min(pool_size, disjoint_ready_lanes, resource_ceiling)

Each bound exists because its absence was MEASURED as a failure:

``pool_size`` (``FLEET_SISTER_POOL``, default 10)
    The principal's own ceiling: the ``FLEET_SISTER_POOL`` knob the loop already
    had. ``FLEET_MAX_AGENTS`` (or the pinned focus's ``max_agents``) sets the
    DEFAULT; see :func:`default_max_agents` for the precedence.

``disjoint_ready_lanes`` (AO-GR-24, golden rule 21)
    "A wave is provably file-disjoint before dispatch." Fanning out by issue
    produced **27 source-file collisions across 14 lanes** — six siblings editing
    the same seven files — so raising the agent count multiplied conflicts
    instead of throughput. Two ready lanes that declare the same file are
    therefore never both dispatched: the later one is HELD, and the refusal names
    the file and the lane that holds it.

``resource_ceiling`` (RAM / ``/tmp`` headroom)
    A ``verify.sh`` storm is a measured failure on this box: **49 concurrent
    ``make verify`` runs, 43 of them stacked in two worktrees, for ~16 hours.**
    The ceiling is COMPUTED from the headroom that actually exists right now
    (``MemAvailable`` and the free space on ``/tmp``) divided by the budget one
    concurrent lane needs — never guessed, and never "unlimited".

HONESTY RULES (the part that keeps the gate from being a formality)
-------------------------------------------------------------------
* **An unreadable resource measurement is CANNOT-ASSESS, never unbounded.** A
  control that cannot measure must not resolve to "no limit"; the stream is held
  and the reason is named. (AO-GR-25: a control that fails *open* is worse than
  no control.)
* **A lane that declares no file set is UNATTRIBUTABLE, and is named.** It is
  admitted — nothing can be compared, and holding all undeclared work would wedge
  a queue whose writers do not declare files yet — but it is returned in
  :attr:`DisjointBound.unattributable` and the caller reports the count, so the
  residual is *visible* rather than silently called disjoint. A principal who
  wants the strict reading sets ``AO_LANE_FILES_REQUIRED=1`` and the undeclared
  lane is then HELD.
* **A bound that cannot bind is not a bound.** ``scripts/check-capacity-gate.sh``
  provokes each of the three bounds, proves the excess is held AND names the
  bound that held it, and proves the relaxed input is admitted — so the held path
  and the admitted path cannot collapse into the same exit code (GR-12).

Everything here is stdlib-only, offline and side-effect free: no clock is read
for the decision (the resource sample is an argument), no file is written, and
``/proc`` is only read by :func:`measure_resources`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

#: ``FLEET_MAX_AGENTS`` — the principal's own default for the fan-out. ``0`` means
#: "resolve it against the pool and the two measured bounds" (the shipped
#: default: max-agents ON, gated).
ENV_MAX_AGENTS = "FLEET_MAX_AGENTS"
#: Per-concurrent-lane RAM budget, in MiB. The default is derived from
#: measurement, not taste — see :data:`DEFAULT_LANE_RAM_MIB`.
ENV_LANE_RAM_MIB = "AO_LANE_RAM_MIB"
#: Per-concurrent-lane ``/tmp`` budget, in MiB.
ENV_LANE_TMP_MIB = "AO_LANE_TMP_MIB"
#: ``1`` makes an undeclared file set HOLD instead of being admitted-and-named.
ENV_FILES_REQUIRED = "AO_LANE_FILES_REQUIRED"
#: Where the ``/tmp`` headroom is measured. A concurrent gate writes there.
ENV_TMP_PATH = "AO_GATE_TMP_PATH"

#: The loop's own pool default, mirrored from ``fleet/terminal.py`` so this
#: module can resolve the default without importing the loop.
DEFAULT_POOL_SIZE = 10

#: Per-lane RAM budget (MiB) when nothing overrides it.
#: MEASURED on 2026-09-14 (this box, 30 GiB RAM / 16 GiB ``/tmp`` tmpfs): the
#: aggregate ``VmRSS`` of one ``scripts/verify.sh`` process TREE was sampled every
#: 0.5s for 420s while sibling lanes ran; across 5 distinct gates the per-gate
#: PEAK was 3 / 23 / 24 / 194 / **236 MiB**.
#: The budget is 1536 and not 236 because a lane is a **executor + its gate**: the
#: executor is the heavier half and the gate can briefly hold several pytest
#: trees, so the gate-only peak is a floor, not the whole lane. 1536 MiB is
#: ~6x the measured gate peak — deliberately conservative — and it is the number
#: that reproduces this box's observed-safe concurrency (~19 GiB available /
#: 1.5 GiB ≈ 12 lanes, which is the ``wave_cap`` the board was already running).
DEFAULT_LANE_RAM_MIB = 1536

#: Per-lane ``/tmp`` budget (MiB).
#: MEASURED on the same box: the largest single consumer under ``/tmp`` was a
#: LANE's own scratch tree at **912 MiB**, with a repo copy at 480 MiB beside it.
#: 512 MiB is the conservative allowance — deliberately BELOW the largest
#: observed lane scratch, so that the fan-out cannot be the thing that fills a
#: shared tmpfs — and on this box it does not bind (13 GiB free / 512 MiB = 26 >
#: the RAM-driven 12), which is the point: it is the emergency bound, not the
#: everyday one.
DEFAULT_LANE_TMP_MIB = 512

#: Where the free space for the ``/tmp`` bound is measured by default.
DEFAULT_TMP_PATH = "/tmp"

#: ``MemAvailable`` is the kernel's own "what could be handed to a new workload"
#: estimate. ``MemFree`` is not it (it counts reclaimable page cache as used, and
#: would hold the fleet for ever on a healthy box).
MEMINFO_PATH = Path("/proc/meminfo")

#: The pinned focus (``governance/dispatch/focus.py`` owns its schema). One field
#: of it is read here — ``max_agents`` — because that is the board's word for the
#: fan-out default of the epic the fleet is driving.
DEFAULT_FOCUS_PATH = Path(".board/focus.json")


class CapacityConfigError(ValueError):
    """A capacity knob is set but unusable — refused, never silently defaulted."""


def _int_at_least(raw: object, knob: str, minimum: int) -> int | None:
    """Parse a configured integer >= ``minimum``; ``None`` when the value is empty.

    A value that is *set* but unusable is REFUSED (the same asymmetry the wave
    cap and the runaway guard use): a typo in ``FLEET_MAX_AGENTS`` must never
    quietly disable the bound it configures.

    ``minimum`` is per-knob on purpose: ``0`` is the focus schema's own word for
    "resolve it against the pool" and ``FLEET_MAX_AGENTS`` uses the same
    vocabulary, while a per-lane budget of ``0`` is a division by zero and is
    refused outright.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise CapacityConfigError(f"{knob} must be an integer >= {minimum}, got {text!r}") from None
    if value < minimum:
        raise CapacityConfigError(f"{knob} must be an integer >= {minimum}, got {text!r}")
    return value


def _flag(raw: object) -> bool:
    """A boolean env knob: ``1/true/yes/on`` (case-insensitive) is on."""
    return str(raw or "").strip().casefold() in {"1", "true", "yes", "on"}


# --- the three bounds --------------------------------------------------------


@dataclass(frozen=True)
class Bound:
    """One limit on the fan-out, with the words needed to report it.

    ``limit is None`` means CANNOT-ASSESS — the bound was asked for an answer it
    could not give. It is never read as "no limit".
    """

    name: str
    limit: int | None
    why: str

    def detail(self) -> str:
        return self.why

    @property
    def assessed(self) -> bool:
        return self.limit is not None


@dataclass(frozen=True)
class Lane:
    """One ready lane: its identity and the files it declares it owns."""

    id: str
    issue: int | None = None
    files: tuple[str, ...] | None = None
    detail: str = ""

    @classmethod
    def from_directive(cls, directive: Mapping[str, object], *, lane_id: str = "") -> "Lane":
        """Build a lane from a directive: its id, its issue and ``task.files``."""
        task = directive.get("task")
        task = task if isinstance(task, Mapping) else {}
        issue = task.get("issue")
        if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
            issue = None
        name = str(lane_id or directive.get("id") or "").strip()
        if not name:
            name = f"#{issue}" if issue is not None else "unnamed-lane"
        return cls(id=name, issue=issue, files=declared_files(task.get("files")))


def declared_files(raw: object) -> tuple[str, ...] | None:
    """Normalise a declared file set, or ``None`` when the lane declared none.

    An EMPTY declaration is the same as no declaration: "I own no files" cannot
    be told apart from "I did not say", and neither can be compared against
    another lane's set — both are *unattributable*, and both are NAMED as such.
    """
    if raw is None:
        return None
    values: list[str] = []
    if isinstance(raw, str):
        candidates: Sequence[object] = raw.replace(",", "\n").splitlines()
    elif isinstance(raw, (list, tuple)):
        flat: list[object] = []
        for item in raw:
            if isinstance(item, str):
                flat.extend(item.replace(",", "\n").splitlines())
            else:
                flat.append(item)
        candidates = flat
    else:
        return None
    for part in candidates:
        text = str(part or "").strip()
        if text and text not in values:
            values.append(text)
    return tuple(values) if values else None


@dataclass(frozen=True)
class Collision:
    """Two ready lanes claiming the same file — the reason the later is held."""

    lane: str
    holder: str
    files: tuple[str, ...]

    def message(self) -> str:
        return (
            f"lane {self.lane} claims {', '.join(self.files)} — already owned by lane "
            f"{self.holder} (AO-GR-24: a wave is provably file-disjoint before dispatch)"
        )


@dataclass(frozen=True)
class DisjointBound:
    """The largest file-disjoint subset of the ready lanes, and what fell out."""

    limit: int
    admitted: tuple[str, ...]
    collisions: tuple[Collision, ...]
    unattributable: tuple[str, ...]

    def detail(self) -> str:
        parts = [f"{self.limit} disjoint of {self.limit + len(self.collisions)} ready"]
        if self.collisions:
            parts.append(f"{len(self.collisions)} held for a file collision")
        if self.unattributable:
            # Never silent: a lane whose set could not be compared is reported by
            # name, so "disjoint" is never claimed for work nobody could check.
            parts.append(
                f"{len(self.unattributable)} unattributable (no Files: declared: "
                f"{', '.join(self.unattributable)})"
            )
        return "; ".join(parts)


def disjoint_bound(
    lanes: Iterable[Lane], *, files_required: bool = False
) -> DisjointBound:
    """Select the pairwise file-disjoint subset of ``lanes``, in the order given.

    Greedy and deterministic: the first lane to claim a file owns it, and a later
    lane claiming any already-owned file is excluded — which is exactly the rule
    "two ready directives owning the same file set are not both dispatched". A
    lane that declares no file set is *unattributable*: admitted and named by
    default, HELD when ``files_required`` is set.
    """
    admitted: list[str] = []
    collisions: list[Collision] = []
    unattributable: list[str] = []
    owner: dict[str, str] = {}
    for lane in lanes:
        files = lane.files
        if not files:
            unattributable.append(lane.id)
            if not files_required:
                admitted.append(lane.id)
            continue
        overlap = tuple(sorted(f for f in files if f in owner))
        if overlap:
            collisions.append(Collision(lane=lane.id, holder=owner[overlap[0]], files=overlap))
            continue
        for name in files:
            owner[name] = lane.id
        admitted.append(lane.id)
    return DisjointBound(
        limit=len(admitted),
        admitted=tuple(admitted),
        collisions=tuple(collisions),
        unattributable=tuple(unattributable),
    )


@dataclass(frozen=True)
class Resources:
    """The measured headroom, or the reason it could not be measured."""

    ram_available_bytes: int | None
    tmp_available_bytes: int | None
    tmp_path: str = DEFAULT_TMP_PATH
    problems: tuple[str, ...] = ()

    @property
    def measured(self) -> bool:
        return self.ram_available_bytes is not None and self.tmp_available_bytes is not None


def measure_resources(
    *, meminfo: Path | str = MEMINFO_PATH, tmp_path: str | None = None
) -> Resources:
    """Read the real headroom: ``MemAvailable`` and the free space on ``/tmp``.

    A failure to read either is RECORDED, never rounded up to infinity.
    """
    problems: list[str] = []
    ram: int | None = None
    try:
        for line in Path(meminfo).read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                ram = int(line.split()[1]) * 1024
                break
        if ram is None:
            problems.append(f"{meminfo}: no MemAvailable line")
    except (OSError, ValueError, IndexError) as exc:
        problems.append(f"{meminfo}: unreadable ({exc})")

    target = str(tmp_path or os.environ.get(ENV_TMP_PATH) or DEFAULT_TMP_PATH)
    tmp: int | None = None
    try:
        stats = os.statvfs(target)
        tmp = stats.f_bavail * stats.f_frsize
    except OSError as exc:
        problems.append(f"{target}: unreadable ({exc})")

    return Resources(
        ram_available_bytes=ram, tmp_available_bytes=tmp, tmp_path=target, problems=tuple(problems)
    )


def resource_bound(
    resources: Resources,
    *,
    ram_budget_mib: int = DEFAULT_LANE_RAM_MIB,
    tmp_budget_mib: int = DEFAULT_LANE_TMP_MIB,
) -> Bound:
    """How many concurrent lanes the measured headroom can carry.

    ``min(ram_gates, tmp_gates)``, floored by plain integer division — a real
    ``0`` when the box genuinely has no room for one more lane, which is a
    verdict (hold), not an excuse to pour the next storm on it.
    """
    if ram_budget_mib < 1 or tmp_budget_mib < 1:
        raise CapacityConfigError(
            f"per-lane budgets must be positive MiB, got ram={ram_budget_mib} tmp={tmp_budget_mib}"
        )
    if not resources.measured:
        # The reason only — ``Capacity.line`` adds the verdict once, so a
        # CANNOT-ASSESS never reads as "CANNOT-ASSESS: CANNOT-ASSESS".
        return Bound(
            name="resource",
            limit=None,
            why="; ".join(resources.problems) or "headroom could not be measured",
        )
    ram_mib = resources.ram_available_bytes // (1024 * 1024)
    tmp_mib = resources.tmp_available_bytes // (1024 * 1024)
    ram_gates = ram_mib // ram_budget_mib
    tmp_gates = tmp_mib // tmp_budget_mib
    binding = "RAM" if ram_gates <= tmp_gates else f"{resources.tmp_path} headroom"
    return Bound(
        name="resource",
        limit=min(ram_gates, tmp_gates),
        why=(
            f"{min(ram_gates, tmp_gates)} lane(s) — bound by {binding}: "
            f"RAM {ram_mib} MiB / {ram_budget_mib} MiB = {ram_gates}, "
            f"{resources.tmp_path} {tmp_mib} MiB / {tmp_budget_mib} MiB = {tmp_gates}"
        ),
    )


def read_focus_max_agents(path: Path | str | None = None) -> int | None:
    """The pinned focus's ``max_agents``, or ``None`` when there is no focus.

    A TOLERANT read of one field, not a second schema implementation: the
    authoritative validator is ``governance/dispatch/focus.py`` (driven by
    ``cli.py focus --self-control`` and the ``epic-focus`` check). This exists so
    the loop can let the board name the epic's fan-out default without shelling
    out to the dispatch CLI every cycle.

    An absent file is ``None`` (nothing is pinned). A file that exists but does
    not carry a usable ``max_agents`` is REFUSED — a corrupt focus must never
    quietly disable the default it configures, which is the same stance
    ``focus.py`` takes.
    """
    target = Path(path or DEFAULT_FOCUS_PATH)
    if not target.exists():
        return None
    import json  # local: the only JSON this module touches

    try:
        obj = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CapacityConfigError(f"{target}: unreadable ({exc})") from None
    if not isinstance(obj, Mapping):
        raise CapacityConfigError(f"{target}: must be a JSON object")
    value = obj.get("max_agents")
    if value is None:
        raise CapacityConfigError(f"{target}: has no 'max_agents' field")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CapacityConfigError(f"{target}: max_agents must be an integer >= 0, got {value!r}")
    return value


def default_max_agents(
    env: Mapping[str, str] | None = None,
    *,
    focus_max_agents: int | None = None,
    pool_size: int | None = None,
) -> int:
    """The fan-out DEFAULT, before the three bounds are applied.

    **The precedence is deliberate and stated here:** an explicit
    ``FLEET_MAX_AGENTS`` always wins, because that is the principal speaking now;
    an unset variable defers to the pinned focus's ``max_agents`` when it is a
    positive number, because that is the board speaking for this epic (``0``
    there means "the pool", the shipped setting); with neither, the loop's own
    ``FLEET_SISTER_POOL`` / :data:`DEFAULT_POOL_SIZE` applies.
    """
    source = os.environ if env is None else env
    pool = pool_size if isinstance(pool_size, int) and not isinstance(pool_size, bool) and pool_size >= 1 else DEFAULT_POOL_SIZE
    declared = str((source or {}).get(ENV_MAX_AGENTS, "") or "").strip()
    if declared:
        configured = _int_at_least(declared, ENV_MAX_AGENTS, 0)
        # ``0`` is the focus schema's own word for "the pool". A principal who
        # types it is overriding the *focus* too, not merely staying silent, so
        # it resolves straight to the pool rather than falling through to the
        # focus's number.
        if not configured:
            return pool
        return configured
    if isinstance(focus_max_agents, int) and not isinstance(focus_max_agents, bool) and focus_max_agents >= 1:
        return focus_max_agents
    return pool


# --- the resolution ----------------------------------------------------------


@dataclass(frozen=True)
class Capacity:
    """The resolved fan-out: the effective count, the binding bound and the rest."""

    effective: int
    binding: str
    bounds: tuple[Bound, ...] = ()
    disjoint: DisjointBound | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def assessed(self) -> bool:
        return not self.problems

    def line(self) -> str:
        """The one-line report a caller prints (the evidence a principal reads)."""
        rendered = " ".join(
            f"{b.name}={b.limit if b.assessed else 'CANNOT-ASSESS'}" for b in self.bounds
        )
        if self.problems:
            return f"capacity: CANNOT-ASSESS — {'; '.join(self.problems)} ({rendered})"
        return f"capacity: effective={self.effective} bound_by={self.binding} ({rendered})"


def resolve_capacity(
    *,
    ready: Iterable[Lane] = (),
    env: Mapping[str, str] | None = None,
    focus_max_agents: int | None = None,
    focus_path: Path | str | None = None,
    pool_size: int | None = None,
    resources: Resources | None = None,
) -> Capacity:
    """``effective = min(pool, disjoint ready lanes, resource ceiling)``.

    Every bound is resolved even when an earlier one is already smaller, so the
    report always carries all three — a principal tuning one knob needs to see
    whether it is the one that binds.

    ``focus_max_agents`` is passed explicitly when the caller already read it;
    otherwise ``focus_path`` is read by :func:`read_focus_max_agents`.
    """
    source = os.environ if env is None else env
    if focus_max_agents is None and focus_path is not None:
        focus_max_agents = read_focus_max_agents(focus_path)
    pool = default_max_agents(
        source, focus_max_agents=focus_max_agents, pool_size=pool_size
    )
    lanes = tuple(ready)
    disjoint = disjoint_bound(lanes, files_required=_flag((source or {}).get(ENV_FILES_REQUIRED)))
    measured = resources if resources is not None else measure_resources()
    ram_budget = _int_at_least((source or {}).get(ENV_LANE_RAM_MIB), ENV_LANE_RAM_MIB, 1)
    tmp_budget = _int_at_least((source or {}).get(ENV_LANE_TMP_MIB), ENV_LANE_TMP_MIB, 1)
    resource = resource_bound(
        measured,
        ram_budget_mib=ram_budget or DEFAULT_LANE_RAM_MIB,
        tmp_budget_mib=tmp_budget or DEFAULT_LANE_TMP_MIB,
    )
    bounds = (
        Bound(name="pool", limit=pool, why=f"{ENV_MAX_AGENTS}/focus/pool"),
        Bound(name="disjoint", limit=disjoint.limit, why=disjoint.detail()),
        resource,
    )
    unassessed = tuple(b.name for b in bounds if not b.assessed)
    if unassessed:
        return Capacity(
            effective=0,
            binding=",".join(unassessed),
            bounds=bounds,
            disjoint=disjoint,
            problems=tuple(b.detail() for b in bounds if not b.assessed),
        )
    limits = [b.limit for b in bounds if b.limit is not None]
    effective = min(limits)
    binding = next(b.name for b in bounds if b.limit == effective)
    return Capacity(effective=effective, binding=binding, bounds=bounds, disjoint=disjoint)


def headline(
    *,
    env: Mapping[str, str] | None = None,
    focus_max_agents: int | None = None,
    pool_size: int | None = None,
    resources: Resources | None = None,
) -> str:
    """The fan-out default and the measured resource ceiling, for a reporting verb.

    The DISJOINT bound is deliberately absent: it is resolved per dispatch cycle
    over the ready set, and a reporting verb has no ready set. Printing a number
    for it would be a guess dressed as a measurement, which is the one thing this
    module exists to avoid — so the sentence says so out loud instead.
    """
    source = os.environ if env is None else env
    pool = default_max_agents(source, focus_max_agents=focus_max_agents, pool_size=pool_size)
    measured = resources if resources is not None else measure_resources()
    ram_budget = _int_at_least((source or {}).get(ENV_LANE_RAM_MIB), ENV_LANE_RAM_MIB, 1) or DEFAULT_LANE_RAM_MIB
    tmp_budget = _int_at_least((source or {}).get(ENV_LANE_TMP_MIB), ENV_LANE_TMP_MIB, 1) or DEFAULT_LANE_TMP_MIB
    ceiling = resource_bound(measured, ram_budget_mib=ram_budget, tmp_budget_mib=tmp_budget)
    rendered = (
        f"max_agents_default={pool} ({ENV_MAX_AGENTS}/focus/FLEET_SISTER_POOL) "
        f"resource_ceiling={ceiling.limit if ceiling.assessed else 'CANNOT-ASSESS'} ({ceiling.detail()})"
    )
    return (
        f"capacity: {rendered} — effective = min(pool, disjoint-ready-lanes, resource_ceiling); "
        "the disjoint bound is resolved per dispatch cycle over the ready set"
    )


# --- the admission decision --------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """Whether one ready lane may be dispatched now, and the bound that decided."""

    admitted: bool
    reason: str
    bound: str
    detail: str
    collision: Collision | None = None

    def line(self, lane: Lane) -> str:
        what = f"#{lane.issue}" if lane.issue is not None else lane.id
        if self.admitted:
            return f"[terminal] capacity: {what} admitted ({self.bound}: {self.detail})"
        return (
            f"[terminal] capacity HELD {what} — {self.reason} "
            f"(bound: {self.bound}; {self.detail})"
        )


def admit(
    lane: Lane,
    *,
    capacity: Capacity,
    active: Sequence[Lane] = (),
) -> Decision:
    """Decide whether ``lane`` may be dispatched alongside ``active`` lanes.

    The order is deliberate: a CANNOT-ASSESS capacity holds everything (the box
    cannot be measured, so nothing may be multiplied on it); then the file
    collision (dispatch is refused even if a worker is free, because two lanes
    editing one file is the measured failure AO-GR-24 exists for); then the
    resource ceiling; then the count against the effective bound.
    """
    if not capacity.assessed:
        return Decision(
            admitted=False,
            reason="cannot-assess — " + "; ".join(capacity.problems),
            bound=capacity.binding or "resource",
            detail="the ceiling could not be measured, so no lane is multiplied on it",
        )

    strict = capacity.disjoint is not None and capacity.disjoint.unattributable
    collision = _collision(lane, active)
    if collision is not None:
        return Decision(
            admitted=False,
            reason="file collision",
            bound="disjoint",
            detail=collision.message(),
            collision=collision,
        )
    # A lane with nothing to compare cannot be *proved* disjoint from an
    # unattributable lane already in flight: in strict mode that is a hold.
    if strict and not lane.files and any(not other.files for other in active):
        held = next(other.id for other in active if not other.files)
        return Decision(
            admitted=False,
            reason="file set undeclared",
            bound="disjoint",
            detail=(
                f"lane {lane.id} declares no Files: and lane {held} declares none either — "
                f"disjointness cannot be proven ({ENV_FILES_REQUIRED}=1)"
            ),
        )

    room = capacity.effective - len(active)
    if room <= 0:
        bound = next((b for b in capacity.bounds if b.name == capacity.binding), None)
        detail = bound.detail() if bound is not None else capacity.binding
        if capacity.binding == "resource":
            return Decision(admitted=False, reason="resource ceiling reached", bound="resource", detail=detail)
        if capacity.binding == "disjoint":
            return Decision(
                admitted=False,
                reason="no further file-disjoint ready lane",
                bound="disjoint",
                detail=detail,
            )
        return Decision(
            admitted=False,
            reason="pool full",
            bound="pool",
            detail=f"{len(active)} active of a resolved ceiling of {capacity.effective}",
        )

    return Decision(
        admitted=True,
        reason="admitted",
        bound=capacity.binding,
        detail=f"{len(active)} active of {capacity.effective} (bound by {capacity.binding})",
    )


def _collision(lane: Lane, active: Sequence[Lane]) -> Collision | None:
    """The files ``lane`` claims that an in-flight lane already owns."""
    if not lane.files:
        return None
    owners: dict[str, str] = {}
    for other in active:
        for name in other.files or ():
            owners.setdefault(name, other.id)
    overlap = tuple(sorted(f for f in lane.files if f in owners))
    if not overlap:
        return None
    return Collision(lane=lane.id, holder=owners[overlap[0]], files=overlap)


# --- the anti-formality self-control ----------------------------------------


def self_control() -> list[str]:
    """Prove every bound here can actually bind (anti-formality, GR-12).

    Returns a problem per failed control; an empty list means every control held.
    Each of the three bounds is provoked, and for each provocation the *relaxed*
    input is also asserted admitted — so a resolver that always returns the same
    verdict fails, and a control whose two paths share an exit code cannot pass.
    """
    problems: list[str] = []

    def expect(name: str, condition: bool, detail: str) -> None:
        if not condition:
            problems.append(f"capacity-self-control['{name}']: {detail}")

    rich = Resources(ram_available_bytes=32 * 1024**3, tmp_available_bytes=32 * 1024**3)

    a = Lane(id="lane-a", issue=1, files=("fleet/a.py",))
    b = Lane(id="lane-b", issue=2, files=("fleet/a.py", "fleet/b.py"))
    c = Lane(id="lane-c", issue=3, files=("fleet/c.py",))
    opaque = Lane(id="lane-d", issue=4, files=None)

    # --- bound 1: the pool (FLEET_MAX_AGENTS sets the default) ---------------
    env = {ENV_MAX_AGENTS: "1"}
    cap = resolve_capacity(ready=[a, b, c], env=env, resources=rich)
    expect("max-agents-sets-the-default", cap.bounds[0].limit == 1, f"got {cap.bounds[0].limit}")
    held = admit(c, capacity=cap, active=[a])
    expect("pool-bound-holds", not held.admitted and held.bound == "pool", f"got {held}")
    relaxed = resolve_capacity(ready=[a, b, c], env={ENV_MAX_AGENTS: "3"}, resources=rich)
    expect("pool-relaxed-admits", admit(c, capacity=relaxed, active=[a]).admitted, "3 agents must admit 2")
    defaulted = default_max_agents({}, focus_max_agents=0, pool_size=7)
    expect("focus-zero-means-the-pool", defaulted == 7, f"got {defaulted}")
    expect(
        "focus-positive-overrides-pool",
        default_max_agents({}, focus_max_agents=4, pool_size=7) == 4,
        "a positive focus max_agents must beat the pool",
    )
    expect(
        "env-beats-focus",
        default_max_agents({ENV_MAX_AGENTS: "2"}, focus_max_agents=4, pool_size=7) == 2,
        "FLEET_MAX_AGENTS must beat the focus",
    )
    expect(
        "env-zero-means-the-pool",
        default_max_agents({ENV_MAX_AGENTS: "0"}, focus_max_agents=4, pool_size=7) == 7,
        "FLEET_MAX_AGENTS=0 is the focus schema's 'the pool', not an error",
    )
    try:
        default_max_agents({ENV_MAX_AGENTS: "zero"}, pool_size=7)
    except CapacityConfigError:
        pass
    else:
        problems.append("capacity-self-control['bad-max-agents-refused']: a typo was silently defaulted")
    try:
        resolve_capacity(env={ENV_LANE_RAM_MIB: "0"})
    except CapacityConfigError:
        pass
    else:
        problems.append("capacity-self-control['zero-ram-budget-refused']: a zero budget is a division by zero")

    # --- bound 2: the file-disjoint bound (AO-GR-24) -------------------------
    sel = disjoint_bound([a, b, c])
    expect("disjoint-selects-both", sel.limit == 2, f"expected 2 of 3, got {sel.limit}")
    expect(
        "disjoint-names-the-collision",
        len(sel.collisions) == 1
        and sel.collisions[0].lane == "lane-b"
        and sel.collisions[0].holder == "lane-a"
        and sel.collisions[0].files == ("fleet/a.py",),
        f"the collision must name lane, holder and file, got {sel.collisions}",
    )
    expect("disjoint-admits-the-clean-lane", "lane-c" in sel.admitted, f"got {sel.admitted}")
    expect(
        "collision-holds-even-with-room",
        not admit(b, capacity=resolve_capacity(ready=[a, b], env={ENV_MAX_AGENTS: "9"}, resources=rich), active=[a]).admitted,
        "a file collision must hold even when a worker is free",
    )
    unattributed = disjoint_bound([a, opaque])
    expect(
        "undeclared-is-named-never-silent",
        unattributed.unattributable == ("lane-d",) and "lane-d" in unattributed.detail(),
        f"an undeclared lane must be reported by name, got {unattributed}",
    )
    strict_sel = disjoint_bound([a, opaque], files_required=True)
    expect(
        "undeclared-is-held-in-strict-mode",
        strict_sel.limit == 1 and "lane-d" not in strict_sel.admitted,
        f"strict mode must hold the undeclared lane, got {strict_sel}",
    )
    strict_cap = resolve_capacity(
        ready=[a, opaque], env={ENV_MAX_AGENTS: "9", ENV_FILES_REQUIRED: "1"}, resources=rich
    )
    expect(
        "strict-mode-holds-two-undeclared",
        not admit(
            Lane(id="lane-e", issue=5, files=None), capacity=strict_cap, active=[opaque]
        ).admitted,
        "in strict mode two undeclared lanes may not run together",
    )

    # --- bound 3: the resource ceiling (RAM / /tmp headroom) -----------------
    tight = Resources(ram_available_bytes=512 * 1024**2, tmp_available_bytes=32 * 1024**3)
    tight_cap = resolve_capacity(ready=[c], env={ENV_MAX_AGENTS: "9"}, resources=tight)
    expect(
        "resource-ceiling-computed-not-guessed",
        tight_cap.effective == 0 and tight_cap.binding == "resource",
        f"512 MiB must not carry a 1536 MiB lane, got {tight_cap.line()}",
    )
    starved = admit(c, capacity=tight_cap, active=[])
    expect(
        "resource-bound-holds",
        not starved.admitted and starved.bound == "resource",
        f"the held directive must name the resource bound, got {starved}",
    )
    tmp_bound = resource_bound(
        Resources(ram_available_bytes=32 * 1024**3, tmp_available_bytes=600 * 1024**2),
        ram_budget_mib=1024,
        tmp_budget_mib=512,
    )
    expect("tmp-headroom-binds", tmp_bound.limit == 1, f"600 MiB /tmp must carry one 512 MiB lane, got {tmp_bound}")

    unmeasured = resolve_capacity(
        ready=[c], env={ENV_MAX_AGENTS: "9"},
        resources=Resources(ram_available_bytes=None, tmp_available_bytes=None, problems=("/proc/meminfo: unreadable",)),
    )
    expect(
        "unmeasured-is-cannot-assess",
        not unmeasured.assessed and unmeasured.effective == 0 and "CANNOT-ASSESS" in unmeasured.line(),
        f"an unreadable measurement must never read as unbounded, got {unmeasured.line()}",
    )
    expect(
        "cannot-assess-holds",
        not admit(c, capacity=unmeasured, active=[]).admitted,
        "a CANNOT-ASSESS ceiling must hold, never admit",
    )

    # --- the resolution itself ------------------------------------------------
    full = resolve_capacity(ready=[a, b, c], env={ENV_MAX_AGENTS: "9"}, resources=rich)
    expect(
        "effective-is-the-minimum",
        full.effective == 2 and full.binding == "disjoint",
        f"3 ready lanes, 2 disjoint, pool 9, room for 22 -> expected 2 by disjoint, got {full.line()}",
    )
    expect(
        "every-bound-is-reported",
        [b.name for b in full.bounds] == ["pool", "disjoint", "resource"],
        f"all three bounds must be reported, got {[b.name for b in full.bounds]}",
    )
    expect("line-names-the-binding-bound", "bound_by=disjoint" in full.line(), full.line())
    return problems
