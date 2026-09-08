"""Optional OPA (Open Policy Agent) backend — enterprise policy engine option.

Issue #26 acceptance #4: the gate framework exposes a pluggable backend seam
so an enterprise may evaluate policy in OPA/Rego instead of (or in addition
to) the local engine.  :class:`OpaBackend` posts the action record to OPA's
``v1/data`` API over plain stdlib HTTP and maps the answer onto the
BLOCK/WARN/LOG contract.

It is an *option*, default OFF: nothing here constructs it unless an OPA
endpoint is explicitly configured, and any OPA failure — unreachable server,
non-200 response, unparseable body, unknown decision token — **fails closed**
to BLOCK with the error attached to the evidence (AO-GR-4, no-false-green).
The transport is injectable so the adapter is fully exercised offline by
tests against a fake HTTP transport; the real OPA binary/server is not bundled
with this lane.

The default local engine stays :class:`PolicyEngine` (see
:mod:`policy.engine`); :class:`LocalBackend` adapts it to the same
:class:`PolicyBackend` protocol so a caller can swap backends without changing
its call site.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional

from policy.decision import DecisionLevel, DecisionResult
from policy.engine import PolicyEngine

#: OPA data-API path prefix (v1 protocol).
_OPA_V1_DATA = "v1/data"


class PolicyBackend:
    """Common evaluation seam implemented by local and OPA backends."""

    def evaluate(
        self,
        action: str,
        subject: Optional[str] = None,
        tenant: Optional[str] = None,
        context: Optional[Mapping[str, Any]] = None,
    ) -> DecisionResult:
        raise NotImplementedError


class LocalBackend(PolicyBackend):
    """Adapter that runs the local :class:`PolicyEngine` through the seam."""

    def __init__(self, engine: PolicyEngine) -> None:
        self.engine = engine

    def evaluate(
        self,
        action: str,
        subject: Optional[str] = None,
        tenant: Optional[str] = None,
        context: Optional[Mapping[str, Any]] = None,
    ) -> DecisionResult:
        return self.engine.evaluate(action, subject=subject, tenant=tenant, context=context)


class Transport:
    """Injected HTTP transport for :class:`OpaBackend` (testable offline)."""

    def request(
        self,
        method: str,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        raise NotImplementedError


class UrllibTransport(Transport):
    """Transport backed by the Python standard library (``urllib``)."""

    def request(
        self,
        method: str,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                status = int(response.status)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"OPA HTTP request failed: {exc}") from exc
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            raise RuntimeError(f"OPA returned non-JSON body: {exc}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("OPA returned a non-object body")
        return status, parsed


class OpaBackend(PolicyBackend):
    """Evaluate policy by posting the action record to an OPA data API.

    ``policy_path`` selects the Rego document (default
    ``agentorchestrator/guardrails/allow``).  The Rego document may return a
    boolean (``allow``), a decision token string (``"block"|"warn"|"log"``),
    or an object ``{"decision": "...", "allow": bool, "reason": "..."}``.
    Anything that cannot be mapped fails closed to BLOCK.
    """

    def __init__(
        self,
        endpoint: str,
        policy_path: str = "agentorchestrator/guardrails/allow",
        transport: Optional[Transport] = None,
        timeout: float = 5.0,
        auth_token: Optional[str] = None,
    ) -> None:
        if not endpoint:
            raise ValueError("OpaBackend requires a non-empty endpoint")
        self.endpoint = endpoint.rstrip("/")
        self.policy_path = policy_path.strip("/")
        self.transport: Transport = transport if transport is not None else UrllibTransport()
        self.timeout = timeout
        self.auth_token = auth_token

    # -- public seam -------------------------------------------------------
    def evaluate(
        self,
        action: str,
        subject: Optional[str] = None,
        tenant: Optional[str] = None,
        context: Optional[Mapping[str, Any]] = None,
    ) -> DecisionResult:
        url = f"{self.endpoint}/{_OPA_V1_DATA}/{self.policy_path}"
        payload: dict[str, Any] = {
            "input": {
                "action": action,
                "subject": subject,
                "tenant": tenant,
                "context": dict(context or {}),
            }
        }
        headers = {"content-type": "application/json", "accept": "application/json"}
        if self.auth_token:
            headers["authorization"] = f"Bearer {self.auth_token}"

        try:
            status, body = self.transport.request("POST", url, payload, headers, self.timeout)
        except Exception as exc:  # network/transport failure -> fail closed
            return self._fail_closed(action, subject, tenant, f"OPA unavailable: {exc}")
        if status != 200:
            return self._fail_closed(action, subject, tenant, f"OPA returned HTTP {status}")
        if not isinstance(body, dict) or "result" not in body:
            return self._fail_closed(action, subject, tenant, "OPA response missing 'result'")

        decision, error = self._map_result(body["result"])
        return DecisionResult(
            decision=decision,
            action=action,
            subject=subject,
            tenant=tenant,
            uncovered=False,
            error=error,
            policies_consulted=(f"opa:{self.policy_path}",),
        )

    # -- result mapping ----------------------------------------------------
    def _map_result(self, result: Any) -> tuple[DecisionLevel, Optional[str]]:
        if isinstance(result, bool):
            return (DecisionLevel.LOG if result else DecisionLevel.BLOCK), None
        if isinstance(result, str):
            try:
                return DecisionLevel.from_token(result), None
            except ValueError:
                return DecisionLevel.BLOCK, f"OPA returned unknown decision token {result!r}"
        if isinstance(result, Mapping):
            if "decision" in result:
                try:
                    return DecisionLevel.from_token(result["decision"]), None
                except ValueError:
                    return (
                        DecisionLevel.BLOCK,
                        f"OPA returned unknown decision token {result['decision']!r}",
                    )
            if "allow" in result:
                allow = result["allow"]
                if isinstance(allow, bool):
                    return (DecisionLevel.LOG if allow else DecisionLevel.BLOCK), None
                return DecisionLevel.BLOCK, "OPA 'allow' field must be a boolean"
            return DecisionLevel.BLOCK, "OPA result object missing 'decision'/'allow'"
        return DecisionLevel.BLOCK, f"OPA returned unparseable result {result!r}"

    def _fail_closed(
        self,
        action: str,
        subject: Optional[str],
        tenant: Optional[str],
        message: str,
    ) -> DecisionResult:
        return DecisionResult(
            decision=DecisionLevel.BLOCK,
            action=action,
            subject=subject,
            tenant=tenant,
            uncovered=False,
            error=message,
            policies_consulted=(f"opa:{self.policy_path}",),
        )
