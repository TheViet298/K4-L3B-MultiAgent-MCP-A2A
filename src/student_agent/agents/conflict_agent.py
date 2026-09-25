from __future__ import annotations

import logging
from typing import Any

from ..cache import CaseCache
from ..trace import TraceWriter

logger = logging.getLogger(__name__)


class ConflictResolver:
    """Agent for detecting discrepancies across data sources and resolving via policy."""

    def __init__(self, cache: CaseCache, trace: TraceWriter) -> None:
        self.cache = cache
        self.trace = trace

    def detect_and_resolve(
        self,
        case: dict[str, Any],
        shipment_analysis: dict[str, Any],
        payment_analysis: dict[str, Any],
    ) -> list[dict[str, Any]]:
        conflicts: list[dict[str, Any]] = []
        customer_request = case.get("customer_request", {})
        claims = customer_request.get("claims", [])
        claim_topics = [c.get("topic", "") for c in claims]

        # 1. Delivery discrepancy: Customer claiming delay vs Carrier tracking on time
        if "late_delivery_logistics" in claim_topics or "late_delivery_seller" in claim_topics:
            if shipment_analysis.get("verdict") == "on_time":
                conflicts.append(
                    {
                        "field": "delivery_status",
                        "sources": ["customer_statement", "carrier_tracking"],
                        "selected_source": "carrier_tracking",
                        "resolution_code": "TRUST_OFFICIAL_CARRIER_TIMESTAMP",
                    }
                )

        # 2. Payment discrepancy: Customer claiming payment error vs gateway reconciled
        if "duplicate_charge" in claim_topics or "payment_mismatch" in claim_topics:
            if payment_analysis.get("verdict") == "reconciled":
                conflicts.append(
                    {
                        "field": "payment_reconciliation",
                        "sources": ["customer_statement", "payment_gateway"],
                        "selected_source": "payment_gateway",
                        "resolution_code": "AUDITED_PAYMENT_LEDGER_CONFIRMED",
                    }
                )

        return conflicts[:5]
