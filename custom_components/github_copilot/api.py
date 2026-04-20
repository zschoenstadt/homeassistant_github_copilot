"""GitHub Copilot API client.

Two-step auth:
1. GitHub OAuth token (long-lived, from device flow with VS Code client_id)
2. Copilot API token (short-lived, exchanged via copilot_internal/v2/token)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
from typing import Any

import aiohttp

from .const import (
    GITHUB_CLIENT_ID,
    GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
    GITHUB_COPILOT_MODELS_URL,
    GITHUB_COPILOT_TOKEN_URL,
    GITHUB_DEVICE_CODE_URL,
    GITHUB_TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=120)

# Safety margin: refresh Copilot token 60s before expiry
_TOKEN_SAFETY_MARGIN = 60


@dataclass
class DeviceFlowResponse:
    """Response from initiating a device flow."""

    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


@dataclass
class TokenResponse:
    """Response from a token exchange."""

    access_token: str
    refresh_token: str | None
    token_type: str
    scope: str
    expires_in: int | None = None


@dataclass
class Model:
    """A model from the catalog."""

    id: str
    name: str
    capabilities: list[str]


class GitHubCopilotClient:
    """Async client for GitHub Copilot API."""

    class AuthError(Exception):
        """Authentication error."""

    class ConnectionError(Exception):  # noqa: A001
        """Connection error."""

    class RateLimitError(Exception):
        """Rate limit error."""

    class ApiError(Exception):
        """Generic API error."""

    def __init__(
        self,
        access_token: str,
        refresh_token: str | None = None,
        token_expiry: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the client."""

        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_expiry: datetime | None = (
            datetime.fromisoformat(token_expiry) if token_expiry else None
        )
        self._session = session
        self._owns_session = session is None
        # Short-lived Copilot API token (exchanged from OAuth token)
        self._copilot_token: str | None = None
        self._copilot_token_expiry: datetime | None = None

    @property
    def access_token(self) -> str:
        """Return the current GitHub OAuth access token."""

        return self._access_token

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp session."""

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def async_close(self) -> None:
        """Close the session if we own it."""

        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    # ── Copilot Token Management ──

    async def _async_ensure_copilot_token(self) -> str:
        """Get a valid Copilot API token, refreshing if needed."""

        # Return cached token if it's still valid
        if (
            self._copilot_token
            and self._copilot_token_expiry
            and datetime.now() < self._copilot_token_expiry
        ):
            return self._copilot_token

        # Exchange the OAuth token for a short-lived Copilot API token
        session = await self._get_session()
        try:
            async with session.get(
                GITHUB_COPILOT_TOKEN_URL,
                headers={
                    "Authorization": f"token {self._access_token}",
                    "User-Agent": "HomeAssistant-GitHubCopilot/1.0",
                    "Accept": "application/json",
                },
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise self.AuthError("GitHub OAuth token is invalid or expired.")
                if resp.status == 403:
                    text = await resp.text()
                    raise self.AuthError(f"No Copilot access for this account: {text}")
                if resp.status >= 400:
                    text = await resp.text()
                    raise self.ApiError(
                        f"Copilot token exchange failed {resp.status}: {text}"
                    )

                # Cache the new token with a safety margin before expiry
                data = await resp.json()
                self._copilot_token = data["token"]
                expires_at = data.get("expires_at", 0)
                self._copilot_token_expiry = datetime.fromtimestamp(
                    expires_at - _TOKEN_SAFETY_MARGIN
                )
                return self._copilot_token

        except (TimeoutError, aiohttp.ClientError) as err:
            raise self.ConnectionError(
                f"Failed to obtain Copilot token: {err}"
            ) from err

    def _copilot_headers(self, copilot_token: str) -> dict[str, str]:
        """Return headers for Copilot API requests."""

        return {
            "Authorization": f"Bearer {copilot_token}",
            "Copilot-Integration-Id": "vscode-chat",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── Device Flow Authentication ──

    @staticmethod
    async def async_initiate_device_flow(
        session: aiohttp.ClientSession | None = None,
    ) -> DeviceFlowResponse:
        """Initiate the OAuth device flow."""

        # Create a session if one wasn't provided
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession()

        # Request a device code from GitHub
        try:
            async with session.post(
                GITHUB_DEVICE_CODE_URL,
                json={"client_id": GITHUB_CLIENT_ID, "scope": ""},
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise GitHubCopilotClient.ConnectionError(
                        f"Failed to initiate device flow: {resp.status} {text}"
                    )

                data = await resp.json()
                return DeviceFlowResponse(
                    device_code=data["device_code"],
                    user_code=data["user_code"],
                    verification_uri=data["verification_uri"],
                    interval=data.get("interval", 5),
                    expires_in=data.get("expires_in", 900),
                )

        except (TimeoutError, aiohttp.ClientError) as err:
            raise GitHubCopilotClient.ConnectionError(
                f"Failed to connect to GitHub: {err}"
            ) from err

        finally:
            if owns_session:
                await session.close()

    @staticmethod
    async def async_poll_for_token(
        device_code: str,
        interval: int,
        expires_in: int,
        session: aiohttp.ClientSession | None = None,
    ) -> TokenResponse:
        """Poll for a token after user authorizes."""

        # Create a session if one wasn't provided
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession()

        poll_interval = interval
        deadline = asyncio.get_event_loop().time() + expires_in

        # Poll GitHub until the user authorizes or the deadline passes
        try:
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(poll_interval)

                async with session.post(
                    GITHUB_TOKEN_URL,
                    json={
                        "client_id": GITHUB_CLIENT_ID,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    data = await resp.json()

                    # Success — user authorized
                    if "access_token" in data:
                        return TokenResponse(
                            access_token=data["access_token"],
                            refresh_token=data.get("refresh_token"),
                            token_type=data.get("token_type", "bearer"),
                            scope=data.get("scope", ""),
                            expires_in=data.get("expires_in"),
                        )

                    # Handle polling-specific error codes
                    error = data.get("error", "")
                    if error == "authorization_pending":
                        continue
                    if error == "slow_down":
                        poll_interval = data.get("interval", poll_interval + 5)
                        continue
                    if error == "expired_token":
                        raise GitHubCopilotClient.AuthError(
                            "Device code expired. Please try again."
                        )
                    if error == "access_denied":
                        raise GitHubCopilotClient.AuthError(
                            "Authorization was denied by the user."
                        )
                    raise GitHubCopilotClient.AuthError(
                        f"Unexpected error during token exchange: {error}"
                    )

            raise GitHubCopilotClient.AuthError("Authorization timed out.")

        except (TimeoutError, aiohttp.ClientError) as err:
            raise GitHubCopilotClient.ConnectionError(
                f"Connection error during token polling: {err}"
            ) from err

        finally:
            if owns_session:
                await session.close()

    async def async_refresh_token(self) -> TokenResponse:
        """Refresh the GitHub OAuth access token."""

        if not self._refresh_token:
            raise self.AuthError("No refresh token available.")

        # Send the refresh request to GitHub
        session = await self._get_session()
        try:
            async with session.post(
                GITHUB_TOKEN_URL,
                json={
                    "client_id": GITHUB_CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                },
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                data = await resp.json()

                if "access_token" in data:
                    token_resp = TokenResponse(
                        access_token=data["access_token"],
                        refresh_token=data.get("refresh_token", self._refresh_token),
                        token_type=data.get("token_type", "bearer"),
                        scope=data.get("scope", ""),
                        expires_in=data.get("expires_in"),
                    )

                    # Update internal state with new credentials
                    self._access_token = token_resp.access_token
                    self._refresh_token = token_resp.refresh_token
                    if token_resp.expires_in:
                        self._token_expiry = datetime.now() + timedelta(
                            seconds=token_resp.expires_in
                        )

                    # Invalidate Copilot token so it gets re-exchanged
                    self._copilot_token = None
                    self._copilot_token_expiry = None
                    return token_resp

                error = data.get("error", "unknown")
                raise self.AuthError(f"Token refresh failed: {error}")

        except (TimeoutError, aiohttp.ClientError) as err:
            raise self.ConnectionError(
                f"Connection error during token refresh: {err}"
            ) from err

    async def async_validate_token(self) -> bool:
        """Validate by obtaining a Copilot token."""

        try:
            await self._async_ensure_copilot_token()
        except self.AuthError:
            raise
        except self.ConnectionError:
            raise
        except Exception as err:
            raise self.ConnectionError(f"Token validation failed: {err}") from err
        else:
            return True

    # ── Models API ──

    async def async_list_models(self) -> list[Model]:
        """List available models from the Copilot API."""

        # Ensure valid auth and get a session
        copilot_token = await self._async_ensure_copilot_token()
        session = await self._get_session()

        # Fetch and parse the models catalog
        try:
            async with session.get(
                GITHUB_COPILOT_MODELS_URL,
                headers=self._copilot_headers(copilot_token),
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise self.AuthError("Copilot token is invalid or expired.")
                if resp.status == 429:
                    raise self.RateLimitError("Rate limit exceeded.")
                if resp.status >= 400:
                    text = await resp.text()
                    raise self.ApiError(f"API error {resp.status}: {text}")

                # Copilot endpoint wraps models: {"object": "list", "data": [...]}
                data = await resp.json()
                models_list = data.get("data", data) if isinstance(data, dict) else data

                return [
                    Model(
                        id=m["id"],
                        name=m.get("display_name", m.get("name", m["id"])),
                        capabilities=m.get("capabilities", []),
                    )
                    for m in models_list
                    if isinstance(m, dict) and "id" in m
                ]

        except (TimeoutError, aiohttp.ClientError) as err:
            raise self.ConnectionError(f"Failed to list models: {err}") from err

    async def async_validate_model(self, model: str) -> bool:
        """Validate that the given model is accessible."""

        # Ensure valid auth and get a session
        copilot_token = await self._async_ensure_copilot_token()
        session = await self._get_session()

        # Send a minimal request to test model access
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }

        try:
            async with session.post(
                GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
                json=payload,
                headers=self._copilot_headers(copilot_token),
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise self.AuthError("Copilot token is invalid or expired.")
                if resp.status == 403:
                    return False
                if resp.status == 429:
                    return True
                if resp.status >= 400:
                    return False
                return True

        except (TimeoutError, aiohttp.ClientError) as err:
            raise self.ConnectionError(f"Failed to validate model: {err}") from err

    # ── Chat Completions ──

    async def async_chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        stream: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a chat completion request (non-streaming)."""

        # Ensure we have a valid Copilot API token and session
        copilot_token = await self._async_ensure_copilot_token()
        session = await self._get_session()

        # Build the request payload with optional parameters
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools

        # Send request and handle HTTP-level errors
        try:
            async with session.post(
                GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
                json=payload,
                headers=self._copilot_headers(copilot_token),
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise self.AuthError("Copilot token is invalid or expired.")
                if resp.status == 429:
                    raise self.RateLimitError("Rate limit exceeded.")
                if resp.status >= 400:
                    text = await resp.text()
                    raise self.ApiError(f"Chat completion error {resp.status}: {text}")

                return await resp.json()

        except (TimeoutError, aiohttp.ClientError) as err:
            raise self.ConnectionError(
                f"Chat completion request failed: {err}"
            ) from err

    async def async_chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str]:
        """Send a streaming chat completion request, yielding content chunks."""

        # Ensure we have a valid Copilot API token and session
        copilot_token = await self._async_ensure_copilot_token()
        session = await self._get_session()

        # Build the streaming request payload
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        # Stream the response and yield content chunks from SSE events
        try:
            async with session.post(
                GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
                json=payload,
                headers=self._copilot_headers(copilot_token),
                timeout=STREAM_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise self.AuthError("Copilot token is invalid or expired.")
                if resp.status == 429:
                    raise self.RateLimitError("Rate limit exceeded.")
                if resp.status >= 400:
                    text = await resp.text()
                    raise self.ApiError(f"Chat completion error {resp.status}: {text}")

                # Parse SSE lines and extract content deltas
                async for line in resp.content:
                    decoded = line.decode("utf-8").strip()
                    if not decoded or not decoded.startswith("data: "):
                        continue

                    data_str = decoded[6:]
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content")
                            if content:
                                yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        _LOGGER.debug(
                            "Skipping malformed SSE chunk: %s",
                            data_str,
                        )
                        continue

        except (TimeoutError, aiohttp.ClientError) as err:
            raise self.ConnectionError(f"Streaming request failed: {err}") from err
