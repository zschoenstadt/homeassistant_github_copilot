"""Base entity for GitHub Copilot integration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import json
import logging
from typing import Any
import uuid

from copilot.generated.session_events import SessionEvent, SessionEventType
from copilot.session import Tool
from copilot.tools import ToolInvocation, ToolResult
from homeassistant.components.conversation import (
    AssistantContentDeltaDict,
    ChatLog,
    SystemContent,
    UserContent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.entity import Entity
from voluptuous_openapi import convert

from .api import SESSION_RESPONSE_TIMEOUT, GitHubCopilotSDKClient
from .const import CONF_MODEL, CONF_PROMPT, DEFAULT_MODEL, DEFAULT_SYSTEM_PROMPT, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Sentinel value to signal the end of the event stream
_STREAM_DONE = object()


class GitHubCopilotBaseEntity(Entity):
    """Base entity providing shared Copilot LLM logic via the SDK."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the base entity."""

        self.entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "GitHub Copilot Client",
        }

    @property
    def model(self) -> str:
        """Return the configured model (options override data)."""

        return self.entry.options.get(
            CONF_MODEL, self.entry.data.get(CONF_MODEL, DEFAULT_MODEL)
        )

    @property
    def system_prompt(self) -> str:
        """Return the configured system prompt (options override data)."""

        return self.entry.options.get(
            CONF_PROMPT, self.entry.data.get(CONF_PROMPT, DEFAULT_SYSTEM_PROMPT)
        )

    def _build_sdk_tools(
        self,
        chat_log: ChatLog,
    ) -> tuple[list[Tool], dict[str, llm.Tool]]:
        """Convert HA LLM tools to SDK Tool objects.

        Returns a tuple of (sdk_tools, ha_tool_map) where ha_tool_map
        maps tool names to HA tool objects for later execution.
        """

        if not chat_log.llm_api or not chat_log.llm_api.tools:
            return [], {}

        sdk_tools: list[Tool] = []
        ha_tool_map: dict[str, llm.Tool] = {}

        for ha_tool in chat_log.llm_api.tools:
            ha_tool_map[ha_tool.name] = ha_tool

            # Convert HA tool parameters to JSON Schema
            parameters = convert(
                ha_tool.parameters,
                custom_serializer=chat_log.llm_api.custom_serializer,
            )

            sdk_tools.append(
                Tool(
                    name=ha_tool.name,
                    description=ha_tool.description or "",
                    handler=self._make_tool_handler(ha_tool.name, chat_log),
                    parameters=parameters,
                    skip_permission=True,
                )
            )

        return sdk_tools, ha_tool_map

    def _make_tool_handler(
        self,
        tool_name: str,
        chat_log: ChatLog,
    ) -> Any:
        """Create an SDK tool handler that delegates to HA's LLM tool execution."""

        async def handler(invocation: ToolInvocation) -> ToolResult:
            """Execute the tool via HA's LLM API and return the result."""

            if not chat_log.llm_api:
                return ToolResult(
                    text_result_for_llm="",
                    result_type="error",
                    error="No LLM API available",
                )

            try:
                args = invocation.arguments or {}
                tool_input = llm.ToolInput(
                    tool_name=tool_name,
                    tool_args=args,
                    id=invocation.tool_call_id,
                )

                # Execute the tool through HA's LLM API
                result = await chat_log.llm_api.async_call_tool(tool_input)
                return ToolResult(
                    text_result_for_llm=json.dumps(result)
                    if result is not None
                    else "",
                )
            except (HomeAssistantError, KeyError, TypeError, ValueError) as err:
                _LOGGER.warning("Tool %s execution failed: %s", tool_name, err)
                return ToolResult(
                    text_result_for_llm="",
                    result_type="error",
                    error=str(err),
                )

        return handler

    async def _async_handle_chat_log(
        self,
        chat_log: ChatLog,
        *,
        structure: Any | None = None,
    ) -> None:
        """Process a ChatLog through the Copilot SDK and update the log.

        Uses resume-first session management: tries to resume an existing SDK
        session (preserving conversation history in the CLI), falls back to
        creating a new one.  System prompt is only set on session creation.
        The SDK handles the tool-call loop internally.
        """

        runtime = self.entry.runtime_data
        sdk_client: GitHubCopilotSDKClient = runtime.sdk_client

        # Build SDK tools from HA's LLM API tools
        sdk_tools, ha_tool_map = self._build_sdk_tools(chat_log)

        # Build the system message (only used for new session creation)
        system_message = self._extract_system_message(chat_log)
        if structure is not None:
            structure_instruction = (
                "Respond with valid JSON matching this structure. "
                "Do not include any other text.\n"
                f"{structure!s}"
            )
            system_message = (
                f"{system_message}\n\n{structure_instruction}"
                if system_message
                else structure_instruction
            )

        # Build the user prompt from the chat log's last user message
        user_prompt = self._extract_user_prompt(chat_log)

        # Derive a deterministic UUID from entry + conversation IDs.
        # The CLI rejects arbitrary strings; uuid5 produces a valid UUID format.
        session_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL, f"{self.entry.entry_id}:{chat_log.conversation_id}"
            )
        )

        # Queue for bridging SDK's sync event callbacks to async HA code
        event_queue: asyncio.Queue[SessionEvent | object] = asyncio.Queue()

        def on_event(event: SessionEvent) -> None:
            """Push SDK events into the async queue."""

            event_queue.put_nowait(event)

            # Signal completion when the session goes idle
            if event.type == SessionEventType.SESSION_IDLE:
                event_queue.put_nowait(_STREAM_DONE)

        # Resume or create the SDK session
        session = await sdk_client.async_get_or_create_session(
            session_id=session_id,
            model=self.model,
            system_message=system_message,
            tools=sdk_tools or None,
            streaming=True,
            on_event=on_event,
        )

        try:
            await session.send(user_prompt)

            # Stream events from the SDK into the chat log
            async for _content in chat_log.async_add_delta_content_stream(
                self.entity_id,
                self._transform_sdk_events(event_queue, ha_tool_map),
            ):
                pass

            # Disconnect to release in-memory resources but keep session on disk
            await session.disconnect()

        except TimeoutError as err:
            # Disconnect on timeout — session can be resumed next turn
            await session.disconnect()
            raise TimeoutError(
                "Timed out waiting for response from GitHub Copilot"
            ) from err

    def _extract_system_message(self, chat_log: ChatLog) -> str:
        """Extract the system message from a ChatLog."""

        parts = [
            content.content
            for content in chat_log.content
            if isinstance(content, SystemContent) and content.content
        ]

        return "\n\n".join(parts) if parts else self.system_prompt

    def _extract_user_prompt(self, chat_log: ChatLog) -> str:
        """Extract the last user message from a ChatLog."""

        # Walk backward to find the last user message
        for content in reversed(chat_log.content):
            if isinstance(content, UserContent) and content.content:
                return content.content

        return ""

    async def _transform_sdk_events(
        self,
        event_queue: asyncio.Queue[SessionEvent | object],
        ha_tool_map: dict[str, llm.Tool],
    ) -> AsyncGenerator[AssistantContentDeltaDict]:
        """Transform SDK session events into HA delta content dicts.

        Consumes events from the queue until _STREAM_DONE is received.
        Yields AssistantContentDeltaDict entries compatible with
        chat_log.async_add_delta_content_stream().
        """

        # Track whether we've yielded a role marker for the current message
        role_yielded = False

        while True:
            try:
                event = await asyncio.wait_for(
                    event_queue.get(),
                    timeout=SESSION_RESPONSE_TIMEOUT,
                )
            except TimeoutError:
                _LOGGER.error("Timed out waiting for SDK event")
                break

            # End of stream
            if event is _STREAM_DONE:
                break

            # Streaming text delta from the assistant
            if event.type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
                if not role_yielded:
                    yield {"role": "assistant"}
                    role_yielded = True

                delta = event.data.delta_content
                if delta:
                    yield {"content": delta}

            # Final assistant message (may contain tool requests)
            elif event.type == SessionEventType.ASSISTANT_MESSAGE:
                if not role_yielded:
                    yield {"role": "assistant"}
                    role_yielded = True

                # If the final message has content and we missed deltas
                if event.data.content and not role_yielded:
                    yield {"content": event.data.content}

                # Emit tool calls as external (SDK handles execution)
                if event.data.tool_requests:
                    tool_calls = [
                        llm.ToolInput(
                            id=tr.tool_call_id,
                            tool_name=tr.name,
                            tool_args=tr.arguments or {},
                            external=True,
                        )
                        for tr in event.data.tool_requests
                    ]
                    if tool_calls:
                        yield {"tool_calls": tool_calls}

                # Reset for next turn (after tool results)
                role_yielded = False

            # Tool execution completed — record the result in chat log
            elif event.type == SessionEventType.TOOL_EXECUTION_COMPLETE:
                tool_name = event.data.tool_name or ""
                tool_call_id = event.data.tool_call_id or ""
                result_text = event.data.result or ""

                # Parse the result if possible
                try:
                    tool_result = json.loads(result_text) if result_text else {}
                except (json.JSONDecodeError, TypeError):
                    tool_result = {"result": result_text}

                yield {
                    "role": "tool_result",
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_result": tool_result,
                }

            # Handle errors from the session
            elif event.type == SessionEventType.SESSION_ERROR:
                error_msg = event.data.message or "Unknown SDK error"
                _LOGGER.error("SDK session error: %s", error_msg)
                if not role_yielded:
                    yield {"role": "assistant"}
                    role_yielded = True
                yield {"content": f"Error: {error_msg}"}
                break
