#!/usr/bin/env python3
"""Generate images through an OpenAI-compatible Images API endpoint."""
from __future__ import annotations

import argparse
import base64
import mimetypes
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
DEFAULT_PARTIAL_IMAGES = 2
DEFAULT_PROGRESS_INTERVAL = 30
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class StreamUnsupported(Exception):
    """Raised when a provider rejects Image API streaming parameters."""


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


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def resolve_config(args: argparse.Namespace) -> tuple[str, str, bool]:
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
    config_insecure = (
        truthy(os.environ.get("CODEX_IMG_INSECURE"))
        or truthy(auth.get("codex_img_insecure"))
        or truthy(auth.get("insecure"))
        or truthy(config.get("codex_img_insecure"))
        or truthy(config.get("insecure"))
        or truthy(provider.get("codex_img_insecure") if isinstance(provider, dict) else None)
        or truthy(provider.get("insecure") if isinstance(provider, dict) else None)
    )
    return api_key, normalize_base_url(base_url), config_insecure


def normalize_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/images/generations"):
        return base[: -len("/images/generations")]
    if base.endswith("/images/edits"):
        return base[: -len("/images/edits")]
    return base


def images_url(base_url: str) -> str:
    base = normalize_base_url(base_url)
    if base.endswith("/v1"):
        return f"{base}/images/generations"
    return f"{base}/v1/images/generations"


def edits_url(base_url: str) -> str:
    base = normalize_base_url(base_url)
    if base.endswith("/v1"):
        return f"{base}/images/edits"
    return f"{base}/v1/images/edits"


def model_url(base_url: str, model: str) -> str:
    base = normalize_base_url(base_url)
    if base.endswith("/v1"):
        return f"{base}/models/{model}"
    return f"{base}/v1/models/{model}"


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


def http_error_text(exc: error.HTTPError) -> str:
    return exc.read().decode("utf-8", errors="replace")


def is_stream_unsupported(exc: error.HTTPError, body: str) -> bool:
    text = body.lower()
    return exc.code in {400, 404, 422} and (
        "stream" in text
        or "partial_images" in text
        or "unknown parameter" in text
        or "unsupported" in text
    )


def request_headers(api_key: str, user_agent: str, accept: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "User-Agent": user_agent,
    }


def multipart_headers(api_key: str, user_agent: str, content_type: str, accept: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": content_type,
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "User-Agent": user_agent,
    }


def guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def encode_multipart(fields: dict[str, Any], files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    boundary = f"codex-img-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for key, value in fields.items():
        if value is None:
            continue
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    for field_name, path in files:
        if not path.exists():
            fail(f"Input image not found: {path}")
        filename = path.name
        mime = guess_mime(path)
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{filename}"\r\n'
            ).encode()
        )
        chunks.append(f"Content-Type: {mime}\r\n\r\n".encode())
        try:
            chunks.append(path.read_bytes())
        except OSError as exc:
            fail(f"Failed to read input image {path}: {exc}")
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def request_with_tls_fallback(
    req: request.Request,
    timeout: int,
    insecure: bool,
    strict_tls: bool,
    action: str,
) -> Any:
    attempts = [insecure]
    if not insecure and not strict_tls:
        attempts.append(True)

    last_error: error.URLError | None = None
    for attempt, use_insecure in enumerate(attempts, start=1):
        if use_insecure and attempt > 1:
            log(f"TLS certificate verification failed; retrying {action} once with verification disabled")
        try:
            return request.urlopen(req, timeout=timeout, context=ssl_context(use_insecure))
        except error.URLError as exc:
            if isinstance(exc, error.HTTPError):
                raise
            if is_ssl_cert_error(exc) and not use_insecure and not strict_tls:
                last_error = exc
                continue
            fail(f"{action} failed: {exc}")
    fail(f"{action} failed: {last_error}")


def get_json(
    url: str,
    api_key: str,
    timeout: int,
    insecure: bool,
    strict_tls: bool,
    user_agent: str,
    action: str,
) -> dict[str, Any]:
    req = request.Request(
        url,
        headers=request_headers(api_key, user_agent, "application/json"),
        method="GET",
    )
    try:
        with request_with_tls_fallback(req, timeout, insecure, strict_tls, action) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body_text = http_error_text(exc)
        fail(explain_http_error_body(exc, body_text))
    except json.JSONDecodeError as exc:
        fail(f"{action} returned invalid JSON: {exc}")


