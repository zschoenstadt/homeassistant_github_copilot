"""Tests for the GitHub Copilot conversation entity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from copilot.generated.session_events import SessionEventType
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
import pytest

from custom_components.github_copilot.conversation import (
    GitHubCopilotConversationEntity,
)


def _make_mock_sdk_client_with_session(response_text="Hello! How can I help?"):
    """Create a mock SDK client whose sessions emit events via on_event callback.

    The entity passes on_event to async_get_or_create_session, so we capture
    it there and fire events through it when session.send() is called.
    """

    captured_on_event = None

    def make_session():
        session = AsyncMock()

        async def send(prompt, **kwargs):
            """Simulate sending and trigger events via on_event."""

            if captured_on_event is None:
                return

            # Emit assistant message delta
            delta_event = MagicMock()
            delta_event.type = SessionEventType.ASSISTANT_MESSAGE_DELTA
            delta_event.data.delta_content = response_text
            captured_on_event(delta_event)

            # Emit final assistant message
            final_event = MagicMock()
            final_event.type = SessionEventType.ASSISTANT_MESSAGE
            final_event.data.content = response_text
            final_event.data.tool_requests = None
            captured_on_event(final_event)

            # Emit session idle
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
async def setup_conversation(
    hass: HomeAssistant,
    mock_config_entry,
    mock_runtime,
    mock_sdk_client,
    mock_auth,
    setup_ha,
):
    """Set up the conversation entity for testing."""

    mock_config_entry.runtime_data = mock_runtime

    # Wire SDK client to emit events via on_event callback
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
    assert entry.state is ConfigEntryState.LOADED


async def test_handle_message_success(
    hass: HomeAssistant, setup_conversation, mock_sdk_client
):
    """Test successful message handling via SDK session."""

    result = await _converse(hass, "Hello, turn on the lights", setup_conversation)

    assert result.response.speech is not None
    assert "Hello" in result.response.speech["plain"]["speech"]


async def test_handle_message_error(
    hass: HomeAssistant, setup_conversation, mock_sdk_client
):
    """Test message handling when SDK session raises an error."""

    def make_error_create_session():
        captured_on_event = None

        async def create_session(**kwargs):
            nonlocal captured_on_event
            captured_on_event = kwargs.get("on_event")

            session = AsyncMock()

            async def send(prompt, **kwargs):
                if captured_on_event:
                    error_event = MagicMock()
                    error_event.type = SessionEventType.SESSION_ERROR
                    error_event.data.message = "Something went wrong"
                    captured_on_event(error_event)

                    idle_event = MagicMock()
                    idle_event.type = SessionEventType.SESSION_IDLE
                    captured_on_event(idle_event)

            session.send = send
            session.disconnect = AsyncMock()
            return session

        return create_session

    mock_sdk_client.async_get_or_create_session = make_error_create_session()

    result = await _converse(hass, "Hello", setup_conversation)

    assert result.response.speech is not None
    speech = result.response.speech["plain"]["speech"]
    assert "Error" in speech


async def test_supported_languages(hass: HomeAssistant, setup_conversation):
    """Test that the conversation entity supports all languages."""

    entry = setup_conversation
    assert entry.state is ConfigEntryState.LOADED

    component = hass.data.get("entity_components", {}).get("conversation")
    if component:
        entities = [
            e
            for e in component.entities
            if isinstance(e, GitHubCopilotConversationEntity)
        ]
        assert len(entities) == 1
        assert entities[0].supported_languages == MATCH_ALL


async def test_conversation_feature_control_flag(
    hass: HomeAssistant,
    mock_config_entry,
    mock_runtime,
    mock_sdk_client,
    mock_auth,
    setup_ha,
):
    """Test that CONTROL feature flag is set when LLM API is configured."""

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
            return_value=mock_auth,
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotSDKClient",
            return_value=mock_sdk_client,
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


async def test_streaming_support_enabled(hass: HomeAssistant, setup_conversation):
    """Test that the conversation entity supports streaming."""

    component = hass.data.get("entity_components", {}).get("conversation")
    if component:
        entities = [
            e
            for e in component.entities
            if isinstance(e, GitHubCopilotConversationEntity)
        ]
        assert len(entities) == 1
        assert entities[0]._attr_supports_streaming is True
