"""Orchestrator agent — coordinates domain-specific sub-agents."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from .config import app_config
from .investigation_context import InvestigationContext
from .integrations.mcp.client_manager import MCPClientManager
from .integrations.tool_registry import tool_registry
from .integrations.aws import register_aws_tools
from .integrations.newrelic import register_newrelic_tools
from .integrations.pagerduty import register_pagerduty_tools
from .integrations.digitalocean import register_digitalocean_tools
from .name_matcher import normalize_name
from .prompts import SYSTEM, EVALUATION_SYSTEM, CORRELATION_ANALYSIS
from .sub_agents import (
    BaseSubAgent,
    SubAgentConfig,
    SubAgentType,
    APMAgent,
    ErrorMonitoringAgent,
    InfrastructureAgent,
    AlertingAgent,
)
from .models import AgentRunRequest, ResourceMap, ResourceNode

logger = logging.getLogger(__name__)

MAX_ORCHESTRATOR_ITERATIONS = 4
MAX_EXTENDED_ITERATIONS = 8
CONFIDENCE_THRESHOLD = 60
MAX_SUBAGENT_FINDINGS_CHARS = 8_000
MAX_TOTAL_CONTEXT_CHARS = 80_000

INTEGRATION_TO_AGENT: dict[str, SubAgentType] = {
    "newrelic": "apm",
    "error_monitoring": "error_monitoring",
    "sentry": "error_monitoring",
    "aws": "infrastructure",
    "pagerduty": "alerting",
    "digitalocean": "infrastructure",
}

AGENT_CLASSES: dict[SubAgentType, type[BaseSubAgent]] = {
    "apm": APMAgent,
    "error_monitoring": ErrorMonitoringAgent,
    "infrastructure": InfrastructureAgent,
    "alerting": AlertingAgent,
}


def _sse_event(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


async def orchestrate(request: AgentRunRequest) -> AsyncGenerator[str, None]:
    """Run the multi-agent orchestration loop, yielding SSE events."""

    model = request.model or app_config.openai_model
    openai_key = request.settings.get("openai.api_key")

    if not openai_key:
        yield _sse_event({"type": "error", "error": "OpenAI API key not configured."})
        return

    client = AsyncOpenAI(api_key=openai_key)

    # Register custom tools
    tool_registry.clear()
    register_aws_tools(resource_maps=request.resource_maps)
    register_newrelic_tools()
    register_pagerduty_tools()
    register_digitalocean_tools()

    # Initialize MCP
    mcp = MCPClientManager(request.settings)
    try:
        await mcp.initialize()
    except Exception as e:
        logger.warning("MCP initialization error: %s", e)

    # Determine enabled integrations
    enabled_integrations = _get_enabled_integrations(request.settings)
    mcp_stats = mcp.get_stats()

    logger.info(
        "Starting orchestration: integrations=%s, mcp_servers=%d, mcp_tools=%d, model=%s",
        enabled_integrations,
        mcp_stats["connectedServers"],
        mcp_stats["totalTools"],
        model,
    )

    yield _sse_event({"type": "thinking"})

    # Build investigation context
    user_messages = [m for m in request.messages if m.role == "user"]
    is_follow_up = len(user_messages) > 1

    if request.persisted_context:
        ctx = InvestigationContext.from_dict(request.persisted_context)
        if user_messages:
            last_msg = user_messages[-1]
            probe = InvestigationContext()
            probe.parse_user_message(last_msg.content)
            if probe.time_window.get("extracted"):
                ctx.time_window = probe.time_window
    else:
        ctx = InvestigationContext()
        ctx.parse_all_user_messages([{"role": m.role, "content": m.content} for m in request.messages])

    # Generate investigation plan from user message
    if not ctx.investigation_plan and user_messages:
        incident_desc = user_messages[-1].content if user_messages else ""
        if incident_desc:
            ctx.generate_investigation_plan(incident_desc)

    # Auto-resolve resource scope from user message before first dispatch
    if not ctx.resources and request.resource_maps and user_messages:
        last_user_msg = user_messages[-1].content if user_messages else ""
        if last_user_msg:
            _auto_resolve_resource_scope(last_user_msg, request.resource_maps, ctx)

    # Create sub-agents
    domain_integrations = [i for i in enabled_integrations if i != "github"]
    base_config = SubAgentConfig(
        client=client,
        model=model,
        mcp=mcp,
        investigation_context=ctx,
        account_id=request.account_id,
        enabled_integrations=enabled_integrations,
        settings=request.settings,
    )

    sub_agents: dict[SubAgentType, BaseSubAgent] = {}
    for integration in domain_integrations:
        agent_type = INTEGRATION_TO_AGENT.get(integration)
        if not agent_type or agent_type not in AGENT_CLASSES:
            continue
        agent = AGENT_CLASSES[agent_type](base_config)
        tools = agent.get_tools()
        if not tools:
            logger.warning("No tools for %s — skipping", agent_type)
            continue
        sub_agents[agent_type] = agent

    resource_maps = request.resource_maps

    try:
        async for event_str in _run_orchestrator(
            client, model, sub_agents, enabled_integrations, resource_maps,
            request, ctx, is_follow_up, request.has_title,
        ):
            yield event_str
    except Exception as e:
        logger.error("Orchestrator error: %s", e, exc_info=True)
        yield _sse_event({"type": "error", "error": str(e)})
    finally:
        await mcp.dispose()


async def _run_orchestrator(
    client: AsyncOpenAI,
    model: str,
    sub_agents: dict[SubAgentType, BaseSubAgent],
    enabled_integrations: list[str],
    resource_maps: list[ResourceMap],
    request: AgentRunRequest,
    ctx: InvestigationContext,
    is_follow_up: bool,
    has_title: bool,
) -> AsyncGenerator[str, None]:
    now = datetime.now(timezone.utc)
    orchestrator_prompt = _build_orchestrator_prompt(
        now, sub_agents, enabled_integrations, resource_maps, ctx,
    )

    # Build input items from conversation history (only text messages)
    input_items: list[Any] = []
    for msg in request.messages:
        if msg.message_type != "text":
            continue
        content = msg.content or ""
        if msg.role == "assistant" and msg.toolUses:
            tool_list = msg.toolUses if isinstance(msg.toolUses, list) else [msg.toolUses]
            tool_summaries = []
            for t in tool_list:
                if t.get("status") == "success":
                    time_params = []
                    inp = t.get("input") or {}
                    for key in ["start_time", "end_time", "since", "from", "to", "startTime", "endTime"]:
                        if inp.get(key):
                            time_params.append(f"{key}={inp[key]}")
                    tool_summaries.append(
                        f"{t['name']}({', '.join(time_params)})" if time_params else t["name"]
                    )
            if tool_summaries:
                content += f"\n\n[Tools used: {', '.join(tool_summaries)}]"
        input_items.append({"role": msg.role, "content": content})

    # Inject persisted context for follow-ups
    if is_follow_up:
        ctx_msg = ctx.build_context_message()
        if ctx_msg:
            follow_up_directive = (
                "\n\n[Follow-Up Query — IMPORTANT]\n"
                "This is a follow-up message in an ongoing investigation. All resources have already been discovered.\n"
                "- If the answer is already in the conversation, respond directly WITHOUT dispatching.\n"
                "- If you must dispatch, use ONLY the single most relevant agent.\n"
                "- Tell the agent to skip all discovery steps (list orgs, list projects, describe instances) "
                "and query directly using the known resource names/IDs above."
            )
            input_items.insert(0, {
                "role": "developer",
                "content": f"[Persisted Investigation Context — maintain this time window unless the user explicitly changes it]\n{ctx_msg}{follow_up_directive}",
            })

    # Internal tools
    available_agent_ids = list(sub_agents.keys())
    dispatch_tool = _build_dispatch_tool(available_agent_ids)
    evaluate_tool = _build_evaluate_tool()
    has_resource_maps = len(resource_maps) > 0
    resources_already_resolved = bool(ctx.resources and ctx.scoped_group_name)

    # Skip _get_connected_resources when resources were auto-resolved from user message
    if has_resource_maps and not resources_already_resolved:
        internal_tools = [_build_get_connected_resources_tool(), dispatch_tool, evaluate_tool]
    else:
        internal_tools = [dispatch_tool, evaluate_tool]

    total_tokens = {"input": 0, "output": 0}

    # ── TRIAGE ────────────────────────────────────────────────
    triage_result = await _triage(
        client, model, input_items, orchestrator_prompt, internal_tools,
        total_tokens, ctx, resource_maps, sub_agents,
    )

    if triage_result["type"] == "direct_response":
        for chunk in triage_result.get("chunks", []):
            yield _sse_event({"type": "text_delta", "text": chunk})
        yield _sse_event({"type": "token_usage", **total_tokens})
        yield _sse_event({"type": "context_update", "context": ctx.to_dict()})

        # Build response message
        response_text = triage_result.get("text", "")
        yield _sse_event({
            "type": "response_complete",
            "conversationId": request.conversation_id,
            "message": {
                "id": _gen_id(),
                "role": "assistant",
                "content": response_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        })
        return

    # Model dispatched
    first_dispatch_call = triage_result["dispatch_call"]
    assignments = triage_result["assignments"]

    for chunk in triage_result.get("chunks", []):
        yield _sse_event({"type": "text_delta", "text": chunk})

    input_items.append(first_dispatch_call)

    # ── ORCHESTRATOR LOOP ─────────────────────────────────────
    all_text_chunks: list[str] = []
    all_text_chunks.extend(triage_result.get("chunks", []))
    next_forced_dispatch: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None

    for iteration in range(MAX_ORCHESTRATOR_ITERATIONS):
        yield _sse_event({"type": "iteration_start", "iteration": iteration})

        if iteration > 0:
            # Context budget check — summarize older findings if approaching limit
            context_size = _estimate_context_chars(input_items)
            if context_size > MAX_TOTAL_CONTEXT_CHARS * 0.8:
                logger.info("Context size %d chars approaching limit, summarizing older findings", context_size)
                await _summarize_older_findings(client, input_items, app_config.openai_summary_model)

            # Check if we have a forced dispatch from next_steps enforcement
            if next_forced_dispatch:
                active_call_id = next_forced_dispatch["call_id"]
                assignments = next_forced_dispatch["assignments"]
                next_forced_dispatch = None
            else:
                # Get new dispatch from the model
                dispatch_call = await _dispatch(
                    client, model, input_items, orchestrator_prompt, internal_tools,
                    total_tokens, ctx, resource_maps, sub_agents,
                )
                if not dispatch_call:
                    break

                input_items.append(dispatch_call)

                try:
                    parsed = json.loads(dispatch_call["arguments"])
                    assignments = [a for a in parsed.get("agents", []) if a["agentId"] in sub_agents]
                except (json.JSONDecodeError, KeyError):
                    break

                if not assignments:
                    break

                active_call_id = dispatch_call["call_id"]

        # ── Run sub-agents ────────────────────────────────────
        if iteration == 0:
            active_call_id = first_dispatch_call["call_id"]

        # Enrich tasks with known resource context for follow-ups
        if is_follow_up:
            assignments = _enrich_follow_up_tasks(assignments, ctx)

        results = []

        for assignment in assignments:
            agent_id = assignment["agentId"]
            task = assignment["task"]
            sub_agent = sub_agents[agent_id]

            yield _sse_event({
                "type": "sub_agent_start",
                "agentId": agent_id,
                "agentType": sub_agent.agent_type,
                "task": task,
            })

            try:
                tool_queue: asyncio.Queue = asyncio.Queue()
                agent_task = asyncio.create_task(
                    sub_agent.run(task, on_tool_use=lambda tu: tool_queue.put_nowait(tu))
                )

                while not agent_task.done():
                    try:
                        tu = await asyncio.wait_for(tool_queue.get(), timeout=0.05)
                        yield _sse_event({"type": "tool_use", "toolUse": tu})
                    except asyncio.TimeoutError:
                        continue

                # Drain any remaining items
                while not tool_queue.empty():
                    yield _sse_event({"type": "tool_use", "toolUse": tool_queue.get_nowait()})

                result = agent_task.result()
                results.append(result)
                total_tokens["input"] += result.token_usage.get("input", 0)
                total_tokens["output"] += result.token_usage.get("output", 0)

                yield _sse_event({
                    "type": "sub_agent_complete",
                    "agentId": result.agent_id,
                    "findings": result.findings[:500],
                })

            except Exception as e:
                logger.error("Sub-agent %s failed: %s", agent_id, e)
                yield _sse_event({"type": "sub_agent_complete", "agentId": agent_id, "findings": f"Agent failed: {e}"})
                from .sub_agents.base import SubAgentResult
                results.append(SubAgentResult(
                    agent_id=agent_id,
                    findings=f"Investigation failed: {e}",
                    tools_used=[],
                    iterations=0,
                    token_usage={"input": 0, "output": 0},
                ))

        # Add findings (truncate each sub-agent's findings to budget)
        for r in results:
            if len(r.findings) > MAX_SUBAGENT_FINDINGS_CHARS:
                r.findings = (
                    r.findings[:MAX_SUBAGENT_FINDINGS_CHARS]
                    + f"\n\n[... truncated {len(r.findings) - MAX_SUBAGENT_FINDINGS_CHARS} chars]"
                )

        findings_summary = "\n\n---\n\n".join(
            f"## {r.agent_id} Agent Findings ({len(r.tools_used)} tools, {r.iterations} iterations)\n{r.findings}"
            for r in results
        )
        input_items.append({
            "type": "function_call_output",
            "call_id": active_call_id,
            "output": findings_summary,
        })

        # ── Cross-agent correlation ──────────────────────────
        correlation = await _correlate_findings(client, results, ctx, total_tokens)
        if correlation:
            yield _sse_event({"type": "correlation", "summary": correlation})
            input_items.append({
                "role": "developer",
                "content": f"[Cross-Agent Correlation]\n{correlation}",
            })

        # ── Evaluate ──────────────────────────────────────────
        is_last = iteration >= MAX_ORCHESTRATOR_ITERATIONS - 1

        if is_last:
            input_items.append({
                "role": "developer",
                "content": 'This is the last orchestrator iteration. Strongly consider marking as "complete" unless critical data is clearly missing.',
            })

        # For follow-up data queries, hint that single-dispatch is often sufficient
        if is_follow_up and iteration == 0:
            input_items.append({
                "role": "developer",
                "content": (
                    "[Follow-up evaluation hint] This is a follow-up query. If the sub-agent returned "
                    "the specific data the user asked about, mark as complete with high confidence. "
                    "Follow-up data queries do NOT require multi-agent investigation."
                ),
            })

        evaluation = await _evaluate(client, model, input_items, orchestrator_prompt, internal_tools, total_tokens, results)

        yield _sse_event({
            "type": "reasoning",
            "iteration": iteration,
            "evaluation": evaluation["result"],
        })

        if evaluation.get("call"):
            input_items.append(evaluation["call"])
            input_items.append({
                "type": "function_call_output",
                "call_id": evaluation["call"]["call_id"],
                "output": f"Evaluation recorded: {evaluation['result']['status']} (confidence: {evaluation['result']['confidence']}%)",
            })

        should_complete = (
            evaluation["result"]["status"] == "complete"
            and evaluation["result"]["confidence"] >= CONFIDENCE_THRESHOLD
        )

        if should_complete or is_last:
            yield _sse_event({"type": "token_usage", **total_tokens})

            async for chunk in _stream_final_answer(
                client, model, input_items, orchestrator_prompt, is_last and not should_complete, ctx,
            ):
                all_text_chunks.append(chunk)
                yield _sse_event({"type": "text_delta", "text": chunk})

            yield _sse_event({"type": "context_update", "context": ctx.to_dict()})
            yield _sse_event({
                "type": "response_complete",
                "conversationId": request.conversation_id,
                "message": {
                    "id": _gen_id(),
                    "role": "assistant",
                    "content": "".join(all_text_chunks),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            })

            # Generate title if needed
            if not has_title:
                async for evt in _generate_title(client, request, "".join(all_text_chunks)):
                    yield evt

            return

        # Resolve next_steps into concrete dispatches for the next iteration
        next_steps = evaluation["result"].get("next_steps", [])
        if next_steps:
            forced_assignments = _resolve_next_steps_to_dispatch(next_steps, sub_agents, ctx)
            if forced_assignments:
                logger.info(
                    "Enforcing next_steps as dispatches: %s",
                    [(a["agentId"], a["task"][:80]) for a in forced_assignments],
                )
                # Inject as a forced dispatch for the next iteration
                forced_dispatch = json.dumps({"agents": forced_assignments})
                forced_call_id = f"forced-{uuid.uuid4()}"
                input_items.append({
                    "type": "function_call",
                    "call_id": forced_call_id,
                    "name": "_dispatch",
                    "arguments": forced_dispatch,
                })
                # We'll handle execution in the next iteration by setting assignments directly
                # Store for the next iteration to pick up
                next_forced_dispatch = {
                    "call_id": forced_call_id,
                    "assignments": forced_assignments,
                }
            else:
                next_forced_dispatch = None
        else:
            next_forced_dispatch = None

        if evaluation["result"]["status"] == "complete" and evaluation["result"]["confidence"] < CONFIDENCE_THRESHOLD:
            input_items.append({
                "role": "developer",
                "content": f"[Evaluation Override] Confidence {evaluation['result']['confidence']}% is below threshold. Continue investigating. Focus on: {', '.join(next_steps) or 'filling gaps'}",
            })

    # Check if we should extend the investigation
    last_confidence = evaluation["result"]["confidence"] if evaluation else 0  # type: ignore[possibly-undefined]
    incomplete_checks = ctx.investigation_plan.get_incomplete_checks() if ctx.investigation_plan else []

    if last_confidence < 40 and incomplete_checks:
        logger.warning(
            "Extending investigation: confidence=%d%%, incomplete_checks=%d",
            last_confidence, len(incomplete_checks),
        )
        yield _sse_event({
            "type": "investigation_extended",
            "reason": f"Confidence {last_confidence}% with {len(incomplete_checks)} incomplete checks",
        })

        for ext_iteration in range(MAX_ORCHESTRATOR_ITERATIONS, MAX_EXTENDED_ITERATIONS):
            yield _sse_event({"type": "iteration_start", "iteration": ext_iteration})

            # Build focused dispatch from incomplete checks only
            incomplete = ctx.investigation_plan.get_incomplete_checks() if ctx.investigation_plan else []
            if not incomplete:
                break

            ext_assignments: list[dict[str, str]] = []
            dispatched: set[str] = set()
            for check in incomplete:
                agent_id = check["agent_id"]
                if agent_id in sub_agents and agent_id not in dispatched:
                    dispatched.add(agent_id)
                    agent_checks = ctx.investigation_plan.get_checks_for_agent(agent_id) if ctx.investigation_plan else []
                    task_desc = "Complete these required checks: " + "; ".join(c["description"] for c in agent_checks)
                    ext_assignments.append({"agentId": agent_id, "task": task_desc})

            if not ext_assignments:
                break

            # Run the focused sub-agents
            ext_call_id = f"extended-{uuid.uuid4()}"
            ext_results = []

            for assignment in ext_assignments:
                agent_id_str = assignment["agentId"]
                ext_task = assignment["task"]
                ext_agent = sub_agents[agent_id_str]

                yield _sse_event({
                    "type": "sub_agent_start",
                    "agentId": agent_id_str,
                    "agentType": ext_agent.agent_type,
                    "task": ext_task,
                })

                try:
                    tool_queue_ext: asyncio.Queue = asyncio.Queue()
                    ext_agent_task = asyncio.create_task(
                        ext_agent.run(ext_task, on_tool_use=lambda tu: tool_queue_ext.put_nowait(tu))
                    )

                    while not ext_agent_task.done():
                        try:
                            tu = await asyncio.wait_for(tool_queue_ext.get(), timeout=0.05)
                            yield _sse_event({"type": "tool_use", "toolUse": tu})
                        except asyncio.TimeoutError:
                            continue

                    while not tool_queue_ext.empty():
                        yield _sse_event({"type": "tool_use", "toolUse": tool_queue_ext.get_nowait()})

                    ext_result = ext_agent_task.result()
                    ext_results.append(ext_result)
                    total_tokens["input"] += ext_result.token_usage.get("input", 0)
                    total_tokens["output"] += ext_result.token_usage.get("output", 0)

                    yield _sse_event({
                        "type": "sub_agent_complete",
                        "agentId": ext_result.agent_id,
                        "findings": ext_result.findings[:500],
                    })

                except Exception as e:
                    logger.error("Extended sub-agent %s failed: %s", agent_id_str, e)
                    from .sub_agents.base import SubAgentResult
                    ext_results.append(SubAgentResult(
                        agent_id=agent_id_str,
                        findings=f"Investigation failed: {e}",
                        tools_used=[],
                        iterations=0,
                        token_usage={"input": 0, "output": 0},
                    ))

            # Truncate and add findings
            for r in ext_results:
                if len(r.findings) > MAX_SUBAGENT_FINDINGS_CHARS:
                    r.findings = r.findings[:MAX_SUBAGENT_FINDINGS_CHARS] + "\n\n[... truncated]"

            ext_findings = "\n\n---\n\n".join(
                f"## {r.agent_id} Agent Findings (extended)\n{r.findings}"
                for r in ext_results
            )
            input_items.append({
                "type": "function_call",
                "call_id": ext_call_id,
                "name": "_dispatch",
                "arguments": json.dumps({"agents": ext_assignments}),
            })
            input_items.append({
                "type": "function_call_output",
                "call_id": ext_call_id,
                "output": ext_findings,
            })

            # Correlate extended findings
            correlation = await _correlate_findings(client, ext_results, ctx, total_tokens)
            if correlation:
                yield _sse_event({"type": "correlation", "summary": correlation})

            # Re-evaluate
            ext_eval = await _evaluate(client, model, input_items, orchestrator_prompt, internal_tools, total_tokens, ext_results)
            yield _sse_event({
                "type": "reasoning",
                "iteration": ext_iteration,
                "evaluation": ext_eval["result"],
            })

            if ext_eval["result"]["status"] == "complete" and ext_eval["result"]["confidence"] >= CONFIDENCE_THRESHOLD:
                break

    # Final answer
    yield _sse_event({"type": "token_usage", **total_tokens})
    async for chunk in _stream_final_answer(client, model, input_items, orchestrator_prompt, True, ctx):
        all_text_chunks.append(chunk)
        yield _sse_event({"type": "text_delta", "text": chunk})

    yield _sse_event({"type": "context_update", "context": ctx.to_dict()})
    yield _sse_event({
        "type": "response_complete",
        "conversationId": request.conversation_id,
        "message": {
            "id": _gen_id(),
            "role": "assistant",
            "content": "".join(all_text_chunks),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    })

    if not has_title:
        async for evt in _generate_title(client, request, "".join(all_text_chunks)):
            yield evt


# ── Triage ────────────────────────────────────────────────────

async def _triage(
    client: AsyncOpenAI,
    model: str,
    input_items: list[Any],
    system_prompt: str,
    tools: list[Any],
    total_tokens: dict[str, int],
    ctx: InvestigationContext,
    resource_maps: list[ResourceMap],
    sub_agents: dict[SubAgentType, BaseSubAgent],
) -> dict[str, Any]:
    stream = await client.responses.create(
        model=model,
        instructions=system_prompt,
        input=input_items,
        tools=tools,
        tool_choice="auto",
        stream=True,
    )

    text_chunks: list[str] = []
    completed_response: Any = None

    async for event in stream:
        if event.type == "response.output_text.delta":
            text_chunks.append(event.delta)
        elif event.type == "response.completed":
            completed_response = event.response

    if completed_response and hasattr(completed_response, "usage") and completed_response.usage:
        total_tokens["input"] += getattr(completed_response.usage, "input_tokens", 0) or 0
        total_tokens["output"] += getattr(completed_response.usage, "output_tokens", 0) or 0

    # Check for function calls
    connected_call = None
    dispatch_call = None

    if completed_response and hasattr(completed_response, "output"):
        for o in completed_response.output:
            if o.type == "function_call" and o.name == "_get_connected_resources":
                connected_call = o
            elif o.type == "function_call" and o.name == "_dispatch":
                dispatch_call = o

    # Handle connected resources lookup
    if connected_call:
        scope_result = _resolve_resource_scope(connected_call, resource_maps)
        scope_output = _format_scope_result(scope_result)

        if scope_result:
            ctx.scope_to_resources(
                [r.name for r in scope_result["resources"]],
                scope_result["group_name"],
            )

        input_items.append({
            "type": "function_call",
            "call_id": connected_call.call_id,
            "name": connected_call.name,
            "arguments": connected_call.arguments,
        })
        input_items.append({
            "type": "function_call_output",
            "call_id": connected_call.call_id,
            "output": scope_output,
        })

        if not dispatch_call:
            follow_up = await client.responses.create(
                model=model,
                instructions=system_prompt,
                input=input_items,
                tools=tools,
                tool_choice="auto",
                stream=True,
            )

            follow_up_response: Any = None
            async for event in follow_up:
                if event.type == "response.output_text.delta":
                    text_chunks.append(event.delta)
                elif event.type == "response.completed":
                    follow_up_response = event.response

            if follow_up_response and hasattr(follow_up_response, "usage") and follow_up_response.usage:
                total_tokens["input"] += getattr(follow_up_response.usage, "input_tokens", 0) or 0
                total_tokens["output"] += getattr(follow_up_response.usage, "output_tokens", 0) or 0

            if follow_up_response and hasattr(follow_up_response, "output"):
                for o in follow_up_response.output:
                    if o.type == "function_call" and o.name == "_dispatch":
                        dispatch_call = o

    if not dispatch_call:
        return {"type": "direct_response", "chunks": text_chunks, "text": "".join(text_chunks)}

    # Parse assignments
    try:
        parsed = json.loads(dispatch_call.arguments)
        assignments = [a for a in parsed.get("agents", []) if a["agentId"] in sub_agents]
    except (json.JSONDecodeError, KeyError):
        return {"type": "direct_response", "chunks": text_chunks, "text": "".join(text_chunks)}

    if not assignments:
        return {"type": "direct_response", "chunks": text_chunks, "text": "".join(text_chunks)}

    return {
        "type": "dispatch",
        "dispatch_call": {
            "type": "function_call",
            "call_id": dispatch_call.call_id,
            "name": dispatch_call.name,
            "arguments": dispatch_call.arguments,
        },
        "assignments": assignments,
        "chunks": text_chunks,
    }


# ── Dispatch (iterations > 0) ────────────────────────────────

async def _dispatch(
    client: AsyncOpenAI,
    model: str,
    input_items: list[Any],
    system_prompt: str,
    tools: list[Any],
    total_tokens: dict[str, int],
    ctx: InvestigationContext,
    resource_maps: list[ResourceMap],
    sub_agents: dict[SubAgentType, BaseSubAgent],
) -> dict[str, Any] | None:
    for _ in range(3):
        response = await client.responses.create(
            model=model,
            instructions=system_prompt,
            input=input_items,
            tools=tools,
            tool_choice="auto",
        )

        if hasattr(response, "usage") and response.usage:
            total_tokens["input"] += getattr(response.usage, "input_tokens", 0) or 0
            total_tokens["output"] += getattr(response.usage, "output_tokens", 0) or 0

        dispatch_call = None
        connected_call = None

        if hasattr(response, "output"):
            for o in response.output:
                if o.type == "function_call" and o.name == "_dispatch":
                    dispatch_call = o
                elif o.type == "function_call" and o.name == "_get_connected_resources":
                    connected_call = o

        if dispatch_call:
            return {
                "type": "function_call",
                "call_id": dispatch_call.call_id,
                "name": dispatch_call.name,
                "arguments": dispatch_call.arguments,
            }

        if connected_call:
            scope_result = _resolve_resource_scope(connected_call, resource_maps)
            scope_output = _format_scope_result(scope_result)

            if scope_result:
                ctx.scope_to_resources(
                    [r.name for r in scope_result["resources"]],
                    scope_result["group_name"],
                )

            input_items.append({
                "type": "function_call",
                "call_id": connected_call.call_id,
                "name": connected_call.name,
                "arguments": connected_call.arguments,
            })
            input_items.append({
                "type": "function_call_output",
                "call_id": connected_call.call_id,
                "output": scope_output,
            })
            continue

        return None

    return None


# ── Evaluate ──────────────────────────────────────────────────

async def _evaluate(
    client: AsyncOpenAI,
    model: str,
    input_items: list[Any],
    system_prompt: str,
    tools: list[Any],
    total_tokens: dict[str, int],
    results: list[Any] | None = None,
) -> dict[str, Any]:
    eval_input = list(input_items)

    # Build data quality summary from sub-agent results
    quality_notes = _build_data_quality_summary(results) if results else ""
    eval_content = EVALUATION_SYSTEM
    if quality_notes:
        eval_content += f"\n\n**DATA QUALITY CHECK (from sub-agent tool calls):**\n{quality_notes}\nYou MUST factor these data quality issues into your confidence score."

    eval_input.append({
        "role": "developer",
        "content": eval_content,
    })

    response = await client.responses.create(
        model=model,
        instructions=system_prompt,
        input=eval_input,
        tools=tools,
        tool_choice={"type": "function", "name": "_evaluate"},
    )

    if hasattr(response, "usage") and response.usage:
        total_tokens["input"] += getattr(response.usage, "input_tokens", 0) or 0
        total_tokens["output"] += getattr(response.usage, "output_tokens", 0) or 0

    eval_call = None
    if hasattr(response, "output"):
        for o in response.output:
            if o.type == "function_call" and o.name == "_evaluate":
                eval_call = o

    if not eval_call:
        return {
            "result": {"status": "continue", "confidence": 0, "summary": "No evaluation produced.", "next_steps": []},
            "call": None,
        }

    try:
        parsed = json.loads(eval_call.arguments)
        confidence = min(100, max(0, parsed.get("confidence", 0)))

        # Hard confidence cap: if any sub-agent had ALL tools fail/empty, cap at 50
        if results:
            for r in results:
                total = len(r.tools_used)
                if total == 0:
                    continue
                usable = sum(
                    1 for tu in r.tools_used
                    if tu.get("status") == "success" and not _is_empty_tool_output(tu.get("output", ""))
                )
                if usable == 0 and confidence > 50:
                    logger.warning(
                        "Capping confidence from %d to 50: sub-agent %s had 0/%d usable tool results",
                        confidence, r.agent_id, total,
                    )
                    confidence = 50
                    if parsed.get("status") == "complete":
                        parsed["status"] = "continue"

        return {
            "result": {
                "status": "complete" if parsed.get("status") == "complete" else "continue",
                "confidence": confidence,
                "summary": parsed.get("summary", ""),
                "next_steps": parsed.get("next_steps", []),
            },
            "call": {
                "type": "function_call",
                "call_id": eval_call.call_id,
                "name": eval_call.name,
                "arguments": eval_call.arguments,
            },
        }
    except (json.JSONDecodeError, KeyError):
        return {
            "result": {"status": "continue", "confidence": 0, "summary": "Failed to parse evaluation.", "next_steps": []},
            "call": {
                "type": "function_call",
                "call_id": eval_call.call_id,
                "name": eval_call.name,
                "arguments": eval_call.arguments,
            },
        }


# ── Next-steps enforcement ────────────────────────────────────

# Keyword mapping from evaluation next_steps to agent IDs
_NEXT_STEP_KEYWORDS: dict[str, list[str]] = {
    "infrastructure": ["cloudwatch", "aws", "rds", "ec2", "ecs", "lambda", "metric", "alarm", "log group", "performance insights", "database metric"],
    "apm": ["apm", "new relic", "newrelic", "nrql", "throughput", "response time", "golden metric", "transaction", "datastore"],
    "error_monitoring": ["sentry", "error", "stack trace", "exception", "issue"],
    "alerting": ["pagerduty", "incident", "alert", "on-call"],
}


def _resolve_next_steps_to_dispatch(
    next_steps: list[str],
    available_agents: dict[str, Any],
    ctx: InvestigationContext,
) -> list[dict[str, str]]:
    """Convert evaluation next_steps into concrete sub-agent dispatch assignments."""
    assignments: list[dict[str, str]] = []
    dispatched_agents: set[str] = set()

    for step in next_steps:
        step_lower = step.lower()
        matched_agent = None

        for agent_id, keywords in _NEXT_STEP_KEYWORDS.items():
            if agent_id not in available_agents:
                continue
            if any(kw in step_lower for kw in keywords):
                matched_agent = agent_id
                break

        if not matched_agent:
            continue

        if matched_agent in dispatched_agents:
            # Merge into existing assignment
            for a in assignments:
                if a["agentId"] == matched_agent:
                    a["task"] += f" Additionally: {step}"
                    break
            continue

        dispatched_agents.add(matched_agent)
        assignments.append({"agentId": matched_agent, "task": step})

    # Also check investigation plan for incomplete checks
    if ctx.investigation_plan:
        for check in ctx.investigation_plan.get_incomplete_checks():
            agent_id = check["agent_id"]
            if agent_id in available_agents and agent_id not in dispatched_agents:
                dispatched_agents.add(agent_id)
                assignments.append({
                    "agentId": agent_id,
                    "task": f"[Required check] {check['description']}",
                })

    return assignments


# ── Cross-agent correlation ───────────────────────────────────

async def _correlate_findings(
    client: AsyncOpenAI,
    results: list[Any],
    ctx: InvestigationContext,
    total_tokens: dict[str, int],
) -> str | None:
    """Correlate findings across sub-agents to identify causal relationships."""
    if len(results) < 2:
        return None

    findings_text = "\n\n---\n\n".join(
        f"## {r.agent_id} Agent Findings\n{r.findings[:4000]}"
        for r in results
        if r.findings
    )

    if not findings_text.strip():
        return None

    # Include critical signals if available
    evidence = ctx.build_evidence_brief()
    if evidence:
        findings_text += f"\n\n---\n\n## Critical Signals\n{evidence}"

    try:
        response = await client.chat.completions.create(
            model=app_config.openai_summary_model,
            messages=[
                {"role": "system", "content": CORRELATION_ANALYSIS},
                {"role": "user", "content": findings_text},
            ],
            max_tokens=500,
        )

        if hasattr(response, "usage") and response.usage:
            total_tokens["input"] += getattr(response.usage, "prompt_tokens", 0) or 0
            total_tokens["output"] += getattr(response.usage, "completion_tokens", 0) or 0

        correlation = (response.choices[0].message.content or "").strip()
        if correlation:
            ctx.correlation_summary = correlation
            logger.info("Cross-agent correlation completed: %s", correlation[:200])
            return correlation
    except Exception as e:
        logger.warning("Correlation analysis failed: %s", e)

    return None


# ── Final answer streaming ────────────────────────────────────

async def _stream_final_answer(
    client: AsyncOpenAI,
    model: str,
    input_items: list[Any],
    system_prompt: str,
    forced: bool,
    ctx: InvestigationContext | None = None,
) -> AsyncGenerator[str, None]:
    final_input = list(input_items)

    phase_msg = (
        "[Phase: Final Answer] You have reached the iteration limit. Produce your final, comprehensive answer NOW using all the data gathered by sub-agents. Do not dispatch any more tasks."
        if forced
        else "[Phase: Final Answer] Your evaluation determined the investigation is complete. Produce your final, comprehensive diagnosis synthesizing all evidence from sub-agents."
    )

    # Include evidence brief so the synthesizer can't miss critical signals
    if ctx:
        evidence = ctx.build_evidence_brief()
        if evidence:
            phase_msg += f"\n\n{evidence}"
        if ctx.correlation_summary:
            phase_msg += f"\n\n**Cross-Agent Correlation:**\n{ctx.correlation_summary}"

    final_input.append({"role": "developer", "content": phase_msg})

    stream = await client.responses.create(
        model=model,
        instructions=system_prompt,
        input=final_input,
        stream=True,
    )

    async for event in stream:
        if event.type == "response.output_text.delta":
            yield event.delta


# ── Title generation ──────────────────────────────────────────

async def _generate_title(
    client: AsyncOpenAI,
    request: AgentRunRequest,
    response_text: str = "",
) -> AsyncGenerator[str, None]:
    summary_model = app_config.openai_summary_model
    user_msg = request.user_message

    # Build title input: use user message + first part of agent response for context
    # This helps when the user message is just a URL (agent response has the actual topic)
    title_input = user_msg[:300]
    if response_text:
        # Extract the first meaningful section (usually "What Happened" or similar)
        first_section = response_text[:400]
        title_input = f"User asked: {title_input}\n\nAgent response summary: {first_section}"

    try:
        response = await client.chat.completions.create(
            model=summary_model,
            messages=[
                {"role": "system", "content": "Generate a short, descriptive title (max 60 chars) for this conversation based on the topic discussed. If the user sent a URL, use the agent's response to determine the topic. Return only the title, no quotes or punctuation."},
                {"role": "user", "content": title_input[:700]},
            ],
            max_tokens=30,
        )
        title = (response.choices[0].message.content or "").strip()
        if title:
            yield _sse_event({
                "type": "title_generated",
                "title": title,
                "conversationId": request.conversation_id,
            })
    except Exception as e:
        logger.warning("Title generation failed: %s", e)


# ── Tool schemas ──────────────────────────────────────────────

def _build_dispatch_tool(available_agent_ids: list[SubAgentType]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": "_dispatch",
        "description": "Dispatch investigation tasks to domain-specific sub-agents.",
        "parameters": {
            "type": "object",
            "properties": {
                "agents": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agentId": {"type": "string", "enum": available_agent_ids},
                            "task": {"type": "string"},
                        },
                        "required": ["agentId", "task"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["agents"],
            "additionalProperties": False,
        },
        "strict": True,
    }


def _build_evaluate_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "_evaluate",
        "description": "Evaluate whether enough data has been gathered to answer the user's question. For simple data queries (status checks, listing resources, metric lookups), successful data retrieval is sufficient. For investigations, evaluate whether a comprehensive diagnosis is possible.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["continue", "complete"]},
                "confidence": {"type": "number"},
                "summary": {"type": "string"},
                "next_steps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "confidence", "summary", "next_steps"],
            "additionalProperties": False,
        },
        "strict": True,
    }


def _build_get_connected_resources_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "_get_connected_resources",
        "description": "Look up a resource or service name in the resource map and return all connected resources in the same group.",
        "parameters": {
            "type": "object",
            "properties": {
                "resource_name": {"type": "string"},
            },
            "required": ["resource_name"],
            "additionalProperties": False,
        },
        "strict": True,
    }


# ── Resource map helpers ──────────────────────────────────────

def _auto_resolve_resource_scope(
    user_message: str,
    resource_maps: list[ResourceMap],
    ctx: InvestigationContext,
) -> None:
    """Try to match user message content against resource map nodes/groups.

    Extracts candidate service names from:
    - PagerDuty URL path segments (e.g., /incidents/P1234ABC)
    - Quoted strings in the message
    - Word tokens that might be service/resource names

    On match: populates ctx.resources and calls ctx.scope_to_resources().
    """
    import re as _re
    candidates: list[str] = []

    # Extract from PagerDuty URLs — check resource map nodes with source == "pagerduty"
    pd_url_match = _re.search(r"pagerduty\.com/incidents/([A-Z0-9]+)", user_message, _re.IGNORECASE)
    if pd_url_match:
        # The incident ID itself won't match, but scan PD nodes for service name matches
        for rmap in resource_maps:
            for node in rmap.nodes:
                if node.source == "pagerduty" and node.name:
                    candidates.append(node.name)

    # Extract quoted strings
    for m in _re.findall(r'"([^"]{2,60})"', user_message):
        candidates.append(m)
    for m in _re.findall(r"'([^']{2,60})'", user_message):
        candidates.append(m)

    # Extract service-like tokens (words with hyphens/underscores that look like identifiers)
    for m in _re.findall(r'\b([a-zA-Z][a-zA-Z0-9_-]{2,40})\b', user_message):
        # Skip common non-service words
        if m.lower() in {"the", "and", "for", "this", "that", "what", "how", "why",
                         "are", "was", "were", "been", "has", "have", "had", "can",
                         "could", "would", "should", "will", "with", "from", "into",
                         "about", "which", "there", "their", "here", "some", "any",
                         "investigate", "check", "look", "help", "please", "thanks",
                         "https", "http", "com", "org", "net", "incidents", "pagerduty"}:
            continue
        candidates.append(m)

    if not candidates:
        return

    # Try to match each candidate against resource map nodes/groups
    for candidate in candidates:
        normalized_candidate = normalize_name(candidate)
        if not normalized_candidate or len(normalized_candidate) < 3:
            continue

        for rmap in resource_maps:
            if not rmap.groups:
                continue

            node_by_id = {n.id: n for n in rmap.nodes}

            # Pass 1: exact normalized name match on nodes
            matched_node = next(
                (n for n in rmap.nodes if n.normalizedName == normalized_candidate), None
            )

            # Pass 2: substring containment on nodes
            if not matched_node:
                matched_node = next(
                    (n for n in rmap.nodes
                     if normalized_candidate in n.normalizedName or n.normalizedName in normalized_candidate),
                    None,
                )

            # Pass 3: group name match
            if not matched_node:
                for group in rmap.groups:
                    ng = normalize_name(group.name)
                    if ng == normalized_candidate or normalized_candidate in ng or ng in normalized_candidate:
                        nodes = [node_by_id[nid] for nid in group.nodeIds if nid in node_by_id]
                        if nodes:
                            _apply_resource_scope(nodes, group.name, ctx)
                            logger.info(
                                "Auto-resolved resource scope from user message: group='%s' (%d resources)",
                                group.name, len(nodes),
                            )
                            return
                continue

            # Find group containing matched node
            for group in rmap.groups:
                if matched_node.id in group.nodeIds:
                    nodes = [node_by_id[nid] for nid in group.nodeIds if nid in node_by_id]
                    if nodes:
                        _apply_resource_scope(nodes, group.name, ctx)
                        logger.info(
                            "Auto-resolved resource scope from user message: matched node='%s', group='%s' (%d resources)",
                            matched_node.name, group.name, len(nodes),
                        )
                        return


def _apply_resource_scope(
    nodes: list[ResourceNode],
    group_name: str,
    ctx: InvestigationContext,
) -> None:
    """Populate ctx.resources from resource map nodes and scope the context."""
    from .investigation_context import ResourceEntry

    for node in nodes:
        ctx._add_resource(ResourceEntry(
            name=node.name,
            type=node.type,
            id=node.externalId,
            attrs=node.attrs,
        ))

    resource_names = [n.name for n in nodes]
    ctx.scope_to_resources(resource_names, group_name)


def _resolve_resource_scope(
    connected_call: Any,
    resource_maps: list[ResourceMap],
) -> dict[str, Any] | None:
    try:
        parsed = json.loads(connected_call.arguments)
        service_name = parsed.get("resource_name", "")
    except (json.JSONDecodeError, KeyError):
        return None

    normalized_input = normalize_name(service_name)

    for rmap in resource_maps:
        if not rmap.groups:
            continue

        node_by_id = {n.id: n for n in rmap.nodes}

        # Pass 1: exact normalized name match
        matched_node = next(
            (n for n in rmap.nodes if n.normalizedName == normalized_input), None
        )

        # Pass 2: substring containment
        if not matched_node:
            matched_node = next(
                (n for n in rmap.nodes if normalized_input in n.normalizedName or n.normalizedName in normalized_input),
                None,
            )

        # Pass 3: group name match
        if not matched_node:
            for group in rmap.groups:
                ng = normalize_name(group.name)
                if ng == normalized_input or normalized_input in ng or ng in normalized_input:
                    nodes = [node_by_id[nid] for nid in group.nodeIds if nid in node_by_id]
                    return {"group_name": group.name, "group_id": group.id, "map_name": rmap.name, "resources": nodes}
            continue

        # Find group containing matched node
        for group in rmap.groups:
            if matched_node.id in group.nodeIds:
                nodes = [node_by_id[nid] for nid in group.nodeIds if nid in node_by_id]
                return {"group_name": group.name, "group_id": group.id, "map_name": rmap.name, "resources": nodes}

    return None


def _format_scope_result(result: dict[str, Any] | None) -> str:
    if not result:
        return "No matching resource group found. Investigate all available resources."

    lines = [
        f'Matched group: **{result["group_name"]}** from map "{result["map_name"]}" ({len(result["resources"])} resources)',
        "",
        "Resources in this group:",
    ]

    for node in result["resources"]:
        id_part = f" (id: {node.externalId})" if node.externalId else ""
        attr_parts = ", ".join(f"{k}={v}" for k, v in node.attrs.items() if v)
        lines.append(f"- [{node.type}] {node.name}{id_part} [{node.source}]{f' — {attr_parts}' if attr_parts else ''}")

    lines.append("")
    lines.append("Scope all dispatch tasks to ONLY these resources.")
    return "\n".join(lines)


def _build_orchestrator_prompt(
    now: datetime,
    sub_agents: dict[SubAgentType, BaseSubAgent],
    enabled_integrations: list[str],
    resource_maps: list[ResourceMap],
    ctx: InvestigationContext | None = None,
) -> str:
    agent_descs: dict[SubAgentType, str] = {
        "apm": "Queries New Relic for APM metrics, throughput, error rates, response times, transaction traces, and NRQL analytics",
        "error_monitoring": "Queries Sentry for error tracking, stack traces, issue frequency, affected users, and release correlation",
        "infrastructure": "Queries AWS for CloudWatch metrics, EC2/ECS/Lambda health, RDS performance, log analysis, and infrastructure alarms",
        "alerting": "Queries PagerDuty for incidents, on-call schedules, alert timelines, service status, and escalation policies",
    }

    agent_descriptions = "\n".join(
        f"- **{agent_id}** ({agent.agent_type}): {agent_descs.get(agent_id, '')}"
        for agent_id, agent in sub_agents.items()
    )

    resource_section = ""
    resources_pre_resolved = ctx and ctx.resources and ctx.scoped_group_name
    if resources_pre_resolved:
        resource_lines = [
            f"\n\n## Resource Scope (Pre-resolved: {ctx.scoped_group_name})",  # type: ignore[union-attr]
            "",
            "Resources have been automatically resolved from the user's message. "
            "All sub-agents will receive these resources in their investigation context. "
            "Do NOT call `_get_connected_resources` — scoping is already done.",
            "",
            "Resolved resources:",
        ]
        for r in ctx.resources:  # type: ignore[union-attr]
            id_part = f" (id: {r.id})" if r.id else ""
            resource_lines.append(f"- [{r.type}] {r.name}{id_part}")
        resource_section = "\n".join(resource_lines)
    elif resource_maps:
        resource_section = """

