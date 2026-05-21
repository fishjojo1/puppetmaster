from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from puppetmaster.config import DiscordConfig, load_config
from puppetmaster import discord_bot as discord_bot_module
from puppetmaster.discord_bot import (
    DISCORD_PROMPT_PREFIX,
    DiscordRuntime,
    NOT_BOUND_REPLY,
    PROMPT_DELIVERED_REACTION,
    TEXT_ONLY_REPLY,
    build_discord_bot,
    chunk_text,
    code_block,
    handle_agents_command,
    handle_bind_command,
    handle_compact_command,
    handle_read_command,
    handle_screenshot_command,
    handle_status_command,
    handle_tree_command,
    handle_unbind_command,
    send_plain_chunks,
    send_chunks,
    validate_discord_config,
)
from puppetmaster.errors import PuppetError
from puppetmaster.registry import Registry
from puppetmaster.services import create_agent_record, handle_stop_hook, send_human_message
from puppetmaster.tmux import Tmux


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(tmp_path / ".state"))
    cfg = load_config()
    return cfg, Registry(cfg), Tmux(cfg)


class FakeGuild:
    id = "guild-1"


class FakeTextChannel:
    id = "channel-1"
    guild = FakeGuild()
    is_text_channel = True


class FakeOtherChannel:
    id = "channel-2"
    guild = FakeGuild()
    is_text_channel = False


class FakeDiscordHTTPException(Exception):
    pass


class FakeDiscordForbidden(FakeDiscordHTTPException):
    status = 403
    code = 50001
    text = "Missing Access"


class FakeDiscordIntents:
    @classmethod
    def default(cls) -> "FakeDiscordIntents":
        return cls()

    def __init__(self) -> None:
        self.message_content = False


class FakeDiscordObject:
    def __init__(self, id: int):
        self.id = id


class FakeDiscordModule:
    HTTPException = FakeDiscordHTTPException
    Forbidden = FakeDiscordForbidden
    Intents = FakeDiscordIntents
    Object = FakeDiscordObject


class FakeAppCommandGroup:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.commands: list[object] = []

    def command(self, name: str, description: str):
        def decorator(func):
            self.commands.append((name, description, func))
            return func

        return decorator


class FakeAppCommandsModule:
    Group = FakeAppCommandGroup

    @staticmethod
    def describe(**_kwargs):
        def decorator(func):
            return func

        return decorator


class FakeDiscordTree:
    def __init__(self) -> None:
        self.added: list[tuple[object, object]] = []

    def add_command(self, group: object, guild: object) -> None:
        self.added.append((group, guild))

    async def sync(self, guild: object) -> list[object]:
        raise FakeDiscordForbidden()


class FakeCommandsBot:
    def __init__(self, command_prefix: str, intents: FakeDiscordIntents) -> None:
        self.command_prefix = command_prefix
        self.intents = intents
        self.tree = FakeDiscordTree()
        self.events: dict[str, object] = {}

    def event(self, func):
        self.events[func.__name__] = func
        return func

    async def close(self) -> None:
        return None

    async def process_commands(self, message: object) -> None:
        return None


class FakeCommandsModule:
    Bot = FakeCommandsBot


class FakeTmux:
    def __init__(self, live: bool = False, pane_text: str = ""):
        self.live = live
        self.pane_text = pane_text
        self.checked: list[str] = []
        self.captured: list[str] = []
        self.sent_prompts: list[tuple[str, str]] = []

    def session_exists(self, session: str) -> bool:
        self.checked.append(session)
        return self.live

    def capture_visible_pane(self, session: str) -> str:
        self.captured.append(session)
        return self.pane_text

    def send_prompt(self, session: str, prompt: str) -> None:
        self.sent_prompts.append((session, prompt))


class FakeDiscordUser:
    def __init__(self, user_id: str, *, mention: str | None = None):
        self.id = user_id
        self.mention = mention or f"<@{user_id}>"


class FakeDiscordGuild:
    def __init__(self, guild_id: str = "123"):
        self.id = guild_id


class FakeTyping:
    def __init__(self, channel: "FakeDiscordChannel"):
        self.channel = channel

    async def __aenter__(self):
        self.channel.typing_entries += 1

    async def __aexit__(self, exc_type, exc, tb):
        self.channel.typing_exits += 1


