from __future__ import annotations

from typing import Any
from .agents.policy_agent import PolicyAgent
from .cache import CaseCache
from .trace import TraceWriter


async def run_policy_worker(
    case: dict[str, Any],
    entity_result: dict[str, Any],
    shipment_analysis: dict[str, Any],
    payment_analysis: dict[str, Any],
    conflicts: list[dict[str, Any]],
    cache: CaseCache,
    trace: TraceWriter,
) -> dict[str, Any]:
    """Phần việc của Nguyễn Quang Đạo (Policy Worker).

    Nhiệm vụ:
    1. Tra cứu điều khoản Olist qua MCP tool get_policy.
    2. Kiểm tra thời hiệu khiếu nại: 7 ngày đổi trả, 30 ngày bảo hành lỗi kỹ thuật.
    3. Đánh giá trách nhiệm, căn cứ điều khoản và đề xuất biện pháp xử lý.
    """
    agent = PolicyAgent(cache, trace)
    return await agent.evaluate(case, entity_result, shipment_analysis, payment_analysis, conflicts)
