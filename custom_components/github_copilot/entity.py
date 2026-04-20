"""Base entity for GitHub Copilot integration."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components.conversation import (
    AssistantContent,
    ChatLog,
    SystemContent,
    ToolResultContent,
    UserContent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import llm
from homeassistant.helpers.entity import Entity

from .api import GitHubCopilotClient
from .const import (
    CONF_MAX_HISTORY,
    CONF_MODEL,
    CONF_PROMPT,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    DOMAIN,
    MAX_TOOL_ITERATIONS,
)

_LOGGER = logging.getLogger(__name__)


def _format_tool(tool: llm.Tool, custom_serializer: Any = None) -> dict[str, Any]:
    """Convert an HA LLM Tool to OpenAI function-calling format."""

    parameters = llm.convert(tool.parameters, custom_serializer=custom_serializer)
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": parameters,
        },
    }


class GitHubCopilotBaseEntity(Entity):
    """Base entity providing shared Copilot LLM logic."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the base entity."""

        self.entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "GitHub Copilot",
            "manufacturer": "GitHub",
        }

    @property
    def client(self) -> GitHubCopilotClient:
        """Return the API client."""

        return self.entry.runtime_data

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

    @property
    def max_history(self) -> int:
        """Return the max history messages setting."""

        return int(self.entry.options.get(CONF_MAX_HISTORY, DEFAULT_MAX_HISTORY))

    def _chat_log_to_messages(self, chat_log: ChatLog) -> list[dict[str, Any]]:
        """Convert a ChatLog to API message format."""

        messages: list[dict[str, Any]] = []

        for content in chat_log.content:
            if isinstance(content, SystemContent):
                messages.append({"role": "system", "content": content.content or ""})

            elif isinstance(content, UserContent):
                messages.append({"role": "user", "content": content.content or ""})

            elif isinstance(content, AssistantContent):
                msg: dict[str, Any] = {"role": "assistant"}
                if content.content:
                    msg["content"] = content.content
                if content.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.tool_name,
                                "arguments": json.dumps(tc.tool_args),
                            },
                        }
                        for tc in content.tool_calls
                    ]
                elif not content.content:
                    msg["content"] = ""
                messages.append(msg)

            elif isinstance(content, ToolResultContent):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": content.tool_call_id,
                        "content": json.dumps(content.tool_result),
                    }
                )

        return messages

    def _trim_history(
        self, messages: list[dict[str, Any]], max_messages: int
    ) -> list[dict[str, Any]]:
        """Trim message history to keep system prompt + last N turns + current."""

        if max_messages < 1:
            return messages  # 0 = unlimited

        # Find all user message positions to count turns
        user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        if len(user_indices) <= max_messages:
            return messages  # Under limit

        # Keep: system prompt (first) + last max_messages user turns + current
        # Each turn is roughly 2 messages (user + assistant)
        num_keep = 2 * max_messages + 1  # +1 for current user message

        if len(messages) > num_keep + 1:
            return [messages[0], *messages[-num_keep:]]
        return messages

    async def _async_handle_chat_log(
        self,
        chat_log: ChatLog,
        *,
        structure: Any | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Process a ChatLog through the Copilot API and update the log."""

        # Build tools list if LLM API is configured
        tools: list[dict[str, Any]] | None = None
        if chat_log.llm_api:
            custom_serializer = chat_log.llm_api.custom_serializer
            tools = [
                _format_tool(tool, custom_serializer) for tool in chat_log.llm_api.tools
            ]
            if not tools:
                tools = None

        # Iterate up to MAX_TOOL_ITERATIONS, calling the API and
        # executing any tool calls it returns each round
        for _iteration in range(MAX_TOOL_ITERATIONS):
            # Convert the chat log to API message format and trim history
            messages = self._chat_log_to_messages(chat_log)
            messages = self._trim_history(messages, self.max_history)

            # Append structure instructions if structured output is requested
            if structure is not None:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Respond with valid JSON matching this structure. "
                            "Do not include any other text.\n"
                            f"{structure!s}"
                        ),
                    }
                )

            # Call the Copilot API, retrying once on auth failure
            try:
                response = await self.client.async_chat_completion(
                    messages=messages,
                    model=self.model,
                    max_tokens=max_tokens,
                    tools=tools,
                )
            except GitHubCopilotClient.AuthError:
                _LOGGER.warning("Auth error during chat, attempting token refresh")
                await self.client.async_refresh_token()
                response = await self.client.async_chat_completion(
                    messages=messages,
                    model=self.model,
                    max_tokens=max_tokens,
                    tools=tools,
                )
            except GitHubCopilotClient.ApiError as err:
                if "403" in str(err):
                    raise ValueError(
                        f"Model '{self.model}' is not accessible with "
                        "your GitHub Copilot subscription. Change the "
                        "model in the integration options."
                    ) from err
                raise

            # Extract the assistant message from the response
            choices = response.get("choices", [])
            if not choices:
                raise ValueError("No choices in response")

            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content")
            tool_calls_data = message.get("tool_calls")

            # Parse tool calls from the response, if any
            tool_calls: list[llm.ToolInput] | None = None
            if tool_calls_data:
                tool_calls = []
                for tc in tool_calls_data:
                    func = tc.get("function", {})
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append(
                        llm.ToolInput(
                            tool_name=func.get("name", ""),
                            tool_args=args,
                            id=tc.get("id", ""),
                        )
                    )

            # Record the assistant's response and execute any tool calls
            assistant_content = AssistantContent(
                agent_id=self.entity_id or "",
                content=content,
                tool_calls=tool_calls,
            )

            has_tool_results = False
            async for _tool_result in chat_log.async_add_assistant_content(
                assistant_content
            ):
                has_tool_results = True

            # If no tool calls were made, we're done
            if not has_tool_results:
                return content or ""

        # Hit max iterations — return whatever content we have
        _LOGGER.warning("Max tool iterations (%d) reached", MAX_TOOL_ITERATIONS)
        return content or ""