class FakeDiscordChannel:
    def __init__(self, channel_id: str = "456", guild: FakeDiscordGuild | None = None):
        self.id = channel_id
        self.guild = guild or FakeDiscordGuild()
        self.is_text_channel = True
        self.sent: list[str] = []
        self.fail_send = False
        self.typing_entries = 0
        self.typing_exits = 0
        self.fetched_messages: dict[str, FakeDiscordMessage] = {}

    async def send(self, message: str) -> None:
        if self.fail_send:
            raise RuntimeError("discord send failed")
        self.sent.append(message)

    def typing(self) -> FakeTyping:
        return FakeTyping(self)

    async def fetch_message(self, message_id: str) -> "FakeDiscordMessage":
        return self.fetched_messages[message_id]


class FakeReference:
    def __init__(self, resolved: "FakeDiscordMessage | None" = None, message_id: str | None = None):
        self.resolved = resolved
        self.message_id = message_id


class FakeDiscordMessage:
    def __init__(
        self,
        content: str,
        *,
        author: FakeDiscordUser | None = None,
        channel: FakeDiscordChannel | None = None,
        mentions: list[FakeDiscordUser] | None = None,
        reference: FakeReference | None = None,
        attachments: list[object] | None = None,
        message_id: str = "message-1",
    ):
        self.id = message_id
        self.content = content
        self.author = author or FakeDiscordUser("human")
        self.channel = channel or FakeDiscordChannel()
        self.guild = self.channel.guild
        self.mentions = mentions or []
        self.reference = reference
        self.attachments = attachments or []
        self.replies: list[str] = []
        self.reactions: list[str] = []

    async def reply(self, message: str) -> None:
        self.replies.append(message)

    async def add_reaction(self, reaction: str) -> None:
        self.reactions.append(reaction)


class FakeDiscordBot:
    def __init__(self, channels: dict[str, FakeDiscordChannel]):
        self.channels = channels
        self.closed = False

    def get_channel(self, channel_id):
        return self.channels.get(str(channel_id))

    def is_closed(self) -> bool:
        return self.closed


def test_chunk_text_returns_one_chunk_for_short_text():
    assert chunk_text("short", chunk_size=20, max_chunks=3) == ["short"]


def test_chunk_text_splits_long_text_under_chunk_size():
    chunks = chunk_text("abcdef", chunk_size=3, max_chunks=3)

    assert chunks == ["abc", "def"]
    assert all(len(chunk) <= 3 for chunk in chunks)


def test_chunk_text_caps_output_and_marks_truncated():
    chunks = chunk_text("abcdefghijklmnopqrstuvwxyz", chunk_size=20, max_chunks=1)

    assert len(chunks) == 1
    assert chunks[-1].endswith("[truncated]")
    assert all(len(chunk) <= 20 for chunk in chunks)


def test_code_block_formats_plain_text():
    assert code_block("hello") == "```\nhello\n```"


def test_send_chunks_wraps_code_blocks_within_chunk_limit():
    class Destination:
        def __init__(self):
            self.messages: list[str] = []

        async def send(self, message: str) -> None:
            self.messages.append(message)

    destination = Destination()
    config = DiscordConfig(chunk_size=20, max_chunks=2)

    asyncio.run(send_chunks(destination, "abcdefghijklmnopqrstuvwxyz", config))

    assert len(destination.messages) == 2
    assert all(message.startswith("```\n") and message.endswith("\n```") for message in destination.messages)
    assert all(len(message) <= 20 for message in destination.messages)
    assert "[truncated]" in destination.messages[-1]


def test_send_plain_chunks_does_not_wrap_code_blocks():
    class Destination:
        def __init__(self):
            self.messages: list[str] = []

        async def send(self, message: str) -> None:
            self.messages.append(message)

    destination = Destination()

    asyncio.run(send_plain_chunks(destination, "hello", DiscordConfig(chunk_size=20, max_chunks=2)))

    assert destination.messages == ["hello"]


def _outbound_row(reg: Registry, message_id: str) -> dict:
    with reg.connect() as conn:
        return dict(conn.execute("select * from outbound_human_messages where id=?", (message_id,)).fetchone())


def test_inbound_plain_bound_message_without_mention_or_reply_is_ignored(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.bind_discord_channel("456", root["id"], "123")
    delivered: list[str] = []

    def fake_prompt(registry, tmux_client, agent_id, prompt, source):
        delivered.append(prompt)
        return {"created_at": "2026-05-20T00:00:01Z"}

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux, prompt_func=fake_prompt)
        message = FakeDiscordMessage("hello", channel=FakeDiscordChannel())
        try:
            handled = await runtime.handle_message(message, FakeDiscordUser("bot"))
        finally:
            await runtime.close()
        return handled, message

    handled, message = asyncio.run(run())

    assert handled is False
    assert delivered == []
    assert message.replies == []
    assert message.reactions == []


