#!/usr/bin/env python3
"""Generate images through an OpenAI-compatible Images API endpoint."""
from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, NoReturn
from urllib import error, request

try:
    import tomllib
except ModuleNotFoundError as exc:
    raise SystemExit("Python 3.11+ is required") from exc


DEFAULT_BASE_URL = "https://ai.transferai.cc/v1"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "1792x1024"
DEFAULT_RESPONSE_FORMAT = "b64_json"
DEFAULT_TIMEOUT = 600
DEFAULT_PROGRESS_INTERVAL = 30
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def log(message: str) -> None:
    print(f"[codex-img] {message}", file=sys.stderr)


def fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / ".codex"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Failed to read {path}: {exc}")


def read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        fail(f"Failed to read {path}: {exc}")


def first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def selected_provider(config: dict[str, Any]) -> dict[str, Any]:
    providers = config.get("model_providers")
    if not isinstance(providers, dict):
        return {}

    selected = config.get("model_provider") or config.get("provider")
    if isinstance(selected, str) and isinstance(providers.get(selected), dict):
        return providers[selected]

    for provider in providers.values():
        if isinstance(provider, dict) and provider.get("base_url"):
            return provider
    return {}


def provider_api_key(provider: dict[str, Any]) -> str | None:
    env_name = first_string(provider.get("api_key_env_var"), provider.get("env_key"))
    if env_name:
        value = os.environ.get(env_name)
        if value:
            return value.strip()
    return first_string(provider.get("api_key"), provider.get("openai_api_key"))


def resolve_config(args: argparse.Namespace) -> tuple[str, str]:
    home = codex_home()
    auth_path = Path(os.environ.get("CODEX_AUTH_FILE", home / "auth.json")).expanduser()
    config_path = Path(os.environ.get("CODEX_CONFIG_FILE", home / "config.toml")).expanduser()
    auth = read_json(auth_path)
    config = read_toml(config_path)
    provider = selected_provider(config)

    api_key = first_string(
        args.api_key,
        os.environ.get("OPENAI_API_KEY"),
        os.environ.get("TRANSFERAI_API_KEY"),
        auth.get("OPENAI_API_KEY"),
        auth.get("api_key"),
        auth.get("openai_api_key"),
        provider_api_key(provider),
        provider_api_key(config),
    )
    if not api_key:
        fail(
            "No API key found. Set OPENAI_API_KEY/TRANSFERAI_API_KEY, pass --api-key, "
            "or configure ~/.codex/auth.json / ~/.codex/config.toml."
        )

    base_url = first_string(
        args.base_url,
        os.environ.get("OPENAI_BASE_URL"),
        os.environ.get("base_url"),
        os.environ.get("BASE_URL"),
        os.environ.get("TRANSFERAI_BASE_URL"),
        auth.get("base_url"),
        auth.get("BASE_URL"),
        auth.get("OPENAI_BASE_URL"),
        provider.get("base_url") if isinstance(provider, dict) else None,
        config.get("base_url"),
        DEFAULT_BASE_URL,
    )
    return api_key, normalize_base_url(base_url)


def normalize_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/images/generations"):
        return base[: -len("/images/generations")]
    return base


def images_url(base_url: str) -> str:
    base = normalize_base_url(base_url)
    if base.endswith("/v1"):
        return f"{base}/images/generations"
    return f"{base}/v1/images/generations"


def output_path(out: str | None, name: str | None) -> Path:
    if out:
        path = Path(out).expanduser()
        return path if path.suffix else path.with_suffix(".png")

    directory = codex_home() / "generated_images" / "codex-img"
    directory.mkdir(parents=True, exist_ok=True)
    prefix = name or "image"
    return directory / f"{prefix}-{uuid.uuid4().hex[:8]}.png"


def ssl_context(insecure: bool) -> ssl.SSLContext | None:
    if insecure:
        return ssl._create_unverified_context()
    return None


def is_ssl_cert_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current:
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        current = current.__cause__ or current.__context__
    return "CERTIFICATE_VERIFY_FAILED" in str(exc)


def explain_http_error(exc: error.HTTPError) -> str:
    text = exc.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {}

    if isinstance(data, dict) and data.get("cloudflare_error"):
        code = data.get("error_code") or exc.code
        name = data.get("error_name") or "cloudflare_error"
        detail = data.get("detail") or text
        return (
            f"Image request blocked by Cloudflare: status={exc.code} "
            f"code={code} name={name}\n{detail}"
        )
    return f"Image request failed: status={exc.code}\n{text}"


@contextmanager
def progress(message: str, interval: int) -> Iterator[None]:
    if interval <= 0:
        yield
        return

    stopped = threading.Event()
    started = time.monotonic()

    def run() -> None:
        while not stopped.wait(interval):
            elapsed = int(time.monotonic() - started)
            log(f"{message} ({elapsed}s elapsed)")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)