## Resource Map

Resource maps are available for this account. After your initial investigation
reveals service or resource names, call `_get_connected_resources` with the
service name to find all related resources (databases, APM apps, error trackers,
alerting services) in the same group. Use the returned resources to scope
follow-up dispatch tasks."""

    return f"""{SYSTEM}

# Your Role: Investigation Orchestrator

You coordinate domain-specific investigation agents. You do NOT call data-source tools directly — instead you dispatch tasks to specialized sub-agents, evaluate their findings, and synthesize a comprehensive diagnosis.

## When to Respond Directly

For messages that do NOT require querying monitoring systems — greetings, casual conversation, clarification questions, general knowledge questions, or follow-up questions that can be answered from context already in the conversation — respond with text directly. Do NOT dispatch sub-agents for these.

## When to Dispatch

For ANY message that requires fetching live data from monitoring systems — whether it's a quick status check, a data query, or a full incident investigation — dispatch to the relevant sub-agents.

**Simple data queries** (status checks, listing resources, metric lookups): dispatch once and produce a final answer from the results. One iteration is sufficient.

**Investigations** (error diagnosis, incident analysis, performance issues): dispatch, evaluate, iterate, and synthesize a comprehensive diagnosis.

## Available Sub-Agents

{agent_descriptions}

## How Investigation Works

