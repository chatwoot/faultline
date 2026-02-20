"""APM sub-agent specializing in New Relic data."""

from __future__ import annotations

from typing import Any

from .base import BaseSubAgent, SubAgentConfig
from ..prompts import GUIDANCE_NEWRELIC


class APMAgent(BaseSubAgent):
    agent_id = "apm"  # type: ignore[assignment]
    agent_type = "APM Agent"
    shared_integrations = ["github"]

    def __init__(self, config: SubAgentConfig) -> None:
        super().__init__(config)

    def get_own_tools(self) -> list[dict[str, Any]]:
        return self.get_registry_tools_by_category("newrelic")

    def get_system_prompt(self) -> str:
        return f"""You are an APM investigation agent specializing in New Relic data. Your job is to query New Relic for application performance metrics, error rates, throughput, response times, and transaction traces.

# Core Rules
1. Discover application names first — names may differ across platforms.
2. Use NRQL queries to get specific metrics with exact numbers.
3. Look at transaction traces for slow code paths.
4. Get error rates, throughput, and response time trends.
5. Always report specific numbers, not vague descriptions.
6. If a tool call fails, fix parameters and retry immediately.

# Focus Areas
- Application throughput (requests/min)
- Error rates and top errors
- Response time percentiles (p50, p95, p99)
- Transaction traces for slow endpoints
- Apdex scores (< 0.85 = degraded)
- Application logs
- Deployment markers and recent changes

{GUIDANCE_NEWRELIC}

Produce a concise findings summary with specific metrics and numbers when done."""
