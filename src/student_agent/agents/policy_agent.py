from __future__ import annotations

import logging
from typing import Any

from ..cache import CaseCache
from ..trace import TraceWriter

logger = logging.getLogger(__name__)


class PolicyAgent:
    """Agent for applying policy rules, root cause analysis, and financial resolution."""

    def __init__(self, cache: CaseCache, trace: TraceWriter) -> None:
        self.cache = cache
        self.trace = trace

    def evaluate(
        self,
        case: dict[str, Any],
        entity_result: dict[str, Any],
        shipment_analysis: dict[str, Any],
        payment_analysis: dict[str, Any],
        conflicts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        customer_request = case.get("customer_request", {})
        claims = customer_request.get("claims", [])
        claim_topics = [c.get("topic", "") for c in claims]

        shipment_verdict = shipment_analysis.get("verdict", "on_time")
        payment_verdict = payment_analysis.get("verdict", "reconciled")
        refundable_total = float(payment_analysis.get("refundable_total_brl", 0.0) or 0.0)
        late_sellers = shipment_analysis.get("late_seller_ids", [])
        affected_entities = entity_result.get("affected_entities", {})
        seller_ids = affected_entities.get("seller_ids", [])

        # 1. Determine primary issue
        primary_issue = "insufficient_evidence"
        secondary_issues: list[str] = []
        case_status = "no_action"
        confidence = 0.90
        ranked_causes: list[dict[str, Any]] = []
        responsible_parties: list[dict[str, Any]] = []
        resolution_actions: list[str] = []
        refund_lines: list[dict[str, Any]] = []
        recommended_refund = 0.0

        if shipment_verdict == "seller_delay":
            primary_issue = "late_delivery_seller"
            case_status = "action_required"
            ranked_causes.append({"cause_code": "SELLER_DISPATCH_DELAY", "rank": 1})
            chosen_seller = late_sellers[0] if late_sellers else (seller_ids[0] if seller_ids else "seller-unknown")
            responsible_parties.append({"party_type": "seller", "party_id": chosen_seller})
            # Compensate customer or refund
            refund_amount = round(min(refundable_total, 25.0) if refundable_total > 0 else 0.0, 2)
            recommended_refund = refund_amount
            if refund_amount > 0:
                refund_lines.append(
                    {"reason_code": "LATE_DELIVERY_COMPENSATION", "amount_brl": refund_amount, "entity_id": chosen_seller}
                )
            resolution_actions.extend(["notify_seller_delay_penalty", "apologize_to_customer"])
            if refund_amount > 0:
                resolution_actions.append("issue_partial_refund")

        elif shipment_verdict == "logistics_delay":
            primary_issue = "late_delivery_logistics"
            case_status = "action_required"
            ranked_causes.append({"cause_code": "CARRIER_TRANSIT_DELAY", "rank": 1})
            responsible_parties.append({"party_type": "logistics_provider", "party_id": "carrier_service"})
            refund_amount = round(min(refundable_total, 15.0) if refundable_total > 0 else 0.0, 2)
            recommended_refund = refund_amount
            if refund_amount > 0:
                refund_lines.append(
                    {"reason_code": "LOGISTICS_DELAY_COURTESY", "amount_brl": refund_amount, "entity_id": None}
                )
            resolution_actions.extend(["expedite_shipment_tracking", "apologize_to_customer"])
            if refund_amount > 0:
                resolution_actions.append("issue_shipping_fee_refund")

        elif shipment_verdict == "lost":
            primary_issue = "canceled_order_paid"
            case_status = "action_required"
            ranked_causes.append({"cause_code": "PARCEL_LOST_IN_TRANSIT", "rank": 1})
            responsible_parties.append({"party_type": "logistics_provider", "party_id": "carrier_service"})
            recommended_refund = round(refundable_total, 2)
            if recommended_refund > 0:
                refund_lines.append(
                    {"reason_code": "FULL_REFUND_LOST_PARCEL", "amount_brl": recommended_refund, "entity_id": None}
                )
            resolution_actions.extend(["issue_full_refund", "file_carrier_lost_claim"])

        elif payment_verdict == "duplicate_capture":
            primary_issue = "duplicate_charge"
            case_status = "action_required"
            ranked_causes.append({"cause_code": "GATEWAY_DUPLICATE_TRANSACTION", "rank": 1})
            responsible_parties.append({"party_type": "payment_provider", "party_id": "payment_gateway"})
            dup_refund = round(min(refundable_total, refundable_total / 2.0 if refundable_total > 0 else 50.0), 2)
            recommended_refund = dup_refund
            refund_lines.append(
                {"reason_code": "REFUND_DUPLICATE_CHARGE", "amount_brl": dup_refund, "entity_id": None}
            )
            resolution_actions.extend(["reverse_duplicate_charge", "notify_customer_refund_issued"])

        elif payment_verdict == "refund_failed":
            primary_issue = "refund_failed"
            case_status = "action_required"
            ranked_causes.append({"cause_code": "PAYMENT_GATEWAY_REFUND_ERROR", "rank": 1})
            responsible_parties.append({"party_type": "payment_provider", "party_id": "payment_gateway"})
            recommended_refund = round(refundable_total, 2)
            refund_lines.append(
                {"reason_code": "RETRY_FAILED_REFUND", "amount_brl": recommended_refund, "entity_id": None}
            )
            resolution_actions.extend(["retry_manual_refund", "verify_customer_bank_account"])

        elif conflicts:
            # When customer claimed delay/error but carrier/gateway disproved it
            primary_issue = "unsupported_claim"
            case_status = "no_action"
            confidence = 0.88
            ranked_causes.append({"cause_code": "CUSTOMER_MISUNDERSTANDING", "rank": 1})
            responsible_parties.append({"party_type": "customer", "party_id": None})
            recommended_refund = 0.0
            refund_lines = []
            resolution_actions = ["send_clarification_to_customer"]

        else:
            # Default fallback when order is on time and payment is reconciled
            primary_issue = "unsupported_claim"
            case_status = "no_action"
            confidence = 0.92
            ranked_causes.append({"cause_code": "NO_FAULT_FOUND", "rank": 1})
            responsible_parties.append({"party_type": "platform", "party_id": "ecommerce_platform"})
            recommended_refund = 0.0
            refund_lines = []
            resolution_actions = ["close_inquiry_with_explanation"]

        # Secondary issues
        for topic in claim_topics:
            if topic != primary_issue and topic not in secondary_issues:
                secondary_issues.append(topic[:80])

        return {
            "assessment": {
                "primary_issue": primary_issue,
                "secondary_issues": secondary_issues[:10],
                "case_status": case_status,
                "confidence": round(confidence, 2),
            },
            "root_cause_analysis": {
                "ranked_causes": ranked_causes[:5],
                "responsible_parties": responsible_parties[:5],
            },
            "financial_resolution": {
                "currency": "BRL",
                "recommended_refund_brl": round(recommended_refund, 2),
                "refund_lines": refund_lines[:10],
            },
            "resolution_actions": list(dict.fromkeys(resolution_actions))[:8],
        }
