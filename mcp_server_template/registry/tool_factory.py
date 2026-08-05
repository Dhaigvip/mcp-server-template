"""Dynamic tool factory for entity-based MCP tools.

For each EntityDef, registers query and mutation tools with explicit input
schemas and descriptions so model calls stay well-formed.
"""

from __future__ import annotations

import inspect
import keyword
import logging
import time
from typing import Annotated, Any, get_args, get_origin

from fastmcp import FastMCP
from pydantic import Field

_CODES_DESC = 'Filter by code (exact match, e.g. ["PRJ-014", "TSK-1042"]).'
_UIDS_DESC = 'Filter by UID (exact match, e.g. ["abc-123-def"]).'
_NAMES_DESC = (
    'Filter by name (startsWith match — e.g. ["Launch"] matches "Launch Campaign", '
    '"Launch Readiness Review"). Pass the beginning of the name; partial prefixes work.'
)
_DESCRIPTIONS_DESC = (
    'Filter by description (contains match — e.g. ["migration"] matches "Database '
    'migration plan"). Searches anywhere in the description text.'
)
_ACTIVE_DESC = (
    "Whether the item is active (not archived/retired/revoked). "
    "Defaults to true (active items only, matching what the UI shows). "
    "Pass false for inactive items only. "
    "Pass null to include all items — use this when the user says 'include archived', "
    "'show all', or 'show inactive'."
)
_PRIORITY_DESC = "Filter by priority. Multiple values = OR logic (any match)."
_DUE_BEFORE_DESC = "Only items due on or before this ISO-8601 date (YYYY-MM-DD)."
_OWNER_UID_DESC = (
    "Filter by owner/assignee uid (username/email or uid). Multiple values = OR logic "
    "(any match)."
)

from mcp_server_template.graphql.builder import (
    build_query,
    build_scalar_query,
    build_mutation,
    build_direct_mutation,
    build_selection,
    available_include_paths,
    available_nested_fields,
)
from mcp_server_template.graphql.client import GraphQLClient
from mcp_server_template.registry.entity_def import EntityDef, MutableFieldDef

logger = logging.getLogger(__name__)

_REQUIRED = inspect.Parameter.empty

# ── Helpers ───────────────────────────────────────────────────────────────────


def _safe_name(f: str) -> str:
    """Append _ to Python reserved keywords so they can be used as parameter names.

    e.g. 'class' → 'class_', 'type' → 'type_'.  The original name is still used
    as the GraphQL field name; only the Python parameter is renamed.
    """
    return f + "_" if keyword.iskeyword(f) else f


def _default_fields(scalar_fields: list[str]) -> list[str]:
    """uid / code / name / active intersection — active always included when present."""
    preferred = ["uid", "code", "name", "active"]
    found = [f for f in preferred if f in scalar_fields]
    return found if found else scalar_fields[:3]


def _kw(name: str, annotation: Any, default: Any = _REQUIRED) -> inspect.Parameter:
    return inspect.Parameter(
        name,
        inspect.Parameter.KEYWORD_ONLY,
        default=default,
        annotation=annotation,
    )


def _ann(tp: Any, desc: str) -> Any:
    """Shorthand: Annotated[tp, Field(description=desc)]."""
    return Annotated[tp, Field(description=desc)]


_PYTHON_TYPES: dict[str, Any] = {
    "str": str,
    "bool": bool,
    "int": int,
    "float": float,
    "list": list,
    "enum": str,
}


def _field_annotation(f: MutableFieldDef) -> Any:
    """Convert a MutableFieldDef to an Annotated Python type for inspect.Parameter."""
    base = _PYTHON_TYPES.get(f.python_type, str)
    return base if f.required else (base | None)


def _mark_enums(data: dict, field_defs: list[MutableFieldDef]) -> dict:
    """Wrap enum values in {"__enum__": "VALUE"} so serializer doesn't quote them."""
    result = {}
    field_types = {f.name: f.python_type for f in field_defs}
    for k, v in data.items():
        if v is not None and field_types.get(k) == "enum":
            result[k] = {"__enum__": v}
        else:
            result[k] = v
    return result


def _strip_code(data: dict, tool_name: str) -> dict:
    """Remove 'code' from a mutation input dict — codes are server-assigned."""
    if "code" in data:
        logger.warning("tool.strip_code  %s  — 'code' field stripped (server-assigned)", tool_name)
        return {k: v for k, v in data.items() if k != "code"}
    return data


def _strip_code_list(items: list, tool_name: str) -> list:
    """Strip 'code' from each item dict in a collection."""
    return [_strip_code(item, tool_name) if isinstance(item, dict) else item for item in items]


def _item_shape(f: MutableFieldDef) -> str:
    """One-line description of the item shape for a list field, e.g. '{text (required), done}'."""
    if not f.item_fields:
        return "list of values"
    req = set(f.item_required)
    parts = [f"{n} (required)" if n in req else n for n in f.item_fields]
    return "{" + ", ".join(parts) + "}"


def _type_label(f: MutableFieldDef) -> str:
    """Type annotation for a field label — enum fields list their actual allowed
    values (e.g. "enum: TODO|IN_PROGRESS|BLOCKED|DONE") instead of the bare
    word "enum", which gives the model nothing to work with and produces
    plausible-but-wrong guesses (e.g. "COMPLETE" for a status field that only
    accepts DONE)."""
    if f.python_type == "enum" and f.enum_values:
        return f"enum: {'|'.join(f.enum_values)}"
    return f.python_type


