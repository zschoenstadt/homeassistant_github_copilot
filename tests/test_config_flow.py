"""Tests for the GitHub Copilot config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.github_copilot.api import (
    GitHubCopilotAuth,
    GitHubCopilotAuthError,
    GitHubCopilotConnectionError,
    GitHubCopilotDeviceFlow,
    GitHubCopilotSDKClient,
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
    mock_flow.verification_uri = "https://github.com/login/device"

    if activation_side_effect is not None:
        mock_flow.async_device_activation = AsyncMock(
            side_effect=activation_side_effect
        )
    else:
        if activation_result is None:
            mock_auth = AsyncMock(spec=GitHubCopilotAuth)
            mock_auth.session = AsyncMock(spec=aiohttp.ClientSession)
            mock_auth.access_token = "gho_test_token_abc123"
            mock_auth.refresh_token = "ghr_test_refresh_xyz789"
            mock_auth.expiry = "2099-12-31T23:59:59"
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
def mock_flow_sdk_client():
    """Mock GitHubCopilotSDKClient constructed during config flow."""

    mock_client = AsyncMock(spec=GitHubCopilotSDKClient)
    mock_client.async_start = AsyncMock()
    mock_client.async_stop = AsyncMock()
    mock_client.async_validate_model = AsyncMock(return_value=True)
    mock_client.async_list_models = AsyncMock(return_value=MOCK_MODELS)

    # Support async with protocol
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotSDKClient",
        return_value=mock_client,
    ):
        yield mock_client


# ── Full Flow Tests ──


async def test_full_flow_success(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
    mock_flow_sdk_client,
    mock_setup_entry,
):
    """Test the complete config flow: user → submit → model → entry."""

    # Step 1: Init shows form with device code
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Step 2: Submit triggers polling, gets auth, goes to model form
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
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


async def test_flow_activation_timeout(
    hass: HomeAssistant, setup_ha, mock_flow_sdk_client
):
    """Test device activation connection error → login_timeout step."""

    mock_flow = _make_mock_device_flow(
        activation_side_effect=GitHubCopilotConnectionError("Connection timed out"),
    )

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotDeviceFlow.async_initiate",
        new_callable=AsyncMock,
        return_value=mock_flow,
    ):
        # Init shows form
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        # Submit → connection error → login_timeout
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "login_timeout"


async def test_flow_activation_denied(
    hass: HomeAssistant, setup_ha, mock_flow_sdk_client
):
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
        # Init shows form
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM

        # Submit → auth error → abort
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "auth_failure"


async def test_flow_login_timeout_retry(
    hass: HomeAssistant, setup_ha, mock_flow_sdk_client
):
    """Test that login_timeout retries the device flow on connection errors.

    Connection errors show a retry form. Submitting it loops back to
    async_step_user which re-tries the device activation.
    """

    mock_auth = AsyncMock(spec=GitHubCopilotAuth)
    mock_auth.session = AsyncMock(spec=aiohttp.ClientSession)
    mock_auth.access_token = "gho_test_token_abc123"
    mock_auth.refresh_token = "ghr_test_refresh_xyz789"
    mock_auth.expiry = "2099-12-31T23:59:59"

    # First call raises connection error; second succeeds
    activation_calls = 0

    async def activation_side_effect():
        nonlocal activation_calls
        activation_calls += 1
        if activation_calls == 1:
            raise GitHubCopilotConnectionError("Connection timed out")
        return mock_auth

    mock_flow = AsyncMock(spec=GitHubCopilotDeviceFlow)
    mock_flow.user_code = "ABCD-1234"
    mock_flow.verification_uri = "https://github.com/login/device"
    mock_flow.async_device_activation = activation_side_effect

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotDeviceFlow.async_initiate",
        new_callable=AsyncMock,
        return_value=mock_flow,
    ):
        # Init → form
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        # Submit → connection error → login_timeout
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "login_timeout"

        # Submit retry → re-enters async_step_user → shows user form
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        # Submit user form again → second activation succeeds → model form
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
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
    mock_sdk_client,
    mock_setup_entry,
):
    """Test options flow rejects model user can't access."""

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_sdk_client.async_validate_model.return_value = False

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


async def test_flow_model_selection(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
    mock_flow_sdk_client,
    mock_setup_entry,
):
    """Test selecting a valid model creates entry."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM

    # Submit to trigger auth
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "model"

    # Select a valid model from the list
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_MODEL: "gpt-4.1-mini"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_MODEL] == "gpt-4.1-mini"


async def test_flow_model_fetch_connection_error(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
):
    """Test model fetch connection error → model_timeout → retry."""

    mock_client = AsyncMock(spec=GitHubCopilotSDKClient)
    mock_client.async_start = AsyncMock()
    mock_client.async_stop = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    # First call: connection error. Second call: success.
    mock_client.async_list_models = AsyncMock(
        side_effect=[
            GitHubCopilotConnectionError("Network timeout"),
            MOCK_MODELS,
        ]
    )

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotSDKClient",
        return_value=mock_client,
    ):
        # Init → form
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM

        # Submit → auth succeeds → model fetch fails → model_timeout
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "model_timeout"

        # Submit retry → model fetch succeeds → model form
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "model"


async def test_flow_model_auth_error_aborts(
    hass: HomeAssistant,
    setup_ha,
    mock_device_flow,
):
    """Test model fetch auth error → abort."""

    mock_client = AsyncMock(spec=GitHubCopilotSDKClient)
    mock_client.async_start = AsyncMock()
    mock_client.async_stop = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.async_list_models = AsyncMock(
        side_effect=GitHubCopilotAuthError("Token expired")
    )

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotSDKClient",
        return_value=mock_client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "auth_failure"


async def test_options_flow_model_validation_exception(
    hass: HomeAssistant,
    setup_ha,
    mock_config_entry,
    mock_runtime,
    mock_sdk_client,
    mock_setup_entry,
):
    """Test options flow handles exception during model validation."""

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_sdk_client.async_validate_model.side_effect = GitHubCopilotConnectionError(
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

    mock_client = AsyncMock(spec=GitHubCopilotSDKClient)
    mock_client.async_start = AsyncMock()
    mock_client.async_stop = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.async_list_models = AsyncMock(side_effect=Exception("API unreachable"))

    with patch(
        "custom_components.github_copilot.config_flow.GitHubCopilotSDKClient",
        return_value=mock_client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "unknown_cannot_fetch_models"
