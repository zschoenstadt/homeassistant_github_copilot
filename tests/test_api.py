"""Tests for the GitHub Copilot API client."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from aioresponses import aioresponses
import pytest

from custom_components.github_copilot.api import (
    GitHubCopilotApiError,
    GitHubCopilotAuth,
    GitHubCopilotAuthError,
    GitHubCopilotClient,
    GitHubCopilotConnectionError,
    GitHubCopilotDeviceFlow,
    GitHubCopilotModel,
    GitHubCopilotRateLimitError,
)
from custom_components.github_copilot.const import (
    GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
    GITHUB_COPILOT_MODELS_URL,
    GITHUB_COPILOT_TOKEN_URL,
    GITHUB_DEVICE_CODE_URL,
    GITHUB_TOKEN_URL,
)

from .conftest import (
    MOCK_CHAT_COMPLETION_RESPONSE,
    MOCK_COPILOT_MODELS_RESPONSE,
    MOCK_COPILOT_TOKEN_RESPONSE,
    MOCK_DEVICE_FLOW_RESPONSE,
    MOCK_TOKEN_DENIED_RESPONSE,
    MOCK_TOKEN_EXPIRED_RESPONSE,
    MOCK_TOKEN_PENDING_RESPONSE,
    MOCK_TOKEN_RESPONSE,
    MOCK_TOKEN_SLOW_DOWN_RESPONSE,
)


def _make_client(session: aiohttp.ClientSession) -> GitHubCopilotClient:
    """Create a client with a pre-set Copilot token (skips token exchange)."""

    auth = GitHubCopilotAuth(
        session=session,
        access_token="gho_test",
        refresh_token=None,
        expiry=None,
    )
    client = GitHubCopilotClient(session=session, auth=auth)

    # Pre-set Copilot token to avoid needing to mock the exchange endpoint
    client.auth._copilot_token = "copilot_test_token"
    client.auth._copilot_token_expiry = datetime.now() + timedelta(hours=1)
    return client


# ── Device Flow Tests ──


async def test_initiate_device_flow(aiohttp_mock):
    """Test initiating the OAuth device flow."""

    aiohttp_mock.post(GITHUB_DEVICE_CODE_URL, payload=MOCK_DEVICE_FLOW_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        result = await GitHubCopilotDeviceFlow.async_initiate(session)
        assert isinstance(result, GitHubCopilotDeviceFlow)
        assert result.user_code == "ABCD-1234"
        assert result.verification_url == "https://github.com/login/device"
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


# ── Token Polling Tests ──


def _make_device_flow(session: aiohttp.ClientSession) -> GitHubCopilotDeviceFlow:
    """Create a device flow instance for testing activation."""

    return GitHubCopilotDeviceFlow(
        session,
        device_code="dc_test_123456",
        user_code="ABCD-1234",
        verification_uri="https://github.com/login/device",
        interval=0,
        expires_in=10,
    )


async def test_device_activation_success(aiohttp_mock):
    """Test successful device activation (token polling)."""

    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        flow = _make_device_flow(session)
        result = await flow.async_device_activation()
        assert isinstance(result, GitHubCopilotAuth)
        assert result.access_token == "gho_test_token_abc123"
        assert result.refresh_token == "ghr_test_refresh_xyz789"
    finally:
        await session.close()


async def test_device_activation_pending_then_success(aiohttp_mock):
    """Test polling that gets pending first, then succeeds."""

    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_PENDING_RESPONSE)
    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        flow = _make_device_flow(session)
        result = await flow.async_device_activation()
        assert result.access_token == "gho_test_token_abc123"
    finally:
        await session.close()


async def test_device_activation_slow_down(aiohttp_mock):
    """Test slow_down response increases interval."""

    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_SLOW_DOWN_RESPONSE)
    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        flow = _make_device_flow(session)
        result = await flow.async_device_activation()
        assert result.access_token == "gho_test_token_abc123"
    finally:
        await session.close()


async def test_device_activation_expired(aiohttp_mock):
    """Test expired device code."""

    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_EXPIRED_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        flow = _make_device_flow(session)
        with pytest.raises(GitHubCopilotAuthError, match="expired"):
            await flow.async_device_activation()
    finally:
        await session.close()


async def test_device_activation_denied(aiohttp_mock):
    """Test user denies authorization."""

    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_DENIED_RESPONSE)

    session = aiohttp.ClientSession()
    try:
        flow = _make_device_flow(session)
        with pytest.raises(GitHubCopilotAuthError, match="denied"):
            await flow.async_device_activation()
    finally:
        await session.close()


# ── Token Refresh Tests ──


async def test_refresh_token_success(aiohttp_mock):
    """Test successful token refresh."""

    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_RESPONSE)

    session = aiohttp.ClientSession()
    auth = GitHubCopilotAuth(
        session=session,
        access_token="gho_old_token",
        refresh_token="ghr_test_refresh",
        expiry=None,
    )
    client = GitHubCopilotClient(session=session, auth=auth)
    callback = AsyncMock()
    try:
        await client.auth.async_refresh_token(callback)
        assert client.auth._access_token == "gho_test_token_abc123"
        assert client.auth._refresh_token == "ghr_test_refresh_xyz789"
        callback.assert_called_once()
    finally:
        await session.close()


async def test_refresh_token_no_refresh_token():
    """Test refresh when no refresh token available."""

    dummy_session = MagicMock(spec=aiohttp.ClientSession)
    auth = GitHubCopilotAuth(
        session=dummy_session,
        access_token="gho_test",
        refresh_token=None,
        expiry=None,
    )
    callback = AsyncMock()
    with pytest.raises(GitHubCopilotAuthError, match="No refresh token"):
        await auth.async_refresh_token(callback)


async def test_refresh_token_expired(aiohttp_mock):
    """Test refresh with expired/revoked refresh token."""

    aiohttp_mock.post(
        GITHUB_TOKEN_URL,
        payload={"error": "bad_refresh_token"},
    )

    session = aiohttp.ClientSession()
    auth = GitHubCopilotAuth(
        session=session,
        access_token="gho_old",
        refresh_token="ghr_expired",
        expiry=None,
    )
    callback = AsyncMock()
    try:
        with pytest.raises(GitHubCopilotAuthError, match="refresh failed"):
            await auth.async_refresh_token(callback)
    finally:
        await session.close()


# ── Copilot Token Exchange Tests ──


async def test_copilot_token_exchange_success(aiohttp_mock):
    """Test successful Copilot token exchange from OAuth token."""

    aiohttp_mock.get(
        GITHUB_COPILOT_TOKEN_URL,
        payload=MOCK_COPILOT_TOKEN_RESPONSE,
    )

    session = aiohttp.ClientSession()
    auth = GitHubCopilotAuth(
        session=session, access_token="gho_test", refresh_token=None, expiry=None
    )
    client = GitHubCopilotClient(session=session, auth=auth)
    try:
        token = await client.auth.async_ensure_copilot_token()
        assert token == MOCK_COPILOT_TOKEN_RESPONSE["token"]
        assert client.auth._copilot_token == token
        assert client.auth._copilot_token_expiry is not None
    finally:
        await session.close()


async def test_copilot_token_exchange_401(aiohttp_mock):
    """Test 401 on Copilot token exchange raises AuthError."""

    aiohttp_mock.get(GITHUB_COPILOT_TOKEN_URL, status=401)

    session = aiohttp.ClientSession()
    auth = GitHubCopilotAuth(
        session=session, access_token="gho_bad", refresh_token=None, expiry=None
    )
    client = GitHubCopilotClient(session=session, auth=auth)
    try:
        with pytest.raises(GitHubCopilotAuthError, match="invalid or expired"):
            await client.auth.async_ensure_copilot_token()
    finally:
        await session.close()


async def test_copilot_token_exchange_403(aiohttp_mock):
    """Test 403 on Copilot token exchange (no Copilot subscription)."""

    aiohttp_mock.get(
        GITHUB_COPILOT_TOKEN_URL,
        status=403,
        body="No Copilot subscription",
    )

    session = aiohttp.ClientSession()
    auth = GitHubCopilotAuth(
        session=session, access_token="gho_test", refresh_token=None, expiry=None
    )
    client = GitHubCopilotClient(session=session, auth=auth)
    try:
        with pytest.raises(GitHubCopilotAuthError, match="No Copilot access"):
            await client.auth.async_ensure_copilot_token()
    finally:
        await session.close()


async def test_copilot_token_cached_when_valid():
    """Test that a valid cached Copilot token is reused without HTTP call."""

    dummy_session = MagicMock(spec=aiohttp.ClientSession)
    auth = GitHubCopilotAuth(
        session=dummy_session, access_token="gho_test", refresh_token=None, expiry=None
    )
    client = GitHubCopilotClient(session=dummy_session, auth=auth)
    client.auth._copilot_token = "cached_token"
    client.auth._copilot_token_expiry = datetime.now() + timedelta(hours=1)

    # No HTTP mock — any HTTP call would fail
    token = await client.auth.async_ensure_copilot_token()
    assert token == "cached_token"


async def test_copilot_token_refreshed_when_expired(aiohttp_mock):
    """Test that an expired Copilot token triggers a new exchange."""

    aiohttp_mock.get(
        GITHUB_COPILOT_TOKEN_URL,
        payload=MOCK_COPILOT_TOKEN_RESPONSE,
    )

    session = aiohttp.ClientSession()
    auth = GitHubCopilotAuth(
        session=session, access_token="gho_test", refresh_token=None, expiry=None
    )
    client = GitHubCopilotClient(session=session, auth=auth)

    # Set an expired token
    client.auth._copilot_token = "old_expired_token"
    client.auth._copilot_token_expiry = datetime.now() - timedelta(minutes=5)
    try:
        token = await client.auth.async_ensure_copilot_token()
        assert token == MOCK_COPILOT_TOKEN_RESPONSE["token"]
        assert token != "old_expired_token"
    finally:
        await session.close()


async def test_copilot_token_connection_error(aiohttp_mock):
    """Test network error during Copilot token exchange."""

    aiohttp_mock.get(
        GITHUB_COPILOT_TOKEN_URL,
        exception=aiohttp.ClientError("Network down"),
    )

    session = aiohttp.ClientSession()
    auth = GitHubCopilotAuth(
        session=session, access_token="gho_test", refresh_token=None, expiry=None
    )
    client = GitHubCopilotClient(session=session, auth=auth)
    try:
        with pytest.raises(
            GitHubCopilotConnectionError,
            match="Failed to obtain Copilot token",
        ):
            await client.auth.async_ensure_copilot_token()
    finally:
        await session.close()


# ── Models API Tests ──


async def test_list_models(aiohttp_mock):
    """Test listing models from catalog."""

    aiohttp_mock.get(GITHUB_COPILOT_MODELS_URL, payload=MOCK_COPILOT_MODELS_RESPONSE)

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        models = await client.async_list_models()
        assert len(models) == 3
        assert all(isinstance(m, GitHubCopilotModel) for m in models)
        assert models[0].id == "gpt-4.1"
        assert models[0].name == "GPT-4.1"
    finally:
        await session.close()


async def test_list_models_401(aiohttp_mock):
    """Test 401 on models endpoint."""

    aiohttp_mock.get(GITHUB_COPILOT_MODELS_URL, status=401)

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        with pytest.raises(GitHubCopilotAuthError):
            await client.async_list_models()
    finally:
        await session.close()


async def test_list_models_429(aiohttp_mock):
    """Test rate limit on models endpoint."""

    aiohttp_mock.get(GITHUB_COPILOT_MODELS_URL, status=429)

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        with pytest.raises(GitHubCopilotRateLimitError):
            await client.async_list_models()
    finally:
        await session.close()


# ── Chat Completion Tests ──


async def test_chat_completion_basic(aiohttp_mock):
    """Test non-streaming chat completion."""

    aiohttp_mock.post(
        GITHUB_COPILOT_CHAT_COMPLETIONS_URL, payload=MOCK_CHAT_COMPLETION_RESPONSE
    )

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        result = await client.async_chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
            model="gpt-4.1",
        )
        assert result["choices"][0]["message"]["content"] == (
            "Hello! How can I help you with your smart home?"
        )
    finally:
        await session.close()


async def test_chat_completion_with_tools(aiohttp_mock):
    """Test that tools are included in the API payload when provided."""

    aiohttp_mock.post(
        GITHUB_COPILOT_CHAT_COMPLETIONS_URL, payload=MOCK_CHAT_COMPLETION_RESPONSE
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "turn_on_light",
                "description": "Turn on a light",
                "parameters": {
                    "type": "object",
                    "properties": {"entity_id": {"type": "string"}},
                },
            },
        }
    ]

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        result = await client.async_chat_completion(
            messages=[{"role": "user", "content": "Turn on the light"}],
            model="gpt-4.1",
            tools=tools,
        )
        assert result["choices"][0]["message"]["content"] is not None
    finally:
        await session.close()


async def test_chat_completion_without_tools(aiohttp_mock):
    """Test that tools key is absent from payload when tools is None."""

    aiohttp_mock.post(
        GITHUB_COPILOT_CHAT_COMPLETIONS_URL, payload=MOCK_CHAT_COMPLETION_RESPONSE
    )

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        await client.async_chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
            model="gpt-4.1",
            tools=None,
        )
    finally:
        await session.close()


async def test_chat_completion_401(aiohttp_mock):
    """Test 401 on chat completion."""

    aiohttp_mock.post(GITHUB_COPILOT_CHAT_COMPLETIONS_URL, status=401)

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        with pytest.raises(GitHubCopilotAuthError):
            await client.async_chat_completion(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4.1",
            )
    finally:
        await session.close()


async def test_chat_completion_429(aiohttp_mock):
    """Test rate limit on chat completion."""

    aiohttp_mock.post(GITHUB_COPILOT_CHAT_COMPLETIONS_URL, status=429)

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        with pytest.raises(GitHubCopilotRateLimitError):
            await client.async_chat_completion(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4.1",
            )
    finally:
        await session.close()


async def test_chat_completion_500(aiohttp_mock):
    """Test server error on chat completion."""

    aiohttp_mock.post(
        GITHUB_COPILOT_CHAT_COMPLETIONS_URL, status=500, body="Internal Error"
    )

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        with pytest.raises(GitHubCopilotApiError, match="500"):
            await client.async_chat_completion(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4.1",
            )
    finally:
        await session.close()


async def test_chat_completion_timeout(aiohttp_mock):
    """Test timeout on chat completion."""

    aiohttp_mock.post(GITHUB_COPILOT_CHAT_COMPLETIONS_URL, exception=TimeoutError())

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        with pytest.raises(GitHubCopilotConnectionError):
            await client.async_chat_completion(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4.1",
            )
    finally:
        await session.close()


# ── Streaming Chat Completion Tests ──


async def test_chat_completion_stream_success(aiohttp_mock):
    """Test successful streaming chat completion."""

    aiohttp_mock.post(
        GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
        body=b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\ndata: {"choices":[{"delta":{"content":"Hello"}}]}\n\ndata: {"choices":[{"delta":{"content":" world"}}]}\n\ndata: {"choices":[{"delta":{"content":"!"}}]}\n\ndata: [DONE]\n\n',
        content_type="text/event-stream",
    )

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        chunks = [
            chunk
            async for chunk in client.async_chat_completion_stream(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4.1",
            )
        ]
        assert chunks == ["Hello", " world", "!"]
    finally:
        await session.close()


async def test_chat_completion_stream_401(aiohttp_mock):
    """Test 401 on streaming endpoint."""

    aiohttp_mock.post(GITHUB_COPILOT_CHAT_COMPLETIONS_URL, status=401)

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        with pytest.raises(GitHubCopilotAuthError):
            async for _ in client.async_chat_completion_stream(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4.1",
            ):
                pass
    finally:
        await session.close()


async def test_chat_completion_stream_429(aiohttp_mock):
    """Test rate limit on streaming endpoint."""

    aiohttp_mock.post(GITHUB_COPILOT_CHAT_COMPLETIONS_URL, status=429)

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        with pytest.raises(GitHubCopilotRateLimitError):
            async for _ in client.async_chat_completion_stream(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4.1",
            ):
                pass
    finally:
        await session.close()


async def test_chat_completion_stream_malformed_chunk(aiohttp_mock):
    """Test streaming with malformed JSON chunks (should skip them)."""

    aiohttp_mock.post(
        GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
        body=b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: {INVALID JSON}\n\ndata: {"choices":[{"delta":{"content":"!"}}]}\n\ndata: [DONE]\n\n',
        content_type="text/event-stream",
    )

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        chunks = [
            chunk
            async for chunk in client.async_chat_completion_stream(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4.1",
            )
        ]
        # Malformed chunk should be skipped, others collected
        assert chunks == ["Hi", "!"]
    finally:
        await session.close()


async def test_chat_completion_stream_connection_error(aiohttp_mock):
    """Test connection error during streaming."""

    aiohttp_mock.post(
        GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
        exception=aiohttp.ClientError("Connection lost"),
    )

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        with pytest.raises(GitHubCopilotConnectionError, match="Streaming"):
            async for _ in client.async_chat_completion_stream(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4.1",
            ):
                pass
    finally:
        await session.close()


# ── Token Refresh Connection Error Tests ──


async def test_refresh_token_connection_error(aiohttp_mock):
    """Test refresh token with network failure."""

    aiohttp_mock.post(GITHUB_TOKEN_URL, exception=aiohttp.ClientError("Network down"))

    session = aiohttp.ClientSession()
    auth = GitHubCopilotAuth(
        session=session,
        access_token="gho_old",
        refresh_token="ghr_test",
        expiry=None,
    )
    callback = AsyncMock()
    try:
        with pytest.raises(
            GitHubCopilotConnectionError,
            match="Connection error during token refresh",
        ):
            await auth.async_refresh_token(callback)
    finally:
        await session.close()


# ── Poll Timeout Test ──


async def test_device_activation_connection_error_during_poll(aiohttp_mock):
    """Test connection error mid-poll is wrapped in GitHubCopilotConnectionError."""

    # First poll returns pending, second raises a network error
    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_PENDING_RESPONSE)
    aiohttp_mock.post(
        GITHUB_TOKEN_URL, exception=aiohttp.ClientError("Connection lost")
    )

    session = aiohttp.ClientSession()
    try:
        flow = _make_device_flow(session)
        with pytest.raises(
            GitHubCopilotConnectionError, match="Connection error during token polling"
        ):
            await flow.async_device_activation()
    finally:
        await session.close()


async def test_refresh_token_callback_args(aiohttp_mock):
    """Test that refresh_token passes exact token values to the callback."""

    aiohttp_mock.post(GITHUB_TOKEN_URL, payload=MOCK_TOKEN_RESPONSE)

    session = aiohttp.ClientSession()
    auth = GitHubCopilotAuth(
        session=session,
        access_token="gho_old_token",
        refresh_token="ghr_test_refresh",
        expiry=None,
    )
    callback = AsyncMock()
    try:
        await auth.async_refresh_token(callback)

        # Verify callback was called with the exact new token values
        callback.assert_called_once()
        call_args = callback.call_args[0]
        assert call_args[0] == MOCK_TOKEN_RESPONSE["access_token"]
        assert call_args[1] == MOCK_TOKEN_RESPONSE["refresh_token"]
        assert call_args[2] is not None  # expiry (datetime object)
    finally:
        await session.close()


async def test_device_activation_timeout(aiohttp_mock):
    """Test that activation times out after expires_in seconds."""

    # Always return pending — the client-side deadline should trigger
    aiohttp_mock.post(
        GITHUB_TOKEN_URL, payload=MOCK_TOKEN_PENDING_RESPONSE, repeat=True
    )

    session = aiohttp.ClientSession()
    try:
        flow = GitHubCopilotDeviceFlow(
            session,
            device_code="dc_test_123456",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            interval=0,
            expires_in=0,  # Immediate timeout
        )
        with pytest.raises(GitHubCopilotAuthError, match="timed out"):
            await flow.async_device_activation()
    finally:
        await session.close()


# ── Validate Model Tests ──


async def test_validate_model_accessible(aiohttp_mock):
    """Test that a model returning 200 is considered accessible."""

    aiohttp_mock.post(
        GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
        payload=MOCK_CHAT_COMPLETION_RESPONSE,
        status=200,
    )

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        result = await client.async_validate_model("gpt-4.1")
        assert result is True
    finally:
        await session.close()


async def test_validate_model_no_access(aiohttp_mock):
    """Test that a 403 response means the model is not accessible."""

    aiohttp_mock.post(
        GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
        payload={"error": {"code": "no_access", "message": "No access to model"}},
        status=403,
    )

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        result = await client.async_validate_model("openai/gpt-5-chat")
        assert result is False
    finally:
        await session.close()


async def test_validate_model_auth_error(aiohttp_mock):
    """Test that a 401 during model validation raises AuthError."""

    aiohttp_mock.post(
        GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
        status=401,
    )

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        with pytest.raises(GitHubCopilotAuthError):
            await client.async_validate_model("gpt-4.1")
    finally:
        await session.close()


async def test_validate_model_rate_limited(aiohttp_mock):
    """Test that a 429 during model validation assumes model is valid."""

    aiohttp_mock.post(
        GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
        status=429,
    )

    session = aiohttp.ClientSession()
    client = _make_client(session)
    try:
        result = await client.async_validate_model("gpt-4.1")
        assert result is True
    finally:
        await session.close()


# ── Fixture for aiohttp mocking ──


@pytest.fixture
def aiohttp_mock():
    """Provide aioresponses mock."""

    with aioresponses() as m:
        yield m
