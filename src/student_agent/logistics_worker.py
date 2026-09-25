from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

ACTOR_NAME = "logistics_worker"


@dataclass(frozen=True)
class LogisticsResult:
    """Standardized output structure from Logistics Worker handoff to Supervisor/Coordinator."""

    shipment_analysis: dict[str, Any]
    affected_entities: dict[str, list[str]]
    evidence_refs: list[str]
    suggested_primary_issue: str | None = None
    responsible_party: dict[str, str | None] | None = None
    data_conflicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "shipment_analysis": self.shipment_analysis,
            "affected_entities": self.affected_entities,
            "evidence_refs": self.evidence_refs,
            "suggested_primary_issue": self.suggested_primary_issue,
            "responsible_party": self.responsible_party,
            "data_conflicts": self.data_conflicts,
        }


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Safely parse an ISO-8601 datetime string with timezone offset."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class LogisticsWorker:
    """Worker specializing in logistics investigation, timeline reconciliation,
    and fault attribution (Seller Delay vs Logistics Delay).
    """

    def __init__(self, gateway: EvidenceGateway, trace: TraceWriter | None = None) -> None:
        self.gateway = gateway
        self.trace = trace

    async def _fetch_and_trace(
        self, tool_name: str, *, case_id: str, **arguments: str
    ) -> dict[str, Any] | None:
        """Call an MCP tool and emit tool_result_consumed trace event."""
        try:
            evidence = await self.gateway.call(tool_name, case_id=case_id, **arguments)
        except Exception:
            return None

        evidence_ref = evidence.get("evidence_ref")
        if self.trace and evidence_ref:
            self.trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor=ACTOR_NAME,
                tool_name=tool_name,
                evidence_refs=[evidence_ref],
                attributes={"status": "ok", "domain": evidence.get("domain", "")},
            )
        return evidence

    async def investigate(self, case_id: str, order_id: str) -> LogisticsResult:
        """Perform end-to-end logistics investigation for a given order."""
        evidence_refs: list[str] = []
        data_conflicts: list[dict[str, Any]] = []

        # 1. Fetch order details
        order_ev = await self._fetch_and_trace("get_order", case_id=case_id, order_id=order_id)
        if order_ev and order_ev.get("evidence_ref"):
            evidence_refs.append(order_ev["evidence_ref"])

        # 2. Fetch order items (sellers, shipping limits)
        items_ev = await self._fetch_and_trace("get_order_items", case_id=case_id, order_id=order_id)
        if items_ev and items_ev.get("evidence_ref"):
            evidence_refs.append(items_ev["evidence_ref"])

        # 3. Fetch shipment summary (carrier dates, events, tracking)
        shipment_ev = await self._fetch_and_trace(
            "get_shipment_summary", case_id=case_id, order_id=order_id
        )
        if shipment_ev and shipment_ev.get("evidence_ref"):
            evidence_refs.append(shipment_ev["evidence_ref"])

        # Extract data payloads safely
        order_data: dict[str, Any] = (
            order_ev.get("data", {})
            if (order_ev and isinstance(order_ev.get("data"), dict))
            else {}
        )
        raw_items = items_ev.get("data", []) if items_ev else []
        if isinstance(raw_items, dict):
            items_data: list[dict[str, Any]] = [raw_items]
        elif isinstance(raw_items, list):
            items_data = [it for it in raw_items if isinstance(it, dict)]
        else:
            items_data = []

        shipment_data: dict[str, Any] = (
            shipment_ev.get("data", {})
            if (shipment_ev and isinstance(shipment_ev.get("data"), dict))
            else {}
        )

        # Collect entities
        seller_ids: set[str] = set()
        item_ids: set[str] = set()
        shipment_ids: set[str] = set()

        if order_data.get("seller_id"):
            seller_ids.add(str(order_data["seller_id"]))
        if shipment_data.get("seller_id"):
            seller_ids.add(str(shipment_data["seller_id"]))
        if order_data.get("order_item_id"):
            item_ids.add(str(order_data["order_item_id"]))
        if shipment_data.get("order_item_id"):
            item_ids.add(str(shipment_data["order_item_id"]))

        for item in items_data:
            if item.get("seller_id"):
                seller_ids.add(str(item["seller_id"]))
            if item.get("order_item_id"):
                item_ids.add(str(item["order_item_id"]))

        for limit in shipment_data.get("shipping_limits", []):
            if isinstance(limit, dict):
                if limit.get("seller_id"):
                    seller_ids.add(str(limit["seller_id"]))
                if limit.get("order_item_id"):
                    item_ids.add(str(limit["order_item_id"]))

        # Extract timestamps and status
        order_status = order_data.get("order_status") or shipment_data.get("order_status")

        # Carrier handover date (when seller gave package to carrier)
        carrier_date_str = (
            order_data.get("order_delivered_carrier_date")
            or shipment_data.get("delivered_carrier_at")
        )
        carrier_dt = parse_iso_datetime(carrier_date_str)

        # Customer delivery date (when customer received package)
        customer_date_str = (
            order_data.get("order_delivered_customer_date")
            or shipment_data.get("delivered_customer_at")
        )
        customer_dt = parse_iso_datetime(customer_date_str)

        # Estimated delivery date (target promise to customer)
        estimated_date_str = (
            order_data.get("order_estimated_delivery_date")
            or shipment_data.get("estimated_delivery_at")
        )
        estimated_dt = parse_iso_datetime(estimated_date_str)

        # Check for discrepancies between order and shipment summary
        if (
            order_data.get("order_delivered_customer_date")
            and shipment_data.get("delivered_customer_at")
            and order_data["order_delivered_customer_date"]
            != shipment_data["delivered_customer_at"]
        ):
            data_conflicts.append(
                {
                    "field": "delivered_customer_date",
                    "sources": ["get_order", "get_shipment_summary"],
                    "selected_source": "get_shipment_summary",
                    "resolution_code": "use_shipment_telemetry",
                }
            )

        events: list[dict[str, Any]] = shipment_data.get("events", [])

        # 4. Check Seller Delays (Timeline Reconciliation for Sellers)
        late_seller_ids: set[str] = set()
        item_limit_dates_present = False

        # Check each item's shipping limit
        for item in items_data:
            limit_str = item.get("shipping_limit_date")
            limit_dt = parse_iso_datetime(limit_str)
            if limit_dt:
                item_limit_dates_present = True
                if carrier_dt and carrier_dt > limit_dt:
                    sid = item.get("seller_id")
                    if sid:
                        late_seller_ids.add(str(sid))

        direct_limit = order_data.get("shipping_limit_date") or shipment_data.get("shipping_limit_date")
        if direct_limit:
            limit_dt = parse_iso_datetime(direct_limit)
            if limit_dt:
                item_limit_dates_present = True
                if carrier_dt and carrier_dt > limit_dt:
                    sid = order_data.get("seller_id") or shipment_data.get("seller_id")
                    if sid:
                        late_seller_ids.add(str(sid))

        # Also check shipping_limits from shipment_summary if any
        for limit in shipment_data.get("shipping_limits", []):
            limit_str = limit.get("shipping_limit_at")
            limit_dt = parse_iso_datetime(limit_str)
            if limit_dt:
                item_limit_dates_present = True
                if carrier_dt and carrier_dt > limit_dt:
                    sid = limit.get("seller_id")
                    if sid:
                        late_seller_ids.add(str(sid))

        # 5. Timeline Completeness Check
        if order_status == "delivered":
            timeline_complete = bool(
                carrier_dt and customer_dt and estimated_dt and item_limit_dates_present
            )
        elif order_status in ("shipped", "in_transit"):
            timeline_complete = bool(carrier_dt and estimated_dt and item_limit_dates_present)
        else:
            timeline_complete = bool(carrier_dt or estimated_dt)

        # 6. Fault Attribution & Verdict Engine
        verdict: str
        suggested_issue: str | None = None
        responsible_party: dict[str, str | None] | None = None

        has_lost_event = any(
            evt.get("event_type") in ("lost", "package_lost", "missing") for evt in events
        )
        has_returned_event = any(
            evt.get("event_type") in ("returned", "returned_to_sender", "undelivered")
            for evt in events
        )
        has_delivered_late_event = any(
            evt.get("event_type") == "delivered_late" and evt.get("status") == "confirmed"
            for evt in events
        )

        if has_lost_event or order_status in ("lost", "canceled_in_transit"):
            verdict = "lost"
            suggested_issue = "late_delivery_logistics"
            responsible_party = {"party_type": "logistics_provider", "party_id": None}
        elif has_returned_event or order_status == "returned":
            verdict = "returned"
            suggested_issue = None
            responsible_party = {"party_type": "logistics_provider", "party_id": None}
        elif not order_ev and not shipment_ev:
            verdict = "insufficient_evidence"
            suggested_issue = "insufficient_evidence"
        elif customer_dt and estimated_dt:
            is_customer_late = (customer_dt > estimated_dt) or has_delivered_late_event

            if is_customer_late:
                if late_seller_ids:
                    verdict = "seller_delay"
                    suggested_issue = "late_delivery_seller"
                    first_late_seller = sorted(late_seller_ids)[0]
                    responsible_party = {"party_type": "seller", "party_id": first_late_seller}
                else:
                    verdict = "logistics_delay"
                    suggested_issue = "late_delivery_logistics"
                    responsible_party = {"party_type": "logistics_provider", "party_id": None}
            else:
                verdict = "on_time"
                suggested_issue = None
                responsible_party = None
        elif has_delivered_late_event:
            if late_seller_ids:
                verdict = "seller_delay"
                suggested_issue = "late_delivery_seller"
                first_late_seller = sorted(late_seller_ids)[0]
                responsible_party = {"party_type": "seller", "party_id": first_late_seller}
            else:
                verdict = "logistics_delay"
                suggested_issue = "late_delivery_logistics"
                responsible_party = {"party_type": "logistics_provider", "party_id": None}
        else:
            verdict = "insufficient_evidence"
            suggested_issue = "insufficient_evidence"

        shipment_analysis = {
            "verdict": verdict,
            "late_seller_ids": sorted(list(late_seller_ids)),
            "timeline_complete": timeline_complete,
        }

        affected_entities = {
            "seller_ids": sorted(list(seller_ids)),
            "item_ids": sorted(list(item_ids)),
            "shipment_ids": sorted(list(shipment_ids)),
        }

        return LogisticsResult(
            shipment_analysis=shipment_analysis,
            affected_entities=affected_entities,
            evidence_refs=sorted(list(set(evidence_refs))),
            suggested_primary_issue=suggested_issue,
            responsible_party=responsible_party,
            data_conflicts=data_conflicts,
        )


async def run_logistics_worker(
    case_id: str, order_id: str, gateway: EvidenceGateway, trace: TraceWriter | None = None
) -> LogisticsResult:
    """Convenience functional interface for Supervisor / Router."""
    worker = LogisticsWorker(gateway, trace)
    return await worker.investigate(case_id, order_id)
