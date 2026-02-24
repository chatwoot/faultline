"""Error monitoring sub-agent specializing in Sentry data."""

from __future__ import annotations

from typing import Any

from .base import BaseSubAgent, SubAgentConfig
from ..prompts import GUIDANCE_SENTRY


class ErrorMonitoringAgent(BaseSubAgent):
    agent_id = "error_monitoring"  # type: ignore[assignment]
    agent_type = "Error Monitoring Agent"
    shared_integrations = ["github"]

    def __init__(self, config: SubAgentConfig) -> None:
        super().__init__(config)

    def get_required_tool_patterns(self, task: str) -> list[str]:
        # Error monitoring agent must always search for issues
        return ["sentry"]

    def get_excluded_tool_patterns(self, task: str) -> list[str]:
        excluded = []
        ctx = self.config.investigation_context
        has_sentry_projects = any(r.type == "sentry_project" for r in ctx.resources)
        if has_sentry_projects:
            excluded.extend(["find_organization", "find_project", "list_project"])
        return excluded

    def get_own_tools(self) -> list[dict[str, Any]]:
        return self.get_mcp_tools("sentry")

    def get_system_prompt(self) -> str:
        ctx = self.config.investigation_context
        has_sentry_projects = any(r.type == "sentry_project" for r in ctx.resources)
        discovery_rule = (
            "1. Use the Sentry project slugs from the investigation context — do NOT call find_organization or find_project."
            if has_sentry_projects
            else "1. Discover project slugs first — they may differ from service names in other platforms."
        )

        return f"""You are an error monitoring investigation agent specializing in Sentry data. Your job is to query Sentry for error tracking, stack traces, issue frequency, and affected users — always scoped to the investigation time window.

# Core Rules
{discovery_rule}
2. **Always filter by time window.** Compare each issue's `firstSeen`/`lastSeen` against the investigation time window. Skip issues whose `lastSeen` is before the window.
3. **Never report total/lifetime event counts.** They are misleading. Instead classify each issue as NEW (firstSeen within window) or RECURRING (active during window).
4. Always retrieve full stack traces for the most relevant errors within the time window.
5. Report specific error messages, file:line references, and affected user counts.
6. If a tool call fails, fix parameters and retry immediately.

# Investigation Depth
- Get the list of issues, then FILTER by time window before analyzing further.
- For each relevant issue, get the full event details including stack traces.
- Look for error clusters: multiple different errors that started around the same time point to a shared root cause (bad deploy, DB failure, config change).
- Check if errors correlate with specific releases or deployments.
- Identify the FIRST error that appeared in the window — this is often the root cause, with subsequent errors being cascading failures.

# Focus Areas
- Errors active during the investigation time window (not all-time)
- Full stack traces with file:line references for top issues
- Error timeline: which errors appeared first, which followed
- Error clusters sharing a common trigger
- Affected user counts and sessions
- Release/deployment correlation

{GUIDANCE_SENTRY}

Produce a concise findings summary with: (1) errors filtered to the time window, (2) timeline of when each error first appeared, (3) stack traces for the top issues, (4) likely root cause connecting the error cluster."""
