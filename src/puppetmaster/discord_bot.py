from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import re
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Config, DiscordConfig, load_config
from .errors import PuppetError
from .logging import log as supervisor_log
from .model import now
from .native_screenshot import capture_native_screenshot
from .registry import Registry
from .services import DISCORD_DEFAULT_ATTACHMENT_LIMIT_BYTES, prompt_agent, prompt_text, read_agent
from .skills import normalize_skill_name
from .terminal_image import render_terminal_png
from .tmux import Tmux

try:  # pragma: no cover - exercised through CLI-friendly import handling.
    import discord
    from discord import app_commands
    from discord.ext import commands
except ImportError:  # pragma: no cover
    discord = None  # type: ignore[assignment]
    app_commands = None  # type: ignore[assignment]
    commands = None  # type: ignore[assignment]


TRUNCATED_MARKER = "[truncated]"
CODE_BLOCK_OVERHEAD = len("```\n\n```")
DISCORD_PROMPT_PREFIX = "DISCORD MESSAGE RECEIVED:\n"
FILES_ATTACHED_HEADING = "FILES ATTACHED"
TEXT_ONLY_REPLY = "Send text or attach a file for the bound orchestrator."
NOT_BOUND_REPLY = "No orchestrator is bound to this channel. Use /puppet agents, then /puppet bind."
PROMPT_DELIVERY_FAILED_REPLY = "I could not deliver that message to the bound root."
PROMPT_DELIVERY_FAILED_HINT = "Use /puppet status or /puppet read."
STALE_BINDING_REPLY = "The orchestrator bound to this channel is no longer live. Use /puppet agents, then /puppet bind."
PROMPT_DELIVERED_REACTION = "\N{WHITE HEAVY CHECK MARK}"
MAX_SKILL_AUTOCOMPLETE_CHOICES = 25
POST_RESET_PROMPT_DELAY_SECONDS = 3.0
POST_CLEAR_TASK_PROMPT = (
    "Your context has just been cleared. Use the send message tool to inform the user that you are now ready to receive tasks."
)
POST_COMPACT_TASK_PROMPT = (
    "Your context has just been compacted. Use the send message tool to inform the user that you are now ready to receive tasks."
)
LOGGER = logging.getLogger(__name__)


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return str(signum)


def _raise_for_shutdown_signal(config: Config, signum: int, _frame: object) -> None:
    signal_name = _signal_name(signum)
    LOGGER.warning(
        "Discord bot received shutdown signal %s.",
        signal_name,
        extra={"event": "discord.bot.signal", "signal": signum, "signal_name": signal_name},
    )
    supervisor_log(
        config,
        "warning",
        "discord.bot.signal",
        "Discord bot received shutdown signal.",
        signal=signum,
        signal_name=signal_name,
    )
    raise SystemExit(128 + signum)


def _discord_config(config: Config | DiscordConfig) -> DiscordConfig:
    return config.discord if isinstance(config, Config) else config


def validate_discord_config(config: Config | DiscordConfig) -> DiscordConfig:
    discord_config = _discord_config(config)
    if not discord_config.token:
        raise PuppetError(
            "discord_token_required",
            "discord.token is required.",
            "Set discord.token in ~/.puppetmaster/config.toml or set PUPPETMASTER_STATE_DIR for isolated state.",
        )
    if discord_config.guild_id is None:
        raise PuppetError(
            "discord_guild_required",
            "discord.guild_id is required.",
            "Set discord.guild_id in ~/.puppetmaster/config.toml or set PUPPETMASTER_STATE_DIR for isolated state.",
        )
    return discord_config


def chunk_text(text: str, chunk_size: int, max_chunks: int) -> list[str]:
    if chunk_size <= 0:
        raise PuppetError("invalid_config", "discord.chunk_size must be positive")
    if max_chunks <= 0:
        raise PuppetError("invalid_config", "discord.max_chunks must be positive")

    remaining = str(text)
    if remaining == "":
        return [""]

    chunks: list[str] = []
    while remaining and len(chunks) < max_chunks:
        if len(remaining) <= chunk_size:
            chunks.append(remaining)
            remaining = ""
            break
        split_at = remaining.rfind("\n", 0, chunk_size + 1)
        if split_at <= 0:
            split_at = chunk_size
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
        if remaining.startswith("\n"):
            remaining = remaining[1:]

    if remaining and chunks:
        suffix = "\n" + TRUNCATED_MARKER
        if len(suffix) >= chunk_size:
            chunks[-1] = suffix[-chunk_size:]
        else:
            chunks[-1] = chunks[-1][: chunk_size - len(suffix)] + suffix

    return chunks


def code_block(text: str) -> str:
    sanitized = str(text).replace("```", "'''")
    return f"```\n{sanitized}\n```"


def _code_block_chunks(text: str, config: Config | DiscordConfig) -> list[str]:
    discord_config = _discord_config(config)
    content_size = max(1, discord_config.chunk_size - CODE_BLOCK_OVERHEAD)
    return [code_block(chunk) for chunk in chunk_text(text, content_size, discord_config.max_chunks)]


async def send_chunks(destination: Any, text: str, config: Config | DiscordConfig) -> None:
    for chunk in _code_block_chunks(text, config):
        await destination.send(chunk)


async def send_plain_chunks(destination: Any, text: str, config: Config | DiscordConfig) -> None:
    discord_config = _discord_config(config)
    for chunk in chunk_text(text, discord_config.chunk_size, discord_config.max_chunks):
        await destination.send(chunk)


def _discord_file_for_outbound(outbound: dict[str, Any]) -> Any | None:
    attachment_path = outbound.get("attachment_path")
    if not attachment_path:
        return None
    if discord is None:
        raise PuppetError("discord_dependency_missing", "discord.py is not installed.")
    path = Path(str(attachment_path))
    if not path.is_file():
        raise PuppetError("file_not_found", f"attachment file not found: {attachment_path}")
    size = path.stat().st_size
    if size > DISCORD_DEFAULT_ATTACHMENT_LIMIT_BYTES:
        limit_mib = DISCORD_DEFAULT_ATTACHMENT_LIMIT_BYTES // (1024 * 1024)
        raise PuppetError("file_too_large", f"attachment is {size} bytes, above Discord's default {limit_mib} MiB upload limit.")
    return discord.File(str(path), filename=outbound.get("attachment_filename") or path.name)


