"""Hetzner Cloud custom tools — direct REST API v1 tools."""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..tool_registry import ToolDefinition, tool_registry

HETZNER_BASE = "https://api.hetzner.cloud/v1"


def get_hetzner_credentials(settings: dict[str, Any], instance: int = 0) -> dict[str, str]:
    api_token = settings.get(f"hetzner.{instance}.api_token")
    if not api_token:
        raise ValueError("Hetzner API token not configured.")
    return {"api_token": api_token}


def _hetzner_headers(settings: dict[str, Any]) -> dict[str, str]:
    creds = get_hetzner_credentials(settings)
    return {
        "Authorization": f"Bearer {creds['api_token']}",
        "Content-Type": "application/json",
    }


# ── Servers ──────────────────────────────────────────────────────


async def _hetzner_list_servers(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    params: dict[str, Any] = {"per_page": args.get("per_page", 50)}
    if args.get("page"):
        params["page"] = args["page"]
    if args.get("status"):
        params["status"] = args["status"]
    if args.get("name"):
        params["name"] = args["name"]

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HETZNER_BASE}/servers", headers=headers, params=params, timeout=30)
        resp.raise_for_status()

    servers = resp.json().get("servers", [])
    return {"title": f"Hetzner Servers ({len(servers)})", "output": json.dumps({"servers": servers}, indent=2)}


async def _hetzner_get_server(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HETZNER_BASE}/servers/{args['server_id']}", headers=headers, timeout=30)
        resp.raise_for_status()
    return {"title": f"Server: {args['server_id']}", "output": json.dumps(resp.json().get("server", {}), indent=2)}


async def _hetzner_get_server_metrics(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    params: dict[str, Any] = {
        "type": args.get("type", "cpu,disk,network"),
        "start": args["start"],
        "end": args["end"],
    }
    if args.get("step"):
        params["step"] = args["step"]

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{HETZNER_BASE}/servers/{args['server_id']}/metrics",
            headers=headers, params=params, timeout=30,
        )
        resp.raise_for_status()
    return {"title": f"Server Metrics: {args['server_id']}", "output": json.dumps(resp.json().get("metrics", {}), indent=2)}


# ── Load Balancers ───────────────────────────────────────────────


async def _hetzner_list_load_balancers(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    params: dict[str, Any] = {"per_page": args.get("per_page", 50)}
    if args.get("name"):
        params["name"] = args["name"]

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HETZNER_BASE}/load_balancers", headers=headers, params=params, timeout=30)
        resp.raise_for_status()

    lbs = resp.json().get("load_balancers", [])
    return {"title": f"Hetzner Load Balancers ({len(lbs)})", "output": json.dumps({"load_balancers": lbs}, indent=2)}


async def _hetzner_get_load_balancer(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HETZNER_BASE}/load_balancers/{args['load_balancer_id']}", headers=headers, timeout=30)
        resp.raise_for_status()
    return {"title": f"Load Balancer: {args['load_balancer_id']}", "output": json.dumps(resp.json().get("load_balancer", {}), indent=2)}


async def _hetzner_get_load_balancer_metrics(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    params: dict[str, Any] = {
        "type": args.get("type", "requests_per_second,bandwidth.in,bandwidth.out,connections_per_second"),
        "start": args["start"],
        "end": args["end"],
    }
    if args.get("step"):
        params["step"] = args["step"]

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{HETZNER_BASE}/load_balancers/{args['load_balancer_id']}/metrics",
            headers=headers, params=params, timeout=30,
        )
        resp.raise_for_status()
    return {"title": f"LB Metrics: {args['load_balancer_id']}", "output": json.dumps(resp.json().get("metrics", {}), indent=2)}


# ── Firewalls ────────────────────────────────────────────────────


async def _hetzner_list_firewalls(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HETZNER_BASE}/firewalls", headers=headers, timeout=30)
        resp.raise_for_status()
    firewalls = resp.json().get("firewalls", [])
    return {"title": f"Hetzner Firewalls ({len(firewalls)})", "output": json.dumps({"firewalls": firewalls}, indent=2)}


# ── Networks ─────────────────────────────────────────────────────


async def _hetzner_list_networks(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HETZNER_BASE}/networks", headers=headers, timeout=30)
        resp.raise_for_status()
    networks = resp.json().get("networks", [])
    return {"title": f"Hetzner Networks ({len(networks)})", "output": json.dumps({"networks": networks}, indent=2)}


async def _hetzner_get_network(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HETZNER_BASE}/networks/{args['network_id']}", headers=headers, timeout=30)
        resp.raise_for_status()
    return {"title": f"Network: {args['network_id']}", "output": json.dumps(resp.json().get("network", {}), indent=2)}


# ── Volumes ──────────────────────────────────────────────────────


async def _hetzner_list_volumes(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    params: dict[str, Any] = {}
    if args.get("status"):
        params["status"] = args["status"]

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HETZNER_BASE}/volumes", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
    volumes = resp.json().get("volumes", [])
    return {"title": f"Hetzner Volumes ({len(volumes)})", "output": json.dumps({"volumes": volumes}, indent=2)}


# ── Floating IPs ─────────────────────────────────────────────────


async def _hetzner_list_floating_ips(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HETZNER_BASE}/floating_ips", headers=headers, timeout=30)
        resp.raise_for_status()
    ips = resp.json().get("floating_ips", [])
    return {"title": f"Hetzner Floating IPs ({len(ips)})", "output": json.dumps({"floating_ips": ips}, indent=2)}


# ── SSH Keys ─────────────────────────────────────────────────────


