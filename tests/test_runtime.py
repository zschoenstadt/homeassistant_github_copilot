"""Tests for the GitHub Copilot Runtime class."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.github_copilot.api import (
    GitHubCopilotAuth,
    GitHubCopilotAuthError,
    GitHubCopilotSDKClient,
)
from custom_components.github_copilot.const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRY,
)
from custom_components.github_copilot.runtime import Runtime


@pytest.fixture
def local_auth():
    """Create a mock GitHubCopilotAuth."""

    auth = AsyncMock(spec=GitHubCopilotAuth)
    auth.access_token = "gho_test_token_abc123"
    auth.refresh_token = "ghr_test_refresh_xyz789"
    auth.async_refresh_token = AsyncMock()
    return auth


@pytest.fixture
def local_sdk_client():
    """Create a mock GitHubCopilotSDKClient."""

    client = AsyncMock(spec=GitHubCopilotSDKClient)
    client.async_start = AsyncMock()
    client.async_stop = AsyncMock()
    client.async_check_auth = AsyncMock(return_value=True)
    client.async_restart = AsyncMock()
    return client


@pytest.fixture
def runtime(hass, mock_config_entry, local_auth, local_sdk_client):
    """Create a Runtime instance with mock dependencies."""

    return Runtime(
        hass=hass,
        entry=mock_config_entry,
        auth=local_auth,
        sdk_client=local_sdk_client,
    )


# ── Auth Validation Tests ──


async def test_validate_auth_success(runtime, local_sdk_client):
    """Test successful auth validation."""

    await runtime.async_validate_auth()

    local_sdk_client.async_check_auth.assert_called_once()


async def test_validate_auth_refresh_on_failure(runtime, local_auth, local_sdk_client):
    """Test auth validation triggers refresh when initial check fails."""

    # First check returns False, after refresh returns True
    local_sdk_client.async_check_auth.side_effect = [False, True]

    # Make the mock actually call the callback to trigger restart
    async def fake_refresh(callback):
        await callback("new_token", "new_refresh", 9999)

    local_auth.async_refresh_token.side_effect = fake_refresh

    await runtime.async_validate_auth()

    local_auth.async_refresh_token.assert_called_once()
    local_sdk_client.async_restart.assert_called_once()


async def test_validate_auth_refresh_still_fails(runtime, local_auth, local_sdk_client):
    """Test auth validation raises when refresh doesn't help."""

    # Always returns False
    local_sdk_client.async_check_auth.return_value = False

    with pytest.raises(GitHubCopilotAuthError, match="after refresh"):
        await runtime.async_validate_auth()


async def test_validate_auth_refresh_token_error(runtime, local_auth, local_sdk_client):
    """Test auth validation propagates refresh token errors."""

    local_sdk_client.async_check_auth.return_value = False
    local_auth.async_refresh_token.side_effect = GitHubCopilotAuthError(
        "Refresh token revoked"
    )

    with pytest.raises(GitHubCopilotAuthError, match="Refresh token revoked"):
        await runtime.async_validate_auth()


# ── Token Persistence Tests ──


async def test_update_tokens_persists_data(
    hass: HomeAssistant, mock_config_entry, local_auth, local_sdk_client
):
    """Test that _async_update_tokens updates the config entry data."""

    runtime = Runtime(
        hass=hass,
        entry=mock_config_entry,
        auth=local_auth,
        sdk_client=local_sdk_client,
    )

    await runtime._async_update_tokens(
        access_token="new_access_token",
        refresh_token="new_refresh_token",
        expiry=1234567890,
    )

    assert mock_config_entry.data[CONF_ACCESS_TOKEN] == "new_access_token"
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] == "new_refresh_token"
    assert mock_config_entry.data[CONF_TOKEN_EXPIRY] == 1234567890

    # SDK subprocess should be restarted to pick up the new token
    local_sdk_client.async_restart.assert_called_once()


async def test_update_tokens_partial(
    hass: HomeAssistant, mock_config_entry, local_auth, local_sdk_client
):
    """Test that _async_update_tokens handles None refresh_token and expiry."""

    runtime = Runtime(
        hass=hass,
        entry=mock_config_entry,
        auth=local_auth,
        sdk_client=local_sdk_client,
    )

    original_refresh = mock_config_entry.data[CONF_REFRESH_TOKEN]
    original_expiry = mock_config_entry.data[CONF_TOKEN_EXPIRY]

    await runtime._async_update_tokens(
        access_token="new_access_token",
        refresh_token=None,
        expiry=None,
    )

    # Access token updated, others unchanged
    assert mock_config_entry.data[CONF_ACCESS_TOKEN] == "new_access_token"
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] == original_refresh
    assert mock_config_entry.data[CONF_TOKEN_EXPIRY] == original_expiry


async def test_validate_auth_refresh_invokes_callback(
    hass: HomeAssistant, mock_config_entry, local_auth, local_sdk_client
):
    """Test that token refresh during validation persists the new tokens."""

    # First check fails, second succeeds after refresh
    local_sdk_client.async_check_auth.side_effect = [False, True]

    # Make async_refresh_token call the callback with new values
    async def fake_refresh(callback):
        await callback("new_access", "new_refresh", 9999999)

    local_auth.async_refresh_token.side_effect = fake_refresh

    runtime = Runtime(
        hass=hass,
        entry=mock_config_entry,
        auth=local_auth,
        sdk_client=local_sdk_client,
    )
    await runtime.async_validate_auth()

    assert mock_config_entry.data[CONF_ACCESS_TOKEN] == "new_access"
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] == "new_refresh"
    assert mock_config_entry.data[CONF_TOKEN_EXPIRY] == 9999999
