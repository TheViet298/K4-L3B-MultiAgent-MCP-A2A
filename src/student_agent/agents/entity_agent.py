from __future__ import annotations

import logging
import re
from typing import Any

from ..cache import CaseCache
from ..trace import TraceWriter

logger = logging.getLogger(__name__)

HEX_ORDER_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


class EntityAgent:
    """Specialist agent for resolving entity candidates and customer context."""

    def __init__(self, cache: CaseCache, trace: TraceWriter) -> None:
        self.cache = cache
        self.trace = trace

    async def resolve(self, case: dict[str, Any]) -> dict[str, Any]:
        case_id = case["case_id"]
        customer_request = case.get("customer_request", {})
        claimed_order_id = customer_request.get("claimed_order_id")
        candidate_ids = case.get("candidate_order_ids", [])
        customer_hint = case.get("customer_unique_id_hint")

        consumed_evidence: list[str] = []
        resolved_order_ids: list[str] = []
        rejected_candidates: list[str] = []

        # Separate real hex candidate IDs from dummy candidates like "candidate-001"
        real_candidates: list[str] = []
        for cand in candidate_ids:
            if HEX_ORDER_PATTERN.match(str(cand)):
                real_candidates.append(str(cand))
            else:
                rejected_candidates.append(str(cand))

        # Determine target candidate to investigate (prefer claimed_order_id if valid hex)
        target_oid: str | None = None
        if claimed_order_id and HEX_ORDER_PATTERN.match(claimed_order_id):
            target_oid = claimed_order_id
        elif real_candidates:
            target_oid = real_candidates[0]

        valid_orders_data: dict[str, dict[str, Any]] = {}
        items_data: list[dict[str, Any]] = []

        if target_oid:
            # 1. Fetch order details
            order_ev = await self.cache.call_safe("get_order", order_id=target_oid)
            if order_ev:
                ref = order_ev.get("evidence_ref")
                if ref:
                    consumed_evidence.append(ref)
                valid_orders_data[target_oid] = order_ev.get("data", {})
                resolved_order_ids.append(target_oid)
            else:
                # If target_oid failed, check other real candidates
                for other_cand in real_candidates:
                    if other_cand != target_oid:
                        o_ev = await self.cache.call_safe("get_order", order_id=other_cand)
                        if o_ev:
                            ref = o_ev.get("evidence_ref")
                            if ref:
                                consumed_evidence.append(ref)
                            valid_orders_data[other_cand] = o_ev.get("data", {})
                            resolved_order_ids.append(other_cand)
                            rejected_candidates.append(target_oid)
                            target_oid = other_cand
                            break
                        else:
                            rejected_candidates.append(other_cand)

            # 2. Fetch order items to extract item_ids, seller_ids, shipping_limit_date
            if target_oid and target_oid in valid_orders_data:
                items_ev = await self.cache.call_safe("get_order_items", order_id=target_oid)
                if items_ev:
                    ref = items_ev.get("evidence_ref")
                    if ref:
                        consumed_evidence.append(ref)
                    raw_items = items_ev.get("data", [])
                    if isinstance(raw_items, list):
                        items_data = raw_items
                    elif isinstance(raw_items, dict):
                        items_data = [raw_items]

        # Finalize rejected candidates
        for c in candidate_ids:
            if c not in resolved_order_ids and c not in rejected_candidates:
                rejected_candidates.append(c)

        resolved_order_ids = sorted(list(set(resolved_order_ids)))
        rejected_candidates = sorted(list(set(rejected_candidates) - set(resolved_order_ids)))

        status = "resolved" if len(resolved_order_ids) == 1 else ("ambiguous" if len(resolved_order_ids) > 1 else "not_found")
        confidence = 0.95 if status == "resolved" else (0.60 if status == "ambiguous" else 0.85)

        # 3. Customer context
        related_order_ids = list(resolved_order_ids)
        customer_unique_id = customer_hint
        if customer_hint:
            cust_ev = await self.cache.call_safe("get_customer_history", customer_unique_id=customer_hint)
            if cust_ev:
                ref = cust_ev.get("evidence_ref")
                if ref:
                    consumed_evidence.append(ref)
                cdata = cust_ev.get("data", {})
                if isinstance(cdata, dict):
                    customer_unique_id = cdata.get("customer_unique_id", customer_hint)
                    c_orders = cdata.get("orders") or cdata.get("order_ids") or []
                    for oid in c_orders:
                        if isinstance(oid, str) and oid not in related_order_ids:
                            related_order_ids.append(oid)

        # 4. Extract Affected Entities
        item_ids: set[str] = set()
        seller_ids: set[str] = set()
        for item in items_data:
            if item.get("order_item_id"):
                item_ids.add(str(item["order_item_id"]))
            elif item.get("item_id"):
                item_ids.add(str(item["item_id"]))
            if item.get("seller_id"):
                seller_ids.add(str(item["seller_id"]))

        # Check shipment_id / payment references from order data
        shipment_ids: set[str] = set()
        payment_references: set[str] = set()
        for oid in resolved_order_ids:
            odata = valid_orders_data.get(oid, {})
            if odata.get("shipment_id"):
                shipment_ids.add(str(odata["shipment_id"]))
            if odata.get("customer_id"):
                shipment_ids.add(str(oid))  # carrier tracking reference

        affected_entities = {
            "order_ids": resolved_order_ids,
            "item_ids": sorted(list(item_ids)),
            "seller_ids": sorted(list(seller_ids)),
            "payment_references": sorted(list(payment_references)),
            "shipment_ids": sorted(list(shipment_ids)),
        }

        if consumed_evidence:
            self.trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="entity-agent",
                evidence_refs=sorted(list(set(consumed_evidence))),
            )

        return {
            "entity_resolution": {
                "status": status,
                "resolved_order_ids": resolved_order_ids,
                "rejected_candidates": rejected_candidates,
                "confidence": confidence,
            },
            "customer_context": {
                "customer_unique_id": customer_unique_id,
                "related_order_ids": sorted(list(set(related_order_ids))),
            },
            "affected_entities": affected_entities,
            "orders_data": valid_orders_data,
            "items_data": items_data,
        }
