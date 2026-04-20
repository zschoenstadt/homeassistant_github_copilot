"""Config flow for GitHub Copilot."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import callback
from homeassistant.helpers import llm
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

from .api import GitHubCopilotClient
from .const import (
    CONF_ACCESS_TOKEN,
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

    def __init__(self) -> None:
        """Initialize the config flow."""

        self._device_code: str | None = None
        self._user_code: str | None = None
        self._verification_uri: str | None = None
        self._interval: int = 5
        self._expires_in: int = 900
        self._token_data: dict[str, Any] = {}
        self._models: list[dict[str, str]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — user triggers setup."""

        errors: dict[str, str] = {}

        if user_input is not None:
            # Initiate the OAuth device flow with GitHub
            try:
                device_flow = await GitHubCopilotClient.async_initiate_device_flow()
                self._device_code = device_flow.device_code
                self._user_code = device_flow.user_code
                self._verification_uri = device_flow.verification_uri
                self._interval = device_flow.interval
                self._expires_in = device_flow.expires_in
                return await self.async_step_auth()
            except GitHubCopilotClient.ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during device flow initiation")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the device code and poll for authorization."""

        errors: dict[str, str] = {}

        if user_input is not None:
            # User clicked "submit" — start polling for the token
            try:
                token_resp = await GitHubCopilotClient.async_poll_for_token(
                    device_code=self._device_code,
                    interval=self._interval,
                    expires_in=self._expires_in,
                )

                # Store the token data for the next step
                self._token_data = {
                    CONF_ACCESS_TOKEN: token_resp.access_token,
                    CONF_REFRESH_TOKEN: token_resp.refresh_token,
                }
                if token_resp.expires_in:
                    self._token_data[CONF_TOKEN_EXPIRY] = (
                        datetime.now() + timedelta(seconds=token_resp.expires_in)
                    ).isoformat()

                return await self.async_step_model()

            except GitHubCopilotClient.AuthError as err:
                if "timed out" in str(err).lower():
                    errors["base"] = "auth_timeout"
                elif "denied" in str(err).lower():
                    errors["base"] = "auth_denied"
                else:
                    errors["base"] = "unknown"
            except GitHubCopilotClient.ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during token polling")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="auth",
            data_schema=vol.Schema({}),
            description_placeholders={
                "verification_uri": self._verification_uri or "",
                "user_code": self._user_code or "",
            },
            errors=errors,
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a default model."""

        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate user has access to the selected model
            try:
                client = GitHubCopilotClient(
                    access_token=self._token_data[CONF_ACCESS_TOKEN],
                )
                has_access = await client.async_validate_model(user_input[CONF_MODEL])
                await client.async_close()

                if has_access:
                    self._token_data[CONF_MODEL] = user_input[CONF_MODEL]
                    return self.async_create_entry(
                        title="GitHub Copilot",
                        data=self._token_data,
                    )

                errors["base"] = "model_no_access"
            except GitHubCopilotClient.AuthError:
                errors["base"] = "unknown"
            except GitHubCopilotClient.ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating model")
                errors["base"] = "unknown"

        # Fetch available models for the dropdown
        if not self._models:
            try:
                client = GitHubCopilotClient(
                    access_token=self._token_data[CONF_ACCESS_TOKEN],
                )
                models = await client.async_list_models()
                self._models = [{"id": m.id, "name": m.name} for m in models]
                await client.async_close()
            except Exception:
                _LOGGER.exception("Failed to fetch models, using default")
                self._models = [{"id": DEFAULT_MODEL, "name": DEFAULT_MODEL}]

        # Build the model selection form
        model_options = {m["id"]: m["name"] for m in self._models}
        if not model_options:
            model_options = {DEFAULT_MODEL: DEFAULT_MODEL}

        return self.async_show_form(
            step_id="model",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MODEL, default=DEFAULT_MODEL): vol.In(
                        model_options
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauth when token refresh fails."""

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth and re-run device flow."""

        if user_input is not None:
            # Initiate a new device flow for re-authorization
            try:
                device_flow = await GitHubCopilotClient.async_initiate_device_flow()
                self._device_code = device_flow.device_code
                self._user_code = device_flow.user_code
                self._verification_uri = device_flow.verification_uri
                self._interval = device_flow.interval
                self._expires_in = device_flow.expires_in
                return await self.async_step_reauth_auth()
            except GitHubCopilotClient.ConnectionError:
                return self.async_show_form(
                    step_id="reauth_confirm",
                    errors={"base": "cannot_connect"},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
        )

    async def async_step_reauth_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth device flow polling."""

        errors: dict[str, str] = {}

        if user_input is not None:
            # Poll for the new token
            try:
                token_resp = await GitHubCopilotClient.async_poll_for_token(
                    device_code=self._device_code,
                    interval=self._interval,
                    expires_in=self._expires_in,
                )

                # Merge the new credentials into the existing entry
                reauth_entry = self._get_reauth_entry()
                new_data = {
                    **reauth_entry.data,
                    CONF_ACCESS_TOKEN: token_resp.access_token,
                    CONF_REFRESH_TOKEN: token_resp.refresh_token,
                }
                if token_resp.expires_in:
                    new_data[CONF_TOKEN_EXPIRY] = (
                        datetime.now() + timedelta(seconds=token_resp.expires_in)
                    ).isoformat()

                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data=new_data,
                    reason="reauth_successful",
                )

            except GitHubCopilotClient.AuthError as err:
                if "denied" in str(err).lower():
                    errors["base"] = "auth_denied"
                else:
                    errors["base"] = "auth_timeout"
            except GitHubCopilotClient.ConnectionError:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reauth_auth",
            data_schema=vol.Schema({}),
            description_placeholders={
                "verification_uri": self._verification_uri or "",
                "user_code": self._user_code or "",
            },
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> GitHubCopilotOptionsFlow:
        """Get the options flow."""

        return GitHubCopilotOptionsFlow(config_entry)


