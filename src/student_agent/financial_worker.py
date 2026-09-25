from __future__ import annotations

from typing import Any
from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter


async def analyze_financial(
    case: dict[str, Any],
    order_id: str | None,
    gateway: EvidenceGateway,
    trace: TraceWriter,
) -> dict[str, Any]:
    """Phần việc của Cao Đức Hiệp (Financial Worker & Verifier).

    Nhiệm vụ:
    1. Truy vấn bảng thanh toán order_payments (giá hàng, phí ship, voucher).
    2. Deterministic Logic: Tính toán chính xác captured, refunded, refundable, recommended_refund.
    3. Đảm bảo quy tắc kiểm định Invariants và Schema.
    """
    case_id = case["case_id"]
    evidence_refs: list[str] = []
    payment_references: list[str] = []

    captured_total_brl: float = 0.0
    refunded_total_brl: float = 0.0
    refundable_total_brl: float = 0.0
    recommended_refund_brl: float = 0.0
    refund_lines: list[dict[str, Any]] = []
    verdict = "reconciled"

    # TODO: Gọi MCP tool tra cứu payment khi MCP server sẵn sàng
    # if order_id:
    #     try:
    #         res = await gateway.call("get_order_payments", case_id=case_id, order_id=order_id)
    #         evidence_refs.append(res["evidence_ref"])
    #         trace.emit(case_id=case_id, event_type="tool_result_consumed", actor="financial_worker", tool_name="get_order_payments", evidence_refs=[res["evidence_ref"]])
    #     except Exception:
    #         verdict = "insufficient_evidence"

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="financial_worker",
        target="router",
        decision_code="financial_analyzed",
        evidence_refs=evidence_refs if evidence_refs else None,
    )

    return {
        "verdict": verdict,  # reconciled, capture_mismatch, duplicate_capture, refund_pending, refund_failed, refunded, insufficient_evidence
        "captured_total_brl": captured_total_brl,
        "refunded_total_brl": refunded_total_brl,
        "refundable_total_brl": refundable_total_brl,
        "recommended_refund_brl": recommended_refund_brl,
        "refund_lines": refund_lines,
        "payment_references": payment_references,
        "evidence_refs": evidence_refs,
    }
