"""
GraphQL query / mutation builders.

Inputs are serialized as GraphQL literals (not JSON variables). Some
backends have trouble deserializing typed JSON variables correctly through
their resolver layer; literal serialization sidesteps that class of problem
entirely, at the cost of needing our own literal serializer below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp_server_template.registry.entity_def import EntityDef, NestedDef


# ── Literal serializer ────────────────────────────────────────────────────────


def serialize_literal(value: Any) -> str:
    """Serialize a Python value to an inline GraphQL literal (no $ variables).

    Enums are passed as dicts: {"__enum__": "VALUE_NAME"} to avoid quoting.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        # Check for enum marker first (before general dict handling)
        if "__enum__" in value:
            return str(value["__enum__"])  # Unquoted enum identifier
        pairs = ", ".join(f"{k}: {serialize_literal(v)}" for k, v in value.items())
        return "{" + pairs + "}"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(serialize_literal(v) for v in value) + "]"
    return f'"{value}"'


# ── Query / mutation builders ─────────────────────────────────────────────────


def build_query(
    query_field: str,
    filter_obj: dict[str, Any] | None,
    selection: str,
) -> str:
    filter_arg = f"(filter: {serialize_literal(filter_obj)})" if filter_obj else ""
    return f"{{ {query_field}{filter_arg} {selection} }}"


def build_scalar_query(query_field: str, filter_obj: dict[str, Any] | None) -> str:
    """For queries that return a scalar (no selection set)."""
    filter_arg = f"(filter: {serialize_literal(filter_obj)})" if filter_obj else ""
    return f"{{ {query_field}{filter_arg} }}"


def build_mutation(mutation_name: str, input_dict: dict[str, Any], selection: str) -> str:
    return f"mutation {{ {mutation_name}(input: {serialize_literal(input_dict)}) {selection} }}"


def build_direct_mutation(mutation_name: str, args: dict[str, Any], selection: str | None) -> str:
    """For mutations with direct args (no `input` wrapper)."""
    args_str = ", ".join(f"{k}: {serialize_literal(v)}" for k, v in args.items() if v is not None)
    args_part = f"({args_str})" if args_str else ""
    return_part = f" {selection}" if selection else ""
    return f"mutation {{ {mutation_name}{args_part}{return_part} }}"


# ── Selection builders ────────────────────────────────────────────────────────


def _default_fields(fields: list[str]) -> list[str]:
    """
    Return uid/code/name/value intersection of fields.
    Used for nested child nodes in include paths so they get a concise default selection.
    Falls back to first 3 fields when none of the preferred names exist.
    """
    preferred = ["uid", "code", "name", "value"]
    found = [f for f in preferred if f in fields]
    return found if found else fields[:3]


def _split_fields(fields: list[str]) -> tuple[list[str], dict[tuple[str, ...], set[str]]]:
    """
    Split a `fields` list into root scalars and per-path nested field requests.

    Dot-notation selects fields on a nested path; the last segment is the field name:
      "name"                          → root scalar "name"
      "tasks.title"                   → on path ("tasks",), select "title"
      "tasks.assignee.email"          → on path ("tasks","assignee"), select "email"
      "tasks.*"                       → on path ("tasks",), select ALL scalar fields

    Returns (root_scalars, nested_map) where nested_map maps a path tuple to a set
    of requested field names ("*" meaning all).
    """
    root: list[str] = []
    nested: dict[tuple[str, ...], set[str]] = {}
    for f in fields:
        if "." in f:
            *path, leaf = f.split(".")
            nested.setdefault(tuple(path), set()).add(leaf)
        else:
            root.append(f)
    return root, nested


def _build_tree(include_paths: list[str], nested_map: dict[tuple[str, ...], set[str]]) -> dict:
    """
    Build a selection tree from include paths (presence) and nested field requests.

    Each node is {"fields": set[str], "children": dict[str, node]}. A path appearing
    in either source is traversed; requesting a nested field auto-includes its path.
    """
    root = {"fields": set(), "children": {}}

    def ensure(segments: list[str]) -> dict:
        node = root
        for seg in segments:
            node = node["children"].setdefault(seg, {"fields": set(), "children": {}})
        return node

    for path in include_paths:
        ensure(path.split("."))
    for path, flds in nested_map.items():
        ensure(list(path))["fields"].update(flds)
    return root