0. **Investigate**: Dispatch to the most relevant sub-agent first.
1. **Scope** (when a resource map is linked): After initial findings reveal service/resource names, call `_get_connected_resources`.
2. **Dispatch**: Use connected resources to dispatch targeted follow-up tasks.
3. **Evaluate**: After sub-agents report back, assess completeness.
4. **Iterate**: If gaps remain, dispatch follow-up tasks.
5. **Synthesize**: When complete, produce a final diagnosis.

## Dispatch Guidelines

- Only dispatch to agents whose domain is relevant.
- Include known context in the task: service names, time windows, error messages, resource IDs.
- For follow-up dispatches, reference specific findings from earlier rounds.

## Follow-Up Query Handling

When this is a follow-up message in an ongoing conversation (previous assistant responses exist):

1. **Answer from context first.** If previous sub-agent findings already contain the answer, respond directly — do NOT dispatch.
2. **Single-agent dispatch.** Follow-up data queries (counts, specific metrics, status checks) need at most ONE sub-agent. Dispatch to ONLY the single most relevant agent.
3. **Specific task descriptions.** Include all known context in the dispatch task:
   - Exact resource names and IDs from the investigation context (e.g., specific RDS instance names, log group paths, Sentry project slugs, New Relic entity GUIDs)
   - The specific metric, count, or data point the user is asking about
   - Explicit instruction: "Resources are already discovered in the investigation context — query directly without re-discovery."
