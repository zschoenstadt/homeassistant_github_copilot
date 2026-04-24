"""Config flow for GitHub Copilot."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TemplateSelector,
)
import voluptuous as vol

from .api import (
    GitHubCopilotAuth,
    GitHubCopilotAuthError,
    GitHubCopilotConnectionError,
    GitHubCopilotDeviceFlow,
    GitHubCopilotModel,
    GitHubCopilotSDKClient,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_LLM_HASS_API,
    CONF_MAX_HISTORY,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRY,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class GitHubCopilotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GitHub Copilot."""

    VERSION = 1

    def __init__(self, *args, **kwargs) -> None:
        """Initialize the config flow."""

        super().__init__(*args, **kwargs)
        self._device_flow: GitHubCopilotDeviceFlow | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expiry: int | None = None
        self._sdk_client: GitHubCopilotSDKClient | None = None
        self._models: list[GitHubCopilotModel] = []

    async def _async_cleanup_sdk_client(self) -> None:
        """Stop the temporary SDK client if active."""

        if self._sdk_client is not None:
            await self._sdk_client.async_stop()
            self._sdk_client = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — user triggers setup."""

        # Initiate the OAuth device flow with GitHub
        if self._device_flow is None:
            try:
                session = async_get_clientsession(self.hass)
                self._device_flow = await GitHubCopilotDeviceFlow.async_initiate(
                    session
                )
            except GitHubCopilotConnectionError:
                _LOGGER.exception("Connection error during device flow initiation.")
                return self.async_abort(reason="cannot_connect")
            except Exception:
                _LOGGER.exception("Unexpected error during device flow initiation")
                return self.async_abort(reason="unknown")

        # User clicked "Submit", Start polling for the results
        if user_input is not None:
            _LOGGER.debug("Creating task to poll for device activation")
            try:
                auth = await self._device_flow.async_device_activation()
            except GitHubCopilotConnectionError:
                return await self.async_step_login_timeout()
            except GitHubCopilotAuthError:
                return self.async_abort(reason="auth_failure")
            except Exception as ex:
                _LOGGER.error("Unexpected error during device activation.", exc_info=ex)
                return self.async_abort(reason="unknown_cannot_login")

            # Store auth credentials for later entry creation
            self._access_token = auth.access_token
            self._refresh_token = auth.refresh_token
            self._token_expiry = auth.expiry

            return await self.async_step_model()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            description_placeholders={
                "verification_uri": self._device_flow.verification_uri,
                "user_code": self._device_flow.user_code,
            },
        )

    async def async_step_login_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle timeout failures. show error and allow retry."""

        if user_input is None:
            return self.async_show_form(step_id="login_timeout")

        return await self.async_step_user()

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a default model."""

        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate the selected model exists in the list
            selected_model = user_input[CONF_MODEL]
            if any(m.id == selected_model for m in self._models):
                await self._async_cleanup_sdk_client()
                return self.async_create_entry(
                    title="GitHub Copilot Client",
                    data={
                        CONF_ACCESS_TOKEN: self._access_token,
                        CONF_REFRESH_TOKEN: self._refresh_token,
                        CONF_TOKEN_EXPIRY: self._token_expiry,
                    },
                    options={
                        CONF_MODEL: selected_model,
                    },
                )
            errors[CONF_MODEL] = "model_no_access"

        # Start a temporary SDK client to list models
        if not self._models:
            try:
                if self._sdk_client is None:
                    # Create a temporary auth for the config flow SDK client
                    temp_auth = GitHubCopilotAuth(
                        async_get_clientsession(self.hass),
                        access_token=self._access_token,
                        refresh_token=self._refresh_token,
                        expiry=self._token_expiry,
                    )
                    self._sdk_client = GitHubCopilotSDKClient(auth=temp_auth)
                    await self._sdk_client.async_start()

                self._models = await self._sdk_client.async_list_models()
            except GitHubCopilotAuthError:
                await self._async_cleanup_sdk_client()
                return self.async_abort(reason="auth_failure")
            except GitHubCopilotConnectionError:
                await self._async_cleanup_sdk_client()
                return await self.async_step_model_timeout()
            except Exception:
                _LOGGER.exception("Failed to fetch models.")
                await self._async_cleanup_sdk_client()
                return self.async_abort(reason="unknown_cannot_fetch_models")

        # Build the model selection form
        default_model = self._models[0].id if self._models else DEFAULT_MODEL
        model_choices = (
            {m.id: m.name for m in self._models}
            if self._models
            else {DEFAULT_MODEL: DEFAULT_MODEL + " (default)"}
        )

        return self.async_show_form(
            step_id="model",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MODEL, default=default_model): vol.In(
                        model_choices
                    ),
                }
            ),
        )

    async def async_step_model_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle model validation failures. show error and allow retry."""

        if user_input is None:
            return self.async_show_form(step_id="model_timeout")

        return await self.async_step_model()

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> GitHubCopilotOptionsFlow:
        """Get the options flow."""

        return GitHubCopilotOptionsFlow()


class GitHubCopilotOptionsFlow(OptionsFlow):
    """Handle options for GitHub Copilot."""

    def __init__(self) -> None:
        """Initialize the options flow."""

        super().__init__()
        self._models: list[GitHubCopilotModel] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options."""

        errors: dict[str, str] = {}
        current_model = self.config_entry.options.get(CONF_MODEL)
        sdk_client: GitHubCopilotSDKClient = self.config_entry.runtime_data.sdk_client

        if user_input is not None:
            new_model = user_input.get(CONF_MODEL, current_model)

            # Validate model exists if changed
            if new_model != current_model:
                try:
                    if not await sdk_client.async_validate_model(new_model):
                        errors[CONF_MODEL] = "model_no_access"
                except Exception:
                    _LOGGER.exception("Model validation failed for %s", new_model)
                    errors[CONF_MODEL] = "model_no_access"

            if not errors:
                return self.async_create_entry(title="", data=user_input)

        # Fetch available models for the dropdown
        if not self._models:
            try:
                self._models = await sdk_client.async_list_models()
            except Exception:
                _LOGGER.exception("Failed to fetch models.")
                errors[CONF_MODEL] = "cannot_fetch_models"

        # Get available LLM APIs for "Control Home Assistant"
        hass_llm_apis = [
            SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]
        selected_llm_apis = self.config_entry.options.get(CONF_LLM_HASS_API, [])

        # Build and show the options form
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_MODEL, default=current_model): vol.In(
                        {m.id: m.name for m in self._models}
                    ),
                    vol.Optional(
                        CONF_PROMPT,
                        description={
                            "suggested_value": self.config_entry.options.get(
                                CONF_PROMPT, DEFAULT_SYSTEM_PROMPT
                            ),
                        },
                    ): TemplateSelector(),
                    vol.Optional(
                        CONF_LLM_HASS_API,
                        description={"suggested_value": selected_llm_apis},
                    ): SelectSelector(
                        SelectSelectorConfig(options=hass_llm_apis, multiple=True)
                    ),
                    vol.Optional(
                        CONF_MAX_HISTORY,
                        description={
                            "suggested_value": self.config_entry.options.get(
                                CONF_MAX_HISTORY,
                                DEFAULT_MAX_HISTORY,
                            ),
                        },
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
