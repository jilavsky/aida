"""Best-effort repair of MCP tool-call arguments against the tool's own
JSON Schema.

Real-world report: Playwright's MCP server (which validates arguments
strictly with Zod) rejected ``browser_snapshot``/``browser_take_screenshot``
calls with "Invalid input: expected number, received string -> at depth" /
"expected boolean, received string -> at fullPage" — the model had emitted
``{"depth": "3"}``/``{"fullPage": "true"}`` (quoted) instead of the bare
``3``/``true`` the schema declares. Traced end to end: neither provider
translation (``anthropic_.py``/``openai_compat.py``, both just
``json.loads`` the model's own generated JSON text) nor anything in the
dispatch path (``McpManager``/``McpServerHandle``) transforms argument
values at all — whatever came out of the model's JSON is sent to the
server byte-for-byte. So the failure originates with the model (a known
characteristic of some models' function-calling output: quoting every
leaf value as a string, even for typed parameters), not with a bug in
AIDA's own code. But it cost the user "lots of turns" of retries before
giving up, and AIDA already has the tool's real schema in hand at the
exact point it's about to dispatch the call — the repair a human would
make by hand, staring at that Zod error, is completely mechanical: parse
the mis-typed string as JSON and see if that recovers the type the schema
wants. This module does that mechanically, once, before ever sending the
call, instead of making the model rediscover it by trial and error.

Deliberately conservative — this is a repair for one specific, common,
mechanical failure mode, not a general schema validator/coercer:

- Only a value that does *not* already match its schema's declared type is
  touched. A legitimately-provided string that matches ``"type":
  "string"`` is never reinterpreted, even if it happens to look numeric
  (e.g. a filename like ``"42"``).
- A value with no confidently-resolvable declared type (missing ``type``,
  ``$ref``, ``allOf``, or any schema shape this module doesn't recognize)
  is left completely alone rather than guessed at.
- The parsed replacement is only used if its *type* actually matches what
  the schema wants — ``coerce_arguments`` never invents a value, it only
  recovers the type of one the model already provided.
"""

from __future__ import annotations

import json
from typing import Any

#: JSON Schema type name -> Python type(s) that satisfy it. Checked via
#: ``_matches_type`` below, which special-cases ``bool`` explicitly since
#: ``bool`` is a subclass of ``int`` in Python (``isinstance(True, int)`` is
#: ``True``) and would otherwise make a "number"/"integer" schema wrongly
#: accept a bare ``True``/``False``.
_TYPE_TO_PY: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}

#: A JSON-invalid but common textual spelling of a boolean some models
#: produce (Python's own ``str(True)``/``str(False)`` capitalization,
#: rather than JSON's lowercase ``true``/``false``) — checked only when the
#: schema unambiguously wants exactly ``"boolean"`` and ``json.loads``
#: itself didn't already resolve it, so this never competes with a
#: legitimate string value.
_TEXTUAL_BOOLEANS = {"true": True, "false": False}


def _declared_types(subschema: Any) -> list[str]:
    """The JSON Schema type name(s) a value at ``subschema`` is allowed to
    be. Handles ``"type": "number"``, ``"type": ["number", "null"]``, and a
    simple ``anyOf``/``oneOf`` union of same-shaped branches. Anything else
    (``$ref``, ``allOf``, no ``type`` at all, ...) returns ``[]`` — callers
    treat an empty list as "type unknown, don't touch this value.\""""
    if not isinstance(subschema, dict):
        return []
    declared = subschema.get("type")
    if isinstance(declared, str):
        return [declared]
    if isinstance(declared, list):
        return [t for t in declared if isinstance(t, str)]
    types: list[str] = []
    for branch in (*subschema.get("anyOf", ()), *subschema.get("oneOf", ())):
        types.extend(_declared_types(branch))
    return types


def _matches_type(value: Any, type_names: list[str]) -> bool:
    """Whether ``value`` already satisfies at least one of ``type_names``.
    An empty ``type_names`` (nothing confidently declared) always matches —
    the caller's job is to leave an undeclared-type value alone, not flag
    it as wrong."""
    if not type_names:
        return True
    for name in type_names:
        py_types = _TYPE_TO_PY.get(name)
        if py_types is None:
            return True  # an unrecognized schema type name — don't guess
        if name in ("number", "integer") and isinstance(value, bool):
            continue
        if isinstance(value, py_types):
            return True
    return False


def _coerce_value(value: Any, subschema: dict[str, Any], *, path: str) -> tuple[Any, str | None]:
    type_names = _declared_types(subschema)
    if _matches_type(value, type_names):
        # Already fine at this level — still worth descending into a
        # correctly-typed container in case one of *its* children is a
        # mis-typed string (e.g. a valid list whose entries are quoted
        # numbers).
        return _coerce_container(value, subschema, path=path)

    if not isinstance(value, str):
        return value, None  # wrong type, but not a string we could reparse

    parsed: Any = None
    resolved = False
    try:
        parsed = json.loads(value)
        resolved = _matches_type(parsed, type_names)
    except ValueError:
        pass
    if not resolved and type_names == ["boolean"] and value.strip().lower() in _TEXTUAL_BOOLEANS:
        parsed = _TEXTUAL_BOOLEANS[value.strip().lower()]
        resolved = True

    if resolved:
        note = (
            f"{path}: {value!r} (str) -> {parsed!r} ({type(parsed).__name__}), "
            f"matching schema type {type_names!r}"
        )
        return parsed, note
    return value, None


def _coerce_container(
    value: Any, subschema: dict[str, Any], *, path: str
) -> tuple[Any, str | None]:
    if isinstance(value, dict) and isinstance(subschema.get("properties"), dict):
        properties = subschema["properties"]
        fixed = dict(value)
        notes: list[str] = []
        for key, item in value.items():
            item_schema = properties.get(key)
            if item_schema is None:
                continue
            new_item, note = _coerce_value(item, item_schema, path=f"{path}.{key}")
            if note is not None:
                fixed[key] = new_item
                notes.append(note)
        return (fixed, "; ".join(notes)) if notes else (value, None)

    if isinstance(value, list) and isinstance(subschema.get("items"), dict):
        item_schema = subschema["items"]
        fixed_list = list(value)
        notes = []
        for i, item in enumerate(value):
            new_item, note = _coerce_value(item, item_schema, path=f"{path}[{i}]")
            if note is not None:
                fixed_list[i] = new_item
                notes.append(note)
        return (fixed_list, "; ".join(notes)) if notes else (value, None)

    return value, None


def coerce_arguments(
    arguments: dict[str, Any], schema: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Best-effort repair of ``arguments`` (a tool call's arguments dict, as
    parsed from the model's own JSON) against ``schema`` (the tool's
    ``inputSchema``/``ToolSchema.parameters`` — JSON Schema shape,
    ``{"type": "object", "properties": {...}}``).

    Returns a new dict (``arguments`` itself is never mutated) plus a list
    of human-readable notes describing each value that was changed —
    empty if nothing needed fixing, which is the overwhelmingly common
    case: most tool calls, from most models, are already well-typed."""
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return dict(arguments), []

    fixed = dict(arguments)
    notes: list[str] = []
    for key, value in arguments.items():
        subschema = properties.get(key)
        if subschema is None:
            continue
        new_value, note = _coerce_value(value, subschema, path=key)
        if note is not None:
            fixed[key] = new_value
            notes.append(note)
    return fixed, notes


__all__ = ["coerce_arguments"]
