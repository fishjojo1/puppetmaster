from __future__ import annotations

import json
import os
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import Config
from .errors import PuppetError, require
from .logging import log
from .model import COMPLETION_STATUSES, TERMINAL_STATUSES, json_dumps, new_id, now, validate_cwd
from .registry import Registry
from .tmux import Tmux


def agent_dir(config: Config, agent_id: str) -> Path:
    return config.agents_dir / agent_id


def make_tmux_session(config: Config, agent_id: str) -> str:
    return f"{config.tmux_session_prefix}{agent_id}"


def create_agent_record(
    config: Config,
    registry: Registry,
    *,
    cwd: str,
    description: str,
    role: str = "subagent",
    parent_id: str | None = None,
    root_id: str | None = None,
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cwd = validate_cwd(cwd)
    agent_id = new_id("agt")
    parent = registry.maybe_agent(parent_id) if parent_id else None
    depth = (parent["depth"] + 1) if parent else 0
    root_id = root_id or (parent["root_id"] if parent else agent_id)
    directory = agent_dir(config, agent_id)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "terminal.log"
    events_path = directory / "events.jsonl"
    return registry.create_agent(
        {
            "id": agent_id,
            "parent_id": parent_id,
            "root_id": root_id,
            "role": role,
            "name": name,
            "description": description,
            "cwd": cwd,
            "tmux_session": make_tmux_session(config, agent_id),
            "status": "starting",
            "depth": depth,
            "log_path": str(log_path),
            "events_path": str(events_path),
            "metadata": metadata or {},
        }
    )


def enforce_create_limits(config: Config, registry: Registry, parent: dict[str, Any] | None) -> None:
    total = registry.count_agents(parent["root_id"] if parent else None)
    require(
        total < config.limits.max_total_agents,
        "limit_exceeded",
        f"Cannot create agent: max_total_agents={config.limits.max_total_agents} is already reached.",
    )
    if parent:
        next_depth = int(parent["depth"]) + 1
        require(
            next_depth <= config.limits.max_depth,
            "limit_exceeded",
            f"Cannot create child agent: max_depth={config.limits.max_depth} would be exceeded by parent {parent['id']}.",
        )
        concurrent_children = sum(1 for child in registry.children(parent["id"]) if child["status"] not in TERMINAL_STATUSES)
        require(
            concurrent_children < config.limits.max_concurrent_children_per_agent,
            "limit_exceeded",
            f"Cannot create child agent: parent {parent['id']} already has max_concurrent_children_per_agent={config.limits.max_concurrent_children_per_agent}.",
        )


def create_raw_agent(
    config: Config,
    registry: Registry,
    tmux: Tmux,
    *,
    cwd: str,
    description: str,
    command: str,
    parent_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    parent = registry.maybe_agent(parent_id) if parent_id else None
    enforce_create_limits(config, registry, parent)
    log(config, "info", "agent.create.requested", "Creating raw agent", parent_id=parent_id)
    agent = create_agent_record(
        config,
        registry,
        cwd=cwd,
        description=description,
        role="subagent" if parent else "orchestrator",
        parent_id=parent_id,
        name=name,
        metadata={"runtime": "raw", "command": command},
    )
    try:
        tmux.create_session(agent["tmux_session"], agent["cwd"], command)
        tmux.pipe_pane(agent["tmux_session"], agent["log_path"])
        agent = registry.update_agent(agent["id"], status="running", started_at=now())
        registry.append_event(agent["id"], "agent.started", "Raw agent started.", {"command": command})
        return agent
    except PuppetError as exc:
        registry.update_agent(agent["id"], status="failed", termination_reason=exc.message)
        registry.append_event(agent["id"], "agent.failed", exc.message, exc.as_dict(), severity="error")
        raise


def read_log_file(path: str, lines: int) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    data = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(data[-lines:])


def read_agent(config: Config, registry: Registry, tmux: Tmux, agent_id: str, lines: int | None, source: str = "auto") -> str:
    agent = registry.get_agent(agent_id)
    line_count = min(int(lines or config.limits.default_log_lines), config.limits.max_log_read_lines)
    if source not in {"auto", "log", "tmux"}:
        raise PuppetError("invalid_source", f"invalid source: {source}", "Use auto, log, or tmux.")
    if source in {"auto", "tmux"} and tmux.session_exists(agent["tmux_session"]):
        try:
            return tmux.capture_pane(agent["tmux_session"], line_count)
        except PuppetError:
            if source == "tmux":
                raise
    return read_log_file(agent["log_path"], line_count)


def inspect_agent(config: Config, registry: Registry, tmux: Tmux, agent_id: str) -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    children = registry.children(agent_id)
    events = registry.list_events(agent_id, limit=10)
    pending = registry.pending_deliveries(agent_id, limit=10)
    tmux_exists = tmux.session_exists(agent["tmux_session"])
    return {
        "agent": agent,
        "tmux_exists": tmux_exists,
        "attach_command": tmux.attach_command(agent["tmux_session"]),
        "children": children,
        "recent_events": events,
        "pending_deliveries": pending,
        "recent_output": read_agent(config, registry, tmux, agent_id, config.limits.default_log_lines, "auto"),
    }


def stop_agent(registry: Registry, tmux: Tmux, agent_id: str, source: str = "human_cli") -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    tmux.stop_session(agent["tmux_session"])
    updated = registry.update_agent(agent_id, status="stopped", stopped_at=now(), termination_reason="stopped")
    registry.append_event(agent_id, "agent.stopped", "Agent stopped.", source=source)
    return updated


def kill_agent(registry: Registry, tmux: Tmux, agent_id: str, source: str = "human_cli") -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    tmux.kill_session(agent["tmux_session"])
    updated = registry.update_agent(agent_id, status="killed", stopped_at=now(), termination_reason="killed")
    registry.append_event(agent_id, "agent.killed", "Agent killed.", severity="warning", source=source)
    return updated


def cleanup_completed_agents(
    registry: Registry,
    tmux: Tmux,
    *,
    root_id: str | None = None,
    dry_run: bool = True,
    kill_stale: bool = False,
    include_roots: bool = False,
) -> dict[str, Any]:
    agents = registry.list_agents(root_id=root_id)
    by_id = {agent["id"]: agent for agent in agents}
    cleanup_statuses = {"completed", "stopped"}
    cleanup_ids = {
        agent["id"]
        for agent in agents
        if agent["status"] in cleanup_statuses and (include_roots or agent["parent_id"] is not None)
    }
    prunable = {
        agent_id
        for agent_id in cleanup_ids
        if all(descendant_id in cleanup_ids for descendant_id in registry.descendants(agent_id) if descendant_id in by_id)
    }
    pruned = sorted(prunable, key=lambda agent_id: int(by_id[agent_id]["depth"]), reverse=True)
    skipped = [
        {
            "agent_id": agent_id,
            "reason": "has non-completed-or-stopped descendants",
        }
        for agent_id in sorted(cleanup_ids - prunable)
    ]
    killed = []
    for agent_id in pruned:
        agent = by_id[agent_id]
        if kill_stale and tmux.session_exists(agent["tmux_session"]):
            if not dry_run:
                tmux.kill_session(agent["tmux_session"])
            killed.append(agent_id)
    if not dry_run:
        registry.delete_agents(pruned)
    return {
        "dry_run": dry_run,
        "root_id": root_id,
        "candidates": [by_id[agent_id] for agent_id in pruned],
        "pruned": pruned if not dry_run else [],
        "would_prune": pruned if dry_run else [],
        "killed": killed if not dry_run else [],
        "would_kill": killed if dry_run else [],
        "skipped": skipped,
        "logs_preserved": True,
    }


def prompt_agent(registry: Registry, tmux: Tmux, agent_id: str, prompt: str, source: str = "human_cli") -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    if not tmux.session_exists(agent["tmux_session"]):
        raise PuppetError("invalid_state", f"agent session is not live: {agent_id}", "Use agent read to inspect logs.")
    tmux.send_prompt(agent["tmux_session"], prompt)
    event = registry.append_event(agent_id, "agent.prompted", "Prompt delivered.", {"prompt_length": len(prompt)}, source=source)
    if agent["status"] in {"idle", "awaiting_input", "unknown"}:
        registry.update_agent(agent_id, status="running")
    return event


def complete_agent(
    registry: Registry,
    agent_id: str,
    *,
    status: str,
    summary: str,
    result: str | None = None,
    files_changed: list[str] | None = None,
    next_steps: list[str] | None = None,
    source: str = "mcp_tool",
    config: Config | None = None,
    tmux: Tmux | None = None,
) -> dict[str, Any]:
    if status not in COMPLETION_STATUSES:
        raise PuppetError("invalid_status", f"invalid completion status: {status}", "Use success, failed, blocked, or cancelled.")
    agent_status = {"success": "completed", "failed": "failed", "blocked": "blocked", "cancelled": "stopped"}[status]
    severity = {"success": "info", "failed": "error", "blocked": "warning", "cancelled": "warning"}[status]
    event_type = {"success": "agent.completed", "failed": "agent.failed", "blocked": "agent.blocked", "cancelled": "agent.cancelled"}[status]
    payload = {
        "completion_status": status,
        "result": result,
        "files_changed": files_changed or [],
        "next_steps": next_steps or [],
    }
    updated = registry.update_agent(
        agent_id,
        status=agent_status,
        completion_status=status,
        completed_at=now(),
        metadata_json=json_dumps({**agent_metadata(registry.get_agent(agent_id)), "completion": {"summary": summary, **payload}}),
    )
    event = registry.append_event(agent_id, event_type, summary, payload, severity=severity, source=source)
    recipients = []
    if updated.get("parent_id"):
        recipients.append(updated["parent_id"])
    if updated["root_id"] not in recipients and updated["root_id"] != agent_id:
        recipients.append(updated["root_id"])
    for recipient in recipients:
        registry.queue_delivery(event["id"], recipient)
    injected = False
    if config and tmux and updated["root_id"] != agent_id:
        injected = maybe_inject_event_prompt(config, registry, tmux, updated["root_id"])
    return {"agent": updated, "event": event, "deliveries": recipients, "injected": injected}


def agent_metadata(agent: dict[str, Any]) -> dict[str, Any]:
    return dict(agent.get("metadata") or {})


def handle_stop_hook(registry: Registry, agent_id: str, hook_input: str, source: str = "codex_hook") -> dict[str, Any]:
    try:
        payload = json.loads(hook_input) if hook_input.strip() else {}
    except json.JSONDecodeError as exc:
        event = registry.append_event(
            agent_id,
            "hook.failed",
            f"Stop hook received invalid JSON: {exc}",
            {"raw": hook_input[:4000]},
            severity="warning",
            source=source,
        )
        return {"event": event, "ok": False}
    agent = registry.get_agent(agent_id)
    event = registry.append_event(agent_id, "agent.turn_stopped", "Agent turn stopped.", payload, source=source)
    if agent["status"] not in TERMINAL_STATUSES:
        registry.update_agent(agent_id, status="idle", last_turn_stopped_at=now())
        if agent.get("parent_id"):
            registry.queue_delivery(event["id"], agent["parent_id"], coalesce=True)
        if agent["root_id"] != agent.get("parent_id") and agent["root_id"] != agent_id:
            registry.queue_delivery(event["id"], agent["root_id"], coalesce=True)
    return {"event": event, "ok": True}


def format_event_prompt(registry: Registry, deliveries: list[dict[str, Any]], remaining: int) -> str:
    header = "PUPPETMASTER EVENT" if len(deliveries) == 1 else "PUPPETMASTER EVENTS"
    parts = [header]
    for index, delivery in enumerate(deliveries, 1):
        agent = registry.get_agent(delivery["agent_id"])
        summary = delivery.get("summary", "")
        if len(summary) > 600:
            summary = summary[:597] + "..."
        parts.append(
            "\n".join(
                [
                    f"{index}. Agent {agent['id']} {delivery['type'].replace('agent.', '')}.",
                    f"Name: {agent.get('name') or '-'}",
                    f"Status: {agent['status']}",
                    f"Cwd: {agent['cwd']}",
                    f"Summary: {summary}",
                    "Available actions:",
                    f'- inspect_agent({{"agent_id":"{agent["id"]}"}})',
                    f'- read_agent({{"agent_id":"{agent["id"]}","lines":120}})',
                    f'- prompt_agent({{"agent_id":"{agent["id"]}","prompt":"..."}})',
                    f'- stop_agent({{"agent_id":"{agent["id"]}"}})',
                ]
            )
        )
    if remaining:
        parts.append(f"{remaining} more events remain queued. Call list_agents or inspect_agent if needed.")
    return "\n\n".join(parts)


def drain_events(config: Config, registry: Registry, agent_id: str) -> dict[str, Any]:
    deliveries = registry.pending_deliveries(agent_id, limit=config.limits.max_event_prompt_events)
    if not deliveries:
        return {"continue": True}
    all_pending = registry.pending_deliveries(agent_id, limit=None)
    remaining = max(0, len(all_pending) - len(deliveries))
    prompt = format_event_prompt(registry, deliveries, remaining)
    registry.mark_delivered([delivery["id"] for delivery in deliveries])
    return {"decision": "block", "reason": prompt}


def maybe_inject_event_prompt(config: Config, registry: Registry, tmux: Tmux, root_agent_id: str) -> bool:
    root = registry.maybe_agent(root_agent_id)
    if not root or root["status"] not in {"idle", "awaiting_input"}:
        return False
    if not tmux.session_exists(root["tmux_session"]):
        return False
    deliveries = registry.pending_deliveries(root_agent_id, limit=config.limits.max_event_prompt_events)
    if not deliveries:
        return False
    all_pending = registry.pending_deliveries(root_agent_id, limit=None)
    prompt = format_event_prompt(registry, deliveries, max(0, len(all_pending) - len(deliveries)))
    tmux.send_prompt(root["tmux_session"], prompt)
    registry.mark_delivered([delivery["id"] for delivery in deliveries])
    registry.update_agent(root_agent_id, status="running")
    registry.append_event(
        root_agent_id,
        "orchestrator.event_prompt_injected",
        "Pending Puppetmaster events were injected into the orchestrator tmux session.",
        {"delivery_ids": [delivery["id"] for delivery in deliveries]},
        source="supervisor",
    )
    return True


def discover_codex() -> dict[str, Any]:
    path = shutil.which("codex")
    if not path:
        raise PuppetError("codex_missing", "codex executable not found", "Install Codex CLI or add it to PATH.")
    version = subprocess.run([path, "--version"], text=True, capture_output=True)
    help_out = subprocess.run([path, "--help"], text=True, capture_output=True)
    supported = help_out.stdout + help_out.stderr
    missing = [flag for flag in ("--no-alt-screen", "--dangerously-bypass-approvals-and-sandbox", "--cd") if flag not in supported]
    if missing:
        raise PuppetError("codex_flag_missing", f"codex is missing required flags: {', '.join(missing)}")
    return {"path": path, "version": version.stdout.strip() or version.stderr.strip(), "required_flags": "ok"}


def prompt_text(agent: dict[str, Any], user_prompt: str) -> str:
    return f"""You are a Puppetmaster-managed Codex agent.

Task:
{user_prompt}

Agent:
- id: {agent['id']}
- parent_id: {agent.get('parent_id') or ''}
- root_id: {agent['root_id']}
- cwd: {agent['cwd']}
- description: {agent['description']}

Use Puppetmaster MCP tools for delegation and status. When the task is done, failed, or blocked,
call complete_agent with status success, failed, or blocked and a concise summary. A human may
attach to this tmux session at any time.
"""


def write_codex_files(config: Config, agent: dict[str, Any], user_prompt: str, orchestrator: bool = False) -> dict[str, str]:
    directory = agent_dir(config, agent["id"])
    codex_home = directory / "codex-config"
    hooks_dir = codex_home / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = directory / "initial-prompt.md"
    prompt_path.write_text(prompt_text(agent, user_prompt), encoding="utf-8")
    stop_hook = hooks_dir / "stop-hook"
    hook_command = "drain-events" if orchestrator else "stop"
    stop_hook.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
exec {sys.executable} -m puppetmaster.cli hook {hook_command} --agent-id "${{PUPPETMASTER_AGENT_ID}}"
""",
        encoding="utf-8",
    )
    stop_hook.chmod(0o755)
    env = {
        "PUPPETMASTER_AGENT_ID": agent["id"],
        "PUPPETMASTER_PARENT_AGENT_ID": agent.get("parent_id") or "",
        "PUPPETMASTER_ROOT_AGENT_ID": agent["root_id"],
        "PUPPETMASTER_STATE_DIR": str(config.state_dir),
        "PUPPETMASTER_CONFIG_DIR": str(codex_home),
        "PUPPETMASTER_ROLE": agent["role"],
        "PYTHONPATH": str(config.repo_dir / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    env_toml = "\n".join(f'{key} = "{value.replace(chr(34), chr(92)+chr(34))}"' for key, value in env.items())
    hooks_json = codex_home / "hooks.json"
    hook_hash = codex_hook_trust_hash(str(stop_hook), "Reporting Puppetmaster turn stop", 30)
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        f"""[features]
hooks = true

[hooks.state."{hooks_json}:stop:0:0"]
trusted_hash = "{hook_hash}"

[projects."{agent['cwd']}"]
trust_level = "trusted"

[mcp_servers.puppetmaster]
command = "{sys.executable}"
args = ["-m", "puppetmaster.cli", "mcp", "serve"]

[mcp_servers.puppetmaster.env]
{env_toml}
""",
        encoding="utf-8",
    )
    hooks_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": str(stop_hook),
                                    "timeout": 30,
                                    "statusMessage": "Reporting Puppetmaster turn stop",
                                }
                            ]
                        }
                    ]
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    auth_json = Path.home() / ".codex" / "auth.json"
    if auth_json.exists() and not (codex_home / "auth.json").exists():
        try:
            (codex_home / "auth.json").symlink_to(auth_json)
        except FileExistsError:
            pass
    launch = directory / "launch.sh"
    codex = discover_codex()["path"]
    flags = ["--cd", agent["cwd"]]
    if config.codex_no_alt_screen:
        flags.insert(0, "--no-alt-screen")
    if config.codex_bypass_approvals_and_sandbox:
        flags.insert(1, "--dangerously-bypass-approvals-and-sandbox")
    launch.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
export PUPPETMASTER_AGENT_ID={agent['id']!r}
export PUPPETMASTER_PARENT_AGENT_ID={agent.get('parent_id') or ''}
export PUPPETMASTER_ROOT_AGENT_ID={agent['root_id']!r}
export PUPPETMASTER_STATE_DIR={str(config.state_dir)!r}
export PUPPETMASTER_CONFIG_DIR={str(codex_home)!r}
export PUPPETMASTER_ROLE={agent['role']!r}
export PYTHONPATH={str(config.repo_dir / 'src')!r}${{PYTHONPATH:+:${{PYTHONPATH}}}}
export CODEX_HOME={str(codex_home)!r}
exec {codex!r} {' '.join(shlex_quote(flag) for flag in flags)} "$(cat {str(prompt_path)!r})"
""",
        encoding="utf-8",
    )
    launch.chmod(0o755)
    return {"prompt": str(prompt_path), "launch": str(launch), "config": str(config_toml), "hook": str(stop_hook)}


def codex_hook_trust_hash(command: str, status_message: str | None, timeout: int) -> str:
    handler: dict[str, Any] = {
        "type": "command",
        "command": command,
        "timeout": timeout,
        "async": False,
    }
    if status_message is not None:
        handler["statusMessage"] = status_message
    identity = {"event_name": "stop", "hooks": [handler]}
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def create_codex_agent(
    config: Config,
    registry: Registry,
    tmux: Tmux,
    *,
    cwd: str,
    description: str,
    prompt: str,
    parent_id: str | None = None,
    name: str | None = None,
    role: str = "subagent",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parent = registry.maybe_agent(parent_id) if parent_id else None
    enforce_create_limits(config, registry, parent)
    discover_codex()
    agent = create_agent_record(
        config,
        registry,
        cwd=cwd,
        description=description,
        role=role,
        parent_id=parent_id,
        root_id=parent["root_id"] if parent else None,
        name=name,
        metadata={"runtime": "codex", **(metadata or {})},
    )
    files = write_codex_files(config, agent, prompt, orchestrator=role == "orchestrator")
    agent = registry.update_agent(
        agent["id"],
        initial_prompt_path=files["prompt"],
        metadata_json=json_dumps({**agent_metadata(agent), "generated_files": files}),
    )
    try:
        tmux.create_session(agent["tmux_session"], agent["cwd"], files["launch"])
        tmux.pipe_pane(agent["tmux_session"], agent["log_path"])
        updated = registry.update_agent(agent["id"], status="running", started_at=now())
        registry.append_event(agent["id"], "agent.started", "Codex agent started.", {"files": files})
        return updated
    except PuppetError as exc:
        registry.update_agent(agent["id"], status="failed", termination_reason=exc.message)
        registry.append_event(agent["id"], "agent.failed", exc.message, exc.as_dict(), severity="error")
        raise


def start_orchestrator(
    config: Config,
    registry: Registry,
    tmux: Tmux,
    *,
    cwd: str,
    prompt: str,
    name: str | None = "root",
    new_root: bool = False,
) -> dict[str, Any]:
    if not new_root:
        for agent in registry.list_agents():
            if agent["role"] == "orchestrator" and agent["status"] in {"starting", "running", "idle", "awaiting_input"}:
                raise PuppetError("orchestrator_exists", f"running orchestrator already exists: {agent['id']}", "Use --new-root to start another.")
    return create_codex_agent(
        config,
        registry,
        tmux,
        cwd=cwd,
        description="Root Puppetmaster orchestrator",
        prompt=prompt,
        parent_id=None,
        name=name,
        role="orchestrator",
    )


def reconcile(config: Config, registry: Registry, tmux: Tmux, agent_id: str | None = None, root_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    agents = [registry.get_agent(agent_id)] if agent_id else registry.list_agents(root_id=root_id)
    changes = []
    tmux_names = {item["session"] for item in tmux.list_sessions(config.tmux_session_prefix)}
    for agent in agents:
        exists = agent["tmux_session"] in tmux_names
        proposed = None
        reason = None
        if agent["status"] in {"starting", "running", "idle", "awaiting_input", "unknown"} and not exists:
            proposed = "dead"
            reason = "registered nonterminal agent has no tmux session"
        elif agent["status"] == "running":
            stopped = registry.latest_event_time(agent["id"], "agent.turn_stopped")
            prompted = registry.latest_event_time(agent["id"], "agent.prompted")
            if stopped and (not prompted or stopped > prompted):
                proposed = "idle"
                reason = "Stop hook is newer than latest prompt"
        if proposed and proposed != agent["status"]:
            changes.append({"agent_id": agent["id"], "from": agent["status"], "to": proposed, "reason": reason})
            if not dry_run:
                registry.update_agent(agent["id"], status=proposed)
                event = registry.append_event(
                    agent["id"],
                    "agent.dead_detected" if proposed == "dead" else "supervisor.reconciled",
                    reason or "Reconciled agent status.",
                    {"from": agent["status"], "to": proposed, "tmux_exists": exists},
                    severity="warning" if proposed == "dead" else "info",
                )
                if proposed == "dead":
                    for recipient in {agent.get("parent_id"), agent["root_id"]} - {None, agent["id"]}:
                        registry.queue_delivery(event["id"], recipient)
    unmanaged_tmux = [name for name in tmux_names if not any(agent["tmux_session"] == name for agent in agents)]
    return {"changes": changes, "unmanaged_tmux": unmanaged_tmux, "dry_run": dry_run}


def doctor(config: Config, registry: Registry, tmux: Tmux, deep: bool = False) -> dict[str, Any]:
    checks = []
    ok = True

    def add(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        checks.append({"name": name, "ok": passed, "detail": detail})

    add("state_dir_writable", os.access(config.state_dir, os.W_OK), str(config.state_dir))
    try:
        tmux_path = tmux.require_tmux()
        version = subprocess.run(["tmux", "-V"], text=True, capture_output=True).stdout.strip()
        add("tmux", True, f"{tmux_path} {version}")
    except PuppetError as exc:
        add("tmux", False, exc.message)
    try:
        codex = discover_codex()
        add("codex", True, f"{codex['path']} {codex['version']}")
    except PuppetError as exc:
        add("codex", False, exc.message)
    with registry.connect() as conn:
        row = conn.execute("select version from schema_version order by version desc limit 1").fetchone()
        add("registry_schema", bool(row and row["version"] == 1), f"version={row['version'] if row else 'missing'}")
    if deep:
        rec = reconcile(config, registry, tmux, dry_run=True)
        add("registry_tmux_consistency", not rec["changes"], f"{len(rec['changes'])} proposed changes")
        for agent in registry.list_agents():
            generated = agent.get("metadata", {}).get("generated_files", {})
            hook = generated.get("hook")
            if hook:
                add(f"hook_executable:{agent['id']}", os.access(hook, os.X_OK), hook)
    return {"ok": ok, "checks": checks}
