"""The GitHub Copilot integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType

from .api import GitHubCopilotClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRY,
    DOMAIN,
    PLATFORMS,
)

__all__ = ["DOMAIN"]

_LOGGER = logging.getLogger(__name__)

# Import exception classes directly so they survive mocking of the constructor
_AuthError = GitHubCopilotClient.AuthError
_ConnectionError = GitHubCopilotClient.ConnectionError

type GitHubCopilotConfigEntry = ConfigEntry[GitHubCopilotClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the GitHub Copilot integration."""

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: GitHubCopilotConfigEntry
) -> bool:
    """Set up GitHub Copilot from a config entry."""

    # Create the API client from stored credentials
    client = GitHubCopilotClient(
        access_token=entry.data[CONF_ACCESS_TOKEN],
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        token_expiry=entry.data.get(CONF_TOKEN_EXPIRY),
    )

    # Validate the token is still good before proceeding
    try:
        await client.async_validate_token()
    except _AuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except _ConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    # Store client on the entry and forward platform setup
    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: GitHubCopilotConfigEntry
) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_close()
    return unload_ok