def _child_field_selection(requested: set[str], nested_def) -> list[str]:
    """
    Decide which scalar fields to render for an included child node.

      "*" in requested  → all of the child's scalar fields
      specific fields   → those (validated against the child), with uid prepended
      nothing requested → default fields (uid/code/name) — the include-only case
    """
    if "*" in requested:
        return list(nested_def.fields)
    if requested:
        chosen = [f for f in nested_def.fields if f in requested]
        if not chosen:  # all requested names were invalid → fall back to defaults
            return _default_fields(nested_def.fields)
        if "uid" in nested_def.fields and "uid" not in chosen:
            chosen = ["uid"] + chosen
        return chosen
    return _default_fields(nested_def.fields)


def _render_selection(scalar_fields: list[str], nested_defs: dict, children: dict) -> str:
    """
    Render a GraphQL selection set.

    Root level uses scalar_fields. Each included child uses _child_field_selection()
    so callers can request specific child fields (or all via "*"). Grandchildren are
    rendered recursively. Unknown path segments are skipped silently.
    """
    parts = list(scalar_fields)
    for name, child_node in children.items():
        nested_def = nested_defs.get(name)
        if nested_def is None:
            continue  # unknown path segment — skip silently
        child_fields = _child_field_selection(child_node["fields"], nested_def)
        child_sel = _render_selection(child_fields, nested_def.nested, child_node["children"])
        parts.append(f"{name} {child_sel}")
    return "{ " + " ".join(parts) + " }"


def build_selection(
    defn: "EntityDef",
    include_paths: list[str],
    fields: list[str] | None = None,
) -> str:
    """
    Build a GraphQL selection set from an EntityDef, include paths, and fields.

    fields         — scalar fields to return. Plain names select root scalars; dotted
                     names select nested fields (e.g. "tasks.title"), and
                     "<path>.*" selects all of that child's scalars. A dotted field
                     auto-includes its path. None/empty = all non-excluded root scalars.
    include_paths  — nested paths to traverse with default child fields (uid/code/name),
                     e.g. ["tasks", "tasks.assignee"].
    """
    excluded = set(defn.exclude_scalar_fields)
    root_explicit, nested_map = _split_fields(fields or [])
    if root_explicit:
        scalar_fields = [f for f in root_explicit if f in defn.scalar_fields and f not in excluded]
        if not scalar_fields:  # fallback if caller sent bad names
            scalar_fields = [f for f in defn.scalar_fields if f not in excluded]
    else:
        scalar_fields = [f for f in defn.scalar_fields if f not in excluded]
    tree = _build_tree(include_paths or [], nested_map)
    return _render_selection(scalar_fields, defn.nested, tree["children"])


def available_include_paths(defn: "EntityDef") -> list[str]:
    """
    Return all valid dot-notation include paths for an EntityDef.

    Depth-2 example for Project:
      ["tasks", "tasks.assignee", "tasks.labels", "milestones", "labels", "owner"]
    """
    paths: list[str] = []
    for nname, ndef in defn.nested.items():
        paths.append(nname)
        for cname, cdef in ndef.nested.items():
            paths.append(f"{nname}.{cname}")
            for gcname in cdef.nested:
                paths.append(f"{nname}.{cname}.{gcname}")
    return paths


def available_nested_fields(defn: "EntityDef") -> list[str]:
    """
    Return one "path: f1, f2, ..." line per nested path, so the tool description can
    advertise which child fields are selectable via dot-notation (e.g. tasks.title).
    Depth follows available_include_paths (up to 3 levels).
    """
    lines: list[str] = []

    def walk(prefix: str, nested: dict) -> None:
        for name, ndef in nested.items():
            path = f"{prefix}{name}"
            lines.append(f"{path}: {', '.join(ndef.fields)}")
            walk(f"{path}.", ndef.nested)

    walk("", defn.nested)
    return lines
