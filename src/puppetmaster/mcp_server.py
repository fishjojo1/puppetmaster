from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .errors import PuppetError
from .registry import Registry
from .services import (
    complete_agent,
    create_codex_agent,
    inspect_agent,
    kill_agent,
    pause_agent as pause_agent_service,
    prompt_agent,
    read_agent,
    resume_agent as resume_agent_service,
    schedule_wakeup,
    send_human_message as send_human_message_service,
    stop_agent,
)
from .skills import list_subagent_skills as list_subagent_skill_metadata
from .skills import subagent_skill
from .tmux import Tmux


def _context() -> tuple[Any, Registry, Tmux, dict[str, Any]]:
    cfg = load_config()
    reg = Registry(cfg)
    tmux = Tmux(cfg)
    agent_id = os.environ.get("PUPPETMASTER_AGENT_ID")
    if not agent_id:
        raise PuppetError("missing_caller", "Puppetmaster MCP is not running in a managed agent context.")
    caller = reg.get_agent(agent_id)
    return cfg, reg, tmux, caller


def _authorized(reg: Registry, caller: dict[str, Any], target_id: str, mutate: bool = False) -> None:
    if target_id == caller["id"]:
        return
    target = reg.get_agent(target_id)
    if caller["role"] == "orchestrator" and target["root_id"] == caller["root_id"]:
        return
    if target_id in reg.descendants(caller["id"]):
        return
    raise PuppetError("not_authorized", f"{caller['id']} is not authorized for {target_id}")


def _error(exc: PuppetError) -> dict:
    return exc.as_dict()


def _is_root_orchestrator(caller: dict[str, Any]) -> bool:
    return caller["role"] == "orchestrator" and caller["id"] == caller["root_id"]


def _compact_agent(agent: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": agent["id"],
        "parent_id": agent.get("parent_id"),
        "root_id": agent["root_id"],
        "role": agent["role"],
        "name": agent.get("name"),
        "description": agent.get("description"),
        "status": agent["status"],
        "completion_status": agent.get("completion_status"),
        "cwd": agent["cwd"],
        "depth": agent["depth"],
        "updated_at": agent.get("updated_at"),
        "completed_at": agent.get("completed_at"),
    }


def _completion_ack(result: dict[str, Any], summary: str) -> dict[str, Any]:
    agent = result["agent"]
    event = result.get("event") or {}
    return {
        "ok": True,
        "agent_id": agent["id"],
        "status": agent["status"],
        "completion_status": agent.get("completion_status"),
        "summary": summary,
        "event_id": event.get("id"),
        "injected": result.get("injected", False),
    }


mcp = FastMCP("puppetmaster")