def check_model_available(
    base_url: str,
    api_key: str,
    model: str,
    timeout: int,
    insecure: bool,
    strict_tls: bool,
    user_agent: str,
) -> bool:
    url = model_url(base_url, model)
    log(f"checking model availability: {model}")
    req = request.Request(
        url,
        headers=request_headers(api_key, user_agent, "application/json"),
        method="GET",
    )
    try:
        with request_with_tls_fallback(req, timeout, insecure, strict_tls, "Model check") as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body_text = http_error_text(exc)
        if exc.code in {404, 405, 501}:
            log(f"model check unavailable on this endpoint: status={exc.code}")
            return False
        fail(explain_http_error_body(exc, body_text))
    except json.JSONDecodeError as exc:
        log(f"model check returned invalid JSON: {exc}")
        return False

    returned = data.get("id") or data.get("model")
    if returned and returned != model:
        fail(f"Model check mismatch: requested={model} returned={returned}")
    log(f"model available: {returned or model}")
    return True


def event_model(event: dict[str, Any]) -> str | None:
    value = event.get("model")
    if isinstance(value, str) and value:
        return value
    for container_key in ("data", "item", "output", "response"):
        container = event.get(container_key)
        if isinstance(container, dict):
            found = event_model(container)
            if found:
                return found
        elif isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    found = event_model(item)
                    if found:
                        return found
    return None


def assert_response_model(data: dict[str, Any], expected: str) -> bool:
    returned = event_model(data)
    if not returned:
        return False
    if returned != expected:
        fail(f"Response model mismatch: requested={expected} returned={returned}")
    return True


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
        headers=request_headers(api_key, user_agent, "application/json"),
        method="POST",
    )
    try:
        with request_with_tls_fallback(req, timeout, insecure, strict_tls, "Image request") as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body_text = http_error_text(exc)
        fail(explain_http_error_body(exc, body_text))
    except json.JSONDecodeError as exc:
        fail(f"Image request returned invalid JSON: {exc}")


def post_multipart_json(
    url: str,
    api_key: str,
    fields: dict[str, Any],
    files: list[tuple[str, Path]],
    timeout: int,
    insecure: bool,
    strict_tls: bool,
    user_agent: str,
) -> dict[str, Any]:
    body, content_type = encode_multipart(fields, files)
    req = request.Request(
        url,
        data=body,
        headers=multipart_headers(api_key, user_agent, content_type, "application/json"),
        method="POST",
    )
    try:
        with request_with_tls_fallback(req, timeout, insecure, strict_tls, "Image edit request") as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body_text = http_error_text(exc)
        fail(explain_http_error_body(exc, body_text))
    except json.JSONDecodeError as exc:
        fail(f"Image edit request returned invalid JSON: {exc}")


def explain_http_error_body(exc: error.HTTPError, text: str) -> str:
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


def decode_b64_image(value: Any) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value)
    except ValueError:
        return None


def image_bytes_from_event(event: dict[str, Any]) -> bytes | None:
    for key in ("b64_json", "image_b64", "result"):
        image = decode_b64_image(event.get(key))
        if image:
            return image

    for container_key in ("data", "item", "output", "response"):
        container = event.get(container_key)
        if isinstance(container, dict):
            image = image_bytes_from_event(container)
            if image:
                return image
        elif isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    image = image_bytes_from_event(item)
                    if image:
                        return image
    return None


def partial_output_path(out_path: Path, index: int) -> Path:
    suffix = out_path.suffix or ".png"
    return out_path.with_name(f"{out_path.stem}.partial-{index}{suffix}")


def iter_sse_json(response: Any) -> Iterator[dict[str, Any]]:
    buffer = ""
    data_lines: list[str] = []
    for chunk in iter(lambda: response.read(4096), b""):
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")
            if not line:
                if data_lines:
                    data = "\n".join(data_lines)
                    data_lines = []
                    if data == "[DONE]":
                        return
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        log(f"ignored non-JSON SSE data: {data[:120]}")
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())


def post_stream(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: int,
    insecure: bool,
    strict_tls: bool,
    user_agent: str,
    out_path: Path,
) -> tuple[bytes, bool]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers=request_headers(api_key, user_agent, "text/event-stream"),
        method="POST",
    )
    try:
        latest: bytes | None = None
        partial_count = 0
        model_seen = False
        expected_model = str(payload.get("model") or "")
        with request_with_tls_fallback(req, timeout, insecure, strict_tls, "Image stream") as response:
            for event in iter_sse_json(response):
                event_type = event.get("type") or event.get("event") or "event"
                if expected_model and assert_response_model(event, expected_model):
                    model_seen = True
                image = image_bytes_from_event(event)
                if image:
                    latest = image
                    if "partial" in str(event_type).lower():
                        partial_count += 1
                        partial_path = partial_output_path(out_path, partial_count)
                        partial_path.parent.mkdir(parents=True, exist_ok=True)
                        partial_path.write_bytes(image)
                        log(f"saved partial image {partial_count}: {partial_path}")
                    else:
                        log(f"received image event: {event_type}")
                elif event_type:
                    log(f"stream event: {event_type}")
        if latest:
            return latest, model_seen
        fail("Stream completed without image data")
    except error.HTTPError as exc:
        body_text = http_error_text(exc)
        if is_stream_unsupported(exc, body_text):
            raise StreamUnsupported(body_text) from exc
        fail(explain_http_error_body(exc, body_text))


