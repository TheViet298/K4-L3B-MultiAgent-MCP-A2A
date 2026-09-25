from __future__ import annotations

from typing import Any
from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter


async def analyze_policy(
    case: dict[str, Any],
    order_id: str | None,
    product_category: str | None,
    gateway: EvidenceGateway,
    trace: TraceWriter,
) -> dict[str, Any]:
    """Phần việc của Nguyễn Quang Đạo (Policy Worker).

    Nhiệm vụ:
    1. Tra cứu điều khoản Olist theo ngành hàng.
    2. Kiểm tra thời hiệu: 7 ngày đổi trả, 30 ngày bảo hành lỗi kỹ thuật.
    3. Trích dẫn chính xác điều khoản, lưu evidence_ref từ MCP.
    """
    case_id = case["case_id"]
    evidence_refs: list[str] = []
    applicable_clauses: list[str] = []

    # TODO: Gọi MCP tool tra cứu policy khi MCP server sẵn sàng
    # try:
    #     res = await gateway.call("get_policy_clause", case_id=case_id, category=product_category or "general")
    #     evidence_refs.append(res["evidence_ref"])
    #     trace.emit(case_id=case_id, event_type="tool_result_consumed", actor="policy_worker", tool_name="get_policy_clause", evidence_refs=[res["evidence_ref"]])
    # except Exception:
    #     pass

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="policy_worker",
        target="router",
        decision_code="policy_analyzed",
        evidence_refs=evidence_refs if evidence_refs else None,
    )

    return {
        "is_within_return_window": True,
        "is_within_warranty_window": True,
        "applicable_clauses": applicable_clauses,
        "evidence_refs": evidence_refs,
    }
