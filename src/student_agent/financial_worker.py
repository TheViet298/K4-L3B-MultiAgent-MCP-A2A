from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

logger = logging.getLogger(__name__)
ACTOR_NAME = "financial_worker"


@dataclass(frozen=True)
class FinancialResult:
    """Standardized output structure from Financial Worker handoff to Supervisor."""

    payment_analysis: dict[str, Any]
    payment_references: list[str]
    evidence_refs: list[str]
    suggested_primary_issue: str | None = None
    data_conflicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "payment_analysis": self.payment_analysis,
            "payment_references": self.payment_references,
            "evidence_refs": self.evidence_refs,
            "suggested_primary_issue": self.suggested_primary_issue,
            "data_conflicts": self.data_conflicts,
        }


class FinancialWorker:
    """Worker specializing in payment reconciliation, refund ledger tracking,
    and deterministic financial calculations.
    """

    def __init__(self, gateway: EvidenceGateway, trace: TraceWriter | None = None) -> None:
        self.gateway = gateway
        self.trace = trace

    async def _fetch_and_trace(
        self, tool_name: str, *, case_id: str, **arguments: str
    ) -> dict[str, Any] | None:
        """Call an MCP tool safely and emit tool_result_consumed trace event."""
        try:
            evidence = await self.gateway.call(tool_name, case_id=case_id, **arguments)
        except Exception as exc:
            logger.debug("FinancialWorker tool %s failed: %s", tool_name, exc)
            return None

        evidence_ref = evidence.get("evidence_ref")
        if self.trace and evidence_ref:
            self.trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor=ACTOR_NAME,
                tool_name=tool_name,
                evidence_refs=[evidence_ref],
                attributes={"status": "ok", "domain": evidence.get("domain", "")},
            )
        return evidence

    async def investigate(
        self,
        case_id: str,
        order_id: str | None,
        claims: list[dict[str, Any]] | None = None,
    ) -> FinancialResult:
        """Perform end-to-end financial analysis and reconciliation for an order."""
        evidence_refs: list[str] = []
        payment_references: list[str] = []
        data_conflicts: list[dict[str, Any]] = []

        if not order_id:
            return FinancialResult(
                payment_analysis={
                    "verdict": "insufficient_evidence",
                    "captured_total_brl": 0.0,
                    "refunded_total_brl": 0.0,
                    "refundable_total_brl": 0.0,
                },
                payment_references=[],
                evidence_refs=[],
                suggested_primary_issue="insufficient_evidence",
            )

        # 1. Fetch order payments
        payments_ev = await self._fetch_and_trace(
            "get_order_payments", case_id=case_id, order_id=order_id
        )
        if payments_ev and payments_ev.get("evidence_ref"):
            evidence_refs.append(payments_ev["evidence_ref"])

        # 2. Fetch payment timeline if claim indicates payment issue
        has_payment_claim = False
        if claims:
            topics = [c.get("topic", "") for c in claims]
            has_payment_claim = any(
                t in ("duplicate_charge", "payment_mismatch", "refund_pending", "refund_failed")
                for t in topics
            )

        timeline_ev = None
        if has_payment_claim:
            timeline_ev = await self._fetch_and_trace(
                "get_payment_timeline", case_id=case_id, order_id=order_id
            )
            if timeline_ev and timeline_ev.get("evidence_ref"):
                evidence_refs.append(timeline_ev["evidence_ref"])

        # Extract payment entries
        payments_data: list[dict[str, Any]] = []
        if payments_ev and isinstance(payments_ev.get("data"), list):
            payments_data = payments_ev["data"]
        elif timeline_ev and isinstance(timeline_ev.get("data"), dict):
            payments_data = timeline_ev["data"].get("payments", [])

        # Parse payments
        captured_total = 0.0
        refunded_total = 0.0
        sequentials: list[int] = []
        duplicate_sequential_found = False

        for idx, payment in enumerate(payments_data):
            try:
                val = float(payment.get("payment_value", 0.0) or 0.0)
            except (ValueError, TypeError):
                val = 0.0
            captured_total += val

            # Sequential check
            seq = payment.get("payment_sequential")
            if seq is not None:
                try:
                    seq_int = int(seq)
                    if seq_int in sequentials and val > 0:
                        duplicate_sequential_found = True
                    sequentials.append(seq_int)
                except (ValueError, TypeError):
                    pass

            # Payment reference
            pref = payment.get("payment_id") or payment.get("payment_sequential") or f"pay_{idx + 1}"
            payment_references.append(str(pref))

        # Check events in payment timeline or payments
        events: list[dict[str, Any]] = []
        if timeline_ev and isinstance(timeline_ev.get("data"), dict):
            events = timeline_ev["data"].get("events", [])

        has_duplicate_capture = duplicate_sequential_found
        has_capture_mismatch = False
        has_refund_pending = False
        has_refund_failed = False

        for evt in events:
            etype = evt.get("event_type", "").lower()
            status = evt.get("status", "").lower()
            amt = 0.0
            try:
                amt = float(evt.get("amount_brl", 0.0) or 0.0)
            except (ValueError, TypeError):
                amt = 0.0

            if "duplicate" in etype or etype == "duplicate_captured":
                has_duplicate_capture = True
            elif "mismatch" in etype:
                has_capture_mismatch = True
            elif etype in ("refund_pending", "pending_refund"):
                has_refund_pending = True
            elif etype in ("refund_failed", "failed_refund"):
                has_refund_failed = True
            elif etype in ("refunded", "refund_processed") or "refund" in etype:
                if status != "failed":
                    refunded_total += amt

        # Deduplicate payment references
        payment_references = sorted(list(set(payment_references)))

        # Deterministic math
        captured_total_brl = round(captured_total, 2)
        refunded_total_brl = round(refunded_total, 2)
        refundable_total_brl = round(max(0.0, captured_total_brl - refunded_total_brl), 2)

        # Verdict Engine
        suggested_issue: str | None = None
        if not payments_ev and not timeline_ev:
            verdict = "insufficient_evidence"
            suggested_issue = "insufficient_evidence"
        elif has_duplicate_capture:
            verdict = "duplicate_capture"
            suggested_issue = "duplicate_charge"
            data_conflicts.append(
                {
                    "field": "payment_sequential",
                    "sources": ["get_order_payments", "payment_gateway"],
                    "selected_source": "payment_gateway",
                    "resolution_code": "flag_duplicate_payment",
                }
            )
        elif has_capture_mismatch:
            verdict = "capture_mismatch"
            suggested_issue = "payment_mismatch"
        elif has_refund_failed:
            verdict = "refund_failed"
            suggested_issue = "refund_failed"
        elif has_refund_pending:
            verdict = "refund_pending"
            suggested_issue = "refund_pending"
        elif refunded_total_brl > 0.0 and refundable_total_brl == 0.0:
            verdict = "refunded"
            suggested_issue = None
        else:
            verdict = "reconciled"
            suggested_issue = None

        if self.trace:
            self.trace.emit(
                case_id=case_id,
                event_type="handoff",
                actor=ACTOR_NAME,
                target="router",
                decision_code=f"payment_{verdict}",
                evidence_refs=evidence_refs if evidence_refs else None,
            )

        return FinancialResult(
            payment_analysis={
                "verdict": verdict,
                "captured_total_brl": captured_total_brl,
                "refunded_total_brl": refunded_total_brl,
                "refundable_total_brl": refundable_total_brl,
            },
            payment_references=payment_references,
            evidence_refs=evidence_refs,
            suggested_primary_issue=suggested_issue,
            data_conflicts=data_conflicts,
        )


async def analyze_financial(
    case: dict[str, Any],
    order_id: str | None,
    gateway: EvidenceGateway,
    trace: TraceWriter,
) -> dict[str, Any]:
    """Compatibility wrapper calling FinancialWorker."""
    worker = FinancialWorker(gateway, trace)
    claims = case.get("customer_request", {}).get("claims")
    result = await worker.investigate(case["case_id"], order_id, claims)
    analysis = dict(result.payment_analysis)
    analysis["payment_references"] = result.payment_references
    analysis["evidence_refs"] = result.evidence_refs
    analysis["data_conflicts"] = result.data_conflicts
    analysis["suggested_primary_issue"] = result.suggested_primary_issue
    return analysis