async def send_plain_chunks_with_attachment(destination: Any, outbound: dict[str, Any], config: Config | DiscordConfig) -> None:
    file = _discord_file_for_outbound(outbound)
    if file is None:
        await send_plain_chunks(destination, outbound["message"], config)
        return
    discord_config = _discord_config(config)
    chunks = chunk_text(outbound["message"], discord_config.chunk_size, discord_config.max_chunks)
    try:
        await destination.send(content=chunks[0] or None, file=file)
    finally:
        close = getattr(file, "close", None)
        if close is not None:
            close()
    for chunk in chunks[1:]:
        await destination.send(chunk)


async def send_interaction_chunks(interaction: Any, text: str, config: Config | DiscordConfig) -> None:
    chunks = _code_block_chunks(text, config)
    if not chunks:
        chunks = [code_block("")]
    response = interaction.response
    if response.is_done():
        for chunk in chunks:
            await interaction.followup.send(chunk)
        return
    await response.send_message(chunks[0])
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk)


async def send_plain_interaction_chunks(interaction: Any, text: str, config: Config | DiscordConfig) -> None:
    discord_config = _discord_config(config)
    chunks = chunk_text(text, discord_config.chunk_size, discord_config.max_chunks)
    if not chunks:
        chunks = [""]
    response = interaction.response
    if response.is_done():
        for chunk in chunks:
            await interaction.followup.send(chunk)
        return
    await response.send_message(chunks[0])
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk)


def format_error(exc: PuppetError) -> str:
    lines = [f"error[{exc.code}]: {exc.message}"]
    if exc.hint:
        lines.append(f"hint: {exc.hint}")
    return "\n".join(lines)


def _short_exception(exc: Exception) -> str:
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    return text[:500]


@dataclass
class TypingState:
    root_agent_id: str
    channel_id: str
    channel: Any
    prompt_delivered_at: str
    timeout_at_monotonic: float
    task: asyncio.Task[None] | None = None


@dataclass
class ScreenshotResult:
    root_agent_id: str
    filename: str
    png: bytes
    source: str = "terminal"
    note: str | None = None


@dataclass
class SlashCommandResult:
    root_agent_id: str
    channel_id: str
    requested_at: str
    command: str


def _user_id(user: Any) -> str | None:
    user_id = getattr(user, "id", None)
    return str(user_id) if user_id is not None else None


def _is_bot_author(message: Any, bot_user: Any) -> bool:
    bot_user_id = _user_id(bot_user)
    author = getattr(message, "author", None)
    author_id = _user_id(author)
    return author_id is not None and bot_user_id is not None and author_id == bot_user_id


def _message_mentions_bot(message: Any, bot_user: Any) -> bool:
    bot_user_id = _user_id(bot_user)
    if bot_user_id is None:
        return False
    for mentioned in getattr(message, "mentions", []) or []:
        if _user_id(mentioned) == bot_user_id:
            return True
    content = str(getattr(message, "content", "") or "")
    return bool(re.search(rf"<@!?{re.escape(bot_user_id)}>", content))


def _strip_bot_mentions(content: str, bot_user: Any) -> str:
    bot_user_id = _user_id(bot_user)
    cleaned = str(content or "")
    if bot_user_id is not None:
        cleaned = re.sub(rf"<@!?{re.escape(bot_user_id)}>", "", cleaned)
    mention = getattr(bot_user, "mention", None)
    if mention:
        cleaned = cleaned.replace(str(mention), "")
    return cleaned.strip()


def _safe_path_component(value: str, fallback: str, max_length: int = 120) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_length] or fallback


def _safe_inbound_attachment_filename(attachment: Any, index: int) -> str:
    raw_name = str(getattr(attachment, "filename", "") or f"attachment-{index}")
    name = Path(raw_name.replace("\\", "/")).name
    return _safe_path_component(name, f"attachment-{index}", max_length=180)


def _format_inbound_prompt(cleaned: str, file_paths: list[str]) -> str:
    if not file_paths:
        return f"{DISCORD_PROMPT_PREFIX}{cleaned}"
    lines = [DISCORD_PROMPT_PREFIX.rstrip()]
    if cleaned:
        lines.extend([cleaned, ""])
    lines.append(FILES_ATTACHED_HEADING)
    lines.extend(file_paths)
    return "\n".join(lines)


async def _is_reply_to_bot(message: Any, bot_user: Any) -> bool:
    bot_user_id = _user_id(bot_user)
    if bot_user_id is None:
        return False

    reference = getattr(message, "reference", None)
    if reference is None:
        return False

    resolved = getattr(reference, "resolved", None) or getattr(message, "referenced_message", None)
    if resolved is not None and _user_id(getattr(resolved, "author", None)) == bot_user_id:
        return True

    message_id = getattr(reference, "message_id", None)
    channel = getattr(message, "channel", None)
    fetch_message = getattr(channel, "fetch_message", None)
    if message_id is None or fetch_message is None:
        return False
    try:
        referenced = await fetch_message(message_id)
    except Exception:
        return False
    return _user_id(getattr(referenced, "author", None)) == bot_user_id