4. **No re-investigation.** A follow-up like "how many X errors?" or "what are the slow queries?" should NOT trigger a multi-agent investigation. It needs a single targeted query to one agent.

Connected integrations: {', '.join(enabled_integrations)}
Current time: {now.isoformat()}{resource_section}"""


# ── Helpers ───────────────────────────────────────────────────

def _has_any_instance(settings: dict[str, Any], integration: str, required_suffix: str) -> bool:
    """Check if any indexed instance of an integration has the required key set."""
    prefix = f"{integration}."
    for key, val in settings.items():
        if key.startswith(prefix) and key.endswith(f".{required_suffix}") and val:
            return True
    return False


def _get_enabled_integrations(settings: dict[str, Any]) -> list[str]:
    enabled: list[str] = []
    if _has_any_instance(settings, "newrelic", "api_key") and _has_any_instance(settings, "newrelic", "account_id"):
        enabled.append("newrelic")
    if _has_any_instance(settings, "sentry", "auth_token") and _has_any_instance(settings, "sentry", "org"):
        enabled.append("sentry")
    if _has_any_instance(settings, "aws", "access_key_id") and _has_any_instance(settings, "aws", "secret_access_key"):
        enabled.append("aws")
    if _has_any_instance(settings, "github", "token"):
        enabled.append("github")
    if _has_any_instance(settings, "pagerduty", "api_key"):
        enabled.append("pagerduty")
    if _has_any_instance(settings, "digitalocean", "api_token"):
        enabled.append("digitalocean")
    return enabled


def _estimate_context_chars(input_items: list[Any]) -> int:
    """Estimate the total character count of all items in the context."""
    total = 0
    for item in input_items:
        if isinstance(item, dict):
            content = item.get("content") or item.get("output") or item.get("arguments") or ""
            total += len(str(content))
        else:
            total += len(str(item))
    return total


async def _summarize_older_findings(
    client: AsyncOpenAI,
    input_items: list[Any],
    summary_model: str,
) -> None:
    """Summarize older function_call_output items to reduce context size."""
    # Find function_call_output items (sub-agent findings)
    output_indices = [
        i for i, item in enumerate(input_items)
        if isinstance(item, dict) and item.get("type") == "function_call_output"
        and len(str(item.get("output", ""))) > 2000
    ]

    if len(output_indices) < 2:
        return  # Only summarize if we have multiple large outputs

    # Summarize all but the most recent output
    for idx in output_indices[:-1]:
        old_output = input_items[idx].get("output", "")
        if len(old_output) <= 2000:
            continue

        try:
            response = await client.chat.completions.create(
                model=summary_model,
                messages=[
                    {"role": "system", "content": "Summarize these investigation findings into key data points, metrics, and conclusions. Keep critical signals, error messages, and specific numbers. Be concise but don't lose important evidence."},
                    {"role": "user", "content": old_output[:15_000]},
                ],
                max_tokens=500,
            )
            summary = (response.choices[0].message.content or "").strip()
            if summary:
                input_items[idx]["output"] = f"[Summarized from earlier iteration]\n{summary}"
        except Exception as e:
            logger.warning("Context summarization failed: %s", e)


def _build_data_quality_summary(results: list[Any] | None) -> str:
    """Build a data quality summary from sub-agent results to inform evaluation."""
    if not results:
        return ""

    lines: list[str] = []
    for r in results:
        agent_id = r.agent_id
        total_tools = len(r.tools_used)
        failed_tools = sum(1 for tu in r.tools_used if tu.get("status") == "error")
        empty_tools = sum(
            1 for tu in r.tools_used
            if tu.get("status") == "success" and _is_empty_tool_output(tu.get("output", ""))
        )
        successful_tools = total_tools - failed_tools - empty_tools

        if total_tools == 0:
            lines.append(f"- **{agent_id}**: No tools were called. Sub-agent may have failed to start.")
        elif successful_tools == 0:
            lines.append(
                f"- **{agent_id}**: ALL {total_tools} tool calls returned errors ({failed_tools}) or empty data ({empty_tools}). "
                f"This sub-agent produced NO usable data. Confidence MUST be capped at 50% max."
            )
        elif failed_tools > 0 or empty_tools > 0:
            lines.append(
                f"- **{agent_id}**: {successful_tools}/{total_tools} tools returned usable data. "
                f"{failed_tools} failed, {empty_tools} returned empty/zero results."
            )
        else:
            lines.append(f"- **{agent_id}**: {successful_tools}/{total_tools} tools returned usable data.")

    return "\n".join(lines)


def _enrich_follow_up_tasks(
    assignments: list[dict[str, str]],
    ctx: InvestigationContext,
) -> list[dict[str, str]]:
    """Enrich dispatch tasks with known resource context for follow-up queries.

    Appends discovered resource names/IDs and a skip-discovery directive so
    sub-agents don't waste tool calls re-discovering what's already known.
    """
    if not ctx.resources:
        return assignments

    # Map agent types to relevant resource types
    agent_resource_types: dict[str, list[str]] = {
        "infrastructure": ["rds", "ec2", "lambda", "log_group"],
        "error_monitoring": ["sentry_project"],
        "apm": ["newrelic_app"],
        "alerting": [],  # PagerDuty doesn't need resource enrichment
    }

    enriched = []
    for a in assignments:
        agent_id = a["agentId"]
        task = a["task"]

        relevant_types = agent_resource_types.get(agent_id, [])
        relevant = [r for r in ctx.resources if r.type in relevant_types]

        if relevant:
            lines = []
            for r in relevant:
                id_part = f" (id: {r.id})" if r.id else ""
                useful_attrs = {k: v for k, v in r.attrs.items() if v and k not in ("name",)}
                attr_str = f" [{', '.join(f'{k}={v}' for k, v in useful_attrs.items())}]" if useful_attrs else ""
                lines.append(f"  - [{r.type}] {r.name}{id_part}{attr_str}")

            task += (
                "\n\nKnown resources (DO NOT re-discover — use these directly):\n"
                + "\n".join(lines)
                + "\n\nSkip all discovery steps (list orgs, list projects, describe instances). "
                "Query the data directly using the resource names/IDs above."
            )

        # Add entity names for the relevant platform
        entity_map = {"apm": "newrelic", "error_monitoring": "sentry", "alerting": "pagerduty"}
        platform = entity_map.get(agent_id)
        if platform and platform in ctx.entities:
            names = ctx.entities[platform]
            task += f"\n\nKnown {platform} entities: {', '.join(names)}"

        enriched.append({"agentId": agent_id, "task": task})

    return enriched


def _is_empty_tool_output(output: str) -> bool:
    """Check if a tool output is effectively empty (zero data / errors only)."""
    if not output or len(output) < 20:
        return True
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            # NRQL results with count: 0
            results = data.get("results", [])
            if results and all(r.get("count", -1) == 0 for r in results if isinstance(r, dict)):
                return True
            # NRQL TIMESERIES results where all data points are zero/null
            timeseries = data.get("timeSeries") or data.get("totalResult", {}).get("timeSeries")
            if not timeseries and isinstance(results, list) and results:
                # Check if results is a list of time-bucketed data (TIMESERIES format)
                # Each bucket looks like {"beginTimeSeconds": ..., "endTimeSeconds": ..., "throughput": 0, ...}
                if all(isinstance(r, dict) and "beginTimeSeconds" in r for r in results[:3]):
                    # Check if ALL numeric values across ALL buckets are 0 or null
                    all_zero = True
                    for r in results:
                        for k, v in r.items():
                            if k in ("beginTimeSeconds", "endTimeSeconds", "inspectedCount"):
                                continue
                            if isinstance(v, (int, float)) and v != 0:
                                all_zero = False
                                break
                            if isinstance(v, dict):
                                # Nested results like percentile: {"50": 0, "95": 0, "99": 0}
                                if any(isinstance(sv, (int, float)) and sv != 0 for sv in v.values()):
                                    all_zero = False
                                    break
                        if not all_zero:
                            break
                    if all_zero and len(results) > 0:
                        return True
            # Empty entity
            if data.get("entity") == {}:
                return True
            # Only errors, no results
            if data.get("errors") and not data.get("results"):
                return True
            # Empty Datapoints in CloudWatch response
            if isinstance(data.get("Datapoints"), list) and len(data["Datapoints"]) == 0:
                return True
            # Empty events in CloudWatch Logs response
            if isinstance(data.get("events"), list) and len(data["events"]) == 0:
                return True
    except (json.JSONDecodeError, TypeError):
        pass
    return False


def _gen_id() -> str:
    return str(uuid.uuid4())
