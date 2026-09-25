from __future__ import annotations

import logging
import re
from typing import Any

from ..contracts import Contracts
from ..trace import TraceWriter

logger = logging.getLogger(__name__)

EVIDENCE_REF_PATTERN = re.compile(r"^ev_[A-Za-z0-9_-]{20,96}$")


class VerifierAgent:
    """The Quality Gatekeeper: enforces invariants, consistency checks, calibration, and schema validity."""

    def __init__(self, contracts: Contracts, trace: TraceWriter) -> None:
        self.contracts = contracts
        self.trace = trace

    def verify_and_repair(self, output: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
        case_id = case["case_id"]

        # 1. Base identity & version check
        output["schema_version"] = "day09-l3b-output-v2"
        output["case_id"] = case_id

        # 2. Calibration guardrail
        assessment = output.get("assessment", {})
        conf = float(assessment.get("confidence", 0.85))
        if assessment.get("primary_issue") == "insufficient_evidence":
            conf = min(conf, 0.40)
        else:
            conf = max(0.50, min(conf, 0.95))
        assessment["confidence"] = round(conf, 2)

        # 3. Status vs Financial Consistency
        case_status = assessment.get("case_status", "no_action")
        financial = output.get("financial_resolution", {})
        refund_lines = financial.get("refund_lines", [])
        payment = output.get("payment_analysis", {})
        refundable = float(payment.get("refundable_total_brl") or 0.0)

        if case_status == "no_action":
            financial["recommended_refund_brl"] = 0.0
            financial["refund_lines"] = []
            # Remove any refund-giving actions
            output["resolution_actions"] = [
                act for act in output.get("resolution_actions", [])
                if "refund" not in act.lower()
            ]
        else:
            # Calculate sum of refund lines
            line_sum = round(sum(float(line.get("amount_brl", 0.0)) for line in refund_lines), 2)
            # Ensure not exceeding refundable
            if line_sum > refundable:
                line_sum = refundable
                if refund_lines:
                    refund_lines[0]["amount_brl"] = line_sum
                    refund_lines = [refund_lines[0]]
            financial["recommended_refund_brl"] = line_sum
            financial["refund_lines"] = refund_lines

        # 4. Shipment vs Responsible Parties Consistency
        shipment = output.get("shipment_analysis", {})
        root_cause = output.get("root_cause_analysis", {})
        responsible_parties = root_cause.get("responsible_parties", [])
        sellers = output.get("affected_entities", {}).get("seller_ids", [])

        if shipment.get("verdict") == "seller_delay":
            late_sellers = shipment.get("late_seller_ids", [])
            if not late_sellers:
                chosen = sellers[0] if sellers else "seller-001"
                shipment["late_seller_ids"] = [chosen]
                late_sellers = [chosen]
            # Ensure seller party exists
            has_seller = any(p.get("party_type") == "seller" for p in responsible_parties)
            if not has_seller:
                responsible_parties.append({"party_type": "seller", "party_id": late_sellers[0]})

        elif shipment.get("verdict") == "logistics_delay":
            has_carrier = any(p.get("party_type") == "logistics_provider" for p in responsible_parties)
            if not has_carrier:
                responsible_parties.append({"party_type": "logistics_provider", "party_id": "carrier_service"})

        root_cause["responsible_parties"] = responsible_parties[:5]

        # 5. Sanitize Evidence Refs
        cleaned_refs: list[str] = []
        for ref in output.get("evidence_refs", []):
            if isinstance(ref, str) and EVIDENCE_REF_PATTERN.match(ref) and ref not in cleaned_refs:
                cleaned_refs.append(ref)
        output["evidence_refs"] = cleaned_refs[:30]

        # Sanitize claim assessments evidence refs
        for claim in output.get("claim_assessments", []):
            claim_refs = []
            for r in claim.get("evidence_refs", []):
                if isinstance(r, str) and EVIDENCE_REF_PATTERN.match(r) and r not in claim_refs:
                    claim_refs.append(r)
            claim["evidence_refs"] = claim_refs[:20]

        # 6. Sanitize resolution actions (ensure unique, max 8, non-empty, max 80 chars)
        actions = []
        for act in output.get("resolution_actions", []):
            if isinstance(act, str) and act.strip():
                clean_act = act.strip()[:80]
                if clean_act not in actions:
                    actions.append(clean_act)
        if not actions:
            actions = ["acknowledge_customer_inquiry"]
        output["resolution_actions"] = actions[:8]

        # 7. Validate with JSON Schema
        self.contracts.validate_output(output, f"verifier:{case_id}")

        # 8. Emit verification completed trace event
        self.trace.emit(
            case_id=case_id,
            event_type="verification_completed",
            actor="verifier",
            attributes={
                "invariants_passed": True,
                "case_status": case_status,
                "confidence": output["assessment"]["confidence"],
            },
        )

        return output
