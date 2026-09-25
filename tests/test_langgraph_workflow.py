from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter
from student_agent.workflow import (
    AgentState,
    build_multi_agent_graph,
    dispute_resolution_graph,
    solve_case,
)


@pytest.fixture
def contracts() -> Contracts:
    root = Path(__file__).resolve().parents[1]
    return Contracts(root / "contracts" / "schemas")


@pytest.fixture
def mock_trace(contracts: Contracts, tmp_path: Path) -> TraceWriter:
    return TraceWriter(tmp_path / "trace.jsonl", contracts)


def test_langgraph_graph_structure() -> None:
    """Verify that LangGraph graph contains all multi-agent nodes and expected connections."""
    graph = build_multi_agent_graph()
    nodes = set(graph.nodes.keys())
    expected_nodes = {
        "router",
        "entity_agent",
        "shipment_specialist",
        "payment_specialist",
        "claim_assessor",
        "conflict_resolver",
        "policy_agent",
        "verifier",
    }
    assert expected_nodes.issubset(nodes)

    compiled = graph.compile()
    assert compiled is not None


@pytest.mark.anyio
async def test_langgraph_ainvoke_execution(contracts: Contracts, mock_trace: TraceWriter) -> None:
    """Verify that ainvoke executes through the entire LangGraph state machine."""
    sample_case = {
        "case_id": "L3B_CASE_LANGGRAPH_001",
        "opened_at": "2018-02-01T10:00:00-03:00",
        "customer_request": {
            "language": "vi",
            "message": "Kiểm tra chậm hàng",
            "claimed_order_id": "order-lg-1",
            "claims": [{"claim_id": "claim-lg-01", "topic": "late_delivery_seller"}],
        },
        "candidate_order_ids": ["order-lg-1"],
        "customer_unique_id_hint": "cust-lg-001",
    }

    mock_gateway = MagicMock()
    mock_gateway._contracts = contracts
    mock_gateway.list_tools = AsyncMock(return_value=["get_order", "get_shipment", "get_payment"])
    mock_gateway.call = AsyncMock(
        return_value={
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": "ev_0123456789012345678999",
            "result_hash": "sha256:" + "b" * 64,
            "domain": "shipment",
            "data": {
                "seller_id": "seller-lg-1",
                "order_delivered_customer_date": "2018-02-15",
                "order_estimated_delivery_date": "2018-02-10",
                "order_purchase_timestamp": "2018-02-01",
                "order_delivered_carrier_date": "2018-02-08",
                "shipping_limit_date": "2018-02-05",
            },
        }
    )

    initial_state: AgentState = {
        "case_id": sample_case["case_id"],
        "case": sample_case,
        "gateway": mock_gateway,
        "trace": mock_trace,
    }

    result = await dispute_resolution_graph.ainvoke(initial_state)

    assert "final_output" in result
    output = result["final_output"]

    # Validate output schema
    contracts.validate_output(output, "langgraph_output")
    assert output["case_id"] == "L3B_CASE_LANGGRAPH_001"
    assert output["assessment"]["primary_issue"] == "late_delivery_seller"
    assert "seller-lg-1" in output["shipment_analysis"]["late_seller_ids"]


@pytest.mark.anyio
async def test_solve_case_wrapper(contracts: Contracts, mock_trace: TraceWriter) -> None:
    """Verify solve_case helper wraps LangGraph execution cleanly."""
    sample_case = {
        "case_id": "L3B_CASE_LANGGRAPH_002",
        "opened_at": "2018-02-01T10:00:00-03:00",
        "customer_request": {
            "language": "vi",
            "message": "Kiểm tra chậm hàng",
            "claimed_order_id": "order-lg-2",
            "claims": [{"claim_id": "claim-lg-02", "topic": "late_delivery_seller"}],
        },
        "candidate_order_ids": ["order-lg-2"],
        "customer_unique_id_hint": "cust-lg-002",
    }

    mock_gateway = MagicMock()
    mock_gateway._contracts = contracts
    mock_gateway.list_tools = AsyncMock(return_value=["get_order", "get_shipment"])
    mock_gateway.call = AsyncMock(
        return_value={
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": "ev_0123456789012345678988",
            "result_hash": "sha256:" + "c" * 64,
            "domain": "shipment",
            "data": {
                "seller_id": "seller-lg-2",
                "order_delivered_customer_date": "2018-02-15",
                "order_estimated_delivery_date": "2018-02-10",
                "order_purchase_timestamp": "2018-02-01",
                "order_delivered_carrier_date": "2018-02-08",
                "shipping_limit_date": "2018-02-05",
            },
        }
    )

    output = await solve_case(sample_case, mock_gateway, mock_trace)
    assert output["case_id"] == "L3B_CASE_LANGGRAPH_002"
    assert output["assessment"]["primary_issue"] == "late_delivery_seller"
