# GitHub Copilot integration for Home Assistant

> **⚠️ AI-Generated Code Disclaimer:** This project was developed with assistance from AI tools. While I do not consider this project to have been 'Vibe Coded', I understand opinions may differ on what that even means; I have designed, reviewed, and tested the using normal engineering processes, treating the Agent as a Lead developer might treat a Junior developer. Some tasks the agent was left to its devices, where others were under strict scrutiny and subject to intervention. I provide this disclaimer to satisfy my own moral and ethical hang-ups regarding the real life harm unrestrained capitalism is doing in the name of AI advancement.

 The Github Copilot integration adds a conversation agent powered by GitHub Copilot in Home Assistant.

## Features

- **Conversation Entity** — Use GitHub Copilot as a conversation agent in Home Assistant's Assist pipeline
- **AI Task Entity** — Generate data (text or structured JSON) via the `ai_task.generate_data` service
- **OAuth Device Flow** — Secure authentication via GitHub's device flow (no secrets in config files)
- **Automatic Token Refresh** — Tokens refresh transparently; re-auth only when refresh fails

## Requirements

- Home Assistant 2025.7.0+
- A GitHub account with an active Copilot subscription

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click **Integrations** → **⋮** → **Custom repositories**
3. Add this repository URL and select **Integration**
4. Install "GitHub Copilot"
5. Restart Home Assistant

### Manual

1. Copy `custom_components/github_copilot/` to your HA `config/custom_components/` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for "GitHub Copilot"
3. Click **Submit** to start the device flow
4. Visit the URL shown and enter the code
5. After authorization, select your preferred model
6. Done!

## Configuration

After setup, you can change settings via **Options** (click **Configure** on the integration card):

- **Model** — Select which GitHub Copilot model to use for chat completions (e.g., `gpt-4.1`, `gpt-4.1-mini`, `claude-sonnet-4`, etc.)
- **Instructions** — System prompt that instructs how the LLM should respond. Supports Home Assistant templates.
- **Control Home Assistant** — Enable tool use to let the model interact with Home Assistant (e.g., control devices, query states) via the Assist API.
- **Max history messages** — Number of previous conversation turns to include as context. Set to `0` for unlimited.

## Usage

### As a Conversation Agent

Select "GitHub Copilot" as your conversation agent in **Settings** → **Voice Assistants**, or use it directly in the Assist pipeline.

### AI Task Service

```yaml
service: ai_task.generate_data
data:
  task_name: describe_scene
  instructions: "Describe what's happening based on the sensor data"
```

## Development

### Code Style (PEP 8)

This project enforces PEP 8 style via [Ruff](https://docs.astral.sh/ruff/). A pre-commit git hook blocks commits with lint or formatting violations.

Activate the hook (one-time per clone):

```bash
git config core.hooksPath .githooks
```

This points git to the `.githooks/` directory in the repo, which contains a pre-commit hook that blocks commits with lint or formatting violations.

To fix violations:

```bash
ruff format .         # auto-format code
ruff check --fix .    # auto-fix lint issues
```

To suppress a specific violation inline:
- **Lint**: `# noqa: <RULE>` (e.g., `# noqa: E501`)
- **Formatting**: wrap with `# fmt: off` / `# fmt: on`

### Running Tests

```bash
# Install dev dependencies
pip install pytest pytest-asyncio pytest-homeassistant-custom-component aioresponses pytest-cov

# Run all tests
pytest tests/ -x --cov=custom_components/github_copilot

# Run a single test
pytest tests/test_api.py::test_chat_completion_basic -xvs

# Run with coverage threshold
pytest tests/ --cov=custom_components/github_copilot --cov-fail-under=80
```