def post_multipart_stream(
    url: str,
    api_key: str,
    fields: dict[str, Any],
    files: list[tuple[str, Path]],
    timeout: int,
    insecure: bool,
    strict_tls: bool,
    user_agent: str,
    out_path: Path,
) -> tuple[bytes, bool]:
    body, content_type = encode_multipart(fields, files)
    req = request.Request(
        url,
        data=body,
        headers=multipart_headers(api_key, user_agent, content_type, "text/event-stream"),
        method="POST",
    )
    try:
        latest: bytes | None = None
        partial_count = 0
        model_seen = False
        expected_model = str(fields.get("model") or "")
        with request_with_tls_fallback(req, timeout, insecure, strict_tls, "Image edit stream") as response:
            for event in iter_sse_json(response):
                event_type = event.get("type") or event.get("event") or "event"
                if expected_model and assert_response_model(event, expected_model):
                    model_seen = True
                image = image_bytes_from_event(event)
                if image:
                    latest = image
                    if "partial" in str(event_type).lower():
                        partial_count += 1
                        partial_path = partial_output_path(out_path, partial_count)
                        partial_path.parent.mkdir(parents=True, exist_ok=True)
                        partial_path.write_bytes(image)
                        log(f"saved partial image {partial_count}: {partial_path}")
                    else:
                        log(f"received image event: {event_type}")
                elif event_type:
                    log(f"stream event: {event_type}")
        if latest:
            return latest, model_seen
        fail("Image edit stream completed without image data")
    except error.HTTPError as exc:
        body_text = http_error_text(exc)
        if is_stream_unsupported(exc, body_text):
            raise StreamUnsupported(body_text) from exc
        fail(explain_http_error_body(exc, body_text))


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

    api_key, base_url, config_insecure = resolve_config(args)
    input_images = [Path(p).expanduser() for p in args.image]
    edit_mode = bool(input_images)
    url = edits_url(base_url) if edit_mode else images_url(base_url)
    out_path = output_path(args.out, args.name)
    insecure = args.insecure or config_insecure
    if insecure:
        log("TLS certificate verification disabled by flag or config")

    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "size": args.size,
        "response_format": args.response_format,
    }
    if args.quality:
        payload["quality"] = args.quality
    if args.output_format:
        payload["output_format"] = args.output_format
    if args.compression is not None:
        payload["output_compression"] = args.compression
    if args.stream:
        payload["stream"] = True
        payload["partial_images"] = args.partial_images

    multipart_fields = payload.copy()
    multipart_files: list[tuple[str, Path]] = []
    if edit_mode:
        field_name = args.image_field
        if field_name == "auto":
            field_name = "image" if len(input_images) == 1 else "image[]"
        multipart_files.extend((field_name, path) for path in input_images)
        if args.mask:
            multipart_files.append(("mask", Path(args.mask).expanduser()))

    if args.dry_run:
        print(
            json.dumps(
                {
                    "url": url,
                    "has_api_key": bool(api_key),
                    "mode": "edit" if edit_mode else "generation",
                    "payload": payload if not edit_mode else multipart_fields,
                    "images": [str(path) for path in input_images],
                    "mask": args.mask,
                    "output": str(out_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.check_model:
        checked = check_model_available(
            base_url,
            api_key,
            args.model,
            args.timeout,
            insecure,
            args.strict_tls,
            args.user_agent,
        )
        if not checked:
            if args.require_model_check:
                fail(f"Model check required but endpoint does not expose /models/{args.model}")
            log("model preflight skipped; endpoint did not expose compatible /models/{model}")

    log(f"POST {url}")
    log(f"mode={'edit' if edit_mode else 'generation'} model={args.model} size={args.size} output={out_path}")
    image: bytes
    model_seen = False
    if edit_mode and args.stream:
        try:
            with progress("waiting for streaming image edit", args.progress_interval):
                image, model_seen = post_multipart_stream(
                    url,
                    api_key,
                    multipart_fields,
                    multipart_files,
                    args.timeout,
                    insecure,
                    args.strict_tls,
                    args.user_agent,
                    out_path,
                )
        except StreamUnsupported:
            log("streaming unsupported by this endpoint; falling back to non-streaming edit request")
            multipart_fields.pop("stream", None)
            multipart_fields.pop("partial_images", None)
            with progress("waiting for image edit", args.progress_interval):
                response_data = post_multipart_json(
                    url,
                    api_key,
                    multipart_fields,
                    multipart_files,
                    args.timeout,
                    insecure,
                    args.strict_tls,
                    args.user_agent,
                )
            image = extract_image_bytes(response_data, args.timeout, insecure, args.strict_tls)
            model_seen = assert_response_model(response_data, args.model)
    elif edit_mode:
        with progress("waiting for image edit", args.progress_interval):
            response_data = post_multipart_json(
                url,
                api_key,
                multipart_fields,
                multipart_files,
                args.timeout,
                insecure,
                args.strict_tls,
                args.user_agent,
            )
        image = extract_image_bytes(response_data, args.timeout, insecure, args.strict_tls)
        model_seen = assert_response_model(response_data, args.model)
    elif args.stream:
        try:
            with progress("waiting for streaming image generation", args.progress_interval):
                image, model_seen = post_stream(
                    url,
                    api_key,
                    payload,
                    args.timeout,
                    insecure,
                    args.strict_tls,
                    args.user_agent,
                    out_path,
                )
        except StreamUnsupported:
            log("streaming unsupported by this endpoint; falling back to non-streaming request")
            payload.pop("stream", None)
            payload.pop("partial_images", None)
            with progress("waiting for image generation", args.progress_interval):
                response_data = post_json(
                    url,
                    api_key,
                    payload,
                    args.timeout,
                    insecure,
                    args.strict_tls,
                    args.user_agent,
                )
            image = extract_image_bytes(response_data, args.timeout, insecure, args.strict_tls)
            model_seen = assert_response_model(response_data, args.model)
    else:
        with progress("waiting for image generation", args.progress_interval):
            response_data = post_json(
                url,
                api_key,
                payload,
                args.timeout,
                insecure,
                args.strict_tls,
                args.user_agent,
            )
        image = extract_image_bytes(response_data, args.timeout, insecure, args.strict_tls)
        model_seen = assert_response_model(response_data, args.model)
    if not model_seen:
        log(f"response did not declare model; request model was {args.model}")
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
    generate_parser.add_argument("--check-model", dest="check_model", action="store_true", default=True, help="check /models/{model} before generation")
    generate_parser.add_argument("--no-check-model", dest="check_model", action="store_false", help="skip /models/{model} preflight check")
    generate_parser.add_argument("--require-model-check", action="store_true", help="fail if /models/{model} is unavailable")
    generate_parser.add_argument("--size", default=DEFAULT_SIZE, help=f"image size (default: {DEFAULT_SIZE})")
    generate_parser.add_argument("--quality", choices=("low", "medium", "high", "auto"), help="image quality")
    generate_parser.add_argument("--output-format", choices=("png", "jpeg", "webp"), help="image output format")
    generate_parser.add_argument("--compression", type=int, choices=range(0, 101), metavar="0-100", help="jpeg/webp compression")
    generate_parser.add_argument(
        "--response-format",
        default=DEFAULT_RESPONSE_FORMAT,
        choices=("b64_json", "url"),
        help=f"response format (default: {DEFAULT_RESPONSE_FORMAT})",
    )
    generate_parser.add_argument("--out", help="output image path")
    generate_parser.add_argument("--name", help="output filename prefix")
    generate_parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="input image path for image editing; repeat for multiple images",
    )
    generate_parser.add_argument("--mask", help="mask image path for image editing")
    generate_parser.add_argument(
        "--image-field",
        default="auto",
        choices=("auto", "image", "image[]"),
        help="multipart field name for input images (default: auto)",
    )
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
    generate_parser.add_argument("--stream", dest="stream", action="store_true", default=True, help="use Image API streaming")
    generate_parser.add_argument("--no-stream", dest="stream", action="store_false", help="disable Image API streaming")
    generate_parser.add_argument(
        "--partial-images",
        type=int,
        default=DEFAULT_PARTIAL_IMAGES,
        choices=range(0, 4),
        metavar="0-3",
        help=f"number of partial images to request while streaming (default: {DEFAULT_PARTIAL_IMAGES})",
    )
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
