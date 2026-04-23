"""Tests for the GitHub Copilot AI Task entity."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.github_copilot.api import GitHubCopilotApiError

from .conftest import MOCK_CHAT_COMPLETION_JSON_RESPONSE, MOCK_CHAT_COMPLETION_RESPONSE


@pytest.fixture
async def setup_ai_task(
    hass: HomeAssistant, mock_config_entry, mock_runtime, mock_client, setup_ha
):
    """Set up the AI task entity for testing."""

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


async def test_entity_setup(hass: HomeAssistant, setup_ai_task):
    """Test that the AI task entity is registered."""

    entry = setup_ai_task
    assert entry.state is ConfigEntryState.LOADED


async def test_generate_data_plain_text(
    hass: HomeAssistant, setup_ai_task, mock_client
):
    """Test generating plain text data."""

    mock_client.async_chat_completion.return_value = MOCK_CHAT_COMPLETION_RESPONSE

    # Call the ai_task service
    result = await hass.services.async_call(
        "ai_task",
        "generate_data",
        {
            "task_name": "test_task",
            "entity_id": "ai_task.github_copilot_client_github_copilot_ai_task",
            "instructions": "Describe the weather",
        },
        blocking=True,
        return_response=True,
    )

    assert result is not None
    assert "data" in result
    assert "Hello" in result["data"]
    mock_client.async_chat_completion.assert_called_once()


async def test_generate_data_api_error(hass: HomeAssistant, setup_ai_task, mock_client):
    """Test AI task with API error."""

    mock_client.async_chat_completion.side_effect = GitHubCopilotApiError(
        "Server error"
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "ai_task",
            "generate_data",
            {
                "task_name": "test_task",
                "entity_id": "ai_task.github_copilot_client_github_copilot_ai_task",
                "instructions": "Describe the weather",
            },
            blocking=True,
            return_response=True,
        )


async def test_generate_data_empty_response(
    hass: HomeAssistant, setup_ai_task, mock_client
):
    """Test AI task with empty response."""

    mock_client.async_chat_completion.return_value = {
        "choices": [{"message": {"role": "assistant", "content": ""}}]
    }

    result = await hass.services.async_call(
        "ai_task",
        "generate_data",
        {
            "task_name": "test_task",
            "entity_id": "ai_task.github_copilot_client_github_copilot_ai_task",
            "instructions": "Describe the weather",
        },
        blocking=True,
        return_response=True,
    )

    # Should still return a result, just with empty data
    assert result is not None
    assert "data" in result
    assert result["data"] == ""


async def test_generate_data_structured_json(
    hass: HomeAssistant, setup_ai_task, mock_client
):
    """Test generating structured JSON data."""

    mock_client.async_chat_completion.return_value = MOCK_CHAT_COMPLETION_JSON_RESPONSE

    result = await hass.services.async_call(
        "ai_task",
        "generate_data",
        {
            "task_name": "test_task",
            "entity_id": "ai_task.github_copilot_client_github_copilot_ai_task",
            "instructions": "Get the temperature",
            "structure": {
                "temperature": {
                    "selector": {"number": {"min": -50, "max": 100}},
                    "description": "Current temperature",
                },
            },
        },
        blocking=True,
        return_response=True,
    )

    assert result is not None
    # Verify the structure prompt was appended to messages
    call_args = mock_client.async_chat_completion.call_args
    messages = call_args.kwargs.get("messages") or call_args[1].get("messages", [])
    assert any(
        "structure" in m.get("content", "").lower()
        for m in messages
        if m.get("role") == "system"
    )


async def test_generate_data_structure_message_format(
    hass: HomeAssistant, setup_ai_task, mock_client
):
    """Test that structured output appends the exact expected system message format."""

    mock_client.async_chat_completion.return_value = MOCK_CHAT_COMPLETION_JSON_RESPONSE

    structure = {
        "temperature": {
            "selector": {"number": {"min": -50, "max": 100}},
            "description": "Current temperature",
        },
    }

    await hass.services.async_call(
        "ai_task",
        "generate_data",
        {
            "task_name": "test_task",
            "entity_id": "ai_task.github_copilot_client_github_copilot_ai_task",
            "instructions": "Get the temperature",
            "structure": structure,
        },
        blocking=True,
        return_response=True,
    )

    # Verify the exact structure message content and position
    call_args = mock_client.async_chat_completion.call_args
    messages = call_args.kwargs.get("messages") or call_args[1].get("messages", [])

    # Find all system messages
    system_messages = [m for m in messages if m.get("role") == "system"]
    assert len(system_messages) >= 2  # At least the prompt + structure message

    # The structure message should be the last system message (appended after history)
    structure_msg = system_messages[-1]
    assert structure_msg["content"].startswith(
        "Respond with valid JSON matching this structure."
    )
    assert "Do not include any other text." in structure_msg["content"]
    # The structure is serialized via str() — verify the key fields are present
    assert "temperature" in structure_msg["content"]


async def test_generate_data_invalid_json_structure(
    hass: HomeAssistant, setup_ai_task, mock_client
):
    """Test structured JSON with malformed JSON response."""

    mock_client.async_chat_completion.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "not valid json {"}}]
    }

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "ai_task",
            "generate_data",
            {
                "task_name": "test_task",
                "entity_id": "ai_task.github_copilot_client_github_copilot_ai_task",
                "instructions": "Get data",
                "structure": {
                    "value": {
                        "selector": {"text": {}},
                    },
                },
            },
            blocking=True,
            return_response=True,
        )
