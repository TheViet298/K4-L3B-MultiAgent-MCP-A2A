from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .agents.conflict_agent import ConflictResolver
from .agents.entity_agent import EntityAgent
from .agents.specialists import DomainSpecialists
from .agents.verifier import VerifierAgent
from .cache import CaseCache
from .financial_worker import FinancialWorker
from .logistics_worker import LogisticsWorker
from .mcp_gateway import EvidenceGateway
from .policy_worker import PolicyWorker
from .trace import TraceWriter

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """LangGraph state schema for multi-agent dispute resolution workflow."""

    # Context & Session
    case_id: str
    case: dict[str, Any]
    gateway: EvidenceGateway
    trace: TraceWriter
    cache: CaseCache

    # Agent outputs
    entity_result: dict[str, Any]
    shipment_analysis: dict[str, Any]
    logistics_result: dict[str, Any]
    payment_analysis: dict[str, Any]
    financial_result: dict[str, Any]
    claim_assessments: list[dict[str, Any]]
    data_conflicts: list[dict[str, Any]]
    policy_result: dict[str, Any]
    raw_output: dict[str, Any]
    final_output: dict[str, Any]


async def router_node(state: AgentState) -> dict[str, Any]:
    """Supervisor/Router: Discover tools, initialize cache, and delegate to Entity Agent."""
    case_id = state["case_id"]
    gateway = state["gateway"]
    trace = state["trace"]

    available_tools = await gateway.list_tools()
    cache = CaseCache(gateway, case_id, available_tools)

    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="entity-agent",
        attributes={"task": "resolve_entities_and_customer_context"},
    )
    return {"cache": cache}


async def entity_node(state: AgentState) -> dict[str, Any]:
    """Entity Agent: Resolve claimed/candidate order IDs and customer context."""
    case = state["case"]
    case_id = state["case_id"]
    cache = state["cache"]
    trace = state["trace"]

    entity_agent = EntityAgent(cache, trace)
    entity_res = await entity_agent.resolve(case)

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="entity-agent",
        target="specialists",
        attributes={
            "resolved_orders_count": len(
                entity_res["entity_resolution"]["resolved_order_ids"]
            )
        },
    )
    return {"entity_result": entity_res}


async def shipment_specialist_node(state: AgentState) -> dict[str, Any]:
    """Logistics Specialist Worker (Nguyễn Văn Giáp): Analyze shipment timeline and fault attribution."""
    case = state["case"]
    case_id = state["case_id"]
    cache = state["cache"]
    trace = state["trace"]
    entity_res = state["entity_result"]

    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="logistics_worker",
        attributes={"task": "analyze_shipment_timeline"},
    )

    resolved_orders = entity_res["entity_resolution"]["resolved_order_ids"]
    target_order_id = (
        resolved_orders[0]
        if resolved_orders
        else case.get("customer_request", {}).get("claimed_order_id", "")
    )

    worker = LogisticsWorker(cache, trace)
    logistics_res = await worker.investigate(case_id, target_order_id or "")
    shipment_analysis = dict(logistics_res.shipment_analysis)

    # In case orders_data has additional fallback info
    if not shipment_analysis.get("late_seller_ids"):
        specialists = DomainSpecialists(cache, trace)
        spec_shipment = await specialists.analyze_shipment(
            case,
            entity_res["entity_resolution"]["resolved_order_ids"],
            entity_res.get("orders_data", {}),
        )
        if spec_shipment.get("late_seller_ids"):
            shipment_analysis["late_seller_ids"] = spec_shipment["late_seller_ids"]
        if shipment_analysis.get("verdict") in ("insufficient_evidence", "on_time") and spec_shipment.get("verdict") not in ("insufficient_evidence", "on_time"):
            shipment_analysis["verdict"] = spec_shipment["verdict"]

    return {
        "shipment_analysis": shipment_analysis,
        "logistics_result": logistics_res.to_dict(),
    }


async def payment_specialist_node(state: AgentState) -> dict[str, Any]:
    """Financial Specialist Worker (Cao Đức Hiệp): Reconcile payments, refunded amounts, and refundable totals."""
    case = state["case"]
    case_id = state["case_id"]
    cache = state["cache"]
    trace = state["trace"]
    entity_res = state["entity_result"]

    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="financial_worker",
        attributes={"task": "reconcile_payments_and_refunds"},
    )

    resolved_orders = entity_res["entity_resolution"]["resolved_order_ids"]
    target_order_id = (
        resolved_orders[0]
        if resolved_orders
        else case.get("customer_request", {}).get("claimed_order_id")
    )
    claims = case.get("customer_request", {}).get("claims")

    worker = FinancialWorker(cache, trace)
    financial_res = await worker.investigate(case_id, target_order_id, claims)
    payment_analysis = dict(financial_res.payment_analysis)

    # Fallback if no payment data was returned from worker but orders_data had info
    if payment_analysis.get("verdict") == "insufficient_evidence":
        specialists = DomainSpecialists(cache, trace)
        spec_payment = await specialists.analyze_payment(
            case,
            entity_res["entity_resolution"]["resolved_order_ids"],
            entity_res.get("orders_data", {}),
        )
        if spec_payment.get("verdict") != "insufficient_evidence":
            payment_analysis = spec_payment

    return {
        "payment_analysis": payment_analysis,
        "financial_result": financial_res.to_dict(),
    }


