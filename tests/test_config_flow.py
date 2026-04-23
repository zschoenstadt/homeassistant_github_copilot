"""Tests for the GitHub Copilot config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.github_copilot.api import (
    AuthError,
    ConnectionError,
    DeviceFlowResponse,
    Model,
    TokenResponse,
)
from custom_components.github_copilot.const import (
    CONF_ACCESS_TOKEN,
    CONF_MODEL,
    CONF_REFRESH_TOKEN,
    DEFAULT_MODEL,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def mock_get_clientsession():
    """Patch async_get_clientsession so the config flow gets a mock session."""

    with patch(
        "custom_components.github_copilot.config_flow.async_get_clientsession",
        return_value=AsyncMock(spec=aiohttp.ClientSession),
    ):
        yield


@pytest.fixture
def mock_device_flow():
    """Mock the device flow initiation."""

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotAuth.async_initiate_device_flow",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = DeviceFlowResponse(
            device_code="dc_test_123456",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            interval=0,
            expires_in=900,
        )
        yield mock


@pytest.fixture
def mock_poll_token():
    """Mock the token polling."""

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotAuth.async_poll_for_token",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = TokenResponse(
            access_token="gho_test_token_abc123",
            refresh_token="ghr_test_refresh_xyz789",
            token_type="bearer",
            scope="copilot",
            expires_in=28800,
        )
        yield mock


@pytest.fixture
def mock_list_models():
    """Mock the model listing."""

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotClient.async_list_models",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = [
            Model(id="gpt-4.1", name="GPT-4.1", capabilities=["streaming"]),
            Model(id="gpt-4.1-mini", name="GPT-4.1 Mini", capabilities=[]),
        ]
        yield mock


@pytest.fixture
def mock_validate_model():
    """Mock the model validation."""

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotClient.async_validate_model",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = True
        yield mock


# ── Full Flow Tests ──


async def test_full_flow_success(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
    mock_poll_token,
    mock_list_models,
    mock_validate_model,
):
    """Test the complete config flow: user → auth → model → entry."""

    # Step 1: User triggers setup
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Step 2: Submit user step → starts device flow → shows progress
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "auth"
    assert result["description_placeholders"]["user_code"] == "ABCD-1234"

    # Step 3: Task completes instantly → HA auto-transitions to model form
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "model"

    # Step 4: Select model → create entry
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_MODEL: "gpt-4.1"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "GitHub Copilot"
    assert result["data"][CONF_ACCESS_TOKEN] == "gho_test_token_abc123"
    assert result["data"][CONF_REFRESH_TOKEN] == "ghr_test_refresh_xyz789"
    assert result["data"][CONF_MODEL] == "gpt-4.1"


async def test_flow_user_step_shows_form(hass: HomeAssistant, setup_ha):
    """Test that the user step shows a form."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_flow_auth_step_shows_code(
    hass: HomeAssistant, setup_ha, mock_device_flow
):
    """Test that the auth step shows the device code in progress UI."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "auth"
    assert result["description_placeholders"]["user_code"] == "ABCD-1234"
    assert (
        result["description_placeholders"]["verification_uri"]
        == "https://github.com/login/device"
    )


async def test_flow_poll_timeout(
    hass: HomeAssistant, setup_ha, mock_device_flow, mock_poll_token
):
    """Test polling timeout aborts with auth_timeout reason."""

    mock_poll_token.side_effect = AuthError("Authorization timed out.")

    # Init → user form → submit → show progress
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "auth"

    # Re-entry — task failed with AuthError → auto-transitions to auth_failed → abort
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "auth_timeout"


async def test_flow_poll_denied(
    hass: HomeAssistant, setup_ha, mock_device_flow, mock_poll_token
):
    """Test user denies auth aborts with auth_denied reason."""

    mock_poll_token.side_effect = AuthError("Authorization was denied by the user.")

    # Init → user form → submit → show progress
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "auth"

    # Re-entry — task failed with denied → auto-transitions to auth_failed → abort
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "auth_denied"


async def test_flow_network_error(hass: HomeAssistant, setup_ha):
    """Test network failure during device flow."""

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotAuth.async_initiate_device_flow",
        new_callable=AsyncMock,
        side_effect=ConnectionError("Network error"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"]["base"] == "cannot_connect"


async def test_options_flow(
    hass: HomeAssistant, setup_ha, mock_config_entry, mock_client, mock_setup_entry
):
    """Test the options flow with model dropdown."""

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "model": "gpt-4.1-mini",
            "prompt": "Custom prompt",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["model"] == "gpt-4.1-mini"
    assert result["data"]["prompt"] == "Custom prompt"


async def test_options_flow_model_no_access(
    hass: HomeAssistant, setup_ha, mock_config_entry, mock_client, mock_setup_entry
):
    """Test options flow rejects model user can't access."""

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.async_validate_model.return_value = False

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    # Try to change to a model we can't access
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "model": "gpt-4.1-mini",
            "prompt": "Custom prompt",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "model_no_access"


