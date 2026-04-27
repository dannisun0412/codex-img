---
name: codex-img
description: Generate images through an OpenAI-compatible /v1/images/generations endpoint by automatically reading API keys and base URLs from the current Codex environment, including environment variables, ~/.codex/auth.json, and ~/.codex/config.toml. Use when the user asks to generate, draw, paint, create AI art, or save image files with gpt-image models or a custom image generation base URL.
---

# Codex Img

Generate images with the current Codex API configuration, especially OpenAI-compatible providers that expose `/v1/images/generations`.

## Quick Start

Run the bundled script:

```bash
python scripts/codex_img.py generate "给我画一个哆啦A梦版的大模型知识图"
```

Common options:

```bash
python scripts/codex_img.py generate \
  --model gpt-image-2 \
  --size 1792x1024 \
  --out ./image.png \
  "给我画一个哆啦A梦版的大模型知识图"
```

Use `--dry-run` to inspect the resolved URL and payload without sending a request. The script redacts secret values.

The script sends a browser-like `User-Agent`, logs waiting progress every 30 seconds, and automatically retries once without TLS certificate verification when a custom endpoint fails with `CERTIFICATE_VERIFY_FAILED`. Use `--strict-tls` to disable that fallback, or `--insecure` to skip TLS verification immediately for a trusted endpoint.

## Configuration

Resolve credentials automatically from the current OS user config:

- macOS/Linux: `~/.codex/auth.json` and `~/.codex/config.toml`
- Windows: `%USERPROFILE%\.codex\auth.json` and `%USERPROFILE%\.codex\config.toml`

Resolve values in this order:

1. CLI flags: `--api-key`, `--base-url`
2. Environment variables: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `base_url`
3. `auth.json`: `OPENAI_API_KEY` and optional `base_url`
4. `config.toml`: top-level `base_url`, selected `model_provider`, matching `[model_providers.<name>]`, `base_url`, `api_key`, or `api_key_env_var`
5. Default base URL: `https://ai.transferai.cc/v1`

Never print API keys. Never commit local config or generated images unless the user explicitly asks.

## Script Behavior

The script sends:

```json
{
  "model": "gpt-image-2",
  "prompt": "...",
  "size": "1792x1024",
  "response_format": "b64_json"
}
```

It accepts either `data[0].b64_json` or `data[0].url`, decodes or downloads the image, and saves it to `~/.codex/generated_images/codex-img/` unless `--out` is provided.

For long image generations, keep waiting while the script logs `waiting for image generation`. If an endpoint blocks automation at Cloudflare, the script reports the Cloudflare code and detail instead of a generic HTTP 403.
