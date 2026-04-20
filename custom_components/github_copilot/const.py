"""Constants for the GitHub Copilot integration."""

from enum import StrEnum
from typing import Final

from homeassistant.const import Platform
from homeassistant.helpers.llm import DEFAULT_INSTRUCTIONS_PROMPT

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
CONF_MAX_HISTORY: Final = "max_history"

# GitHub OAuth — VS Code public app (device flow, no secret needed)
GITHUB_DEVICE_CODE_URL: Final = "https://github.com/login/device/code"
GITHUB_TOKEN_URL: Final = "https://github.com/login/oauth/access_token"
GITHUB_CLIENT_ID: Final = "01ab8ac9400c4e429b23"

# GitHub Copilot Internal API
GITHUB_COPILOT_TOKEN_URL: Final = "https://api.github.com/copilot_internal/v2/token"
GITHUB_COPILOT_API_BASE: Final = "https://api.githubcopilot.com"
GITHUB_COPILOT_MODELS_URL: Final = f"{GITHUB_COPILOT_API_BASE}/models"
GITHUB_COPILOT_CHAT_COMPLETIONS_URL: Final = (
    f"{GITHUB_COPILOT_API_BASE}/chat/completions"
)

# Legacy GitHub Models API (kept for reference)
GITHUB_MODELS_API_BASE: Final = "https://models.github.ai"
GITHUB_MODELS_CATALOG_URL: Final = f"{GITHUB_MODELS_API_BASE}/catalog/models"
GITHUB_CHAT_COMPLETIONS_URL: Final = (
    f"{GITHUB_MODELS_API_BASE}/inference/chat/completions"
)

# Defaults
DEFAULT_MODEL: Final = "gpt-4.1"
DEFAULT_NAME: Final = "GitHub Copilot"
DEFAULT_CONVERSATION_NAME: Final = "GitHub Copilot Conversation"
DEFAULT_AI_TASK_NAME: Final = "GitHub Copilot AI Task"
DEFAULT_MAX_HISTORY: Final = 20
MAX_TOOL_ITERATIONS: Final = 10

DEFAULT_SYSTEM_PROMPT: Final = DEFAULT_INSTRUCTIONS_PROMPT


class SubentryType(StrEnum):
    """Subentry types."""

    CONVERSATION = "conversation"
    AI_TASK = "ai_task_data"
