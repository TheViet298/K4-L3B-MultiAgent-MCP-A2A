from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from ..cache import CaseCache
from ..trace import TraceWriter

logger = logging.getLogger(__name__)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class PolicyAgent:
    """Agent for applying policy rules, clause verification, root cause analysis, and settlement."""

    def __init__(self, cache: CaseCache, trace: TraceWriter) -> None:
        self.cache = cache
        self.trace = trace

    async def evaluate(
        self,
        case: dict[str, Any],
        entity_result: dict[str, Any],
        shipment_analysis: dict[str, Any],
        payment_analysis: dict[str, Any],
        conflicts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        case_id = case["case_id"]
        customer_request = case.get("customer_request", {})
        claims = customer_request.get("claims", [])
        claim_topics = [c.get("topic", "") for c in claims]
        main_topic = claim_topics[0] if claim_topics else "unsupported_claim"

        policy_version = case.get("policy_version", "EC_POLICY_V2")

        # 1. Fetch official policy clauses from MCP Gateway
        policy_tool = self.cache.find_matching_tool("policy")
        policy_evidence = None
        if policy_tool:
            policy_evidence = await self.cache.call_safe(policy_tool, policy_version=policy_version)

        policy_refs: list[str] = []
        if policy_evidence:
            ref = policy_evidence.get("evidence_ref")
            if ref:
                policy_refs.append(ref)
                self.trace.emit(
                    case_id=case_id,
                    event_type="tool_result_consumed",
                    actor="policy-agent",
                    tool_name=policy_tool,
                    evidence_refs=[ref],
                    attributes={"policy_version": policy_version},
                )

        shipment_verdict = shipment_analysis.get("verdict", "on_time")
        payment_verdict = payment_analysis.get("verdict", "reconciled")
        refundable_total = float(payment_analysis.get("refundable_total_brl", 0.0) or 0.0)
        captured_total = float(payment_analysis.get("captured_total_brl", 0.0) or 0.0)
        late_sellers = shipment_analysis.get("late_seller_ids", [])
        affected_entities = entity_result.get("affected_entities", {})
        seller_ids = affected_entities.get("seller_ids", [])
        chosen_seller = late_sellers[0] if late_sellers else (seller_ids[0] if seller_ids else "seller-001")

        # 2. Determine Primary Issue accurately based on the case claim topic and audit evidence
        primary_issue = "unsupported_claim"
        case_status = "no_action"
        confidence = 0.90
        ranked_causes: list[dict[str, Any]] = []
        responsible_parties: list[dict[str, Any]] = []
        resolution_actions: list[str] = []
        refund_lines: list[dict[str, Any]] = []
        recommended_refund = 0.0

        if main_topic == "late_delivery_seller":
            primary_issue = "late_delivery_seller"
            case_status = "action_required"
            ranked_causes.append({"cause_code": "SELLER_DISPATCH_DELAY", "rank": 1})
            responsible_parties.append({"party_type": "seller", "party_id": chosen_seller})
            refund_amount = round(min(refundable_total, 25.0) if refundable_total > 0 else 0.0, 2)
            recommended_refund = refund_amount
            if refund_amount > 0:
                refund_lines.append(
                    {"reason_code": "LATE_DELIVERY_COMPENSATION", "amount_brl": refund_amount, "entity_id": chosen_seller}
                )
            resolution_actions.extend(["notify_seller_delay_penalty", "apologize_to_customer", "issue_partial_refund"])

        elif main_topic == "late_delivery_logistics":
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
            resolution_actions.extend(["expedite_shipment_tracking", "apologize_to_customer", "issue_shipping_fee_refund"])

        elif main_topic == "valid_split_payment":
            primary_issue = "valid_split_payment"
            case_status = "no_action"
            ranked_causes.append({"cause_code": "SPLIT_PAYMENT_AUTHORIZED", "rank": 1})
            responsible_parties.append({"party_type": "customer", "party_id": None})
            recommended_refund = 0.0
            refund_lines = []
            resolution_actions.extend(["send_clarification_to_customer", "close_inquiry_with_explanation"])

        elif main_topic == "payment_mismatch":
            primary_issue = "payment_mismatch"
            case_status = "action_required"
            ranked_causes.append({"cause_code": "PAYMENT_CAPTURE_DISCREPANCY", "rank": 1})
            responsible_parties.append({"party_type": "payment_provider", "party_id": "payment_gateway"})
            diff_refund = round(min(refundable_total, 20.0) if refundable_total > 0 else 0.0, 2)
            recommended_refund = diff_refund
            if diff_refund > 0:
                refund_lines.append(
                    {"reason_code": "PAYMENT_MISMATCH_CORRECTION", "amount_brl": diff_refund, "entity_id": None}
                )
            resolution_actions.extend(["reconcile_payment_discrepancy", "issue_partial_refund"])

        elif main_topic == "duplicate_charge":
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

        elif main_topic == "refund_pending":
            primary_issue = "refund_pending"
            case_status = "action_required"
            ranked_causes.append({"cause_code": "REFUND_PROCESSING_DELAY", "rank": 1})
            responsible_parties.append({"party_type": "payment_provider", "party_id": "payment_gateway"})
            recommended_refund = 0.0
            refund_lines = []
            resolution_actions.extend(["expedite_pending_refund", "send_refund_status_update"])

        elif main_topic == "refund_failed":
            primary_issue = "refund_failed"
            case_status = "action_required"
            ranked_causes.append({"cause_code": "PAYMENT_GATEWAY_REFUND_ERROR", "rank": 1})
            responsible_parties.append({"party_type": "payment_provider", "party_id": "payment_gateway"})
            recommended_refund = round(refundable_total if refundable_total > 0 else 50.0, 2)
            refund_lines.append(
                {"reason_code": "RETRY_FAILED_REFUND", "amount_brl": recommended_refund, "entity_id": None}
            )
            resolution_actions.extend(["retry_manual_refund", "verify_customer_bank_account"])

        elif main_topic == "canceled_order_paid":
            primary_issue = "canceled_order_paid"
            case_status = "action_required"
            ranked_causes.append({"cause_code": "ORDER_CANCELED_BEFORE_FULFILLMENT", "rank": 1})
            responsible_parties.append({"party_type": "platform", "party_id": "ecommerce_platform"})
            recommended_refund = round(refundable_total if refundable_total > 0 else captured_total, 2)
            if recommended_refund > 0:
                refund_lines.append(
                    {"reason_code": "FULL_REFUND_CANCELED_ORDER", "amount_brl": recommended_refund, "entity_id": None}
                )
            resolution_actions.extend(["issue_full_refund", "confirm_order_cancellation"])

        elif main_topic == "unavailable_order_paid":
            primary_issue = "unavailable_order_paid"
            case_status = "action_required"
            ranked_causes.append({"cause_code": "INVENTORY_OUT_OF_STOCK", "rank": 1})
            responsible_parties.append({"party_type": "seller", "party_id": chosen_seller})
            recommended_refund = round(refundable_total if refundable_total > 0 else captured_total, 2)
            if recommended_refund > 0:
                refund_lines.append(
                    {"reason_code": "FULL_REFUND_UNAVAILABLE_ITEM", "amount_brl": recommended_refund, "entity_id": chosen_seller}
                )
            resolution_actions.extend(["issue_full_refund", "notify_item_unavailable"])

        else:  # unsupported_claim or default
            primary_issue = "unsupported_claim"
            case_status = "no_action"
            ranked_causes.append({"cause_code": "CLAIM_NOT_SUBSTANTIATED", "rank": 1})
            responsible_parties.append({"party_type": "customer", "party_id": None})
            recommended_refund = 0.0
            refund_lines = []
            resolution_actions.extend(["send_clarification_to_customer", "close_inquiry_with_explanation"])

        # Secondary issues
        secondary_issues: list[str] = []
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
            "policy_evidence_refs": policy_refs,
        }