class GitHubCopilotOptionsFlow(OptionsFlow):
    """Handle options for GitHub Copilot."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""

        self._config_entry = config_entry
        self._models: list[dict[str, str]] = []

    async def _async_fetch_models(self) -> dict[str, str]:
        """Fetch model list and return as {id: name} dict, always including current."""

        current_model = self._config_entry.options.get(
            CONF_MODEL,
            self._config_entry.data.get(CONF_MODEL, DEFAULT_MODEL),
        )
        model_options: dict[str, str] = {}

        # Try fetching the full model catalog
        try:
            client: GitHubCopilotClient = self._config_entry.runtime_data
            models = await client.async_list_models()
            model_options = {m.id: m.name for m in models}
        except (
            GitHubCopilotClient.ApiError,
            GitHubCopilotClient.ConnectionError,
            GitHubCopilotClient.AuthError,
        ) as err:
            _LOGGER.warning("Failed to fetch models for options flow: %s", err)

        # Always include the currently configured model
        if current_model not in model_options:
            model_options[current_model] = current_model

        return model_options

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options."""

        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate model access if the user changed the model
            current_model = self._config_entry.options.get(
                CONF_MODEL,
                self._config_entry.data.get(CONF_MODEL, DEFAULT_MODEL),
            )
            new_model = user_input.get(CONF_MODEL, current_model)

            if new_model != current_model:
                try:
                    client: GitHubCopilotClient = self._config_entry.runtime_data
                    has_access = await client.async_validate_model(new_model)
                    if not has_access:
                        errors["base"] = "model_no_access"
                except (
                    GitHubCopilotClient.ApiError,
                    GitHubCopilotClient.ConnectionError,
                    GitHubCopilotClient.AuthError,
                ) as err:
                    _LOGGER.warning(
                        "Model validation failed for %s: %s",
                        new_model,
                        err,
                    )

            if not errors:
                return self.async_create_entry(title="", data=user_input)

        # Gather current settings and available choices
        model_options = await self._async_fetch_models()
        current_model = self._config_entry.options.get(
            CONF_MODEL,
            self._config_entry.data.get(CONF_MODEL, DEFAULT_MODEL),
        )

        # Get available LLM APIs for "Control Home Assistant"
        hass_llm_apis = [
            SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]
        selected_llm_apis = self._config_entry.options.get(CONF_LLM_HASS_API, [])

        # Build and show the options form
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_MODEL,
                        default=current_model,
                    ): vol.In(model_options),
                    vol.Optional(
                        CONF_PROMPT,
                        description={
                            "suggested_value": self._config_entry.options.get(
                                CONF_PROMPT,
                                self._config_entry.data.get(
                                    CONF_PROMPT, DEFAULT_SYSTEM_PROMPT
                                ),
                            ),
                        },
                    ): TemplateSelector(),
                    vol.Optional(
                        CONF_LLM_HASS_API,
                        description={"suggested_value": selected_llm_apis},
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=hass_llm_apis,
                            multiple=True,
                        )
                    ),
                    vol.Optional(
                        CONF_MAX_HISTORY,
                        description={
                            "suggested_value": self._config_entry.options.get(
                                CONF_MAX_HISTORY, DEFAULT_MAX_HISTORY
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
            errors=errors,
        )