def test_inbound_mention_is_delivered_with_clean_prompt_and_reaction(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.bind_discord_channel("456", root["id"], "123")
    delivered: list[tuple[str, str, str]] = []
    bot_user = FakeDiscordUser("999")

    def fake_prompt(registry, tmux_client, agent_id, prompt, source):
        delivered.append((agent_id, prompt, source))
        return {"created_at": "2026-05-20T00:00:01Z"}

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux, prompt_func=fake_prompt)
        message = FakeDiscordMessage(
            "<@999> hello there",
            channel=FakeDiscordChannel(),
            mentions=[bot_user],
            attachments=[object()],
        )
        try:
            handled = await runtime.handle_message(message, bot_user)
            assert root["id"] in runtime.active_typing
        finally:
            await runtime.close()
        return handled, message

    handled, message = asyncio.run(run())

    assert handled is True
    assert delivered == [(root["id"], f"{DISCORD_PROMPT_PREFIX}hello there", "discord")]
    assert message.reactions == [PROMPT_DELIVERED_REACTION]
    assert message.replies == []


def test_inbound_reply_to_bot_is_delivered_without_mention(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.bind_discord_channel("456", root["id"], "123")
    bot_user = FakeDiscordUser("999")
    prompts: list[str] = []

    def fake_prompt(registry, tmux_client, agent_id, prompt, source):
        prompts.append(prompt)
        return {"created_at": "2026-05-20T00:00:01Z"}

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux, prompt_func=fake_prompt)
        bot_message = FakeDiscordMessage("previous bot message", author=bot_user)
        message = FakeDiscordMessage("follow up", reference=FakeReference(resolved=bot_message))
        try:
            handled = await runtime.handle_message(message, bot_user)
        finally:
            await runtime.close()
        return handled

    assert asyncio.run(run()) is True
    assert prompts == [f"{DISCORD_PROMPT_PREFIX}follow up"]


def test_inbound_text_empty_message_with_attachment_is_rejected(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.bind_discord_channel("456", root["id"], "123")
    bot_user = FakeDiscordUser("999")
    delivered: list[str] = []

    def fake_prompt(registry, tmux_client, agent_id, prompt, source):
        delivered.append(prompt)
        return {"created_at": "2026-05-20T00:00:01Z"}

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux, prompt_func=fake_prompt)
        message = FakeDiscordMessage("<@999>   ", mentions=[bot_user], attachments=[object()])
        try:
            handled = await runtime.handle_message(message, bot_user)
        finally:
            await runtime.close()
        return handled, message

    handled, message = asyncio.run(run())

    assert handled is True
    assert delivered == []
    assert message.replies == [TEXT_ONLY_REPLY]


def test_inbound_unbound_channel_mention_receives_setup_hint(ctx):
    _cfg, reg, tmux = ctx
    bot_user = FakeDiscordUser("999")

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux)
        message = FakeDiscordMessage("<@999> hello", mentions=[bot_user])
        try:
            handled = await runtime.handle_message(message, bot_user)
        finally:
            await runtime.close()
        return handled, message

    handled, message = asyncio.run(run())

    assert handled is True
    assert message.replies == [NOT_BOUND_REPLY]
    assert message.replies[0] == "No orchestrator is bound to this channel. Use /puppet agents, then /puppet bind."


def test_inbound_prompt_delivery_failure_creates_visible_reply(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.bind_discord_channel("456", root["id"], "123")
    bot_user = FakeDiscordUser("999")

    def fake_prompt(registry, tmux_client, agent_id, prompt, source):
        raise PuppetError("invalid_state", "agent session is not live")

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux, prompt_func=fake_prompt)
        message = FakeDiscordMessage("<@999> hello", mentions=[bot_user])
        try:
            handled = await runtime.handle_message(message, bot_user)
        finally:
            await runtime.close()
        return handled, message

    handled, message = asyncio.run(run())

    assert handled is True
    assert message.reactions == []
    assert "I could not deliver" in message.replies[0]
    assert "error[invalid_state]" in message.replies[0]
    assert "/puppet status or /puppet read" in message.replies[0]


