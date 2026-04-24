"""The GitHub Copilot integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import (
    GitHubCopilotAuth,
    GitHubCopilotAuthError,
    GitHubCopilotConnectionError,
    GitHubCopilotSDKClient,
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

type GitHubCopilotConfigEntry = ConfigEntry[Runtime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the GitHub Copilot integration."""

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: GitHubCopilotConfigEntry
) -> bool:
    """Set up GitHub Copilot from a config entry."""

    # Create the auth manager for OAuth token refresh
    session = async_get_clientsession(hass)
    auth = GitHubCopilotAuth(
        session,
        access_token=entry.data[CONF_ACCESS_TOKEN],
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        expiry=entry.data.get(CONF_TOKEN_EXPIRY),
    )

    # Create and start the SDK client (spawns the CLI subprocess)
    sdk_client = GitHubCopilotSDKClient(auth=auth)

    try:
        await sdk_client.async_start()
    except Exception as err:
        raise ConfigEntryNotReady(f"Failed to start Copilot SDK client: {err}") from err

    # Store runtime data
    entry.runtime_data = Runtime(
        hass=hass,
        entry=entry,
        auth=auth,
        sdk_client=sdk_client,
    )

    # Validate authentication via the SDK
    try:
        await entry.runtime_data.async_validate_auth()
    except GitHubCopilotAuthError as err:
        await sdk_client.async_stop()
        raise ConfigEntryAuthFailed(str(err)) from err
    except GitHubCopilotConnectionError as err:
        await sdk_client.async_stop()
        raise ConfigEntryNotReady(str(err)) from err

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: GitHubCopilotConfigEntry
) -> bool:
    """Unload a config entry."""

    # Stop the SDK client and its CLI subprocess
    if hasattr(entry, "runtime_data") and entry.runtime_data:
        await entry.runtime_data.sdk_client.async_stop()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
