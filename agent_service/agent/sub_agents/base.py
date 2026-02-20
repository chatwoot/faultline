"""Base sub-agent with tool execution loop."""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Literal

from openai import AsyncOpenAI

from ..integrations.mcp.client_manager import MCPClientManager
from ..integrations.tool_registry import tool_registry
from ..integrations.tool_context import ToolContext
from ..investigation_context import InvestigationContext

logger = logging.getLogger(__name__)

SubAgentType = Literal["apm", "error_monitoring", "infrastructure", "alerting"]

MAX_SUB_AGENT_ITERATIONS = 5
MAX_TOOL_RESULT_CHARS = 30_000


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

    def get_tools(self) -> list[dict[str, Any]]:
        own = self.get_own_tools()
        shared: list[dict[str, Any]] = []
        for integration in self.shared_integrations:
            if integration in self.config.enabled_integrations:
                shared.extend(self.get_mcp_tools(integration))
        return [*own, *shared]

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
        tools = self.get_tools()
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

        for iteration in range(MAX_SUB_AGENT_ITERATIONS):
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

            # No function calls → done
            if not function_calls:
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

                if len(content) > MAX_TOOL_RESULT_CHARS:
                    content = (
                        content[:MAX_TOOL_RESULT_CHARS]
                        + f"\n\n[... truncated {len(content) - MAX_TOOL_RESULT_CHARS} chars]"
                    )

                tw_start = ctx.time_window.get("start")
                tw_end = ctx.time_window.get("end")
                if tw_start and tw_end:
                    content += f"\n\n[Investigation window: {tw_start.isoformat()} to {tw_end.isoformat()}]"

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

            except Exception as e:
                execution_time = int((time.time() - start_time) * 1000)
                input_items.append({
                    "type": "function_call_output",
                    "call_id": fc["call_id"],
                    "output": f"TOOL ERROR — {tool_name} failed: {e}\n\nRetry with different parameters, or use an alternative tool.",
                })
                self._emit_tool_update(all_tool_uses, on_tool_use, {
                    "id": fc["call_id"], "name": tool_name, "integration": integration,
                    "input": tool_args, "status": "error", "error": str(e),
                    "executionTimeMs": execution_time, "agentId": self.agent_id,
                })

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