def test_two_channel_global_routing_keeps_roots_distinct(ctx, tmp_path):
    cfg, reg, tmux = ctx
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    root_a = create_agent_record(cfg, reg, cwd=str(project_a), description="root A", role="orchestrator")
    root_b = create_agent_record(cfg, reg, cwd=str(project_b), description="root B", role="orchestrator")
    child_a = create_agent_record(cfg, reg, cwd=str(project_a), description="child A", parent_id=root_a["id"])
    channel_a = FakeDiscordChannel("111", FakeDiscordGuild("123"))
    channel_b = FakeDiscordChannel("222", FakeDiscordGuild("123"))
    handle_bind_command(reg, channel_a, root_a["id"])
    handle_bind_command(reg, channel_b, root_b["id"])
    bot_user = FakeDiscordUser("999")
    delivered: list[tuple[str, str, str]] = []

    def fake_prompt(registry, tmux_client, agent_id, prompt, source):
        delivered.append((agent_id, prompt, source))
        return {"id": f"evt-{len(delivered)}", "created_at": f"2026-05-20T00:00:0{len(delivered)}Z"}

    async def run_inbound():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux, prompt_func=fake_prompt)
        try:
            handled_a = await runtime.handle_message(
                FakeDiscordMessage("<@999> message for A", channel=channel_a, mentions=[bot_user], message_id="msg-a"),
                bot_user,
            )
            handled_b = await runtime.handle_message(
                FakeDiscordMessage("<@999> message for B", channel=channel_b, mentions=[bot_user], message_id="msg-b"),
                bot_user,
            )
            assert handled_a is True
            assert handled_b is True
        finally:
            await runtime.close()

    asyncio.run(run_inbound())

    assert delivered == [
        (root_a["id"], f"{DISCORD_PROMPT_PREFIX}message for A", "discord"),
        (root_b["id"], f"{DISCORD_PROMPT_PREFIX}message for B", "discord"),
    ]

    queued_a = send_human_message(reg, child_a["id"], "reply from A child")
    queued_b = send_human_message(reg, root_b["id"], "reply from B root")
    assert queued_a["channel_id"] == "111"
    assert queued_b["channel_id"] == "222"

    async def run_outbound():
        runtime = DiscordRuntime(
            DiscordConfig(guild_id=123),
            reg,
            tmux,
            bot=FakeDiscordBot({"111": channel_a, "222": channel_b}),
        )
        await runtime.dispatch_pending_outbound_once()
        await runtime.close()

    asyncio.run(run_outbound())

    assert channel_a.sent == ["reply from A child"]
    assert channel_b.sent == ["reply from B root"]
    assert _outbound_row(reg, queued_a["id"])["root_agent_id"] == root_a["id"]
    assert _outbound_row(reg, queued_b["id"])["root_agent_id"] == root_b["id"]


def test_outbound_dispatch_sends_pending_and_marks_delivered(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    outbound = reg.enqueue_outbound_human_message(root["id"], root["id"], "discord", "456", "hello human")
    channel = FakeDiscordChannel()

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux, bot=FakeDiscordBot({"456": channel}))
        await runtime.dispatch_pending_outbound_once()
        await runtime.close()

    asyncio.run(run())

    assert channel.sent == ["hello human"]
    assert _outbound_row(reg, outbound["id"])["status"] == "delivered"


def test_pending_outbound_created_before_runtime_start_is_delivered(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    outbound = reg.enqueue_outbound_human_message(root["id"], root["id"], "discord", "456", "queued before startup")
    channel = FakeDiscordChannel()

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux, bot=FakeDiscordBot({"456": channel}))
        await runtime.poll_once()
        await runtime.close()

    asyncio.run(run())

    assert channel.sent == ["queued before startup"]
    assert _outbound_row(reg, outbound["id"])["status"] == "delivered"


