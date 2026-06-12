"""Safe URL construction for local LLM backends."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

_ALLOWED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "host.docker.internal",
    "ollama",
    "ollama-proxy",
    "ollama-runtime",
    "vllm",
    "central-vllm",
    "central-fast",
    "llama-cpp-fast",
    "llama-cpp-aux",
    "llama_cpp_fast",
    "llama_cpp_aux",
}


def validated_base_url(base_url: str) -> tuple[str, str, int | None, str]:
    raw = (base_url or "").strip()
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Invalid LLM backend URL") from exc

    if parts.scheme != "https" or not host:
        raise ValueError("LLM backend URL must be absolute HTTPS")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("LLM backend URL must not contain credentials, query, or fragment")
    if host not in _ALLOWED_HOSTS:
        raise ValueError("LLM backend host is not allowed")

    path = parts.path.rstrip("/")
    safe = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return safe, host, port, path


def llm_chat_url(base_url: str) -> str:
    safe, _host, port, path = validated_base_url(base_url)
    parts = urlsplit(safe)
    if port == 11434:
        endpoint = f"{path}/api/chat" if path else "/api/chat"
    elif path.endswith("/v1"):
        endpoint = f"{path}/chat/completions"
    else:
        endpoint = f"{path}/v1/chat/completions" if path else "/v1/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, endpoint, "", ""))


def ollama_generate_url(base_url: str) -> str:
    safe, _host, _port, path = validated_base_url(base_url)
    parts = urlsplit(safe)
    endpoint = f"{path}/api/generate" if path else "/api/generate"
    return urlunsplit((parts.scheme, parts.netloc, endpoint, "", ""))


def disables_thinking(base_url: str) -> bool:
    _safe, host, port, _path = validated_base_url(base_url)
    return host == "vllm" or port == 8000


def is_ollama_backend(base_url: str) -> bool:
    _safe, _host, port, _path = validated_base_url(base_url)
    return port == 11434
