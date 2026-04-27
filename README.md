# codex-img

Codex skill for generating and editing images with OpenAI-compatible `/v1/images/generations` and `/v1/images/edits` endpoints.

## Install

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/dannisun0412/codex-img.git ~/.codex/skills/codex-img
```

Restart Codex after installing the skill.

## Configure

The skill automatically reads the current user's Codex config:

- macOS/Linux: `~/.codex/auth.json` and `~/.codex/config.toml`
- Windows: `%USERPROFILE%\.codex\auth.json` and `%USERPROFILE%\.codex\config.toml`

Expected fields:

- `OPENAI_API_KEY` from `auth.json` or the environment
- `base_url` from `config.toml`, `auth.json`, or the environment

You can also set environment variables:

```bash
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://ai.transferai.cc/v1"
# or:
export base_url="https://ai.transferai.cc/v1"
```

Or configure Codex:

```toml
model_provider = "transferai"

[model_providers.transferai]
base_url = "https://ai.transferai.cc/v1"
api_key_env_var = "TRANSFERAI_API_KEY"
```

For a trusted custom gateway with local certificate-chain issues, configure this once instead of passing `--insecure`:

```toml
[model_providers.transferai]
base_url = "https://ai.transferai.cc/v1"
api_key_env_var = "TRANSFERAI_API_KEY"
codex_img_insecure = true
```

## Use

```bash
scripts/codex-img generate \
  --model gpt-image-2 \
  --size 1792x1024 \
  "给我画一个哆啦A梦版的大模型知识图"
```

Windows:

```bat
scripts\codex-img.cmd generate --model gpt-image-2 --size 1792x1024 "给我画一个哆啦A梦版的大模型知识图"
```

The launcher chooses a Python 3.11+ runtime automatically. Set `CODEX_IMG_PYTHON` to force a specific Python executable:

```bash
CODEX_IMG_PYTHON=/opt/homebrew/bin/python3 scripts/codex-img generate "prompt"
```

Use `--dry-run` to preview the request without calling the API.

Edit or transform an existing image:

```bash
scripts/codex-img generate \
  --image ./input.png \
  --size 1024x1024 \
  --out ./edited.png \
  "Change the background to deep blue while preserving the product"
```

Use multiple reference images by repeating `--image`. Use `--mask ./mask.png` for masked edits when the provider supports masks.

Reliability defaults:

- Request payload always includes the selected `model`; default is `gpt-image-2`.
- `--image` automatically switches from `/images/generations` to `/images/edits`.
- `/v1/models/{model}` preflight runs when the endpoint supports it.
- Response `model` fields are validated when present.
- Image API streaming is enabled by default for `gpt-image-2`.
- Partial images are saved as `name.partial-1.png`, `name.partial-2.png`, etc.
- If a provider rejects streaming parameters, the script falls back to a normal request.
- Browser-like `User-Agent` header for custom gateways.
- Waiting progress log every 30 seconds.
- Automatic one-time TLS fallback for local `CERTIFICATE_VERIFY_FAILED` issues.
- Optional `codex_img_insecure = true` config to skip the failed TLS probe for trusted gateways.
- Clear Cloudflare block messages when a gateway rejects automated traffic.

Advanced flags:

```bash
# Fail instead of auto-fallback when TLS certificate verification fails.
scripts/codex-img generate --strict-tls "prompt"

# Disable TLS verification immediately for a trusted custom endpoint.
scripts/codex-img generate --insecure "prompt"

# Change waiting log cadence, or disable it with 0.
scripts/codex-img generate --progress-interval 10 "prompt"

# Disable streaming when a provider behaves badly with SSE.
scripts/codex-img generate --no-stream "prompt"

# Faster/lighter generation for slow gateways.
scripts/codex-img generate --quality low --size 1024x1024 "prompt"

# Fail if the endpoint cannot confirm the model through /models/{model}.
scripts/codex-img generate --model gpt-image-2 --require-model-check "prompt"
```

Note: `/v1/models/{model}` confirms that a compatible endpoint exposes the model ID. It cannot prove a third-party gateway's internal routing if that gateway aliases or hides model metadata. The script also validates response `model` fields when the gateway returns them.
