"""Tests for the GitHub Copilot Runtime class."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.github_copilot.api import (
    GitHubCopilotAuth,
    GitHubCopilotAuthError,
    GitHubCopilotClient,
)
from custom_components.github_copilot.const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRY,
)
from custom_components.github_copilot.runtime import Runtime

from .conftest import MOCK_CHAT_COMPLETION_RESPONSE


@pytest.fixture
def mock_auth():
    """Create a mock GitHubCopilotAuth."""

    auth = AsyncMock(spec=GitHubCopilotAuth)
    auth.async_ensure_copilot_token = AsyncMock(return_value="copilot_test_token")
    auth.async_refresh_token = AsyncMock()
    return auth


@pytest.fixture
def mock_ghc(mock_auth):
    """Create a mock GitHubCopilotClient with the mock auth."""

    client = AsyncMock(spec=GitHubCopilotClient)
    client.auth = mock_auth
    client.async_chat_completion = AsyncMock(return_value=MOCK_CHAT_COMPLETION_RESPONSE)
    return client


@pytest.fixture
def runtime(hass, mock_config_entry, mock_ghc):
    """Create a Runtime instance with mock dependencies."""

    return Runtime(hass=hass, entry=mock_config_entry, ghc=mock_ghc)


# ── Token Validation Tests ──


async def test_validate_tokens_success(runtime, mock_auth):
    """Test successful token validation."""

    await runtime.async_validate_tokens()

    mock_auth.async_ensure_copilot_token.assert_called_once()


async def test_validate_tokens_refresh_on_auth_error(runtime, mock_auth):
    """Test token validation retries after refresh on auth error."""

    # First call fails, second (after refresh) succeeds
    mock_auth.async_ensure_copilot_token.side_effect = [
        GitHubCopilotAuthError("Token expired"),
        "new_copilot_token",
    ]

    await runtime.async_validate_tokens()

    mock_auth.async_refresh_token.assert_called_once()
    assert mock_auth.async_ensure_copilot_token.call_count == 2


async def test_validate_tokens_refresh_fails(runtime, mock_auth):
    """Test token validation raises when refresh also fails."""

    mock_auth.async_ensure_copilot_token.side_effect = GitHubCopilotAuthError(
        "Token expired"
    )
    mock_auth.async_refresh_token.side_effect = GitHubCopilotAuthError(
        "Refresh token revoked"
    )

    with pytest.raises(GitHubCopilotAuthError, match="Refresh token revoked"):
        await runtime.async_validate_tokens()


# ── Chat Completion Tests ──


async def test_chat_completion_success(runtime, mock_ghc):
    """Test successful chat completion."""

    result = await runtime.async_chat_completion(
        messages=[{"role": "user", "content": "Hello"}],
        model="gpt-4.1",
    )

    assert result == MOCK_CHAT_COMPLETION_RESPONSE
    mock_ghc.async_chat_completion.assert_called_once()


async def test_chat_completion_refresh_on_auth_error(runtime, mock_ghc, mock_auth):
    """Test chat completion retries after refresh on auth error."""

    mock_ghc.async_chat_completion.side_effect = [
        GitHubCopilotAuthError("Token expired"),
        MOCK_CHAT_COMPLETION_RESPONSE,
    ]

    result = await runtime.async_chat_completion(
        messages=[{"role": "user", "content": "Hello"}],
        model="gpt-4.1",
    )

    assert result == MOCK_CHAT_COMPLETION_RESPONSE
    mock_auth.async_refresh_token.assert_called_once()
    assert mock_ghc.async_chat_completion.call_count == 2


async def test_chat_completion_refresh_fails(runtime, mock_ghc, mock_auth):
    """Test chat completion raises when refresh also fails."""

    mock_ghc.async_chat_completion.side_effect = GitHubCopilotAuthError("Token expired")
    mock_auth.async_refresh_token.side_effect = GitHubCopilotAuthError(
        "Refresh token revoked"
    )

    with pytest.raises(GitHubCopilotAuthError, match="Refresh token revoked"):
        await runtime.async_chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
            model="gpt-4.1",
        )


# ── Token Persistence Tests ──


async def test_validate_tokens_refresh_invokes_callback(
    hass: HomeAssistant, mock_config_entry, mock_auth
):
    """Test that token validation refresh actually invokes the persistence callback."""

    # First ensure_copilot_token fails, second succeeds
    mock_auth.async_ensure_copilot_token.side_effect = [
        GitHubCopilotAuthError("Token expired"),
        "new_copilot_token",
    ]

    # Make async_refresh_token actually call the callback with test values
    async def fake_refresh(callback):
        await callback("new_access", "new_refresh", 9999999)

    mock_auth.async_refresh_token.side_effect = fake_refresh

    mock_ghc = AsyncMock(spec=GitHubCopilotClient)
    mock_ghc.auth = mock_auth

    runtime = Runtime(hass=hass, entry=mock_config_entry, ghc=mock_ghc)
    await runtime.async_validate_tokens()

    # Verify the callback persisted the new tokens to the config entry
    assert mock_config_entry.data[CONF_ACCESS_TOKEN] == "new_access"
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] == "new_refresh"
    assert mock_config_entry.data[CONF_TOKEN_EXPIRY] == 9999999


async def test_chat_completion_refresh_invokes_callback(
    hass: HomeAssistant, mock_config_entry, mock_auth
):
    """Test that chat completion refresh actually invokes the persistence callback."""

    mock_ghc = AsyncMock(spec=GitHubCopilotClient)
    mock_ghc.auth = mock_auth
    mock_ghc.async_chat_completion.side_effect = [
        GitHubCopilotAuthError("Token expired"),
        MOCK_CHAT_COMPLETION_RESPONSE,
    ]

    # Make async_refresh_token actually call the callback with test values
    async def fake_refresh(callback):
        await callback("refreshed_access", "refreshed_refresh", 8888888)

    mock_auth.async_refresh_token.side_effect = fake_refresh

    runtime = Runtime(hass=hass, entry=mock_config_entry, ghc=mock_ghc)
    result = await runtime.async_chat_completion(
        messages=[{"role": "user", "content": "Hello"}],
        model="gpt-4.1",
    )

    assert result == MOCK_CHAT_COMPLETION_RESPONSE
    assert mock_config_entry.data[CONF_ACCESS_TOKEN] == "refreshed_access"
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] == "refreshed_refresh"
    assert mock_config_entry.data[CONF_TOKEN_EXPIRY] == 8888888


async def test_update_tokens_persists_data(
    hass: HomeAssistant, mock_config_entry, mock_ghc
):
    """Test that _async_update_tokens updates the config entry data."""

    runtime = Runtime(hass=hass, entry=mock_config_entry, ghc=mock_ghc)

    await runtime._async_update_tokens(
        access_token="new_access_token",
        refresh_token="new_refresh_token",
        expiry=1234567890,
    )

    assert mock_config_entry.data[CONF_ACCESS_TOKEN] == "new_access_token"
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] == "new_refresh_token"
    assert mock_config_entry.data[CONF_TOKEN_EXPIRY] == 1234567890


async def test_update_tokens_partial(hass: HomeAssistant, mock_config_entry, mock_ghc):
    """Test that _async_update_tokens handles None refresh_token and expiry."""

    runtime = Runtime(hass=hass, entry=mock_config_entry, ghc=mock_ghc)

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
