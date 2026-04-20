"""Fixtures for GitHub Copilot integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.github_copilot.api import GitHubCopilotClient, Model
from custom_components.github_copilot.const import (
    CONF_ACCESS_TOKEN,
    CONF_MODEL,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRY,
    DEFAULT_MODEL,
    DOMAIN,
)

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

MOCK_MODELS_RESPONSE = [
    {
        "id": "gpt-4.1",
        "name": "GPT-4.1",
        "capabilities": ["streaming", "tool-calling"],
    },
    {
        "id": "gpt-4.1-mini",
        "name": "GPT-4.1 Mini",
        "capabilities": ["streaming"],
    },
    {
        "id": "anthropic/claude-sonnet-4",
        "name": "Claude Sonnet 4",
        "capabilities": ["streaming"],
    },
]

# Copilot models endpoint wraps in {"object": "list", "data": [...]}
MOCK_COPILOT_MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {
            "id": "gpt-4.1",
            "object": "model",
            "created": 0,
            "owned_by": "openai",
            "display_name": "GPT-4.1",
        },
        {
            "id": "gpt-4.1-mini",
            "object": "model",
            "created": 0,
            "owned_by": "openai",
            "display_name": "GPT-4.1 Mini",
        },
        {
            "id": "claude-sonnet-4",
            "object": "model",
            "created": 0,
            "owned_by": "anthropic",
            "display_name": "Claude Sonnet 4",
        },
    ],
}

MOCK_COPILOT_TOKEN_RESPONSE = {
    "token": "tid=copilot_test_token_abc123;exp=9999999999;sku=monthly",
    "expires_at": int((datetime.now() + timedelta(hours=1)).timestamp()),
}

MOCK_CHAT_COMPLETION_RESPONSE = {
    "id": "chatcmpl-test123",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Hello! How can I help you with your smart home?",
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22},
}

MOCK_CHAT_COMPLETION_JSON_RESPONSE = {
    "id": "chatcmpl-test456",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": '{"temperature": 22, "lights_on": true}',
            },
            "finish_reason": "stop",
        }
    ],
}

MOCK_STREAMING_CHUNKS = [
    b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"!"}}]}\n\n',
    b"data: [DONE]\n\n",
]


# ── Fixtures ──


@pytest.fixture
def mock_config_entry(hass):
    """Create a mock config entry with valid test tokens."""

    expiry = (datetime.now() + timedelta(hours=8)).isoformat()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_user_123",
        title="GitHub Copilot",
        data={
            CONF_ACCESS_TOKEN: "gho_test_token_abc123",
            CONF_REFRESH_TOKEN: "ghr_test_refresh_xyz789",
            CONF_TOKEN_EXPIRY: expiry,
            CONF_MODEL: DEFAULT_MODEL,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_client():
    """Create a mock GitHubCopilotClient."""

    client = AsyncMock(spec=GitHubCopilotClient)
    client.access_token = "gho_test_token_abc123"
    client._access_token = "gho_test_token_abc123"
    client.async_validate_token = AsyncMock(return_value=True)
    client.async_validate_model = AsyncMock(return_value=True)
    client.async_list_models = AsyncMock(
        return_value=[
            Model(id="gpt-4.1", name="GPT-4.1", capabilities=["streaming"]),
            Model(
                id="gpt-4.1-mini",
                name="GPT-4.1 Mini",
                capabilities=["streaming"],
            ),
        ]
    )
    client.async_chat_completion = AsyncMock(return_value=MOCK_CHAT_COMPLETION_RESPONSE)
    client.async_refresh_token = AsyncMock()
    client.async_close = AsyncMock()
    return client


@pytest.fixture
def mock_setup_entry(mock_client):
    """Mock the integration setup to inject mock client."""

    with patch(
        "custom_components.github_copilot.GitHubCopilotClient",
        return_value=mock_client,
    ) as mock_cls:
        mock_cls.return_value = mock_client
        mock_cls.AuthError = GitHubCopilotClient.AuthError
        mock_cls.ConnectionError = GitHubCopilotClient.ConnectionError
        mock_cls.async_initiate_device_flow = AsyncMock()
        mock_cls.async_poll_for_token = AsyncMock()
        yield mock_cls
