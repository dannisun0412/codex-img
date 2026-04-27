---
name: codex-img
description: Generate images through an OpenAI-compatible /v1/images/generations endpoint by automatically reading API keys and base URLs from the current Codex environment, including environment variables, ~/.codex/auth.json, and ~/.codex/config.toml. Use when the user asks to generate, draw, paint, create AI art, or save image files with gpt-image models or a custom image generation base URL.
---

# Codex Img

Generate images with the current Codex API configuration, especially OpenAI-compatible providers that expose `/v1/images/generations`.

## Quick Start

Run the bundled script:

```bash
scripts/codex-img generate "给我画一个哆啦A梦版的大模型知识图"
```

Common options:

```bash
scripts/codex-img generate \
  --model gpt-image-2 \
  --size 1792x1024 \
  --out ./image.png \
  "给我画一个哆啦A梦版的大模型知识图"
```

Use `scripts/codex-img` on macOS/Linux and `scripts\codex-img.cmd` on Windows. The launcher resolves a Python 3.11+ runtime in this order: `CODEX_IMG_PYTHON`, `python3`, `python`, then `uv run python` on macOS/Linux; `CODEX_IMG_PYTHON`, `py -3`, `python`, `python3`, then `uv run python` on Windows. Prefer the launcher instead of calling `python` directly. When running from outside the skill folder, call the launcher by absolute path.

Use `--dry-run` to inspect the resolved URL and payload without sending a request. The script redacts secret values.

The script uses Image API streaming by default, saves partial images, sends a browser-like `User-Agent`, logs waiting progress every 30 seconds, and automatically retries once without TLS certificate verification when a custom endpoint fails with `CERTIFICATE_VERIFY_FAILED`. Use `codex_img_insecure = true` in provider config to skip the failed TLS probe for a trusted endpoint, `--no-stream` if a provider behaves badly with SSE, `--strict-tls` to disable TLS fallback, or `--insecure` to skip TLS verification immediately.

The script sends `model` exactly as requested, defaults to `gpt-image-2`, checks `/v1/models/{model}` when the endpoint supports it, and validates response `model` fields when present. Use `--require-model-check` to fail if the endpoint does not expose `/models/{model}`. If the provider does not expose model metadata and omits model fields in image events, the script can only verify the request payload, not the provider's internal routing.

## Configuration

Resolve credentials automatically from the current OS user config:

- macOS/Linux: `~/.codex/auth.json` and `~/.codex/config.toml`
- Windows: `%USERPROFILE%\.codex\auth.json` and `%USERPROFILE%\.codex\config.toml`

Resolve values in this order:

1. CLI flags: `--api-key`, `--base-url`
2. Environment variables: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `base_url`
3. `auth.json`: `OPENAI_API_KEY` and optional `base_url`
4. `config.toml`: top-level `base_url`, selected `model_provider`, matching `[model_providers.<name>]`, `base_url`, `api_key`, `api_key_env_var`, and optional `codex_img_insecure`
5. Default base URL: `https://ai.transferai.cc/v1`

Never print API keys. Never commit local config or generated images unless the user explicitly asks.

If no Python 3.11+ runtime is found, set `CODEX_IMG_PYTHON` to the absolute path of a valid interpreter, for example:

```bash
CODEX_IMG_PYTHON=/opt/homebrew/bin/python3 /path/to/codex-img/scripts/codex-img generate "prompt"
```

## Script Behavior

The script sends:

```json
{
  "model": "gpt-image-2",
  "prompt": "...",
  "size": "1792x1024",
  "response_format": "b64_json",
  "stream": true,
  "partial_images": 2
}
```

It accepts either streamed image events, `data[0].b64_json`, or `data[0].url`, decodes or downloads the image, and saves it to `~/.codex/generated_images/codex-img/` unless `--out` is provided. Partial streamed images are saved next to the target output as `.partial-N` files.

For long image generations, prefer streaming because partial-image events keep the connection active and reduce upstream idle-timeout risk. If an endpoint blocks automation at Cloudflare, the script reports the Cloudflare code and detail instead of a generic HTTP 403.

For strict model validation:

```bash
scripts/codex-img generate --model gpt-image-2 --require-model-check "prompt"
```
