"""The GitHub Copilot integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import (
    async_get_clientsession,
)
from homeassistant.helpers.typing import ConfigType

from .api import (
    GitHubCopilotAuth,
    GitHubCopilotAuthError,
    GitHubCopilotClient,
    GitHubCopilotConnectionError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRY,
    DOMAIN,
    PLATFORMS,
)
from .runtime import Runtime

__all__ = ["DOMAIN"]

_LOGGER = logging.getLogger(__name__)

type GitHubCopilotConfigEntry = ConfigEntry[GitHubCopilotClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the GitHub Copilot integration."""

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: GitHubCopilotConfigEntry
) -> bool:
    """Set up GitHub Copilot from a config entry."""

    # Create the API client with HA's shared session
    session = async_get_clientsession(hass)
    entry.runtime_data = Runtime(
        hass=hass,
        entry=entry,
        ghc=GitHubCopilotClient(
            session,
            auth=GitHubCopilotAuth(
                session,
                access_token=entry.data[CONF_ACCESS_TOKEN],
                refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
                expiry=entry.data.get(CONF_TOKEN_EXPIRY),
            ),
        ),
    )

    # Validate the token, attempting a refresh if auth fails
    try:
        await entry.runtime_data.async_validate_tokens()
    except GitHubCopilotAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except GitHubCopilotConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: GitHubCopilotConfigEntry
) -> bool:
    """Unload a config entry."""

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
