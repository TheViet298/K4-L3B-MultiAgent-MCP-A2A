from __future__ import annotations

import logging
from typing import Any

from .mcp_gateway import EvidenceGateway

logger = logging.getLogger(__name__)


class CaseCache:
    """Manages MCP tool discovery, caching per case, and evidence tracking."""

    def __init__(self, gateway: EvidenceGateway, case_id: str, available_tools: list[str]) -> None:
        self.gateway = gateway
        self.case_id = case_id
        self.available_tools = set(available_tools)
        self._cache: dict[tuple[str, tuple[tuple[str, Any], ...]], dict[str, Any]] = {}
        self.evidence_refs: list[str] = []
        self.domain_evidence: dict[str, list[dict[str, Any]]] = {}

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self.available_tools

    def find_matching_tool(self, *keywords: str) -> str | None:
        """Find a tool whose name contains all the specified keywords."""
        for tool in sorted(self.available_tools):
            if all(kw.lower() in tool.lower() for kw in keywords):
                return tool
        return None

    async def call_safe(self, tool_name: str, **kwargs: Any) -> dict[str, Any] | None:
        """Call a tool safely if it exists, using case cache."""
        if not self.has_tool(tool_name):
            return None

        # Sort kwargs for consistent cache key
        cache_key = (tool_name, tuple(sorted((k, str(v)) for k, v in kwargs.items())))
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            evidence = await self.gateway.call(tool_name, case_id=self.case_id, **kwargs)
            self._cache[cache_key] = evidence
            ref = evidence.get("evidence_ref")
            if ref and ref not in self.evidence_refs:
                self.evidence_refs.append(ref)

            domain = evidence.get("domain", "unknown")
            self.domain_evidence.setdefault(domain, []).append(evidence)
            return evidence
        except Exception as exc:
            logger.warning("Failed to call tool %s with %s: %s", tool_name, kwargs, exc)
            return None