class DiscordRuntime:
    def __init__(
        self,
        config: Config | DiscordConfig,
        registry: Registry,
        tmux: Tmux,
        *,
        bot: Any | None = None,
        prompt_func: Callable[[Registry, Tmux, str, str, str], dict[str, Any]] = prompt_agent,
    ) -> None:
        self.config = config
        self.discord_config = _discord_config(config)
        self.registry = registry
        self.tmux = tmux
        self.bot = bot
        self.prompt_func = prompt_func
        self.active_typing: dict[str, TypingState] = {}
        self._poll_task: asyncio.Task[None] | None = None
        self._post_reset_tasks: set[asyncio.Task[None]] = set()

    def _log(self, level: str, event: str, message: str, **fields: Any) -> None:
        log_method = getattr(LOGGER, level, LOGGER.info)
        log_method("%s", message, extra={"event": event, **fields})
        if isinstance(self.config, Config):
            supervisor_log(self.config, level, event, message, **fields)

    async def _save_inbound_attachments(self, root_agent_id: str, message: Any) -> list[str]:
        attachments = list(getattr(message, "attachments", []) or [])
        if not attachments:
            return []
        message_id = _safe_path_component(str(getattr(message, "id", "") or ""), "message")
        target_dir = self.registry.config.state_dir / "human_files" / _safe_path_component(root_agent_id, "root") / message_id
        target_dir.mkdir(parents=True, exist_ok=True)
        saved_paths: list[str] = []
        for index, attachment in enumerate(attachments, start=1):
            size = getattr(attachment, "size", None)
            if size is not None and int(size) > DISCORD_DEFAULT_ATTACHMENT_LIMIT_BYTES:
                limit_mib = DISCORD_DEFAULT_ATTACHMENT_LIMIT_BYTES // (1024 * 1024)
                raise PuppetError(
                    "file_too_large",
                    f"inbound attachment is {int(size)} bytes, above Discord's default {limit_mib} MiB attachment limit.",
                )
            filename = _safe_inbound_attachment_filename(attachment, index)
            path = target_dir / f"{index:02d}-{filename}"
            save = getattr(attachment, "save", None)
            if save is None:
                raise PuppetError("attachment_download_unavailable", "Discord attachment cannot be downloaded.")
            await save(path)
            if not path.is_file():
                raise PuppetError("attachment_download_failed", f"Discord attachment was not saved: {path}")
            actual_size = path.stat().st_size
            if actual_size > DISCORD_DEFAULT_ATTACHMENT_LIMIT_BYTES:
                with contextlib.suppress(OSError):
                    path.unlink()
                limit_mib = DISCORD_DEFAULT_ATTACHMENT_LIMIT_BYTES // (1024 * 1024)
                raise PuppetError(
                    "file_too_large",
                    f"inbound attachment is {actual_size} bytes, above Discord's default {limit_mib} MiB attachment limit.",
                )
            saved_paths.append(str(path))
        return saved_paths

    async def handle_message(self, message: Any, bot_user: Any) -> bool:
        if _is_bot_author(message, bot_user):
            return False
        if getattr(message, "interaction", None) is not None:
            return False

        guild = getattr(message, "guild", None)
        if guild is None or str(getattr(guild, "id", "")) != str(self.discord_config.guild_id):
            return False

        channel = getattr(message, "channel", None)
        try:
            channel_id, _guild_id = _text_channel_ids(channel)
        except PuppetError:
            return False

        mentioned = _message_mentions_bot(message, bot_user)
        reply_to_bot = await _is_reply_to_bot(message, bot_user)
        if not mentioned and not reply_to_bot:
            return False

        binding = self.registry.discord_binding_for_channel(channel_id)
        discord_message_id = str(getattr(message, "id", "")) or None
        if not self.registry.claim_discord_inbound_message(
            channel_id,
            discord_message_id,
            binding["root_agent_id"] if binding else None,
        ):
            self._log(
                "info",
                "discord.inbound.duplicate",
                "Discord prompt ignored because the Discord message was already claimed.",
                channel_id=channel_id,
                guild_id=str(getattr(guild, "id", "")),
                discord_message_id=discord_message_id,
            )
            return True
        if not binding:
            self._log(
                "info",
                "discord.inbound.unbound",
                "Discord prompt ignored because the channel is not bound.",
                channel_id=channel_id,
                guild_id=str(getattr(guild, "id", "")),
                discord_message_id=discord_message_id,
            )
            await message.reply(NOT_BOUND_REPLY)
            return True

        if self.prompt_func is prompt_agent:
            root = self.registry.maybe_agent(binding["root_agent_id"])
            if root is None or not self.tmux.session_exists(root["tmux_session"]):
                self.registry.unbind_discord_channel(channel_id)
                self._log(
                    "warning",
                    "discord.inbound.stale_binding",
                    "Discord channel binding pointed at a non-live root orchestrator.",
                    root_agent_id=binding["root_agent_id"],
                    channel_id=channel_id,
                    guild_id=str(getattr(guild, "id", "")),
                    discord_message_id=discord_message_id,
                )
                await message.reply(STALE_BINDING_REPLY)
                return True

        cleaned = _strip_bot_mentions(str(getattr(message, "content", "") or ""), bot_user)
        attachments = list(getattr(message, "attachments", []) or [])
        if not cleaned and not attachments:
            await message.reply(TEXT_ONLY_REPLY)
            return True

        try:
            file_paths = await self._save_inbound_attachments(binding["root_agent_id"], message)
        except PuppetError as exc:
            self._log(
                "warning",
                "discord.inbound.attachment_failed",
                "Discord attachment download failed.",
                root_agent_id=binding["root_agent_id"],
                channel_id=channel_id,
                discord_message_id=discord_message_id,
                error_code=exc.code,
                error_message=exc.message,
            )
            await message.reply(f"I could not download that attachment. {format_error(exc)}")
            return True

        prompt = _format_inbound_prompt(cleaned, file_paths)
        try:
            event = self.prompt_func(self.registry, self.tmux, binding["root_agent_id"], prompt, "discord")
        except PuppetError as exc:
            self._log(
                "warning",
                "discord.inbound.failed",
                "Discord prompt delivery failed.",
                root_agent_id=binding["root_agent_id"],
                channel_id=channel_id,
                discord_message_id=discord_message_id,
                error_code=exc.code,
                error_message=exc.message,
            )
            await message.reply(f"{PROMPT_DELIVERY_FAILED_REPLY} {format_error(exc)}\n{PROMPT_DELIVERY_FAILED_HINT}")
            return True
        except Exception as exc:
            LOGGER.exception(
                "Failed to deliver Discord prompt to root %s from Discord message %s",
                binding["root_agent_id"],
                getattr(message, "id", None),
            )
            self._log(
                "error",
                "discord.inbound.failed",
                "Discord prompt delivery failed.",
                root_agent_id=binding["root_agent_id"],
                channel_id=channel_id,
                discord_message_id=discord_message_id,
                error_message=_short_exception(exc),
            )
            await message.reply(f"{PROMPT_DELIVERY_FAILED_REPLY} {_short_exception(exc)}\n{PROMPT_DELIVERY_FAILED_HINT}")
            return True

        await message.add_reaction(PROMPT_DELIVERED_REACTION)
        self._log(
            "info",
            "discord.inbound.delivered",
            "Discord prompt delivered to root orchestrator.",
            root_agent_id=binding["root_agent_id"],
            channel_id=channel_id,
            guild_id=str(getattr(guild, "id", "")),
            discord_message_id=discord_message_id,
            prompt_length=len(cleaned),
            attachment_count=len(file_paths),
            event_id=event.get("id"),
        )
        self.start_typing(binding["root_agent_id"], channel, event.get("created_at") or now())
        return True

    def start_typing(self, root_agent_id: str, channel: Any, prompt_delivered_at: str) -> None:
        timeout_at = time.monotonic() + self.discord_config.typing_timeout_seconds
        existing = self.active_typing.get(root_agent_id)
        if existing is not None:
            existing.channel_id = str(getattr(channel, "id"))
            existing.channel = channel
            existing.prompt_delivered_at = prompt_delivered_at
            existing.timeout_at_monotonic = timeout_at
            if existing.task is None or existing.task.done():
                existing.task = asyncio.create_task(self._typing_loop(root_agent_id))
            return

        state = TypingState(
            root_agent_id=root_agent_id,
            channel_id=str(getattr(channel, "id")),
            channel=channel,
            prompt_delivered_at=prompt_delivered_at,
            timeout_at_monotonic=timeout_at,
        )
        self.active_typing[root_agent_id] = state
        state.task = asyncio.create_task(self._typing_loop(root_agent_id))

    def stop_typing(self, root_agent_id: str) -> None:
        state = self.active_typing.pop(root_agent_id, None)
        if state and state.task and not state.task.done():
            state.task.cancel()

    def stop_expired_typing(self) -> None:
        current = time.monotonic()
        for root_agent_id, state in list(self.active_typing.items()):
            if current >= state.timeout_at_monotonic:
                self._log(
                    "info",
                    "discord.typing.timeout",
                    "Discord typing indicator timed out.",
                    root_agent_id=root_agent_id,
                    channel_id=state.channel_id,
                )
                self.stop_typing(root_agent_id)

    async def _typing_loop(self, root_agent_id: str) -> None:
        try:
            while True:
                state = self.active_typing.get(root_agent_id)
                if state is None:
                    return
                remaining = state.timeout_at_monotonic - time.monotonic()
                if remaining <= 0:
                    self._log(
                        "info",
                        "discord.typing.timeout",
                        "Discord typing indicator timed out.",
                        root_agent_id=root_agent_id,
                        channel_id=state.channel_id,
                    )
                    self.active_typing.pop(root_agent_id, None)
                    return
                sleep_for = min(8.0, max(0.05, remaining))
                typing = state.channel.typing()
                async with typing:
                    await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Discord typing loop failed for root %s", root_agent_id)
            self.active_typing.pop(root_agent_id, None)

    async def _get_channel(self, channel_id: str) -> Any | None:
        if self.bot is None:
            return None
        channel: Any | None = None
        get_channel = getattr(self.bot, "get_channel", None)
        if get_channel is not None:
            try:
                channel = get_channel(int(channel_id))
            except (TypeError, ValueError):
                channel = get_channel(channel_id)
        if channel is not None:
            return channel
        fetch_channel = getattr(self.bot, "fetch_channel", None)
        if fetch_channel is None:
            return None
        try:
            return await fetch_channel(int(channel_id))
        except (TypeError, ValueError):
            return await fetch_channel(channel_id)

    async def dispatch_pending_outbound_once(self) -> None:
        for outbound in self.registry.claim_pending_outbound_human_messages("discord", limit=20):
            try:
                channel = await self._get_channel(outbound["channel_id"])
                if channel is None:
                    raise PuppetError("channel_not_found", f"Discord channel not found: {outbound['channel_id']}")
                await send_plain_chunks_with_attachment(channel, outbound, self.config)
                self.registry.mark_outbound_human_message_delivered(outbound["id"])
                self._log(
                    "info",
                    "discord.outbound.delivered",
                    "Outbound Discord message delivered.",
                    message_id=outbound["id"],
                    root_agent_id=outbound["root_agent_id"],
                    agent_id=outbound["agent_id"],
                    channel_id=outbound["channel_id"],
                    message_length=len(outbound["message"]),
                    attachment_size=outbound.get("attachment_size"),
                )
                self.stop_typing(outbound["root_agent_id"])
            except Exception as exc:
                error = _short_exception(exc)
                LOGGER.exception("Failed to deliver outbound Discord message %s", outbound["id"])
                self.registry.mark_outbound_human_message_failed(outbound["id"], error)
                self._log(
                    "warning",
                    "discord.outbound.failed",
                    "Outbound Discord message delivery failed.",
                    message_id=outbound["id"],
                    root_agent_id=outbound["root_agent_id"],
                    agent_id=outbound["agent_id"],
                    channel_id=outbound["channel_id"],
                    message_length=len(outbound["message"]),
                    attachment_size=outbound.get("attachment_size"),
                    error_message=error,
                )

    def stop_typing_for_turn_stops(self) -> None:
        for root_agent_id, state in list(self.active_typing.items()):
            stopped_at = self.registry.latest_event_time(root_agent_id, "agent.turn_stopped")
            if stopped_at is not None and stopped_at > state.prompt_delivered_at:
                self.stop_typing(root_agent_id)

    def request_compact(self, channel: Any) -> str:
        result = handle_compact_command(self.registry, self.tmux, channel)
        self._schedule_post_reset_prompt(
            result.root_agent_id,
            result.channel_id,
            "compact",
            POST_COMPACT_TASK_PROMPT,
        )
        self._log(
            "info",
            "discord.compact.requested",
            "Discord compact command delivered to root orchestrator.",
            root_agent_id=result.root_agent_id,
            channel_id=result.channel_id,
        )
        return f"Sent /compact to {result.root_agent_id}."

    def request_clear(self, channel: Any) -> str:
        result = handle_clear_command(self.registry, self.tmux, channel)
        self._schedule_post_reset_prompt(
            result.root_agent_id,
            result.channel_id,
            "clear",
            POST_CLEAR_TASK_PROMPT,
        )
        self._log(
            "info",
            "discord.clear.requested",
            "Discord clear command delivered to root orchestrator.",
            root_agent_id=result.root_agent_id,
            channel_id=result.channel_id,
        )
        return f"Sent /clear to {result.root_agent_id}."

    def _schedule_post_reset_prompt(
        self,
        root_agent_id: str,
        channel_id: str,
        reset_command: str,
        user_prompt: str,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._log(
                "warning",
                f"discord.{reset_command}.resume_prompt_not_scheduled",
                f"Post-{reset_command} orchestration prompt could not be scheduled without a running event loop.",
                root_agent_id=root_agent_id,
                channel_id=channel_id,
            )
            return

        task = loop.create_task(
            self._send_post_reset_prompt_later(root_agent_id, channel_id, reset_command, user_prompt)
        )
        self._post_reset_tasks.add(task)
        task.add_done_callback(self._post_reset_tasks.discard)

    async def _send_post_reset_prompt_later(
        self,
        root_agent_id: str,
        channel_id: str,
        reset_command: str,
        user_prompt: str,
    ) -> None:
        await asyncio.sleep(POST_RESET_PROMPT_DELAY_SECONDS)
        self._send_post_reset_prompt(root_agent_id, channel_id, reset_command, user_prompt)

    def _send_post_reset_prompt(
        self,
        root_agent_id: str,
        channel_id: str,
        reset_command: str,
        user_prompt: str,
    ) -> None:
        root = self.registry.get_agent(root_agent_id)
        try:
            self.tmux.send_prompt(root["tmux_session"], prompt_text(root, user_prompt))
        except Exception as exc:
            self._log(
                "warning",
                f"discord.{reset_command}.resume_prompt_failed",
                f"Post-{reset_command} orchestration prompt failed.",
                root_agent_id=root_agent_id,
                channel_id=channel_id,
                error_message=_short_exception(exc),
            )
            return
        self._log(
            "info",
            f"discord.{reset_command}.resume_prompt_sent",
            f"Post-{reset_command} orchestration prompt sent to root orchestrator.",
            root_agent_id=root_agent_id,
            channel_id=channel_id,
            tmux_session=root["tmux_session"],
        )

    async def poll_once(self) -> None:
        await self.dispatch_pending_outbound_once()
        self.stop_typing_for_turn_stops()
        self.stop_expired_typing()

    def start(self) -> None:
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        while self.bot is None or not self.bot.is_closed():
            try:
                await self.poll_once()
            except Exception:
                LOGGER.exception("Discord runtime poll loop failed")
            await asyncio.sleep(self.discord_config.poll_interval_seconds)

    async def close(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
        for task in list(self._post_reset_tasks):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._post_reset_tasks.clear()
        typing_tasks = [state.task for state in self.active_typing.values() if state.task is not None]
        for root_agent_id in list(self.active_typing):
            self.stop_typing(root_agent_id)
        for task in typing_tasks:
            if not task.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await task


def root_agents(registry: Registry) -> list[dict[str, Any]]:
    return [
        agent
        for agent in registry.list_agents()
        if agent["role"] == "orchestrator" and agent["id"] == agent["root_id"]
    ]


def handle_agents_command(registry: Registry) -> str:
    roots = root_agents(registry)
    if not roots:
        return "No root orchestrators found."
    rows = ["Root orchestrators:"]
    for agent in roots:
        name = agent.get("name") or "-"
        rows.extend(
            [
                "",
                f"**{name}** `{agent['status']}`",
                "```text",
                str(agent["id"]),
                "```",
                f"`cwd:` {agent['cwd']}",
            ]
        )
    return "\n".join(rows)


def _text_channel_ids(channel: Any) -> tuple[str, str | None]:
    guild = getattr(channel, "guild", None)
    if guild is None:
        raise PuppetError("invalid_channel", "This command must be used in a guild text channel.")

    is_text_channel = bool(getattr(channel, "is_text_channel", False))
    if discord is not None and isinstance(channel, discord.TextChannel):
        is_text_channel = True
    if not is_text_channel:
        raise PuppetError(
            "invalid_channel",
            "This command must be used in a guild text channel.",
            "Run it from a normal Discord text channel, not a DM, thread, or voice/forum channel.",
        )

    return str(getattr(channel, "id")), str(getattr(guild, "id")) if getattr(guild, "id", None) is not None else None


def _require_bound_root(registry: Registry, channel: Any) -> dict[str, Any]:
    channel_id, _guild_id = _text_channel_ids(channel)
    binding = registry.discord_binding_for_channel(channel_id)
    if not binding:
        raise PuppetError(
            "not_bound",
            "No orchestrator is bound to this channel.",
            "Use /puppet agents, then /puppet bind.",
        )
    return registry.get_agent(binding["root_agent_id"])


def _require_root_orchestrator(registry: Registry, agent_id: str) -> dict[str, Any]:
    agent = registry.maybe_agent(agent_id)
    if not agent:
        raise PuppetError("not_found", f"agent not found: {agent_id}")
    if agent["role"] != "orchestrator" or agent["id"] != agent["root_id"]:
        raise PuppetError(
            "invalid_agent",
            f"agent is not a root orchestrator: {agent_id}",
            "/puppet bind accepts root orchestrator ids only.",
        )
    if agent["status"] in {"killed", "dead"}:
        raise PuppetError(
            "invalid_agent",
            f"root orchestrator is not available: {agent_id}",
            "Start a new root orchestrator, then bind that live root.",
        )
    return agent


def handle_bind_command(registry: Registry, channel: Any, agent_id: str) -> str:
    agent = _require_root_orchestrator(registry, agent_id)
    channel_id, guild_id = _text_channel_ids(channel)
    registry.bind_discord_channel(channel_id, agent["id"], guild_id)
    LOGGER.info(
        "Bound Discord channel %s to root orchestrator %s.",
        channel_id,
        agent["id"],
        extra={"event": "discord.binding.bound", "channel_id": channel_id, "root_agent_id": agent["id"], "guild_id": guild_id},
    )
    return f"Bound this channel to {agent['id']}"


def handle_unbind_command(registry: Registry, channel: Any) -> str:
    channel_id, _guild_id = _text_channel_ids(channel)
    binding = registry.discord_binding_for_channel(channel_id)
    if not binding:
        return "This channel was not bound."
    registry.unbind_discord_channel(channel_id)
    LOGGER.info(
        "Unbound Discord channel %s from root orchestrator %s.",
        channel_id,
        binding["root_agent_id"],
        extra={
            "event": "discord.binding.unbound",
            "channel_id": channel_id,
            "root_agent_id": binding["root_agent_id"],
            "guild_id": binding.get("guild_id"),
        },
    )
    return f"Unbound this channel from {binding['root_agent_id']}"


def handle_status_command(registry: Registry, tmux: Tmux, channel: Any) -> str:
    root = _require_bound_root(registry, channel)
    child_count = sum(1 for agent in registry.list_agents(root_id=root["id"]) if agent["id"] != root["id"])
    live_tmux = "yes" if tmux.session_exists(root["tmux_session"]) else "no"
    lines = [
        f"Root: {root['id']}",
        f"Name: {root.get('name') or '-'}",
        f"Status: {root['status']}",
        f"Cwd: {root['cwd']}",
        f"Live tmux: {live_tmux}",
        f"Child count: {child_count}",
        f"Last turn stopped: {root.get('last_turn_stopped_at') or '-'}",
        f"Completed at: {root.get('completed_at') or '-'}",
    ]
    return "\n".join(lines)


def handle_read_command(config: Config, registry: Registry, tmux: Tmux, channel: Any, lines: int | None = None) -> str:
    if lines is not None and lines <= 0:
        raise PuppetError("invalid_lines", "lines must be a positive integer.")
    root = _require_bound_root(registry, channel)
    output = read_agent(config, registry, tmux, root["id"], lines, "auto")
    return output or "(no output)"


def handle_screenshot_command(registry: Registry, tmux: Tmux, channel: Any, mode: str | None = None) -> ScreenshotResult:
    root = _require_bound_root(registry, channel)
    requested_mode = (mode or "terminal").strip().lower()
    if requested_mode != "terminal":
        try:
            scope, png = capture_native_screenshot(requested_mode)
            return ScreenshotResult(
                root_agent_id=root["id"],
                filename=f"puppet-{root['id']}-native-{scope}.png",
                png=png,
                source=f"native-{scope}",
            )
        except PuppetError as exc:
            if exc.code != "native_screenshot_unavailable":
                raise
            native_note = exc.message
    else:
        native_note = None

    if not tmux.session_exists(root["tmux_session"]):
        raise PuppetError(
            "tmux_missing_session",
            f"tmux session is not live for bound root: {root['id']}",
            "Start or reconcile the root orchestrator before taking a screenshot.",
        )
    pane_text = tmux.capture_visible_pane(root["tmux_session"])
    return ScreenshotResult(
        root_agent_id=root["id"],
        filename=f"puppet-{root['id']}-screenshot.png",
        png=render_terminal_png(pane_text),
        note=native_note,
    )


def _send_bound_root_slash_command(
    registry: Registry,
    tmux: Tmux,
    channel: Any,
    command: str,
    missing_session_hint: str,
) -> SlashCommandResult:
    channel_id, _guild_id = _text_channel_ids(channel)
    root = _require_bound_root(registry, channel)
    if not tmux.session_exists(root["tmux_session"]):
        raise PuppetError(
            "tmux_missing_session",
            f"tmux session is not live for bound root: {root['id']}",
            missing_session_hint,
        )
    tmux.send_prompt(root["tmux_session"], command)
    return SlashCommandResult(
        root_agent_id=root["id"],
        channel_id=channel_id,
        requested_at=now(),
        command=command,
    )


def handle_compact_command(registry: Registry, tmux: Tmux, channel: Any) -> SlashCommandResult:
    return _send_bound_root_slash_command(
        registry,
        tmux,
        channel,
        "/compact",
        "Start or reconcile the root orchestrator before compacting.",
    )


def handle_clear_command(registry: Registry, tmux: Tmux, channel: Any) -> SlashCommandResult:
    return _send_bound_root_slash_command(
        registry,
        tmux,
        channel,
        "/clear",
        "Start or reconcile the root orchestrator before clearing.",
    )


def _normalize_skill_name(skill_name: str | None) -> str | None:
    return normalize_skill_name(skill_name)


def _format_skill_list(skills: list[dict[str, Any]]) -> str:
    if not skills:
        return "No skills saved. Create one with /skills skill-name:<name> prompt:<prompt>."
    lines = ["Saved skills:"]
    lines.extend(f"- {skill['name']}" for skill in skills)
    return "\n".join(lines)


def _format_skill_detail(skill: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Skill: {skill['name']}",
            f"Updated: {skill['updated_at']}",
            "",
            skill["prompt"],
        ]
    )


def autocomplete_discord_skill_names(registry: Registry, current: str | None, limit: int = MAX_SKILL_AUTOCOMPLETE_CHOICES) -> list[str]:
    if limit <= 0:
        return []
    query = (current or "").strip().lower()
    names = [skill["name"] for skill in registry.list_discord_skills()]
    if query:
        names = [name for name in names if query in name]
        names.sort(key=lambda name: (not name.startswith(query), name))
    return names[:limit]


def handle_skills_command(
    registry: Registry,
    tmux: Tmux,
    channel: Any,
    skill_name: str | None = None,
    prompt: str | None = None,
    forget: bool | None = False,
    view: bool | None = False,
) -> str:
    normalized_name = _normalize_skill_name(skill_name)
    prompt_text_value = (prompt or "").strip()

    if normalized_name is None:
        if prompt_text_value or forget or view:
            raise PuppetError("skill_name_required", "skill-name is required.", "Pass a skill-name to create, run, view, or forget a skill.")
        return _format_skill_list(registry.list_discord_skills())

    if forget:
        deleted = registry.delete_discord_skill(normalized_name)
        if not deleted:
            return f"Skill not found: {normalized_name}"
        return f"Forgot skill: {normalized_name}"

    if prompt is not None:
        if not prompt_text_value:
            raise PuppetError("skill_prompt_required", "prompt must be non-empty.", "Pass the reusable prompt text for this skill.")
        registry.upsert_discord_skill(normalized_name, prompt_text_value)
        return f"Saved skill: {normalized_name}"

    skill = registry.discord_skill(normalized_name)
    if not skill:
        raise PuppetError(
            "skill_not_found",
            f"skill not found: {normalized_name}",
            "Create it with /skills skill-name:<name> prompt:<prompt>.",
        )

    if view:
        return _format_skill_detail(skill)

    root = _require_bound_root(registry, channel)
    prompt_agent(
        registry,
        tmux,
        root["id"],
        f"{DISCORD_PROMPT_PREFIX}Run Discord skill `{normalized_name}`:\n\n{skill['prompt']}",
        "discord",
    )
    return f"Sent skill to {root['id']}: {normalized_name}"


def _agent_tree_label(agent: dict[str, Any]) -> str:
    label = agent.get("name") or agent.get("description") or "-"
    return str(label)


def _agent_tree_sort_key(agent: dict[str, Any]) -> tuple[str, str, str]:
    return (str(agent.get("created_at") or ""), _agent_tree_label(agent), str(agent["id"]))


def _format_agent_tree(root: dict[str, Any], agents: list[dict[str, Any]]) -> str:
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for agent in agents:
        by_parent.setdefault(agent["parent_id"], []).append(agent)
    for siblings in by_parent.values():
        siblings.sort(key=_agent_tree_sort_key)

    status_counts: dict[str, int] = {}
    for agent in agents:
        status = str(agent["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    status_summary = ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))

    lines = [
        f"Agent tree: {_agent_tree_label(root)}",
        f"Root: {root['id']}",
        f"Cwd: {root['cwd']}",
        f"Agents: {len(agents)} ({status_summary})",
        "",
    ]

    def append_agent(agent: dict[str, Any], prefix: str = "", connector: str = "") -> None:
        label = _agent_tree_label(agent)
        lines.append(f"{prefix}{connector}{label}  [{agent['role']} | {agent['status']}]")
        detail_prefix = prefix + ("    " if connector == "`-- " else "|   " if connector else "")
        lines.append(f"{detail_prefix}id={agent['id']}")
        if agent["cwd"] != root["cwd"]:
            lines.append(f"{detail_prefix}cwd={agent['cwd']}")

        children = by_parent.get(agent["id"], [])
        for index, child in enumerate(children):
            child_connector = "`-- " if index == len(children) - 1 else "|-- "
            child_prefix = detail_prefix
            append_agent(child, child_prefix, child_connector)

    append_agent(root)
    return "\n".join(lines)


def handle_tree_command(registry: Registry, channel: Any) -> str:
    root = _require_bound_root(registry, channel)
    agents = registry.list_agents(root_id=root["id"])
    if not agents:
        return "No agents found."
    return _format_agent_tree(root, agents)


CommandFunc = Callable[..., str]


async def _defer_interaction_response(interaction: Any) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(thinking=True)


async def _run_interaction_command(
    interaction: Any,
    config: Config,
    func: CommandFunc,
    *args: Any,
) -> None:
    await _defer_interaction_response(interaction)
    try:
        text = func(*args)
    except PuppetError as exc:
        text = format_error(exc)
    await send_interaction_chunks(interaction, text, config)


async def _run_plain_interaction_command(
    interaction: Any,
    config: Config,
    func: CommandFunc,
    *args: Any,
) -> None:
    await _defer_interaction_response(interaction)
    try:
        text = func(*args)
    except PuppetError as exc:
        text = format_error(exc)
    await send_plain_interaction_chunks(interaction, text, config)


async def _run_screenshot_interaction(
    interaction: Any,
    config: Config,
    registry: Registry,
    tmux: Tmux,
    mode: str | None = None,
) -> None:
    await _defer_interaction_response(interaction)
    try:
        screenshot = handle_screenshot_command(registry, tmux, interaction.channel, mode)
    except PuppetError as exc:
        await send_interaction_chunks(interaction, format_error(exc), config)
        return
    file = discord.File(io.BytesIO(screenshot.png), filename=screenshot.filename)
    if screenshot.source.startswith("native-"):
        content = f"Native {screenshot.source.removeprefix('native-')} screenshot for {screenshot.root_agent_id}."
    else:
        content = f"Terminal pane snapshot for {screenshot.root_agent_id}."
        if screenshot.note:
            content += f" Native screenshot unavailable: {screenshot.note}."
    if interaction.response.is_done():
        await interaction.followup.send(content=content, file=file)
    else:
        await interaction.response.send_message(content=content, file=file)


def _require_discord_dependency() -> None:
    if discord is None or app_commands is None or commands is None:
        raise PuppetError(
            "discord_dependency_missing",
            "discord.py is not installed.",
            "Install project dependencies with `uv sync` and retry.",
        )


def _raise_discord_command_sync_error(exc: Exception, guild_id: int) -> None:
    status = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    text = getattr(exc, "text", None)
    detail = f"Discord rejected slash command sync for guild {guild_id}"
    if status is not None or code is not None:
        detail += f" (status={status or 'unknown'}, code={code or 'unknown'})"
    if text:
        detail += f": {text}"
    else:
        detail += "."

    if status == 403 and code == 50001:
        raise PuppetError(
            "discord_guild_access_denied",
            detail,
            "Check discord.guild_id and re-invite the bot to that server with the bot and applications.commands scopes.",
        ) from exc

    raise PuppetError(
        "discord_command_sync_failed",
        detail,
        "Check the Discord bot token, configured guild id, and bot application permissions.",
    ) from exc


async def _sync_discord_commands(bot: Any, guild: Any, guild_id: int) -> list[Any]:
    try:
        return await bot.tree.sync(guild=guild)
    except Exception as exc:
        if discord is not None and isinstance(exc, discord.HTTPException):
            _raise_discord_command_sync_error(exc, guild_id)
        raise


def build_discord_bot(config: Config | None = None, registry: Registry | None = None, tmux: Tmux | None = None) -> Any:
    _require_discord_dependency()
    cfg = config or load_config()
    discord_config = validate_discord_config(cfg)
    reg = registry or Registry(cfg)
    tmux_client = tmux or Tmux(cfg)

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="puppet!", intents=intents)
    runtime = DiscordRuntime(cfg, reg, tmux_client, bot=bot)
    guild = discord.Object(id=discord_config.guild_id)
    group = app_commands.Group(name="puppet", description="Puppetmaster commands")

    @group.command(name="agents", description="List root orchestrators.")
    async def agents(interaction: discord.Interaction) -> None:
        await _run_plain_interaction_command(interaction, cfg, handle_agents_command, reg)

    @group.command(name="bind", description="Bind this channel to a root orchestrator.")
    @app_commands.describe(agent_id="Root orchestrator agent id.")
    async def bind(interaction: discord.Interaction, agent_id: str) -> None:
        await _run_interaction_command(interaction, cfg, handle_bind_command, reg, interaction.channel, agent_id)

    @group.command(name="unbind", description="Remove this channel binding.")
    async def unbind(interaction: discord.Interaction) -> None:
        await _run_interaction_command(interaction, cfg, handle_unbind_command, reg, interaction.channel)

    @group.command(name="status", description="Show the bound root orchestrator status.")
    async def status(interaction: discord.Interaction) -> None:
        await _run_interaction_command(interaction, cfg, handle_status_command, reg, tmux_client, interaction.channel)

    @group.command(name="read", description="Read recent output from the bound root orchestrator.")
    @app_commands.describe(lines="Optional number of terminal lines to read.")
    async def read(interaction: discord.Interaction, lines: int | None = None) -> None:
        await _run_interaction_command(interaction, cfg, handle_read_command, cfg, reg, tmux_client, interaction.channel, lines)

    @group.command(name="tree", description="Show the bound root orchestrator tree.")
    async def tree(interaction: discord.Interaction) -> None:
        await _run_interaction_command(interaction, cfg, handle_tree_command, reg, interaction.channel)

    @group.command(name="screenshot", description="Send a PNG snapshot for the bound root.")
    @app_commands.describe(mode="terminal, native-window, or native-screen.")
    async def screenshot(interaction: discord.Interaction, mode: str | None = None) -> None:
        await _run_screenshot_interaction(interaction, cfg, reg, tmux_client, mode)

    @group.command(name="compact", description="Compact the bound root orchestrator context.")
    async def compact(interaction: discord.Interaction) -> None:
        await _run_interaction_command(interaction, cfg, runtime.request_compact, interaction.channel)

    @group.command(name="clear", description="Clear the bound root orchestrator context.")
    async def clear(interaction: discord.Interaction) -> None:
        await _run_interaction_command(interaction, cfg, runtime.request_clear, interaction.channel)

    async def skill_name_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        del interaction
        return [
            app_commands.Choice(name=name, value=name)
            for name in autocomplete_discord_skill_names(reg, current)
        ]

    @app_commands.command(name="skills", description="Create, list, view, forget, or run reusable prompts.")
    @app_commands.describe(
        skill_name="Skill name to run, view, create, or forget.",
        prompt="Reusable prompt text. Omit this to run the skill.",
        view="Show the stored prompt instead of running it.",
        forget="Delete this skill instead of running it.",
    )
    @app_commands.autocomplete(skill_name=skill_name_autocomplete)
    async def skills(
        interaction: discord.Interaction,
        skill_name: str | None = None,
        prompt: str | None = None,
        view: bool | None = False,
        forget: bool | None = False,
    ) -> None:
        await _run_interaction_command(
            interaction,
            cfg,
            handle_skills_command,
            reg,
            tmux_client,
            interaction.channel,
            skill_name,
            prompt,
            forget,
            view,
        )

    @bot.event
    async def setup_hook() -> None:
        bot.tree.add_command(group, guild=guild)
        bot.tree.add_command(skills, guild=guild)
        synced = await _sync_discord_commands(bot, guild, discord_config.guild_id)
        LOGGER.info(
            "Synced %s Discord slash command object(s) to guild %s.",
            len(synced),
            discord_config.guild_id,
            extra={"event": "discord.commands.synced", "guild_id": str(discord_config.guild_id), "command_count": len(synced)},
        )
        supervisor_log(
            cfg,
            "info",
            "discord.commands.synced",
            "Discord slash commands synced.",
            guild_id=str(discord_config.guild_id),
            command_count=len(synced),
        )
        print(f"Synced {len(synced)} Discord slash command object(s) to guild {discord_config.guild_id}.")

    @bot.event
    async def on_ready() -> None:
        runtime.start()

    @bot.event
    async def on_message(message: discord.Message) -> None:
        await runtime.handle_message(message, bot.user)
        await bot.process_commands(message)

    original_close = bot.close

    async def close_with_runtime() -> None:
        await runtime.close()
        await original_close()

    bot.close = close_with_runtime  # type: ignore[method-assign]
    bot.puppetmaster_runtime = runtime  # type: ignore[attr-defined]
    return bot


def run_discord_bot() -> int:
    cfg = load_config()
    discord_config = validate_discord_config(cfg)
    LOGGER.info(
        "Starting Discord bot for guild %s.",
        discord_config.guild_id,
        extra={"event": "discord.bot.starting", "guild_id": str(discord_config.guild_id)},
    )
    supervisor_log(
        cfg,
        "info",
        "discord.bot.starting",
        "Discord bot starting.",
        guild_id=str(discord_config.guild_id),
        poll_interval_seconds=discord_config.poll_interval_seconds,
        typing_timeout_seconds=discord_config.typing_timeout_seconds,
    )
    bot = build_discord_bot(cfg)
    previous_handlers: dict[signal.Signals, Any] = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, lambda received, frame: _raise_for_shutdown_signal(cfg, received, frame))

    try:
        bot.run(discord_config.token)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        LOGGER.info(
            "Discord bot exiting with code %s.",
            code,
            extra={"event": "discord.bot.exiting", "exit_code": code},
        )
        supervisor_log(
            cfg,
            "info",
            "discord.bot.exiting",
            "Discord bot exiting.",
            exit_code=code,
        )
        raise
    except BaseException as exc:
        LOGGER.exception(
            "Discord bot crashed.",
            extra={"event": "discord.bot.crashed", "exception_type": type(exc).__name__},
        )
        supervisor_log(
            cfg,
            "error",
            "discord.bot.crashed",
            "Discord bot crashed.",
            exception_type=type(exc).__name__,
            error=str(exc),
        )
        raise
    else:
        LOGGER.info("Discord bot stopped.", extra={"event": "discord.bot.stopped"})
        supervisor_log(cfg, "info", "discord.bot.stopped", "Discord bot stopped.")
        return 0
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
