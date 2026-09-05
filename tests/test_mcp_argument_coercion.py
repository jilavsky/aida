"""Tests for aida.mcp.argument_coercion — repairing a model's mis-typed MCP
tool-call arguments (quoted numbers/booleans/etc.) against the tool's own
JSON Schema before the call ever reaches the server. Motivated by a real
report: Playwright's MCP server rejecting `browser_snapshot`/
`browser_take_screenshot` calls with Zod errors ("expected number, received
string -> at depth", "expected boolean, received string -> at fullPage")
because the model sent `{"depth": "3"}`/`{"fullPage": "true"}` instead of
the bare `3`/`true` the schema declares.
"""

from __future__ import annotations

from aida.mcp.argument_coercion import coerce_arguments

_PLAYWRIGHT_LIKE_SCHEMA = {
    "type": "object",
    "properties": {
        "depth": {"type": "number"},
        "fullPage": {"type": "boolean"},
        "selector": {"type": "string"},
    },
}


def test_coerces_a_quoted_number_to_match_a_number_schema():
    fixed, notes = coerce_arguments({"depth": "3"}, _PLAYWRIGHT_LIKE_SCHEMA)
    assert fixed == {"depth": 3}
    assert len(notes) == 1
    assert "depth" in notes[0]


def test_coerces_a_quoted_boolean_to_match_a_boolean_schema():
    fixed, notes = coerce_arguments({"fullPage": "true"}, _PLAYWRIGHT_LIKE_SCHEMA)
    assert fixed == {"fullPage": True}
    assert len(notes) == 1


def test_coerces_python_style_capitalized_boolean_text():
    # Not valid JSON (`json.loads("True")` raises) but a common textual
    # spelling some models produce anyway — only attempted when the schema
    # unambiguously wants "boolean" (see the module docstring).
    fixed, notes = coerce_arguments({"fullPage": "True"}, _PLAYWRIGHT_LIKE_SCHEMA)
    assert fixed == {"fullPage": True}
    assert notes


def test_leaves_a_correctly_typed_value_untouched():
    fixed, notes = coerce_arguments({"depth": 3, "fullPage": True}, _PLAYWRIGHT_LIKE_SCHEMA)
    assert fixed == {"depth": 3, "fullPage": True}
    assert notes == []


def test_never_reinterprets_a_value_that_already_matches_a_string_schema():
    # A filename that happens to look numeric must not be "fixed" into an
    # int just because it parses as JSON — the schema says string, and the
    # value already satisfies that.
    fixed, notes = coerce_arguments({"selector": "42"}, _PLAYWRIGHT_LIKE_SCHEMA)
    assert fixed == {"selector": "42"}
    assert notes == []


def test_leaves_an_unparseable_string_unchanged_rather_than_erroring():
    fixed, notes = coerce_arguments({"depth": "not-a-number"}, _PLAYWRIGHT_LIKE_SCHEMA)
    assert fixed == {"depth": "not-a-number"}
    assert notes == []


def test_leaves_a_value_with_no_declared_schema_type_alone():
    schema = {"type": "object", "properties": {"anything": {}}}
    fixed, notes = coerce_arguments({"anything": "3"}, schema)
    assert fixed == {"anything": "3"}
    assert notes == []


def test_ignores_an_argument_name_not_present_in_the_schema():
    fixed, notes = coerce_arguments({"unexpected": "3"}, _PLAYWRIGHT_LIKE_SCHEMA)
    assert fixed == {"unexpected": "3"}
    assert notes == []


def test_does_not_mutate_the_input_dict():
    original = {"depth": "3"}
    coerce_arguments(original, _PLAYWRIGHT_LIKE_SCHEMA)
    assert original == {"depth": "3"}


def test_schema_with_no_properties_is_a_noop():
    fixed, notes = coerce_arguments({"depth": "3"}, {"type": "object"})
    assert fixed == {"depth": "3"}
    assert notes == []


def test_handles_a_type_union_including_null():
    schema = {"type": "object", "properties": {"count": {"type": ["integer", "null"]}}}
    fixed, notes = coerce_arguments({"count": "5"}, schema)
    assert fixed == {"count": 5}
    assert notes


def test_handles_anyof_union_schema():
    schema = {
        "type": "object",
        "properties": {"value": {"anyOf": [{"type": "number"}, {"type": "string"}]}},
    }
    # Already satisfies "string" branch of the union — must not be touched.
    fixed, notes = coerce_arguments({"value": "3"}, schema)
    assert fixed == {"value": "3"}
    assert notes == []


def test_coerces_a_quoted_array_to_match_an_array_schema():
    schema = {
        "type": "object",
        "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
    }
    fixed, notes = coerce_arguments({"tags": '["a", "b"]'}, schema)
    assert fixed == {"tags": ["a", "b"]}
    assert notes


def test_recurses_into_a_correctly_typed_array_to_fix_mis_typed_items():
    schema = {
        "type": "object",
        "properties": {"depths": {"type": "array", "items": {"type": "number"}}},
    }
    fixed, notes = coerce_arguments({"depths": ["1", 2, "3"]}, schema)
    assert fixed == {"depths": [1, 2, 3]}
    assert len(notes) == 1  # one note per top-level argument, joined internally


def test_recurses_into_a_correctly_typed_nested_object():
    schema = {
        "type": "object",
        "properties": {
            "options": {
                "type": "object",
                "properties": {"depth": {"type": "number"}},
            }
        },
    }
    fixed, notes = coerce_arguments({"options": {"depth": "3"}}, schema)
    assert fixed == {"options": {"depth": 3}}
    assert notes


def test_does_not_reject_an_int_where_number_is_declared():
    # "number" accepts both int and float per JSON Schema — an already-int
    # value must not be touched or flagged.
    fixed, notes = coerce_arguments({"depth": 3}, _PLAYWRIGHT_LIKE_SCHEMA)
    assert fixed == {"depth": 3}
    assert notes == []


def test_bool_is_never_accepted_as_a_number_even_though_it_is_an_int_subclass():
    # If a model sends a bare `true` for a "number" field, that's still
    # wrong — Python's bool being an int subclass must not paper over it.
    schema = {"type": "object", "properties": {"depth": {"type": "number"}}}
    fixed, notes = coerce_arguments({"depth": True}, schema)
    assert fixed == {"depth": True}  # left as-is: not a string, nothing to reparse
    assert notes == []
