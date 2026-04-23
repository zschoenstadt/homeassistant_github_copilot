"""Tests for the GitHub Copilot config flow."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.github_copilot.api import (
    GitHubCopilotAuth,
    GitHubCopilotAuthError,
    GitHubCopilotClient,
    GitHubCopilotConnectionError,
    GitHubCopilotDeviceFlow,
)
from custom_components.github_copilot.const import (
    CONF_ACCESS_TOKEN,
    CONF_MODEL,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

from .conftest import MOCK_MODELS


@pytest.fixture(autouse=True)
def mock_get_clientsession():
    """Patch async_get_clientsession so the config flow gets a mock session."""

    with patch(
        "custom_components.github_copilot.config_flow.async_get_clientsession",
        return_value=AsyncMock(spec=aiohttp.ClientSession),
    ):
        yield


def _make_mock_device_flow(
    *,
    activation_result=None,
    activation_side_effect=None,
):
    """Build a mock GitHubCopilotDeviceFlow instance."""

    mock_flow = AsyncMock(spec=GitHubCopilotDeviceFlow)
    mock_flow.user_code = "ABCD-1234"
    mock_flow.verification_url = "https://github.com/login/device"

    if activation_side_effect is not None:
        mock_flow.async_device_activation = AsyncMock(
            side_effect=activation_side_effect
        )
    else:
        # Default: return a mock auth that completes successfully
        if activation_result is None:
            mock_auth = AsyncMock(spec=GitHubCopilotAuth)
            mock_auth.session = AsyncMock(spec=aiohttp.ClientSession)
            mock_auth.access_token = "gho_test_token_abc123"
            mock_auth.refresh_token = "ghr_test_refresh_xyz789"
            mock_auth.expiry = 9999999999
            activation_result = mock_auth
        mock_flow.async_device_activation = AsyncMock(return_value=activation_result)

    return mock_flow


@pytest.fixture
def mock_device_flow():
    """Mock the device flow initiation to return a mock DeviceFlow."""

    mock_flow = _make_mock_device_flow()

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotDeviceFlow.async_initiate",
        new_callable=AsyncMock,
        return_value=mock_flow,
    ) as mock_initiate:
        mock_initiate._mock_flow = mock_flow
        yield mock_initiate


@pytest.fixture
def mock_ghc_client():
    """Mock GitHubCopilotClient constructed during config flow."""

    mock_client = AsyncMock(spec=GitHubCopilotClient)
    mock_client.async_validate_model = AsyncMock(return_value=True)
    mock_client.async_list_models = AsyncMock(return_value=MOCK_MODELS)

    # Mock auth on the client
    mock_auth = AsyncMock(spec=GitHubCopilotAuth)
    mock_auth.access_token = "gho_test_token_abc123"
    mock_auth.refresh_token = "ghr_test_refresh_xyz789"
    mock_auth.expiry = 9999999999
    mock_client.auth = mock_auth

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotClient",
        return_value=mock_client,
    ):
        yield mock_client


# ── Full Flow Tests ──


async def test_full_flow_success(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
    mock_ghc_client,
):
    """Test the complete config flow: user → progress_done → model → entry."""

    # Step 1: Init triggers device flow. With mocked async the task completes
    # immediately so we get SHOW_PROGRESS_DONE with next_step_id="model".
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE
    assert result["step_id"] == "model"

    # Step 2: Advance past progress_done → model form
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "model"

    # Step 3: Select model → create entry
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_MODEL: "gpt-4.1"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "GitHub Copilot Client"
    assert result["data"][CONF_ACCESS_TOKEN] == "gho_test_token_abc123"
    assert result["data"][CONF_REFRESH_TOKEN] == "ghr_test_refresh_xyz789"
    assert result["options"][CONF_MODEL] == "gpt-4.1"


async def test_flow_user_step_shows_progress_done(
    hass: HomeAssistant, setup_ha, mock_device_flow, mock_ghc_client
):
    """Test that the user step shows progress_done when task completes instantly."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # With mocked async, task completes immediately → SHOW_PROGRESS_DONE
    assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE
    assert result["step_id"] == "model"


async def test_flow_activation_timeout(hass: HomeAssistant, setup_ha, mock_ghc_client):
    """Test device activation connection error → login_timeout step."""

    mock_flow = _make_mock_device_flow(
        activation_side_effect=GitHubCopilotConnectionError("Connection timed out"),
    )

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotDeviceFlow.async_initiate",
        new_callable=AsyncMock,
        return_value=mock_flow,
    ):
        # Init → task fails immediately → SHOW_PROGRESS_DONE(login_timeout)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE
        assert result["step_id"] == "login_timeout"

        # Advance past progress_done → login_timeout form
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "login_timeout"


async def test_flow_activation_denied(hass: HomeAssistant, setup_ha, mock_ghc_client):
    """Test user denies auth → abort with auth_failure."""

    mock_flow = _make_mock_device_flow(
        activation_side_effect=GitHubCopilotAuthError(
            "Authorization was denied by the user."
        ),
    )

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotDeviceFlow.async_initiate",
        new_callable=AsyncMock,
        return_value=mock_flow,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        # Task fails immediately with auth error → abort
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "auth_failure"


