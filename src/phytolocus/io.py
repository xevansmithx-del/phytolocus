"""Strict, race-resistant JSON input primitives."""

from __future__ import annotations

import json
import math
import os
import stat
from pathlib import Path
from typing import Any

from .models import ModelError

ValidationError = ModelError

MAX_JSON_BYTES = 100 * 1024 * 1024
MAX_JSON_INTEGER_DIGITS = 1_000


def validate_path_argument(value: str | Path) -> str:
    """Return one safe Unicode-scalar filesystem argument or reject it before I/O."""

    try:
        path = os.fspath(value)
    except TypeError as exc:
        raise ValidationError("filesystem path must be a string") from exc
    if not isinstance(path, str) or not path:
        raise ValidationError("filesystem path must be a nonempty string")
    if "\x00" in path:
        raise ValidationError("filesystem path contains a NUL byte")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValidationError("filesystem path is not a Unicode scalar string") from exc
    return path


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"JSON contains duplicate key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValidationError(f"JSON contains non-finite number: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValidationError(f"JSON contains non-finite number: {value}")
    return parsed


def _bounded_int(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > MAX_JSON_INTEGER_DIGITS:
        raise ValidationError(f"JSON integer exceeds {MAX_JSON_INTEGER_DIGITS} digit limit")
    try:
        return int(value)
    except ValueError as exc:
        raise ValidationError("JSON integer cannot be represented safely") from exc


def _validate_json_unicode_scalars(value: Any) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in current):
                raise ValidationError("JSON string contains non-Unicode-scalar code point")
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def _read_json_once(source: Path, maximum_bytes: int) -> bytes:
    try:
        before = os.lstat(source)
    except OSError as exc:
        raise ValidationError("JSON input cannot be inspected safely") from exc
    if stat.S_ISLNK(before.st_mode):
        raise ValidationError("refusing symbolic-link JSON input")
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValidationError("JSON input must be a single-link regular file")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ValidationError("JSON input cannot be opened safely") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ValidationError("JSON input must be a single-link regular file")
        if (before.st_dev, before.st_ino) != (details.st_dev, details.st_ino):
            raise ValidationError("JSON input identity changed before reading")
        if details.st_size > maximum_bytes:
            raise ValidationError(f"JSON input exceeds {maximum_bytes} bytes")
        chunks: list[bytes] = []
        remaining = details.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ValidationError("JSON input changed while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValidationError("JSON input grew while reading")
        after = os.fstat(descriptor)
        try:
            after_path = os.lstat(source)
        except OSError as exc:
            raise ValidationError("JSON input identity changed while reading") from exc
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if any(getattr(details, field) != getattr(after, field) for field in stable):
            raise ValidationError("JSON input changed while reading")
        if any(getattr(details, field) != getattr(after_path, field) for field in stable):
            raise ValidationError("JSON input identity changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def parse_json_bytes(payload: bytes, *, label: str = "JSON input") -> Any:
    if len(payload) > MAX_JSON_BYTES:
        raise ValidationError(f"{label} exceeds {MAX_JSON_BYTES} bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{label} is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            parse_int=_bounded_int,
        )
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
    except RecursionError as exc:
        raise ValidationError("JSON nesting exceeds parser limit") from exc
    _validate_json_unicode_scalars(value)
    return value


def load_json(path: str | Path, *, maximum_bytes: int = MAX_JSON_BYTES) -> Any:
    return parse_json_bytes(
        _read_json_once(Path(validate_path_argument(path)), maximum_bytes),
        label="JSON input",
    )


__all__ = ["MAX_JSON_BYTES", "load_json", "parse_json_bytes", "validate_path_argument"]