async def _hetzner_list_ssh_keys(args: dict, ctx: Any) -> dict:
    headers = _hetzner_headers(ctx.settings)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HETZNER_BASE}/ssh_keys", headers=headers, timeout=30)
        resp.raise_for_status()
    keys = resp.json().get("ssh_keys", [])
    return {"title": f"SSH Keys ({len(keys)})", "output": json.dumps({"ssh_keys": keys}, indent=2)}


# ── Tool Definitions ─────────────────────────────────────────────

HETZNER_TOOLS = [
    ToolDefinition(
        name="hetzner_list_servers",
        description="List all Hetzner Cloud servers with status, IPs, server type, datacenter, and labels.",
        parameters={
            "type": "object",
            "properties": {
                "per_page": {"type": "number", "description": "Results per page (default 50)"},
                "page": {"type": "number", "description": "Page number"},
                "status": {"type": "string", "description": "Filter by status: running, initializing, starting, stopping, off, deleting, migrating, rebuilding, unknown"},
                "name": {"type": "string", "description": "Filter by server name"},
            },
        },
        execute=_hetzner_list_servers,
        category="hetzner",
    ),
    ToolDefinition(
        name="hetzner_get_server",
        description="Get details of a specific Hetzner server by ID, including status, IPs, server type, datacenter, volumes, and labels.",
        parameters={
            "type": "object",
            "properties": {"server_id": {"type": "string", "description": "Server ID"}},
            "required": ["server_id"],
        },
        execute=_hetzner_get_server,
        category="hetzner",
    ),
    ToolDefinition(
        name="hetzner_get_server_metrics",
        description="Get metrics (CPU, disk, network) for a Hetzner server. Requires server_id and ISO 8601 start/end timestamps.",
        parameters={
            "type": "object",
            "properties": {
                "server_id": {"type": "string", "description": "Server ID"},
                "type": {"type": "string", "description": "Comma-separated metric types: cpu, disk, network (default: all three)"},
                "start": {"type": "string", "description": "Start timestamp in ISO 8601 (e.g. 2025-01-01T00:00:00Z)"},
                "end": {"type": "string", "description": "End timestamp in ISO 8601"},
                "step": {"type": "number", "description": "Resolution step in seconds (optional)"},
            },
            "required": ["server_id", "start", "end"],
        },
        execute=_hetzner_get_server_metrics,
        category="hetzner",
    ),
    ToolDefinition(
        name="hetzner_list_load_balancers",
        description="List all Hetzner load balancers with targets, services, health checks, and algorithm.",
        parameters={
            "type": "object",
            "properties": {
                "per_page": {"type": "number", "description": "Results per page (default 50)"},
                "name": {"type": "string", "description": "Filter by name"},
            },
        },
        execute=_hetzner_list_load_balancers,
        category="hetzner",
    ),
    ToolDefinition(
        name="hetzner_get_load_balancer",
        description="Get details of a specific Hetzner load balancer by ID.",
        parameters={
            "type": "object",
            "properties": {"load_balancer_id": {"type": "string", "description": "Load Balancer ID"}},
            "required": ["load_balancer_id"],
        },
        execute=_hetzner_get_load_balancer,
        category="hetzner",
    ),
    ToolDefinition(
        name="hetzner_get_load_balancer_metrics",
        description="Get metrics for a Hetzner load balancer (requests/sec, bandwidth, connections/sec). Requires ISO 8601 start/end timestamps.",
        parameters={
            "type": "object",
            "properties": {
                "load_balancer_id": {"type": "string", "description": "Load Balancer ID"},
                "type": {"type": "string", "description": "Comma-separated: requests_per_second, bandwidth.in, bandwidth.out, connections_per_second"},
                "start": {"type": "string", "description": "Start timestamp in ISO 8601"},
                "end": {"type": "string", "description": "End timestamp in ISO 8601"},
                "step": {"type": "number", "description": "Resolution step in seconds (optional)"},
            },
            "required": ["load_balancer_id", "start", "end"],
        },
        execute=_hetzner_get_load_balancer_metrics,
        category="hetzner",
    ),
    ToolDefinition(
        name="hetzner_list_firewalls",
        description="List all Hetzner firewalls with their rules and applied resources.",
        parameters={"type": "object", "properties": {}},
        execute=_hetzner_list_firewalls,
        category="hetzner",
    ),
    ToolDefinition(
        name="hetzner_list_networks",
        description="List all Hetzner private networks with subnets, routes, and attached servers.",
        parameters={"type": "object", "properties": {}},
        execute=_hetzner_list_networks,
        category="hetzner",
    ),
    ToolDefinition(
        name="hetzner_get_network",
        description="Get details of a specific Hetzner network by ID.",
        parameters={
            "type": "object",
            "properties": {"network_id": {"type": "string", "description": "Network ID"}},
            "required": ["network_id"],
        },
        execute=_hetzner_get_network,
        category="hetzner",
    ),
    ToolDefinition(
        name="hetzner_list_volumes",
        description="List all Hetzner volumes with size, status, and attached server.",
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: creating, available"},
            },
        },
        execute=_hetzner_list_volumes,
        category="hetzner",
    ),
    ToolDefinition(
        name="hetzner_list_floating_ips",
        description="List all Hetzner floating IPs with assignment status and location.",
        parameters={"type": "object", "properties": {}},
        execute=_hetzner_list_floating_ips,
        category="hetzner",
    ),
    ToolDefinition(
        name="hetzner_list_ssh_keys",
        description="List all SSH keys in the Hetzner project.",
        parameters={"type": "object", "properties": {}},
        execute=_hetzner_list_ssh_keys,
        category="hetzner",
    ),
]

HETZNER_TOOL_NAMES = [t.name for t in HETZNER_TOOLS]


def register_hetzner_tools() -> None:
    for tool in HETZNER_TOOLS:
        tool_registry.define(tool)