@mcp.tool()
def create_agent(
    cwd: str,
    description: str | None = None,
    prompt: str | None = None,
    skill: str | None = None,
    goal: bool = False,
    name: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Create a child Codex agent.

    cwd is required and must be an existing absolute path. prompt is the child
    agent's initial task. skill is an optional subagent skill name returned by
    list_subagent_skills; when provided, Puppetmaster prepends that skill's
    instructions to the child prompt. goal is an optional boolean; when true, Puppetmaster
    starts the agent in goal mode by prepending literal "/goal " to the start of
    prompt. It does nothing else. description is a short human-readable label.
    """
    try:
        cfg, reg, tmux, caller = _context()
        base_task = (prompt or "").strip()
        if not base_task:
            raise PuppetError("prompt_required", "create_agent requires prompt.", "Pass a full prompt.")
        selected_skill = subagent_skill(skill)
        task = _task_with_goal_mode(_task_with_skill(base_task, selected_skill), goal)
        agent_metadata = dict(metadata or {})
        if selected_skill:
            agent_metadata["skill"] = selected_skill.name
        agent_description = (description or _description_from_task(prompt or task)).strip()
        agent = create_codex_agent(
            cfg,
            reg,
            tmux,
            cwd=cwd,
            description=agent_description,
            prompt=task,
            parent_id=caller["id"],
            name=name,
            metadata=agent_metadata,
        )
        return {
            "id": agent["id"],
            "status": agent["status"],
            "cwd": agent["cwd"],
            "skill": selected_skill.name if selected_skill else None,
            "attach_command": tmux.attach_command(agent["tmux_session"]),
        }
    except PuppetError as exc:
        return _error(exc)


def _description_from_task(task: str) -> str:
    first_line = next((line.strip() for line in task.splitlines() if line.strip()), "Child Codex agent")
    if len(first_line) <= 96:
        return first_line
    return first_line[:93].rstrip() + "..."


def _task_with_goal_mode(prompt: str | None, goal: bool) -> str:
    task = (prompt or "").strip()
    if goal and task:
        return f"/goal {task}"
    return task


def _task_with_skill(prompt: str, skill: Any | None) -> str:
    if skill is None:
        return prompt
    return f"""You are being started with Puppetmaster subagent skill `{skill.name}`.

Skill description:
{skill.description}

Skill instructions:
{skill.body}

Assigned task:
{prompt}"""


@mcp.tool()
def list_subagent_skills() -> dict:
    """List built-in subagent skills available for create_agent(skill=...)."""
    try:
        return {"skills": list_subagent_skill_metadata()}
    except PuppetError as exc:
        return _error(exc)


@mcp.tool(name="complete_agent")
def complete_agent_tool(
    status: str,
    summary: str,
    result: str | None = None,
    files_changed: list[str] | None = None,
    next_steps: list[str] | None = None,
    verbose: bool = False,
) -> dict:
    """Report this agent complete, failed, blocked, or cancelled."""
    try:
        cfg, reg, tmux, caller = _context()
        completion = complete_agent(
            reg,
            caller["id"],
            status=status,
            summary=summary,
            result=result,
            files_changed=files_changed,
            next_steps=next_steps,
            source="mcp_tool",
            config=cfg,
            tmux=tmux,
        )
        if verbose:
            return completion
        return _completion_ack(completion, summary)
    except PuppetError as exc:
        return _error(exc)


@mcp.tool(name="wait")
def wait_tool(seconds: int, reason: str | None = None) -> dict:
    """Schedule a wakeup and return immediately. End your turn after calling this."""
    try:
        cfg, reg, _tmux, caller = _context()
        wakeup = schedule_wakeup(cfg, reg, caller["id"], int(seconds), reason)
        return {
            "scheduled": True,
            "wakeup_id": wakeup["id"],
            "wake_at": wakeup["wake_at"],
            "instruction": "End your turn. Puppetmaster will send a wait-over message when the timer expires.",
        }
    except PuppetError as exc:
        return _error(exc)


@mcp.tool(name="read_agent")
def read_agent_tool(agent_id: str, lines: int = 120, source: str = "auto") -> dict:
    """Read recent terminal output for an authorized agent."""
    try:
        cfg, reg, tmux, caller = _context()
        _authorized(reg, caller, agent_id)
        return {"agent_id": agent_id, "output": read_agent(cfg, reg, tmux, agent_id, lines, source)}
    except PuppetError as exc:
        return _error(exc)


@mcp.tool(name="inspect_agent")
def inspect_agent_tool(
    agent_id: str,
    include_output: bool = False,
    include_events: bool = False,
    include_metadata: bool = False,
    include_children: bool = False,
    verbose: bool = False,
) -> dict:
    """Inspect metadata, state, children, events, and recent output for an authorized agent."""
    try:
        cfg, reg, tmux, caller = _context()
        _authorized(reg, caller, agent_id)
        if verbose:
            return inspect_agent(cfg, reg, tmux, agent_id)

        agent = reg.get_agent(agent_id)
        compact_agent = _compact_agent(agent)
        if include_metadata:
            compact_agent["metadata"] = agent.get("metadata") or {}
        response = {
            "agent": compact_agent,
            "tmux_session": agent["tmux_session"],
            "tmux_exists": tmux.session_exists(agent["tmux_session"]),
            "attach_command": tmux.attach_command(agent["tmux_session"]),
            "child_count": len(reg.children(agent_id)),
        }
        if include_children:
            response["children"] = [_compact_agent(child) for child in reg.children(agent_id)]
        if include_events:
            response["recent_events"] = reg.list_events(agent_id, limit=10)
            response["pending_deliveries"] = reg.pending_deliveries(agent_id, limit=10)
        if include_output:
            response["recent_output"] = read_agent(cfg, reg, tmux, agent_id, cfg.limits.default_log_lines, "auto")
        return response
    except PuppetError as exc:
        return _error(exc)


@mcp.tool()
def list_agents(
    root_id: str | None = None,
    parent_id: str | None = None,
    status: str | None = None,
    include_dead: bool = True,
    verbose: bool = False,
) -> dict:
    """List agents visible to the caller."""
    try:
        _cfg, reg, _tmux, caller = _context()
        root = root_id or caller["root_id"]
        if caller["role"] != "orchestrator" and root != caller["root_id"]:
            raise PuppetError("not_authorized", "caller cannot list another root tree")
        agents = reg.list_agents(root_id=root, parent_id=parent_id, status=status, include_dead=include_dead)
        if verbose:
            return {"agents": agents}
        return {"agents": [_compact_agent(agent) for agent in agents]}
    except PuppetError as exc:
        return _error(exc)


@mcp.tool(name="prompt_agent")
def prompt_agent_tool(agent_id: str, prompt: str) -> dict:
    """Send a prompt to an authorized live agent."""
    try:
        _cfg, reg, tmux, caller = _context()
        _authorized(reg, caller, agent_id, mutate=True)
        return prompt_agent(reg, tmux, agent_id, prompt, source="mcp_tool")
    except PuppetError as exc:
        return _error(exc)


@mcp.tool()
def send_human_message(message: str = "", file_path: str | None = None, filename: str | None = None) -> dict:
    """Send a concise message and optional file attachment to the bound human operator channel. Root orchestrators only."""
    try:
        _cfg, reg, _tmux, caller = _context()
        if not _is_root_orchestrator(caller):
            raise PuppetError(
                "not_authorized",
                "send_human_message is only available to root orchestrators.",
                "Child agents should report results through complete_agent or prompt their parent/root agent.",
            )
        return send_human_message_service(reg, caller["id"], message, file_path=file_path, filename=filename, source="mcp_tool")
    except PuppetError as exc:
        return _error(exc)


@mcp.tool(name="stop_agent")
def stop_agent_tool(agent_id: str) -> dict:
    """Gracefully stop an authorized agent session."""
    try:
        cfg, reg, tmux, caller = _context()
        _authorized(reg, caller, agent_id, mutate=True)
        return stop_agent(reg, tmux, agent_id, source="mcp_tool", config=cfg)
    except PuppetError as exc:
        return _error(exc)


@mcp.tool(name="kill_agent")
def kill_agent_tool(agent_id: str) -> dict:
    """Force-kill an authorized agent tmux session."""
    try:
        cfg, reg, tmux, caller = _context()
        _authorized(reg, caller, agent_id, mutate=True)
        return kill_agent(reg, tmux, agent_id, source="mcp_tool", config=cfg)
    except PuppetError as exc:
        return _error(exc)


@mcp.tool()
def pause_agent(agent_id: str) -> dict:
    """Mark an agent awaiting input. This does not suspend the process."""
    try:
        cfg, reg, tmux, caller = _context()
        _authorized(reg, caller, agent_id, mutate=True)
        return pause_agent_service(reg, agent_id, source="mcp_tool", config=cfg, tmux=tmux)
    except PuppetError as exc:
        return _error(exc)


@mcp.tool()
def resume_agent(agent_id: str) -> dict:
    """Mark an awaiting agent running. Use prompt_agent to actually send instructions."""
    try:
        cfg, reg, tmux, caller = _context()
        _authorized(reg, caller, agent_id, mutate=True)
        return resume_agent_service(reg, agent_id, source="mcp_tool", config=cfg, tmux=tmux)
    except PuppetError as exc:
        return _error(exc)


@mcp.tool()
def attach_agent(agent_id: str) -> dict:
    """Return the tmux attach command for an authorized agent."""
    try:
        _cfg, reg, tmux, caller = _context()
        _authorized(reg, caller, agent_id)
        agent = reg.get_agent(agent_id)
        return {"attach_command": tmux.attach_command(agent["tmux_session"])}
    except PuppetError as exc:
        return _error(exc)


def run() -> None:
    try:
        _cfg, _reg, _tmux, caller = _context()
        if not _is_root_orchestrator(caller):
            mcp.remove_tool("send_human_message")
    except PuppetError:
        pass
    mcp.run()
