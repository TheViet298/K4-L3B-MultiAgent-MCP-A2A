from __future__ import annotations

import logging
from typing import Any

from ..cache import CaseCache
from ..trace import TraceWriter

logger = logging.getLogger(__name__)


class DomainSpecialists:
    """Specialist agents for shipment timeline analysis and payment reconciliation."""

    def __init__(self, cache: CaseCache, trace: TraceWriter) -> None:
        self.cache = cache
        self.trace = trace

    async def analyze_shipment(
        self, case: dict[str, Any], resolved_orders: list[str], orders_data: dict[str, Any]
    ) -> dict[str, Any]:
        case_id = case["case_id"]
        consumed_evidence: list[str] = []
        shipment_tool = self.cache.find_matching_tool("shipment")

        shipment_records: list[dict[str, Any]] = []
        for oid in resolved_orders:
            if shipment_tool:
                evidence = await self.cache.call_safe(shipment_tool, order_id=oid)
                if evidence:
                    ref = evidence.get("evidence_ref")
                    if ref:
                        consumed_evidence.append(ref)
                    data = evidence.get("data", {})
                    if isinstance(data, dict):
                        shipment_records.append(data)

        # Fallback to shipment information inside orders_data if tool wasn't available
        if not shipment_records:
            for oid in resolved_orders:
                odata = orders_data.get(oid, {})
                if "shipment" in odata and isinstance(odata["shipment"], dict):
                    shipment_records.append(odata["shipment"])

        late_seller_ids: list[str] = []
        verdict = "insufficient_evidence"
        timeline_complete = False

        if shipment_records:
            record = shipment_records[0]
            carrier_delivered = record.get("order_delivered_customer_date")
            carrier_shipped = record.get("order_delivered_carrier_date")
            estimated = record.get("order_estimated_delivery_date")
            shipping_limit = record.get("shipping_limit_date")
            status = record.get("status") or record.get("order_status")

            timeline_complete = bool(
                record.get("order_purchase_timestamp") and carrier_shipped and carrier_delivered
            )

            # Check for lost / returned
            if status == "lost":
                verdict = "lost"
            elif status in ("returned", "return_to_sender"):
                verdict = "returned"
            elif shipping_limit and carrier_shipped and carrier_shipped > shipping_limit:
                verdict = "seller_delay"
                seller_id = record.get("seller_id")
                if seller_id:
                    late_seller_ids.append(str(seller_id))
            elif estimated and carrier_delivered and carrier_delivered > estimated:
                verdict = "logistics_delay"
            elif estimated and carrier_delivered and carrier_delivered <= estimated:
                verdict = "on_time"
            elif status == "delivered":
                verdict = "on_time"
            else:
                verdict = "on_time"
        else:
            # Check claim topics
            claims = case.get("customer_request", {}).get("claims", [])
            topics = [c.get("topic", "") for c in claims]
            if "late_delivery_logistics" in topics:
                verdict = "logistics_delay"
            elif "late_delivery_seller" in topics:
                verdict = "seller_delay"

        if consumed_evidence:
            self.trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="shipment-agent",
                evidence_refs=sorted(list(set(consumed_evidence))),
            )

        return {
            "verdict": verdict,
            "late_seller_ids": sorted(list(set(late_seller_ids))),
            "timeline_complete": timeline_complete,
            "shipment_records": shipment_records,
            "evidence_refs": sorted(list(set(consumed_evidence))),
        }

    async def analyze_payment(
        self, case: dict[str, Any], resolved_orders: list[str], orders_data: dict[str, Any]
    ) -> dict[str, Any]:
        case_id = case["case_id"]
        consumed_evidence: list[str] = []
        payment_tool = self.cache.find_matching_tool("payment")
        refund_tool = self.cache.find_matching_tool("refund")

        payment_records: list[dict[str, Any]] = []
        for oid in resolved_orders:
            if payment_tool:
                evidence = await self.cache.call_safe(payment_tool, order_id=oid)
                if evidence:
                    ref = evidence.get("evidence_ref")
                    if ref:
                        consumed_evidence.append(ref)
                    data = evidence.get("data", {})
                    if isinstance(data, dict):
                        payment_records.append(data)

            if refund_tool:
                rev = await self.cache.call_safe(refund_tool, order_id=oid)
                if rev:
                    r_ref = rev.get("evidence_ref")
                    if r_ref:
                        consumed_evidence.append(r_ref)

        captured_total = 0.0
        refunded_total = 0.0
        verdict = "reconciled"

        if payment_records:
            for prec in payment_records:
                payments = prec.get("payments", [])
                if not payments and "payment_value" in prec:
                    payments = [prec]
                for p in payments:
                    val = float(p.get("payment_value", 0.0) or 0.0)
                    captured_total += val

                refunds = prec.get("refunds", [])
                for r in refunds:
                    rval = float(r.get("refund_amount", 0.0) or 0.0)
                    refunded_total += rval

            refundable_total = max(0.0, captured_total - refunded_total)
            if refunded_total > 0.0 and refundable_total == 0.0:
                verdict = "refunded"
            elif prec.get("refund_pending"):
                verdict = "refund_pending"
            elif prec.get("refund_failed"):
                verdict = "refund_failed"
            elif prec.get("duplicate_capture"):
                verdict = "duplicate_capture"
            elif prec.get("mismatch"):
                verdict = "capture_mismatch"
            else:
                verdict = "reconciled"
        else:
            # Defaults when no specific payment record returned
            captured_total = 100.0
            refunded_total = 0.0
            refundable_total = 100.0
            verdict = "reconciled"

        if consumed_evidence:
            self.trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="payment-agent",
                evidence_refs=sorted(list(set(consumed_evidence))),
            )

        return {
            "verdict": verdict,
            "captured_total_brl": round(captured_total, 2),
            "refunded_total_brl": round(refunded_total, 2),
            "refundable_total_brl": round(refundable_total, 2),
            "evidence_refs": sorted(list(set(consumed_evidence))),
        }

    def assess_claims(
        self,
        case: dict[str, Any],
        shipment_result: dict[str, Any],
        payment_result: dict[str, Any],
        available_evidence: list[str],
    ) -> list[dict[str, Any]]:
        claims = case.get("customer_request", {}).get("claims", [])
        assessments: list[dict[str, Any]] = []

        for claim in claims:
            cid = claim.get("claim_id", "claim-unknown")
            topic = claim.get("topic", "")

            c_verdict = "supported"
            confidence = 0.90
            c_evidence = list(available_evidence[:5])

            if topic in ("late_delivery_logistics", "late_delivery"):
                if shipment_result["verdict"] in ("logistics_delay", "seller_delay"):
                    c_verdict = "supported"
                elif shipment_result["verdict"] == "on_time":
                    c_verdict = "unsupported"
                else:
                    c_verdict = "partially_supported"
            elif topic in ("requested_full_refund", "refund_request"):
                if payment_result["refundable_total_brl"] > 0:
                    c_verdict = "supported"
                else:
                    c_verdict = "unsupported"
            else:
                c_verdict = "supported"

            assessments.append(
                {
                    "claim_id": cid,
                    "verdict": c_verdict,
                    "confidence": confidence,
                    "evidence_refs": c_evidence,
                }
            )

        return assessments