def test_outbound_dispatch_caps_long_messages(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.enqueue_outbound_human_message(root["id"], root["id"], "discord", "456", "abcdefghijklmnopqrstuvwxyz")
    channel = FakeDiscordChannel()

    async def run():
        runtime = DiscordRuntime(
            DiscordConfig(guild_id=123, chunk_size=10, max_chunks=2),
            reg,
            tmux,
            bot=FakeDiscordBot({"456": channel}),
        )
        await runtime.dispatch_pending_outbound_once()
        await runtime.close()

    asyncio.run(run())

    assert len(channel.sent) == 2
    assert all(len(message) <= 10 for message in channel.sent)
    assert "truncated" in channel.sent[-1]


def test_outbound_dispatch_failure_marks_failed_and_continues(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    failed = reg.enqueue_outbound_human_message(root["id"], root["id"], "discord", "456", "first")
    delivered = reg.enqueue_outbound_human_message(root["id"], root["id"], "discord", "789", "second")
    failing_channel = FakeDiscordChannel("456")
    failing_channel.fail_send = True
    success_channel = FakeDiscordChannel("789")

    async def run():
        runtime = DiscordRuntime(
            DiscordConfig(guild_id=123),
            reg,
            tmux,
            bot=FakeDiscordBot({"456": failing_channel, "789": success_channel}),
        )
        await runtime.dispatch_pending_outbound_once()
        await runtime.close()

    asyncio.run(run())

    failed_row = _outbound_row(reg, failed["id"])
    assert failed_row["status"] == "failed"
    assert "discord send failed" in failed_row["error"]
    assert _outbound_row(reg, delivered["id"])["status"] == "delivered"
    assert success_channel.sent == ["second"]


def test_delivered_outbound_is_not_resent_after_dispatcher_restart(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    outbound = reg.enqueue_outbound_human_message(root["id"], root["id"], "discord", "456", "send once")
    channel = FakeDiscordChannel()

    async def run_once():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux, bot=FakeDiscordBot({"456": channel}))
        await runtime.dispatch_pending_outbound_once()
        await runtime.close()

    asyncio.run(run_once())
    asyncio.run(run_once())

    assert channel.sent == ["send once"]
    assert _outbound_row(reg, outbound["id"])["status"] == "delivered"


def test_failed_outbound_is_not_retried_after_dispatcher_restart(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    outbound = reg.enqueue_outbound_human_message(root["id"], root["id"], "discord", "456", "do not retry")
    channel = FakeDiscordChannel()
    channel.fail_send = True

    async def run_once():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux, bot=FakeDiscordBot({"456": channel}))
        await runtime.dispatch_pending_outbound_once()
        await runtime.close()

    asyncio.run(run_once())
    channel.fail_send = False
    asyncio.run(run_once())

    row = _outbound_row(reg, outbound["id"])
    assert row["status"] == "failed"
    assert "discord send failed" in row["error"]
    assert channel.sent == []


def test_existing_binding_persists_across_registry_instances(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.bind_discord_channel("456", root["id"], "123")

    reopened = Registry(cfg)

    assert reopened.discord_binding_for_channel("456")["root_agent_id"] == root["id"]
    assert reopened.discord_binding_for_root(root["id"])["channel_id"] == "456"


def test_outbound_delivery_stops_active_typing(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.enqueue_outbound_human_message(root["id"], root["id"], "discord", "456", "done")
    channel = FakeDiscordChannel()

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux, bot=FakeDiscordBot({"456": channel}))
        runtime.start_typing(root["id"], channel, "2026-05-20T00:00:01Z")
        assert root["id"] in runtime.active_typing
        await runtime.dispatch_pending_outbound_once()
        try:
            assert root["id"] not in runtime.active_typing
        finally:
            await runtime.close()

    asyncio.run(run())


def test_typing_second_prompt_refreshes_state(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    channel = FakeDiscordChannel()

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123, typing_timeout_seconds=60), reg, tmux)
        runtime.start_typing(root["id"], channel, "2026-05-20T00:00:01Z")
        first_timeout = runtime.active_typing[root["id"]].timeout_at_monotonic
        await asyncio.sleep(0)
        runtime.start_typing(root["id"], channel, "2026-05-20T00:00:02Z")
        try:
            state = runtime.active_typing[root["id"]]
            assert state.prompt_delivered_at == "2026-05-20T00:00:02Z"
            assert state.timeout_at_monotonic >= first_timeout
        finally:
            await runtime.close()

    asyncio.run(run())


def test_turn_stop_after_prompt_stops_typing(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    channel = FakeDiscordChannel()

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux)
        runtime.start_typing(root["id"], channel, "2000-01-01T00:00:00Z")
        reg.append_event(root["id"], "agent.turn_stopped", "Agent turn stopped.")
        runtime.stop_typing_for_turn_stops()
        try:
            assert root["id"] not in runtime.active_typing
        finally:
            await runtime.close()

    asyncio.run(run())


def test_turn_stop_before_prompt_does_not_stop_typing(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    channel = FakeDiscordChannel()

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, tmux)
        reg.append_event(root["id"], "agent.turn_stopped", "Agent turn stopped.")
        runtime.start_typing(root["id"], channel, "2999-01-01T00:00:00Z")
        runtime.stop_typing_for_turn_stops()
        try:
            assert root["id"] in runtime.active_typing
        finally:
            await runtime.close()

    asyncio.run(run())


def test_typing_timeout_stops_typing(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    channel = FakeDiscordChannel()

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123, typing_timeout_seconds=0.001), reg, tmux)
        runtime.start_typing(root["id"], channel, "2026-05-20T00:00:01Z")
        await asyncio.sleep(0.01)
        runtime.stop_expired_typing()
        try:
            assert root["id"] not in runtime.active_typing
        finally:
            await runtime.close()

    asyncio.run(run())


def test_discord_runtime_logs_delivery_without_message_body(ctx, tmp_path, caplog):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.enqueue_outbound_human_message(root["id"], root["id"], "discord", "456", "secret message body")
    channel = FakeDiscordChannel()
    caplog.set_level("INFO", logger="puppetmaster.discord_bot")

    async def run():
        runtime = DiscordRuntime(cfg, reg, tmux, bot=FakeDiscordBot({"456": channel}))
        await runtime.dispatch_pending_outbound_once()
        await runtime.close()

    asyncio.run(run())

    assert any(record.__dict__.get("event") == "discord.outbound.delivered" for record in caplog.records)
    assert "secret message body" not in caplog.text


def test_typing_timeout_is_logged(ctx, tmp_path, caplog):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    channel = FakeDiscordChannel()
    caplog.set_level("INFO", logger="puppetmaster.discord_bot")

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123, typing_timeout_seconds=0.001), reg, tmux)
        runtime.start_typing(root["id"], channel, "2026-05-20T00:00:01Z")
        await asyncio.sleep(0.01)
        runtime.stop_expired_typing()
        await runtime.close()

    asyncio.run(run())

    assert any(record.__dict__.get("event") == "discord.typing.timeout" for record in caplog.records)


