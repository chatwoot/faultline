"""Base sub-agent with tool execution loop."""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from openai import AsyncOpenAI

from ..integrations.mcp.client_manager import MCPClientManager
from ..integrations.tool_registry import tool_registry
from ..integrations.tool_context import ToolContext
from ..investigation_context import InvestigationContext, CRITICAL_PATTERNS

logger = logging.getLogger(__name__)

SubAgentType = Literal["apm", "error_monitoring", "infrastructure", "alerting"]

MAX_SUB_AGENT_ITERATIONS = 5
MAX_TOOL_RESULT_CHARS = 30_000

# Valid GroupBy Groups for Performance Insights get-resource-metrics
PI_VALID_GROUPBY_GROUPS = {
    "db.sql", "db.sql_tokenized", "db.host", "db.application",
    "db.session_type", "db.user",
}
# These are valid as Metric names, NOT as GroupBy Groups
PI_METRIC_ONLY_GROUPS = {"db.wait_event", "db.wait_state"}

# Patterns for secrets that should be redacted from tool output
_SECRET_PATTERNS = [
    # API keys and tokens with common prefixes
    re.compile(r'(sk[-_][a-zA-Z0-9_-]{20,})'),                      # OpenAI, Stripe, etc.
    re.compile(r'(ghp_[a-zA-Z0-9]{20,})'),                           # GitHub PAT
    re.compile(r'(gho_[a-zA-Z0-9]{20,})'),                           # GitHub OAuth
    re.compile(r'(ghu_[a-zA-Z0-9]{20,})'),                           # GitHub User
    re.compile(r'(ghs_[a-zA-Z0-9]{20,})'),                           # GitHub Server
    re.compile(r'(xoxb-[a-zA-Z0-9-]{20,})'),                         # Slack bot token
    re.compile(r'(xoxp-[a-zA-Z0-9-]{20,})'),                         # Slack user token
    re.compile(r'(AKIA[A-Z0-9]{16})'),                                # AWS access key
    # Key-value patterns in JSON/env output
    re.compile(r'("(?:API_KEY|SECRET_KEY|ACCESS_KEY|AUTH_TOKEN|PRIVATE_KEY|PASSWORD|SECRET|TOKEN|CREDENTIALS|GH_PAT|OPENAI_API_KEY|ELEVENLABS_API_KEY|DAILY_API_KEY|ANTHROPIC_API_KEY|STRIPE_SECRET_KEY|DATABASE_URL|REDIS_URL|SENTRY_DSN)"\s*:\s*")([^"]{8,})(")'),
    # Master username/password in RDS output
    re.compile(r'("MasterUserPassword"\s*:\s*")([^"]+)(")'),
]


def _redact_secrets(content: str) -> str:
    """Redact known secret patterns from tool output."""
    for pattern in _SECRET_PATTERNS:
        groups = pattern.groups if hasattr(pattern, 'groups') else 0
        if pattern.groups >= 3:
            # Key-value pattern: preserve key, redact value
            content = pattern.sub(r'\1[REDACTED]\3', content)
        else:
            # Direct token pattern: replace entire match
            content = pattern.sub('[REDACTED]', content)
    return content


class SubAgentResult:
    def __init__(
        self,
        agent_id: SubAgentType,
        findings: str,
        tools_used: list[dict[str, Any]],
        iterations: int,
        token_usage: dict[str, int],
    ):
        self.agent_id = agent_id
        self.findings = findings
        self.tools_used = tools_used
        self.iterations = iterations
        self.token_usage = token_usage


class SubAgentConfig:
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        mcp: MCPClientManager,
        investigation_context: InvestigationContext,
        account_id: str,
        enabled_integrations: list[str],
        settings: dict[str, Any],
    ):
        self.client = client
        self.model = model
        self.mcp = mcp
        self.investigation_context = investigation_context
        self.account_id = account_id
        self.enabled_integrations = enabled_integrations
        self.settings = settings


