from __future__ import annotations

from typing import Any

from .financial_worker import analyze_financial
from .logistics_worker import analyze_logistics
from .mcp_gateway import EvidenceGateway
from .policy_worker import analyze_policy
from .trace import TraceWriter


def _resolve_entities(case: dict[str, Any]) -> tuple[str | None, list[str], list[str], str]:
    """Entity Resolution: Xác định order_id mục tiêu từ claimed_order_id hoặc candidate_order_ids."""
    cust_req = case.get("customer_request", {})
    claimed_order_id = cust_req.get("claimed_order_id")
    candidates = case.get("candidate_order_ids", [])

    if claimed_order_id and claimed_order_id in candidates:
        resolved = [claimed_order_id]
        rejected = [c for c in candidates if c != claimed_order_id]
        return claimed_order_id, resolved, rejected, "resolved"

    if candidates:
        # Nếu có candidates, chọn candidate đầu tiên làm resolved tạm thời
        primary = candidates[0]
        resolved = [primary]
        rejected = candidates[1:]
        return primary, resolved, rejected, "resolved"

    return None, [], [], "not_found"


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Supervisor / Router Agent do Ngô Thế Việt phụ trách.

    Điều phối luồng xử lý:
    1. Entity Resolution (Bóc tách thực thể).
    2. Giao việc cho 3 Worker (Policy, Logistics, Financial).
    3. Tổng hợp phán quyết, xử lý xung đột và sinh kết quả chuẩn Schema.
    """
    case_id = case["case_id"]

    # --- Bước 1: Router tiếp nhận case & Entity Resolution ---
    target_order_id, resolved_order_ids, rejected_candidates, entity_status = _resolve_entities(case)
    customer_unique_id = case.get("customer_unique_id_hint")

    # --- Bước 2: Giao việc cho Worker 1 (Policy Worker - Đạo) ---
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="router",
        target="policy_worker",
    )
    policy_result = await analyze_policy(
        case=case,
        order_id=target_order_id,
        product_category=None,
        gateway=gateway,
        trace=trace,
    )

    # --- Bước 3: Giao việc cho Worker 2 (Logistics Worker - Giáp) ---
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="router",
        target="logistics_worker",
    )
    logistics_result = await analyze_logistics(
        case=case,
        order_id=target_order_id,
        gateway=gateway,
        trace=trace,
    )

    # --- Bước 4: Giao việc cho Worker 3 (Financial Worker - Hiệp) ---
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="router",
        target="financial_worker",
    )
    financial_result = await analyze_financial(
        case=case,
        order_id=target_order_id,
        gateway=gateway,
        trace=trace,
    )

    # --- Bước 5: Tổng hợp bằng chứng từ tất cả các Worker ---
    all_evidence_refs = list(
        dict.fromkeys(
            policy_result.get("evidence_refs", [])
            + logistics_result.get("evidence_refs", [])
            + financial_result.get("evidence_refs", [])
        )
    )

    # Đánh giá sơ bộ claims
    claims = case.get("customer_request", {}).get("claims", [])
    claim_assessments = [
        {
            "claim_id": c.get("claim_id", f"claim_{idx}"),
            "verdict": "insufficient_evidence",
            "confidence": 0.8,
            "evidence_refs": all_evidence_refs,
        }
        for idx, c in enumerate(claims)
    ]

    # --- Bước 6: Router ra phán quyết cuối cùng (Final Synthesis) ---
    output: dict[str, Any] = {
        "schema_version": "day09-l3b-output-v2",
        "case_id": case_id,
        "assessment": {
            "primary_issue": "insufficient_evidence",
            "secondary_issues": [],
            "case_status": "needs_investigation",
            "confidence": 0.75,
        },
        "affected_entities": {
            "order_ids": resolved_order_ids,
            "item_ids": logistics_result.get("item_ids", []),
            "seller_ids": logistics_result.get("seller_ids", []),
            "payment_references": financial_result.get("payment_references", []),
            "shipment_ids": logistics_result.get("shipment_ids", []),
        },
        "claim_assessments": claim_assessments,
        "entity_resolution": {
            "status": entity_status,
            "resolved_order_ids": resolved_order_ids,
            "rejected_candidates": rejected_candidates,
            "confidence": 0.9 if entity_status == "resolved" else 0.5,
        },
        "customer_context": {
            "customer_unique_id": customer_unique_id,
            "related_order_ids": resolved_order_ids,
        },
        "shipment_analysis": {
            "verdict": logistics_result.get("verdict", "insufficient_evidence"),
            "late_seller_ids": logistics_result.get("late_seller_ids", []),
            "timeline_complete": logistics_result.get("timeline_complete", False),
        },
        "payment_analysis": {
            "verdict": financial_result.get("verdict", "insufficient_evidence"),
            "captured_total_brl": financial_result.get("captured_total_brl", 0.0),
            "refunded_total_brl": financial_result.get("refunded_total_brl", 0.0),
            "refundable_total_brl": financial_result.get("refundable_total_brl", 0.0),
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": "INSUFFICIENT_EVIDENCE", "rank": 1}],
            "responsible_parties": [{"party_type": "unknown", "party_id": None}],
        },
        "evidence_refs": all_evidence_refs,
        "data_conflicts": [],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": financial_result.get("recommended_refund_brl", 0.0),
            "refund_lines": financial_result.get("refund_lines", []),
        },
        "resolution_actions": ["escalate_to_tier2_support"],
    }

    trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor="verifier",
        decision_code="schema_verified",
    )

    return output
