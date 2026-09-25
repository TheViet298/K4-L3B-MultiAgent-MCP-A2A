from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from student_agent.financial_worker import FinancialWorker
from student_agent.policy_worker import PolicyWorker


def make_evidence(tool_name: str, domain: str, data: Any, ev_ref: str = "ev_test_12345678901234567890") -> dict[str, Any]:
    return {
        "schema_version": "day09-mcp-evidence-v1",
        "evidence_ref": ev_ref,
        "result_hash": "sha256:" + "a" * 64,
        "domain": domain,
        "data": data,
        "warnings": [],
    }


@pytest.mark.anyio
async def test_financial_worker_reconciled() -> None:
    gateway = AsyncMock()
    gateway.call.side_effect = [
        make_evidence("get_order_payments", "payment", [
            {"payment_sequential": "1", "payment_type": "credit_card", "payment_value": "100.50"},
            {"payment_sequential": "2", "payment_type": "voucher", "payment_value": "20.00"},
        ], "ev_pay_12345678901234567890"),
    ]

    worker = FinancialWorker(gateway)
    res = await worker.investigate("CASE_001", "order-001")

    assert res.payment_analysis["verdict"] == "reconciled"
    assert res.payment_analysis["captured_total_brl"] == 120.50
    assert res.payment_analysis["refunded_total_brl"] == 0.0
    assert res.payment_analysis["refundable_total_brl"] == 120.50
    assert len(res.evidence_refs) == 1


@pytest.mark.anyio
async def test_financial_worker_duplicate_capture() -> None:
    gateway = AsyncMock()
    gateway.call.side_effect = [
        make_evidence("get_order_payments", "payment", [
            {"payment_sequential": "1", "payment_type": "credit_card", "payment_value": "50.00"},
            {"payment_sequential": "1", "payment_type": "credit_card", "payment_value": "50.00"},
        ], "ev_pay_dup12345678901234567"),
    ]

    worker = FinancialWorker(gateway)
    res = await worker.investigate("CASE_002", "order-002")

    assert res.payment_analysis["verdict"] == "duplicate_capture"
    assert res.suggested_primary_issue == "duplicate_charge"
    assert len(res.data_conflicts) > 0


@pytest.mark.anyio
async def test_policy_worker_rule_matching() -> None:
    gateway = AsyncMock()
    gateway.call.return_value = make_evidence("get_policy", "policy", {
        "currency": "BRL",
        "policy_version": "EC_POLICY_V2",
        "rules": {
            "late_delivery_seller": {
                "case_status": "action_required",
                "recommended_action": "refund_freight",
                "refund_brl": 18.0,
                "responsible_parties": [{"party_id": "seller-001", "party_type": "seller"}],
            }
        },
    }, "ev_pol_12345678901234567890")

    worker = PolicyWorker(gateway)
    case = {
        "case_id": "CASE_POL_001",
        "policy_version": "EC_POLICY_V2",
        "customer_request": {
            "claims": [{"claim_id": "c1", "topic": "late_delivery_seller"}],
        },
    }

    res = await worker.evaluate(
        case=case,
        entity_result={"affected_entities": {"seller_ids": ["seller-001"]}},
        shipment_analysis={"verdict": "seller_delay", "late_seller_ids": ["seller-001"], "timeline_complete": True},
        payment_analysis={"verdict": "reconciled", "refundable_total_brl": 50.0},
    )

    assert res.assessment["primary_issue"] == "late_delivery_seller"
    assert res.assessment["case_status"] == "action_required"
    assert res.financial_resolution["recommended_refund_brl"] == 18.0
    assert len(res.financial_resolution["refund_lines"]) == 1
    assert res.root_cause_analysis["responsible_parties"][0]["party_type"] == "seller"
