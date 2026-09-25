from __future__ import annotations

from typing import Any
from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter


async def analyze_logistics(
    case: dict[str, Any],
    order_id: str | None,
    gateway: EvidenceGateway,
    trace: TraceWriter,
) -> dict[str, Any]:
    """Phần việc của Nguyễn Văn Giáp (Logistics Worker).

    Nhiệm vụ:
    1. Truy vấn dữ liệu bảng orders, order_items, shipments trong Olist DB.
    2. So sánh ngày giao thực tế vs ngày hẹn dự kiến của đơn hàng.
    3. Xác định lỗi giao chậm thuộc về Shipper (logistics_delay) hay Seller (seller_delay).
    """
    case_id = case["case_id"]
    evidence_refs: list[str] = []
    late_seller_ids: list[str] = []
    shipment_ids: list[str] = []
    seller_ids: list[str] = []
    item_ids: list[str] = []
    verdict = "on_time"
    timeline_complete = True

    # TODO: Gọi MCP tool tra cứu shipment khi MCP server sẵn sàng
    # if order_id:
    #     try:
    #         res = await gateway.call("get_shipment_details", case_id=case_id, order_id=order_id)
    #         evidence_refs.append(res["evidence_ref"])
    #         trace.emit(case_id=case_id, event_type="tool_result_consumed", actor="logistics_worker", tool_name="get_shipment_details", evidence_refs=[res["evidence_ref"]])
    #     except Exception:
    #         timeline_complete = False

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="logistics_worker",
        target="router",
        decision_code="logistics_analyzed",
        evidence_refs=evidence_refs if evidence_refs else None,
    )

    return {
        "verdict": verdict,  # on_time, seller_delay, logistics_delay, lost, returned, conflicting, insufficient_evidence
        "late_seller_ids": late_seller_ids,
        "shipment_ids": shipment_ids,
        "seller_ids": seller_ids,
        "item_ids": item_ids,
        "timeline_complete": timeline_complete,
        "evidence_refs": evidence_refs,
    }
