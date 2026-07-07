"""Parsers for Xpoz MCP-over-HTTP text responses."""

from __future__ import annotations

from typing import Any
import csv
import json
import re


def parse_mcp_response_text(text: str) -> Any:
    """Parse JSON or Server-Sent Event JSON from an MCP response body."""

    value = text.strip()
    if not value:
        return {}
    if value.startswith("data:") or "\ndata:" in value:
        payloads = []
        for line in value.splitlines():
            if line.startswith("data:"):
                payloads.append(line[5:].strip())
        value = "\n".join(payloads).strip()
    try:
        return json.loads(value)
    except ValueError as exc:
        raise ValueError("Xpoz returned invalid MCP JSON.") from exc


def parse_xpoz_rows(text: str, *, preferred_prefix: str | None = None) -> list[dict[str, Any]]:
    """Parse Xpoz tool text output into row dictionaries."""

    if preferred_prefix:
        rows = _parse_xpoz_table(text, preferred_prefix)
        if rows:
            return rows
    for prefix in ("results", "posts", "comments"):
        rows = _parse_xpoz_table(text, prefix)
        if rows:
            return rows
    return _parse_xpoz_list(text)


def _parse_xpoz_table(text: str, prefix: str) -> list[dict[str, Any]]:
    match = re.search(rf"{re.escape(prefix)}\[(\d+)\]\{{([^}}]+)\}}:", text)
    if not match:
        return []
    fields = [field.strip() for field in match.group(2).split(",")]
    rows: list[dict[str, Any]] = []
    for line in text[match.end() :].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[A-Za-z_]+:", stripped):
            break
        try:
            values = next(csv.reader([stripped]))
        except csv.Error:
            continue
        if len(values) == len(fields):
            rows.append(dict(zip(fields, values, strict=True)))
    return rows


def _parse_xpoz_list(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_key: str | None = None
    for line in text.splitlines():
        if re.match(r"\s*-\s+[A-Za-z0-9_]+:", line):
            if current:
                rows.append(current)
            current = {}
            line = re.sub(r"^\s*-\s+", "", line)
        if current is None:
            continue
        indexed = re.match(r"\s*([A-Za-z0-9_]+)\[(\d+)\]:\s*(.*)$", line)
        if indexed:
            key = indexed.group(1)
            current.setdefault(key, [])
            if isinstance(current[key], list):
                current[key].append(_parse_scalar(indexed.group(3)))
            last_key = key
            continue
        field = re.match(r"\s*([A-Za-z0-9_]+):\s*(.*)$", line)
        if field:
            key = field.group(1)
            current[key] = _parse_scalar(field.group(2))
            last_key = key
            continue
        array_item = re.match(r"\s*-\s*(.*)$", line)
        if array_item and last_key:
            if not isinstance(current.get(last_key), list):
                current[last_key] = []
            current[last_key].append(_parse_scalar(array_item.group(1)))
    if current:
        rows.append(current)
    return rows


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return value.strip('"')
