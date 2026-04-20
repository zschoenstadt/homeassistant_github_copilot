"""AI Task entity for GitHub Copilot."""

from __future__ import annotations

import json
import logging

from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DEFAULT_AI_TASK_NAME
from .entity import GitHubCopilotBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up AI Task entities."""

    async_add_entities(
        [GitHubCopilotAITaskEntity(config_entry)],
    )


class GitHubCopilotAITaskEntity(
    ai_task.AITaskEntity,
    GitHubCopilotBaseEntity,
):
    """GitHub Copilot AI Task entity."""

    _attr_name = DEFAULT_AI_TASK_NAME

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the AI Task entity."""

        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_ai_task"
        self._attr_supported_features = ai_task.AITaskEntityFeature.GENERATE_DATA

    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Handle a generate data task."""

        # Send the chat log through the Copilot API
        try:
            await self._async_handle_chat_log(
                chat_log,
                structure=task.structure if hasattr(task, "structure") else None,
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Error generating data with GitHub Copilot: {err}"
            ) from err

        # Verify the last content entry is from the assistant
        if not isinstance(chat_log.content[-1], conversation.AssistantContent):
            _LOGGER.error(
                "Last content in chat log is not AssistantContent: %s",
                chat_log.content[-1],
            )
            raise HomeAssistantError("Error getting response from GitHub Copilot")

        text = chat_log.content[-1].content or ""

        # Return plain text if no structured output was requested
        if not hasattr(task, "structure") or not task.structure:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id,
                data=text,
            )

        # Parse the response as JSON for structured output
        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            _LOGGER.error(
                "Failed to parse JSON response: %s. Response: %s",
                err,
                text,
            )
            raise HomeAssistantError(
                "Error getting response from GitHub Copilot"
            ) from err

        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )
