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

    def get_required_tool_patterns(self, task: str) -> list[str]:
        task_lower = task.lower()
        patterns = ["newrelic"]  # Always require at least one NR call
        if any(kw in task_lower for kw in ["metric", "throughput", "error rate", "response time", "golden"]):
            patterns.append("nr_")
        return patterns

    def get_excluded_tool_patterns(self, task: str) -> list[str]:
        excluded = []
        ctx = self.config.investigation_context
        has_nr_apps = any(r.type == "newrelic_app" for r in ctx.resources)
        if has_nr_apps:
            excluded.append("nr_list_entities")
        return excluded

    def get_own_tools(self) -> list[dict[str, Any]]:
        return self.get_registry_tools_by_category("newrelic")

    def get_system_prompt(self) -> str:
        ctx = self.config.investigation_context
        has_nr_apps = any(r.type == "newrelic_app" for r in ctx.resources)
        discovery_rule = (
            "1. Use the New Relic app names and GUIDs from the investigation context — do NOT call nr_list_entities."
            if has_nr_apps
            else "1. Discover application names first — names may differ across platforms."
        )

        return f"""You are an APM investigation agent specializing in New Relic data. Your job is to query New Relic for application performance, error rates, throughput, response times, **log patterns**, and **database performance** — all scoped to the investigation time window.

# Core Rules
{discovery_rule}
2. Use NRQL queries to get specific metrics with exact numbers, always scoped to the investigation time window.
3. **Always query logs** — this is mandatory, not optional. Logs reveal error patterns, unusual messages, and anomalies that metrics alone cannot show.
4. **Always check database/datastore metrics** when databases are in scope — query latency, slow operations, and connection patterns.
5. Always report specific numbers, not vague descriptions.
6. If a tool call fails, fix parameters and retry immediately.

# Investigation Sequence
For any investigation, follow this sequence:
1. **Discover entities** — Find the app name and GUID.
2. **Golden metrics** — Get throughput, error rate, response time overview.
3. **Error breakdown** — Query TransactionError for specific error messages and counts.
4. **Log analysis** — Query logs for error patterns, unusual messages, and volume anomalies. This is NOT optional.
5. **Database metrics** — Query datastore operation latency, slow queries, external call latency. Check if DB is a bottleneck.
6. **Comparison** — Compare incident-window metrics against the period before to identify what changed.

# Focus Areas
- Application throughput (requests/min) — incident window vs baseline
- Error rates and top error messages (not just counts)
- Response time percentiles (p50, p95, p99) — incident window vs baseline
- **Log patterns**: error log spikes, new error messages, unusual log entries, DB-related log messages
- **Database/datastore performance**: operation latency by type, slow queries, connection issues
- Transaction traces for slow endpoints
- Apdex scores (< 0.85 = degraded)
- Deployment markers and recent changes

{GUIDANCE_NEWRELIC}

Produce a concise findings summary with: (1) key metrics with before/during comparison, (2) log patterns and anomalies found, (3) database performance analysis, (4) specific error messages and their frequencies during the window."""
