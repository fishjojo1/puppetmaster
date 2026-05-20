from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from puppetmaster.config import load_config
from puppetmaster.errors import PuppetError
from puppetmaster.registry import Registry
import puppetmaster.services as services
import puppetmaster.mcp_server as mcp_server
import puppetmaster.tmux as tmux_module
from puppetmaster.services import (
    cleanup_completed_agents,
    complete_agent,
    create_agent_record,
    drain_events,
    fire_due_wakeups,
    fire_wakeup,
    format_event_prompt,
    handle_stop_hook,
    inject_pending_prompt,
    kill_agent,
    pause_agent,
    prompt_text,
    reconcile,
    resume_agent,
    schedule_wakeup,
    stop_agent,
    write_codex_files,
)
from puppetmaster.tmux import Tmux
import puppetmaster.tui as tui_module
from puppetmaster.tui import TuiApp, build_tree_rows, format_tree_row, parse_context_left, summarize_tree


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(tmp_path / ".state"))
    cfg = load_config()
    return cfg, Registry(cfg), Tmux(cfg)


def test_registry_create_get_list_and_events(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    assert reg.get_agent(agent["id"])["cwd"] == str(tmp_path)
    assert reg.list_agents()[0]["id"] == agent["id"]
    reg.update_agent(agent["id"], status="running")
    event = reg.append_event(agent["id"], "agent.started", "started")
    assert reg.list_events(agent["id"])[0]["id"] == event["id"]


def test_cwd_validation_rejects_relative(ctx):
    cfg, reg, _tmux = ctx
    with pytest.raises(PuppetError) as exc:
        create_agent_record(cfg, reg, cwd="relative", description="bad")
    assert exc.value.code == "invalid_cwd"


def test_legacy_max_children_config_loads_as_concurrent_limit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "config.toml").write_text(
        """[limits]
max_children_per_agent = 7
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))

    cfg = load_config()

    assert cfg.limits.max_concurrent_children_per_agent == 7


def test_completion_queues_parent_and_root(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="parent", parent_id=root["id"])
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=parent["id"])
    result = complete_agent(reg, child["id"], status="success", summary="done")
    assert result["agent"]["status"] == "completed"
    recipients = {d["recipient_agent_id"] for d in reg.all_deliveries()}
    assert parent["id"] in recipients
    assert root["id"] in recipients


def test_drain_events_marks_delivered(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    complete_agent(reg, child["id"], status="blocked", summary="need input")
    result = drain_events(cfg, reg, root["id"])
    assert result["decision"] == "block"
    assert "PUPPETMASTER EVENT" in result["reason"]
    assert reg.pending_deliveries(root["id"]) == []


def test_schedule_wakeup_persists_and_spawns_helper(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    calls = []
    sleeps = []
    monkeypatch.setattr(services.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))

        class Process:
            pass

        return Process()

    monkeypatch.setattr(services.subprocess, "Popen", fake_popen)

    wakeup = schedule_wakeup(cfg, reg, agent["id"], 30, "waiting for child agents")

    assert wakeup["status"] == "scheduled"
    assert wakeup["agent_id"] == agent["id"]
    assert wakeup["reason"] == "waiting for child agents"
    assert wakeup["payload"] == {"seconds": 30}
    assert calls[0][0][-3:] == ["sleep-and-fire", "--wakeup-id", wakeup["id"]]
    assert sleeps == []


def test_schedule_wakeup_rejects_invalid_waits(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")

    with pytest.raises(PuppetError) as zero:
        schedule_wakeup(cfg, reg, agent["id"], 0, spawn_helper=False)
    assert zero.value.code == "invalid_wait"

    with pytest.raises(PuppetError) as too_large:
        schedule_wakeup(cfg, reg, agent["id"], cfg.limits.max_wait_seconds + 1, spawn_helper=False)
    assert too_large.value.code == "invalid_wait"


def test_child_agent_can_schedule_wakeup(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])

    wakeup = schedule_wakeup(cfg, reg, child["id"], 5, spawn_helper=False)

    assert wakeup["agent_id"] == child["id"]
    assert wakeup["root_id"] == root["id"]


def test_mcp_wait_tool_schedules_for_caller_and_returns_instruction(ctx, tmp_path, monkeypatch):
    cfg, reg, tmux = ctx
    caller = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    monkeypatch.setattr(mcp_server, "_context", lambda: (cfg, reg, tmux, caller))
    monkeypatch.setattr(services, "spawn_wakeup_helper", lambda config, wakeup_id: None)

    result = mcp_server.wait_tool(10, "backoff")

    assert result["scheduled"] is True
    assert result["instruction"].startswith("End your turn.")
    wakeup = reg.get_wakeup(result["wakeup_id"])
    assert wakeup["agent_id"] == caller["id"]
    assert wakeup["reason"] == "backoff"


def test_fire_wakeup_marks_fired_queues_wait_over_and_is_idempotent(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    wakeup = schedule_wakeup(cfg, reg, agent["id"], 5, "poll again", spawn_helper=False)

    first = fire_wakeup(cfg, reg, wakeup["id"])
    second = fire_wakeup(cfg, reg, wakeup["id"])

    assert first["fired"] is True
    assert second["fired"] is False
    assert reg.get_wakeup(wakeup["id"])["status"] == "fired"
    events = reg.list_events(agent["id"], limit=10)
    assert [event["type"] for event in events].count("agent.wait_over") == 1
    delivery = reg.pending_deliveries(agent["id"])[0]
    assert delivery["type"] == "agent.wait_over"
    assert delivery["payload"]["reason"] == "poll again"


def test_fire_due_wakeups_fires_overdue_scheduled_wakeups(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    wakeup = schedule_wakeup(cfg, reg, agent["id"], 300, spawn_helper=False)
    with reg.connect() as conn:
        conn.execute("update scheduled_wakeups set wake_at=? where id=?", ("2000-01-01T00:00:00Z", wakeup["id"]))

    result = fire_due_wakeups(cfg, reg, agent_id=agent["id"])

    assert result["count"] == 1
    assert reg.get_wakeup(wakeup["id"])["status"] == "fired"


def test_drain_events_fires_due_wakeup_before_delivery_lookup(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    wakeup = schedule_wakeup(cfg, reg, agent["id"], 300, "wake me", spawn_helper=False)
    with reg.connect() as conn:
        conn.execute("update scheduled_wakeups set wake_at=? where id=?", ("2000-01-01T00:00:00Z", wakeup["id"]))

    result = drain_events(cfg, reg, agent["id"])

    assert result["decision"] == "block"
    assert "PUPPETMASTER WAIT OVER" in result["reason"]
    assert "Wait over for agent" in result["reason"]
    assert reg.pending_deliveries(agent["id"]) == []


def test_format_event_prompt_renders_state_change_and_wait_over(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    complete_agent(reg, child["id"], status="blocked", summary="need input")

    state_prompt = format_event_prompt(reg, reg.pending_deliveries(root["id"]), 0)

    assert f"{child['id']} has new state blocked." in state_prompt
    reg.mark_delivered([delivery["id"] for delivery in reg.pending_deliveries(root["id"])])

    wakeup = schedule_wakeup(cfg, reg, root["id"], 5, "retry", spawn_helper=False)
    fire_wakeup(cfg, reg, wakeup["id"])
    wait_prompt = format_event_prompt(reg, reg.pending_deliveries(root["id"]), 0)

    assert wait_prompt.startswith("PUPPETMASTER WAIT OVER")
    assert "Reason: retry" in wait_prompt


def test_stop_kill_pause_resume_queue_parent_and_root_notifications(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="parent", parent_id=root["id"])
    children = [
        create_agent_record(cfg, reg, cwd=str(tmp_path), description=f"child {index}", parent_id=parent["id"])
        for index in range(4)
    ]

    class FakeTmux:
        def stop_session(self, session):
            pass

        def kill_session(self, session):
            pass

        def session_exists(self, session):
            return False

    fake = FakeTmux()
    stop_agent(reg, fake, children[0]["id"], config=cfg)
    kill_agent(reg, fake, children[1]["id"], config=cfg)
    pause_agent(reg, children[2]["id"], config=cfg, tmux=fake)
    resume_agent(reg, children[3]["id"], config=cfg, tmux=fake)

    parent_types = [delivery["type"] for delivery in reg.pending_deliveries(parent["id"])]
    root_types = [delivery["type"] for delivery in reg.pending_deliveries(root["id"])]
    for event_type in ("agent.stopped", "agent.killed", "agent.paused", "agent.resumed"):
        assert event_type in parent_types
        assert event_type in root_types


def test_inject_pending_prompt_does_not_require_idle_status(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    reg.update_agent(root["id"], status="running")
    complete_agent(reg, child["id"], status="success", summary="done")

    class FakeTmux:
        prompts = []

        def session_exists(self, session):
            return True

        def send_prompt(self, session, prompt):
            self.prompts.append((session, prompt))

    fake = FakeTmux()

    assert inject_pending_prompt(cfg, reg, fake, root["id"]) is True
    assert fake.prompts[0][0] == root["tmux_session"]
    assert "PUPPETMASTER EVENT" in fake.prompts[0][1]
    assert reg.pending_deliveries(root["id"]) == []


def test_create_limit_counts_concurrent_children_only(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="parent", role="orchestrator")
    children = [
        create_agent_record(cfg, reg, cwd=str(tmp_path), description=f"child {index}", parent_id=parent["id"])
        for index in range(5)
    ]
    for child, status in zip(children, ["running", "idle", "awaiting_input", "completed", "stopped"], strict=True):
        reg.update_agent(child["id"], status=status)

    services.enforce_create_limits(cfg, reg, parent)

    extra_running_1 = create_agent_record(cfg, reg, cwd=str(tmp_path), description="extra 1", parent_id=parent["id"])
    extra_running_2 = create_agent_record(cfg, reg, cwd=str(tmp_path), description="extra 2", parent_id=parent["id"])
    reg.update_agent(extra_running_1["id"], status="running")
    reg.update_agent(extra_running_2["id"], status="starting")

    with pytest.raises(PuppetError) as exc:
        services.enforce_create_limits(cfg, reg, parent)
    assert exc.value.code == "limit_exceeded"
    assert "max_concurrent_children_per_agent=5" in exc.value.message


def test_stop_hook_marks_idle_and_coalesces(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    reg.update_agent(child["id"], status="running")
    handle_stop_hook(reg, child["id"], "{}")
    handle_stop_hook(reg, child["id"], "{}")
    assert reg.get_agent(child["id"])["status"] == "idle"
    assert len(reg.pending_deliveries(root["id"])) == 1


def test_reconcile_running_missing_tmux_becomes_dead(ctx, tmp_path, monkeypatch):
    cfg, reg, tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.update_agent(agent["id"], status="running")
    monkeypatch.setattr(tmux, "list_sessions", lambda prefix=None: [])
    result = reconcile(cfg, reg, tmux)
    assert result["changes"][0]["to"] == "dead"
    assert reg.get_agent(agent["id"])["status"] == "dead"


def test_generated_codex_config_shape(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    files = write_codex_files(cfg, agent, "hello", orchestrator=True)
    data = tomllib.loads(Path(files["config"]).read_text(encoding="utf-8"))
    launch = Path(files["launch"]).read_text(encoding="utf-8")
    assert data["features"]["hooks"] is True
    state_key = f"{Path(files['config']).parent / 'hooks.json'}:stop:0:0"
    assert data["hooks"]["state"][state_key]["trusted_hash"].startswith("sha256:")
    assert data["projects"][str(tmp_path)]["trust_level"] == "trusted"
    assert data["mcp_servers"]["puppetmaster"]["env"]["PUPPETMASTER_AGENT_ID"] == agent["id"]
    assert "initial-prompt.md" not in launch
    assert '"$(cat ' not in launch


def test_prompt_text_explains_orchestrator_wait_and_event_loop(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])

    root_prompt = prompt_text(root, "Coordinate the work.")
    child_prompt = prompt_text(child, "Run the tests.")

    assert "If you are only waiting for child-agent progress, do not call wait(). Simply end your turn." in root_prompt
    assert "Call wait(seconds, reason) only when you need a time-based wakeup" in root_prompt
    assert "Puppetmaster will send you a fresh PUPPETMASTER EVENT message" in root_prompt
    assert "PUPPETMASTER WAIT OVER" in root_prompt
    assert "If you are only waiting for child-agent progress" not in child_prompt
    assert "Use wait(seconds, reason) only for a time-based wakeup." in child_prompt


def test_create_codex_agent_launches_interactive_session_then_sends_initial_prompt(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    sleeps = []
    monkeypatch.setattr(services.time, "sleep", lambda seconds: sleeps.append(seconds))

    class FakeTmux:
        def __init__(self):
            self.created = []
            self.piped = []
            self.prompts = []

        def create_session(self, session, cwd, command):
            self.created.append((session, cwd, command))

        def pipe_pane(self, session, log_path):
            self.piped.append((session, log_path))

        def send_prompt(self, session, prompt):
            self.prompts.append((session, prompt))

    fake = FakeTmux()

    agent = services.create_codex_agent(
        cfg,
        reg,
        fake,
        cwd=str(tmp_path),
        description="root",
        prompt="Coordinate child work.",
        role="orchestrator",
    )

    launch = Path(agent["metadata"]["generated_files"]["launch"]).read_text(encoding="utf-8")
    assert '"$(cat ' not in launch
    assert len(fake.created) == 1
    assert len(fake.piped) == 1
    assert fake.prompts[0][0] == agent["tmux_session"]
    assert "Task:\nCoordinate child work." in fake.prompts[0][1]
    assert "Orchestrator event loop:" in fake.prompts[0][1]
    assert sleeps == [services.INITIAL_PROMPT_SEND_DELAY_SECONDS]


def test_generated_codex_config_preserves_user_defaults(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        """model = "gpt-custom"
approval_policy = "on-request"

[features]
hooks = false

[profiles.default]
model = "gpt-profile"

[mcp_servers.existing]
command = "existing-server"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")

    files = write_codex_files(cfg, agent, "hello", orchestrator=True)

    data = tomllib.loads(Path(files["config"]).read_text(encoding="utf-8"))
    assert data["model"] == "gpt-custom"
    assert data["approval_policy"] == "on-request"
    assert data["profiles"]["default"]["model"] == "gpt-profile"
    assert data["mcp_servers"]["existing"]["command"] == "existing-server"
    assert data["features"]["hooks"] is True
    assert data["mcp_servers"]["puppetmaster"]["env"]["PUPPETMASTER_AGENT_ID"] == agent["id"]


def test_tmux_send_prompt_pastes_and_confirms_with_second_enter(ctx, monkeypatch):
    _cfg, _reg, tmux = ctx
    calls = []
    sleeps = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(tmux, "require_tmux", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)
    monkeypatch.setattr(tmux_module.time, "sleep", lambda seconds: sleeps.append(seconds))

    tmux.send_prompt("puppet_agt_child", "continue")

    commands = [call[0] for call in calls]
    assert commands[:4] == [
        ["tmux", "set-buffer", "-b", commands[0][3], "continue"],
        ["tmux", "paste-buffer", "-b", commands[0][3], "-t", "puppet_agt_child"],
        ["tmux", "send-keys", "-t", "puppet_agt_child", "Enter"],
        ["tmux", "send-keys", "-t", "puppet_agt_child", "Enter"],
    ]
    assert commands[4] == ["tmux", "delete-buffer", "-b", commands[0][3]]
    assert sleeps == [tmux_module.PROMPT_SUBMIT_CONFIRM_DELAY_SECONDS]


def test_mcp_create_agent_prepends_goal_mode_to_prompt(tmp_path, monkeypatch):
    captured = {}
    caller = {"id": "agt_parent"}

    class FakeTmux:
        def attach_command(self, session):
            return f"tmux attach -t {session}"

    def fake_create_codex_agent(cfg, reg, tmux, **kwargs):
        captured.update(kwargs)
        return {"id": "agt_child", "status": "running", "cwd": kwargs["cwd"], "tmux_session": "puppet_agt_child"}

    monkeypatch.setattr(mcp_server, "_context", lambda: (object(), object(), FakeTmux(), caller))
    monkeypatch.setattr(mcp_server, "create_codex_agent", fake_create_codex_agent)

    result = mcp_server.create_agent(cwd=str(tmp_path), prompt="Run the focused smoke tests", goal=True)

    assert result["id"] == "agt_child"
    assert captured["prompt"] == "/goal Run the focused smoke tests"
    assert captured["description"] == "Run the focused smoke tests"
    assert captured["parent_id"] == caller["id"]
    assert captured["metadata"] == {}


def test_mcp_create_agent_leaves_prompt_unchanged_without_goal_mode(tmp_path, monkeypatch):
    captured = {}
    caller = {"id": "agt_parent"}

    class FakeTmux:
        def attach_command(self, session):
            return f"tmux attach -t {session}"

    def fake_create_codex_agent(cfg, reg, tmux, **kwargs):
        captured.update(kwargs)
        return {"id": "agt_child", "status": "running", "cwd": kwargs["cwd"], "tmux_session": "puppet_agt_child"}

    monkeypatch.setattr(mcp_server, "_context", lambda: (object(), object(), FakeTmux(), caller))
    monkeypatch.setattr(mcp_server, "create_codex_agent", fake_create_codex_agent)

    result = mcp_server.create_agent(cwd=str(tmp_path), prompt="Use the release workspace.", goal=False)

    assert result["id"] == "agt_child"
    assert captured["prompt"] == "Use the release workspace."
    assert captured["metadata"] == {}


def test_mcp_create_agent_requires_prompt_when_goal_omitted(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "_context", lambda: (object(), object(), object(), {"id": "agt_parent"}))

    result = mcp_server.create_agent(cwd=str(tmp_path))

    assert result["error"]["code"] == "prompt_required"


def test_completion_injects_pending_event_when_root_idle(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    reg.update_agent(root["id"], status="idle")

    class FakeTmux:
        prompts = []

        def session_exists(self, session):
            return True

        def send_prompt(self, session, prompt):
            self.prompts.append((session, prompt))

    fake = FakeTmux()
    result = complete_agent(reg, child["id"], status="success", summary="done", config=cfg, tmux=fake)
    assert result["injected"] is True
    assert "PUPPETMASTER EVENT" in fake.prompts[0][1]
    assert reg.pending_deliveries(root["id"]) == []


def test_tui_builds_nested_tree_rows(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator", name="root")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"], name="child")
    grandchild = create_agent_record(cfg, reg, cwd=str(tmp_path), description="grandchild", parent_id=child["id"])

    rows = build_tree_rows(reg.list_agents())

    assert [row["agent"]["id"] for row in rows] == [root["id"], child["id"], grandchild["id"]]
    assert [row["depth"] for row in rows] == [0, 1, 2]
    assert rows[0]["has_children"] is True
    assert rows[2]["has_children"] is False
    assert "live child" in format_tree_row(rows[1], live=True)


def test_tui_parses_context_left():
    assert parse_context_left("Context left: 42%") == "42%"
    assert parse_context_left("status 17.5% context remaining") == "17.5%"
    assert parse_context_left("\x1b[32mContext: 120k/200k\x1b[0m") == "120k/200k"
    assert parse_context_left("no context metric here") is None


def test_tui_strips_terminal_control_sequences_from_log_preview():
    raw = "\x1b[?1049l\x1b[?25h\x1b]0;agent title\x07done\x1b[0m\x1b7clean\x1b8\x1b]2;unterminated"

    assert tui_module.strip_ansi(raw) == "doneclean"


def test_tui_refresh_preview_sanitizes_stopped_agent_log(ctx, tmp_path):
    cfg, reg, tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    Path(agent["log_path"]).write_text("before\n\x1b[?1049l\x1b]0;title\x07after\x1b[0m\n", encoding="utf-8")

    app = TuiApp(cfg, reg, tmux, root_id=None, refresh=1.0, lines=120)
    app.rows = build_tree_rows([agent])
    app.live_sessions = set()
    app.refresh_preview()

    assert app.preview == "before\nafter"


def test_tui_summarizes_tree_stats(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    reg.update_agent(root["id"], status="running")
    reg.update_agent(child["id"], status="idle")

    agents = reg.list_agents()
    stats = summarize_tree(agents, {root["tmux_session"]})

    assert stats["total"] == 2
    assert stats["live"] == 1
    assert stats["max_depth"] == 1
    assert "idle:1" in stats["statuses"]
    assert "running:1" in stats["statuses"]


def test_tui_preview_scroll_is_bounded(ctx):
    cfg, reg, tmux = ctx

    class Screen:
        def getmaxyx(self):
            return (23, 80)

    app = TuiApp(cfg, reg, tmux, root_id=None, refresh=1.0, lines=120)
    app.preview = "\n".join(f"line {index}" for index in range(20))
    screen = Screen()

    assert app.preview_page_size(screen) == 5
    assert app.max_preview_scroll(screen) == 15

    app.scroll_preview(10, screen)
    assert app.preview_scroll == 10

    app.scroll_preview(10, screen)
    assert app.preview_scroll == 15

    app.scroll_preview(-100, screen)
    assert app.preview_scroll == 0


def test_tui_mouse_wheel_on_right_scrolls_preview_not_selection(ctx, monkeypatch):
    cfg, reg, tmux = ctx
    monkeypatch.setattr(tui_module.curses, "BUTTON4_PRESSED", 1, raising=False)

    class Screen:
        def getmaxyx(self):
            return (23, 90)

    app = TuiApp(cfg, reg, tmux, root_id=None, refresh=1.0, lines=120)
    app.preview = "\n".join(f"line {index}" for index in range(20))
    app.selected = 4

    app.handle_mouse_event(TuiApp.right_x(90), 17, 1, Screen())

    assert app.preview_scroll == 3
    assert app.selected == 4


def test_cleanup_completed_prunes_completed_subtrees_and_preserves_active_descendants(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    completed_parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="done parent", parent_id=root["id"])
    completed_child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="done child", parent_id=completed_parent["id"])
    mixed_parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="mixed parent", parent_id=root["id"])
    active_child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="active child", parent_id=mixed_parent["id"])
    root_completed_child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="done root child", parent_id=root["id"])

    for agent in (root, completed_parent, completed_child, mixed_parent, root_completed_child):
        reg.update_agent(agent["id"], status="completed")
    reg.update_agent(active_child["id"], status="running")

    dry_run = cleanup_completed_agents(reg, tmux, dry_run=True)
    assert set(dry_run["would_prune"]) == {completed_parent["id"], completed_child["id"], root_completed_child["id"]}
    assert dry_run["pruned"] == []
    assert reg.maybe_agent(completed_child["id"]) is not None

    result = cleanup_completed_agents(reg, tmux, dry_run=False)
    assert set(result["pruned"]) == {completed_parent["id"], completed_child["id"], root_completed_child["id"]}
    assert reg.maybe_agent(completed_parent["id"]) is None
    assert reg.maybe_agent(completed_child["id"]) is None
    assert reg.maybe_agent(root_completed_child["id"]) is None
    assert reg.maybe_agent(root["id"]) is not None
    assert reg.maybe_agent(mixed_parent["id"]) is not None
    assert reg.maybe_agent(active_child["id"]) is not None
    assert result["logs_preserved"] is True