def post_json(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: int,
    insecure: bool,
    strict_tls: bool,
    user_agent: str,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "User-Agent": user_agent,
        },
        method="POST",
    )
    attempts = [insecure]
    if not insecure and not strict_tls:
        attempts.append(True)

    last_error: error.URLError | None = None
    for attempt, use_insecure in enumerate(attempts, start=1):
        if use_insecure:
            if attempt > 1:
                log("TLS certificate verification failed; retrying once with verification disabled")
            else:
                log("warning: TLS certificate verification is disabled for this request")
        try:
            with request.urlopen(req, timeout=timeout, context=ssl_context(use_insecure)) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            fail(explain_http_error(exc))
        except error.URLError as exc:
            if is_ssl_cert_error(exc) and not use_insecure and not strict_tls:
                last_error = exc
                continue
            fail(f"Image request failed: {exc}")
        except json.JSONDecodeError as exc:
            fail(f"Image request returned invalid JSON: {exc}")

    fail(f"Image request failed: {last_error}")


def fetch_url(url: str, timeout: int, insecure: bool, strict_tls: bool) -> bytes:
    attempts = [insecure]
    if not insecure and not strict_tls:
        attempts.append(True)

    last_error: error.URLError | None = None
    for use_insecure in attempts:
        try:
            with request.urlopen(url, timeout=timeout, context=ssl_context(use_insecure)) as response:
                return response.read()
        except error.URLError as exc:
            if is_ssl_cert_error(exc) and not use_insecure and not strict_tls:
                last_error = exc
                continue
            fail(f"Failed to download image URL: {exc}")
    fail(f"Failed to download image URL: {last_error}")


def extract_image_bytes(
    response_data: dict[str, Any], timeout: int, insecure: bool, strict_tls: bool
) -> bytes:
    items = response_data.get("data")
    if not isinstance(items, list) or not items:
        fail(f"Response did not contain image data: {json.dumps(response_data, ensure_ascii=False)[:1200]}")

    first = items[0]
    if not isinstance(first, dict):
        fail("Response image item is not an object")

    b64_json = first.get("b64_json")
    if isinstance(b64_json, str) and b64_json:
        try:
            return base64.b64decode(b64_json)
        except ValueError as exc:
            fail(f"Invalid b64_json image data: {exc}")

    url = first.get("url")
    if isinstance(url, str) and url:
        return fetch_url(url, timeout, insecure, strict_tls)

    fail(f"Image item did not contain b64_json or url: {json.dumps(first, ensure_ascii=False)[:1200]}")


def generate(args: argparse.Namespace) -> int:
    prompt = args.prompt.strip()
    if not prompt:
        fail("Prompt cannot be empty")

    api_key, base_url = resolve_config(args)
    url = images_url(base_url)
    out_path = output_path(args.out, args.name)

    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "size": args.size,
        "response_format": args.response_format,
    }

    if args.dry_run:
        print(
            json.dumps(
                {
                    "url": url,
                    "has_api_key": bool(api_key),
                    "payload": payload,
                    "output": str(out_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    log(f"POST {url}")
    log(f"model={args.model} size={args.size} output={out_path}")
    with progress("waiting for image generation", args.progress_interval):
        response_data = post_json(
            url,
            api_key,
            payload,
            args.timeout,
            args.insecure,
            args.strict_tls,
            args.user_agent,
        )
    image = extract_image_bytes(response_data, args.timeout, args.insecure, args.strict_tls)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(image)
    log(f"saved {out_path}")
    print(str(out_path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate images using Codex API configuration")
    subparsers = parser.add_subparsers(dest="command")

    generate_parser = subparsers.add_parser("generate", help="generate an image")
    generate_parser.add_argument("prompt", help="image prompt")
    generate_parser.add_argument("--model", default=DEFAULT_MODEL, help=f"model name (default: {DEFAULT_MODEL})")
    generate_parser.add_argument("--size", default=DEFAULT_SIZE, help=f"image size (default: {DEFAULT_SIZE})")
    generate_parser.add_argument(
        "--response-format",
        default=DEFAULT_RESPONSE_FORMAT,
        choices=("b64_json", "url"),
        help=f"response format (default: {DEFAULT_RESPONSE_FORMAT})",
    )
    generate_parser.add_argument("--out", help="output image path")
    generate_parser.add_argument("--name", help="output filename prefix")
    generate_parser.add_argument("--api-key", help="override API key")
    generate_parser.add_argument("--base-url", help="override base URL")
    generate_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="request timeout in seconds")
    generate_parser.add_argument(
        "--progress-interval",
        type=int,
        default=DEFAULT_PROGRESS_INTERVAL,
        help="seconds between waiting progress logs; use 0 to disable",
    )
    generate_parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent header")
    generate_parser.add_argument(
        "--insecure",
        action="store_true",
        help="disable TLS certificate verification immediately",
    )
    generate_parser.add_argument(
        "--strict-tls",
        action="store_true",
        help="fail on TLS certificate errors instead of auto-retrying with verification disabled",
    )
    generate_parser.add_argument("--dry-run", action="store_true", help="print request details without calling API")
    generate_parser.set_defaults(func=generate)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
