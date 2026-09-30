"""Bounded, portable JSON objects for PostgreSQL payloads and safe API receipts.

Only ordinary JSON types are accepted. Numbers stay in the interoperable JSON
safe range: PostgreSQL JSONB can otherwise expand exponent floats into huge
integers, creating a payload which passes enqueue but fails on read/dispatch.
Content redaction remains the application service's responsibility.
"""

import json
import math
from typing import cast

_MAX_NUMBER = 2**53 - 1


def bounded_json_object(value: dict[str, object]) -> dict[str, object]:
    def validate(node: object, depth: int = 0) -> None:
        if depth > 32:
            raise ValueError("JSON payload exceeds 32 nesting levels")
        if node is None or type(node) is bool:
            return
        if isinstance(node, str):
            if "\x00" in node:
                raise ValueError("JSON strings cannot contain NUL")
            try:
                node.encode("utf-8")
            except UnicodeError:
                raise ValueError("JSON strings must contain valid Unicode") from None
        elif type(node) is int:
            if abs(node) > _MAX_NUMBER:
                raise ValueError("JSON number exceeds the interoperable safe range")
        elif type(node) is float:
            if not math.isfinite(node) or abs(node) > _MAX_NUMBER:
                raise ValueError("JSON number must be finite and within the safe range")
        elif isinstance(node, dict):
            for key, item in node.items():
                if not isinstance(key, str):
                    raise ValueError("JSON object keys must be strings")
                validate(key, depth + 1)
                validate(item, depth + 1)
        elif isinstance(node, list):
            for item in node:
                validate(item, depth + 1)
        else:
            raise ValueError("Payload contains a non-JSON type")

    if not isinstance(value, dict):
        raise ValueError("A JSON object is required")
    validate(value)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(encoded.encode("utf-8")) > 65536:
        raise ValueError("JSON payload exceeds 64 KiB")
    return cast(dict[str, object], json.loads(encoded))
