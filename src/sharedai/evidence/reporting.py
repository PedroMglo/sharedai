"""Shared Markdown helpers for deterministic evidence reports."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def append_storage_reference(lines: list[str], published_uri: str | None) -> None:
    if published_uri:
        lines.append(f"- storage_guardian object: `{published_uri}`")


def append_key_value_section(lines: list[str], title: str, items: Iterable[tuple[str, Any]]) -> None:
    lines.extend(["", f"## {title}"])
    for key, value in items:
        if value is None or value == "":
            continue
        lines.append(f"- {key}: {value}")
