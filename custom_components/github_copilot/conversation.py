"""Conversation entity for GitHub Copilot."""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_LLM_HASS_API, CONF_PROMPT, DEFAULT_CONVERSATION_NAME, DOMAIN
from .entity import GitHubCopilotBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up conversation entities."""

    async_add_entities(
        [GitHubCopilotConversationEntity(config_entry)],
    )


class GitHubCopilotConversationEntity(
    conversation.ConversationEntity,
    GitHubCopilotBaseEntity,
):
    """GitHub Copilot conversation agent."""

    _attr_name = DEFAULT_CONVERSATION_NAME
    _attr_supports_streaming = True

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the conversation entity."""

        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_conversation"
        self._update_supported_features()

    def _update_supported_features(self) -> None:
        """Update supported features based on options."""

        if self.entry.options.get(CONF_LLM_HASS_API):
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )
        else:
            self._attr_supported_features = conversation.ConversationEntityFeature(0)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""

        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """When entity is added to Home Assistant."""

        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)
        self.async_on_remove(
            self.entry.add_update_listener(self._async_entry_update_listener)
        )

    @staticmethod
    async def _async_entry_update_listener(
        hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Handle options updates (ignore data-only changes like token refresh)."""

        await hass.config_entries.async_reload(entry.entry_id)

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from Home Assistant."""

        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Process a conversation message."""

        # Provide LLM context so tools and prompts are available
        options = self.entry.options
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                user_llm_hass_api=options.get(CONF_LLM_HASS_API),
                user_llm_prompt=options.get(CONF_PROMPT),
                user_extra_system_prompt=user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        # Send through the Copilot API
        try:
            await self._async_handle_chat_log(chat_log)
        except Exception as err:
            raise HomeAssistantError(
                f"Error getting response from GitHub Copilot: {err}"
            ) from err

        return conversation.async_get_result_from_chat_log(user_input, chat_log)