async def claim_assessor_node(state: AgentState) -> dict[str, Any]:
    """Specialists Synthesis / Assessor: Synthesize shipment & payment results to assess claims."""
    case = state["case"]
    case_id = state["case_id"]
    cache = state["cache"]
    trace = state["trace"]
    shipment_res = state["shipment_analysis"]
    payment_res = state["payment_analysis"]

    specialists = DomainSpecialists(cache, trace)
    claim_assessments = specialists.assess_claims(
        case, shipment_res, payment_res, cache.evidence_refs
    )

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="specialists",
        target="conflict-resolver",
    )
    return {"claim_assessments": claim_assessments}


async def conflict_resolver_node(state: AgentState) -> dict[str, Any]:
    """Conflict Resolver: Detect discrepancies across sources and apply source precedence rules."""
    case = state["case"]
    case_id = state["case_id"]
    cache = state["cache"]
    trace = state["trace"]
    shipment_res = state["shipment_analysis"]
    payment_res = state["payment_analysis"]
    logistics_res = state.get("logistics_result", {})
    financial_res = state.get("financial_result", {})

    conflict_agent = ConflictResolver(cache, trace)
    conflicts = conflict_agent.detect_and_resolve(case, shipment_res, payment_res)

    # Add conflicts detected by logistics and financial workers
    if logistics_res and "data_conflicts" in logistics_res:
        for c in logistics_res["data_conflicts"]:
            if c not in conflicts:
                conflicts.append(c)

    if financial_res and "data_conflicts" in financial_res:
        for c in financial_res["data_conflicts"]:
            if c not in conflicts:
                conflicts.append(c)

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="conflict-resolver",
        target="policy-agent",
    )
    return {"data_conflicts": conflicts[:5]}


async def policy_node(state: AgentState) -> dict[str, Any]:
    """Policy & Root Cause Worker (Nguyễn Quang Đạo): Apply policy rules and formulate resolutions."""
    case = state["case"]
    case_id = state["case_id"]
    cache = state["cache"]
    trace = state["trace"]
    entity_res = state["entity_result"]
    shipment_res = state["shipment_analysis"]
    payment_res = state["payment_analysis"]
    conflicts = state["data_conflicts"]
    claim_assessments = state["claim_assessments"]
    logistics_res = state.get("logistics_result", {})
    financial_res = state.get("financial_result", {})

    worker = PolicyWorker(cache, trace)
    policy_res = await worker.evaluate(case, entity_res, shipment_res, payment_res, conflicts)

    # Consolidate affected entities across all workers
    affected_entities = dict(entity_res.get("affected_entities", {}))
    if logistics_res and "affected_entities" in logistics_res:
        log_entities = logistics_res["affected_entities"]
        for key in ("seller_ids", "item_ids", "shipment_ids"):
            combined = set(affected_entities.get(key, [])) | set(log_entities.get(key, []))
            affected_entities[key] = sorted(list(combined))

    if financial_res and "payment_references" in financial_res:
        combined = set(affected_entities.get("payment_references", [])) | set(
            financial_res["payment_references"]
        )
        affected_entities["payment_references"] = sorted(list(combined))

    raw_output: dict[str, Any] = {
        "schema_version": "day09-l3b-output-v2",
        "case_id": case_id,
        "assessment": policy_res.assessment,
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
        "root_cause_analysis": policy_res.root_cause_analysis,
        "evidence_refs": sorted(list(set(cache.evidence_refs))),
        "data_conflicts": conflicts,
        "financial_resolution": policy_res.financial_resolution,
        "resolution_actions": policy_res.resolution_actions,
    }

    return {"policy_result": policy_res.to_dict(), "raw_output": raw_output}


async def verifier_node(state: AgentState) -> dict[str, Any]:
    """Verifier Agent (Cao Đức Hiệp): Validate invariants, calibrate confidence, auto-repair, and check schema."""
    case = state["case"]
    gateway = state["gateway"]
    trace = state["trace"]
    raw_output = state["raw_output"]

    verifier = VerifierAgent(gateway._contracts, trace)
    final_output = verifier.verify_and_repair(raw_output, case)
    return {"final_output": final_output}


def build_multi_agent_graph() -> StateGraph:
    """Build the LangGraph state machine orchestrating the multi-agent system."""
    builder = StateGraph(AgentState)

    # Register agent nodes
    builder.add_node("router", router_node)
    builder.add_node("entity_agent", entity_node)
    builder.add_node("shipment_specialist", shipment_specialist_node)
    builder.add_node("payment_specialist", payment_specialist_node)
    builder.add_node("claim_assessor", claim_assessor_node)
    builder.add_node("conflict_resolver", conflict_resolver_node)
    builder.add_node("policy_agent", policy_node)
    builder.add_node("verifier", verifier_node)

    # Define orchestration flow (sequential & parallel fan-out/fan-in)
    builder.add_edge(START, "router")
    builder.add_edge("router", "entity_agent")

    # Parallel fan-out: Domain Specialists
    builder.add_edge("entity_agent", "shipment_specialist")
    builder.add_edge("entity_agent", "payment_specialist")

    # Fan-in: Specialists Synthesis
    builder.add_edge("shipment_specialist", "claim_assessor")
    builder.add_edge("payment_specialist", "claim_assessor")

    # Downstream pipeline
    builder.add_edge("claim_assessor", "conflict_resolver")
    builder.add_edge("conflict_resolver", "policy_agent")
    builder.add_edge("policy_agent", "verifier")
    builder.add_edge("verifier", END)

    return builder


dispute_resolution_graph = build_multi_agent_graph().compile()


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Execute the multi-agent LangGraph workflow for investigating e-commerce disputes."""
    initial_state: AgentState = {
        "case_id": case["case_id"],
        "case": case,
        "gateway": gateway,
        "trace": trace,
    }
    result = await dispute_resolution_graph.ainvoke(initial_state)
    return result["final_output"]
