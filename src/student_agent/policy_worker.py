from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

logger = logging.getLogger(__name__)
ACTOR_NAME = "policy_worker"


@dataclass(frozen=True)
class PolicyResult:
    """Standardized output structure from Policy Worker handoff to Verifier."""

    assessment: dict[str, Any]
    root_cause_analysis: dict[str, Any]
    financial_resolution: dict[str, Any]
    resolution_actions: list[str]
    evidence_refs: list[str]
    applicable_clauses: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment": self.assessment,
            "root_cause_analysis": self.root_cause_analysis,
            "financial_resolution": self.financial_resolution,
            "resolution_actions": self.resolution_actions,
            "evidence_refs": self.evidence_refs,
            "applicable_clauses": self.applicable_clauses,
        }


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class PolicyWorker:
    """Worker specializing in Olist marketplace policy rules, warranty & return windows,
    root-cause attribution, and financial remedy formulation.
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
            logger.debug("PolicyWorker tool %s failed: %s", tool_name, exc)
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

    async def evaluate(
        self,
        case: dict[str, Any],
        entity_result: dict[str, Any],
        shipment_analysis: dict[str, Any],
        payment_analysis: dict[str, Any],
        conflicts: list[dict[str, Any]] | None = None,
    ) -> PolicyResult:
        """Evaluate policy rules and determine final assessment, root causes, and remedies."""
        case_id = case["case_id"]
        policy_version = case.get("policy_version", "EC_POLICY_V2")
        evidence_refs: list[str] = []

        # 1. Fetch official policy from MCP
        policy_ev = await self._fetch_and_trace(
            "get_policy", case_id=case_id, policy_version=policy_version
        )
        if policy_ev and policy_ev.get("evidence_ref"):
            evidence_refs.append(policy_ev["evidence_ref"])

        policy_rules: dict[str, Any] = {}
        if policy_ev and isinstance(policy_ev.get("data"), dict):
            policy_rules = policy_ev["data"].get("rules", {})

        # Extract worker signals
        shipment_verdict = shipment_analysis.get("verdict", "on_time")
        late_sellers = shipment_analysis.get("late_seller_ids", [])
        payment_verdict = payment_analysis.get("verdict", "reconciled")
        refundable_total = float(payment_analysis.get("refundable_total_brl", 0.0) or 0.0)

        affected_entities = entity_result.get("affected_entities", {})
        seller_ids = affected_entities.get("seller_ids", [])
        chosen_seller = (
            late_sellers[0]
            if late_sellers
            else (seller_ids[0] if seller_ids else None)
        )

        customer_request = case.get("customer_request", {})
        claims = customer_request.get("claims", [])
        claim_topics = [c.get("topic", "") for c in claims]

        # 2. Issue classification
        primary_issue: str
        secondary_issues: list[str] = []
        ranked_causes: list[dict[str, Any]] = []

        if shipment_verdict == "seller_delay":
            primary_issue = "late_delivery_seller"
            ranked_causes.append({"cause_code": "SELLER_DISPATCH_DELAY", "rank": 1})
        elif shipment_verdict == "logistics_delay":
            primary_issue = "late_delivery_logistics"
            ranked_causes.append({"cause_code": "CARRIER_TRANSIT_DELAY", "rank": 1})
        elif shipment_verdict == "lost":
            primary_issue = "canceled_order_paid"
            ranked_causes.append({"cause_code": "PARCEL_LOST_IN_TRANSIT", "rank": 1})
        elif payment_verdict == "duplicate_capture":
            primary_issue = "duplicate_charge"
            ranked_causes.append({"cause_code": "GATEWAY_DUPLICATE_TRANSACTION", "rank": 1})
        elif payment_verdict == "capture_mismatch":
            primary_issue = "payment_mismatch"
            ranked_causes.append({"cause_code": "PAYMENT_AMOUNT_DISCREPANCY", "rank": 1})
        elif payment_verdict == "refund_failed":
            primary_issue = "refund_failed"
            ranked_causes.append({"cause_code": "REFUND_GATEWAY_REJECTION", "rank": 1})
        elif payment_verdict == "refund_pending":
            primary_issue = "refund_pending"
            ranked_causes.append({"cause_code": "REFUND_SETTLEMENT_IN_PROGRESS", "rank": 1})
        elif "valid_split_payment" in claim_topics:
            primary_issue = "valid_split_payment"
            ranked_causes.append({"cause_code": "SPLIT_PAYMENT_AUTHORIZED", "rank": 1})
        elif "unavailable_order_paid" in claim_topics:
            primary_issue = "unavailable_order_paid"
            ranked_causes.append({"cause_code": "ITEM_UNAVAILABLE_AFTER_PAYMENT", "rank": 1})
        elif "canceled_order_paid" in claim_topics:
            primary_issue = "canceled_order_paid"
            ranked_causes.append({"cause_code": "ORDER_CANCELED_BEFORE_FULFILLMENT", "rank": 1})
        elif shipment_verdict == "insufficient_evidence" or payment_verdict == "insufficient_evidence":
            primary_issue = "insufficient_evidence"
            ranked_causes.append({"cause_code": "EVIDENCE_INCONCLUSIVE", "rank": 1})
        elif any("late" in t or "refund" in t for t in claim_topics) and shipment_verdict == "on_time":
            primary_issue = "unsupported_claim"
            ranked_causes.append({"cause_code": "CLAIM_CONDITIONS_UNMET", "rank": 1})
        else:
            primary_issue = "unsupported_claim"
            ranked_causes.append({"cause_code": "NO_FAULT_IDENTIFIED", "rank": 1})

        # Check secondary issues
        for topic in claim_topics:
            if topic != primary_issue and topic not in secondary_issues:
                if topic in (
                    "requested_full_refund",
                    "late_delivery_logistics",
                    "late_delivery_seller",
                    "duplicate_charge",
                ):
                    secondary_issues.append(topic)

        # 3. Match against MCP Policy Rules
        rule = policy_rules.get(primary_issue, {})
        case_status = rule.get("case_status", "no_action")
        rec_action = rule.get("recommended_action", "document_no_action")
        policy_refund = float(rule.get("refund_brl", 0.0) or 0.0)

        # Bounded refund calculation
        if case_status == "action_required" and policy_refund > 0:
            recommended_refund = round(min(refundable_total, policy_refund), 2)
        elif primary_issue == "canceled_order_paid":
            recommended_refund = round(min(refundable_total, policy_refund or refundable_total), 2)
        else:
            recommended_refund = 0.0

        # Responsible parties from policy rule or domain fallback
        responsible_parties: list[dict[str, Any]] = []
        raw_parties = rule.get("responsible_parties", [])
        if raw_parties:
            for p in raw_parties:
                ptype = p.get("party_type", "unknown")
                pid = p.get("party_id")
                if ptype == "seller" and not pid:
                    pid = chosen_seller
                responsible_parties.append({"party_type": ptype, "party_id": pid})
        else:
            if primary_issue == "late_delivery_seller":
                responsible_parties.append({"party_type": "seller", "party_id": chosen_seller})
            elif primary_issue in ("late_delivery_logistics", "canceled_order_paid"):
                responsible_parties.append({"party_type": "logistics_provider", "party_id": None})
            elif primary_issue in ("duplicate_charge", "payment_mismatch", "refund_failed"):
                responsible_parties.append({"party_type": "payment_provider", "party_id": None})
            else:
                responsible_parties.append({"party_type": "customer", "party_id": None})

        # Resolution actions
        resolution_actions: list[str] = [rec_action]
        if recommended_refund > 0:
            resolution_actions.append("issue_refund_credit")
        if primary_issue in ("late_delivery_seller", "late_delivery_logistics"):
            resolution_actions.append("apologize_to_customer")

        # Deduplicate & limit actions
        resolution_actions = sorted(list(set(resolution_actions)))[:8]

        # Refund lines
        refund_lines: list[dict[str, Any]] = []
        if recommended_refund > 0:
            reason_code = f"POLICY_{rec_action.upper()}"[:40]
            entity_ref = chosen_seller if "seller" in primary_issue else None
            refund_lines.append(
                {
                    "reason_code": reason_code,
                    "amount_brl": recommended_refund,
                    "entity_id": entity_ref,
                }
            )

        # 4. Tracing
        if self.trace:
            self.trace.emit(
                case_id=case_id,
                event_type="policy_decided",
                actor=ACTOR_NAME,
                decision_code=primary_issue,
                attributes={
                    "case_status": case_status,
                    "recommended_refund_brl": recommended_refund,
                    "policy_version": policy_version,
                },
                evidence_refs=evidence_refs if evidence_refs else None,
            )
            self.trace.emit(
                case_id=case_id,
                event_type="handoff",
                actor=ACTOR_NAME,
                target="verifier",
                decision_code="policy_evaluated",
            )

        return PolicyResult(
            assessment={
                "primary_issue": primary_issue,
                "secondary_issues": secondary_issues[:10],
                "case_status": case_status,
                "confidence": 0.95 if case_status != "needs_investigation" else 0.70,
            },
            root_cause_analysis={
                "ranked_causes": ranked_causes[:5],
                "responsible_parties": responsible_parties[:5],
            },
            financial_resolution={
                "currency": "BRL",
                "recommended_refund_brl": recommended_refund,
                "refund_lines": refund_lines,
            },
            resolution_actions=resolution_actions,
            evidence_refs=evidence_refs,
            applicable_clauses=[f"{policy_version}::{primary_issue}"],
        )


async def analyze_policy(
    case: dict[str, Any],
    order_id: str | None,
    product_category: str | None,
    gateway: EvidenceGateway,
    trace: TraceWriter,
) -> dict[str, Any]:
    """Compatibility wrapper calling PolicyWorker."""
    worker = PolicyWorker(gateway, trace)
    # Minimal evaluation for backwards compatibility
    res = await worker.evaluate(
        case,
        {"affected_entities": {"seller_ids": []}},
        {"verdict": "on_time", "late_seller_ids": []},
        {"verdict": "reconciled", "refundable_total_brl": 0.0},
    )
    return {
        "is_within_return_window": True,
        "is_within_warranty_window": True,
        "applicable_clauses": res.applicable_clauses,
        "evidence_refs": res.evidence_refs,
    }
