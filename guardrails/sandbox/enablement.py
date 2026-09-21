"""Sandbox runtime enablement flag: ONE explicit, reversible switch (issue #636).

The sandbox runtimes (``docker``, ``firecracker``) ship ``enabled=False`` and
refuse to execute anything while they are off. That is the correct fail-closed
default, but it leaves the deployment question unanswered: *how does an
operator turn a runtime on, and what exactly changes when they do?* This module
answers it with a single declared flag so the answer is testable — and so the
OFF-by-default posture is a property of the code, not of everyone's memory.

The contract (declared in ``enablement.schema.json``):

* **Exactly one flag per sandbox.** :class:`SandboxEnablement` holds the flag;
  there is no second, implicit switch (no environment variable is consulted —
  an env var would make the flag unobservable and unforgeable in a review).
* **OFF is the only automatic state.** :meth:`SandboxEnablement.__post_init__`
  refuses construction with ``enabled=True`` unless ``activate()`` is called
  first; :func:`assert_not_auto_on` is the negative control a gate runs to
  prove that refusal still bites. A flag that could default ON is precisely
  the failure mode #636 exists to prevent.
* **Enabling is reversible and observable.** :meth:`enable` constructs the
  requested runtime *disabled* and only then flips the flag, so a runtime that
  cannot exist (unknown name, missing dependency) fails **before** the flag
  changes — never a half-enabled state. :meth:`disable` returns the sandbox to
  the OFF posture and the injected runtime refuses again.
* **Enabling a runtime is not sandboxing.** A real ``docker``/``firecracker``
  runtime is a *declared seam*: once enabled, ``run`` still refuses with a
  :class:`~sandbox.errors.RuntimeExecutionError` rather than pretending to
  execute. There is no code path that turns "flag on" into a fabricated
  success. The round-trip below is therefore provable on the **injectable
  offline runtime** with no docker daemon and no network.

Everything here is stdlib-only and offline.


---knowledge---
module_id: guardrails.sandbox.enablement
system: guardrails
app: sandbox
solution_class: enterprise
patterns: [feature-flag-gated-off, single-seam, declared-authority]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [EnablementError, SandboxEnablement, default_enablement, assert_not_auto_on]
invariants: "exactly one flag per sandbox, and no environment variable is consulted, so the OFF-by-default posture is a property of the code rather than of everyone's memory"
gotchas: "an env var would make the flag unobservable and unforgeable in a review, which is why the contract forbids one"
related: ["#636", "#58"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .docker import DockerRuntime
from .errors import SandboxConfigError, SandboxError
from .firecracker import FirecrackerMicroVmRuntime
from .offline import OfflineRuntime
from .runtime import Runtime

#: The closed vocabulary of runtime names a deployment may enable.
RUNTIME_NAMES = ("offline", "docker", "firecracker")

#: Runtimes that are *declared* (flag-gated) rather than injectable fakes.
DECLARED_RUNTIMES = ("docker", "firecracker")


class EnablementError(SandboxError):
    """The enablement flag was driven into an invalid state."""


def _build_runtime(name: str, *, enabled: bool) -> Runtime:
    """Construct runtime ``name`` with an explicit initial ``enabled`` state.

    Kept private and total: an unknown name is a hard
    :class:`~sandbox.errors.SandboxConfigError`, never a silent fallback to a
    permissive default.
    """
    if name == "offline":
        return OfflineRuntime(enabled=enabled)
    if name == "docker":
        return DockerRuntime(enabled=enabled)
    if name == "firecracker":
        return FirecrackerMicroVmRuntime(enabled=enabled)
    raise SandboxConfigError(
        f"unknown sandbox runtime {name!r} (declared: {RUNTIME_NAMES})"
    )


@dataclass
class SandboxEnablement:
    """The single feature flag governing whether a sandbox runtime may run.

    Ships OFF and stays OFF unless somewhere calls :meth:`enable`. ``runtime``
    is ``None`` while disabled — there is no live runtime to leak a call into.
    """

    name: str = "offline"
    enabled: bool = False
    runtime: Optional[Runtime] = None
    _activations: int = 0

    def __post_init__(self) -> None:
        if self.name not in RUNTIME_NAMES:
            raise SandboxConfigError(
                f"unknown sandbox runtime {self.name!r} "
                f"(declared: {RUNTIME_NAMES})"
            )
        # The OFF posture is structural. A caller that wants the flag ON must
        # say so through enable(), which is the act a reviewer can see; there
        # is deliberately no constructor path to an already-on sandbox.
        if self.enabled:
            raise EnablementError(
                f"sandbox enablement for {self.name!r} cannot be constructed "
                "already ON — call enable() (a flag that starts ON is a "
                "formality; AO-GR-6)"
            )
        if self.runtime is not None:
            raise EnablementError(
                "sandbox enablement cannot be constructed with a live runtime "
                "while the flag is OFF"
            )

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    @property
    def is_enabled(self) -> bool:
        return bool(self.enabled)

    @property
    def is_declared(self) -> bool:
        """True when this flag governs a declared (seam-only) runtime."""
        return self.name in DECLARED_RUNTIMES

    def activations(self) -> int:
        """How many times this flag has been turned ON (audit-able count)."""
        return self._activations

    # ------------------------------------------------------------------ #
    # writes — the enable/disable round-trip
    # ------------------------------------------------------------------ #
    def enable(self, factory: Optional[Callable[..., Runtime]] = None) -> Runtime:
        """Turn the flag ON and return the live runtime.

        The runtime is built **disabled first**; only a runtime that constructs
        successfully has its flag flipped. Enabling an already-enabled sandbox
        returns the existing runtime unchanged (idempotent, no second runtime).
        """
        if self.enabled and self.runtime is not None:
            return self.runtime

        runtime = (
            factory(self.name, enabled=False)
            if factory is not None
            else _build_runtime(self.name, enabled=False)
        )
        # Only now that construction succeeded does the flag move.
        runtime.enabled = True
        self.runtime = runtime
        self.enabled = True
        self._activations += 1
        return runtime

    def disable(self) -> None:
        """Turn the flag OFF and drop the runtime (idempotent).

        After this call the sandbox is back to its shipped posture: no live
        runtime, and the next dispatch refuses.
        """
        if self.runtime is not None:
            self.runtime.enabled = False
        self.runtime = None
        self.enabled = False

    def require(self) -> Runtime:
        """The live runtime, or a hard error (fail closed, never ``None``)."""
        if not self.enabled or self.runtime is None:
            raise EnablementError(
                f"sandbox runtime {self.name!r} is not enabled — enable the "
                "flag before dispatching (a disabled sandbox never degrades "
                "into an unsandboxed run)"
            )
        return self.runtime

    # ------------------------------------------------------------------ #
    # the probe a gate calls after a negative-control mutation
    # ------------------------------------------------------------------ #
    def assert_not_auto_on(self) -> "SandboxEnablement":
        """Negative control: a sandbox constructed without ``enable()`` MUST be OFF.

        Returns ``self`` when the invariant holds. Raises
        :class:`EnablementError` when the flag is ON without an explicit
        activation — i.e. when the OFF-by-default guarantee has stopped
        biting. A gate runs this after mutating this module's default so a
        regression cannot pass silently.
        """
        if self.enabled or self.runtime is not None or self._activations != 0:
            raise EnablementError(
                f"sandbox enablement for {self.name!r} is ON without an "
                f"explicit enable() (activations={self._activations}) — the "
                "OFF-by-default guarantee is broken"
            )
        return self


def default_enablement(name: str = "offline") -> SandboxEnablement:
    """The shipped posture: a disabled flag for ``name`` (AO-GR-6)."""
    return SandboxEnablement(name=name)


def assert_not_auto_on(name: str = "docker", *, factory: Optional[Callable[..., SandboxEnablement]] = None) -> None:
    """Module-level negative control used by the enablement gate.

    Proves that a freshly constructed flag — for a *declared* runtime, the
    riskiest case — is OFF and has no live runtime. Raises
    :class:`AssertionError` if the model ever accepts an auto-ON flag, so a
    gate that calls this cannot report a false green.

    ``factory`` lets a gate substitute the very constructor under test; when
    omitted the shipped :func:`default_enablement` is used. Either way the
    assertion is about the *result*, not about this function's opinion.
    """
    build = factory or default_enablement
    flag = build(name)
    try:
        flag.assert_not_auto_on()
    except EnablementError as exc:
        # The flag is ON without an explicit activation — the invariant broke.
        raise AssertionError(
            f"sandbox enablement for {name!r} was ON without an explicit "
            f"enable() — default-OFF is not enforced ({exc})"
        ) from exc
    # The method returned normally: the freshly built flag is genuinely OFF.
    return
