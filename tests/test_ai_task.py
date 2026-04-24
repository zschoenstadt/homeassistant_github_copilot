"""Tests for the GitHub Copilot AI Task entity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from copilot.generated.session_events import SessionEventType
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest


def _make_mock_sdk_client_with_session(response_text="Hello! How can I help?"):
    """Create a mock create_session that captures on_event and fires events."""

    captured_on_event = None

    def make_session():
        session = AsyncMock()

        async def send(prompt, **kwargs):
            if captured_on_event is None:
                return

            delta_event = MagicMock()
            delta_event.type = SessionEventType.ASSISTANT_MESSAGE_DELTA
            delta_event.data.delta_content = response_text
            captured_on_event(delta_event)

            final_event = MagicMock()
            final_event.type = SessionEventType.ASSISTANT_MESSAGE
            final_event.data.content = response_text
            final_event.data.tool_requests = None
            captured_on_event(final_event)

            idle_event = MagicMock()
            idle_event.type = SessionEventType.SESSION_IDLE
            captured_on_event(idle_event)

        session.send = send
        session.disconnect = AsyncMock()
        return session

    async def mock_get_or_create_session(**kwargs):
        nonlocal captured_on_event
        captured_on_event = kwargs.get("on_event")
        return make_session()

    return mock_get_or_create_session


@pytest.fixture
async def setup_ai_task(
    hass: HomeAssistant,
    mock_config_entry,
    mock_runtime,
    mock_sdk_client,
    mock_auth,
    setup_ha,
):
    """Set up the AI task entity for testing."""

    mock_config_entry.runtime_data = mock_runtime

    mock_sdk_client.async_get_or_create_session = _make_mock_sdk_client_with_session()

    with (
        patch(
            "custom_components.github_copilot.Runtime",
            return_value=mock_runtime,
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotAuth",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotSDKClient",
            return_value=mock_sdk_client,
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
    hass: HomeAssistant, setup_ai_task, mock_sdk_client
):
    """Test generating plain text data."""

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


async def test_generate_data_sdk_error(
    hass: HomeAssistant, setup_ai_task, mock_sdk_client
):
    """Test AI task with SDK session error."""

    async def failing_create_session(**kwargs):
        raise RuntimeError("SDK error")

    mock_sdk_client.async_get_or_create_session = failing_create_session

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


async def test_generate_data_structured_json(
    hass: HomeAssistant, setup_ai_task, mock_sdk_client
):
    """Test generating structured JSON data."""

    # Set up a session that returns JSON
    mock_sdk_client.async_get_or_create_session = _make_mock_sdk_client_with_session(
        '{"temperature": 22, "lights_on": true}'
    )

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
    assert "data" in result


async def test_generate_data_invalid_json_structure(
    hass: HomeAssistant, setup_ai_task, mock_sdk_client
):
    """Test structured JSON with malformed JSON response."""

    # Session returns non-JSON text when JSON is expected
    mock_sdk_client.async_get_or_create_session = _make_mock_sdk_client_with_session(
        "not valid json {"
    )

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
