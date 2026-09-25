from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from student_agent.agents.verifier import VerifierAgent
from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case


@pytest.fixture
def contracts() -> Contracts:
    root = Path(__file__).resolve().parents[1]
    return Contracts(root / "contracts" / "schemas")


@pytest.fixture
def mock_trace(contracts: Contracts, tmp_path: Path) -> TraceWriter:
    return TraceWriter(tmp_path / "trace.jsonl", contracts)


def test_verifier_repairs_and_validates(contracts: Contracts, mock_trace: TraceWriter) -> None:
    verifier = VerifierAgent(contracts, mock_trace)

    sample_case = {
        "case_id": "L3B_CASE_001",
        "opened_at": "2018-01-01T09:00:00-03:00",
        "customer_request": {
            "language": "vi",
            "message": "Kiểm tra giao trễ",
            "claims": [{"claim_id": "claim-001", "topic": "late_delivery_seller"}],
        },
    }

    raw_output = {
        "schema_version": "day09-l3b-output-v2",
        "case_id": "L3B_CASE_001",
        "assessment": {
            "primary_issue": "late_delivery_seller",
            "secondary_issues": [],
            "case_status": "action_required",
            "confidence": 1.0,  # Should be calibrated/smoothed
        },
        "affected_entities": {
            "order_ids": ["order-123"],
            "item_ids": ["item-1"],
            "seller_ids": ["seller-456"],
            "payment_references": [],
            "shipment_ids": [],
        },
        "claim_assessments": [
            {
                "claim_id": "claim-001",
                "verdict": "supported",
                "confidence": 0.9,
                "evidence_refs": [],
            }
        ],
        "entity_resolution": {
            "status": "resolved",
            "resolved_order_ids": ["order-123"],
            "rejected_candidates": [],
            "confidence": 0.95,
        },
        "customer_context": {
            "customer_unique_id": "cust-001",
            "related_order_ids": ["order-123"],
        },
        "shipment_analysis": {
            "verdict": "seller_delay",
            "late_seller_ids": [],  # Invariant repair should populate this
            "timeline_complete": True,
        },
        "payment_analysis": {
            "verdict": "reconciled",
            "captured_total_brl": 150.0,
            "refunded_total_brl": 0.0,
            "refundable_total_brl": 150.0,
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": "SELLER_DISPATCH_DELAY", "rank": 1}],
            "responsible_parties": [],  # Invariant repair should add seller
        },
        "evidence_refs": [],
        "data_conflicts": [],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": 25.0,
            "refund_lines": [
                {"reason_code": "LATE_DELIVERY_COMPENSATION", "amount_brl": 25.0, "entity_id": "seller-456"}
            ],
        },
        "resolution_actions": ["notify_seller_delay_penalty"],
    }

    result = verifier.verify_and_repair(raw_output, sample_case)

    # Check that confidence was calibrated away from 1.0
    assert result["assessment"]["confidence"] <= 0.95
    # Check that late_seller_ids is not empty
    assert len(result["shipment_analysis"]["late_seller_ids"]) > 0
    # Check that responsible parties has a seller
    assert any(p["party_type"] == "seller" for p in result["root_cause_analysis"]["responsible_parties"])
    # Schema check must pass
    contracts.validate_output(result, "test_output")


def test_solve_case_mock_workflow(contracts: Contracts, mock_trace: TraceWriter) -> None:
    import asyncio

    sample_case = {
        "case_id": "L3B_CASE_001",
        "opened_at": "2018-01-01T09:00:00-03:00",
        "customer_request": {
            "language": "vi",
            "message": "Điều tra đơn hàng",
            "claimed_order_id": "order-123",
            "claims": [{"claim_id": "claim-001", "topic": "late_delivery_logistics"}],
        },
        "candidate_order_ids": ["order-123", "order-999"],
        "customer_unique_id_hint": "cust-001",
    }

    mock_gateway = MagicMock()
    mock_gateway._contracts = contracts
    mock_gateway.list_tools = AsyncMock(return_value=["get_order", "get_shipment"])
    mock_gateway.call = AsyncMock(
        return_value={
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": "ev_0123456789012345678901",
            "result_hash": "sha256:" + "a" * 64,
            "domain": "order",
            "data": {
                "items": [{"item_id": "item-1", "seller_id": "seller-1"}],
                "shipment": {
                    "order_delivered_customer_date": "2018-01-10",
                    "order_estimated_delivery_date": "2018-01-08",
                    "order_purchase_timestamp": "2018-01-01",
                    "order_delivered_carrier_date": "2018-01-03",
                },
            },
        }
    )

    output = asyncio.run(solve_case(sample_case, mock_gateway, mock_trace))

    contracts.validate_output(output, "workflow_output")
    assert output["case_id"] == "L3B_CASE_001"
    assert output["assessment"]["primary_issue"] in [
        "canceled_order_paid", "unavailable_order_paid", "late_delivery_seller",
        "late_delivery_logistics", "valid_split_payment", "payment_mismatch",
        "duplicate_charge", "refund_pending", "refund_failed",
        "unsupported_claim", "insufficient_evidence"
    ]
