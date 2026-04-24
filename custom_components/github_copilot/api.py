"""GitHub Copilot API client.

Auth flow:
1. GitHub OAuth token (long-lived, from device flow with VS Code client_id)
2. Token passed to Copilot SDK → bundled CLI handles all Copilot API communication

The SDK spawns a CLI binary as a subprocess and communicates via JSON-RPC.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

import aiohttp
from copilot import CopilotClient, SubprocessConfig
from copilot._jsonrpc import JsonRpcError, ProcessExitedError
from copilot.generated.session_events import SessionEvent
from copilot.session import CopilotSession, PermissionHandler, Tool

from .const import (
    GITHUB_CLIENT_ID,
    GITHUB_DEVICE_CODE_URL,
    GITHUB_DEVICE_GRANT,
    GITHUB_TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Timeout for waiting on SDK session responses
SESSION_RESPONSE_TIMEOUT = 120

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


class GitHubCopilotDeviceFlow:
    """OAuth device flow for GitHub authentication."""

    @staticmethod
    async def async_initiate(
        session: aiohttp.ClientSession,
    ) -> GitHubCopilotDeviceFlow:
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
    """Manages GitHub OAuth token lifecycle.

    The SDK/CLI handles Copilot token exchange internally — we only
    need to manage the long-lived OAuth token and its refresh cycle.
    """

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

    @property
    def access_token(self) -> str:
        """Return the current GitHub OAuth access token."""

        return self._access_token

    @property
    def refresh_token(self) -> str | None:
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


class GitHubCopilotSDKClient:
    """Wraps the Copilot SDK client for use in Home Assistant.

    Manages the lifecycle of the bundled CLI subprocess and provides
    typed methods for auth validation, model listing, and session creation.

    Holds a reference to ``GitHubCopilotAuth`` so the subprocess is always
    started with the latest OAuth token after a refresh cycle.  SDK sessions
    persist on disk across subprocess restarts, so a token-refresh restart
    does not lose conversation history.
    """

    def __init__(self, auth: GitHubCopilotAuth) -> None:
        """Initialize with an auth manager."""

        self._auth = auth
        self._client: CopilotClient | None = None
        self._restart_lock = asyncio.Lock()

    @property
    def client(self) -> CopilotClient:
        """Return the underlying SDK client, raising if not started."""

        if self._client is None:
            raise GitHubCopilotConnectionError("SDK client not started.")

        return self._client

    async def async_start(self) -> None:
        """Start the SDK client and its CLI subprocess.

        Reads the current token from the auth manager, so a prior
        token refresh is automatically picked up.
        """

        config = SubprocessConfig(
            github_token=self._auth.access_token,
            use_logged_in_user=False,
        )
        self._client = CopilotClient(config)
        await self._client.start()

    async def async_stop(self) -> None:
        """Stop the SDK client and terminate the CLI subprocess."""

        if self._client is not None:
            try:
                await self._client.stop()
            except (JsonRpcError, ProcessExitedError, OSError, RuntimeError):
                _LOGGER.debug("Error stopping SDK client", exc_info=True)
            finally:
                self._client = None

    async def async_restart(self) -> None:
        """Restart the subprocess with the current auth token.

        Serialized by a lock so concurrent callers (e.g. multiple entities
        hitting an auth error at once) only restart once.  SDK session state
        survives on disk, so ``resume_session`` will still work after this.
        """

        async with self._restart_lock:
            await self.async_stop()
            await self.async_start()

    async def async_check_auth(self) -> bool:
        """Check if the current token is authenticated."""

        try:
            status = await self.client.get_auth_status()
        except (JsonRpcError, ProcessExitedError, OSError, RuntimeError) as err:
            raise GitHubCopilotConnectionError(
                f"Failed to check auth status: {err}"
            ) from err

        return status.isAuthenticated

    async def async_list_models(self) -> list[GitHubCopilotModel]:
        """List available models from the Copilot API."""

        try:
            models = await self.client.list_models()
        except (JsonRpcError, ProcessExitedError, OSError, RuntimeError) as err:
            raise GitHubCopilotConnectionError(f"Failed to list models: {err}") from err

        return [GitHubCopilotModel(id=m.id, name=m.name) for m in models]

    async def async_validate_model(self, model_id: str) -> bool:
        """Validate that a model is available."""

        models = await self.async_list_models()
        return any(m.id == model_id for m in models)

    def _build_system_message_config(
        self,
        system_message: str | None,
    ) -> dict[str, str] | None:
        """Build the system message config dict for the SDK."""

        if not system_message:
            return None

        return {"content": system_message, "mode": "append"}

    async def async_create_session(
        self,
        *,
        session_id: str,
        model: str,
        system_message: str | None = None,
        tools: list[Tool] | None = None,
        streaming: bool = True,
        on_event: Callable[[SessionEvent], None] | None = None,
    ) -> CopilotSession:
        """Create a new SDK session with an explicit ID.

        The session ID should be namespaced (e.g. ``entry_id:conversation_id``)
        to avoid collisions.  System message is set here and persists for the
        lifetime of the session — callers should not re-send it on resume.
        """

        return await self.client.create_session(
            session_id=session_id,
            on_permission_request=PermissionHandler.approve_all,
            model=model,
            system_message=self._build_system_message_config(system_message),
            tools=tools,
            available_tools=[],
            streaming=streaming,
            on_event=on_event,
        )

    async def async_resume_session(
        self,
        *,
        session_id: str,
        model: str,
        tools: list[Tool] | None = None,
        streaming: bool = True,
        on_event: Callable[[SessionEvent], None] | None = None,
    ) -> CopilotSession:
        """Resume an existing SDK session by ID.

        Conversation history is preserved on disk by the CLI.  Tools and event
        handlers must be re-registered since they are in-memory only.  System
        message is NOT re-sent — it was set during create.
        """

        return await self.client.resume_session(
            session_id=session_id,
            on_permission_request=PermissionHandler.approve_all,
            model=model,
            tools=tools,
            available_tools=[],
            streaming=streaming,
            on_event=on_event,
        )

    async def async_get_or_create_session(
        self,
        *,
        session_id: str,
        model: str,
        system_message: str | None = None,
        tools: list[Tool] | None = None,
        streaming: bool = True,
        on_event: Callable[[SessionEvent], None] | None = None,
    ) -> CopilotSession:
        """Resume a session if it exists, otherwise create a new one.

        This is the primary entry point for entity code.  It tries resume
        first (no in-memory tracking needed), and falls back to create if
        the session doesn't exist on disk.  System message is only applied
        on initial creation.
        """

        # Try resuming first — this is the common path for ongoing conversations
        try:
            return await self.async_resume_session(
                session_id=session_id,
                model=model,
                tools=tools,
                streaming=streaming,
                on_event=on_event,
            )
        except (JsonRpcError, ProcessExitedError, OSError, RuntimeError):
            _LOGGER.debug("Session %s not found, creating new session", session_id)

        # Fall back to creating a fresh session
        return await self.async_create_session(
            session_id=session_id,
            model=model,
            system_message=system_message,
            tools=tools,
            streaming=streaming,
            on_event=on_event,
        )
