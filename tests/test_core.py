from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from puppetmaster.config import load_config
from puppetmaster.errors import PuppetError
from puppetmaster.registry import Registry
import puppetmaster.services as services
from puppetmaster.services import (
    cleanup_completed_agents,
    complete_agent,
    create_agent_record,
    drain_events,
    handle_stop_hook,
    reconcile,
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
    assert data["features"]["hooks"] is True
    state_key = f"{Path(files['config']).parent / 'hooks.json'}:stop:0:0"
    assert data["hooks"]["state"][state_key]["trusted_hash"].startswith("sha256:")
    assert data["projects"][str(tmp_path)]["trust_level"] == "trusted"
    assert data["mcp_servers"]["puppetmaster"]["env"]["PUPPETMASTER_AGENT_ID"] == agent["id"]


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
