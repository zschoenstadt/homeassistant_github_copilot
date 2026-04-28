"""Constants for the GitHub Copilot integration."""

from enum import StrEnum
from typing import Final

from homeassistant.const import CONF_LLM_HASS_API, Platform
from homeassistant.helpers.llm import DEFAULT_INSTRUCTIONS_PROMPT

__all__ = ["CONF_LLM_HASS_API"]

DOMAIN: Final = "github_copilot"

PLATFORMS: Final = (
    Platform.CONVERSATION,
    Platform.AI_TASK,
)

# Config keys
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_TOKEN_EXPIRY: Final = "token_expiry"
CONF_MODEL: Final = "model"
CONF_PROMPT: Final = "prompt"

# GitHub OAuth — VS Code public app (device flow, no secret needed)
GITHUB_DEVICE_CODE_URL: Final = "https://github.com/login/device/code"
GITHUB_TOKEN_URL: Final = "https://github.com/login/oauth/access_token"
GITHUB_CLIENT_ID: Final = "01ab8ac9400c4e429b23"
GITHUB_DEVICE_GRANT: Final = "urn:ietf:params:oauth:grant-type:device_code"

# Defaults
DEFAULT_MODEL: Final = "gpt-4.1"
DEFAULT_NAME: Final = "GitHub Copilot Client"
DEFAULT_CONVERSATION_NAME: Final = "GitHub Copilot Conversation"
DEFAULT_AI_TASK_NAME: Final = "GitHub Copilot AI Task"

DEFAULT_SYSTEM_PROMPT: Final = DEFAULT_INSTRUCTIONS_PROMPT


class SubentryType(StrEnum):
    """Subentry types."""

    CONVERSATION = "conversation"
    AI_TASK = "ai_task_data"