def _field_label(f: MutableFieldDef) -> str:
    """Short label for a MutableFieldDef listing — e.g. 'name', 'active (bool)', 'checklist: list of {...}'."""
    if f.python_type == "list":
        return f"{f.name}: list of {_item_shape(f)}"
    if f.python_type != "str":
        return f"{f.name} ({_type_label(f)})"
    return f.name


def _build_filter_params() -> list[inspect.Parameter]:
    """Build the standard filter parameters for get_* tools.

    This is a fixed, hardcoded param set — not derived from introspecting
    each query's actual filter type. It matches demo-schema.graphql's
    ItemFilter, which every filterable query shares by design (see that
    schema's header comment). A backend with per-entity filter types would
    need this (and the matching GraphQL side, ItemFilter) reworked to
    introspect each query's real filter shape instead.
    """
    return [
        _kw("codes", _ann(list[str] | None, _CODES_DESC), None),
        _kw("uids", _ann(list[str] | None, _UIDS_DESC), None),
        _kw("names", _ann(list[str] | None, _NAMES_DESC), None),
        _kw("descriptions", _ann(list[str] | None, _DESCRIPTIONS_DESC), None),
        _kw("active", _ann(bool | None, _ACTIVE_DESC), True),
        _kw("priority", _ann(list[str] | None, _PRIORITY_DESC), None),
        _kw("dueBefore", _ann(str | None, _DUE_BEFORE_DESC), None),
        _kw("ownerUid", _ann(list[str] | None, _OWNER_UID_DESC), None),
    ]


