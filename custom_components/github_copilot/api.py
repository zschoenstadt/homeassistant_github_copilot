"""GitHub Copilot API client.

Two-step auth:
1. GitHub OAuth token (long-lived, from device flow with VS Code client_id)
2. Copilot API token (short-lived, exchanged via copilot_internal/v2/token)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
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
    GITHUB_DEVICE_GRANT,
    GITHUB_TOKEN_URL,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=120)

# Safety margin: refresh Copilot token 60s before expiry
TOKEN_SAFETY_MARGIN = 60

type AsyncRefreshCallback = Callable[[str, str | None, int | None], Awaitable[None]]


class GitHubCopilotAuthError(Exception):
    """Authentication error."""


class GitHubCopilotConnectionError(Exception):
    """Connection error."""


class GitHubCopilotRateLimitError(Exception):
    """Rate limit error."""


class GitHubCopilotApiError(Exception):
    """Generic API error."""


@dataclass
class GitHubCopilotModel:
    """A model from the catalog."""

    id: str
    name: str
    capabilities: list[str]


class GitHubCopilotDeviceFlow:
    @staticmethod
    async def async_initiate(session: aiohttp.ClientSession) -> GitHubCopilotDeviceFlow:
        """Initiate the OAuth device flow."""

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
                    raise GitHubCopilotConnectionError(
                        f"Failed to initiate device flow: {resp.status} {text}"
                    )

                data = await resp.json()

                return GitHubCopilotDeviceFlow(
                    session,
                    device_code=data["device_code"],
                    user_code=data["user_code"],
                    verification_uri=data["verification_uri"],
                    interval=data.get("interval", 5),
                    expires_in=data.get("expires_in", 900),
                )

        except (TimeoutError, aiohttp.ClientError) as err:
            raise GitHubCopilotConnectionError(
                f"Failed to connect to GitHub: {err}"
            ) from err

    def __init__(
        self,
        session: aiohttp.ClientSession,
        device_code: str,
        user_code: str,
        verification_uri: str,
        interval: int,
        expires_in: int,
    ) -> None:
        """Response from initiating a device flow."""

        self._session = session
        self._device_code = device_code
        self._user_code = user_code
        self._verification_uri = verification_uri
        self._interval = interval
        self._expires_in = expires_in

    @property
    def user_code(self) -> str:
        """Return the current GitHub OAuth device code."""
        return self._user_code

    @property
    def verification_uri(self) -> str:
        """Return the current GitHub OAuth verification URL."""

        return self._verification_uri

    async def async_device_activation(self) -> GitHubCopilotAuth:
        """Poll for a token after user authorizes."""

        poll_interval = self._interval
        deadline = asyncio.get_event_loop().time() + self._expires_in

        # Poll GitHub until the user authorizes or the deadline passes
        try:
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(poll_interval)

                async with self._session.post(
                    GITHUB_TOKEN_URL,
                    json={
                        "client_id": GITHUB_CLIENT_ID,
                        "device_code": self._device_code,
                        "grant_type": GITHUB_DEVICE_GRANT,
                    },
                    headers={"Accept": "application/json"},
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    data = await resp.json()

                    # Success — user authorized
                    if "access_token" in data:
                        return GitHubCopilotAuth(
                            self._session,
                            access_token=data["access_token"],
                            refresh_token=data.get("refresh_token"),
                            expiry=datetime.now()
                            + timedelta(seconds=data.get("expires_in", 0)),
                        )

                    # Handle polling-specific error codes
                    error = data.get("error", "")
                    if error == "authorization_pending":
                        continue
                    if error == "slow_down":
                        poll_interval = data.get("interval", poll_interval + 5)
                        continue
                    if error == "expired_token":
                        raise GitHubCopilotAuthError(
                            "Device code expired. Please try again."
                        )
                    if error == "access_denied":
                        raise GitHubCopilotAuthError(
                            "Authorization was denied by the user."
                        )
                    raise GitHubCopilotAuthError(
                        f"Unexpected error during token exchange: {error}"
                    )

            raise GitHubCopilotAuthError("Authorization timed out.")

        except (TimeoutError, aiohttp.ClientError) as err:
            raise GitHubCopilotConnectionError(
                f"Connection error during token polling: {err}"
            ) from err


class GitHubCopilotAuth:
    """Manages GitHub OAuth and Copilot API token lifecycle."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        refresh_token: str | None,
        expiry: int,
    ) -> None:
        """Initialize the auth manager."""

        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._expiry = expiry
        self._refresh_lock = asyncio.Lock()

        # Short-lived Copilot API token (exchanged from OAuth token)
        self._copilot_token: str | None = None
        self._copilot_token_expiry: datetime | None = None

    @property
    def access_token(self) -> str:
        """Return the current GitHub OAuth access token."""
        return self._access_token

    @property
    def refresh_token(self) -> str:
        """Return the current GitHub OAuth refresh token."""
        return self._refresh_token

    @property
    def expiry(self) -> int:
        """Return the current GitHub OAuth expiration."""
        return self._expiry

    @property
    def session(self) -> aiohttp.ClientSession:
        """Return the current session."""

        return self._session

    async def async_refresh_token(self, async_callback: AsyncRefreshCallback) -> None:
        """Refresh the GitHub OAuth access token."""

        if not self._refresh_token:
            raise GitHubCopilotAuthError("No refresh token available.")

        # Serialize concurrent refresh attempts
        async with self._refresh_lock:
            # Send the refresh request to GitHub
            session = self._session
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
                        # Update internal state with new credentials
                        self._access_token = data["access_token"]
                        self._refresh_token = data.get(
                            "refresh_token", self._refresh_token
                        )
                        self._expiry = datetime.now() + timedelta(
                            seconds=data.get("expires_in", 0),
                        )

                        # Invalidate Copilot token so it gets re-exchanged
                        self._copilot_token = None
                        self._copilot_token_expiry = None

                        await async_callback(
                            self._access_token,
                            self._refresh_token,
                            self._expiry,
                        )
                        return

                    error = data.get("error", "unknown")
                    raise GitHubCopilotAuthError(f"Token refresh failed: {error}")

            except (TimeoutError, aiohttp.ClientError) as err:
                raise GitHubCopilotConnectionError(
                    f"Connection error during token refresh: {err}"
                ) from err

    async def async_ensure_copilot_token(self) -> str:
        """Get a valid Copilot API token, refreshing if needed."""

        # Return cached token if it's still valid
        if (
            self._copilot_token
            and self._copilot_token_expiry
            and datetime.now() < self._copilot_token_expiry
        ):
            return self._copilot_token

        # Exchange the OAuth token for a short-lived Copilot API token
        try:
            async with self._session.get(
                GITHUB_COPILOT_TOKEN_URL,
                headers={
                    "Authorization": f"token {self._access_token}",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise GitHubCopilotAuthError(
                        "GitHub OAuth token is invalid or expired."
                    )
                if resp.status == 403:
                    text = await resp.text()
                    raise GitHubCopilotAuthError(
                        f"No Copilot access for this account: {text}"
                    )
                if resp.status >= 400:
                    text = await resp.text()
                    raise GitHubCopilotApiError(
                        f"Copilot token exchange failed {resp.status}: {text}"
                    )

                # Cache the new token with a safety margin before expiry
                data = await resp.json()
                self._copilot_token = data["token"]
                self._copilot_token_expiry = datetime.fromtimestamp(
                    data.get("expires_at", 0) - TOKEN_SAFETY_MARGIN
                )
                return self._copilot_token

        except (TimeoutError, aiohttp.ClientError) as err:
            raise GitHubCopilotConnectionError(
                f"Failed to obtain Copilot token: {err}"
            ) from err


class GitHubCopilotClient:
    """Async client for GitHub Copilot API operations."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: GitHubCopilotAuth,
    ) -> None:
        """Initialize the client."""

        self._session = session
        self._auth = auth

    @property
    def auth(self) -> GitHubCopilotAuth:
        """Return the auth manager."""

        return self._auth

    ### HTTP Helpers ###

    def copilot_headers(self, copilot_token: str) -> dict[str, str]:
        """Return headers for Copilot API requests."""

        return {
            "Authorization": f"Bearer {copilot_token}",
            "Copilot-Integration-Id": "vscode-chat",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _raise_for_status(
        self, resp: aiohttp.ClientResponse, context: str
    ) -> None:
        """Raise typed exceptions for non-success HTTP status codes."""

        if resp.status == 401:
            raise GitHubCopilotAuthError(f"{context}: token invalid.")

        if resp.status == 429:
            raise GitHubCopilotRateLimitError("Rate limit exceeded.")

        if resp.status >= 400:
            text = await resp.text()
            raise GitHubCopilotApiError(f"{context} {resp.status}: {text}")

    ### Models API ###

    async def async_list_models(self) -> list[GitHubCopilotModel]:
        """List available models from the Copilot API."""

        # Ensure valid auth and get a session
        copilot_token = await self._auth.async_ensure_copilot_token()

        # Fetch and parse the models catalog
        try:
            async with self._session.get(
                GITHUB_COPILOT_MODELS_URL,
                headers=self.copilot_headers(copilot_token),
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                await self._raise_for_status(resp, "Models API error")

                # Copilot endpoint wraps models: {"object": "list", "data": [...]}
                data = await resp.json()
                models_list = data.get("data", data) if isinstance(data, dict) else data

                return [
                    GitHubCopilotModel(
                        id=m["id"],
                        name=m.get("display_name", m.get("name", m["id"])),
                        capabilities=m.get("capabilities", []),
                    )
                    for m in models_list
                    if isinstance(m, dict) and "id" in m
                ]

        except (TimeoutError, aiohttp.ClientError) as err:
            raise GitHubCopilotConnectionError(f"Failed to list models: {err}") from err

    async def async_validate_model(self, model: str) -> bool:
        """Validate that the given model is accessible."""

        # Ensure valid auth and get a session
        copilot_token = await self._auth.async_ensure_copilot_token()
        session = self._session

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
                headers=self.copilot_headers(copilot_token),
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise GitHubCopilotAuthError("Copilot token is invalid or expired.")
                if resp.status == 403:
                    return False
                if resp.status == 429:
                    return True
                if resp.status >= 400:
                    return False
                return True

        except (TimeoutError, aiohttp.ClientError) as err:
            raise GitHubCopilotConnectionError(
                f"Failed to validate model: {err}"
            ) from err

    def _build_chat_payload(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        stream: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build the payload dict for a chat completion request."""

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }

        if temperature is not None:
            payload["temperature"] = temperature

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        if tools:
            payload["tools"] = tools

        return payload

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
        copilot_token = await self._auth.async_ensure_copilot_token()
        session = self._session

        # Build the request payload
        payload = self._build_chat_payload(
            messages,
            model,
            stream=False,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )

        # Send request and handle HTTP-level errors
        try:
            async with session.post(
                GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
                json=payload,
                headers=self.copilot_headers(copilot_token),
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                await self._raise_for_status(resp, "Chat completion error")

                return await resp.json()

        except (TimeoutError, aiohttp.ClientError) as err:
            raise GitHubCopilotConnectionError(
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
        copilot_token = await self._auth.async_ensure_copilot_token()
        session = self._session

        # Build the streaming request payload
        payload = self._build_chat_payload(
            messages,
            model,
            stream=True,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Stream the response and yield content chunks from SSE events
        try:
            async with session.post(
                GITHUB_COPILOT_CHAT_COMPLETIONS_URL,
                json=payload,
                headers=self.copilot_headers(copilot_token),
                timeout=STREAM_TIMEOUT,
            ) as resp:
                await self._raise_for_status(resp, "Chat completion error")

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
            raise GitHubCopilotConnectionError(
                f"Streaming request failed: {err}"
            ) from err
