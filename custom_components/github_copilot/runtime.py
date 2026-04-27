"""Runtime data management for the GitHub Copilot integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import GitHubCopilotAuth, GitHubCopilotAuthError, GitHubCopilotSDKClient
from .const import CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, CONF_TOKEN_EXPIRY

_LOGGER = logging.getLogger(__name__)


@dataclass
class Runtime:
    """Runtime data management for the integration."""

    hass: HomeAssistant
    entry: ConfigEntry
    auth: GitHubCopilotAuth
    sdk_client: GitHubCopilotSDKClient

    async def _async_update_tokens(
        self,
        access_token: str,
        refresh_token: str | None,
        expiry: str | None,
    ) -> None:
        """Persist refreshed tokens to the config entry."""

        new_data = {
            **self.entry.data,
            CONF_ACCESS_TOKEN: access_token,
        }

        if refresh_token:
            new_data[CONF_REFRESH_TOKEN] = refresh_token
        if expiry:
            new_data[CONF_TOKEN_EXPIRY] = expiry

        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

        # Restart the SDK subprocess so it picks up the new token
        await self.sdk_client.async_restart()

    async def async_validate_auth(self) -> None:
        """Validate authentication by checking with the SDK.

        Proactively refreshes if the OAuth token is known to be expired,
        then falls back to SDK auth check with refresh on failure.
        """

        # Proactively refresh if we already know the token is expired
        if self.auth.is_expired:
            _LOGGER.info("OAuth token expired, refreshing before SDK check")
            await self.auth.async_refresh_token(self._async_update_tokens)
            return

        # Check if the current token is valid via the SDK
        authenticated = await self.sdk_client.async_check_auth()
        if authenticated:
            return

        # Try refreshing the OAuth token and re-checking
        _LOGGER.info("Auth check failed, attempting token refresh")
        await self.auth.async_refresh_token(self._async_update_tokens)

        authenticated = await self.sdk_client.async_check_auth()
        if not authenticated:
            raise GitHubCopilotAuthError(
                "GitHub token is not authenticated for Copilot after refresh."
            )