class BaseSubAgent(ABC):
    agent_id: SubAgentType
    agent_type: str
    shared_integrations: list[str] = []

    def __init__(self, config: SubAgentConfig) -> None:
        self.config = config
        self._tool_cache: dict[str, dict[str, Any]] = {}

    @abstractmethod
    def get_own_tools(self) -> list[dict[str, Any]]:
        """Domain-specific tools."""
        ...

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Domain-specific system prompt."""
        ...

    def get_required_tool_patterns(self, task: str) -> list[str]:
        """Return tool name patterns that MUST be called given the task.

        Override in sub-agents to enforce specific tools for specific tasks.
        Returns empty list by default (no required tools).
        """
        return []

    def _check_required_tools(self, task: str, all_tool_uses: list[dict[str, Any]]) -> list[str]:
        """Check if required tools were called AND returned useful data.

        Returns list of missing or failed tool pattern descriptions.
        """
        required = self.get_required_tool_patterns(task)
        if not required:
            return []

        missing = []
        for pattern in required:
            pat_lower = pattern.lower()
            # Find tools matching this pattern
            matching_tools = [
                tu for tu in all_tool_uses
                if pat_lower in tu.get("name", "").lower()
            ]

            if not matching_tools:
                missing.append(pattern)
                continue

            # Check if ALL matching tools failed or returned empty data
            all_failed = all(
                tu.get("status") == "error"
                or self._is_empty_result(tu.get("output", ""))
                for tu in matching_tools
            )
            if all_failed:
                missing.append(f"{pattern} (called but returned errors/empty — retry with corrected parameters)")

        return missing

    @staticmethod
    def _is_empty_result(output: str) -> bool:
        """Check if a tool result is effectively empty (zero data)."""
        if not output:
            return True
        # Check for common empty result patterns
        try:
            data = json.loads(output)
            if isinstance(data, dict):
                # NRQL results with count: 0
                results = data.get("results", [])
                if results and all(r.get("count", -1) == 0 for r in results if isinstance(r, dict)):
                    return True
                # NRQL TIMESERIES results where all data points are zero/null
                if isinstance(results, list) and results:
                    if all(isinstance(r, dict) and "beginTimeSeconds" in r for r in results[:3]):
                        all_zero = True
                        for r in results:
                            for k, v in r.items():
                                if k in ("beginTimeSeconds", "endTimeSeconds", "inspectedCount"):
                                    continue
                                if isinstance(v, (int, float)) and v != 0:
                                    all_zero = False
                                    break
                                if isinstance(v, dict):
                                    if any(isinstance(sv, (int, float)) and sv != 0 for sv in v.values()):
                                        all_zero = False
                                        break
                            if not all_zero:
                                break
                        if all_zero and len(results) > 0:
                            return True
                # Empty entity response
                if data.get("entity") == {}:
                    return True
                # All errors, no results
                if data.get("errors") and not data.get("results"):
                    return True
                # Empty Datapoints / events
                if isinstance(data.get("Datapoints"), list) and len(data["Datapoints"]) == 0:
                    return True
                if isinstance(data.get("events"), list) and len(data["events"]) == 0:
                    return True
        except (json.JSONDecodeError, TypeError):
            pass
        return False

    # ── Tool helpers ──────────────────────────────────────────

    def get_mcp_tools(self, integration: str) -> list[dict[str, Any]]:
        mcp_tools = self.config.mcp.get_tools_for_integrations([integration])
        return [
            {
                "type": "function",
                "name": t.name,
                "description": t.description or "",
                "parameters": (t.inputSchema or {}) if hasattr(t, "inputSchema") else {},
                "strict": False,
            }
            for t in mcp_tools
        ]

    def get_registry_tools_by_category(self, category: str) -> list[dict[str, Any]]:
        return tool_registry.to_openai_tools_by_category(category)

    def get_excluded_tool_patterns(self, task: str) -> list[str]:
        """Return tool name patterns to EXCLUDE given the task.

        Override in sub-agents to filter out irrelevant tools.
        Returns empty list by default (no exclusions).
        """
        return []

    def get_tools(self, task: str = "") -> list[dict[str, Any]]:
        own = self.get_own_tools()
        shared: list[dict[str, Any]] = []
        for integration in self.shared_integrations:
            if integration in self.config.enabled_integrations:
                shared.extend(self.get_mcp_tools(integration))
        all_tools = [*own, *shared]

        # Filter out excluded tools based on task context
        excluded = self.get_excluded_tool_patterns(task)
        if excluded:
            filtered = [
                t for t in all_tools
                if not any(pat in t.get("name", "").lower() for pat in excluded)
            ]
            if len(filtered) < len(all_tools):
                logger.info(
                    "Filtered %d tools from %s (excluded patterns: %s)",
                    len(all_tools) - len(filtered), self.agent_id, excluded,
                )
            return filtered

        return all_tools

    # ── Run loop ──────────────────────────────────────────────

    async def run(
        self,
        task: str,
        on_tool_use: Any = None,
        on_text_delta: Any = None,
    ) -> SubAgentResult:
        client = self.config.client
        model = self.config.model
        ctx = self.config.investigation_context
        tools = self.get_tools(task)
        system_prompt = self.get_system_prompt()

        input_items: list[Any] = [{"role": "user", "content": task}]

        ctx_msg = ctx.build_context_message()
        if ctx_msg:
            input_items.insert(0, {
                "role": "developer",
                "content": f"[Investigation Context]\n{ctx_msg}",
            })

        all_tool_uses: list[dict[str, Any]] = []
        full_text = ""
        total_tokens = {"input": 0, "output": 0}
        forced_extension_used = False

        for iteration in range(MAX_SUB_AGENT_ITERATIONS + 1):  # +1 for possible forced extension
            tool_choice = "required" if iteration == 0 and tools else "auto"

            response = await client.responses.create(
                model=model,
                instructions=system_prompt,
                input=input_items,
                tools=tools if tools else None,
                tool_choice=tool_choice if tools else None,
                stream=True,
            )

            iteration_text = ""
            function_calls: list[dict[str, str]] = []
            completed_response: Any = None

            async for event in response:
                if event.type == "response.output_text.delta":
                    iteration_text += event.delta
                    full_text += event.delta
                    if on_text_delta:
                        on_text_delta(self.agent_id, event.delta)

                elif event.type == "response.output_item.added":
                    if event.item.type == "function_call":
                        item = event.item
                        tool_use = {
                            "id": item.call_id,
                            "name": item.name,
                            "integration": (
                                (tool_registry.get(item.name).category if tool_registry.get(item.name) else None)
                                or self.config.mcp.get_tool_server(item.name)
                            ),
                            "input": {},
                            "status": "running",
                            "agentId": self.agent_id,
                        }
                        all_tool_uses.append(tool_use)
                        if on_tool_use:
                            on_tool_use(tool_use)

                elif event.type == "response.completed":
                    completed_response = event.response

            if completed_response and hasattr(completed_response, "usage") and completed_response.usage:
                total_tokens["input"] += getattr(completed_response.usage, "input_tokens", 0) or 0
                total_tokens["output"] += getattr(completed_response.usage, "output_tokens", 0) or 0

            # Extract function calls
            if completed_response and hasattr(completed_response, "output"):
                for item in completed_response.output:
                    if item.type == "function_call":
                        function_calls.append({
                            "call_id": item.call_id,
                            "name": item.name,
                            "arguments": item.arguments,
                        })

            # No function calls → done (but check required tools first)
            if not function_calls:
                missing = self._check_required_tools(task, all_tool_uses)
                if missing and not forced_extension_used:
                    # Force one more iteration to call missing tools
                    forced_extension_used = True
                    logger.warning(
                        "Sub-agent %s finished without calling required tools: %s. Forcing extension.",
                        self.agent_id, missing,
                    )
                    input_items.append({
                        "role": "developer",
                        "content": f"You have NOT called the following required tools: {', '.join(missing)}. You MUST call them before finishing. This is mandatory for a complete investigation.",
                    })
                    continue

                return SubAgentResult(
                    agent_id=self.agent_id,
                    findings=full_text,
                    tools_used=all_tool_uses,
                    iterations=iteration + 1,
                    token_usage=total_tokens,
                )

            # Append response output items
            if completed_response and hasattr(completed_response, "output"):
                for item in completed_response.output:
                    input_items.append(item)

            # Execute tool calls
            await self._execute_tool_calls(
                function_calls, input_items, ctx, on_tool_use, all_tool_uses,
            )

        return SubAgentResult(
            agent_id=self.agent_id,
            findings=full_text or "Sub-agent reached maximum iterations without producing a text summary.",
            tools_used=all_tool_uses,
            iterations=MAX_SUB_AGENT_ITERATIONS,
            token_usage=total_tokens,
        )

    async def _execute_tool_calls(
        self,
        function_calls: list[dict[str, str]],
        input_items: list[Any],
        ctx: InvestigationContext,
        on_tool_use: Any,
        all_tool_uses: list[dict[str, Any]],
    ) -> None:
        mcp = self.config.mcp

        for fc in function_calls:
            tool_name = fc["name"]
            is_custom = tool_registry.has(tool_name)
            integration = (
                (tool_registry.get(tool_name).category if tool_registry.get(tool_name) else None)
                if is_custom
                else mcp.get_tool_server(tool_name)
            )

            try:
                tool_args = json.loads(fc["arguments"])
            except json.JSONDecodeError:
                input_items.append({
                    "type": "function_call_output",
                    "call_id": fc["call_id"],
                    "output": f"TOOL ERROR — Could not parse arguments as JSON. Raw: {fc['arguments']}\n\nRetry with valid JSON arguments.",
                })
                self._emit_tool_update(all_tool_uses, on_tool_use, {
                    "id": fc["call_id"], "name": tool_name, "integration": integration,
                    "input": {}, "status": "error", "error": "Invalid JSON arguments",
                    "agentId": self.agent_id,
                })
                continue

            # Intercept and validate tool args
            tool_args = self._intercept_tool_args(tool_name, tool_args, integration)

            # Cache check
            cache_key = f"{tool_name}::{json.dumps(tool_args, sort_keys=True)}"
            cached = self._tool_cache.get(cache_key)
            if cached:
                input_items.append({
                    "type": "function_call_output",
                    "call_id": fc["call_id"],
                    "output": cached["output"],
                })
                self._emit_tool_update(all_tool_uses, on_tool_use, {
                    "id": fc["call_id"], "name": tool_name, "integration": integration,
                    "input": tool_args, "output": cached["output"], "status": "success",
                    "executionTimeMs": cached["executionTimeMs"], "agentId": self.agent_id,
                })
                continue

            start_time = time.time()

            self._emit_tool_update(all_tool_uses, on_tool_use, {
                "id": fc["call_id"], "name": tool_name, "integration": integration,
                "input": tool_args, "status": "running", "agentId": self.agent_id,
            })

            try:
                if is_custom:
                    tool_ctx = ToolContext(
                        account_id=self.config.account_id,
                        settings=self.config.settings,
                    )
                    result = await tool_registry.execute(tool_name, tool_args, tool_ctx)
                    content = result.output
                else:
                    result = await mcp.call_tool(tool_name, tool_args)
                    content = json.dumps(result) if not isinstance(result, str) else result

                execution_time = int((time.time() - start_time) * 1000)

                # Redact secrets before any further processing
                content = _redact_secrets(content)

                # Post-process results (Sentry time filtering)
                content = self._post_process_tool_result(tool_name, content, integration)

                if len(content) > MAX_TOOL_RESULT_CHARS:
                    content = (
                        content[:MAX_TOOL_RESULT_CHARS]
                        + f"\n\n[... truncated {len(content) - MAX_TOOL_RESULT_CHARS} chars]"
                    )

                tw_start = ctx.time_window.get("start")
                tw_end = ctx.time_window.get("end")
                if tw_start and tw_end:
                    utc_s = tw_start.astimezone(timezone.utc) if tw_start.tzinfo else tw_start.replace(tzinfo=timezone.utc)
                    utc_e = tw_end.astimezone(timezone.utc) if tw_end.tzinfo else tw_end.replace(tzinfo=timezone.utc)
                    content += f"\n\n[Investigation window: {utc_s.strftime('%Y-%m-%d %H:%M:%S')} UTC to {utc_e.strftime('%Y-%m-%d %H:%M:%S')} UTC]"

                self._tool_cache[cache_key] = {"output": content, "executionTimeMs": execution_time}

                input_items.append({
                    "type": "function_call_output",
                    "call_id": fc["call_id"],
                    "output": content,
                })

                self._emit_tool_update(all_tool_uses, on_tool_use, {
                    "id": fc["call_id"], "name": tool_name, "integration": integration,
                    "input": tool_args, "output": content, "status": "success",
                    "executionTimeMs": execution_time, "agentId": self.agent_id,
                })

                ctx.extract_from_tool_result(tool_name, integration, tool_args, content)

                # Extract critical signals
                for signal in self._extract_critical_signals(tool_name, content):
                    ctx.add_critical_signal(signal)

                # Update investigation plan
                if ctx.investigation_plan:
                    ctx.investigation_plan.mark_check_by_tool(tool_name, self.agent_id)

            except Exception as e:
                execution_time = int((time.time() - start_time) * 1000)
                error_msg = f"TOOL ERROR — {tool_name} failed: {e}\n\nRetry with different parameters, or use an alternative tool."
                # Add PI-specific hint for GroupBy errors
                if ("resource_metrics" in tool_name.lower() or "get-resource-metrics" in tool_name.lower()) and "GroupBy" in str(e):
                    error_msg += (
                        "\n\nHINT: Valid GroupBy Groups for Performance Insights are: "
                        "db.sql, db.sql_tokenized, db.host, db.application, db.session_type, db.user. "
                        "db.wait_event and db.wait_state are Metrics, NOT GroupBy Groups."
                    )
                input_items.append({
                    "type": "function_call_output",
                    "call_id": fc["call_id"],
                    "output": error_msg,
                })
                self._emit_tool_update(all_tool_uses, on_tool_use, {
                    "id": fc["call_id"], "name": tool_name, "integration": integration,
                    "input": tool_args, "status": "error", "error": str(e),
                    "executionTimeMs": execution_time, "agentId": self.agent_id,
                })

    # ── Tool interception ──────────────────────────────────────

    def _intercept_pi_args(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        """Intercept Performance Insights tool args to fix invalid GroupBy Groups."""
        tool_lower = tool_name.lower()
        if "resource_metrics" not in tool_lower and "get-resource-metrics" not in tool_lower:
            return tool_args

        metric_queries = tool_args.get("MetricQueries")
        if not isinstance(metric_queries, list):
            return tool_args

        modified = False
        for mq in metric_queries:
            group_by = mq.get("GroupBy")
            if not isinstance(group_by, dict):
                continue
            group = group_by.get("Group", "")
            if group in PI_METRIC_ONLY_GROUPS:
                logger.warning(
                    "PI auto-correct: '%s' is a Metric, not a valid GroupBy Group. "
                    "Replacing with 'db.sql_tokenized'.",
                    group,
                )
                group_by["Group"] = "db.sql_tokenized"
                modified = True
            elif group and group not in PI_VALID_GROUPBY_GROUPS:
                logger.warning(
                    "PI auto-correct: unknown GroupBy Group '%s'. "
                    "Replacing with 'db.sql_tokenized'. Valid groups: %s",
                    group, ", ".join(sorted(PI_VALID_GROUPBY_GROUPS)),
                )
                group_by["Group"] = "db.sql_tokenized"
                modified = True

        if modified:
            tool_args = {**tool_args, "MetricQueries": metric_queries}

        return tool_args

    def _intercept_tool_args(self, tool_name: str, tool_args: dict[str, Any], integration: str | None = None) -> dict[str, Any]:
        """Intercept and fix tool arguments before execution.

        - For PI tools: fix invalid GroupBy Groups.
        - For NRQL queries: fix timezone offsets, bare apdex(), wrong time windows.
        - For Sentry tools: auto-inject time window filters.
        - For all tools: validate timestamp arguments against the investigation window.
        """
        # PI GroupBy validation (always, regardless of time window)
        tool_args = self._intercept_pi_args(tool_name, tool_args)

        ctx = self.config.investigation_context
        tw_start = ctx.time_window.get("start")
        tw_end = ctx.time_window.get("end")

        if not tw_start or not tw_end:
            return tool_args

        tool_lower = tool_name.lower()
        integration_lower = (integration or "").lower()
        is_sentry = "sentry" in integration_lower or "sentry" in tool_lower
        is_nrql = "nrql" in tool_lower or "nr_run_nrql" in tool_lower

        # NRQL query interception — fix common errors
        if is_nrql and "nrql" in tool_args:
            tool_args = {**tool_args, "nrql": self._fix_nrql_query(tool_args["nrql"], tw_start, tw_end)}

        # Sentry time-window injection
        if is_sentry and ("search" in tool_lower or "list_issue" in tool_lower or "list_issues" in tool_lower):
            query = tool_args.get("query", "")
            if "firstSeen" not in query and "lastSeen" not in query:
                utc_start = tw_start.astimezone(timezone.utc) if tw_start.tzinfo else tw_start
                utc_end = tw_end.astimezone(timezone.utc) if tw_end.tzinfo else tw_end
                time_filter = f" firstSeen:>{utc_start.strftime('%Y-%m-%dT%H:%M:%S')} lastSeen:<{utc_end.strftime('%Y-%m-%dT%H:%M:%S')}"
                tool_args = {**tool_args, "query": (query + time_filter).strip()}
                logger.info("Injected time window into Sentry query: %s", tool_args["query"])

        # Timestamp validation
        self._validate_timestamps(tool_args, tw_start, tw_end)

        return tool_args

    def _fix_nrql_query(self, nrql: str, tw_start: datetime, tw_end: datetime) -> str:
        """Fix common NRQL syntax issues before execution."""
        original = nrql

        # Normalize timestamps to UTC
        utc_start = tw_start.astimezone(timezone.utc) if tw_start.tzinfo else tw_start.replace(tzinfo=timezone.utc)
        utc_end = tw_end.astimezone(timezone.utc) if tw_end.tzinfo else tw_end.replace(tzinfo=timezone.utc)

        # Fix 1: Replace timezone-offset timestamps with single-quoted UTC
        # Pattern: SINCE 2026-02-20T00:48:21+05:30  or  SINCE 2026-02-20 00:48:21+05:30
        tz_offset_pattern = r"(SINCE|UNTIL)\s+'?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})[+-]\d{2}:\d{2}'?"
        for match in re.finditer(tz_offset_pattern, nrql, re.IGNORECASE):
            keyword = match.group(1)
            ts_str = match.group(2)
            try:
                # Parse the original timestamp with offset to get the actual moment
                full_match_str = match.group(0)
                # Extract just the offset part from the original
                offset_match = re.search(r'([+-]\d{2}:\d{2})', full_match_str)
                if offset_match:
                    full_ts = ts_str.replace("T", " ") + offset_match.group(1)
                    parsed = datetime.fromisoformat(full_ts)
                    utc_ts = parsed.astimezone(timezone.utc)
                    nrql = nrql.replace(match.group(0), f"{keyword} '{utc_ts.strftime('%Y-%m-%d %H:%M:%S')}'")
            except (ValueError, TypeError):
                pass

        # Fix 2: Replace bare apdex() or apdex(score:X) with apdex(duration, t: 0.5)
        nrql = re.sub(r'apdex\(\)', 'apdex(duration, t: 0.5)', nrql)
        nrql = re.sub(r'apdex\(score\s*:\s*[\d.]+\)', 'apdex(duration, t: 0.5)', nrql)

        # Fix 3: Detect "SINCE X hours ago" when X is too small for the actual incident window
        now = datetime.now(timezone.utc)
        hours_since_incident_start = (now - utc_start).total_seconds() / 3600

        if hours_since_incident_start > 4:  # Incident is more than 4 hours old
            relative_match = re.search(r"SINCE\s+(\d+)\s+hours?\s+ago", nrql, re.IGNORECASE)
            if relative_match:
                requested_hours = int(relative_match.group(1))
                # If the model is querying a recent window that doesn't overlap with the incident
                if requested_hours < hours_since_incident_start - 1:
                    # Check if there's also an UNTIL clause
                    until_match = re.search(r"UNTIL\s+(\d+)\s+hours?\s+ago", nrql, re.IGNORECASE)
                    if until_match:
                        until_hours = int(until_match.group(1))
                        # If the UNTIL is also recent (doesn't reach the incident), fix both
                        if until_hours < int(hours_since_incident_start) - 2:
                            padded_start_hours = int(hours_since_incident_start) + 1
                            end_hours = max(0, int((now - utc_end).total_seconds() / 3600))
                            nrql = re.sub(
                                r"SINCE\s+\d+\s+hours?\s+ago\s+UNTIL\s+\d+\s+hours?\s+ago",
                                f"SINCE {padded_start_hours} hours ago UNTIL {end_hours} hours ago",
                                nrql,
                                flags=re.IGNORECASE,
                            )
                    else:
                        # No UNTIL clause — replace SINCE with the full incident window
                        padded_start_hours = int(hours_since_incident_start) + 1
                        end_hours = max(0, int((now - utc_end).total_seconds() / 3600))
                        nrql = re.sub(
                            r"SINCE\s+\d+\s+hours?\s+ago",
                            f"SINCE {padded_start_hours} hours ago UNTIL {end_hours} hours ago",
                            nrql,
                            flags=re.IGNORECASE,
                        )

        # Fix 4: Unquoted absolute timestamps (SINCE 2024-01-15 14:00:00 without quotes)
        nrql = re.sub(
            r"(SINCE|UNTIL)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})(?!')",
            r"\1 '\2'",
            nrql,
            flags=re.IGNORECASE,
        )

        if nrql != original:
            logger.info("NRQL auto-corrected:\n  original: %s\n  fixed:    %s", original, nrql)

        return nrql

    def _validate_timestamps(self, tool_args: dict[str, Any], tw_start: datetime, tw_end: datetime) -> None:
        """Validate and auto-correct timestamp arguments that are outside the investigation window."""
        timestamp_keys = ["start_time", "end_time", "since", "from", "to", "startTime", "endTime", "start-time", "end-time"]

        for key in timestamp_keys:
            val = tool_args.get(key)
            if not val or not isinstance(val, str):
                continue

            parsed_ts = self._try_parse_timestamp(val)
            if not parsed_ts:
                continue

            # If timestamp is >24h outside the window, auto-correct
            window_start_padded = tw_start - timedelta(hours=24)
            window_end_padded = tw_end + timedelta(hours=24)

            if parsed_ts < window_start_padded or parsed_ts > window_end_padded:
                if "start" in key.lower() or key in ("since", "from"):
                    corrected = tw_start - timedelta(minutes=30)
                else:
                    corrected = tw_end

                logger.warning(
                    "Timestamp %s=%s is outside investigation window [%s, %s]. Auto-correcting to %s.",
                    key, val, tw_start.isoformat(), tw_end.isoformat(), corrected.isoformat(),
                )
                tool_args[key] = corrected.isoformat()

    @staticmethod
    def _try_parse_timestamp(val: str) -> datetime | None:
        """Try to parse various timestamp formats."""
        for fmt in [
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]:
            try:
                ts = datetime.strptime(val, fmt)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return ts
            except ValueError:
                continue
        # Try epoch milliseconds
        try:
            epoch_ms = int(val)
            if epoch_ms > 1_000_000_000_000:  # epoch ms
                return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
            elif epoch_ms > 1_000_000_000:  # epoch seconds
                return datetime.fromtimestamp(epoch_ms, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
        return None

    def _post_process_tool_result(self, tool_name: str, content: str, integration: str | None = None) -> str:
        """Post-process tool results to reduce token waste and improve quality."""
        tool_lower = tool_name.lower()
        integration_lower = (integration or "").lower()

        # Compact all-zero NRQL TIMESERIES results
        if "nrql" in tool_lower or "nr_run_nrql" in tool_lower:
            content = self._compact_empty_timeseries(content)

        # Summarize large CloudWatch log event outputs
        if "filter_log_events" in tool_lower or "filter-log-events" in tool_lower:
            content = self._summarize_log_events(content)

        if "sentry" not in integration_lower and "sentry" not in tool_lower:
            return content

        ctx = self.config.investigation_context
        tw_start = ctx.time_window.get("start")
        tw_end = ctx.time_window.get("end")
        if not tw_start or not tw_end:
            return content

        # Widen the filter window by ±2 hours
        filter_start = tw_start - timedelta(hours=2)
        filter_end = tw_end + timedelta(hours=2)

        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content

        # Handle MCP-style nested content
        if isinstance(data, dict) and isinstance(data.get("content"), list):
            for block in data["content"]:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    try:
                        inner = json.loads(block["text"])
                        if isinstance(inner, list):
                            filtered = self._filter_sentry_issues(inner, filter_start, filter_end)
                            block["text"] = json.dumps(filtered[:25])
                    except (json.JSONDecodeError, TypeError):
                        pass
            return json.dumps(data)

        if isinstance(data, list):
            filtered = self._filter_sentry_issues(data, filter_start, filter_end)
            return json.dumps(filtered[:25])

        return content

    @staticmethod
    def _filter_sentry_issues(issues: list[dict], start: datetime, end: datetime) -> list[dict]:
        """Filter Sentry issues to those with lastSeen within the given window."""
        filtered = []
        for issue in issues:
            last_seen_str = issue.get("lastSeen")
            if not last_seen_str:
                filtered.append(issue)  # Keep if we can't determine time
                continue
            try:
                last_seen = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
                if last_seen >= start:
                    filtered.append(issue)
            except (ValueError, TypeError):
                filtered.append(issue)
        return filtered

    @staticmethod
    def _compact_empty_timeseries(content: str) -> str:
        """Replace all-zero NRQL TIMESERIES with a compact summary to save tokens."""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content

        if not isinstance(data, dict):
            return content

        results = data.get("results")
        if not isinstance(results, list) or len(results) < 3:
            return content

        # Check if this is a TIMESERIES response (has beginTimeSeconds)
        if not all(isinstance(r, dict) and "beginTimeSeconds" in r for r in results[:3]):
            return content

        # Check if ALL data values are zero/null
        all_zero = True
        for r in results:
            for k, v in r.items():
                if k in ("beginTimeSeconds", "endTimeSeconds", "inspectedCount"):
                    continue
                if isinstance(v, (int, float)) and v != 0:
                    all_zero = False
                    break
                if isinstance(v, dict):
                    if any(isinstance(sv, (int, float)) and sv != 0 for sv in v.values()):
                        all_zero = False
                        break
            if not all_zero:
                break

        if all_zero:
            # Replace with compact summary
            n_buckets = len(results)
            first_ts = results[0].get("beginTimeSeconds", 0)
            last_ts = results[-1].get("endTimeSeconds", 0)
            metric_keys = [k for k in results[0].keys() if k not in ("beginTimeSeconds", "endTimeSeconds", "inspectedCount")]
            nrql = data.get("nrql", "")

            summary = {
                "nrql": nrql,
                "summary": f"TIMESERIES returned 0 across all {n_buckets} time buckets for metrics: {', '.join(metric_keys)}",
                "timeRange": {"beginTimeSeconds": first_ts, "endTimeSeconds": last_ts},
                "totalBuckets": n_buckets,
                "allZero": True,
            }
            logger.info("Compacted all-zero TIMESERIES: %d buckets → summary", n_buckets)
            return json.dumps(summary)

        return content

    @staticmethod
    def _summarize_log_events(content: str) -> str:
        """Summarize large CloudWatch log event results into counts + samples."""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content

        if not isinstance(data, dict):
            return content

        events = data.get("events")
        if not isinstance(events, list) or len(events) < 30:
            return content  # Only summarize when there are many events

        # Count events by message pattern (strip timestamps and connection IDs)
        pattern_counts: dict[str, int] = {}
        timestamps: list[int] = []

        for event in events:
            ts = event.get("timestamp")
            if ts:
                timestamps.append(ts)

            msg = event.get("message", "")
            # Normalize: strip leading timestamp, IP, user, PID
            normalized = re.sub(
                r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC:[\d.:()]+:\w+@\w+:\[\d+\]:',
                '',
                msg,
            ).strip()
            # Further normalize by removing unique identifiers
            normalized = re.sub(r'\[\w{8}-\w{4}-\w{4}-\w{4}-\w{12}\]', '[REQ_ID]', normalized)

            if not normalized:
                normalized = msg[:100]

            # Group by first 80 chars of the normalized pattern
            key = normalized[:80]
            pattern_counts[key] = pattern_counts.get(key, 0) + 1

        # Build summary
        total = len(events)
        time_range = ""
        if timestamps:
            min_ts = min(timestamps)
            max_ts = max(timestamps)
            min_dt = datetime.fromtimestamp(min_ts / 1000, tz=timezone.utc) if min_ts > 1_000_000_000_000 else datetime.fromtimestamp(min_ts, tz=timezone.utc)
            max_dt = datetime.fromtimestamp(max_ts / 1000, tz=timezone.utc) if max_ts > 1_000_000_000_000 else datetime.fromtimestamp(max_ts, tz=timezone.utc)
            time_range = f"{min_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC to {max_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC"

        # Top patterns sorted by count
        sorted_patterns = sorted(pattern_counts.items(), key=lambda x: -x[1])[:10]

        summary_lines = [
            f"**Log Events Summary** ({total} events{f', {time_range}' if time_range else ''})",
            "",
            "**Event patterns (top by frequency):**",
        ]
        for pattern, count in sorted_patterns:
            summary_lines.append(f"  - [{count}x] {pattern}")

        # Include 3 sample raw messages
        summary_lines.append("")
        summary_lines.append("**Sample messages:**")
        for event in events[:3]:
            summary_lines.append(f"  - {event.get('message', '')[:200]}")

        has_next = data.get("nextToken") or data.get("nextForwardToken")
        if has_next:
            summary_lines.append(f"\n(Results were paginated — {total} events shown, more available)")

        logger.info("Summarized %d log events into compact summary", total)
        return "\n".join(summary_lines)

    def _extract_critical_signals(self, tool_name: str, content: str) -> list[dict[str, Any]]:
        """Scan tool output for critical patterns and return detected signals."""
        signals: list[dict[str, Any]] = []
        for pattern, category in CRITICAL_PATTERNS.items():
            matches = re.findall(pattern, content[:10_000])  # Only scan first 10K chars
            if matches:
                signals.append({
                    "category": category,
                    "match": matches[0] if isinstance(matches[0], str) else str(matches[0]),
                    "source_tool": tool_name,
                    "agent_id": self.agent_id,
                })
        return signals

    def _emit_tool_update(
        self,
        all_tool_uses: list[dict[str, Any]],
        on_tool_use: Any,
        tool_use: dict[str, Any],
    ) -> None:
        for i, t in enumerate(all_tool_uses):
            if t["id"] == tool_use["id"]:
                all_tool_uses[i] = tool_use
                break
        if on_tool_use:
            on_tool_use(tool_use)
