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
- **Docker (x86_64)**: A custom container image is required — see [Docker Deployment](#docker-deployment) below

## Docker Deployment

The Copilot SDK ships a native CLI binary linked against **glibc**. Home Assistant's official Docker image uses **Alpine Linux (musl libc)**, so the binary won't run out of the box. This project includes a `docker/Dockerfile` that builds a custom HA image with the necessary glibc compatibility layer.

### Building the Custom Image

```bash
# Clone this repo
git clone https://github.com/zschoenstadt/homeassistant_github_copilot.git
cd homeassistant_github_copilot

# Build (defaults to HA stable, SDK 0.2.2)
docker build -t ha-copilot -f docker/Dockerfile .

# Or pin a specific HA version and SDK version
docker build -t ha-copilot -f docker/Dockerfile \
  --build-arg HA_VERSION=2026.4.3 \
  --build-arg SDK_VERSION=0.2.2 .
```

### Running

Replace your existing HA container image with `ha-copilot`. Everything else stays the same:

```bash
docker run -d --name homeassistant \
  --restart=unless-stopped \
  -v /path/to/ha-config:/config \
  -e TZ=America/New_York \
  -p 8123:8123 \
  ha-copilot
```

> **Note:** When Home Assistant releases a new version, rebuild the image with the updated `HA_VERSION` build arg.

### What the Dockerfile Does

Extends the official HA Alpine image with glibc compatibility:

1. **gcompat + libstdc++ + libucontext** — Alpine packages providing the glibc ABI shim, C++ standard library, and context-switching functions
2. **glibc shim library** — A small compiled shim (`glibc_shim.so`) providing `fcntl64` and `gnu_get_libc_version` — symbols the CLI binary expects but gcompat doesn't cover. On musl, `fcntl` is already 64-bit, so `fcntl64` is a simple forwarding wrapper.
3. **Copilot SDK** — Downloads and extracts the `github-copilot-sdk` manylinux wheel (which bundles the CLI binary). Alpine's pip rejects manylinux platform tags, so the wheel is extracted directly.

### Updating the SDK

To update to a newer SDK version, rebuild with the new version:

```bash
docker build -t ha-copilot -f docker/Dockerfile \
  --build-arg SDK_VERSION=0.2.3 .
```

## Installation

### HACS (Recommended)

> **Important:** You must be running the custom Docker image (see above) for the SDK binary to work.

1. Open HACS in Home Assistant
2. Click **Integrations** → **⋮** → **Custom repositories**
3. Add this repository URL and select **Integration**
4. Install "GitHub Copilot"
5. Restart Home Assistant

### Manual

1. Ensure you're running the custom Docker image (see [Docker Deployment](#docker-deployment))
2. Copy `custom_components/github_copilot/` to your HA `config/custom_components/` directory
3. Restart Home Assistant

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