async def test_flow_model_no_access(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
    mock_poll_token,
    mock_list_models,
    mock_validate_model,
):
    """Test selecting a model the user can't access shows error."""

    mock_validate_model.return_value = False

    # Navigate through auth progress steps
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "model"

    # Select a model we don't have access to
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_MODEL: "gpt-4.1"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "model"
    assert result["errors"]["base"] == "model_no_access"

    # Now allow access and retry
    mock_validate_model.return_value = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_MODEL: "gpt-4.1-mini"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODEL] == "gpt-4.1-mini"


# ── Reauth Flow Tests ──


async def test_reauth_flow_success(
    hass: HomeAssistant,
    setup_ha,
    mock_config_entry,
    mock_client,
    mock_device_flow,
    mock_poll_token,
):
    """Test successful reauth flow."""

    mock_config_entry.runtime_data = mock_client
    with patch(
        "custom_components.github_copilot.GitHubCopilotClient",
        return_value=mock_client,
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    # Confirm reauth → triggers device flow → show progress
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "reauth_auth"
    assert result["description_placeholders"]["user_code"] == "ABCD-1234"

    # Task completes instantly → auto-transitions to reauth_complete → abort
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_reauth_flow_connection_error(
    hass: HomeAssistant,
    setup_ha,
    mock_config_entry,
    mock_client,
):
    """Test reauth with connection error during device flow."""

    mock_config_entry.runtime_data = mock_client
    with patch(
        "custom_components.github_copilot.GitHubCopilotClient",
        return_value=mock_client,
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await mock_config_entry.start_reauth_flow(hass)

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotAuth.async_initiate_device_flow",
        new_callable=AsyncMock,
        side_effect=ConnectionError("Network error"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reauth_flow_denied(
    hass: HomeAssistant,
    setup_ha,
    mock_config_entry,
    mock_client,
    mock_device_flow,
    mock_poll_token,
):
    """Test reauth when user denies authorization."""

    mock_poll_token.side_effect = AuthError("Authorization was denied by the user.")

    mock_config_entry.runtime_data = mock_client
    with patch(
        "custom_components.github_copilot.GitHubCopilotClient",
        return_value=mock_client,
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await mock_config_entry.start_reauth_flow(hass)

    # Confirm reauth → show progress
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "reauth_auth"

    # Task failed with denied → auto-transitions to auth_failed → abort
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "auth_denied"


# ── Model Fetch Fallback Test ──


async def test_flow_model_fetch_failure(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
    mock_poll_token,
    mock_validate_model,
):
    """Test model fetch failure falls back to default model."""

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotClient.async_list_models",
        new_callable=AsyncMock,
        side_effect=Exception("API unreachable"),
    ):
        # Navigate through auth progress steps
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["type"] == FlowResultType.SHOW_PROGRESS

        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "model"

        # Select the default model (which is the fallback)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_MODEL: DEFAULT_MODEL}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
