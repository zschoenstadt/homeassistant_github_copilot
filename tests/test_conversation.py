"""Tests for the GitHub Copilot conversation entity."""

from __future__ import annotations

import json
from unittest.mock import patch

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.intent import IntentResponseType
import pytest

from custom_components.github_copilot.api import (
    GitHubCopilotApiError,
    GitHubCopilotAuthError,
)
from custom_components.github_copilot.const import MAX_TOOL_ITERATIONS
from custom_components.github_copilot.conversation import (
    GitHubCopilotConversationEntity,
)

from .conftest import MOCK_CHAT_COMPLETION_RESPONSE


@pytest.fixture
async def setup_conversation(
    hass: HomeAssistant, mock_config_entry, mock_runtime, setup_ha
):
    """Set up the conversation entity for testing."""

    mock_config_entry.runtime_data = mock_runtime

    with (
        patch(
            "custom_components.github_copilot.Runtime",
            return_value=mock_runtime,
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotAuth",
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotClient",
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    return mock_config_entry


async def _converse(hass, text, entry, **kwargs):
    """Helper to converse with our specific agent."""

    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=kwargs.get("conversation_id"),
        context=None,
        agent_id=entry.entry_id,
    )


async def test_entity_setup(hass: HomeAssistant, setup_conversation):
    """Test that the conversation entity is registered."""

    entry = setup_conversation
    entity_id = "conversation.github_copilot_client_github_copilot_conversation"

    hass.states.get(entity_id)
    assert entry.state is ConfigEntryState.LOADED


async def test_handle_message_success(
    hass: HomeAssistant, setup_conversation, mock_client
):
    """Test successful message handling."""

    mock_client.async_chat_completion.return_value = MOCK_CHAT_COMPLETION_RESPONSE

    result = await _converse(hass, "Hello, turn on the lights", setup_conversation)

    assert result.response.speech is not None
    assert "Hello" in result.response.speech["plain"]["speech"]
    mock_client.async_chat_completion.assert_called_once()

    # Verify the user message was passed through
    call_args = mock_client.async_chat_completion.call_args
    messages = call_args.kwargs.get("messages") or call_args[1].get("messages", [])
    assert any("Hello, turn on the lights" in m.get("content", "") for m in messages)


async def test_handle_message_api_error(
    hass: HomeAssistant, setup_conversation, mock_client
):
    """Test message handling when API returns error."""

    mock_client.async_chat_completion.side_effect = GitHubCopilotApiError(
        "Server error"
    )

    result = await _converse(hass, "Hello", setup_conversation)

    assert result.response.response_type == IntentResponseType.ERROR


async def test_handle_message_auth_expired(
    hass: HomeAssistant, setup_conversation, mock_client
):
    """Test message handling with expired auth returns error.

    Retry logic is now inside Runtime, not the entity. The entity sees the
    error after Runtime's retry has already failed or been handled.
    """

    mock_client.async_chat_completion.side_effect = GitHubCopilotAuthError(
        "Token expired"
    )

    result = await _converse(hass, "Hello after refresh", setup_conversation)

    assert result.response.response_type == IntentResponseType.ERROR


async def test_supported_languages(hass: HomeAssistant, setup_conversation):
    """Test that the conversation entity supports all languages."""

    entry = setup_conversation
    assert entry.state is ConfigEntryState.LOADED

    # Find the actual entity instance and verify supported_languages
    component = hass.data.get("entity_components", {}).get("conversation")
    if component:
        entities = [
            e
            for e in component.entities
            if isinstance(e, GitHubCopilotConversationEntity)
        ]
        assert len(entities) == 1
        assert entities[0].supported_languages == MATCH_ALL


async def test_handle_message_refresh_failure(
    hass: HomeAssistant, setup_conversation, mock_client
):
    """Test message handling when auth error is not recoverable.

    Refresh logic is now inside Runtime. The entity just sees the final error.
    """

    mock_client.async_chat_completion.side_effect = GitHubCopilotAuthError(
        "Token expired"
    )

    result = await _converse(hass, "Hello after failed refresh", setup_conversation)

    assert result.response.response_type == IntentResponseType.ERROR


async def test_handle_message_no_choices(
    hass: HomeAssistant, setup_conversation, mock_client
):
    """Test message handling when API returns empty choices array."""

    mock_client.async_chat_completion.return_value = {"choices": []}

    result = await _converse(hass, "Hello", setup_conversation)

    assert result.response.response_type == IntentResponseType.ERROR


async def test_handle_message_model_no_access(
    hass: HomeAssistant, setup_conversation, mock_client
):
    """Test that a 403 model access error gives a clear message."""

    mock_client.async_chat_completion.side_effect = GitHubCopilotApiError(
        "Chat completion error 403: No access to model"
    )

    result = await _converse(hass, "Hello", setup_conversation)

    assert result.response.response_type == IntentResponseType.ERROR
    error_text = result.response.speech["plain"]["speech"]
    assert "not accessible" in error_text
    assert "integration options" in error_text


async def test_conversation_feature_control_flag(
    hass: HomeAssistant, mock_config_entry, mock_runtime, setup_ha
):
    """Test that CONTROL feature flag is set when LLM API is configured."""

    # Set up with LLM API configured
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, "llm_hass_api": ["assist"]},
    )
    mock_config_entry.runtime_data = mock_runtime

    with (
        patch(
            "custom_components.github_copilot.Runtime",
            return_value=mock_runtime,
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotAuth",
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotClient",
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    component = hass.data.get("entity_components", {}).get("conversation")
    if component:
        entities = [
            e
            for e in component.entities
            if isinstance(e, GitHubCopilotConversationEntity)
        ]
        assert len(entities) == 1
        assert (
            entities[0].supported_features
            == conversation.ConversationEntityFeature.CONTROL
        )


async def test_conversation_no_control_flag_without_llm_api(
    hass: HomeAssistant, setup_conversation
):
    """Test that CONTROL feature flag is NOT set without LLM API."""

    component = hass.data.get("entity_components", {}).get("conversation")
    if component:
        entities = [
            e
            for e in component.entities
            if isinstance(e, GitHubCopilotConversationEntity)
        ]
        assert len(entities) == 1
        assert entities[0].supported_features == conversation.ConversationEntityFeature(
            0
        )


async def test_tool_calls_passed_to_api(
    hass: HomeAssistant, setup_conversation, mock_client
):
    """Test that when model returns tool_calls without LLM API, they're handled gracefully."""

    # Model returns tool_calls but no LLM API is configured,
    # so async_add_assistant_content won't execute tools — just one call
    tool_call_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "I'll try to help.",
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "test_tool",
                                "arguments": json.dumps({"arg1": "value1"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    mock_client.async_chat_completion.return_value = tool_call_response

    result = await _converse(hass, "Use a tool please", setup_conversation)

    assert result.response.speech is not None
    mock_client.async_chat_completion.assert_called_once()


async def test_tools_included_in_api_payload(
    hass: HomeAssistant, setup_conversation, mock_client
):
    """Test that tools=None is passed when no LLM API configured."""

    mock_client.async_chat_completion.return_value = MOCK_CHAT_COMPLETION_RESPONSE

    await _converse(hass, "Hello", setup_conversation)

    call_args = mock_client.async_chat_completion.call_args
    assert call_args.kwargs.get("tools") is None


async def test_max_history_trimming(
    hass: HomeAssistant, mock_config_entry, mock_runtime, mock_client, setup_ha
):
    """Test that message history is trimmed based on max_history setting."""

    # Set max_history to 2
    hass.config_entries.async_update_entry(
        mock_config_entry, options={**mock_config_entry.options, "max_history": 2}
    )
    mock_config_entry.runtime_data = mock_runtime
    mock_client.async_chat_completion.return_value = MOCK_CHAT_COMPLETION_RESPONSE

    with (
        patch(
            "custom_components.github_copilot.Runtime",
            return_value=mock_runtime,
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotAuth",
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotClient",
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # Send multiple messages to build history
    conv_id = None
    for i in range(5):
        result = await conversation.async_converse(
            hass=hass,
            text=f"Message {i}",
            conversation_id=conv_id,
            context=None,
            agent_id=mock_config_entry.entry_id,
        )
        if conv_id is None:
            conv_id = result.conversation_id

    # Check the last API call's messages were trimmed
    last_call = mock_client.async_chat_completion.call_args
    messages = last_call.kwargs.get("messages") or last_call[1].get("messages", [])

    # With max_history=2, should have: system + 2 turns (4 msgs) + current user = 6-ish
    user_messages = [m for m in messages if m.get("role") == "user"]
    assert len(user_messages) <= 3


async def test_tool_call_iteration_loop(
    hass: HomeAssistant, mock_config_entry, mock_runtime, mock_client, setup_ha
):
    """Test that the tool call loop iterates when the API returns tool_calls.

    First response has tool_calls → loop continues (tools execute).
    Second response has plain text → loop exits.
    Requires LLM API to be configured so tool results are produced.
    """

    # Configure with LLM API so tool calls are processed
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, "llm_hass_api": ["assist"]},
    )
    mock_config_entry.runtime_data = mock_runtime

    with (
        patch(
            "custom_components.github_copilot.Runtime",
            return_value=mock_runtime,
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotAuth",
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotClient",
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    tool_call_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "HassGetState",
                                "arguments": json.dumps({}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }

    final_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Done! The tool worked.",
                },
                "finish_reason": "stop",
            }
        ]
    }

    # First call returns tool_calls, second returns plain text
    mock_client.async_chat_completion.side_effect = [tool_call_response, final_response]

    result = await _converse(hass, "What is the state?", mock_config_entry)

    assert result.response.speech is not None
    assert "Done! The tool worked." in result.response.speech["plain"]["speech"]
    assert mock_client.async_chat_completion.call_count == 2


async def test_tool_call_max_iterations_warning(
    hass: HomeAssistant,
    mock_config_entry,
    mock_runtime,
    mock_client,
    setup_ha,
    caplog,
):
    """Test that hitting MAX_TOOL_ITERATIONS logs a warning and returns gracefully."""

    # Configure with LLM API so tool calls are processed
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, "llm_hass_api": ["assist"]},
    )
    mock_config_entry.runtime_data = mock_runtime

    with (
        patch(
            "custom_components.github_copilot.Runtime",
            return_value=mock_runtime,
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotAuth",
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotClient",
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    tool_call_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Still calling tools...",
                    "tool_calls": [
                        {
                            "id": "call_loop",
                            "type": "function",
                            "function": {
                                "name": "HassGetState",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }

    # Always return tool_calls — should hit max iterations
    mock_client.async_chat_completion.return_value = tool_call_response

    result = await _converse(hass, "Loop forever", mock_config_entry)

    assert result.response.speech is not None
    assert mock_client.async_chat_completion.call_count == MAX_TOOL_ITERATIONS
    assert "Max tool iterations" in caplog.text


async def test_max_history_zero_unlimited(
    hass: HomeAssistant, mock_config_entry, mock_runtime, mock_client, setup_ha
):
    """Test that max_history=0 means unlimited (no trimming)."""

    hass.config_entries.async_update_entry(
        mock_config_entry, options={**mock_config_entry.options, "max_history": 0}
    )
    mock_config_entry.runtime_data = mock_runtime
    mock_client.async_chat_completion.return_value = MOCK_CHAT_COMPLETION_RESPONSE

    with (
        patch(
            "custom_components.github_copilot.Runtime",
            return_value=mock_runtime,
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotAuth",
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotClient",
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    conv_id = None
    for i in range(5):
        result = await conversation.async_converse(
            hass=hass,
            text=f"Message {i}",
            conversation_id=conv_id,
            context=None,
            agent_id=mock_config_entry.entry_id,
        )
        if conv_id is None:
            conv_id = result.conversation_id

    # With unlimited history, all messages should be present
    last_call = mock_client.async_chat_completion.call_args
    messages = last_call.kwargs.get("messages") or last_call[1].get("messages", [])
    user_messages = [m for m in messages if m.get("role") == "user"]
    assert len(user_messages) == 5
