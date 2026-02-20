"""MCP client manager — connects to MCP servers and manages tool discovery."""

from __future__ import annotations

import contextlib
import logging
import os
import re
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

# Write/mutate tools the agent should never call (read-only investigation agent).
WRITE_TOOL_PATTERN = re.compile(
    r"^(create|update|delete|remove|set|add|edit|close|merge|assign|resolve|acknowledge|"
    r"snooze|put|post|fork|transfer|enable|disable|approve|reject|reopen|escalate|reassign|"
    r"archive|unarchive|lock|unlock|comment|reply|subscribe|unsubscribe|invite|revoke|push|"
    r"publish|deploy|trigger|run|execute|start|stop|restart|cancel|retry|dismiss|mute|unmute|"
    r"upload|write|insert|modify|patch|toggle|revert|reset|clear|purge|rename|move|copy|clone|"
    r"link|unlink|attach|detach)_"
)


class MCPServerConfig:
    def __init__(
        self,
        name: str,
        transport: str,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.name = name
        self.transport = transport
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.url = url
        self.headers = headers or {}


class MCPClientManager:
    """Manages MCP server connections and tool discovery.

    Settings are passed in from Rails (not read from DB).
    """

    def __init__(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._clients: dict[str, ClientSession] = {}
        self._tools: dict[str, dict[str, Any]] = {}  # tool_name -> {server, tool}
        self._exit_stack = contextlib.AsyncExitStack()

    def _get_instance_indices(self, integration: str) -> list[int]:
        """Find all configured instance indices for an integration."""
        prefix = f"{integration}."
        indices: set[int] = set()
        for key in self._settings:
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix):]
            parts = rest.split(".", 1)
            if len(parts) == 2 and parts[0].isdigit():
                indices.add(int(parts[0]))
        return sorted(indices)

    def _get_server_configs(self) -> list[MCPServerConfig]:
        configs: list[MCPServerConfig] = []

        # Sentry MCP Servers (stdio) — one per instance
        for idx in self._get_instance_indices("sentry"):
            sentry_token = self._settings.get(f"sentry.{idx}.auth_token")
            sentry_org = self._settings.get(f"sentry.{idx}.org")
            if sentry_token and sentry_org:
                name = f"sentry-{idx}" if idx > 0 else "sentry"
                configs.append(MCPServerConfig(
                    name=name,
                    transport="stdio",
                    command="npx",
                    args=["-y", "@sentry/mcp-server@latest"],
                    env={"SENTRY_ACCESS_TOKEN": sentry_token},
                ))

        # GitHub MCP Servers (stdio) — one per instance
        for idx in self._get_instance_indices("github"):
            github_token = self._settings.get(f"github.{idx}.token")
            if github_token:
                name = f"github-{idx}" if idx > 0 else "github"
                configs.append(MCPServerConfig(
                    name=name,
                    transport="stdio",
                    command="npx",
                    args=["-y", "@modelcontextprotocol/server-github"],
                    env={"GITHUB_PERSONAL_ACCESS_TOKEN": github_token},
                ))

        # PagerDuty MCP Servers (streamable-http) — one per instance
        for idx in self._get_instance_indices("pagerduty"):
            pagerduty_key = self._settings.get(f"pagerduty.{idx}.api_key")
            if pagerduty_key:
                name = f"pagerduty-{idx}" if idx > 0 else "pagerduty"
                configs.append(MCPServerConfig(
                    name=name,
                    transport="streamable-http",
                    url="https://mcp.pagerduty.com/mcp",
                    headers={"Authorization": f"Token token={pagerduty_key}"},
                ))

        return configs

    async def initialize(self) -> None:
        configs = self._get_server_configs()
        logger.info("Initializing %d MCP servers", len(configs))

        for config in configs:
            try:
                await self._connect_server(config)
            except Exception as e:
                logger.warning(
                    "Failed to connect to MCP server %s: %s — tools will not be available",
                    config.name,
                    e,
                )

        logger.info(
            "MCP servers initialized: %d connected, %d tools",
            len(self._clients),
            len(self._tools),
        )

    async def _connect_server(self, config: MCPServerConfig) -> None:
        if config.transport == "stdio":
            if not config.command:
                raise ValueError("stdio transport requires command")

            merged_env = {**os.environ, **config.env}
            server_params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=merged_env,
            )

            read_stream, write_stream = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

        elif config.transport == "streamable-http":
            if not config.url:
                raise ValueError("streamable-http transport requires url")

            read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
                streamablehttp_client(
                    config.url,
                    headers=config.headers,
                )
            )
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

        else:
            raise ValueError(f"Unknown transport: {config.transport}")

        # List and filter tools
        result = await session.list_tools()
        all_tools = result.tools

        tools = [
            t for t in all_tools
            if not WRITE_TOOL_PATTERN.match(t.name)
        ]

        dropped = len(all_tools) - len(tools)
        logger.info(
            "Connected to MCP server %s: %d tools registered (%d dropped)",
            config.name,
            len(tools),
            dropped,
        )

        self._clients[config.name] = session
        for tool in tools:
            self._tools[tool.name] = {"server": config.name, "tool": tool}

    def get_all_tools(self) -> list[Any]:
        return [t["tool"] for t in self._tools.values()]

    def get_tools_for_integrations(self, integrations: list[str]) -> list[Any]:
        return [
            t["tool"]
            for t in self._tools.values()
            if any(
                t["server"] == name or t["server"].startswith(f"{name}-")
                for name in integrations
            )
        ]

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        tool_info = self._tools.get(tool_name)
        if not tool_info:
            raise ValueError(f"Tool not found: {tool_name}")

        session = self._clients.get(tool_info["server"])
        if not session:
            raise ValueError(f"Client not connected for server: {tool_info['server']}")

        result = await session.call_tool(tool_name, arguments=args)

        # Extract text content from MCP response
        extracted: Any = result
        if hasattr(result, "content") and isinstance(result.content, list) and result.content:
            text_content = next(
                (c for c in result.content if hasattr(c, "type") and c.type == "text"),
                None,
            )
            if text_content and hasattr(text_content, "text"):
                import json
                try:
                    extracted = json.loads(text_content.text)
                except (json.JSONDecodeError, TypeError):
                    extracted = text_content.text

        if hasattr(result, "isError") and result.isError:
            error_text = extracted if isinstance(extracted, str) else str(extracted)
            raise RuntimeError(f"[{tool_info['server']}] {tool_name} error: {error_text}")

        return extracted

    def get_tool_server(self, tool_name: str) -> str | None:
        info = self._tools.get(tool_name)
        return info["server"] if info else None

    def get_stats(self) -> dict[str, Any]:
        stats: dict[str, int] = {}
        for tool_info in self._tools.values():
            stats[tool_info["server"]] = stats.get(tool_info["server"], 0) + 1
        return {
            "connectedServers": len(self._clients),
            "totalTools": len(self._tools),
            "toolsByServer": stats,
        }

    async def dispose(self) -> None:
        try:
            await self._exit_stack.aclose()
        except Exception as e:
            logger.warning("Error disposing MCP stack: %s", e)
        finally:
            self._clients.clear()
            self._tools.clear()