async def test_flow_login_timeout_retry(hass: HomeAssistant, setup_ha, mock_ghc_client):
    """Test that login_timeout retries the device flow on connection errors.

    Connection errors show a retry form. Submitting it loops back to
    async_step_user which re-creates the login task on the same device flow.

    The second activation uses an async function that awaits a Future so the
    task doesn't complete instantly — otherwise HA's async_configure while-loop
    on SHOW_PROGRESS_DONE would forward user_input into the next step.
    """

    # Future that blocks until we resolve it (simulates real async polling)
    pending_future: asyncio.Future = hass.loop.create_future()

    mock_auth = AsyncMock(spec=GitHubCopilotAuth)
    mock_auth.session = AsyncMock(spec=aiohttp.ClientSession)
    mock_auth.access_token = "gho_test_token_abc123"
    mock_auth.refresh_token = "ghr_test_refresh_xyz789"
    mock_auth.expiry = 9999999999

    # First call raises connection error; second blocks on future
    activation_calls = 0

    async def activation_side_effect():
        nonlocal activation_calls
        activation_calls += 1
        if activation_calls == 1:
            raise GitHubCopilotConnectionError("Connection timed out")
        return await pending_future

    # Single device flow object — login_timeout doesn't reset _device_flow
    mock_flow = AsyncMock(spec=GitHubCopilotDeviceFlow)
    mock_flow.user_code = "ABCD-1234"
    mock_flow.verification_url = "https://github.com/login/device"
    mock_flow.async_device_activation = activation_side_effect

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotDeviceFlow.async_initiate",
        new_callable=AsyncMock,
        return_value=mock_flow,
    ):
        # Init → task fails → SHOW_PROGRESS_DONE(login_timeout)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE
        assert result["step_id"] == "login_timeout"

        # Advance past progress_done → login_timeout form
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "login_timeout"

        # Submit retry → resets _login_task, re-enters async_step_user
        # Second activation is pending → SHOW_PROGRESS
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["type"] == FlowResultType.SHOW_PROGRESS
        assert result["step_id"] == "user"

        # Resolve the future → task completes with auth result
        pending_future.set_result(mock_auth)
        await hass.async_block_till_done()

        # Re-enter → task done → progress_done(model) → engine advances to model form
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "model"


async def test_flow_network_error(hass: HomeAssistant, setup_ha):
    """Test network failure during device flow initiation."""

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotDeviceFlow.async_initiate",
        new_callable=AsyncMock,
        side_effect=GitHubCopilotConnectionError("Network error"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "cannot_connect"


async def test_options_flow(
    hass: HomeAssistant,
    setup_ha,
    mock_config_entry,
    mock_runtime,
    mock_setup_entry,
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
    hass: HomeAssistant,
    setup_ha,
    mock_config_entry,
    mock_runtime,
    mock_client,
    mock_setup_entry,
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
    assert result["errors"][CONF_MODEL] == "model_no_access"


async def test_flow_model_no_access(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
    mock_ghc_client,
):
    """Test selecting a model the user can't access shows error."""

    mock_ghc_client.async_validate_model.return_value = False

    # Navigate through progress steps (task completes instantly)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "model"

    # Select a model we don't have access to
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_MODEL: "gpt-4.1"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "model"
    assert result["errors"][CONF_MODEL] == "model_no_access"

    # Now allow access and retry
    mock_ghc_client.async_validate_model.return_value = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_MODEL: "gpt-4.1-mini"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_MODEL] == "gpt-4.1-mini"


# ── Model Fetch Fallback Test ──


async def test_flow_model_timeout_and_retry(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
    mock_ghc_client,
):
    """Test model validation connection error → model_timeout → retry succeeds."""

    # First validation raises connection error, second succeeds
    mock_ghc_client.async_validate_model.side_effect = [
        GitHubCopilotConnectionError("Network timeout"),
        True,
    ]

    # Navigate through device flow (completes instantly)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "model"

    # Select model → connection error → model_timeout form
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_MODEL: "gpt-4.1"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "model_timeout"

    # Submit retry → back to model step
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "model"

    # Select model again → succeeds
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_MODEL: "gpt-4.1"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_flow_model_auth_error_aborts(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
    mock_ghc_client,
):
    """Test model validation auth error → abort."""

    mock_ghc_client.async_validate_model.side_effect = GitHubCopilotAuthError(
        "Token expired"
    )

    # Navigate through device flow
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "model"

    # Select model → auth error → abort
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_MODEL: "gpt-4.1"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "auth_failure"


async def test_options_flow_model_validation_exception(
    hass: HomeAssistant,
    setup_ha,
    mock_config_entry,
    mock_runtime,
    mock_client,
    mock_setup_entry,
):
    """Test options flow handles exception during model validation."""

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.async_validate_model.side_effect = GitHubCopilotConnectionError(
        "Network error"
    )

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    # Change model → connection error → form with error
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"model": "gpt-4.1-mini", "prompt": "Custom prompt"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_MODEL] == "model_no_access"


async def test_flow_model_fetch_failure(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
):
    """Test model fetch failure aborts the flow."""

    mock_client = AsyncMock(spec=GitHubCopilotClient)
    mock_client.async_list_models = AsyncMock(side_effect=Exception("API unreachable"))

    mock_auth = AsyncMock(spec=GitHubCopilotAuth)
    mock_auth.access_token = "gho_test_token_abc123"
    mock_auth.refresh_token = "ghr_test_refresh_xyz789"
    mock_auth.expiry = 9999999999
    mock_client.auth = mock_auth

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotClient",
        return_value=mock_client,
    ):
        # Navigate through progress steps (task completes instantly)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE

        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "unknown_cannot_fetch_models"