def test_cleanup_completed_prunes_subtrees_with_pending_deliveries(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="parent", parent_id=root["id"])
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=parent["id"])

    complete_agent(reg, parent["id"], status="success", summary="done")
    complete_agent(reg, child["id"], status="success", summary="done")

    result = cleanup_completed_agents(reg, tmux, dry_run=False)
    assert set(result["pruned"]) == {parent["id"], child["id"]}
    assert reg.maybe_agent(parent["id"]) is None
    assert reg.maybe_agent(child["id"]) is None
    assert reg.maybe_agent(root["id"]) is not None
    assert reg.pending_deliveries(root["id"]) == []


def test_cleanup_completed_prunes_stopped_subtrees(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    stopped_parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="stopped parent", parent_id=root["id"])
    stopped_child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="stopped child", parent_id=stopped_parent["id"])
    completed_parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="completed parent", parent_id=root["id"])
    stopped_leaf = create_agent_record(cfg, reg, cwd=str(tmp_path), description="stopped leaf", parent_id=completed_parent["id"])
    active_child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="active child", parent_id=stopped_leaf["id"])

    for agent in (stopped_parent, stopped_child, stopped_leaf):
        reg.update_agent(agent["id"], status="stopped")
    reg.update_agent(completed_parent["id"], status="completed")
    reg.update_agent(active_child["id"], status="running")

    dry_run = cleanup_completed_agents(reg, tmux, dry_run=True)
    assert set(dry_run["would_prune"]) == {stopped_parent["id"], stopped_child["id"]}
    assert {item["agent_id"] for item in dry_run["skipped"]} == {completed_parent["id"], stopped_leaf["id"]}

    result = cleanup_completed_agents(reg, tmux, dry_run=False)
    assert set(result["pruned"]) == {stopped_parent["id"], stopped_child["id"]}
    assert reg.maybe_agent(stopped_parent["id"]) is None
    assert reg.maybe_agent(stopped_child["id"]) is None
    assert reg.maybe_agent(completed_parent["id"]) is not None
    assert reg.maybe_agent(stopped_leaf["id"]) is not None
    assert reg.maybe_agent(active_child["id"]) is not None
