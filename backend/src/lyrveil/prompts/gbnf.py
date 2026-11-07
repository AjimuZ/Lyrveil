"""schema_to_gbnf: the GBNF grammar of a Pydantic model's JSON Schema, for structured output (ADR 0013).

The supported subset is the one ADR 0013 names: objects with fixed keys, string enums and constants, integers (a
bounded range is listed value by value), strings, booleans, null, nullable values and arrays, with or without an item
bound. Every key of an object is written, in schema order, so the model fills each field and never invents a key.
Anything outside the subset raises UnsupportedSchemaError, so a schema change the grammar cannot follow fails at once
instead of loosening the output. The grammar constrains the shape only: lengths, patterns, string formats and the
semantic rules stay with Pydantic and the service, because model output is untrusted whatever the grammar allowed.
"""

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

# A bounded integer range with at most this many values is listed; a wider one becomes any integer.
MAX_ENUMERATED_RANGE = 256

# The JSON pieces every grammar shares. Strings follow JSON: no raw control character, and the standard escapes.
# Whitespace is at most one character between tokens, so a model cannot spend its tokens on indentation.
_BASE_RULES = (
    r"ws ::= [ \t\n]?",
    r'string ::= "\"" char* "\""',
    r'char ::= [^"\\\x7F\x00-\x1F] | "\\" (["\\/bfnrt] | "u" hex hex hex hex)',
    r"hex ::= [0-9a-fA-F]",
    r'integer ::= "-"? ("0" | [1-9] [0-9]*)',
    r'boolean ::= "true" | "false"',
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NOT_RULE_CHAR = re.compile(r"[^a-z0-9]+")
# Annotations that never change what a value may look like, and the constraints on a string's content (lengths,
# patterns, formats such as `date`), which validation checks: the grammar only says "a string".
_IGNORED_KEYS = frozenset(
    {
        "title",
        "description",
        "default",
        "examples",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "additionalProperties",
    }
)
# The keys the converter turns into grammar.
_CONVERTED_KEYS = frozenset(
    {
        "$defs",
        "$ref",
        "allOf",
        "anyOf",
        "const",
        "enum",
        "type",
        "properties",
        "required",
        "items",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
    }
)


class UnsupportedSchemaError(ValueError):
    """The schema uses a construct outside the subset of ADR 0013; no grammar was produced."""


def gbnf_literal(text: str) -> str:
    """Returns `text` as a GBNF string literal, with quotes, backslashes and line breaks escaped."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    for raw, escape in (("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")):
        escaped = escaped.replace(raw, escape)
    return f'"{escaped}"'


def _json_literal(value: Any) -> str:
    """Returns the GBNF literal that matches `value` written as JSON."""
    return gbnf_literal(json.dumps(value, ensure_ascii=False))


def _rule_name(definition: str) -> str:
    """Returns a GBNF rule name for a `$defs` entry: lowercase words joined by hyphens, such as `visual-identity-en`."""
    words = _CAMEL_BOUNDARY.sub("-", definition).lower()
    return "def-" + _NOT_RULE_CHAR.sub("-", words).strip("-")


def _alternatives(options: list[str]) -> str:
    return options[0] if len(options) == 1 else "(" + " | ".join(options) + ")"


class _Converter:
    """Turns one JSON Schema into rules. Each `$defs` entry becomes one rule, written once even when it repeats."""

    def __init__(self, schema: Mapping[str, Any]) -> None:
        self._defs: Mapping[str, Any] = schema.get("$defs", {})
        self.rules: dict[str, str] = {}
        self._building: set[str] = set()

    def expression(self, schema: Mapping[str, Any]) -> str:
        unknown = set(schema) - _IGNORED_KEYS - _CONVERTED_KEYS
        if unknown:
            raise UnsupportedSchemaError(f"unsupported schema keys: {sorted(unknown)}")
        if additional := schema.get("additionalProperties"):
            raise UnsupportedSchemaError(f"an object with free keys is not supported: {additional!r}")
        if "$ref" in schema:
            return self._reference(schema["$ref"])
        if "allOf" in schema:
            parts = schema["allOf"]
            if len(parts) != 1:
                raise UnsupportedSchemaError("allOf is supported with one schema only")
            return self.expression(parts[0])
        if "anyOf" in schema:
            return _alternatives([self.expression(option) for option in schema["anyOf"]])
        if "const" in schema:
            return _json_literal(schema["const"])
        if "enum" in schema:
            return _alternatives([_json_literal(value) for value in schema["enum"]])
        kind = schema.get("type")
        if isinstance(kind, list):
            return _alternatives([self.expression({**schema, "type": single}) for single in kind])
        if kind == "object":
            return self._object(schema)
        if kind == "array":
            return self._array(schema)
        if kind == "integer":
            return self._integer(schema)
        if kind == "string":
            return "string"
        if kind == "boolean":
            return "boolean"
        if kind == "null":
            return '"null"'
        raise UnsupportedSchemaError(f"unsupported schema type: {kind!r}")

    def _reference(self, reference: str) -> str:
        prefix = "#/$defs/"
        if not reference.startswith(prefix) or reference[len(prefix) :] not in self._defs:
            raise UnsupportedSchemaError(f"unsupported reference: {reference!r}")
        definition = reference[len(prefix) :]
        name = _rule_name(definition)
        if name not in self.rules and name not in self._building:
            self._building.add(name)
            self.rules[name] = self.expression(self._defs[definition])
            self._building.discard(name)
        return name

    def _object(self, schema: Mapping[str, Any]) -> str:
        properties: Mapping[str, Any] = schema.get("properties", {})
        members = [f'{_json_literal(key)} ws ":" ws {self.expression(value)}' for key, value in properties.items()]
        if not members:
            return '"{" ws "}"'
        return '"{" ws ' + ' ws "," ws '.join(members) + ' ws "}"'

    def _array(self, schema: Mapping[str, Any]) -> str:
        if "items" not in schema:
            raise UnsupportedSchemaError("an array needs an item schema")
        item = self.expression(schema["items"])
        low: int = schema.get("minItems", 0)
        high: int | None = schema.get("maxItems")
        if high == 0:
            return '"[" ws "]"'
        more_required = max(low - 1, 0)
        following = f'ws "," ws {item}'
        parts = [item, *([following] * more_required)]
        if high is None:
            parts.append(f"({following})*")
        else:
            # Up to `high` items without the {m,n} repetition, which older grammar engines lack: nested optionals.
            nested = ""
            for _ in range(high - 1 - more_required):
                nested = f"({following} {nested})?" if nested else f"({following})?"
            if nested:
                parts.append(nested)
        items = " ".join(parts)
        if low == 0:
            items = f"({items})?"
        return f'"[" ws {items} ws "]"'

    @staticmethod
    def _integer(schema: Mapping[str, Any]) -> str:
        low = schema.get("minimum", schema["exclusiveMinimum"] + 1 if "exclusiveMinimum" in schema else None)
        high = schema.get("maximum", schema["exclusiveMaximum"] - 1 if "exclusiveMaximum" in schema else None)
        if isinstance(low, int) and isinstance(high, int) and 0 < high - low + 1 <= MAX_ENUMERATED_RANGE:
            return _alternatives([gbnf_literal(str(value)) for value in range(low, high + 1)])
        return "integer"


def schema_to_gbnf(model: type[BaseModel]) -> str:
    """Returns the GBNF grammar, rule `root` first, that accepts the JSON objects of `model` with every key present.

    The output is deterministic for a given schema. Raises UnsupportedSchemaError for a construct outside the subset.
    """
    schema = model.model_json_schema()
    converter = _Converter(schema)
    root = converter.expression(schema)
    lines = [f"root ::= {root}", *(f"{name} ::= {body}" for name, body in converter.rules.items()), *_BASE_RULES]
    return "\n".join(lines) + "\n"