def test_bind_rejects_child_agents(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])

    with pytest.raises(PuppetError) as exc:
        handle_bind_command(reg, FakeTextChannel(), child["id"])

    assert exc.value.code == "invalid_agent"
    assert reg.discord_binding_for_channel("channel-1") is None


def test_bind_rejects_missing_agents(ctx):
    _cfg, reg, _tmux = ctx

    with pytest.raises(PuppetError) as exc:
        handle_bind_command(reg, FakeTextChannel(), "agt_missing")

    assert exc.value.code == "not_found"


def test_bind_rejects_non_text_channels(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")

    with pytest.raises(PuppetError) as exc:
        handle_bind_command(reg, FakeOtherChannel(), root["id"])

    assert exc.value.code == "invalid_channel"


def test_bind_stores_channel_binding_for_root_orchestrator(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")

    result = handle_bind_command(reg, FakeTextChannel(), root["id"])

    assert result == f"Bound this channel to {root['id']}"
    binding = reg.discord_binding_for_channel("channel-1")
    assert binding["root_agent_id"] == root["id"]
    assert binding["guild_id"] == "guild-1"


def test_unbind_removes_binding(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    handle_bind_command(reg, FakeTextChannel(), root["id"])

    result = handle_unbind_command(reg, FakeTextChannel())

    assert result == f"Unbound this channel from {root['id']}"
    assert reg.discord_binding_for_channel("channel-1") is None
    assert handle_unbind_command(reg, FakeTextChannel()) == "This channel was not bound."


def test_status_requires_a_binding(ctx):
    _cfg, reg, _tmux = ctx

    with pytest.raises(PuppetError) as exc:
        handle_status_command(reg, FakeTmux(), FakeTextChannel())

    assert exc.value.code == "not_bound"
    assert "No orchestrator is bound" in exc.value.message


def test_status_shows_bound_root_state(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator", name="root")
    create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    reg.update_agent(root["id"], status="running", last_turn_stopped_at="2026-05-20T00:00:00Z")
    handle_bind_command(reg, FakeTextChannel(), root["id"])

    output = handle_status_command(reg, FakeTmux(live=True), FakeTextChannel())

    assert f"Root: {root['id']}" in output
    assert "Name: root" in output
    assert "Status: running" in output
    assert "Live tmux: yes" in output
    assert "Child count: 1" in output
    assert "Last turn stopped: 2026-05-20T00:00:00Z" in output


def test_read_reads_bound_root_only(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    Path(root["log_path"]).write_text("root line\n", encoding="utf-8")
    Path(child["log_path"]).write_text("child line\n", encoding="utf-8")
    handle_bind_command(reg, FakeTextChannel(), root["id"])

    output = handle_read_command(cfg, reg, FakeTmux(live=False), FakeTextChannel(), lines=10)

    assert "root line" in output
    assert "child line" not in output


def test_read_requires_a_binding(ctx):
    cfg, reg, tmux = ctx

    with pytest.raises(PuppetError) as exc:
        handle_read_command(cfg, reg, tmux, FakeTextChannel(), lines=10)

    assert exc.value.code == "not_bound"
    assert "Use /puppet agents" in exc.value.hint


def test_screenshot_requires_a_binding(ctx):
    _cfg, reg, _tmux = ctx

    with pytest.raises(PuppetError) as exc:
        handle_screenshot_command(reg, FakeTmux(live=True), FakeTextChannel())

    assert exc.value.code == "not_bound"


def test_screenshot_reports_missing_tmux_session(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    handle_bind_command(reg, FakeTextChannel(), root["id"])

    with pytest.raises(PuppetError) as exc:
        handle_screenshot_command(reg, FakeTmux(live=False), FakeTextChannel())

    assert exc.value.code == "tmux_missing_session"
    assert root["id"] in exc.value.message


def test_screenshot_captures_bound_root_visible_pane_as_png_without_events(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    handle_bind_command(reg, FakeTextChannel(), root["id"])
    fake_tmux = FakeTmux(live=True, pane_text="root line\nunicode: snowman")

    result = handle_screenshot_command(reg, fake_tmux, FakeTextChannel())

    assert result.root_agent_id == root["id"]
    assert result.filename == f"puppet-{root['id']}-screenshot.png"
    assert result.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert fake_tmux.checked == [root["tmux_session"]]
    assert fake_tmux.captured == [root["tmux_session"]]
    assert child["tmux_session"] not in fake_tmux.checked
    assert reg.list_events(root["id"]) == []
    assert reg.pending_deliveries(root["id"]) == []


def test_compact_requires_a_binding(ctx):
    _cfg, reg, _tmux = ctx

    with pytest.raises(PuppetError) as exc:
        handle_compact_command(reg, FakeTmux(live=True), FakeTextChannel())

    assert exc.value.code == "not_bound"


def test_compact_reports_missing_tmux_session(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    handle_bind_command(reg, FakeTextChannel(), root["id"])

    with pytest.raises(PuppetError) as exc:
        handle_compact_command(reg, FakeTmux(live=False), FakeTextChannel())

    assert exc.value.code == "tmux_missing_session"
    assert root["id"] in exc.value.message


def test_compact_sends_literal_command_to_bound_root_without_prompt_events(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    handle_bind_command(reg, FakeTextChannel(), root["id"])
    fake_tmux = FakeTmux(live=True)

    result = handle_compact_command(reg, fake_tmux, FakeTextChannel())

    assert result.root_agent_id == root["id"]
    assert result.channel_id == "channel-1"
    assert result.turn_stop_count == 0
    assert fake_tmux.checked == [root["tmux_session"]]
    assert fake_tmux.sent_prompts == [(root["tmux_session"], "/compact")]
    assert child["tmux_session"] not in [session for session, _prompt in fake_tmux.sent_prompts]
    assert reg.list_events(root["id"]) == []
    assert reg.pending_deliveries(root["id"]) == []


def test_compact_done_update_posts_after_root_stop_hook(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    handle_bind_command(reg, FakeTextChannel(), root["id"])
    channel = FakeDiscordChannel("channel-1", FakeDiscordGuild("guild-1"))
    fake_tmux = FakeTmux(live=True)

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, fake_tmux)
        try:
            acknowledgement = runtime.request_compact(channel)
            await runtime.dispatch_completed_compactions_once()
            assert channel.sent == []
            handle_stop_hook(reg, root["id"], "{}")
            await runtime.dispatch_completed_compactions_once()
            return acknowledgement
        finally:
            await runtime.close()

    acknowledgement = asyncio.run(run())

    assert acknowledgement == f"Sent /compact to {root['id']}. I will post an update when that turn stops."
    assert fake_tmux.sent_prompts == [(root["tmux_session"], "/compact")]
    assert channel.sent == [f"Compact completed for {root['id']}."]
    assert reg.count_events(root["id"], "agent.turn_stopped") == 1


def test_compact_ignores_stop_hooks_that_precede_request(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    handle_bind_command(reg, FakeTextChannel(), root["id"])
    channel = FakeDiscordChannel("channel-1", FakeDiscordGuild("guild-1"))
    fake_tmux = FakeTmux(live=True)
    handle_stop_hook(reg, root["id"], "{}")

    async def run():
        runtime = DiscordRuntime(DiscordConfig(guild_id=123), reg, fake_tmux)
        try:
            runtime.request_compact(channel)
            await runtime.dispatch_completed_compactions_once()
        finally:
            await runtime.close()

    asyncio.run(run())

    assert channel.sent == []


def test_agents_lists_roots_only(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator", name="root")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"], name="child")

    output = handle_agents_command(reg)

    assert root["id"] in output
    assert child["id"] not in output
    assert "root" in output
    assert "child" not in output


def test_tree_requires_binding_and_renders_descendants(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator", name="root")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"], name="child")

    with pytest.raises(PuppetError) as exc:
        handle_tree_command(reg, FakeTextChannel())
    assert exc.value.code == "not_bound"
    assert "Use /puppet agents" in exc.value.hint

    handle_bind_command(reg, FakeTextChannel(), root["id"])
    output = handle_tree_command(reg, FakeTextChannel())

    assert f"{root['id']} orchestrator" in output
    assert f"  {child['id']} subagent" in output


def test_validate_discord_config_requires_token_and_guild_id(ctx):
    cfg, _reg, _tmux = ctx

    with pytest.raises(PuppetError) as missing_token:
        validate_discord_config(cfg)
    assert missing_token.value.code == "discord_token_required"
    assert "discord.token is required" in missing_token.value.message
    assert "~/.puppetmaster/config.toml" in missing_token.value.hint

    with pytest.raises(PuppetError) as missing_guild:
        validate_discord_config(DiscordConfig(token="secret", guild_id=None))
    assert missing_guild.value.code == "discord_guild_required"
    assert "discord.guild_id is required" in missing_guild.value.message
    assert "~/.puppetmaster/config.toml" in missing_guild.value.hint


def test_build_discord_bot_reports_missing_guild_access(ctx, monkeypatch):
    cfg, reg, tmux = ctx
    cfg = replace(cfg, discord=DiscordConfig(token="secret", guild_id=123))
    monkeypatch.setattr(discord_bot_module, "discord", FakeDiscordModule)
    monkeypatch.setattr(discord_bot_module, "app_commands", FakeAppCommandsModule)
    monkeypatch.setattr(discord_bot_module, "commands", FakeCommandsModule)

    bot = build_discord_bot(cfg, reg, tmux)
    setup_hook = bot.events["setup_hook"]

    with pytest.raises(PuppetError) as exc:
        asyncio.run(setup_hook())

    group = bot.tree.added[0][0]
    assert "screenshot" in {name for name, _description, _func in group.commands}
    assert "compact" in {name for name, _description, _func in group.commands}
    assert exc.value.code == "discord_guild_access_denied"
    assert "guild 123" in exc.value.message
    assert "Missing Access" in exc.value.message
    assert "discord.guild_id" in exc.value.hint
    assert "applications.commands" in exc.value.hint


def test_readme_documents_discord_operations_and_safety():
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert "uv tool install" in readme
    assert "pipx install" in readme
    assert "puppet init" in readme
    assert "~/.puppetmaster/" in readme
    assert "~/.puppetmaster/config.toml" in readme
    assert "token" in readme
    assert "puppet discord serve --background" in readme
    assert "puppet discord stop" in readme
    assert "PUPPETMASTER_STATE_DIR" in readme
    assert "project-local `.puppetmaster/` directories are not migrated automatically" in readme
    assert "Automatic migration is not provided" in readme
    assert "bind channel A to the project A root and channel B to the project B root" in readme
    assert "--agent-id project-a" in readme
    assert "/puppet screenshot" in readme
    assert "tmux pane" in readme
    assert "mention" in readme.lower()
    assert "reply" in readme.lower()
    assert "send_human_message" in readme
    assert "bypassed approvals" in readme.lower()
    assert "sandbox" in readme.lower()
