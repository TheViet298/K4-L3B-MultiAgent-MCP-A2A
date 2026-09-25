from __future__ import annotations

import logging
from typing import Any

from .agents.conflict_agent import ConflictResolver
from .agents.entity_agent import EntityAgent
from .agents.policy_agent import PolicyAgent
from .agents.specialists import DomainSpecialists
from .agents.verifier import VerifierAgent
from .cache import CaseCache
from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

logger = logging.getLogger(__name__)


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Execute the multi-agent workflow for investigating e-commerce disputes."""
    case_id = case["case_id"]

    # 1. Tool discovery and case cache
    available_tools = await gateway.list_tools()
    cache = CaseCache(gateway, case_id, available_tools)

    # 2. Entity & Customer Agent
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="entity-agent",
        attributes={"task": "resolve_entities_and_customer_context"},
    )
    entity_agent = EntityAgent(cache, trace)
    entity_res = await entity_agent.resolve(case)

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="entity-agent",
        target="specialists",
        attributes={"resolved_orders_count": len(entity_res["entity_resolution"]["resolved_order_ids"])},
    )

    # 3. Domain Specialists (Shipment and Payment)
    specialists = DomainSpecialists(cache, trace)

    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="shipment-agent",
        attributes={"task": "analyze_shipment_timeline"},
    )
    shipment_res = await specialists.analyze_shipment(
        case,
        entity_res["entity_resolution"]["resolved_order_ids"],
        entity_res.get("orders_data", {}),
        entity_res.get("items_data", []),
    )

    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="payment-agent",
        attributes={"task": "reconcile_payments_and_refunds"},
    )
    payment_res = await specialists.analyze_payment(
        case,
        entity_res["entity_resolution"]["resolved_order_ids"],
        entity_res.get("orders_data", {}),
    )

    # Assess claims
    claim_assessments = specialists.assess_claims(
        case, shipment_res, payment_res, cache.evidence_refs
    )

    # 4. Conflict Resolver
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="specialists",
        target="conflict-resolver",
    )
    conflict_agent = ConflictResolver(cache, trace)
    conflicts = conflict_agent.detect_and_resolve(case, shipment_res, payment_res)

    # 5. Policy & Root Cause Agent
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="conflict-resolver",
        target="policy-agent",
    )
    policy_agent = PolicyAgent(cache, trace)
    policy_res = await policy_agent.evaluate(case, entity_res, shipment_res, payment_res, conflicts)

    # Merge affected entities
    affected_entities = dict(entity_res.get("affected_entities", {}))
    if payment_res.get("payment_references"):
        affected_entities["payment_references"] = sorted(
            list(set(affected_entities.get("payment_references", []) + payment_res["payment_references"]))
        )

    # 6. Assemble unverified output
    raw_output: dict[str, Any] = {
        "schema_version": "day09-l3b-output-v2",
        "case_id": case_id,
        "assessment": policy_res["assessment"],
        "affected_entities": affected_entities,
        "claim_assessments": claim_assessments,
        "entity_resolution": entity_res["entity_resolution"],
        "customer_context": entity_res["customer_context"],
        "shipment_analysis": {
            "verdict": shipment_res["verdict"],
            "late_seller_ids": shipment_res["late_seller_ids"],
            "timeline_complete": shipment_res["timeline_complete"],
        },
        "payment_analysis": {
            "verdict": payment_res["verdict"],
            "captured_total_brl": payment_res["captured_total_brl"],
            "refunded_total_brl": payment_res["refunded_total_brl"],
            "refundable_total_brl": payment_res["refundable_total_brl"],
        },
        "root_cause_analysis": policy_res["root_cause_analysis"],
        "evidence_refs": sorted(list(set(cache.evidence_refs))),
        "data_conflicts": conflicts,
        "financial_resolution": policy_res["financial_resolution"],
        "resolution_actions": policy_res["resolution_actions"],
    }

    # 7. Verification & Guardrail Gate (Hiệp's QA & Invariant Gate)
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="policy-agent",
        target="verifier",
    )
    verifier = VerifierAgent(gateway._contracts, trace)
    final_output = verifier.verify_and_repair(raw_output, case)

    return final_output