def _assemble_filter(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Assemble a filter dict from flat tool params. Returns None if no filter fields are set."""
    filter_obj: dict[str, Any] = {}
    if kwargs.get("codes"):
        filter_obj["codes"] = kwargs["codes"]
    if kwargs.get("uids"):
        filter_obj["uids"] = kwargs["uids"]
    if kwargs.get("names"):
        filter_obj["names"] = kwargs["names"]
    if kwargs.get("descriptions"):
        filter_obj["descriptions"] = kwargs["descriptions"]
    if kwargs.get("active") is not None:
        filter_obj["active"] = kwargs["active"]
    if kwargs.get("priority"):
        # priority is a list of Priority enums — mark each one
        filter_obj["priority"] = [{"__enum__": p} for p in kwargs["priority"]]
    if kwargs.get("dueBefore"):
        filter_obj["dueBefore"] = kwargs["dueBefore"]
    if kwargs.get("ownerUid"):
        filter_obj["ownerUid"] = kwargs["ownerUid"]
    return filter_obj or None


# ── Description helpers ───────────────────────────────────────────────────────


def _query_description(defn: EntityDef) -> str:
    excluded = set(defn.exclude_scalar_fields)
    scalars = [f for f in defn.scalar_fields if f not in excluded]

    schema_note = defn.description or defn.schema_description

    desc = f"Retrieve {defn.snake_name.replace('_', ' ')}."
    if schema_note:
        desc += f"\n{schema_note}"
    return desc


# Cross-cutting rules every create/update/set tool must convey. Lives in the tool DESCRIPTION
# rather than the shared system prompt, so it appears exactly once per mutation tool instead of
# being repeated (and paid for) on every turn regardless of which tools are actually used. The
# agent binds every tool's full schema on every call, so this is a DRY/scoping choice,
# not an on-demand-loading or token-savings choice.
# Keep it tight: it repeats once per mutation tool's schema.
#
# Assumes every mutation returns {ok: Boolean!, message: String, <entity>: Type} — see
# _mutation_result() below. This is demo-schema.graphql's convention (its own header
# comment calls it out as deliberate, so one response handler can serve every generated
# mutation tool); a backend with a different result shape needs this text (and
# _mutation_result) adapted to match.
_MUTATION_CONTRACT = """
MUTATION RULES (all create/update/set tools):
- BATCH — tools with a collection-shaped parameter take ALL items in ONE call, e.g.
  create_items(collection=[{...}, {...}, {...}]). NEVER call the tool once per item — a single
  call creates/updates/sets many. Calling it per-item is wrong and wastes time.
- create_* assigns uid (and code, for entities that have one) server-side — do NOT pass
  `uid` or `code` on create. Pass `uid` only on update_*/set_* to identify the existing item.
- Read before write: get_* to obtain the uid first; pass only the fields you intend to change.
- Returns {"success": true/false, "message": "...", "<entity>": {"uid": "...", ...}}.
  Use the uid for follow-ups; NEVER show uids to the user.
- CHECK "success" — do not assume a call that returned without an error actually changed
  anything. If success is false, tell the user what "message" says went wrong — do not
  claim it's done.
- After a TRUE success, report concisely (e.g. "Done. Task updated.") — do not echo field
  values back verbatim unless asked.
""".rstrip()


def _mutation_selection(defn: EntityDef, include_paths: list[str], fields: list[str]) -> str:
    """Build the RESPONSE selection for a mutation call.

    Every generated mutation targets a `<Entity>Payload { ok, message, <entity> }`
    return type (see `_mutation_result()` and demo-schema.graphql's own mutation-
    payloads section, which calls this shape out as deliberate — one response
    handler serves every generated tool). `build_selection()` alone returns a
    selection shaped for the ENTITY type itself (used as-is for `get_*` query
    tools, which return the entity directly) — applying it unwrapped against a
    Payload type fails with "Cannot query field 'uid' on type '<Entity>Payload'"
    since `uid`/etc. live one level down, under the entity field. This wraps it:
    `{ ok message <snake_singular> <entity selection> }`.
    """
    entity_selection = build_selection(defn, include_paths, fields)
    return f"{{ ok message {defn.snake_singular} {entity_selection} }}"


def _mutation_result(result: dict) -> dict:
    """Build the tool's response dict from a mutation's raw GraphQL result.

    `success` reflects the payload's own `ok` field — client.execute() already raises on
    real GraphQL errors (see graphql/client.py), so reaching this point only means the
    request was well-formed; it does NOT mean the mutation had any effect. Silently
    reporting success=True regardless is exactly the failure mode this guards against:
    the agent telling the user "Done" while nothing changed.
    """
    if "ok" not in result:
        logger.warning(
            "tool.mutation_result  missing 'ok' key in mutation payload (keys=%s) — this "
            "generator assumes every mutation returns {ok, message, <entity>}. Defaulting "
            "success=True; a backend with a different result shape needs this adapted.",
            list(result.keys()),
        )
        return {"success": True, **result}
    return {"success": bool(result.get("ok")), **result}


def _mutation_description(defn: EntityDef, mutation_name: str, input_type: str) -> str:
    lines = [
        f"{mutation_name}. {_risk_label(mutation_name)}",
        "",
    ]

    # ── Wrapper pattern ───────────────────────────────────────────────────────
    if defn.update_identifier_field and defn.update_data_field:
        lines.append(f"PARAMETERS:")
        lines.append(
            f"  {defn.update_identifier_field} (required) — UID of the entity to update. Obtain via get_* first."
        )
        lines.append(
            f"  fields (dict) — pass the keys below INSIDE this dict; do NOT pass them as named parameters."
        )
        lines.append("")
        lines.append("KEYS INSIDE `fields`:")
        for f in defn.typed_mutable_fields:
            type_label = _type_label(f)
            if f.python_type == "list":
                type_label = f"list — {_item_shape(f)}"
            req = " (required)" if f.required else ""
            lines.append(f"  {f.name}: {type_label}{req}")
        lines.append("")
        lines.append(
            f'CALL SHAPE: {{{defn.update_identifier_field}: "<uid>", fields: {{ ...keys above... }}}}'
        )

    # ── Batch pattern (no parent identifier) ──────────────────────────────────
    elif not defn.set_identifier_field and defn.set_collection_field:
        is_create = mutation_name.startswith("create")
        action = "Creates" if is_create else "Updates"
        lines.append(f"{action} multiple items in a single call.")

    # ── Set/collection pattern (with parent identifier) ───────────────────────
    elif defn.set_identifier_field and defn.set_collection_field:
        is_create = mutation_name.startswith("create")
        if is_create:
            lines.append(f"Creates new items under the specified parent.")
        else:
            lines.append(
                f"Updates or creates items in {defn.set_collection_field}. Items not in the list are left unchanged."
            )

    # ── Typed flat pattern (e.g. ProjectUpdateInput { uid!, name, priority, ... } —
    #    what demo-schema.graphql's update_project/update_task actually use) ─
    elif defn.typed_mutable_fields:
        typed = defn.typed_mutable_fields
        required = [f for f in typed if f.required]
        optional = [f for f in typed if not f.required]
        lines.append("INPUT FIELDS (pass inside the `input` dict):")
        if required:
            req_parts = [_field_label(f) for f in required]
            lines.append(f"  REQUIRED: {', '.join(req_parts)}")
        if optional:
            opt_parts = [_field_label(f) for f in optional]
            lines.append(f"  OPTIONAL: {', '.join(opt_parts)}")

    # ── Flat named-parameter pattern (no typed fields metadata) ───────────
    else:
        child_names = [n for n in defn.nested]
        child_note = (
            f"\nCANNOT modify child collections via this mutation: {', '.join(child_names)}. "
            "Use dedicated create_*/update_* tools for those."
            if child_names
            else ""
        )
        required = ["uid"]
        optional = [f for f in defn.mutable_fields if f != "uid"]
        lines.append("INPUT FIELDS (pass each as a named parameter):")
        lines.append(f"  REQUIRED: {', '.join(required)}")
        lines.append(f"  OPTIONAL: {', '.join(optional) if optional else 'none'}")
        lines.append(child_note)
        lines.append("")
        lines.append(_MUTATION_CONTRACT)
        return "\n".join(lines)

    lines.append("")
    schema_note = defn.description or defn.schema_description_update
    if schema_note:
        lines.append(schema_note)
    lines.append("")
    lines.append(_MUTATION_CONTRACT)
    return "\n".join(lines)


def _risk_label(mutation_name: str) -> str:
    n = mutation_name.lower()
    if any(w in n for w in ("commit",)):
        return "Risk: HIGHEST — permanently commits changes. Cannot be undone."
    if any(w in n for w in ("delete", "remove", "purge")):
        return "Risk: HIGH — permanently deletes data."
    if any(w in n for w in ("inactivate", "deactivate", "disable")):
        return "Risk: MODERATE — deactivates the entity (reversible)."
    if any(w in n for w in ("activate", "enable")):
        return "Risk: MODERATE — activates the entity."
    if any(w in n for w in ("set", "update", "modify")):
        return "Risk: MODERATE — modifies data."
    return "Risk: MODERATE — creates data."


def _create_description(defn: EntityDef, mutation_name: str) -> str:
    lines = [
        f"{mutation_name}. {_risk_label(mutation_name)}",
        "",
    ]

    fields = defn.typed_create_fields
    if defn.set_identifier_field and defn.set_collection_field:
        # Parent + collection branch (e.g. createMilestoneTasks { milestoneUid, tasks: [...] })
        lines.append(f"Creates new items under the specified parent.")
    elif not defn.set_identifier_field and defn.set_collection_field:
        # Batch create (no parent) — e.g. createProjects { projects: [...] }
        lines.append("Creates multiple items in a single call.")
    elif fields:
        # Typed-create branch — signature is a single `input` dict.
        # The keys below go INSIDE that dict; passing them as named params will fail.
        lines.append("PARAMETERS:")
        lines.append(
            "  input (dict) — pass the keys below INSIDE this dict; do NOT pass them as named parameters."
        )
        lines.append("")
        lines.append("KEYS INSIDE `input`:")
        required = [f for f in fields if f.required]
        optional = [f for f in fields if not f.required]
        if required:
            lines.append(f"  REQUIRED: {', '.join(f.name for f in required)}")
        if optional:
            opt_parts = []
            for f in optional:
                if f.python_type == "list":
                    opt_parts.append(f"{f.name} (list — {_item_shape(f)})")
                elif f.python_type != "str":
                    opt_parts.append(f"{f.name} ({_type_label(f)})")
                else:
                    opt_parts.append(f.name)
            lines.append(f"  OPTIONAL: {', '.join(opt_parts)}")
        lines.append("")
        lines.append("CALL SHAPE: input={ ...keys above... }")
    else:
        # Direct-arg branch: signature takes named parameters.
        lines.append("INPUT FIELDS (pass each as a named parameter):")
        if defn.create_required:
            lines.append(f"  REQUIRED: {', '.join(defn.create_required)}")
        if defn.create_optional:
            lines.append(f"  OPTIONAL: {', '.join(defn.create_optional)}")
        if not defn.create_required and not defn.create_optional:
            lines.append("  (none)")

    schema_note = defn.description or defn.schema_description_create
    if schema_note:
        lines.append(schema_note)
    lines.append("")
    lines.append(_MUTATION_CONTRACT)
    return "\n".join(lines)


# ── registerQueryTool ─────────────────────────────────────────────────────────


def _register_query_tool(mcp: FastMCP, defn: EntityDef, client: GraphQLClient) -> str:
    tool_name = f"get_{defn.snake_name}"

    excluded = set(defn.exclude_scalar_fields)
    scalars = [f for f in defn.scalar_fields if f not in excluded]
    paths = available_include_paths(defn)
    is_scalar_return = not scalars

    desc = _query_description(defn)

    if is_scalar_return:
        # ── Scalar-returning query (no fields/include) ────────────────────────
        # Only add filter params when the query actually accepts one.
        if defn.has_filter:
            params = _build_filter_params()
        else:
            params = []  # no filter args (e.g. current_workspace)

        _defn, _client = defn, client

        async def _get_scalar(**kwargs: Any) -> Any:
            filter_obj = _assemble_filter(kwargs)
            t0 = time.monotonic()
            logger.info("tool.call  %-30s  filter=%s", tool_name, filter_obj)
            query = build_scalar_query(_defn.query, filter_obj)
            logger.info("tool.query %-30s  %s", tool_name, query)
            try:
                data = await _client.execute(query)
                result = data.get(_defn.query)
                logger.info(
                    "tool.result %-30s  ms=%d", tool_name, int((time.monotonic() - t0) * 1000)
                )
                return result
            except Exception as exc:
                logger.error("tool.error  %-30s  %s", tool_name, exc)
                raise

        _get_scalar.__name__ = tool_name
        _get_scalar.__qualname__ = tool_name
        _get_scalar.__signature__ = inspect.Signature(params)
        _get_scalar.__annotations__ = {p.name: p.annotation for p in params}
        _get_scalar.__doc__ = desc
        mcp.add_tool(_get_scalar)
        logger.debug(
            "tool.def  %s\n    desc: %s\n    params: %s",
            tool_name,
            desc.replace("\n", " | ")[:200],
            [p.name for p in params] or "(none)",
        )

    else:
        # ── Object-returning query (fields + include) ─────────────────────────
        defaults = _default_fields(scalars)
        nested_fields = available_nested_fields(defn)
        nested_note = (
            " For a field on an included child, use dot-notation: "
            "<path>.<field> (e.g. tasks.title), or <path>.* for all of that "
            "child's fields. Selectable nested fields — " + "; ".join(nested_fields) + "."
            if nested_fields
            else ""
        )
        filter_params = _build_filter_params() if defn.has_filter else []
        params = filter_params + [
            _kw(
                "fields",
                _ann(
                    list[str] | None,
                    f"Scalar fields to return. Available: {', '.join(scalars)}. "
                    f"Default (when omitted): {', '.join(defaults)}." + nested_note,
                ),
                None,
            ),
            _kw(
                "include",
                _ann(
                    list[str] | None,
                    (
                        f"Nested collections to traverse (dot-notation). Available: {', '.join(paths)}. "
                        "An included path returns uid/code/name by default; to choose its fields, "
                        "list them in `fields` as <path>.<field> (or <path>.* for all)."
                        if paths
                        else "This entity has no nested collections — omit this parameter."
                    ),
                ),
                None,
            ),
        ]

        _defn, _client, _defaults = defn, client, defaults
        # Identifier fields are ALWAYS selected (see _get) so every row is self-describing.
        _identifiers = [f for f in ("uid", "code", "name") if f in scalars]

        async def _get(**kwargs: Any) -> list[dict] | dict:
            filter_obj = _assemble_filter(kwargs)
            # Split root scalars from dotted nested-field requests. When the caller
            # gives only nested fields (e.g. ["tasks.title"]), still apply the
            # root defaults so the parent rows stay useful.
            raw_fields = kwargs.get("fields") or []
            root_fields = [f for f in raw_fields if "." not in f]
            nested_fields_req = [f for f in raw_fields if "." in f]
            # Always prepend identifier fields (uid/code/name) so rows are self-describing
            # even when the caller asks for only e.g. ["priority"] — otherwise they get
            # nameless rows and must waste a round-trip refetching just to get names.
            requested_root = root_fields or _defaults
            merged_root = list(dict.fromkeys([*_identifiers, *requested_root]))
            fields_ = merged_root + nested_fields_req
            include_ = kwargs.get("include") or []
            t0 = time.monotonic()
            logger.info(
                "tool.call  %-30s  filter=%s  fields=%s  include=%s",
                tool_name,
                filter_obj,
                fields_,
                include_,
            )
            selection = build_selection(_defn, include_, fields_)
            query = build_query(_defn.query, filter_obj, selection)
            logger.info("tool.query %-30s  %s", tool_name, query)
            try:
                data = await _client.execute(query)
                # The GraphQL field may return [Type] (most entities) or a bare Type
                # (e.g. currentWorkspace: Workspace) — defn.is_list (from
                # introspection's _unwrap_list_type) tells us which, so we don't force
                # single-object results into a list (or vice versa). See Known Pitfall #1.
                if _defn.is_list:
                    result: list[dict] | dict = data.get(_defn.query, [])
                    count = len(result)
                else:
                    result = data.get(_defn.query) or {}
                    count = 1 if result else 0
                logger.info(
                    "tool.result %-30s  count=%-4d  ms=%d",
                    tool_name,
                    count,
                    int((time.monotonic() - t0) * 1000),
                )
                # Query tools return the result directly (no success wrapper)
                return result
            except Exception as exc:
                logger.error("tool.error  %-30s  %s", tool_name, exc)
                raise

        _get.__name__ = tool_name
        _get.__qualname__ = tool_name
        _get.__signature__ = inspect.Signature(
            params, return_annotation=(list[dict] if defn.is_list else dict)
        )
        _get.__annotations__ = {p.name: p.annotation for p in params}
        _get.__doc__ = desc
        mcp.add_tool(_get)
        logger.debug(
            "tool.def  %s\n    desc: %s\n    params: %s",
            tool_name,
            desc.replace("\n", " | ")[:200],
            [p.name for p in params],
        )

    return tool_name


# ── registerMutationTool (input wrapper) ──────────────────────────────────────


def _register_mutation_tool(mcp: FastMCP, defn: EntityDef, client: GraphQLClient) -> str:
    tool_name = defn.action_name or f"update_{defn.snake_singular}"

    excluded = set(defn.exclude_scalar_fields)
    scalars = [f for f in defn.scalar_fields if f not in excluded]
    defaults = _default_fields(scalars)
    _mut = defn.mutation_update

    desc = _mutation_description(defn, _mut or tool_name, defn.input_type_update or "input")

    # ── Branch A: wrapper pattern ─────────────────────────────────────────────
    # e.g. UpdateProjectInput { projectUid: String!, project: ProjectFields! }
    if defn.update_identifier_field and defn.update_data_field:
        id_field = defn.update_identifier_field
        data_field = defn.update_data_field
        typed = defn.typed_mutable_fields

        # Single dict param — all mutable fields described inline
        fields_desc_parts = []
        for f in typed:
            if f.python_type == "list":
                fields_desc_parts.append(f"{f.name}: list of {_item_shape(f)}")
            elif f.python_type != "str":
                fields_desc_parts.append(f"{f.name} ({_type_label(f)})")
            else:
                fields_desc_parts.append(f.name)
        fields_desc = (
            "Fields to update. Pass only what you want to change. "
            "Available: " + ", ".join(fields_desc_parts) + "."
        )

        params = [
            _kw(id_field, _ann(str, "UID of the entity to update. Obtain via get_* first.")),
            _kw("fields", _ann(dict | None, fields_desc), None),
        ]

        _defn, _client, _defaults = defn, client, defaults

        async def _update_wrapper(**kwargs: Any) -> dict:
            id_val = kwargs[id_field]
            data_obj = _strip_code(kwargs.get("fields") or {}, tool_name)
            data_obj = _mark_enums(data_obj, typed)  # see _update_typed_flat for why
            input_ = {id_field: id_val, data_field: data_obj}
            t0 = time.monotonic()
            logger.info(
                "tool.call  %-30s  %s=%s  data_keys=%s",
                tool_name,
                id_field,
                id_val,
                list(data_obj.keys()),
            )
            selection = _mutation_selection(_defn, [], _defaults)
            mutation = build_mutation(_mut, input_, selection)
            logger.info("tool.query %-30s  %s", tool_name, mutation)
            try:
                data = await _client.execute(mutation)
                result = data.get(_mut, {})
                logger.info(
                    "tool.result %-30s  ms=%d", tool_name, int((time.monotonic() - t0) * 1000)
                )
                # success reflects the payload's own `ok` field — see _mutation_result.
                return _mutation_result(result)
            except Exception as exc:
                logger.error("tool.error  %-30s  %s", tool_name, exc)
                raise

        fn = _update_wrapper

    # ── Branch B: batch pattern (no parent identifier) ────────────────────────
    # e.g. CreateProjectsInput { projects: [CreateProjectInput!]! }
    elif not defn.set_identifier_field and defn.set_collection_field:
        coll_field = defn.set_collection_field
        item_shape_str = (
            "list of "
            + "{"
            + ", ".join(
                (f.name + " (required)" if f.required else f.name) for f in defn.set_item_fields
            )
            + "}"
            if defn.set_item_fields
            else "list"
        )

        is_create = (_mut or "").startswith("create")
        if is_create:
            coll_desc = f"Each item: {item_shape_str}."
        else:
            coll_desc = f"Each item must include uid. Item shape: {item_shape_str}."

        if defn.set_collection_required:
            coll_param = _kw(coll_field, _ann(list, coll_desc))
        else:
            coll_param = _kw(coll_field, _ann(list | None, coll_desc), None)
        params = [coll_param]

        _defn, _client, _defaults = defn, client, defaults

        async def _update_batch(**kwargs: Any) -> dict:
            collection = _strip_code_list(kwargs.get(coll_field) or [], tool_name)
            input_ = {coll_field: collection}
            t0 = time.monotonic()
            logger.info("tool.call  %-30s  items=%d", tool_name, len(collection))
            # For batch operations, always request the collection back with default fields
            selection = _mutation_selection(_defn, [coll_field], _defaults)
            mutation = build_mutation(_mut, input_, selection)
            logger.info("tool.query %-30s  %s", tool_name, mutation)
            try:
                data = await _client.execute(mutation)
                result = data.get(_mut, {})
                logger.info(
                    "tool.result %-30s  ms=%d", tool_name, int((time.monotonic() - t0) * 1000)
                )
                # success reflects the payload's own `ok` field — see _mutation_result.
                return _mutation_result(result)
            except Exception as exc:
                logger.error("tool.error  %-30s  %s", tool_name, exc)
                raise

        fn = _update_batch

    # ── Branch C: set/collection pattern (with parent identifier) ─────────────
    # e.g. SetProjectLabelsInput { projectUid: String!, labels: [Item!]! }
    elif defn.set_identifier_field and defn.set_collection_field:
        id_field = defn.set_identifier_field
        coll_field = defn.set_collection_field
        item_shape_str = (
            "list of "
            + "{"
            + ", ".join(
                (f.name + " (required)" if f.required else f.name) for f in defn.set_item_fields
            )
            + "}"
            if defn.set_item_fields
            else "list"
        )

        is_create = (_mut or "").startswith("create")
        coll_desc = f"Each item: {item_shape_str}."
        if defn.set_collection_required:
            coll_param = _kw(coll_field, _ann(list, coll_desc))
        else:
            coll_param = _kw(coll_field, _ann(list | None, coll_desc), None)
        params = [
            _kw(id_field, _ann(str, "UID of the parent entity. Obtain via get_* first.")),
            coll_param,
        ]

        _defn, _client, _defaults = defn, client, defaults

        async def _update_set(**kwargs: Any) -> dict:
            id_val = kwargs[id_field]
            collection = _strip_code_list(kwargs.get(coll_field) or [], tool_name)
            input_ = {id_field: id_val, coll_field: collection}
            t0 = time.monotonic()
            logger.info(
                "tool.call  %-30s  %s=%s  items=%d", tool_name, id_field, id_val, len(collection)
            )
            if is_create:
                # Server assigns uids to new children — request them back
                selection = _mutation_selection(_defn, [coll_field], ["uid"])
            else:
                selection = _mutation_selection(_defn, [], _defaults)
            mutation = build_mutation(_mut, input_, selection)
            logger.info("tool.query %-30s  %s", tool_name, mutation)
            try:
                data = await _client.execute(mutation)
                result = data.get(_mut, {})
                logger.info(
                    "tool.result %-30s  ms=%d", tool_name, int((time.monotonic() - t0) * 1000)
                )
                # success reflects the payload's own `ok` field — see _mutation_result.
                return _mutation_result(result)
            except Exception as exc:
                logger.error("tool.error  %-30s  %s", tool_name, exc)
                raise

        fn = _update_set

    # ── Branch D: typed flat input (e.g. update_project with ProjectUpdateInput) ─
    elif defn.typed_mutable_fields:
        typed = defn.typed_mutable_fields
        required = [f for f in typed if f.required]
        input_parts = [_field_label(f) for f in typed]
        req_names = [f.name for f in required]
        input_desc = (
            ("REQUIRED: " + ", ".join(req_names) + ". " if req_names else "")
            + "Available fields: "
            + ", ".join(input_parts)
            + "."
        )
        # input is required when any field is required, optional otherwise
        if required:
            params = [
                _kw("input", _ann(dict, input_desc)),
            ]
        else:
            params = [
                _kw("input", _ann(dict | None, input_desc), None),
            ]

        _defn, _client, _defaults = defn, client, defaults

        async def _update_typed_flat(**kwargs: Any) -> dict:
            input_ = _strip_code(kwargs.get("input") or {}, tool_name)
            # Without this, an enum value (e.g. status: "DONE") gets literal-
            # serialized as a quoted STRING, which GraphQL rejects for an enum
            # field ("Enum 'TaskStatus' cannot represent non-enum value").
            # _create_typed (the sibling create-path branch) already does this;
            # this update-path branch was missing it.
            input_ = _mark_enums(input_, typed)
            t0 = time.monotonic()
            logger.info("tool.call  %-30s  input_keys=%s", tool_name, list(input_.keys()))
            selection = _mutation_selection(_defn, [], _defaults)
            mutation = build_mutation(_mut, input_, selection)
            logger.info("tool.query %-30s  %s", tool_name, mutation)
            try:
                data = await _client.execute(mutation)
                result = data.get(_mut, {})
                logger.info(
                    "tool.result %-30s  ms=%d", tool_name, int((time.monotonic() - t0) * 1000)
                )
                # success reflects the payload's own `ok` field — see _mutation_result.
                return _mutation_result(result)
            except Exception as exc:
                logger.error("tool.error  %-30s  %s", tool_name, exc)
                raise

        fn = _update_typed_flat

    # ── Branch E: flat named-parameter pattern (no typed fields metadata) ─────
    else:
        mutable_optional = [f for f in defn.mutable_fields if f != "uid"]
        params = [
            _kw("uid", _ann(str, "UID of the entity to update. Obtain via get_* first.")),
            *[
                _kw(_safe_name(f), _ann(str | None, f"New value for {f}."), None)
                for f in mutable_optional
            ],
        ]

        _defn, _client, _defaults = defn, client, defaults

        async def _update_flat(**kwargs: Any) -> dict:
            uid_ = kwargs["uid"]
            input_ = {"uid": uid_}
            for f in _defn.mutable_fields:
                if f != "uid" and kwargs.get(_safe_name(f)) is not None:
                    input_[f] = kwargs[_safe_name(f)]
            input_ = _strip_code(input_, tool_name)
            t0 = time.monotonic()
            logger.info(
                "tool.call  %-30s  uid=%s  input_keys=%s", tool_name, uid_, list(input_.keys())
            )
            selection = _mutation_selection(_defn, [], _defaults)
            mutation = build_mutation(_mut, input_, selection)
            logger.info("tool.query %-30s  %s", tool_name, mutation)
            try:
                data = await _client.execute(mutation)
                result = data.get(_mut, {})
                logger.info(
                    "tool.result %-30s  ms=%d", tool_name, int((time.monotonic() - t0) * 1000)
                )
                # success reflects the payload's own `ok` field — see _mutation_result.
                return _mutation_result(result)
            except Exception as exc:
                logger.error("tool.error  %-30s  %s", tool_name, exc)
                raise

        fn = _update_flat

    fn.__name__ = tool_name
    fn.__qualname__ = tool_name
    fn.__signature__ = inspect.Signature(params, return_annotation=dict)
    fn.__annotations__ = {p.name: p.annotation for p in params}
    fn.__doc__ = desc
    mcp.add_tool(fn)
    logger.debug(
        "tool.def  %s\n    desc: %s\n    params: %s",
        tool_name,
        desc.replace("\n", " | ")[:200],
        [p.name for p in params],
    )
    return tool_name


# ── registerDirectMutationTool (no input wrapper) ─────────────────────────────


def _register_direct_mutation_tool(mcp: FastMCP, defn: EntityDef, client: GraphQLClient) -> str:
    tool_name = defn.action_name or f"create_{defn.snake_singular}"

    excluded = set(defn.exclude_scalar_fields)
    scalars = [f for f in defn.scalar_fields if f not in excluded]
    defaults = _default_fields(scalars)
    _mut = defn.mutation_create
    _has_input_wrapper = bool(defn.input_type_create)
    desc = _create_description(defn, _mut or tool_name)

    # ── Batch create path (collection with no parent identifier) ─────────────
    if not defn.set_identifier_field and defn.set_collection_field:
        coll_field = defn.set_collection_field
        item_shape_str = (
            "list of "
            + "{"
            + ", ".join(
                (f.name + " (required)" if f.required else f.name) for f in defn.set_item_fields
            )
            + "}"
            if defn.set_item_fields
            else "list"
        )

        coll_desc = f"List of items to create. Each item: {item_shape_str}."
        if defn.set_collection_required:
            coll_param = _kw(coll_field, _ann(list, coll_desc))
        else:
            coll_param = _kw(coll_field, _ann(list | None, coll_desc), None)
        params = [coll_param]

        _defn, _client, _defaults = defn, client, defaults

        async def _create_batch(**kwargs: Any) -> dict:
            collection = _strip_code_list(kwargs.get(coll_field) or [], tool_name)
            input_ = {coll_field: collection}
            t0 = time.monotonic()
            logger.info("tool.call  %-30s  items=%d", tool_name, len(collection))
            # For batch creates, request the collection back with default fields
            selection = _mutation_selection(_defn, [coll_field], _defaults)
            mutation = build_mutation(_mut, input_, selection)
            logger.info("tool.query %-30s  %s", tool_name, mutation)
            try:
                data = await _client.execute(mutation)
                result = data.get(_mut, {})
                logger.info(
                    "tool.result %-30s  ms=%d", tool_name, int((time.monotonic() - t0) * 1000)
                )
                # success reflects the payload's own `ok` field — see _mutation_result.
                return _mutation_result(result)
            except Exception as exc:
                logger.error("tool.error  %-30s  %s", tool_name, exc)
                raise

        fn = _create_batch

    # ── Create with parent identifier (e.g. CreateMilestoneTasksInput { milestoneUid!, tasks: [...] })
    elif defn.set_identifier_field and defn.set_collection_field:
        id_field = defn.set_identifier_field
        coll_field = defn.set_collection_field
        item_shape_str = (
            "list of "
            + "{"
            + ", ".join(
                (f.name + " (required)" if f.required else f.name) for f in defn.set_item_fields
            )
            + "}"
            if defn.set_item_fields
            else "list"
        )

        coll_desc = f"Each item: {item_shape_str}."
        if defn.set_collection_required:
            coll_param = _kw(coll_field, _ann(list, coll_desc))
        else:
            coll_param = _kw(coll_field, _ann(list | None, coll_desc), None)
        params = [
            _kw(id_field, _ann(str, "UID of the parent entity. Obtain via get_* first.")),
            coll_param,
        ]

        _defn, _client, _defaults = defn, client, defaults

        async def _create_with_parent(**kwargs: Any) -> dict:
            id_val = kwargs[id_field]
            collection = _strip_code_list(kwargs.get(coll_field) or [], tool_name)
            input_ = {id_field: id_val, coll_field: collection}
            t0 = time.monotonic()
            logger.info(
                "tool.call  %-30s  %s=%s  items=%d", tool_name, id_field, id_val, len(collection)
            )
            selection = _mutation_selection(_defn, [coll_field], ["uid"])
            mutation = build_mutation(_mut, input_, selection)
            logger.info("tool.query %-30s  %s", tool_name, mutation)
            try:
                data = await _client.execute(mutation)
                result = data.get(_mut, {})
                logger.info(
                    "tool.result %-30s  ms=%d", tool_name, int((time.monotonic() - t0) * 1000)
                )
                children = result.get(coll_field, [])
                return {coll_field: [{"uid": c.get("uid")} for c in children]}
            except Exception as exc:
                logger.error("tool.error  %-30s  %s", tool_name, exc)
                raise

        fn = _create_with_parent

    # ── Typed fields path — single input dict ────────────────────────────────
    elif defn.typed_create_fields:
        typed = defn.typed_create_fields
        required = [f for f in typed if f.required]
        optional = [f for f in typed if not f.required]

        # Build description for single input param
        input_parts = []
        for f in typed:
            if f.python_type == "list":
                input_parts.append(f"{f.name}: list of {_item_shape(f)}")
            elif f.python_type != "str":
                input_parts.append(f"{f.name} ({_type_label(f)})")
            else:
                input_parts.append(f.name)
        req_names = [f.name for f in required]
        input_desc = (
            ("REQUIRED: " + ", ".join(req_names) + ". " if req_names else "")
            + "Available fields: "
            + ", ".join(input_parts)
            + "."
        )

        params = [
            _kw("input", _ann(dict | None, input_desc), None),
        ]

        _defn, _client, _defaults = defn, client, defaults

        async def _create_typed(**kwargs: Any) -> dict:
            args_ = _strip_code(kwargs.get("input") or {}, tool_name)
            # Mark enum values so serializer doesn't quote them
            args_ = _mark_enums(args_, typed)
            t0 = time.monotonic()
            logger.info("tool.call  %-30s  args=%s", tool_name, list(args_.keys()))
            selection = _mutation_selection(_defn, [], _defaults)
            mutation = (
                build_mutation(_mut, args_, selection)
                if _has_input_wrapper
                else build_direct_mutation(_mut, args_, selection)
            )
            logger.info("tool.query %-30s  %s", tool_name, mutation)
            try:
                data = await _client.execute(mutation)
                result = data.get(_mut, {})
                logger.info(
                    "tool.result %-30s  ms=%d", tool_name, int((time.monotonic() - t0) * 1000)
                )
                # success reflects the payload's own `ok` field — see _mutation_result.
                return _mutation_result(result)
            except Exception as exc:
                logger.error("tool.error  %-30s  %s", tool_name, exc)
                raise

        fn = _create_typed

    # ── Flat-string action path (for action-style tools) ───────────────────────
    else:
        params = [
            *[_kw(_safe_name(f), _ann(str, f"Required input: {f}.")) for f in defn.create_required],
            *[
                _kw(_safe_name(f), _ann(str | None, f"Optional input: {f}."), None)
                for f in defn.create_optional
            ],
        ]

        _defn, _client, _defaults = defn, client, defaults

        async def _create_flat(**kwargs: Any) -> dict:
            args_ = {f: kwargs[_safe_name(f)] for f in _defn.create_required}
            args_.update(
                {
                    f: kwargs[_safe_name(f)]
                    for f in _defn.create_optional
                    if kwargs.get(_safe_name(f)) is not None
                }
            )
            args_ = _strip_code(args_, tool_name)
            t0 = time.monotonic()
            logger.info("tool.call  %-30s  args=%s", tool_name, list(args_.keys()))
            selection = _mutation_selection(_defn, [], _defaults)
            mutation = (
                build_mutation(_mut, args_, selection)
                if _has_input_wrapper
                else build_direct_mutation(_mut, args_, selection)
            )
            logger.info("tool.query %-30s  %s", tool_name, mutation)
            try:
                data = await _client.execute(mutation)
                result = data.get(_mut, {})
                logger.info(
                    "tool.result %-30s  ms=%d", tool_name, int((time.monotonic() - t0) * 1000)
                )
                # success reflects the payload's own `ok` field — see _mutation_result.
                return _mutation_result(result)
            except Exception as exc:
                logger.error("tool.error  %-30s  %s", tool_name, exc)
                raise

        fn = _create_flat

    fn.__name__ = tool_name
    fn.__qualname__ = tool_name
    fn.__signature__ = inspect.Signature(params, return_annotation=dict)
    fn.__annotations__ = {p.name: p.annotation for p in params}
    fn.__doc__ = desc
    mcp.add_tool(fn)
    logger.debug(
        "tool.def  %s\n    desc: %s\n    params: %s",
        tool_name,
        desc.replace("\n", " | ")[:200],
        [p.name for p in params],
    )
    return tool_name


# ── registerDomainTools entry point ───────────────────────────────────────────


def register_entity_tools(mcp: FastMCP, defn: EntityDef, client: GraphQLClient) -> list[str]:
    """Register get/update/create tools for one entity. Returns registered tool names."""
    registered: list[str] = []

    # Only register a query tool when a GraphQL query field exists.
    # Standalone action defs (e.g. a bare orphan mutation) have query="".
    if defn.query:
        registered.append(_register_query_tool(mcp, defn, client))

    if defn.mutation_update:
        registered.append(_register_mutation_tool(mcp, defn, client))

    if defn.mutation_create:
        registered.append(_register_direct_mutation_tool(mcp, defn, client))

    # Startup summary line of registered tools
    logger.info(
        "Tools ready: %s",
        "  |  ".join(registered),
    )
    return registered
