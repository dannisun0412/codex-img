# codex-img

Codex skill for generating images with an OpenAI-compatible `/v1/images/generations` endpoint.

## Install

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/dannisun0412/codex-img.git ~/.codex/skills/codex-img
```

Restart Codex after installing the skill.

## Configure

Set environment variables:

```bash
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://ai.transferai.cc/v1"
```

Or configure Codex:

```toml
model_provider = "transferai"

[model_providers.transferai]
base_url = "https://ai.transferai.cc/v1"
api_key_env_var = "TRANSFERAI_API_KEY"
```

## Use

```bash
python scripts/codex_img.py generate \
  --model gpt-image-2 \
  --size 1792x1024 \
  "给我画一个哆啦A梦版的大模型知识图"
```

Use `--dry-run` to preview the request without calling the API.
