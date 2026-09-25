from __future__ import annotations

import logging
from typing import Any

from ..cache import CaseCache
from ..trace import TraceWriter

logger = logging.getLogger(__name__)


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

        # Find available order lookup tool
        order_tool = self.cache.find_matching_tool("order")
        customer_tool = self.cache.find_matching_tool("customer")

        # 1. Investigate candidates
        valid_orders_data: dict[str, dict[str, Any]] = {}
        for candidate in candidate_ids:
            if order_tool:
                evidence = await self.cache.call_safe(order_tool, order_id=candidate)
                if evidence:
                    ref = evidence.get("evidence_ref")
                    if ref:
                        consumed_evidence.append(ref)
                    valid_orders_data[candidate] = evidence.get("data", {})
                else:
                    rejected_candidates.append(candidate)
            else:
                # Fallback if no specific tool discovered: match with claimed_order_id
                if claimed_order_id and candidate == claimed_order_id:
                    resolved_order_ids.append(candidate)
                else:
                    rejected_candidates.append(candidate)

        if valid_orders_data:
            # If we got order evidence, prioritize claimed_order_id if valid
            if claimed_order_id and claimed_order_id in valid_orders_data:
                resolved_order_ids.append(claimed_order_id)
                for c in candidate_ids:
                    if c != claimed_order_id and c not in rejected_candidates:
                        rejected_candidates.append(c)
            else:
                # Select the valid candidate(s)
                for cand in valid_orders_data:
                    resolved_order_ids.append(cand)

        # Remove duplicates
        resolved_order_ids = sorted(list(set(resolved_order_ids)))
        rejected_candidates = sorted(list(set(rejected_candidates) - set(resolved_order_ids)))

        # Determine status and confidence
        if len(resolved_order_ids) == 1:
            status = "resolved"
            confidence = 0.95
        elif len(resolved_order_ids) > 1:
            status = "ambiguous"
            confidence = 0.60
        else:
            status = "not_found"
            confidence = 0.85

        # 2. Customer context
        related_order_ids = list(resolved_order_ids)
        customer_unique_id = customer_hint
        if customer_hint and customer_tool:
            cust_evidence = await self.cache.call_safe(
                customer_tool, customer_unique_id=customer_hint
            )
            if cust_evidence:
                ref = cust_evidence.get("evidence_ref")
                if ref:
                    consumed_evidence.append(ref)
                data = cust_evidence.get("data", {})
                if isinstance(data, dict):
                    customer_unique_id = data.get("customer_unique_id", customer_hint)
                    orders = data.get("orders") or data.get("order_ids") or []
                    for oid in orders:
                        if isinstance(oid, str) and oid not in related_order_ids:
                            related_order_ids.append(oid)

        # 3. Affected entities compilation
        affected_entities = {
            "order_ids": sorted(list(set(resolved_order_ids))),
            "item_ids": [],
            "seller_ids": [],
            "payment_references": [],
            "shipment_ids": [],
        }

        # Extract items, sellers, payments, shipments from order data if available
        for oid in resolved_order_ids:
            order_data = valid_orders_data.get(oid, {})
            items = order_data.get("items", [])
            for item in items:
                if isinstance(item, dict):
                    if "item_id" in item:
                        affected_entities["item_ids"].append(str(item["item_id"]))
                    elif "order_item_id" in item:
                        affected_entities["item_ids"].append(str(item["order_item_id"]))
                    if "seller_id" in item:
                        affected_entities["seller_ids"].append(str(item["seller_id"]))
            if "shipment_id" in order_data:
                affected_entities["shipment_ids"].append(str(order_data["shipment_id"]))
            if "payment_id" in order_data:
                affected_entities["payment_references"].append(str(order_data["payment_id"]))

        for key in affected_entities:
            affected_entities[key] = sorted(list(set(affected_entities[key])))

        # Trace tool result consumed
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
        }
