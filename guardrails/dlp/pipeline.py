"""guardrails.dlp.pipeline — the composed egress pipeline (one gate per call).

Wires the four mandatory gates in order so **every** outbound commercial-model
call passes DLP scrub, prompt-injection defense, the egress allowlist, and the
per-call HMAC audit — in that order — before dispatch. A call that fails any
gate never reaches the provider and is surfaced to the security view.

Order of gates (mirrors the external-LLM egress doctrine, scrub-then-sign):

1. **Injection defense** — analyze the raw payload; a ``blocked`` verdict
   stops the call (telemetry: ``injection_attempt``).
2. **DLP scrub** — run the policy catalog; a ``blocked`` verdict stops the
   call (telemetry: ``scrub_blocked``); otherwise the payload sent is the
   redacted text.
3. **Egress guard** — provider/endpoint must be allowlisted for the tenant
   (telemetry: ``egress_denied``).
4. **HMAC audit** — build the call record (tenant, agent, call id, provider,
   endpoint, SHA-256 of the exact dispatched payload, ruleset version,
   injection score, nonce, UTC timestamp), sign it, and append it to the audit
   log. Dispatch is refused if the append cannot complete (no unaudited call).

On the inbound side, :meth:`EgressPipeline.verify_inbound` recomputes the tag;
an unsigned or tampered call is rejected (telemetry: ``tamper_detected``).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping, Optional

from .egress import EgressDecision, EgressGuard
from .engine import ScrubEngine, ScrubResult
from .hmac_audit import HmacAuditLog, HmacSigner
from .injection import InjectionDetector, InjectionReport
from .telemetry import SecurityTelemetry


@dataclass(frozen=True)
class CallOutcome:
    """Result of running one outbound call through the pipeline."""

    verdict: str  # sent|blocked_injection|blocked_dlp|egress_denied|error
    reasons: tuple = field(default_factory=tuple)
    text: str = ""
    record: Optional[dict] = None  # signed audit record when sent
    scrub: Optional[ScrubResult] = None
    injection: Optional[InjectionReport] = None
    egress: Optional[EgressDecision] = None

    @property
    def sent(self) -> bool:
        return self.verdict == "sent"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class EgressPipeline:
    """Composed DLP + injection + egress + HMAC audit pipeline."""

    def __init__(
        self,
        *,
        scrubber: Optional[ScrubEngine] = None,
        detector: Optional[InjectionDetector] = None,
        guard: Optional[EgressGuard] = None,
        signer: Optional[HmacSigner] = None,
        audit_log: Optional[HmacAuditLog] = None,
        telemetry: Optional[SecurityTelemetry] = None,
    ) -> None:
        self.scrubber = scrubber if scrubber is not None else ScrubEngine()
        self.detector = detector if detector is not None else InjectionDetector()
        self.guard = guard if guard is not None else EgressGuard()  # default deny
        self.signer = signer if signer is not None else HmacSigner()
        self.audit_log = audit_log if audit_log is not None else HmacAuditLog(self.signer)
        self.telemetry = telemetry if telemetry is not None else SecurityTelemetry()

    # -- outbound ------------------------------------------------------------

    def guard_call(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        provider: str,
        endpoint: str,
        payload: str,
        call_id: Optional[str] = None,
    ) -> CallOutcome:
        """Run the four gates; returns a :class:`CallOutcome`."""
        call_id = call_id or uuid.uuid4().hex

        # 1. Injection defense on the raw payload.
        injection = self.detector.analyze(payload)
        if injection.blocked:
            self.telemetry.emit(
                "injection_attempt",
                tenant_id=tenant_id,
                agent_id=agent_id,
                severity="critical",
                call_id=call_id,
                verdict="blocked",
                signals=injection.reasons,
            )
            return CallOutcome(
                verdict="blocked_injection",
                reasons=tuple(injection.reasons),
                text=payload,
                injection=injection,
            )
        if injection.verdict == "suspicious":
            self.telemetry.emit(
                "injection_attempt",
                tenant_id=tenant_id,
                agent_id=agent_id,
                severity="warning",
                call_id=call_id,
                verdict="suspicious",
                signals=injection.reasons,
            )

        # 2. DLP scrub gate.
        scrub = self.scrubber.scrub(payload)
        if scrub.blocked:
            self.telemetry.emit(
                "scrub_blocked",
                tenant_id=tenant_id,
                agent_id=agent_id,
                severity="critical",
                call_id=call_id,
                blocked_by=list(scrub.blocked_by),
                ruleset_version=scrub.ruleset_version,
            )
            return CallOutcome(
                verdict="blocked_dlp",
                reasons=tuple(f"scrub block: {rid}" for rid in scrub.blocked_by),
                text=payload,
                scrub=scrub,
                injection=injection,
            )
        dispatched_text = scrub.text

        # 3. Egress guard.
        egress = self.guard.allow(tenant_id=tenant_id, provider=provider, endpoint=endpoint)
        if not egress.allowed:
            self.telemetry.emit(
                "egress_denied",
                tenant_id=tenant_id,
                agent_id=agent_id,
                severity="warning",
                call_id=call_id,
                provider=provider,
                endpoint=endpoint,
                reason=egress.reason,
            )
            return CallOutcome(
                verdict="egress_denied",
                reasons=(egress.reason,),
                text=dispatched_text,
                scrub=scrub,
                injection=injection,
                egress=egress,
            )

        # 4. HMAC audit — no unaudited call.
        record = {
            "record_type": "egress_call",
            "record_id": call_id,
            "call_id": call_id,
            "ts": _utc_now(),
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "provider": provider,
            "endpoint": endpoint,
            "payload_sha256": _sha256(dispatched_text),
            "nonce": os.urandom(8).hex(),
            "ruleset_version": scrub.ruleset_version,
            "injection_verdict": injection.verdict,
            "injection_score": injection.score,
            "decision": "sent",
        }
        try:
            signed = self.audit_log.append(record)
        except Exception as exc:  # fail closed: never dispatch unaudited
            self.telemetry.emit(
                "audit_failed",
                tenant_id=tenant_id,
                agent_id=agent_id,
                severity="critical",
                call_id=call_id,
                error=str(exc),
            )
            return CallOutcome(
                verdict="error",
                reasons=(f"audit failed: {exc}",),
                text=dispatched_text,
                scrub=scrub,
                injection=injection,
                egress=egress,
            )

        self.telemetry.emit(
            "call_sent",
            tenant_id=tenant_id,
            agent_id=agent_id,
            severity="info",
            call_id=call_id,
            provider=provider,
            endpoint=endpoint,
            ruleset_version=scrub.ruleset_version,
        )
        return CallOutcome(
            verdict="sent",
            reasons=("scrubbed", "injection-checked", "egress-allowed", "hmac-signed"),
            text=dispatched_text,
            record=signed,
            scrub=scrub,
            injection=injection,
            egress=egress,
        )

    # -- inbound -------------------------------------------------------------

    def payload_ok(self, record: Mapping, text: str) -> bool:
        """True when ``text`` matches the payload SHA-256 bound in the record."""
        return record.get("payload_sha256") == _sha256(text)

    def verify_inbound(self, record: Mapping) -> tuple:
        """Verify an inbound call record's integrity. Returns (ok, reason).

        An unsigned record is rejected outright (no unaudited call); a record
        whose tag does not recompute is rejected and telemetry records the
        tamper detection.
        """
        if "hmac" not in record:
            return False, "missing hmac: unaudited call rejected"
        ok = self.audit_log.verify_record(record)
        if not ok:
            self.telemetry.emit(
                "tamper_detected",
                tenant_id=str(record.get("tenant_id", "")),
                agent_id=str(record.get("agent_id", "")),
                severity="critical",
                record_id=str(record.get("record_id", "")),
            )
            return False, "hmac mismatch: call record tampered"
        return True, "hmac valid"
