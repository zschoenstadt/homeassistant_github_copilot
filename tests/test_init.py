"""Tests for GitHub Copilot integration setup."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.github_copilot.api import (
    GitHubCopilotAuthError,
    GitHubCopilotConnectionError,
)


async def test_async_setup_entry(
    hass: HomeAssistant,
    mock_config_entry,
    mock_runtime,
    mock_sdk_client,
    mock_auth,
    setup_ha,
):
    """Test successful setup of a config entry."""

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

    assert mock_config_entry.state == ConfigEntryState.LOADED


async def test_async_unload_entry(
    hass: HomeAssistant,
    mock_config_entry,
    mock_runtime,
    mock_sdk_client,
    mock_auth,
    setup_ha,
):
    """Test unloading a config entry stops the SDK client."""

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

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state == ConfigEntryState.NOT_LOADED


async def test_setup_entry_auth_failed(
    hass: HomeAssistant,
    mock_config_entry,
    mock_runtime,
    mock_sdk_client,
    mock_auth,
    setup_ha,
):
    """Test setup with invalid token raises auth failed."""

    mock_runtime.async_validate_auth = AsyncMock(
        side_effect=GitHubCopilotAuthError("Token invalid")
    )

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
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_ERROR


async def test_setup_entry_not_ready(
    hass: HomeAssistant,
    mock_config_entry,
    mock_runtime,
    mock_sdk_client,
    mock_auth,
    setup_ha,
):
    """Test setup when SDK client cannot connect."""

    mock_runtime.async_validate_auth = AsyncMock(
        side_effect=GitHubCopilotConnectionError("Cannot connect")
    )

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
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_RETRY
