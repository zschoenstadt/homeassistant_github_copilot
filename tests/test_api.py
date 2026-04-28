"""Tests for the GitHub Copilot API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aioresponses import aioresponses
import pytest

from custom_components.github_copilot.api import (
    GitHubCopilotAuth,
    GitHubCopilotAuthError,
    GitHubCopilotConnectionError,
    GitHubCopilotDeviceFlow,
    GitHubCopilotModel,
    GitHubCopilotSDKClient,
)
from custom_components.github_copilot.const import (
    GITHUB_DEVICE_CODE_URL,
    GITHUB_TOKEN_URL,
)

from .conftest import (
    MOCK_DEVICE_FLOW_RESPONSE,
    MOCK_TOKEN_DENIED_RESPONSE,
    MOCK_TOKEN_EXPIRED_RESPONSE,
    MOCK_TOKEN_PENDING_RESPONSE,
    MOCK_TOKEN_RESPONSE,
    MOCK_TOKEN_SLOW_DOWN_RESPONSE,
)


@pytest.fixture
def aiohttp_mock():
    """Mock aiohttp requests."""

    with aioresponses() as mock:
        yield mock


# ── Device Flow Tests ──


async def test_initiate_device_flow(aiohttp_mock):
    """Test initiating the OAuth device flow."""

    aiohttp_mock.post(GITHUB_DEVICE_CODE_URL, payload=MOCK_DEVICE_FLOW_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        result = await GitHubCopilotDeviceFlow.async_initiate(session)
        assert isinstance(result, GitHubCopilotDeviceFlow)
        assert result.user_code == "ABCD-1234"
        assert result.verification_uri == "https://github.com/login/device"
    finally:
        await session.close()


async def test_initiate_device_flow_connection_error(aiohttp_mock):
    """Test device flow with connection error."""

    aiohttp_mock.post(GITHUB_DEVICE_CODE_URL, exception=aiohttp.ClientError("fail"))

    session = aiohttp.ClientSession()
    try:
        with pytest.raises(GitHubCopilotConnectionError):
            await GitHubCopilotDeviceFlow.async_initiate(session)
    finally:
        await session.close()


async def test_initiate_device_flow_server_error(aiohttp_mock):
    """Test device flow with server error status."""

    aiohttp_mock.post(GITHUB_DEVICE_CODE_URL, status=500, body="Server Error")

    session = aiohttp.ClientSession()
    try:
        with pytest.raises(GitHubCopilotConnectionError, match="500"):
            await GitHubCopilotDeviceFlow.async_initiate(session)
    finally:
        await session.close()


async def test_device_activation_success(aiohttp_mock):
    """Test successful device activation with token returned."""

    aiohttp_mock.post(GITHUB_DEVICE_CODE_URL, payload=MOCK_DEVICE_FLOW_RESPONSE)
    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        flow = await GitHubCopilotDeviceFlow.async_initiate(session)
        auth = await flow.async_device_activation()
        assert isinstance(auth, GitHubCopilotAuth)
        assert auth.access_token == "gho_test_token_abc123"
        assert auth.refresh_token == "ghr_test_refresh_xyz789"
    finally:
        await session.close()


async def test_device_activation_pending_then_success(aiohttp_mock):
    """Test polling with pending then success."""

    aiohttp_mock.post(GITHUB_DEVICE_CODE_URL, payload=MOCK_DEVICE_FLOW_RESPONSE)

    # First poll: pending, second: success
    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_PENDING_RESPONSE)
    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        flow = await GitHubCopilotDeviceFlow.async_initiate(session)
        # Override interval to speed up test
        flow._interval = 0

        auth = await flow.async_device_activation()
        assert auth.access_token == "gho_test_token_abc123"
    finally:
        await session.close()


async def test_device_activation_denied(aiohttp_mock):
    """Test device activation when user denies."""

    aiohttp_mock.post(GITHUB_DEVICE_CODE_URL, payload=MOCK_DEVICE_FLOW_RESPONSE)
    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_DENIED_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        flow = await GitHubCopilotDeviceFlow.async_initiate(session)
        flow._interval = 0

        with pytest.raises(GitHubCopilotAuthError, match="denied"):
            await flow.async_device_activation()
    finally:
        await session.close()


async def test_device_activation_expired(aiohttp_mock):
    """Test device activation when code expires."""

    aiohttp_mock.post(GITHUB_DEVICE_CODE_URL, payload=MOCK_DEVICE_FLOW_RESPONSE)
    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_EXPIRED_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        flow = await GitHubCopilotDeviceFlow.async_initiate(session)
        flow._interval = 0

        with pytest.raises(GitHubCopilotAuthError, match="expired"):
            await flow.async_device_activation()
    finally:
        await session.close()


async def test_device_activation_slow_down(aiohttp_mock):
    """Test device activation handles slow_down response."""

    aiohttp_mock.post(GITHUB_DEVICE_CODE_URL, payload=MOCK_DEVICE_FLOW_RESPONSE)

    # First poll: slow_down, second: success
    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_SLOW_DOWN_RESPONSE)
    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        flow = await GitHubCopilotDeviceFlow.async_initiate(session)
        flow._interval = 0

        auth = await flow.async_device_activation()
        assert auth.access_token == "gho_test_token_abc123"
    finally:
        await session.close()


# ── Auth Token Refresh Tests ──


async def test_auth_refresh_token_success(aiohttp_mock):
    """Test successful OAuth token refresh."""

    aiohttp_mock.post(
        GITHUB_TOKEN_URL,
        payload={
            "access_token": "gho_refreshed_token",
            "refresh_token": "ghr_new_refresh",
            "expires_in": 28800,
        },
    )

    session = aiohttp.ClientSession()
    try:
        auth = GitHubCopilotAuth(
            session=session,
            access_token="gho_old_token",
            refresh_token="ghr_old_refresh",
            expiry=None,
        )

        callback = AsyncMock()
        await auth.async_refresh_token(callback)

        assert auth.access_token == "gho_refreshed_token"
        assert auth.refresh_token == "ghr_new_refresh"
        assert auth.expiry is not None
        assert auth.is_expired is False
        callback.assert_called_once()
    finally:
        await session.close()


async def test_auth_refresh_token_no_refresh_token():
    """Test refresh fails when no refresh token available."""

    session = AsyncMock(spec=aiohttp.ClientSession)
    auth = GitHubCopilotAuth(
        session=session,
        access_token="gho_test",
        refresh_token=None,
        expiry=None,
    )

    with pytest.raises(GitHubCopilotAuthError, match="No refresh token"):
        await auth.async_refresh_token(AsyncMock())


async def test_auth_refresh_token_error(aiohttp_mock):
    """Test refresh fails when GitHub returns error."""

    aiohttp_mock.post(
        GITHUB_TOKEN_URL,
        payload={"error": "bad_refresh_token"},
    )

    session = aiohttp.ClientSession()
    try:
        auth = GitHubCopilotAuth(
            session=session,
            access_token="gho_old",
            refresh_token="ghr_bad",
            expiry=None,
        )

        with pytest.raises(GitHubCopilotAuthError, match="refresh failed"):
            await auth.async_refresh_token(AsyncMock())
    finally:
        await session.close()


async def test_auth_is_expired_with_past_expiry():
    """Test is_expired returns True when expiry is in the past."""

    session = AsyncMock(spec=aiohttp.ClientSession)
    auth = GitHubCopilotAuth(
        session=session,
        access_token="gho_test",
        refresh_token=None,
        expiry="2020-01-01T00:00:00",
    )

    assert auth.is_expired is True


async def test_auth_is_expired_with_future_expiry():
    """Test is_expired returns False when expiry is in the future."""

    session = AsyncMock(spec=aiohttp.ClientSession)
    auth = GitHubCopilotAuth(
        session=session,
        access_token="gho_test",
        refresh_token=None,
        expiry="2099-12-31T23:59:59",
    )

    assert auth.is_expired is False


async def test_auth_is_expired_with_no_expiry():
    """Test is_expired returns False when token has no expiry (never expires)."""

    session = AsyncMock(spec=aiohttp.ClientSession)
    auth = GitHubCopilotAuth(
        session=session,
        access_token="gho_test",
        refresh_token=None,
        expiry=None,
    )

    assert auth.is_expired is False


# ── SDK Client Tests ──


def _make_mock_auth(token: str = "gho_test_token") -> MagicMock:
    """Create a mock GitHubCopilotAuth with a given access token."""

    auth = MagicMock(spec=GitHubCopilotAuth)
    auth.access_token = token
    return auth


def _make_sdk_client(token: str = "gho_test_token") -> GitHubCopilotSDKClient:
    """Create a GitHubCopilotSDKClient with a mock auth."""

    return GitHubCopilotSDKClient(auth=_make_mock_auth(token))


async def test_sdk_client_start_stop():
    """Test SDK client lifecycle."""

    mock_copilot_client = AsyncMock()
    mock_copilot_client.start = AsyncMock()
    mock_copilot_client.stop = AsyncMock()

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=mock_copilot_client,
    ):
        client = _make_sdk_client()
        await client.async_start()

        assert client._client is not None
        mock_copilot_client.start.assert_called_once()

        await client.async_stop()
        assert client._client is None
        mock_copilot_client.stop.assert_called_once()


async def test_sdk_client_not_started():
    """Test accessing client property before start raises error."""

    client = _make_sdk_client()

    with pytest.raises(GitHubCopilotConnectionError, match="not started"):
        _ = client.client


async def test_sdk_client_check_auth():
    """Test auth status check via SDK."""

    mock_copilot_client = AsyncMock()
    mock_copilot_client.start = AsyncMock()
    mock_copilot_client.stop = AsyncMock()

    mock_status = MagicMock()
    mock_status.isAuthenticated = True
    mock_copilot_client.get_auth_status = AsyncMock(return_value=mock_status)

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=mock_copilot_client,
    ):
        client = _make_sdk_client()
        await client.async_start()

        result = await client.async_check_auth()
        assert result is True


async def test_sdk_client_list_models():
    """Test listing models via SDK."""

    mock_copilot_client = AsyncMock()
    mock_copilot_client.start = AsyncMock()
    mock_copilot_client.stop = AsyncMock()

    # Mock model objects
    mock_model_1 = MagicMock()
    mock_model_1.id = "gpt-4.1"
    mock_model_1.name = "GPT-4.1"
    mock_model_2 = MagicMock()
    mock_model_2.id = "gpt-4.1-mini"
    mock_model_2.name = "GPT-4.1 Mini"
    mock_copilot_client.list_models = AsyncMock(
        return_value=[mock_model_1, mock_model_2]
    )

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=mock_copilot_client,
    ):
        client = _make_sdk_client()
        await client.async_start()

        models = await client.async_list_models()
        assert len(models) == 2
        assert models[0] == GitHubCopilotModel(id="gpt-4.1", name="GPT-4.1")
        assert models[1] == GitHubCopilotModel(id="gpt-4.1-mini", name="GPT-4.1 Mini")


async def test_sdk_client_validate_model():
    """Test model validation via SDK."""

    mock_copilot_client = AsyncMock()
    mock_copilot_client.start = AsyncMock()
    mock_copilot_client.stop = AsyncMock()

    mock_model = MagicMock()
    mock_model.id = "gpt-4.1"
    mock_model.name = "GPT-4.1"
    mock_copilot_client.list_models = AsyncMock(return_value=[mock_model])

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=mock_copilot_client,
    ):
        client = _make_sdk_client()
        await client.async_start()

        assert await client.async_validate_model("gpt-4.1") is True
        assert await client.async_validate_model("nonexistent") is False


async def test_sdk_client_restart():
    """Test that restart cycles stop and start with current auth token."""

    mock_copilot_client = AsyncMock()
    mock_copilot_client.start = AsyncMock()
    mock_copilot_client.stop = AsyncMock()

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=mock_copilot_client,
    ):
        client = _make_sdk_client()
        await client.async_start()

        # Restart should stop and start
        await client.async_restart()
        mock_copilot_client.stop.assert_called_once()
        assert mock_copilot_client.start.call_count == 2


async def test_sdk_client_create_session():
    """Test creating a session via SDK."""

    mock_session = AsyncMock()
    mock_copilot_client = AsyncMock()
    mock_copilot_client.start = AsyncMock()
    mock_copilot_client.stop = AsyncMock()
    mock_copilot_client.create_session = AsyncMock(return_value=mock_session)

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=mock_copilot_client,
    ):
        client = _make_sdk_client()
        await client.async_start()

        session = await client.async_create_session(
            session_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            model="gpt-4.1",
            system_message="You are helpful.",
        )
        assert session == mock_session
        mock_copilot_client.create_session.assert_called_once()


async def test_sdk_client_connection_error_on_list_models():
    """Test that OS errors are wrapped in GitHubCopilotConnectionError."""

    mock_copilot_client = AsyncMock()
    mock_copilot_client.start = AsyncMock()
    mock_copilot_client.stop = AsyncMock()
    mock_copilot_client.list_models = AsyncMock(
        side_effect=OSError("Connection refused")
    )

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=mock_copilot_client,
    ):
        client = _make_sdk_client()
        await client.async_start()

        with pytest.raises(GitHubCopilotConnectionError):
            await client.async_list_models()


async def test_sdk_client_connection_error_on_check_auth():
    """Test that OS errors during auth check are wrapped properly."""

    mock_copilot_client = AsyncMock()
    mock_copilot_client.start = AsyncMock()
    mock_copilot_client.stop = AsyncMock()
    mock_copilot_client.get_auth_status = AsyncMock(
        side_effect=RuntimeError("Process died")
    )

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=mock_copilot_client,
    ):
        client = _make_sdk_client()
        await client.async_start()

        with pytest.raises(GitHubCopilotConnectionError):
            await client.async_check_auth()


async def test_sdk_client_context_manager():
    """Test that async with starts and stops the client."""

    mock_copilot_client = AsyncMock()
    mock_copilot_client.start = AsyncMock()
    mock_copilot_client.stop = AsyncMock()

    mock_status = MagicMock()
    mock_status.isAuthenticated = True
    mock_copilot_client.get_auth_status = AsyncMock(return_value=mock_status)

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=mock_copilot_client,
    ):
        async with _make_sdk_client() as client:
            # Client should be started inside the context
            mock_copilot_client.start.assert_called_once()
            assert client._client is not None

            result = await client.async_check_auth()
            assert result is True

        # Client should be stopped after exiting the context
        mock_copilot_client.stop.assert_called_once()


async def test_sdk_client_context_manager_stops_on_exception():
    """Test that async with stops the client even when an exception occurs."""

    mock_copilot_client = AsyncMock()
    mock_copilot_client.start = AsyncMock()
    mock_copilot_client.stop = AsyncMock()
    mock_copilot_client.list_models = AsyncMock(
        side_effect=OSError("Connection refused")
    )

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=mock_copilot_client,
    ):
        with pytest.raises(GitHubCopilotConnectionError):
            async with _make_sdk_client() as client:
                await client.async_list_models()

        # Client must be stopped even though an exception was raised
        mock_copilot_client.stop.assert_called_once()
