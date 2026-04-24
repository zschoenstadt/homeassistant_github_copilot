# Copilot Instructions for ha_github_copilot

## Project Overview

This is a **Home Assistant custom integration** (`github_copilot`) that connects GitHub Copilot as a conversation agent and AI task provider. It uses direct HTTP API calls (aiohttp) to GitHub's Models REST API — no SDK binary dependency.

## Architecture

```
custom_components/github_copilot/
├── __init__.py       # Entry setup: creates API client, forwards platforms
├── api.py            # GitHubCopilotClient: OAuth device flow, token refresh, chat completions
├── config_flow.py    # OAuth device flow UI: user → auth → model select → entry creation
├── entity.py         # GitHubCopilotBaseEntity: shared LLM logic, ChatLog→messages conversion
├── conversation.py   # ConversationEntity: _async_handle_message → API → ConversationResult
├── ai_task.py        # AITaskEntity: _async_generate_data → API → GenDataTaskResult
├── const.py          # Domain, URLs, config keys, defaults
```

The integration follows the same patterns as `openai_conversation` in HA core. When in doubt, reference that integration for guidance.

## Key Patterns

- **Entity base class**: `GitHubCopilotBaseEntity` in `entity.py` contains shared logic for converting `ChatLog` to API messages and calling the chat completions endpoint. Both `conversation.py` and `ai_task.py` inherit from it.
- **Auth flow**: OAuth Device Flow implemented in `config_flow.py`. Tokens stored in config entry data (HA's encrypted `.storage/`). Token refresh handled in `api.py`.
- **Config flow error recovery**: Auth errors during the device flow abort immediately — the user re-opens the flow to retry. Connection/timeout errors show a retry form (`login_timeout`) that loops back to `async_step_user` so the user can retry without restarting. Model timeout (`model_timeout`) follows the same pattern. This matches common HA core integration patterns.
- **Error hierarchy**: `GitHubCopilotClient` defines `AuthError`, `ConnectionError`, `RateLimitError`, `ApiError`. The `__init__.py` maps `AuthError` → `ConfigEntryAuthFailed` and `ConnectionError` → `ConfigEntryNotReady`.

## Build / Test / Lint

```bash
# Run all tests
pytest tests/ -x --cov=custom_components/github_copilot --cov-report=term-missing

# Run single test file
pytest tests/test_api.py -xvs

# Run single test
pytest tests/test_conversation.py::test_handle_message_success -xvs

# Coverage threshold (CI)
pytest tests/ --cov=custom_components/github_copilot --cov-fail-under=80
```

Test dependencies: `pytest`, `pytest-asyncio`, `pytest-homeassistant-custom-component`, `aioresponses`, `pytest-cov`

## Code Style

This project enforces formatting rules on top of ruff and pylint. These rules apply to **every** function, method, and code block in both source and test files.

### Blank Line After Docstring

Every function and method must have a blank line between its docstring and the first line of code. This creates breathing room between the signature block and the function body. Applies universally — even to small functions. `D202` is intentionally disabled in our ruff config to allow this.

### Code Paragraphs

Function bodies are organized into **paragraphs** — logical groups of lines separated by blank lines, like paragraphs in prose. Each paragraph represents one phase or step of the function's work.

1. **Separate paragraphs with a single blank line.** Every distinct logical phase gets its own paragraph.
2. **Non-trivial paragraphs get a comment header.** At minimum, a single-line comment above the paragraph explaining *what* it does. Use multiple comment lines when the paragraph's purpose, context, or reasoning needs more explanation.
3. **Trivial/obvious blocks skip the comment.** A lone `return`, a simple assignment, or a single obvious statement doesn't need a comment.
4. **Comments describe the *what*, not the *how*.** Write `# Build the request payload` not `# Create a dict and add keys to it`.

### Judgment Calls

- **When in doubt, add a blank line.** Over-separating is better than a wall of code.
- **Don't over-comment.** A `return result` or `raise SomeError(...)` at the end of a block doesn't need its own comment. The paragraph header covers it.
- **Formatting vs. refactoring.** If a function is hard to read, first ask: is this a formatting problem or a structural problem? Apply paragraph formatting first. If it's still hard to follow, the function likely needs refactoring (extract helper functions, simplify control flow, etc.).

## Conventions

- **Branching**: When working on a new feature, use or create a branch named `copilot/<appropriate-feature-name>` before making changes.
- All HTTP is mocked via `aioresponses` in tests — no real network calls
- Config entry `runtime_data` holds the `GitHubCopilotClient` instance (typed via `ConfigEntry[GitHubCopilotClient]`)
- Fixtures are in `tests/conftest.py`: `mock_config_entry`, `mock_client`, `mock_setup_entry`
- Mock API response constants (e.g., `MOCK_CHAT_COMPLETION_RESPONSE`) are defined in `conftest.py` and imported by test modules
- String translations go in both `strings.json` (source of truth) and `translations/en.json`

## GitHub API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST https://github.com/login/device/code` | Initiate device flow |
| `POST https://github.com/login/oauth/access_token` | Exchange/refresh tokens |
| `POST https://api.github.com/copilot_internal/v2/token` | Exchange OAuth token for Copilot API token |
| `GET https://api.githubcopilot.com/models` | List available models |
| `POST https://api.githubcopilot.com/chat/completions` | Chat completions |
