"""Tests for GitHub Copilot integration setup."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.github_copilot.api import GitHubCopilotClient


async def test_async_setup_entry(
    hass: HomeAssistant, mock_config_entry, mock_client, setup_ha
):
    """Test successful setup of a config entry."""

    with patch(
        "custom_components.github_copilot.GitHubCopilotClient",
        return_value=mock_client,
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.LOADED


async def test_async_unload_entry(
    hass: HomeAssistant, mock_config_entry, mock_client, setup_ha
):
    """Test unloading a config entry closes the client session."""

    mock_config_entry.runtime_data = mock_client

    with patch(
        "custom_components.github_copilot.GitHubCopilotClient",
        return_value=mock_client,
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state == ConfigEntryState.NOT_LOADED
    mock_client.async_close.assert_called_once()


async def test_setup_entry_auth_failed(
    hass: HomeAssistant, mock_config_entry, mock_client, setup_ha
):
    """Test setup with invalid token raises auth failed."""

    mock_client.async_validate_token.side_effect = GitHubCopilotClient.AuthError(
        "Token invalid"
    )

    with patch(
        "custom_components.github_copilot.GitHubCopilotClient",
        return_value=mock_client,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_ERROR


async def test_setup_entry_not_ready(
    hass: HomeAssistant, mock_config_entry, mock_client, setup_ha
):
    """Test setup when API is unreachable."""

    mock_client.async_validate_token.side_effect = GitHubCopilotClient.ConnectionError(
        "Cannot connect"
    )

    with patch(
        "custom_components.github_copilot.GitHubCopilotClient",
        return_value=mock_client,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_RETRY
