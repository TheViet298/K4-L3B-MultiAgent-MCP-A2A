from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from student_agent.logistics_worker import LogisticsWorker, parse_iso_datetime


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
async def test_on_time_delivery() -> None:
    gateway = AsyncMock()
    gateway.call.side_effect = [
        make_evidence("get_order", "order", {
            "order_id": "ord-001",
            "order_status": "delivered",
            "order_delivered_carrier_date": "2018-05-12T09:00:00-03:00",
            "order_delivered_customer_date": "2018-05-18T09:00:00-03:00",
            "order_estimated_delivery_date": "2018-05-20T09:00:00-03:00",
        }, "ev_order_12345678901234567890"),
        make_evidence("get_order_items", "item", [
            {
                "order_id": "ord-001",
                "order_item_id": "item-001",
                "seller_id": "seller-001",
                "shipping_limit_date": "2018-05-13T09:00:00-03:00",
            }
        ], "ev_items_12345678901234567890"),
        make_evidence("get_shipment_summary", "shipment", {
            "order_id": "ord-001",
            "delivered_carrier_at": "2018-05-12T09:00:00-03:00",
            "delivered_customer_at": "2018-05-18T09:00:00-03:00",
            "estimated_delivery_at": "2018-05-20T09:00:00-03:00",
            "events": [],
        }, "ev_shipment_12345678901234567890"),
    ]

    worker = LogisticsWorker(gateway)
    res = await worker.investigate("CASE_TEST", "ord-001")

    assert res.shipment_analysis["verdict"] == "on_time"
    assert res.shipment_analysis["late_seller_ids"] == []
    assert res.shipment_analysis["timeline_complete"] is True
    assert "seller-001" in res.affected_entities["seller_ids"]
    assert "item-001" in res.affected_entities["item_ids"]
    assert len(res.evidence_refs) == 3


@pytest.mark.anyio
async def test_seller_delay() -> None:
    gateway = AsyncMock()
    # Carrier received on 2018-05-15, but seller limit was 2018-05-13.
    # Customer received late on 2018-05-22 (estimated was 2018-05-20).
    gateway.call.side_effect = [
        make_evidence("get_order", "order", {
            "order_id": "ord-002",
            "order_status": "delivered",
            "order_delivered_carrier_date": "2018-05-15T09:00:00-03:00",
            "order_delivered_customer_date": "2018-05-22T09:00:00-03:00",
            "order_estimated_delivery_date": "2018-05-20T09:00:00-03:00",
        }, "ev_order_12345678901234567890"),
        make_evidence("get_order_items", "item", [
            {
                "order_id": "ord-002",
                "order_item_id": "item-002",
                "seller_id": "seller-bad",
                "shipping_limit_date": "2018-05-13T09:00:00-03:00",
            }
        ], "ev_items_12345678901234567890"),
        make_evidence("get_shipment_summary", "shipment", {
            "order_id": "ord-002",
            "delivered_carrier_at": "2018-05-15T09:00:00-03:00",
            "delivered_customer_at": "2018-05-22T09:00:00-03:00",
            "estimated_delivery_at": "2018-05-20T09:00:00-03:00",
            "events": [],
        }, "ev_shipment_12345678901234567890"),
    ]

    worker = LogisticsWorker(gateway)
    res = await worker.investigate("CASE_TEST", "ord-002")

    assert res.shipment_analysis["verdict"] == "seller_delay"
    assert res.shipment_analysis["late_seller_ids"] == ["seller-bad"]
    assert res.shipment_analysis["timeline_complete"] is True
    assert res.suggested_primary_issue == "late_delivery_seller"
    assert res.responsible_party == {"party_type": "seller", "party_id": "seller-bad"}


@pytest.mark.anyio
async def test_logistics_delay() -> None:
    gateway = AsyncMock()
    # Seller handed over on time (2018-05-12 <= 2018-05-13).
    # But customer received late (2018-05-25 > 2018-05-20).
    gateway.call.side_effect = [
        make_evidence("get_order", "order", {
            "order_id": "ord-003",
            "order_status": "delivered",
            "order_delivered_carrier_date": "2018-05-12T09:00:00-03:00",
            "order_delivered_customer_date": "2018-05-25T09:00:00-03:00",
            "order_estimated_delivery_date": "2018-05-20T09:00:00-03:00",
        }, "ev_order_12345678901234567890"),
        make_evidence("get_order_items", "item", [
            {
                "order_id": "ord-003",
                "order_item_id": "item-003",
                "seller_id": "seller-good",
                "shipping_limit_date": "2018-05-13T09:00:00-03:00",
            }
        ], "ev_items_12345678901234567890"),
        make_evidence("get_shipment_summary", "shipment", {
            "order_id": "ord-003",
            "delivered_carrier_at": "2018-05-12T09:00:00-03:00",
            "delivered_customer_at": "2018-05-25T09:00:00-03:00",
            "estimated_delivery_at": "2018-05-20T09:00:00-03:00",
            "events": [
                {
                    "event_type": "delivered_late",
                    "actor": "logistics_provider",
                    "status": "confirmed",
                }
            ],
        }, "ev_shipment_12345678901234567890"),
    ]

    worker = LogisticsWorker(gateway)
    res = await worker.investigate("CASE_TEST", "ord-003")

    assert res.shipment_analysis["verdict"] == "logistics_delay"
    assert res.shipment_analysis["late_seller_ids"] == []
    assert res.shipment_analysis["timeline_complete"] is True
    assert res.suggested_primary_issue == "late_delivery_logistics"
    assert res.responsible_party == {"party_type": "logistics_provider", "party_id": None}


@pytest.mark.anyio
async def test_lost_shipment() -> None:
    gateway = AsyncMock()
    gateway.call.side_effect = [
        make_evidence("get_order", "order", {
            "order_id": "ord-004",
            "order_status": "canceled_in_transit",
        }, "ev_order_12345678901234567890"),
        make_evidence("get_order_items", "item", [], "ev_items_12345678901234567890"),
        make_evidence("get_shipment_summary", "shipment", {
            "order_id": "ord-004",
            "events": [{"event_type": "package_lost"}],
        }, "ev_shipment_12345678901234567890"),
    ]

    worker = LogisticsWorker(gateway)
    res = await worker.investigate("CASE_TEST", "ord-004")

    assert res.shipment_analysis["verdict"] == "lost"


@pytest.mark.anyio
async def test_trace_emission() -> None:
    gateway = AsyncMock()
    gateway.call.side_effect = [
        make_evidence("get_order", "order", {"order_id": "ord-005"}, "ev_order_12345678901234567890"),
        make_evidence("get_order_items", "item", [], "ev_items_12345678901234567890"),
        make_evidence("get_shipment_summary", "shipment", {}, "ev_shipment_12345678901234567890"),
    ]
    trace = MagicMock()

    worker = LogisticsWorker(gateway, trace)
    await worker.investigate("CASE_TRACE", "ord-005")

    assert trace.emit.call_count == 3
    # Check that actor is logistics_worker and event_type is tool_result_consumed
    call_args = trace.emit.call_args_list[0].kwargs
    assert call_args["actor"] == "logistics_worker"
    assert call_args["event_type"] == "tool_result_consumed"
    assert call_args["tool_name"] == "get_order"
    assert call_args["evidence_refs"] == ["ev_order_12345678901234567890"]
