"""Infrastructure sub-agent specializing in AWS data."""

from __future__ import annotations

from typing import Any

from .base import BaseSubAgent, SubAgentConfig
from ..prompts import GUIDANCE_AWS


class InfrastructureAgent(BaseSubAgent):
    agent_id = "infrastructure"  # type: ignore[assignment]
    agent_type = "Infrastructure Agent"

    def __init__(self, config: SubAgentConfig) -> None:
        super().__init__(config)

    def get_required_tool_patterns(self, task: str) -> list[str]:
        task_lower = task.lower()
        patterns = []
        if any(kw in task_lower for kw in ["rds", "database", "db", "aurora", "postgres", "mysql"]):
            patterns.extend(["cloudwatch", "rds"])
            # Require PI when investigating database incidents and PI resources exist
            ctx = self.config.investigation_context
            if any(r.type == "rds" and r.attrs.get("piEnabled") == "True" for r in ctx.resources):
                patterns.append("pi")
        if any(kw in task_lower for kw in ["ec2", "instance", "server"]):
            patterns.append("cloudwatch")
        if any(kw in task_lower for kw in ["lambda", "function"]):
            patterns.append("cloudwatch")
        if any(kw in task_lower for kw in ["ecs", "fargate", "container"]):
            patterns.append("cloudwatch")
        if any(kw in task_lower for kw in ["log", "error pattern"]):
            patterns.append("logs")
        return patterns

    def get_excluded_tool_patterns(self, task: str) -> list[str]:
        task_lower = task.lower()
        excluded = []
        is_db = any(kw in task_lower for kw in ["rds", "database", "db", "aurora", "postgres", "mysql"])
        is_lambda = any(kw in task_lower for kw in ["lambda", "function"])
        is_ec2 = any(kw in task_lower for kw in ["ec2", "instance", "server"])
        is_ecs = any(kw in task_lower for kw in ["ecs", "fargate", "container"])

        # Exclude Lambda tools when investigating non-Lambda resources
        if not is_lambda and (is_db or is_ec2 or is_ecs):
            excluded.extend(["list_functions", "get_function"])
        # Exclude ECS tools when investigating non-ECS resources
        if not is_ecs and (is_db or is_ec2 or is_lambda):
            excluded.extend(["list_clusters", "describe_clusters", "list_services", "describe_services", "list_tasks", "describe_tasks"])
        # Exclude EC2 tools when investigating non-EC2 resources
        if not is_ec2 and (is_db or is_lambda or is_ecs):
            excluded.append("describe_instances")

        return excluded

    def get_own_tools(self) -> list[dict[str, Any]]:
        # Use MCP AWS tools (call_aws, suggest_aws_commands) when available,
        # plus custom boto3 tools from the registry
        mcp_tools = self.get_mcp_tools("aws")
        registry_tools = self.get_registry_tools_by_category("aws")
        return [*mcp_tools, *registry_tools]

    def get_system_prompt(self) -> str:
        # Check if any discovered RDS resources have Performance Insights enabled
        ctx = self.config.investigation_context
        pi_resources = []
        for r in ctx.resources:
            if r.type == "rds" and r.attrs.get("piEnabled") == "True":
                pi_resources.append(r)

        pi_section = ""
        if pi_resources:
            pi_ids = ", ".join(f"`{r.id}`" for r in pi_resources if r.id)
            pi_section = f"""

# Performance Insights (MANDATORY — PI is enabled on: {', '.join(r.name for r in pi_resources)})

You MUST call Performance Insights for these RDS instances. This is the HIGHEST PRIORITY investigation step for database incidents because it reveals WHICH specific SQL queries caused the CPU spike.

Use `aws_get_resource_metrics` with:
- **ServiceType**: "RDS"
- **Identifier**: The DbiResourceId (e.g. {pi_ids}) — NOT the instance name
- **MetricQueries**: Use `db.load` metric with `GroupBy` dimensions:
  - `db.sql` — Top SQL statements by load
  - `db.wait_event` — Top wait events (CPU, IO, Lock, etc.)
- **StartTime** / **EndTime**: Use the investigation time window (ISO 8601)
- **PeriodInSeconds**: 60 for detailed view

Also call `aws_describe_dimension_keys` with:
- **ServiceType**: "RDS"
- **Identifier**: The DbiResourceId
- **Metric**: `db.load`
- **GroupBy**: `db.sql` to get the top SQL queries ranked by DB load

This data tells you exactly which queries consumed the most CPU — this is the root cause evidence."""

        return f"""You are an infrastructure investigation agent specializing in AWS data. You have access to AWS tools that let you query CloudWatch metrics, EC2 instances, Lambda functions, RDS databases, CloudWatch Logs, and Performance Insights.

# Rules
1. Always include region context in your queries.
2. If a command fails, read the error, fix parameters, retry.
3. Empty results may mean wrong region — try alternatives.
4. Report specific metric values, not vague descriptions.
5. Use resource names and IDs from the investigation context when available — do not re-discover.
6. **Always compare incident-window metrics against baseline** (the period before the incident) to show what changed.
{pi_section}

# Investigation Sequence
For any investigation, follow this sequence:
1. **CloudWatch Alarms** — Check alarm states for affected resources.
2. **Compute health** — EC2/ECS/Lambda status and key metrics.
3. **Database investigation** (MANDATORY when any RDS/database is in scope):
   a. CloudWatch metrics: CPUUtilization, DatabaseConnections, FreeableMemory, ReadLatency, WriteLatency, DiskQueueDepth
   b. **Performance Insights** (MANDATORY when PI is enabled): Top SQL queries and wait events during the incident window. This is the MOST IMPORTANT step for identifying root cause.
   c. Compare DB metrics during incident vs. baseline period before
4. **CloudWatch Logs** — Search application and infrastructure logs for error patterns, timeouts, and connection issues during the incident window.
   - For PostgreSQL databases, search with filter pattern `"duration"` (PostgreSQL logs slow queries as `duration: X ms`). Do NOT search for `"slow"` — PostgreSQL does not use that word.
   - Also search for `"ERROR"`, `"FATAL"`, `"canceling statement"`, `"connection"`.
5. **Network/connectivity** — Check for timeout patterns, connection refused errors, and load balancer metrics if applicable.

# Focus Areas
- CloudWatch alarms and their state changes
- EC2, Lambda, ECS health and resource utilization
- **RDS/Database**: CPU, connections, latency, slow queries, Performance Insights wait events, connection pool exhaustion
- **Performance Insights**: Top SQL queries by DB load, wait event breakdown (CPU vs IO vs Lock) — this identifies the root cause
- **CloudWatch Logs**: Error patterns, timeout patterns, unusual log entries scoped to the investigation time window
- Load balancer metrics (5xx errors, latency, healthy host count)
- Resource utilization trends (before vs during incident)

{GUIDANCE_AWS}

Produce a concise findings summary with: (1) alarm states and changes, (2) database health with specific metrics and comparisons, (3) **Performance Insights top SQL queries** (when available), (4) log patterns found during the incident window, (5) resource utilization before vs during the incident."""
