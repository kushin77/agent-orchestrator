"""portal.server.nous — the console's Nous provider surface adapter (issue #1561).

---knowledge---
module_id: portal.server.nous
system: portal
app: server
solution_class: enterprise
patterns: [declaration-reader, injected-probe, ttl-cache, rate-limited-probe, stale-labelled]
derives_from: portal/server/sessions.py
owner_sme: platform-sme
tier: L1
interfaces: [NousSurface, NousSurface.overview]
invariants: "a stale reading is always labelled stale with its measured age, and is never presented as current"
gotchas: "the provider contract declares NO balance endpoint, so the credits section reports the declared meter plus metered debits, never a fabricated balance"
related: ["#1561", "#1559", "#1748"]
do_not_duplicate: portal/server/settings.py
---knowledge---

WHY this exists: the console has no surface for the Nous provider at all — no
route, no view, no nav entry (SPOG-REVIEW-2026-09-20.md §1, §3). Issue #1561
adds one, fed by the gateway's own Nous integration. This adapter is the
server-side half; ``portal/static/views/nous.html`` is the view.

**What this adapter is allowed to know.** It renders four sections, and every
value in each one comes from a declaration this checkout already carries or from
a record this checkout actually wrote — never from a guess:

* **status/heartbeat** — presence of the declared credential
  (``infra/terraform/provider-credentials.json``, issue #1748) and the state of
  the gate it is projected behind (``infra/feature-flags/registry.yaml``), plus
  a bounded live probe of the provider's own catalog endpoint.
* **model catalog + per-model cost** — ``gateway/finops/provider-credits.yaml``
  through its own reader (``gateway.finops.provider_credits.load_credits``): the
  declared plan ceilings, top-up amounts, and every priced model with its
  input/output USD-per-MTok rate and context window.
* **credits balance + burn rate** — the declared meter's tracking mode plus the
  **actual metered debits** for provider ``nous`` read from the durable usage
  store the rest of the console already consumes
  (``portal.server.livestore.DEFAULT_USAGE_STORE``).
* **tool-gateway usage** — the same usage records, grouped per model.

**There is no balance endpoint, and this adapter does not pretend otherwise.**
The published contract documents ``POST /chat/completions`` and
``POST /completions`` only; ``provider-credits.yaml`` declares
``balanceEndpoint: null`` and ``balanceTracking: manual-top-ups`` and says so in
its own note. So ``credits.remainingUsd`` is ``null`` unless a plan is pinned (a
ceiling for an undeclared plan would be an invented number), and the reason is
carried in the payload beside it. A surface that printed a balance here would be
printing a number nobody read.

**Cost discipline against a billed provider (the L1 half of this issue).** Two
rules, because a surface that spends money to render is a defect:

1. **The probe never bills.** It is a single unauthenticated ``GET
   {baseUrl}/models`` — the endpoint ``provider-credits.yaml`` already names as
   the catalog source, which answers without a credential. No ``Authorization``
   header is sent at all, so the probe cannot leak the key and cannot debit the
   account. The billed ``chat/completions`` path is never called from a render.
2. **Reloads do not reach the provider.** ``_ProbeCache`` enforces both a **TTL**
   (a reading younger than ``cache_ttl_seconds`` is reused with no upstream call)
   and a **minimum probe interval** floor applied *even on a cache miss* — so a
   burst of reloads, or a wall of concurrent operators, still produces at most
   one upstream request per interval. A provider that is down cannot be hammered
   into ratelimiting the account by the console itself.

**And the honesty rule on failure.** A probe failure does not blank the surface
and does not silently serve an old number as a current one: the last successful
reading is returned with ``state: "stale"``, its ``valueAt`` timestamp and its
measured ``ageSeconds``, so the view renders "last known, N seconds old" rather
than a value presented as current. With no prior reading there is nothing to
label, and the state is ``unreachable`` — or ``not-configured``, when the
credential the operator step owes is simply absent.

No socket is opened by the caller: the probe is injected (``probe=``), so the
whole adapter is exercised offline, and the default probe is resolved only when a
credential is actually configured.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from portal.server.config_flags import NOUS_SURFACE, surface_enabled
from portal.server.livestore import DEFAULT_USAGE_STORE

SCHEMA = "ao.portal-nous/v1"

#: The provider id this surface renders. It is the gateway's own id (issue
#: #1559) — the *billed cloud* adapter, deliberately distinct from the keyless
#: local ``hermes`` hop.
PROVIDER_ID = "nous"

#: Where the operator takes the account actions the console does not replicate
#: (billing changes, top-ups). The view renders this as a link-out.
MANAGE_ACCOUNT_URL = "https://portal.nousresearch.com"

#: Named so a missing declaration is reported by its repo-relative path even when
#: the credits reader itself could not be imported. The reader pins the real
#: path, so the two can never drift.
CREDIT_DECLARATION = "gateway/finops/provider-credits.yaml"

#: Declarations this surface consumes, repo-root relative.
CREDENTIALS_RELATIVE = Path("infra") / "terraform" / "provider-credentials.json"
FEATURE_FLAGS_RELATIVE = Path("infra") / "feature-flags" / "registry.yaml"
MODULE_RELATIVE = Path("gateway") / "catalog" / "modules" / "nous" / "module.json"

#: The service whose entry in the credential declaration is this provider's.
CREDENTIAL_SERVICE = "gateway"

#: Reachability states. ``live`` is a reading taken inside the TTL; ``stale`` is a
#: last-known reading whose age is reported; ``unreachable`` is a failed probe
#: with nothing to fall back to; ``not-configured`` is the operator step being
#: outstanding, so there is nothing to probe with.
STATE_LIVE = "live"
STATE_STALE = "stale"
STATE_UNREACHABLE = "unreachable"
STATE_NOT_CONFIGURED = "not-configured"

#: A reading is reused for this long before the provider is asked again.
DEFAULT_TTL_SECONDS = 60.0

#: The floor between two upstream attempts, applied even past the TTL: a burst of
#: reloads makes at most one call per interval, whatever the cache says.
DEFAULT_MIN_PROBE_INTERVAL_SECONDS = 60.0

#: How long the default probe waits on the provider before giving up.
DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0


class NousProbeError(RuntimeError):
    """A live probe did not produce a usable catalog reading."""


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(float(seconds), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is the accepted stack
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return None


def _within_window(stamp: Any, cutoff: float) -> bool:
    """True when an ISO-8601 stamp sits at/after ``cutoff`` (epoch seconds).

    An unparsable stamp is **not** in the window: an age nobody can measure must
    not inflate a burn-rate figure.
    """
    if not isinstance(stamp, str) or not stamp:
        return False
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() >= cutoff


class _ProbeCache:
    """One reading, its age, and the floor between upstream attempts.

    The cache exists because the thing on the other side is a *billed* external
    provider: rendering a page must not cost money and must not ratelimit the
    account. It therefore holds two rules rather than one —

    * **TTL** — a reading younger than ``ttl`` is returned as-is, with no call.
    * **floor** — no upstream attempt within ``min_interval`` of the previous
      attempt, *even when the TTL has expired*. Without it, a provider that is
      down (so nothing is ever cached) would be re-probed on every single
      reload; with it, a burst of reloads is one attempt plus N cache reads.
    """

    def __init__(
        self,
        *,
        ttl: float,
        min_interval: float,
        clock: Callable[[], float],
    ) -> None:
        self._ttl = float(ttl)
        self._floor = float(min_interval)
        self._clock = clock
        self._value: Optional[dict[str, Any]] = None
        self._value_at: Optional[float] = None
        self._attempt_at: Optional[float] = None

    def read(self, prober: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """A reading plus how it was obtained.

        ``{"state", "value", "value_at", "age_seconds", "cached", "attempted",
        "reason"}`` — ``reason`` is present when a probe was tried and failed.
        """
        now = self._clock()
        if (
            self._value is not None
            and self._value_at is not None
            and (now - self._value_at) < self._ttl
        ):
            return self._as_live(now, attempted=False)
        if self._attempt_at is not None and (now - self._attempt_at) < self._floor:
            # Rate-limited: do not reach the provider, whatever the TTL says.
            return self._fallback(now, attempted=False)
        self._attempt_at = now
        try:
            value = prober()
        except Exception as exc:  # noqa: BLE001 - any probe failure is a state
            return self._fallback(now, attempted=True, reason=str(exc))
        self._value = value
        self._value_at = now
        result = self._as_live(now, attempted=True)
        result["cached"] = False
        return result

    def _as_live(self, now: float, *, attempted: bool) -> dict[str, Any]:
        return {
            "state": STATE_LIVE,
            "value": self._value,
            "value_at": self._value_at,
            "age_seconds": int(max(0.0, now - float(self._value_at or now))),
            "cached": True,
            "attempted": attempted,
        }

    def _fallback(
        self, now: float, *, attempted: bool, reason: str = ""
    ) -> dict[str, Any]:
        if self._value is not None and self._value_at is not None:
            result = self._as_live(now, attempted=attempted)
            result["state"] = STATE_STALE
            result["reason"] = reason
            return result
        return {
            "state": STATE_UNREACHABLE,
            "value": None,
            "value_at": None,
            "age_seconds": None,
            "cached": False,
            "attempted": attempted,
            "reason": reason,
        }


def _default_probe(base_url: str, *, timeout: float) -> dict[str, Any]:
    """The live probe: one FREE, unauthenticated catalog read.

    ``GET {base_url}/models`` is the endpoint ``provider-credits.yaml`` already
    names as the catalog source, and it answers without a credential — so this
    sends **no** ``Authorization`` header at all. That is not an omission: the key
    must never leave the gateway's own call path, and a probe that billed the
    account to draw a page would be the defect this design exists to avoid. The
    billed ``chat/completions`` path is never called from here.
    """
    url = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(getattr(response, "status", 0) or 0)
            body = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NousProbeError(f"the catalog probe failed: {exc}") from exc
    if status != 200:
        raise NousProbeError(f"the catalog probe answered HTTP {status}")
    try:
        document = json.loads(body)
    except ValueError as exc:
        raise NousProbeError(
            f"the catalog probe did not answer JSON: {exc}"
        ) from exc
    models = document.get("data") if isinstance(document, dict) else None
    if not isinstance(models, list):
        raise NousProbeError("the catalog probe carried no models list")
    return {"endpoint": url, "modelCount": len(models)}


class NousSurface:
    """The console's Nous provider surface (issue #1561).

    Feature-flag-gated OFF (GR-5), declared in the portal's own
    ``portal/config/feature-flags.yaml`` beside the other portal-only views.
    Every declaration is read fresh on each call; the only cached thing is the
    live probe reading, and its age is always reported.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        config_path: Optional[Path | str] = None,
        credentials_path: Optional[Path | str] = None,
        feature_flags_path: Optional[Path | str] = None,
        module_path: Optional[Path | str] = None,
        credits_path: Optional[Path | str] = None,
        usage_store_path: Optional[Path | str] = None,
        plan: Optional[str] = None,
        top_ups_usd: Optional[list[float]] = None,
        probe: Optional[Callable[[], dict[str, Any]]] = None,
        clock: Optional[Callable[[], float]] = None,
        cache_ttl_seconds: float = DEFAULT_TTL_SECONDS,
        min_probe_interval_seconds: float = DEFAULT_MIN_PROBE_INTERVAL_SECONDS,
        probe_timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.config_path = Path(config_path) if config_path is not None else None
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root, config_path=self.config_path, surface=NOUS_SURFACE
            )
        self.enabled = bool(enabled)
        self._credentials_path = (
            Path(credentials_path) if credentials_path is not None else None
        )
        self._feature_flags_path = (
            Path(feature_flags_path) if feature_flags_path is not None else None
        )
        self._module_path = Path(module_path) if module_path is not None else None
        self._credits_path = Path(credits_path) if credits_path is not None else None
        self._usage_store_path = (
            Path(usage_store_path) if usage_store_path is not None else None
        )
        # A pinned plan is what turns the declared meter into real arithmetic.
        # Neither the plan nor its top-ups is invented here: with no plan pinned,
        # ``remainingUsd`` is null and the payload says why.
        self._plan = plan
        self._top_ups_usd = list(top_ups_usd or [])
        self._probe = probe
        self._clock = clock or (lambda: datetime.now(timezone.utc).timestamp())
        self._probe_timeout = float(probe_timeout_seconds)
        self._env = env
        self._cache = _ProbeCache(
            ttl=cache_ttl_seconds,
            min_interval=min_probe_interval_seconds,
            clock=self._clock,
        )

    # -- declaration readers -------------------------------------------------
    def _env_value(self, name: str) -> str:
        source = self._env if self._env is not None else os.environ
        return str(source.get(name, "") or "")

    def _credential(self) -> tuple[Optional[dict[str, Any]], list[str]]:
        """The gateway's declared Nous credential, if this checkout declares one."""
        path = self._credentials_path or (self.repo_root / CREDENTIALS_RELATIVE)
        if not path.is_file():
            return None, [CREDENTIALS_RELATIVE.as_posix()]
        document = _read_json(path)
        secrets = document.get("secrets") if isinstance(document, dict) else None
        if not isinstance(secrets, list):
            return None, [CREDENTIALS_RELATIVE.as_posix()]
        for entry in secrets:
            if isinstance(entry, dict) and entry.get("service") == CREDENTIAL_SERVICE:
                return entry, []
        return None, [CREDENTIALS_RELATIVE.as_posix()]

    def _gate(self, gate: str) -> dict[str, Any]:
        """The flag the credential sits behind: declared default + promotion.

        Keyed the way ``infra/feature-flags/registry.yaml`` actually spells it —
        a service entry names its terraform variable as ``tf_flag`` (the
        ``hermes`` entry carries ``tf_flag: enable_hermes``). Fail-closed like
        every other reader here: anything unreadable is ``unknown``, which the
        payload renders as such rather than as promoted.
        """
        result: dict[str, Any] = {"name": gate or None, "state": "unknown", "promoted": None}
        if not gate:
            return result
        path = self._feature_flags_path or (self.repo_root / FEATURE_FLAGS_RELATIVE)
        document = _read_yaml(path)
        if not isinstance(document, dict):
            return result
        services = document.get("services")
        if not isinstance(services, dict):
            return result
        for name, entry in services.items():
            if not isinstance(entry, dict):
                continue
            keys = (
                str(entry.get("tf_flag") or ""),
                str(entry.get("flag") or ""),
                str(name or ""),
            )
            if gate not in keys:
                continue
            if isinstance(entry.get("promoted"), bool):
                result["promoted"] = entry["promoted"]
            default = entry.get("default")
            if default is True or (
                isinstance(default, str) and default.strip().lower() == "on"
            ):
                result["state"] = "on"
            elif default is False or (
                isinstance(default, str) and default.strip().lower() == "off"
            ):
                result["state"] = "off"
            return result
        return result

    def _credits_table(self) -> tuple[Any, str]:
        """``(provider_credits.CreditsTable, declaration path)`` — lazy, may raise."""
        from gateway.finops import provider_credits

        path = self._credits_path or Path(provider_credits.CREDITS_PATH)
        return provider_credits.load_credits(path), str(provider_credits.CREDITS_PATH)

    def _catalog(self) -> tuple[dict[str, Any], list[str], list[str]]:
        """The declared catalog: priced models, plan ceilings, top-up amounts."""
        notes: list[str] = []
        try:
            table, declaration = self._credits_table()
        except ImportError:
            return {}, [CREDIT_DECLARATION], [
                "the FinOps credits declaration is not readable from this "
                "checkout, so no catalog or rate is shown"
            ]
        except Exception as exc:  # noqa: BLE001 - a bad declaration is a state
            return {}, [CREDIT_DECLARATION], [
                f"the FinOps credits declaration could not be read ({exc})"
            ]

        card = table.card(PROVIDER_ID)
        if card is None:
            return {}, [CREDIT_DECLARATION], [
                f"the credits declaration carries no {PROVIDER_ID} provider entry"
            ]

        models = []
        for model_id in sorted(card.models):
            rate = card.rate_for(model_id)
            models.append(
                {
                    "id": model_id,
                    "contextWindow": getattr(rate, "context_window", None),
                    "pricePublished": bool(getattr(rate, "price_published", False)),
                    "inputUsdPerMTok": getattr(rate, "input_usd_per_mtok", None),
                    "outputUsdPerMTok": getattr(rate, "output_usd_per_mtok", None),
                }
            )
        plans = [
            {
                "name": plan.name,
                "priceUsdPerMonth": plan.price_usd_per_month,
                "grantedCreditsUsd": plan.granted_credits_usd,
                "rolloverCapUsd": plan.rollover_cap_usd,
            }
            for plan in sorted(card.plans.values(), key=lambda item: item.name)
        ]
        unmetered = sorted(card.unmetered_models())
        if unmetered:
            notes.append(
                "declared models with no published price are reported as "
                "unmetered, never as free: " + ", ".join(unmetered)
            )
        catalog = {
            "declaration": declaration,
            "displayName": card.display_name,
            "baseUrl": card.base_url,
            "docs": card.docs,
            "billing": card.billing,
            "models": models,
            "plans": plans,
            "topUpsUsd": [float(amount) for amount in card.top_ups_usd],
            "unmeteredModels": unmetered,
            "balanceEndpoint": card.balance_endpoint,
            "balanceTracking": card.balance_tracking,
            "balanceTrackingNote": card.balance_tracking_note,
        }
        return catalog, [], notes

    def _tier_feature(self) -> Optional[dict[str, Any]]:
        """The declared tier-routing feature from the module catalog entry.

        The LOW/MED/HIGH/MAX -> model-id resolution itself is a *code*
        declaration (``gateway/providers/config.py``); this reader does not
        re-parse prose to reconstruct it, and the payload says where it lives.
        """
        path = self._module_path or (self.repo_root / MODULE_RELATIVE)
        document = _read_json(path)
        features = document.get("features") if isinstance(document, dict) else None
        if not isinstance(features, list):
            return None
        for feature in features:
            if isinstance(feature, dict) and feature.get("id") == "tier-routing":
                return {
                    "id": feature.get("id"),
                    "flags": list(feature.get("flags") or []),
                    "declaredDefault": feature.get("default"),
                    "description": feature.get("desc"),
                }
        return None

    # -- usage / burn --------------------------------------------------------
    def _usage(self) -> tuple[dict[str, Any], list[str]]:
        """Metered Nous usage from the durable store the console already reads."""
        override = self._usage_store_path
        path = override if override is not None else self.repo_root / DEFAULT_USAGE_STORE
        # Report the path that was actually checked: the canonical repo-relative
        # constant for the default store (what a reader greps for), the given
        # path when a caller injected one.
        named = (
            override.as_posix()
            if override is not None
            else DEFAULT_USAGE_STORE.as_posix()
        )
        empty = {
            "calls": 0,
            "billableCalls": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "totalTokens": 0,
            "costUsd": None,
            "byModel": [],
            "window": {"last24hCostUsd": None, "last24hCalls": 0},
        }
        if not path.is_file():
            return empty, [named]
        try:
            from telemetry.metering.store import load_records
        except ImportError:
            return empty, [named]

        records = [
            record
            for record in load_records(path)
            if str(getattr(record, "provider", "") or "") == PROVIDER_ID
        ]
        cutoff = self._clock() - 86400.0
        by_model: dict[str, dict[str, Any]] = {}
        total_cost: Optional[float] = None
        recent_cost: Optional[float] = None
        recent_calls = 0
        billable = 0
        input_tokens = 0
        output_tokens = 0
        for record in records:
            model_id = str(getattr(record, "model", "") or "")
            row = by_model.setdefault(
                model_id,
                {
                    "model": model_id,
                    "calls": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "costUsd": None,
                },
            )
            in_tokens = int(getattr(record, "input_tokens", 0) or 0)
            out_tokens = int(getattr(record, "output_tokens", 0) or 0)
            row["calls"] += 1
            row["inputTokens"] += in_tokens
            row["outputTokens"] += out_tokens
            input_tokens += in_tokens
            output_tokens += out_tokens
            if getattr(record, "billable", False):
                billable += 1
            cost = getattr(record, "cost_usd", None)
            if isinstance(cost, (int, float)):
                total_cost = (total_cost or 0.0) + float(cost)
                row["costUsd"] = (row["costUsd"] or 0.0) + float(cost)
                if _within_window(getattr(record, "ts", ""), cutoff):
                    recent_cost = (recent_cost or 0.0) + float(cost)
                    recent_calls += 1

        return (
            {
                "calls": len(records),
                "billableCalls": billable,
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": input_tokens + output_tokens,
                "costUsd": total_cost,
                "byModel": [by_model[key] for key in sorted(by_model)],
                "window": {
                    "last24hCostUsd": recent_cost,
                    "last24hCalls": recent_calls,
                },
            },
            [],
        )

    # -- credits -------------------------------------------------------------
    def _credits(self, usage: dict[str, Any]) -> dict[str, Any]:
        """The declared meter plus the **metered** debits — never a fake balance."""
        debited = usage.get("costUsd")
        section: dict[str, Any] = {
            "balanceEndpoint": None,
            "balanceTracking": None,
            "balanceTrackingNote": None,
            "plan": self._plan,
            "topUpsUsd": list(self._top_ups_usd),
            "debitedUsd": debited,
            "remainingUsd": None,
            "remainingReason": None,
        }
        try:
            table, _ = self._credits_table()
        except ImportError:
            section["remainingReason"] = (
                "the FinOps credits declaration is not readable from this "
                "checkout, so no ceiling can be resolved"
            )
            return section
        except Exception as exc:  # noqa: BLE001 - a bad declaration is a state
            section["remainingReason"] = (
                f"the FinOps credits declaration could not be read ({exc})"
            )
            return section

        card = table.card(PROVIDER_ID)
        if card is None:
            section["remainingReason"] = (
                f"the credits declaration carries no {PROVIDER_ID} provider entry"
            )
            return section

        section["balanceEndpoint"] = card.balance_endpoint
        section["balanceTracking"] = card.balance_tracking
        section["balanceTrackingNote"] = card.balance_tracking_note
        if self._plan is None:
            section["remainingReason"] = (
                "no plan is pinned for this surface, so there is no ceiling to "
                "subtract the metered debits from; the account's real balance "
                "lives with the provider, and its published contract exposes no "
                "endpoint to read it back from"
            )
            return section
        try:
            section["remainingUsd"] = card.remaining_credits_usd(
                self._plan, self._top_ups_usd, float(debited or 0.0)
            )
        except Exception as exc:  # noqa: BLE001 - an unresolvable plan is a state
            section["remainingUsd"] = None
            section["remainingReason"] = (
                f"the pinned plan could not be resolved: {exc}"
            )
        return section

    # -- the served payload --------------------------------------------------
    def overview(self) -> dict[str, Any]:
        """Every section the view renders, joined from declarations + records."""
        missing: list[str] = []
        notes: list[str] = []

        catalog, catalog_missing, catalog_notes = self._catalog()
        missing.extend(catalog_missing)
        notes.extend(catalog_notes)
        usage, usage_missing = self._usage()
        missing.extend(usage_missing)
        credits = self._credits(usage)

        credential, credential_missing = self._credential()
        missing.extend(credential_missing)

        base_url = str(catalog.get("baseUrl") or "")
        gate_name = str((credential or {}).get("gate") or "")
        gate = self._gate(gate_name)
        gate_state = str(gate["state"])
        env_name = str((credential or {}).get("env") or "")
        key_present = bool(self._env_value(env_name)) if env_name else False

        status: dict[str, Any] = {
            "state": STATE_NOT_CONFIGURED,
            "detail": "",
            "checkedAt": _iso(self._clock()),
            "valueAt": None,
            "ageSeconds": None,
            "cached": False,
            "credential": {
                "secretId": (credential or {}).get("secret_id"),
                "env": env_name or None,
                "gate": gate_name or None,
                "configured": key_present,
                "source": CREDENTIALS_RELATIVE.as_posix(),
            },
            "gate": gate,
            "probe": {"endpoint": None, "kind": None, "callsMade": 0},
        }

        if credential is None:
            status["detail"] = (
                "this checkout declares no gateway Nous credential "
                f"({CREDENTIALS_RELATIVE.as_posix()}), so there is nothing to "
                "probe with"
            )
        elif not env_name:
            status["detail"] = (
                "the declared Nous credential names no environment variable to "
                "read the key from"
            )
        elif not key_present:
            status["detail"] = (
                "the operator step this issue's prerequisite owes is "
                f"outstanding: no {env_name} is set in this process, so the "
                "provider is not configured here. The declared and metered "
                "figures below are not a live account reading."
            )
        else:
            probe_fn = self._probe
            if probe_fn is None:
                probe_fn = self._default_probe_factory(base_url)
            reading = self._cache.read(probe_fn)
            status["state"] = reading["state"]
            status["valueAt"] = (
                _iso(reading["value_at"])
                if reading["value_at"] is not None
                else None
            )
            status["ageSeconds"] = reading["age_seconds"]
            status["cached"] = bool(reading["cached"])
            status["probe"]["kind"] = "catalog"
            status["probe"]["callsMade"] = 1 if reading["attempted"] else 0
            if reading.get("value"):
                status["probe"]["endpoint"] = reading["value"].get("endpoint")
                live = catalog.setdefault("live", {})
                live["modelCount"] = reading["value"].get("modelCount")
            reason = reading.get("reason") or ""
            if reading["state"] == STATE_LIVE:
                how = (
                    "was served from the cache"
                    if reading["cached"]
                    else "was taken just now"
                )
                status["detail"] = (
                    "the provider's own catalog endpoint answered; the reading "
                    f"{how}"
                )
            elif reading["state"] == STATE_STALE:
                status["detail"] = (
                    "the live probe failed, so the last known reading is shown "
                    "labelled stale with its measured age — it is not a current "
                    "value" + (f" ({reason})" if reason else "")
                )
            else:
                status["detail"] = (
                    "the provider's catalog endpoint could not be reached and no "
                    "earlier reading is held"
                    + (f" ({reason})" if reason else "")
                )

        if gate_state == "off":
            notes.append(
                f"the credential is projected behind {gate_name or 'its gate'}, "
                "which this checkout declares OFF (GR-5): the declaration exists "
                "and nothing is promoted"
            )
        elif gate_state == "unknown":
            notes.append(
                f"the gate {gate_name or 'the credential names'} could not be read "
                "from the feature-flag declaration, so its state is unknown "
                "rather than assumed promoted"
            )
        elif gate.get("promoted") is False:
            notes.append(
                f"{gate_name} is declared ON but its registry entry carries "
                "promoted: false — the switch is on and this capability has not "
                "been promoted, so nothing is served from it yet"
            )

        notes.append(
            "the LOW/MED/HIGH/MAX tier ladder the gateway routes this provider "
            "with is declared in gateway/providers/config.py (code); this surface "
            "renders the declared priced model set and does not re-derive that map"
        )

        return {
            "schema": SCHEMA,
            "provider": {
                "id": PROVIDER_ID,
                "displayName": catalog.get("displayName") or "Nous Research",
                "baseUrl": base_url or None,
                "docs": catalog.get("docs"),
                "billing": catalog.get("billing"),
            },
            "status": status,
            "catalog": catalog,
            "tierFeature": self._tier_feature(),
            "credits": credits,
            "usage": usage,
            "missing": sorted(dict.fromkeys(missing)),
            "notes": notes,
            "links": {"manageAccount": MANAGE_ACCOUNT_URL},
        }

    def _default_probe_factory(
        self, base_url: str
    ) -> Callable[[], dict[str, Any]]:
        """The resolved default probe, bound to the declared base URL."""

        def probe() -> dict[str, Any]:
            return _default_probe(base_url, timeout=self._probe_timeout)

        return probe
