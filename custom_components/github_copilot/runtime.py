"""Runtime data management for the GitHub Copilot integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import GitHubCopilotAuthError, GitHubCopilotClient
from .const import CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, CONF_TOKEN_EXPIRY

_LOGGER = logging.getLogger(__name__)


@dataclass
class Runtime:
    """Runtime data management for the integration."""

    hass: HomeAssistant
    entry: ConfigEntry  # XXX: Ideally GitHubCopilotConfigEntry, but circular import from __init__
    ghc: GitHubCopilotClient

    async def _async_update_tokens(
        self,
        access_token: str,
        refresh_token: str | None,
        expiry: int | None,
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

    async def async_validate_tokens(self) -> None:
        """Validate by obtaining a Copilot Token, attempting a refresh if auth fails."""

        try:
            await self.ghc.auth.async_ensure_copilot_token()
        except GitHubCopilotAuthError:
            _LOGGER.info("Auth error during token validation, attempting token refresh")
            await self.ghc.auth.async_refresh_token(self._async_update_tokens)
            await self.ghc.auth.async_ensure_copilot_token()
            # Purposefully allowing the caller to handle exception filtering on re-error

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
        """Run an API chat completion, but attempt to refresh tokens if auth fails."""

        async def _call_chat() -> dict[str, Any]:
            return await self.ghc.async_chat_completion(
                messages=messages,
                model=model,
                stream=stream,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
            )

        # Call the Copilot API, retrying once on auth failure
        try:
            return await _call_chat()
        except GitHubCopilotAuthError:
            _LOGGER.info("Auth error during chat, attempting token refresh")
            await self.ghc.auth.async_refresh_token(self._async_update_tokens)
            return await _call_chat()
            # Purposefully allowing the caller to handle exception filtering on re-error
