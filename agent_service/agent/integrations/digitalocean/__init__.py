"""DigitalOcean custom tools — REST API v2 integration."""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..tool_registry import ToolDefinition, tool_registry

DO_BASE = "https://api.digitalocean.com/v2"


def get_digitalocean_credentials(settings: dict[str, Any], instance: int = 0) -> dict[str, str]:
    api_token = settings.get(f"digitalocean.{instance}.api_token")
    if not api_token:
        raise ValueError("DigitalOcean API token not configured.")
    return {"api_token": api_token}


def _do_headers(settings: dict[str, Any]) -> dict[str, str]:
    creds = get_digitalocean_credentials(settings)
    return {
        "Authorization": f"Bearer {creds['api_token']}",
        "Content-Type": "application/json",
    }


# ── Droplets ─────────────────────────────────────────────────────


async def _do_list_droplets(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    params: dict[str, Any] = {"per_page": args.get("per_page", 50)}
    if args.get("page"):
        params["page"] = args["page"]
    if args.get("tag_name"):
        params["tag_name"] = args["tag_name"]
    if args.get("name"):
        params["name"] = args["name"]

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/droplets", headers=headers, params=params, timeout=30)
        resp.raise_for_status()

    droplets = resp.json().get("droplets", [])
    return {"title": f"Droplets ({len(droplets)})", "output": json.dumps({"droplets": droplets}, indent=2)}


async def _do_get_droplet(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/droplets/{args['droplet_id']}", headers=headers, timeout=30)
        resp.raise_for_status()
    return {"title": f"Droplet: {args['droplet_id']}", "output": json.dumps(resp.json().get("droplet", {}), indent=2)}


# ── Droplet Metrics ──────────────────────────────────────────────


async def _do_get_droplet_cpu(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    params = {"host_id": args["host_id"], "start": args["start"], "end": args["end"]}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/monitoring/metrics/droplet/cpu", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
    return {"title": f"CPU Metrics: {args['host_id']}", "output": json.dumps(resp.json().get("data", {}), indent=2)}


async def _do_get_droplet_memory_free(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    params = {"host_id": args["host_id"], "start": args["start"], "end": args["end"]}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/monitoring/metrics/droplet/memory_free", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
    return {"title": f"Memory Free: {args['host_id']}", "output": json.dumps(resp.json().get("data", {}), indent=2)}


async def _do_get_droplet_bandwidth(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    params = {
        "host_id": args["host_id"],
        "start": args["start"],
        "end": args["end"],
        "interface": args.get("interface", "public"),
        "direction": args.get("direction", "inbound"),
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/monitoring/metrics/droplet/bandwidth", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
    return {"title": f"Bandwidth: {args['host_id']}", "output": json.dumps(resp.json().get("data", {}), indent=2)}


# ── Databases ────────────────────────────────────────────────────


async def _do_list_databases(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/databases", headers=headers, timeout=30)
        resp.raise_for_status()
    databases = resp.json().get("databases", [])
    return {"title": f"Databases ({len(databases)})", "output": json.dumps({"databases": databases}, indent=2)}


async def _do_get_database(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/databases/{args['database_id']}", headers=headers, timeout=30)
        resp.raise_for_status()
    return {"title": f"Database: {args['database_id']}", "output": json.dumps(resp.json().get("database", {}), indent=2)}


# ── Load Balancers ───────────────────────────────────────────────


async def _do_list_load_balancers(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/load_balancers", headers=headers, timeout=30)
        resp.raise_for_status()
    lbs = resp.json().get("load_balancers", [])
    return {"title": f"Load Balancers ({len(lbs)})", "output": json.dumps({"load_balancers": lbs}, indent=2)}


async def _do_get_load_balancer(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/load_balancers/{args['load_balancer_id']}", headers=headers, timeout=30)
        resp.raise_for_status()
    return {"title": f"Load Balancer: {args['load_balancer_id']}", "output": json.dumps(resp.json().get("load_balancer", {}), indent=2)}


# ── Firewalls ────────────────────────────────────────────────────


async def _do_list_firewalls(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/firewalls", headers=headers, timeout=30)
        resp.raise_for_status()
    firewalls = resp.json().get("firewalls", [])
    return {"title": f"Firewalls ({len(firewalls)})", "output": json.dumps({"firewalls": firewalls}, indent=2)}


# ── Kubernetes ───────────────────────────────────────────────────


async def _do_list_kubernetes_clusters(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/kubernetes/clusters", headers=headers, timeout=30)
        resp.raise_for_status()
    clusters = resp.json().get("kubernetes_clusters", [])
    return {"title": f"Kubernetes Clusters ({len(clusters)})", "output": json.dumps({"kubernetes_clusters": clusters}, indent=2)}


async def _do_get_kubernetes_cluster(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/kubernetes/clusters/{args['cluster_id']}", headers=headers, timeout=30)
        resp.raise_for_status()
    return {"title": f"Kubernetes Cluster: {args['cluster_id']}", "output": json.dumps(resp.json().get("kubernetes_cluster", {}), indent=2)}


# ── Monitoring Alerts ────────────────────────────────────────────


async def _do_list_alert_policies(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/monitoring/alerts", headers=headers, timeout=30)
        resp.raise_for_status()
    policies = resp.json().get("policies", [])
    return {"title": f"Alert Policies ({len(policies)})", "output": json.dumps({"policies": policies}, indent=2)}


# ── Domains ──────────────────────────────────────────────────────


async def _do_list_domains(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/domains", headers=headers, timeout=30)
        resp.raise_for_status()
    domains = resp.json().get("domains", [])
    return {"title": f"Domains ({len(domains)})", "output": json.dumps({"domains": domains}, indent=2)}


# ── Volumes ──────────────────────────────────────────────────────


async def _do_list_volumes(args: dict, ctx: Any) -> dict:
    headers = _do_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DO_BASE}/volumes", headers=headers, timeout=30)
        resp.raise_for_status()
    volumes = resp.json().get("volumes", [])
    return {"title": f"Volumes ({len(volumes)})", "output": json.dumps({"volumes": volumes}, indent=2)}


# ── Tool Definitions ─────────────────────────────────────────────

_METRICS_PARAMS = {
    "type": "object",
    "properties": {
        "host_id": {"type": "string", "description": "Droplet ID"},
        "start": {"type": "string", "description": "Start timestamp (Unix epoch seconds)"},
        "end": {"type": "string", "description": "End timestamp (Unix epoch seconds)"},
    },
    "required": ["host_id", "start", "end"],
}

DIGITALOCEAN_TOOLS = [
    ToolDefinition(
        name="do_list_droplets",
        description="List all DigitalOcean Droplets with status, IPs, region, size, tags, and VPC info.",
        parameters={
            "type": "object",
            "properties": {
                "per_page": {"type": "number", "description": "Results per page (default 50)"},
                "page": {"type": "number", "description": "Page number"},
                "tag_name": {"type": "string", "description": "Filter by tag"},
                "name": {"type": "string", "description": "Filter by name"},
            },
        },
        execute=_do_list_droplets,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_get_droplet",
        description="Get details of a specific Droplet by ID, including status, IPs, region, size, volumes, and tags.",
        parameters={
            "type": "object",
            "properties": {"droplet_id": {"type": "string", "description": "Droplet ID"}},
            "required": ["droplet_id"],
        },
        execute=_do_get_droplet,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_get_droplet_cpu",
        description="Get CPU utilization metrics for a Droplet. Requires host_id (Droplet ID) and Unix epoch start/end timestamps.",
        parameters=_METRICS_PARAMS,
        execute=_do_get_droplet_cpu,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_get_droplet_memory_free",
        description="Get free memory metrics for a Droplet. Requires host_id (Droplet ID) and Unix epoch start/end timestamps.",
        parameters=_METRICS_PARAMS,
        execute=_do_get_droplet_memory_free,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_get_droplet_bandwidth",
        description="Get bandwidth metrics for a Droplet (public/private, inbound/outbound). Requires host_id and Unix epoch timestamps.",
        parameters={
            "type": "object",
            "properties": {
                "host_id": {"type": "string", "description": "Droplet ID"},
                "start": {"type": "string", "description": "Start timestamp (Unix epoch seconds)"},
                "end": {"type": "string", "description": "End timestamp (Unix epoch seconds)"},
                "interface": {"type": "string", "description": "Network interface: public or private (default: public)"},
                "direction": {"type": "string", "description": "Traffic direction: inbound or outbound (default: inbound)"},
            },
            "required": ["host_id", "start", "end"],
        },
        execute=_do_get_droplet_bandwidth,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_list_databases",
        description="List all DigitalOcean managed databases with engine, status, region, and connection details.",
        parameters={"type": "object", "properties": {}},
        execute=_do_list_databases,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_get_database",
        description="Get details of a specific managed database by ID.",
        parameters={
            "type": "object",
            "properties": {"database_id": {"type": "string", "description": "Database cluster ID"}},
            "required": ["database_id"],
        },
        execute=_do_get_database,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_list_load_balancers",
        description="List all DigitalOcean load balancers with health checks, forwarding rules, and Droplet pools.",
        parameters={"type": "object", "properties": {}},
        execute=_do_list_load_balancers,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_get_load_balancer",
        description="Get details of a specific load balancer by ID.",
        parameters={
            "type": "object",
            "properties": {"load_balancer_id": {"type": "string", "description": "Load Balancer ID"}},
            "required": ["load_balancer_id"],
        },
        execute=_do_get_load_balancer,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_list_firewalls",
        description="List all DigitalOcean firewalls with rules and associated Droplets.",
        parameters={"type": "object", "properties": {}},
        execute=_do_list_firewalls,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_list_kubernetes_clusters",
        description="List all DigitalOcean Kubernetes (DOKS) clusters with node pools, version, and status.",
        parameters={"type": "object", "properties": {}},
        execute=_do_list_kubernetes_clusters,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_get_kubernetes_cluster",
        description="Get details of a specific Kubernetes cluster by ID.",
        parameters={
            "type": "object",
            "properties": {"cluster_id": {"type": "string", "description": "Kubernetes cluster ID"}},
            "required": ["cluster_id"],
        },
        execute=_do_get_kubernetes_cluster,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_list_alert_policies",
        description="List all DigitalOcean monitoring alert policies with thresholds and targets.",
        parameters={"type": "object", "properties": {}},
        execute=_do_list_alert_policies,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_list_domains",
        description="List all DigitalOcean domains.",
        parameters={"type": "object", "properties": {}},
        execute=_do_list_domains,
        category="digitalocean",
    ),
    ToolDefinition(
        name="do_list_volumes",
        description="List all DigitalOcean block storage volumes with size, status, and attached Droplets.",
        parameters={"type": "object", "properties": {}},
        execute=_do_list_volumes,
        category="digitalocean",
    ),
]

DIGITALOCEAN_TOOL_NAMES = [t.name for t in DIGITALOCEAN_TOOLS]


def register_digitalocean_tools() -> None:
    for tool in DIGITALOCEAN_TOOLS:
        tool_registry.define(tool)
