"""Investigation context — tracks state across the agent loop."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


class ResourceEntry:
    """A discovered resource from a cloud/monitoring platform."""

    def __init__(
        self,
        name: str,
        type: str,
        id: str | None = None,
        attrs: dict[str, str] | None = None,
    ):
        self.name = name
        self.type = type
        self.id = id
        self.attrs = attrs or {}

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.id:
            d["id"] = self.id
        if self.attrs:
            d["attrs"] = self.attrs
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ResourceEntry:
        return ResourceEntry(
            name=d["name"],
            type=d["type"],
            id=d.get("id"),
            attrs=d.get("attrs", {}),
        )


# ── Investigation Plan ─────────────────────────────────────────

# Mapping from incident keywords to required checks
INCIDENT_TYPE_CHECKS: dict[str, list[dict[str, Any]]] = {
    "rds": [
        {"check_id": "cw_rds_cpu", "agent_id": "infrastructure", "description": "CloudWatch RDS CPUUtilization, DatabaseConnections, FreeableMemory", "tool_patterns": ["cloudwatch", "get-metric"]},
        {"check_id": "cw_rds_io", "agent_id": "infrastructure", "description": "CloudWatch RDS ReadLatency, WriteLatency, ReadIOPS, WriteIOPS, DiskQueueDepth", "tool_patterns": ["cloudwatch", "get-metric"]},
        {"check_id": "pi_rds", "agent_id": "infrastructure", "description": "Performance Insights top SQL queries and wait events", "tool_patterns": ["pi", "performance-insights"]},
        {"check_id": "rds_events", "agent_id": "infrastructure", "description": "RDS event log for recent events", "tool_patterns": ["rds", "describe-events"]},
        {"check_id": "sentry_errors", "agent_id": "error_monitoring", "description": "Sentry errors in incident time window", "tool_patterns": ["sentry"]},
        {"check_id": "apm_golden", "agent_id": "apm", "description": "APM golden metrics (throughput, error rate, response time)", "tool_patterns": ["newrelic", "nrql"]},
        {"check_id": "apm_db", "agent_id": "apm", "description": "APM database/datastore operation latency", "tool_patterns": ["newrelic", "nrql", "datastore"]},
        {"check_id": "apm_logs", "agent_id": "apm", "description": "APM log analysis for error patterns", "tool_patterns": ["newrelic", "nrql", "log"]},
    ],
    "ecs": [
        {"check_id": "cw_ecs", "agent_id": "infrastructure", "description": "CloudWatch ECS CPUUtilization, MemoryUtilization, task counts", "tool_patterns": ["cloudwatch", "get-metric"]},
        {"check_id": "ecs_events", "agent_id": "infrastructure", "description": "ECS service events and task failures", "tool_patterns": ["ecs", "describe"]},
        {"check_id": "cw_logs", "agent_id": "infrastructure", "description": "CloudWatch Logs error patterns", "tool_patterns": ["logs", "filter-log-events"]},
        {"check_id": "sentry_errors", "agent_id": "error_monitoring", "description": "Sentry errors in incident time window", "tool_patterns": ["sentry"]},
        {"check_id": "apm_golden", "agent_id": "apm", "description": "APM golden metrics", "tool_patterns": ["newrelic", "nrql"]},
        {"check_id": "apm_logs", "agent_id": "apm", "description": "APM log analysis for error patterns", "tool_patterns": ["newrelic", "nrql", "log"]},
    ],
    "lambda": [
        {"check_id": "cw_lambda", "agent_id": "infrastructure", "description": "CloudWatch Lambda Duration, Errors, Throttles, ConcurrentExecutions", "tool_patterns": ["cloudwatch", "get-metric"]},
        {"check_id": "cw_logs", "agent_id": "infrastructure", "description": "CloudWatch Logs for Lambda error patterns", "tool_patterns": ["logs", "filter-log-events"]},
        {"check_id": "sentry_errors", "agent_id": "error_monitoring", "description": "Sentry errors in incident time window", "tool_patterns": ["sentry"]},
        {"check_id": "apm_golden", "agent_id": "apm", "description": "APM golden metrics", "tool_patterns": ["newrelic", "nrql"]},
    ],
    "ec2": [
        {"check_id": "cw_ec2", "agent_id": "infrastructure", "description": "CloudWatch EC2 CPUUtilization, StatusCheckFailed, NetworkIn/Out", "tool_patterns": ["cloudwatch", "get-metric"]},
        {"check_id": "ec2_status", "agent_id": "infrastructure", "description": "EC2 instance status checks", "tool_patterns": ["ec2", "describe"]},
        {"check_id": "cw_logs", "agent_id": "infrastructure", "description": "CloudWatch Logs for error patterns", "tool_patterns": ["logs", "filter-log-events"]},
        {"check_id": "sentry_errors", "agent_id": "error_monitoring", "description": "Sentry errors in incident time window", "tool_patterns": ["sentry"]},
        {"check_id": "apm_golden", "agent_id": "apm", "description": "APM golden metrics", "tool_patterns": ["newrelic", "nrql"]},
    ],
    "generic": [
        {"check_id": "sentry_errors", "agent_id": "error_monitoring", "description": "Sentry errors in incident time window", "tool_patterns": ["sentry"]},
        {"check_id": "apm_golden", "agent_id": "apm", "description": "APM golden metrics", "tool_patterns": ["newrelic", "nrql"]},
        {"check_id": "apm_logs", "agent_id": "apm", "description": "APM log analysis", "tool_patterns": ["newrelic", "nrql", "log"]},
        {"check_id": "cw_alarms", "agent_id": "infrastructure", "description": "CloudWatch alarm states", "tool_patterns": ["cloudwatch", "describe-alarms"]},
    ],
}

# Keywords that map incident descriptions to types
INCIDENT_TYPE_KEYWORDS: dict[str, list[str]] = {
    "rds": ["rds", "database", "db", "aurora", "postgres", "mysql", "mariadb", "sql"],
    "ecs": ["ecs", "fargate", "container", "task"],
    "lambda": ["lambda", "serverless", "function"],
    "ec2": ["ec2", "instance", "server", "compute"],
}


class InvestigationPlan:
    """Tracks what checks are required and what's been done."""

    def __init__(self) -> None:
        self.required_checks: list[dict[str, Any]] = []
        self.findings: list[dict[str, Any]] = []
        self.missing_data: list[str] = []

    @classmethod
    def generate_plan(cls, incident_description: str) -> InvestigationPlan:
        """Generate a pre-populated plan based on the incident type."""
        plan = cls()
        lower = incident_description.lower()

        matched_type = "generic"
        for incident_type, keywords in INCIDENT_TYPE_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                matched_type = incident_type
                break

        template_checks = INCIDENT_TYPE_CHECKS.get(matched_type, INCIDENT_TYPE_CHECKS["generic"])
        for check_template in template_checks:
            plan.required_checks.append({
                **check_template,
                "status": "pending",
            })

        logger.info("Generated investigation plan: type=%s, checks=%d", matched_type, len(plan.required_checks))
        return plan

    def mark_check_complete(self, check_id: str, findings_summary: str) -> None:
        for check in self.required_checks:
            if check["check_id"] == check_id:
                check["status"] = "complete"
                break
        self.findings.append({"check_id": check_id, "summary": findings_summary})

    def mark_check_by_tool(self, tool_name: str, agent_id: str) -> None:
        """Mark checks as complete based on a tool that was called."""
        tool_lower = tool_name.lower()
        for check in self.required_checks:
            if check["status"] != "pending" or check["agent_id"] != agent_id:
                continue
            if any(pat in tool_lower for pat in check["tool_patterns"]):
                check["status"] = "complete"

    def get_incomplete_checks(self) -> list[dict[str, Any]]:
        return [c for c in self.required_checks if c["status"] == "pending"]

    def get_checks_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        return [c for c in self.required_checks if c["agent_id"] == agent_id and c["status"] == "pending"]

    def add_finding(self, agent_id: str, iteration: int, summary: str, severity: str = "info", evidence: str = "") -> None:
        self.findings.append({
            "agent_id": agent_id,
            "iteration": iteration,
            "summary": summary,
            "severity": severity,
            "evidence": evidence,
        })

    def build_status_summary(self) -> str:
        completed = [c for c in self.required_checks if c["status"] == "complete"]
        pending = self.get_incomplete_checks()

        lines = [f"**Investigation Plan:** {len(completed)}/{len(self.required_checks)} checks complete"]

        if completed:
            lines.append("\nCompleted:")
            for c in completed:
                lines.append(f"  - [done] {c['description']}")

        if pending:
            lines.append("\nPending:")
            for c in pending:
                lines.append(f"  - [TODO] {c['description']} (→ {c['agent_id']})")

        if self.missing_data:
            lines.append("\nMissing data:")
            for gap in self.missing_data:
                lines.append(f"  - {gap}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requiredChecks": self.required_checks,
            "findings": self.findings,
            "missingData": self.missing_data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InvestigationPlan:
        plan = cls()
        plan.required_checks = data.get("requiredChecks", [])
        plan.findings = data.get("findings", [])
        plan.missing_data = data.get("missingData", [])
        return plan


# ── Critical Signal Detection ─────────────────────────────────

CRITICAL_PATTERNS: dict[str, str] = {
    r"PG::QueryCanceled": "database_timeout",
    r"OOM|Out of memory|OutOfMemoryError": "memory_exhaustion",
    r"connection refused|connection timeout|ECONNREFUSED": "connectivity_failure",
    r"CPU.*9[0-9]%|CPU.*100%|CPUUtilization.*9[0-9]|CPUUtilization.*100": "cpu_saturation",
    r"deadlock|Deadlock": "deadlock",
    r"disk full|no space left|ENOSPC": "disk_exhaustion",
    r"throttl|rate.?limit|429": "throttling",
    r"max_connections|too many connections|connection pool exhausted": "connection_exhaustion",
}


class InvestigationContext:
    """Tracks investigation state across the agent loop.

    Extracts time windows, entity names, and key identifiers from tool results
    and injects them as context so the model uses consistent parameters.
    """

    def __init__(self) -> None:
        self.time_window: dict[str, Any] = {
            "description": "last 24 hours",
            "start": None,
            "end": None,
            "extracted": False,
        }
        self.entities: dict[str, list[str]] = {}
        self.identifiers = {
            "service_names": set[str](),
            "error_messages": set[str](),
            "transaction_names": set[str](),
            "hostnames": set[str](),
            "regions": set[str](),
        }
        self.resources: list[ResourceEntry] = []
        self.scoped_group_name: str | None = None
        self.investigation_plan: InvestigationPlan | None = None
        self.critical_signals: list[dict[str, Any]] = []
        self.correlation_summary: str | None = None
        self.pi_findings: list[dict[str, Any]] = []

    # ── Parse user message ──────────────────────────────────────

    def parse_user_message(self, message: str) -> None:
        lower = message.lower()
        now = datetime.now(timezone.utc)

        m = re.search(r"last\s+(\d+)\s+hour", lower)
        if m:
            hours = int(m.group(1))
            start = now - timedelta(hours=hours)
            self.time_window = {"description": f"last {hours} hours", "start": start, "end": now, "extracted": True}
            return

        m = re.search(r"last\s+(\d+)\s+day", lower)
        if m:
            days = int(m.group(1))
            start = now - timedelta(days=days)
            self.time_window = {"description": f"last {days} days", "start": start, "end": now, "extracted": True}
            return

        m = re.search(r"last\s+(\d+)\s+min", lower)
        if m:
            mins = int(m.group(1))
            start = now - timedelta(minutes=mins)
            self.time_window = {"description": f"last {mins} minutes", "start": start, "end": now, "extracted": True}
            return

        if re.search(r"today|last 24|past 24", lower):
            start = now - timedelta(hours=24)
            self.time_window = {"description": "last 24 hours", "start": start, "end": now, "extracted": True}
            return

        if re.search(r"this week|past week|last 7|last week", lower):
            start = now - timedelta(days=7)
            self.time_window = {"description": "last 7 days", "start": start, "end": now, "extracted": True}
            return

        if re.search(r"this month|past month|last 30", lower):
            start = now - timedelta(days=30)
            self.time_window = {"description": "last 30 days", "start": start, "end": now, "extracted": True}
            return

        # Default: last 24 hours
        start = now - timedelta(hours=24)
        self.time_window = {"description": "last 24 hours", "start": start, "end": now, "extracted": False}

    # ── Extract from tool results ───────────────────────────────

    def extract_from_tool_result(
        self,
        tool_name: str,
        integration: str | None,
        args: dict[str, Any],
        result: Any,
    ) -> None:
        result_str = result if isinstance(result, str) else __import__("json").dumps(result)

        self._extract_resources(tool_name, integration, result_str)

        # Extract Performance Insights findings
        if integration == "aws":
            self._extract_pi_findings(tool_name, args, result_str)

        # AWS regions
        if integration == "aws":
            if isinstance(args.get("region"), str):
                self.identifiers["regions"].add(args["region"])
            for r in re.findall(
                r"(?:us|eu|ap|sa|ca|me|af)-(?:east|west|north|south|central|northeast|southeast|northwest|southwest)-\d",
                result_str,
            ):
                self.identifiers["regions"].add(r)

        # Hostnames
        for h in re.findall(
            r"(?:ip-[\d-]+|i-[0-9a-f]{8,17}|[\w-]+\.(?:compute|ec2)\.amazonaws\.com)",
            result_str,
        )[:5]:
            self.identifiers["hostnames"].add(h)

        # PagerDuty incident timestamps
        if integration == "pagerduty":
            m = re.search(r'"created_at"\s*:\s*"([^"]+)"', result_str)
            if m:
                try:
                    incident_time = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                    # Normalize to UTC
                    incident_time = incident_time.astimezone(timezone.utc)
                    start = incident_time - timedelta(hours=1)
                    end = incident_time + timedelta(hours=1)
                    self.time_window = {
                        "description": f"around incident time {incident_time.strftime('%Y-%m-%d %H:%M:%S')} UTC",
                        "start": start,
                        "end": end,
                        "extracted": True,
                    }
                except (ValueError, TypeError):
                    pass

        # Entity names
        if integration == "newrelic":
            for m in re.findall(r'"appName"\s*:\s*"([^"]+)"', result_str):
                names = self.entities.setdefault("newrelic", [])
                if m not in names:
                    names.append(m)
                self.identifiers["service_names"].add(m)

        if integration == "sentry":
            for m in re.findall(r'"slug"\s*:\s*"([^"]+)"', result_str):
                slugs = self.entities.setdefault("sentry", [])
                if m not in slugs:
                    slugs.append(m)

        if integration == "pagerduty":
            for m in re.findall(r'"name"\s*:\s*"([^"]+)"', result_str):
                if 2 < len(m) < 60:
                    names = self.entities.setdefault("pagerduty", [])
                    if m not in names:
                        names.append(m)

        # Error messages
        for m in re.findall(r'"(?:error|message|title)"\s*:\s*"([^"]{10,120})"', result_str)[:3]:
            self.identifiers["error_messages"].add(m)

        # Transaction names
        if integration == "newrelic":
            for m in re.findall(r'"name"\s*:\s*"(WebTransaction[^"]+)"', result_str)[:5]:
                self.identifiers["transaction_names"].add(m)

    # ── Resource extraction ─────────────────────────────────────

    def _extract_resources(self, tool_name: str, _integration: str | None, result_str: str) -> None:
        import json

        try:
            data = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            return

        # RDS instances
        if isinstance(data.get("DBInstances"), list):
            for db in data["DBInstances"]:
                self._add_resource(ResourceEntry(
                    name=db.get("DBInstanceIdentifier", ""),
                    id=db.get("DbiResourceId"),
                    type="rds",
                    attrs={
                        "engine": f"{db.get('Engine', '')}/{db.get('EngineVersion', '')}",
                        "status": db.get("DBInstanceStatus", ""),
                        "class": db.get("DBInstanceClass", ""),
                        "endpoint": (db.get("Endpoint") or {}).get("Address", ""),
                        "piEnabled": str(db.get("PerformanceInsightsEnabled", False)),
                    },
                ))

        # EC2 instances
        if isinstance(data.get("Reservations"), list):
            for res in data["Reservations"]:
                for inst in res.get("Instances", []):
                    name_tag = None
                    for t in inst.get("Tags", []):
                        if t.get("Key") == "Name":
                            name_tag = t.get("Value")
                    self._add_resource(ResourceEntry(
                        name=name_tag or inst.get("InstanceId", ""),
                        id=inst.get("InstanceId"),
                        type="ec2",
                        attrs={
                            "state": (inst.get("State") or {}).get("Name", ""),
                            "instanceType": inst.get("InstanceType", ""),
                            "az": (inst.get("Placement") or {}).get("AvailabilityZone", ""),
                        },
                    ))

        # Lambda functions
        if isinstance(data.get("Functions"), list):
            for fn in data["Functions"]:
                self._add_resource(ResourceEntry(
                    name=fn.get("FunctionName", ""),
                    id=fn.get("FunctionArn"),
                    type="lambda",
                    attrs={
                        "runtime": fn.get("Runtime", ""),
                        "memory": str(fn.get("MemorySize", "")),
                    },
                ))

        # CloudWatch log groups
        if isinstance(data.get("logGroups"), list):
            for lg in data["logGroups"]:
                self._add_resource(ResourceEntry(
                    name=lg.get("logGroupName", ""),
                    type="log_group",
                    attrs={"retentionDays": str(lg.get("retentionInDays", "never"))},
                ))

        # New Relic entities
        if tool_name == "nr_list_entities" and isinstance(data.get("entities"), list):
            for ent in data["entities"]:
                self._add_resource(ResourceEntry(
                    name=ent.get("name", ""),
                    id=ent.get("guid"),
                    type="newrelic_app",
                    attrs={
                        "entityType": ent.get("entityType") or ent.get("type", ""),
                        "language": ent.get("language", ""),
                        "reporting": str(ent.get("reporting", "")),
                    },
                ))

        # New Relic golden metrics entity
        if tool_name == "nr_get_entity_golden_metrics" and isinstance(data.get("entity"), dict):
            ent = data["entity"]
            self._add_resource(ResourceEntry(
                name=ent.get("name", ""),
                id=ent.get("guid"),
                type="newrelic_app",
                attrs={"entityType": ent.get("entityType", "")},
            ))

        # Sentry projects
        self._extract_sentry_resources(tool_name, result_str)

    def _extract_sentry_resources(self, tool_name: str, result_str: str) -> None:
        import json

        if not re.search(r"list_project|list_issue|get_issue|search_issue|get_project", tool_name, re.IGNORECASE):
            return

        try:
            outer = json.loads(result_str)
            project_data: list[Any] = []

            if isinstance(outer, dict) and isinstance(outer.get("content"), list):
                for block in outer["content"]:
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        try:
                            inner = json.loads(block["text"])
                            if isinstance(inner, list):
                                project_data.extend(inner)
                            elif isinstance(inner, dict):
                                project_data.append(inner)
                        except (json.JSONDecodeError, TypeError):
                            pass

            if isinstance(outer, list):
                project_data = outer

            for item in project_data:
                slug = item.get("slug") or item.get("project_slug")
                if slug:
                    self._add_resource(ResourceEntry(
                        name=slug,
                        id=str(item["id"]) if item.get("id") else None,
                        type="sentry_project",
                        attrs={
                            "name": item.get("name", ""),
                            "platform": item.get("platform", ""),
                            "status": item.get("status", ""),
                        },
                    ))

                proj = item.get("project")
                if isinstance(proj, dict) and proj.get("slug"):
                    self._add_resource(ResourceEntry(
                        name=proj["slug"],
                        id=str(proj["id"]) if proj.get("id") else None,
                        type="sentry_project",
                        attrs={
                            "name": proj.get("name", ""),
                            "platform": proj.get("platform", ""),
                        },
                    ))

        except (json.JSONDecodeError, TypeError):
            for m in re.findall(r'"(?:slug|project_slug)"\s*:\s*"([^"]+)"', result_str):
                self._add_resource(ResourceEntry(name=m, type="sentry_project"))

    def _extract_pi_findings(self, tool_name: str, args: dict[str, Any], result_str: str) -> None:
        """Extract Performance Insights findings (top SQL, load metrics) from PI tool results."""
        import json

        tool_lower = tool_name.lower()
        is_dimension_keys = "describe_dimension_keys" in tool_lower or "describe-dimension-keys" in tool_lower
        is_resource_metrics = "get_resource_metrics" in tool_lower or "get-resource-metrics" in tool_lower

        if not is_dimension_keys and not is_resource_metrics:
            return

        try:
            data = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            return

        if not isinstance(data, dict):
            return

        if is_dimension_keys:
            # Extract top SQL queries with their load values from Keys[]
            keys = data.get("Keys", [])
            if not keys:
                return
            finding: dict[str, Any] = {
                "type": "top_sql_queries",
                "source_tool": tool_name,
                "metric": args.get("Metric", "db.load"),
                "queries": [],
            }
            for key in keys[:10]:  # Top 10 queries
                dimensions = key.get("Dimensions", {})
                total = key.get("Total")
                sql = (
                    dimensions.get("db.sql_tokenized.statement")
                    or dimensions.get("db.sql.statement")
                    or dimensions.get("db.sql_tokenized.id")
                    or str(dimensions)
                )
                entry = {"sql": sql, "load": total}
                if dimensions.get("db.sql_tokenized.id"):
                    entry["sql_id"] = dimensions["db.sql_tokenized.id"]
                finding["queries"].append(entry)
            if finding["queries"]:
                self.pi_findings.append(finding)
                logger.info(
                    "Extracted %d PI top SQL queries (highest load: %.2f)",
                    len(finding["queries"]),
                    finding["queries"][0].get("load", 0) or 0,
                )

        elif is_resource_metrics:
            # Extract peak/avg load from MetricList[].DataPoints[]
            metric_list = data.get("MetricList", [])
            if not metric_list:
                return
            finding = {
                "type": "resource_metrics",
                "source_tool": tool_name,
                "metrics": [],
            }
            for metric_entry in metric_list:
                metric_key = metric_entry.get("Key", {})
                data_points = metric_entry.get("DataPoints", [])
                if not data_points:
                    continue
                values = [dp.get("Value", 0) for dp in data_points if dp.get("Value") is not None]
                if values:
                    finding["metrics"].append({
                        "metric": metric_key.get("Metric", ""),
                        "group_by": metric_key.get("Dimensions", {}),
                        "peak": max(values),
                        "avg": sum(values) / len(values),
                        "data_points": len(values),
                    })
            if finding["metrics"]:
                self.pi_findings.append(finding)

    def _add_resource(self, entry: ResourceEntry) -> None:
        if not entry.name:
            return
        for i, r in enumerate(self.resources):
            if r.type == entry.type and r.name == entry.name:
                self.resources[i] = ResourceEntry(
                    name=entry.name,
                    type=entry.type,
                    id=entry.id or r.id,
                    attrs={**r.attrs, **entry.attrs},
                )
                return
        self.resources.append(entry)

    # ── Investigation plan ────────────────────────────────────────

    def generate_investigation_plan(self, incident_description: str) -> None:
        """Generate and attach an investigation plan based on the incident description."""
        self.investigation_plan = InvestigationPlan.generate_plan(incident_description)

    # ── Critical signals ───────────────────────────────────────

    def add_critical_signal(self, signal: dict[str, Any]) -> None:
        """Add a critical signal detected from tool output."""
        # Deduplicate by pattern + category
        for existing in self.critical_signals:
            if existing["category"] == signal["category"] and existing.get("match") == signal.get("match"):
                return
        self.critical_signals.append(signal)

    def build_evidence_brief(self) -> str | None:
        """Summarize all critical signals grouped by category."""
        if not self.critical_signals:
            return None

        by_category: dict[str, list[dict[str, Any]]] = {}
        for sig in self.critical_signals:
            by_category.setdefault(sig["category"], []).append(sig)

        lines = ["**Critical Signals Detected:**"]
        for category, signals in by_category.items():
            lines.append(f"\n_{category}_:")
            for sig in signals:
                source = sig.get("source_tool", "unknown")
                agent = sig.get("agent_id", "unknown")
                match_text = sig.get("match", "")
                lines.append(f"  - [{agent}/{source}] {match_text}")

        return "\n".join(lines)

    # ── Context message for model ───────────────────────────────

    def build_context_message(self) -> str | None:
        parts: list[str] = []

        start = self.time_window.get("start")
        end = self.time_window.get("end")

        if start and end:
            # Normalize to UTC for consistency
            utc_start = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
            utc_end = end.astimezone(timezone.utc) if end.tzinfo else end.replace(tzinfo=timezone.utc)
            padded_start = utc_start - timedelta(minutes=30)
            window_ms = (utc_end - utc_start).total_seconds() * 1000
            is_broad = window_ms > 3 * 24 * 60 * 60 * 1000

            # Format as UTC strings (no timezone offset — safe for all query languages)
            start_utc_str = utc_start.strftime("%Y-%m-%d %H:%M:%S")
            end_utc_str = utc_end.strftime("%Y-%m-%d %H:%M:%S")
            padded_start_utc_str = padded_start.strftime("%Y-%m-%d %H:%M:%S")

            # Calculate NRQL-safe relative time (hours ago from now)
            now = datetime.now(timezone.utc)
            hours_since_start = (now - utc_start).total_seconds() / 3600
            hours_since_end = (now - utc_end).total_seconds() / 3600

            # Epoch milliseconds for AWS CloudWatch
            start_epoch_ms = int(utc_start.timestamp() * 1000)
            end_epoch_ms = int(utc_end.timestamp() * 1000)

            parts.append(
                f"**Investigation time window:** {self.time_window['description']}\n"
                f"  start (UTC): {start_utc_str}\n"
                f"  end (UTC):   {end_utc_str}\n"
                f"  padded_start (UTC, -30min): {padded_start_utc_str}\n"
                f"\n"
                f"  **NRQL time (use these EXACTLY):**\n"
                f"    Absolute: SINCE '{padded_start_utc_str}' UNTIL '{end_utc_str}'\n"
                f"    Relative: SINCE {int(hours_since_start) + 1} hours ago UNTIL {max(0, int(hours_since_end))} hours ago\n"
                f"\n"
                f"  **AWS CloudWatch epoch ms:**\n"
                f"    startTime: {start_epoch_ms}\n"
                f"    endTime: {end_epoch_ms}"
            )
            if is_broad:
                parts.append(
                    "⚠️ This is a broad time window. Start with it to identify spikes/clusters, "
                    "then narrow to the specific time range where the problem is concentrated."
                )
        else:
            parts.append(f"**Investigation time window:** {self.time_window['description']}")

        if self.entities:
            parts.append("\n**Discovered entities (use the correct name for each platform):**")
            for platform, names in self.entities.items():
                parts.append(f"- {platform}: {', '.join(names)}")

        ids: list[str] = []
        if self.identifiers["regions"]:
            ids.append(f"AWS regions: {', '.join(self.identifiers['regions'])}")
        if self.identifiers["service_names"]:
            ids.append(f"Service names: {', '.join(list(self.identifiers['service_names'])[:5])}")
        if self.identifiers["error_messages"]:
            ids.append(f"Error messages: {' | '.join(list(self.identifiers['error_messages'])[:3])}")
        if self.identifiers["transaction_names"]:
            ids.append(f"Transactions: {', '.join(list(self.identifiers['transaction_names'])[:3])}")
        if self.identifiers["hostnames"]:
            ids.append(f"Hostnames: {', '.join(list(self.identifiers['hostnames'])[:3])}")

        if ids:
            parts.append("\n**Extracted identifiers:**")
            for id_str in ids:
                parts.append(f"- {id_str}")

        if self.resources:
            parts.append("\n**Discovered Resources (use these exact names/IDs — do NOT re-discover):**")
            by_type: dict[str, list[ResourceEntry]] = {}
            for r in self.resources:
                by_type.setdefault(r.type, []).append(r)
            for type_name, entries in by_type.items():
                parts.append(f"\n_{type_name}:_")
                for r in entries:
                    id_part = f" (id: {r.id})" if r.id else ""
                    attr_parts = ", ".join(f"{k}={v}" for k, v in r.attrs.items() if v)
                    parts.append(f"- {r.name}{id_part}{f' [{attr_parts}]' if attr_parts else ''}")

        if self.pi_findings:
            parts.append("\n**Performance Insights Data (REAL — cite these exactly, do NOT fabricate SQL):**")
            for finding in self.pi_findings:
                if finding["type"] == "top_sql_queries":
                    parts.append(f"  _Top SQL by {finding.get('metric', 'db.load')} (from {finding['source_tool']}):_")
                    for i, q in enumerate(finding.get("queries", []), 1):
                        load_str = f" (load: {q['load']:.2f})" if q.get("load") is not None else ""
                        parts.append(f"  {i}. `{q['sql']}`{load_str}")
                elif finding["type"] == "resource_metrics":
                    for m in finding.get("metrics", []):
                        parts.append(
                            f"  - {m['metric']}: peak={m['peak']:.2f}, avg={m['avg']:.2f} "
                            f"({m['data_points']} data points)"
                        )

        if self.investigation_plan:
            parts.append(f"\n{self.investigation_plan.build_status_summary()}")

        evidence = self.build_evidence_brief()
        if evidence:
            parts.append(f"\n{evidence}")

        if self.correlation_summary:
            parts.append(f"\n**Cross-Agent Correlation:**\n{self.correlation_summary}")

        return "\n".join(parts)

    # ── Serialization ───────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "timeWindow": {
                "description": self.time_window["description"],
                "start": self.time_window["start"].isoformat() if self.time_window.get("start") else None,
                "end": self.time_window["end"].isoformat() if self.time_window.get("end") else None,
                "extracted": self.time_window.get("extracted", False),
            },
            "entities": self.entities,
            "identifiers": {
                "serviceNames": list(self.identifiers["service_names"]),
                "errorMessages": list(self.identifiers["error_messages"]),
                "transactionNames": list(self.identifiers["transaction_names"]),
                "hostnames": list(self.identifiers["hostnames"]),
                "regions": list(self.identifiers["regions"]),
            },
            "resources": [r.to_dict() for r in self.resources],
        }
        if self.scoped_group_name:
            d["scopedGroupName"] = self.scoped_group_name
        if self.investigation_plan:
            d["investigationPlan"] = self.investigation_plan.to_dict()
        if self.critical_signals:
            d["criticalSignals"] = self.critical_signals
        if self.correlation_summary:
            d["correlationSummary"] = self.correlation_summary
        if self.pi_findings:
            d["piFindings"] = self.pi_findings
        return d

    @classmethod
    def from_dict(cls, snapshot: dict[str, Any]) -> InvestigationContext:
        ctx = cls()
        tw = snapshot.get("timeWindow", {})

        def _parse_utc(val: str | None) -> datetime | None:
            if not val:
                return None
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        ctx.time_window = {
            "description": tw.get("description", "last 24 hours"),
            "start": _parse_utc(tw.get("start")),
            "end": _parse_utc(tw.get("end")),
            "extracted": tw.get("extracted", False),
        }
        ctx.entities = snapshot.get("entities", {})
        ids = snapshot.get("identifiers", {})
        ctx.identifiers = {
            "service_names": set(ids.get("serviceNames", [])),
            "error_messages": set(ids.get("errorMessages", [])),
            "transaction_names": set(ids.get("transactionNames", [])),
            "hostnames": set(ids.get("hostnames", [])),
            "regions": set(ids.get("regions", [])),
        }
        ctx.resources = [ResourceEntry.from_dict(r) for r in snapshot.get("resources", [])]
        ctx.scoped_group_name = snapshot.get("scopedGroupName")
        if snapshot.get("investigationPlan"):
            ctx.investigation_plan = InvestigationPlan.from_dict(snapshot["investigationPlan"])
        ctx.critical_signals = snapshot.get("criticalSignals", [])
        ctx.correlation_summary = snapshot.get("correlationSummary")
        ctx.pi_findings = snapshot.get("piFindings", [])
        return ctx

    def parse_all_user_messages(self, messages: list[dict[str, str]]) -> None:
        for msg in messages:
            if msg.get("role") != "user":
                continue
            probe = InvestigationContext()
            probe.parse_user_message(msg.get("content", ""))
            if probe.time_window.get("extracted"):
                self.time_window = probe.time_window

    def scope_to_resources(self, resource_names: list[str], group_name: str) -> None:
        name_set = {n.lower() for n in resource_names}
        self.resources = [r for r in self.resources if r.name.lower() in name_set]

        for platform in list(self.entities.keys()):
            filtered = [n for n in self.entities[platform] if n.lower() in name_set]
            if filtered:
                self.entities[platform] = filtered
            else:
                del self.entities[platform]

        self.identifiers["service_names"] = {
            n for n in self.identifiers["service_names"] if n.lower() in name_set
        }
        self.scoped_group_name = group_name
