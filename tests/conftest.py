"""Fixtures for GitHub Copilot integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.github_copilot.api import (
    GitHubCopilotAuth,
    GitHubCopilotModel,
    GitHubCopilotSDKClient,
)
from custom_components.github_copilot.const import (
    CONF_ACCESS_TOKEN,
    CONF_MODEL,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRY,
    DEFAULT_MODEL,
    DOMAIN,
)
from custom_components.github_copilot.runtime import Runtime

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""

    return


@pytest.fixture
async def setup_ha(hass):
    """Set up the homeassistant component so conversation works."""

    assert await async_setup_component(hass, "homeassistant", {})
    await hass.async_block_till_done()
    return hass


# ── Mock API Response Data ──

MOCK_DEVICE_FLOW_RESPONSE = {
    "device_code": "dc_test_123456",
    "user_code": "ABCD-1234",
    "verification_uri": "https://github.com/login/device",
    "interval": 5,
    "expires_in": 900,
}

MOCK_TOKEN_RESPONSE = {
    "access_token": "gho_test_token_abc123",
    "refresh_token": "ghr_test_refresh_xyz789",
    "token_type": "bearer",
    "scope": "copilot",
    "expires_in": 28800,
}

MOCK_TOKEN_PENDING_RESPONSE = {
    "error": "authorization_pending",
    "error_description": "The authorization request is still pending.",
}

MOCK_TOKEN_SLOW_DOWN_RESPONSE = {
    "error": "slow_down",
    "interval": 10,
}

MOCK_TOKEN_EXPIRED_RESPONSE = {
    "error": "expired_token",
    "error_description": "The device code has expired.",
}

MOCK_TOKEN_DENIED_RESPONSE = {
    "error": "access_denied",
    "error_description": "The user denied the authorization request.",
}

MOCK_MODELS = [
    GitHubCopilotModel(id="gpt-4.1", name="GPT-4.1"),
    GitHubCopilotModel(id="gpt-4.1-mini", name="GPT-4.1 Mini"),
]


# ── Fixtures ──


@pytest.fixture
def mock_config_entry(hass):
    """Create a mock config entry with valid test tokens."""

    expiry = (datetime.now() + timedelta(hours=8)).isoformat()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_user_123",
        title="GitHub Copilot Client",
        data={
            CONF_ACCESS_TOKEN: "gho_test_token_abc123",
            CONF_REFRESH_TOKEN: "ghr_test_refresh_xyz789",
            CONF_TOKEN_EXPIRY: expiry,
        },
        options={
            CONF_MODEL: DEFAULT_MODEL,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_sdk_client():
    """Create a mock GitHubCopilotSDKClient."""

    client = AsyncMock(spec=GitHubCopilotSDKClient)
    client.async_start = AsyncMock()
    client.async_stop = AsyncMock()
    client.async_check_auth = AsyncMock(return_value=True)
    client.async_list_models = AsyncMock(return_value=MOCK_MODELS)
    client.async_validate_model = AsyncMock(return_value=True)
    client.update_token = MagicMock()
    return client


@pytest.fixture
def mock_auth():
    """Create a mock GitHubCopilotAuth."""

    auth = AsyncMock(spec=GitHubCopilotAuth)
    auth.access_token = "gho_test_token_abc123"
    auth.refresh_token = "ghr_test_refresh_xyz789"
    auth.async_refresh_token = AsyncMock()
    return auth


@pytest.fixture
def mock_runtime(hass, mock_config_entry, mock_sdk_client, mock_auth):
    """Create a mock Runtime wrapping the mock SDK client."""

    runtime = MagicMock(spec=Runtime)
    runtime.hass = hass
    runtime.entry = mock_config_entry
    runtime.sdk_client = mock_sdk_client
    runtime.auth = mock_auth
    runtime.async_validate_auth = AsyncMock()
    runtime._async_update_tokens = AsyncMock()
    return runtime


@pytest.fixture
def mock_setup_entry(mock_runtime, mock_sdk_client, mock_auth):
    """Mock the integration setup to inject mock runtime as runtime_data."""

    with (
        patch(
            "custom_components.github_copilot.Runtime",
        ) as mock_runtime_cls,
        patch(
            "custom_components.github_copilot.GitHubCopilotAuth",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.github_copilot.GitHubCopilotSDKClient",
            return_value=mock_sdk_client,
        ),
    ):
        mock_runtime_cls.return_value = mock_runtime
        yield mock_runtime_cls
