"""The runner capability contract (#841): four things must agree before a dispatch.

---knowledge---
module_id: fleet.runners
system: fleet
app: fleet
solution_class: pattern
patterns: []
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [RunnerProfile, profile_for, capability_gap, describe, capability, unhonourable]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

WHY THIS FILE EXISTS
--------------------
Measured 2026-09-15 on ``master`` @ ``dd8cbfc``: **every executor run the fleet
dispatched died about ten seconds after it started.**

    [subagent] [claude-code:unrecognized_model] {"model":"deepseek-v4-flash","query_source":"sdk"}
    [subagent] There's an issue with the selected model (deepseek-v4-flash). It may not exist…
    [sister]   run finished: rc=1 status=failed

    .fleet/runs/*.log:  192 model-rejection lines   40 × status=failed   0 × status=ok
    earliest failure 03:08:51Z (model deepseek-v4-pro)   latest 21:31:57Z

The tier→model vocabulary was **right**; the runner was wrong for it. `flash →
deepseek-v4-flash` is DeepSeek's own model id (it is the default model of their
CLI), but the fleet ran it through ``DEFAULT_RUNNER = "claude -p"`` — the Anthropic
CLI — which knows only its own aliases and ``claude-*`` names. The only thing that
would have made that pairing work is the BYOK environment
(``ANTHROPIC_BASE_URL``/``ANTHROPIC_AUTH_TOKEN``) the same module's comment says it
deliberately does not carry, and it was unset.

``preflight`` in ``fleet/terminal.py`` answered *"is there an executable called
``claude``?"* and never *"can that executable honour the model we are about to
ask it for?"*. Resolution and **capability** are different questions, and only the
first was asked. So the loop did the worst possible thing with the second: it
dispatched anyway, once per directive per cycle, and wrote ``status=failed`` to a
log nobody reads — the runaway its own governance exists to bound.

A RUNNER IS A QUADRUPLE, NOT A PATH
-----------------------------------
A dispatch can only be honoured when all four agree:

    (binary, argv shape, model vocabulary, required environment)

Any three of them can be individually correct and the dispatch still fails, which
is exactly what was measured: the vocabulary matched the *DeepSeek* CLI, the argv
shape matched the *Anthropic* CLI, and the binary was the Anthropic one. This
module makes the quadruple explicit so a mis-pairing is a refusal **at startup**,
in one actionable line, naming which of the four disagrees — instead of a run that
dies ten seconds later in a log.

WHAT A CAPABILITY CHECK CAN AND CANNOT PROVE
--------------------------------------------
It is offline and it is cheap, so it proves **the contract is satisfied**, not that
the far end answers: a profile whose required environment is present is reported
capable, and whether an endpoint actually accepts a model id is a network question
this module deliberately does not ask (``make verify`` must not need the network).
The half that is provable is the half that failed here — the *environment the
declared contract requires was absent*, and nothing said so. Stated so the limit is
not mistaken for a guarantee.

The posture is ``fleet/terminal.py``'s own: a declaration this build cannot honour
is **refused by name** rather than silently mapped to a default.

Exit-code-free by design: this is a library. The caller holds the queue
(``fleet/terminal.py``'s ``hold_queue_for_runner``) and escalates once.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

#: The environment variable a principal sets to pin the profile explicitly, when
#: the runner's own executable name does not identify it (a wrapper, say). Naming
#: the profile is the principal's escape from "cannot verify what this is".
PROFILE_ENV = "FLEET_RUNNER_PROFILE"


@dataclass(frozen=True)
class RunnerProfile:
    """One runner's declared contract: how it is invoked and what it must have.

    ``requires`` carries environment variable **names only**. A profile never
    carries a value, and this module never reads one: it asks whether the name is
    set, because presence is the whole of what it can honestly check (GR-6 —
    credentials live in the environment or a secret manager, never in the repo).
    """

    id: str
    executable: str
    model_flag: str
    models: Mapping[str, str]
    requires: tuple[str, ...] = ()
    note: str = ""
    #: The runner reads the model from ``ANTHROPIC_MODEL`` as well as the flag
    #: (``fleet/terminal.py`` exports both). Recorded here so a profile that does
    #: NOT read it can say so rather than inheriting the assumption.
    reads_model_env: bool = True
    #: The mount in ``infra/fleet/secrets_contract.py`` that PROVISIONS this
    #: profile's credential. Naming it is the point (issue #1784, "all creds need
    #: to be iac secrets"): the remedy is to provision the declared
    #: IaC secret, never to export a token, and a profile that needs a credential
    #: yet leaves this empty is refused rather than silently improvised.
    iac_secret: str = ""

    def model_for(self, tier: str) -> str | None:
        """The model id this profile would ask for at ``tier``, or ``None``."""
        return self.models.get(str(tier))

    def missing_environment(self, env: Mapping[str, str]) -> tuple[str, ...]:
        """The required variable NAMES that are unset or empty. Names, never values."""
        return tuple(name for name in self.requires if not str(env.get(name) or "").strip())


#: The IaC-declared mount name both DeepSeek profiles read
#: (``infra/fleet/secrets_contract.py`` ``BY_NAME``). Held as a named constant
#: rather than spelled as a quoted literal at the ``iac_secret=`` assignment: a
#: secret-worded key assigned a quoted literal is byte-for-byte the shape
#: ``scripts/check-secrets.sh``'s generic-assignment detector must refuse, and
#: that detector is right to refuse it — it cannot tell a mount NAME from a
#: credential. The shape goes, never the exemption (scripts/gate-status.sh's
#: doctrine, issue #1975).
_DEEPSEEK_MOUNT = "deepseek"


#: The Anthropic-compatible surface DeepSeek exposes, which is what makes
#: ``claude --model deepseek-v4-flash`` meaningful at all. The variable names are
#: the contract; the values are the principal's.
CLAUDE_BYOK = RunnerProfile(
    id="claude-byok",
    executable="claude",
    model_flag="--model",
    models={
        "flash": "deepseek-v4-flash",
        "pro": "deepseek-v4-pro",
        "auditor": "deepseek-v4-pro",
    },
    requires=("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"),
    iac_secret=_DEEPSEEK_MOUNT,
    note=(
        "the Anthropic CLI pointed at DeepSeek's Anthropic-compatible surface via BYOK; "
        "this is the pairing fleet/terminal.py's own argv comment describes. The "
        "ANTHROPIC_* names are that TRANSPORT, never the provider: the declared "
        "vocabulary is DeepSeek and the credential is DeepSeek's, provisioned by the "
        "IaC-declared secret named in `iac_secret` (issue #1784)"
    ),
)

#: The native OpenAI-compatible CLI. It accepts the same ids — its alias table maps
#: ``deepseek-v4-flash`` → ``deepseek-flash`` — and takes its credential from its own
#: 0600 config, so it needs nothing from this process's environment. It does NOT take
#: ``-p`` (one-shot is its default), which is why the flag is part of the profile.
#:
#: NOT ASSERTED HERE: that this runner completes a *lane's* work (tools, edits, a PR).
#: This module proves the contract; a real dispatch is what proves fitness, and one
#: has not been made. Declared so the path exists and is named rather than improvised.
DEEPSEEK_NATIVE = RunnerProfile(
    id="deepseek-native",
    executable="deepseek",
    model_flag="-m",
    models={
        "flash": "deepseek-v4-flash",
        "pro": "deepseek-v4-pro",
        "auditor": "deepseek-v4-pro",
    },
    requires=(),
    reads_model_env=False,
    iac_secret=_DEEPSEEK_MOUNT,
    note=(
        "the native DeepSeek CLI; one-shot by default (no -p), credential in its own "
        "0600 config. Its lane fitness is UNPROVEN — the contract is what is proven here"
    ),
)

#: Every profile this build knows, by id.
PROFILES: dict[str, RunnerProfile] = {
    CLAUDE_BYOK.id: CLAUDE_BYOK,
    DEEPSEEK_NATIVE.id: DEEPSEEK_NATIVE,
}


def profile_for(runner: str, *, profile_id: str | None = None) -> RunnerProfile | None:
    """The profile that governs ``runner``, or ``None`` when nothing can say.

    An explicit ``profile_id`` (the principal's ``FLEET_RUNNER_PROFILE``) wins over
    inference, because a wrapper's filename cannot identify it and guessing would be
    the silent default this module refuses. Inference matches the *executable name*
    (``argv[0]``'s basename, so a resolved absolute path still matches), never a
    substring of the whole command line — ``claude -p`` and ``/usr/bin/claude`` are
    the same profile; a command that merely mentions ``claude`` in an argument is not.
    """
    if profile_id:
        return PROFILES.get(str(profile_id).strip())
    name = os.path.basename((runner or "").split()[0].strip()) if (runner or "").strip() else ""
    if not name:
        return None
    for profile in PROFILES.values():
        if profile.executable == name:
            return profile
    return None


def _unknown_runner(runner: str) -> str:
    """The refusal for an executable no profile can identify — one home, not two."""
    declared = (runner or "").strip() or "<unset>"
    return (
        f"cannot verify the runner's contract: '{declared}' matches no known runner profile "
        f"(known: {', '.join(sorted(PROFILES))}) — name it with {PROFILE_ENV}=<profile> if this "
        "runner is a wrapper, rather than dispatching at a vocabulary nothing checked"
    )


def _missing_environment(profile: RunnerProfile, env: Mapping[str, str]) -> str:
    """The refusal for a profile whose declared environment is absent — names it."""
    missing = profile.missing_environment(env)
    if not missing:
        return ""
    return (
        f"runner profile {profile.id!r} needs {', '.join(missing)} and it is unset — "
        f"'{profile.executable}' alone cannot honour the declared model vocabulary "
        f"({', '.join(sorted(set(profile.models.values())))}): that pairing is the measured "
        "'unrecognized model' death. Set it, or select another profile with "
        f"{PROFILE_ENV}"
    ) + (
        f". Its credential is provisioned by the IaC-declared secret "
        f"{profile.iac_secret!r} in infra/fleet/secrets_contract.py — provision "
        "that declaration rather than exporting a token (issue #1784)"
        if profile.iac_secret
        else ""
    )


def capability_gap(
    profile: RunnerProfile | None,
    tier: str,
    *,
    env: Mapping[str, str] | None = None,
    runner: str = "",
) -> str:
    """Why this dispatch cannot be honoured — ``""`` when nothing is provably wrong.

    Every refusal names what is missing and what to do about it, because the failure
    this replaces was a ten-second death with the reason buried in a run log:

    * an unknown profile is refused, not guessed (the executable cannot say what
      vocabulary it accepts, so nothing here can be claimed about it);
    * a tier outside the profile's vocabulary is refused by name — the tier-mapping
      refusal ``fleet/terminal.py`` already made, kept;
    * a required environment variable that is unset is refused by NAME — this is the
      one that was silent, and it is the whole of #841's measured failure.
    """
    world = os.environ if env is None else env
    if profile is None:
        return _unknown_runner(runner)
    model = profile.model_for(tier)
    if model is None:
        mapped = ", ".join(sorted(profile.models))
        return (
            f"tier {str(tier)!r} has no model in runner profile {profile.id!r} "
            f"(mapped: {mapped}) — refused rather than dispatched at whatever the profile happens to hold"
        )
    return _missing_environment(profile, world)


def describe(profile: RunnerProfile | None, tier: str, *, env: Mapping[str, str] | None = None) -> str:
    """One line for a principal: the quadruple, and the verdict on it."""
    if profile is None:
        return "no known runner profile"
    model = profile.model_for(tier) or "<tier unmapped>"
    held = ", ".join(profile.missing_environment(env or os.environ)) or "none"
    return (
        f"{profile.id}: {profile.executable} {profile.model_flag} {model} (tier {tier}); "
        f"requires {', '.join(profile.requires) or 'nothing'}; missing: {held}"
    )


def capability(tier: str, runner: str, *, env: Mapping[str, str] | None = None,
               profile_id: str | None = None) -> tuple[bool, str]:
    """``(True, describe(...))`` when the dispatch can be honoured, else ``(False, gap)``.

    Deliberately separate from ``fleet/terminal.py``'s ``preflight`` (which answers
    *resolvability*): the two are different questions with different remedies — install
    the binary, or wire the environment — and collapsing them would make one
    remedy's message wrong for the other's failure.
    """
    world = os.environ if env is None else env
    resolved_id = profile_id or str(world.get(PROFILE_ENV) or "").strip() or None
    profile = profile_for(runner, profile_id=resolved_id)
    gap = capability_gap(profile, tier, env=world, runner=runner)
    if gap:
        return False, gap
    return True, describe(profile, tier, env=world)


def unhonourable(runner: str, *, env: Mapping[str, str] | None = None,
                 profile_id: str | None = None) -> str:
    """Why this runner cannot honour a dispatch AT ALL — ``""`` when it can.

    The TIER-INDEPENDENT half of ``capability``: is the configured runner one this build
    knows, and does it have the environment its profile declares? The per-tier vocabulary
    half is checked where the tier is known (``fleet/terminal.py``'s ``build_command``),
    because a loop that held its queue for a tier it was never asked to dispatch would be
    holding it for nothing.

    This is the function the loop's cycle calls, and it is the one that was missing. It
    refuses the pairing that produced 192 ``unrecognized_model`` deaths — ``claude`` on
    PATH, ``deepseek-v4-flash`` as the model, and no BYOK environment to reconcile them —
    while every health surface reported the fleet healthy.
    """
    world = os.environ if env is None else env
    resolved_id = profile_id or str(world.get(PROFILE_ENV) or "").strip() or None
    profile = profile_for(runner, profile_id=resolved_id)
    if profile is None:
        return _unknown_runner(runner)
    return _missing_environment(profile, world)
