"""
Schema introspection → EntityDef raw dict.

Three strategies:

  1. File  — load a downloaded schema (JSON introspection result or SDL text).
             No live backend needed. Good for testing.
             Call: introspect_from_file(path)

  2. Live (GET)  — GET a dedicated schema endpoint, when the backend has one.
  3. Live (query) — standard GraphQL __schema introspection — universal fallback.
             Call: introspect_schema(client)

The mapping from a backend's own custom schema-endpoint format to EntityDef
format is an extension point, scaffolded in _map_from_custom_schema_endpoint()
— implement it for your backend if it has one; otherwise standard __schema
introspection (_map_from_introspection()) is the path every backend supports.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from graphql import build_schema, graphql_sync

from mcp_server_template.graphql.client import GraphQLClient

logger = logging.getLogger(__name__)

_INTROSPECTION_QUERY = """
{
  __schema {
    queryType {
      fields {
        name
        description
        type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
        args { name type { kind name ofType { kind name ofType { kind name ofType { kind name } } } } }
      }
    }
    mutationType {
      fields {
        name
        description
        type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
        args { name type { kind name ofType { kind name ofType { kind name ofType { kind name } } } } }
      }
    }
    types {
      name
      kind
      fields { name description type { kind name ofType { kind name ofType { kind name ofType { kind name } } } } }
      inputFields { name description type { kind name ofType { kind name ofType { kind name ofType { kind name } } } } }
    }
  }
}
"""


async def introspect_schema(client: GraphQLClient) -> dict[str, dict]:
    """
    Return a mapping of entity_snake_name -> raw dict (matches EntityDef fields).
    Try the backend's dedicated schema endpoint first, if configured; fall back
    to standard GraphQL introspection.
    """
    try:
        schema_data = await client.fetch_schema()
        result = _map_from_custom_schema_endpoint(schema_data)
        if result:
            return result
    except Exception as exc:
        logger.warning("GET schema endpoint failed (%s) — falling back to introspection query", exc)

    data = await client.execute(_INTROSPECTION_QUERY)
    return _map_from_introspection(data.get("__schema", {}))


def introspect_from_file(path: str | Path) -> dict[str, dict]:
    """
    Load a downloaded schema file and return entity_snake_name -> raw dict.

    Accepted formats:
      .json  — standard GraphQL introspection result:
                 { "__schema": { ... } }          (raw introspection)
                 { "data": { "__schema": { ... } } } (wrapped in GraphQL envelope)
               OR a backend's custom schema JSON (passed to
               _map_from_custom_schema_endpoint).

      .graphql / .gql / .sdl / .txt — SDL text, converted via graphql-core.

    Raises FileNotFoundError if the path does not exist.
    Raises ValueError if the file format cannot be determined or parsed.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Schema file not found: {p}")

    logger.info("Loading schema from file: %s", p)
    text = p.read_text(encoding="utf-8")

    suffix = p.suffix.lower()

    if suffix == ".json":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in schema file {p}: {exc}") from exc

        # Unwrap GraphQL envelope if present.
        if isinstance(raw, dict) and "data" in raw:
            raw = raw["data"]

        if isinstance(raw, dict) and "__schema" in raw:
            logger.info("Detected standard introspection format")
            return _map_from_introspection(raw["__schema"])

        # Assume a backend-native schema format.
        logger.info("Detected non-standard (backend-native) schema format")
        result = _map_from_custom_schema_endpoint(raw)
        if not result:
            logger.warning(
                "File loaded but _map_from_custom_schema_endpoint returned no "
                "entities. Implement the mapping for this backend's schema format."
            )
        return result

    if suffix in (".graphql", ".gql", ".sdl", ".txt"):
        return _map_from_sdl(text)

    raise ValueError(
        f"Unrecognised schema file extension '{suffix}'. "
        "Use .json (introspection result) or .graphql/.gql (SDL)."
    )


def _map_from_sdl(sdl_text: str) -> dict[str, dict]:
    """Convert SDL schema text to EntityDef dicts via graphql-core introspection."""
    schema = build_schema(sdl_text)
    result = graphql_sync(schema, _INTROSPECTION_QUERY)
    if result.errors:
        raise ValueError(f"SDL introspection failed: {result.errors[0]}")
    return _map_from_introspection((result.data or {}).get("__schema", {}))


# ── Custom schema-endpoint mapping (extension point) ──────────────────────────


def _map_from_custom_schema_endpoint(schema: Any) -> dict[str, dict]:
    """
    Map a backend's own dedicated schema-endpoint response to EntityDef raw
    dicts, when that response isn't a standard GraphQL introspection result.

    Every backend that has one of these shapes its response differently, so
    there's no generic mapping to provide here — implement this for your
    specific backend if it has a non-standard schema endpoint. Standard
    GraphQL introspection (_map_from_introspection) is the path that works
    without any backend-specific code, and is what demo-schema.graphql uses.
    """
    logger.warning(
        "_map_from_custom_schema_endpoint: not implemented — no backend-specific "
        "schema endpoint is configured for this template. Implement this function "
        "if your backend has one; otherwise standard introspection is used."
    )
    return {}


# ── Standard introspection mapping ───────────────────────────────────────────

_SCALARS = frozenset({"String", "Boolean", "Int", "Float", "ID"})

# GraphQL scalar → Python type name used in EntityDef dicts.
_SCALAR_TO_PYTHON: dict[str, str] = {
    "String": "str",
    "ID": "str",
    "Boolean": "bool",
    "Int": "int",
    "Float": "float",
}

# Max nesting depth for nested field classification.
_MAX_DEPTH = 2


def _map_from_introspection(schema: dict) -> dict[str, dict]:
    """
    Map standard GraphQL __schema introspection result to EntityDef raw dicts.

    Pass 1 — Entity detection:
      Every Query field whose return type is a list of an OBJECT type becomes one
      entity (get/update/create tools). update{X} / create{X} mutations are
      attached to the matching entity and marked as claimed.

      ASSUMPTION: mutations must be named exactly update{ReturnTypeName} /
      create{ReturnTypeName} to be recognized as belonging to an entity — e.g. a
      query returning [Project!]! is matched against mutations literally named
      updateProject/createProject (see f"update{ret_type_name}" below). This is
      not a GraphQL standard, it's a convention the backend's own schema has to
      follow. demo-schema.graphql was written to match it deliberately. A schema
      that names mutations differently (modifyProject, projectUpdate, ...) will
      have those mutations fall through to Pass 2 as standalone action tools
      instead of being attached to their entity's update_*/create_* tools.
      No per-entity override for this exists yet — would need one in Task 8's
      overrides mechanism to support other naming conventions.

    Pass 2 — Orphan mutations:
      Any mutation NOT claimed in pass 1 gets its own standalone action def
      (no query tool, tool named directly as snake_case(mutation_name)).
    """
    query_fields = {f["name"]: f for f in (schema.get("queryType") or {}).get("fields", [])}
    mutation_fields = {f["name"]: f for f in (schema.get("mutationType") or {}).get("fields", [])}
    types_by_name = {
        t["name"]: t for t in schema.get("types", []) if not t["name"].startswith("__")
    }

    result: dict[str, dict] = {}
    claimed: set[str] = set()

    # ── Pass 1: ALL query fields → get_* tools ───────────────────────────────
    for query_name, query_field in query_fields.items():
        snake = _camel_to_snake(query_name)
        singular = _to_singular(snake)

        ret_type_name = _unwrap_named(query_field["type"])
        is_list = _unwrap_list_type(query_field["type"]) is not None
        ret_type = types_by_name.get(ret_type_name or "", {})
        is_scalar_ret = (
            not ret_type_name
            or ret_type_name in _SCALARS
            or ret_type.get("kind") in ("SCALAR", "ENUM")
        )
        arg_names = {a["name"] for a in query_field.get("args", [])}
        has_filter = "filter" in arg_names

        if is_scalar_ret:
            scalar_fields = []
            nested_fields = {}
            mutation_update = None
            mutation_create = None
            input_update = None
            input_create = None
            typed_mutable = []
            typed_create = []
            update_id_field = ""
            update_data_fld = ""
            set_id_field = ""
            set_coll_field = ""
            set_item_fields = []
            set_coll_required = False
        else:
            scalar_fields, nested_fields = _classify_type_fields(
                ret_type, types_by_name, depth=0, seen=set()
            )

            if is_list:
                mutation_update = _find_mutation(mutation_fields, f"update{ret_type_name}")
                mutation_create = _find_mutation(mutation_fields, f"create{ret_type_name}")
            else:
                mutation_update = None
                mutation_create = None

            input_update = _input_type_for(mutation_fields, mutation_update)
            input_create = _input_type_for(mutation_fields, mutation_create)

            # Analyse update input structure (wrapper vs flat)
            (
                typed_mutable,
                update_id_field,
                update_data_fld,
                set_id_field,
                set_coll_field,
                set_item_fields,
                set_coll_required,
            ) = _analyse_mutation_input(types_by_name, input_update)

            # Analyse create input structure
            typed_create, _, _, _, _, _, _ = _analyse_mutation_input(types_by_name, input_create)

        if mutation_update:
            claimed.add(mutation_update)
        if mutation_create:
            claimed.add(mutation_create)

        # Collect schema descriptions per operation
        query_desc = query_field.get("description") or ""
        update_desc = (
            mutation_fields.get(mutation_update, {}).get("description", "")
            if mutation_update else ""
        )
        create_desc = (
            mutation_fields.get(mutation_create, {}).get("description", "")
            if mutation_create else ""
        )

        result[snake] = _build_entity_dict(
            snake,
            singular,
            query_name,
            mutation_update,
            mutation_create,
            input_update,
            input_create,
            scalar_fields,
            nested_fields,
            typed_mutable,
            typed_create,
            update_id_field,
            update_data_fld,
            set_id_field,
            set_coll_field,
            set_item_fields,
            set_coll_required,
            has_filter,
            schema_description_query=query_desc,
            schema_description_update=update_desc,
            schema_description_create=create_desc,
            is_list=is_list,
        )

    # ── Pass 2: orphan mutations → standalone action defs ─────────────────────
    # Collected below and reported as one summary warning after the loop —
    # see the ASSUMPTION note on Pass 1 above for why this happens.
    suspicious_orphans: list[str] = []

    for mut_name, mut_field in mutation_fields.items():
        if mut_name in claimed:
            continue

        if mut_name.startswith("create") or mut_name.startswith("update"):
            suspicious_orphans.append(mut_name)

        action_snake = _camel_to_snake(mut_name)
        has_input_arg = any(a["name"] == "input" for a in mut_field.get("args", []))

        ret_type_name = _unwrap_named(mut_field["type"])
        ret_type = types_by_name.get(ret_type_name or "", {})
        is_scalar_ret = (
            not ret_type_name
            or ret_type_name in _SCALARS
            or ret_type.get("kind") in ("SCALAR", "ENUM")
        )

        if is_scalar_ret:
            scalar_fields, nested_fields = [], {}
        else:
            scalar_fields, nested_fields = _classify_type_fields(
                ret_type, types_by_name, depth=0, seen=set()
            )

        if has_input_arg:
            input_type = _input_type_for(mutation_fields, mut_name)
            (
                typed_mutable,
                update_id_field,
                update_data_fld,
                set_id_field,
                set_coll_field,
                set_item_fields,
                set_coll_required,
            ) = _analyse_mutation_input(types_by_name, input_type)

            mut_desc = mut_field.get("description") or ""
            # Batch mutations: createProjects, updateProjects, etc.
            # Classify as create vs update based on name
            is_create_mutation = mut_name.startswith("create")
            result[action_snake] = _build_entity_dict(
                action_snake,
                action_snake,
                "",
                None if is_create_mutation else mut_name,  # mutation_update
                mut_name if is_create_mutation else None,  # mutation_create
                None if is_create_mutation else input_type,  # input_type_update
                input_type if is_create_mutation else None,  # input_type_create
                scalar_fields,
                nested_fields,
                [] if is_create_mutation else typed_mutable,  # typed_mutable_fields
                typed_mutable if is_create_mutation else [],  # typed_create_fields
                update_id_field,
                update_data_fld,
                set_id_field,
                set_coll_field,
                set_item_fields,
                set_coll_required,
                False,
                action_name=action_snake,
                schema_description_update="" if is_create_mutation else mut_desc,
                schema_description_create=mut_desc if is_create_mutation else "",
            )
        else:
            # Direct-arg mutation (no `input` wrapper — args passed individually)
            req_args = [a["name"] for a in mut_field.get("args", []) if _is_non_null(a["type"])]
            opt_args = [a["name"] for a in mut_field.get("args", []) if not _is_non_null(a["type"])]

            mut_desc = mut_field.get("description") or ""
            result[action_snake] = {
                "entity": action_snake,
                "snake_name": action_snake,
                "snake_singular": action_snake,
                "query": "",
                "mutation_update": None,
                "mutation_create": mut_name,
                "input_type_update": None,
                "input_type_create": None,
                "scalar_fields": scalar_fields,
                "nested": nested_fields,
                "mutable_fields": [],
                "typed_mutable_fields": [],
                "create_required_fields": req_args,
                "create_optional_fields": opt_args,
                "typed_create_fields": [],
                "update_identifier_field": "",
                "update_data_field": "",
                "set_identifier_field": "",
                "set_collection_field": "",
                "set_item_fields": [],
                "schema_description": "",
                "schema_description_update": mut_desc,
                "schema_description_create": mut_desc,
                "action_name": action_snake,
                "has_filter": False,
            }

        logger.debug("Orphan mutation '%s' → standalone action tool '%s'", mut_name, action_snake)

    if suspicious_orphans:
        logger.warning(
            "%d mutation(s) named create*/update* were NOT attached to an entity "
            "and became standalone action tools instead: %s. This generator only "
            "auto-attaches mutations named exactly update{ReturnTypeName} / "
            "create{ReturnTypeName} for some query's return type (e.g. updateProject "
            "for a query returning [Project!]!) — see the ASSUMPTION note in "
            "_map_from_introspection's docstring. If any of these were meant to be "
            "an entity's update_*/create_* tool, check the naming matches exactly; "
            "if they're genuinely standalone actions, ignore this warning.",
            len(suspicious_orphans),
            suspicious_orphans,
        )

    return result


# ── Input type analysis ───────────────────────────────────────────────────────


def _analyse_mutation_input(
    types_by_name: dict,
    input_type: str | None,
) -> tuple[list[dict], str, str, str, str, list[dict], bool]:
    """
    Analyse a mutation input type and return:
      (typed_fields, update_identifier_field, update_data_field,
       set_identifier_field, set_collection_field, set_item_fields, set_collection_required)

    Patterns detected:

    WRAPPER pattern — e.g. UpdateProjectInput { projectUid: String!, project: ProjectFields! }
      One required scalar (identifier) + one required INPUT_OBJECT (data).
      → Flatten data type fields as typed_fields.
      → update_identifier_field = "projectUid", update_data_field = "project".

    BATCH pattern — e.g. CreateProjectsInput { projects: [CreateProjectInput!]! }
      Single required list field with INPUT_OBJECT items (no identifier field).
      → set_collection_field = "projects", set_item_fields = fields of the item type.
      → set_identifier_field = "" (empty, signals batch mode without parent identifier).

    SET pattern — e.g. SetProjectLabelsInput { projectUid: String!, labels: [Item!]! }
      One required scalar (identifier) + one required list of INPUT_OBJECT.
      → set_identifier_field = "projectUid", set_collection_field = "labels".
      → set_item_fields = fields of the item type.

    FLAT pattern — all other input types (e.g. ProjectUpdateInput/ProjectCreateInput
      in demo-schema.graphql).
      → All fields as typed_fields, update_identifier_field = "" (tool factory uses "uid").
    """
    empty = ([], "", "", "", "", [], False)
    if not input_type or input_type not in types_by_name:
        return empty

    input_fields = types_by_name[input_type].get("inputFields", [])
    if not input_fields:
        return empty

    # Classify each input field
    scalars = []  # (name, required)
    objects = []  # (name, type_name, required, is_list)

    for f in input_fields:
        fname = f["name"]
        ftype = f["type"]
        required = _is_non_null(ftype)
        tname = _unwrap_named(ftype)
        is_list = _unwrap_list_type(ftype) is not None
        item_tname = _unwrap_list_type(ftype) if is_list else None

        ref = types_by_name.get(tname or "", {})

        if tname in _SCALARS or ref.get("kind") in ("SCALAR", "ENUM"):
            scalars.append((fname, required))
        else:
            actual_type = item_tname if is_list else tname
            objects.append((fname, actual_type or tname, required, is_list))

    # ── WRAPPER pattern ───────────────────────────────────────────────────────
    # Exactly one required scalar (the UID) + exactly one required INPUT_OBJECT (the data wrapper)
    req_scalars = [s for s in scalars if s[1]]
    req_objects = [o for o in objects if o[2] and not o[3]]  # required, not a list

    if len(req_scalars) == 1 and len(req_objects) == 1 and len(input_fields) == 2:
        id_field = req_scalars[0][0]
        data_fname = req_objects[0][0]
        data_tname = req_objects[0][1]
        data_type = types_by_name.get(data_tname or "", {})

        if data_type.get("kind") == "INPUT_OBJECT":
            typed_fields = _typed_fields_from_input_type(data_type, types_by_name)
            return (typed_fields, id_field, data_fname, "", "", [], False)

    # ── BATCH pattern ─────────────────────────────────────────────────────────
    # Single required list field with INPUT_OBJECT items (e.g. CreateProjectsInput { projects: [CreateProjectInput!]! })
    # Used for batch create/update operations
    req_list_objects = [o for o in objects if o[3]]  # is_list  (name, type, required, is_list)

    if len(scalars) == 0 and len(req_list_objects) == 1 and len(input_fields) == 1:
        coll_fname = req_list_objects[0][0]
        coll_required = req_list_objects[0][2]
        item_tname = req_list_objects[0][1]
        item_type = types_by_name.get(item_tname or "", {})

        if item_type.get("kind") == "INPUT_OBJECT":
            item_fields = _typed_fields_from_input_type(item_type, types_by_name)
            # Return as set pattern with empty identifier (signals batch mode)
            return ([], "", "", "", coll_fname, item_fields, coll_required)

    # ── SET pattern ───────────────────────────────────────────────────────────
    # Exactly one required scalar (the UID) + one list of INPUT_OBJECT (the collection)

    if len(req_scalars) == 1 and len(req_list_objects) == 1 and len(input_fields) == 2:
        id_field = req_scalars[0][0]
        coll_fname = req_list_objects[0][0]
        coll_required = req_list_objects[0][2]  # True when schema marks collection as non-null (!)
        item_tname = req_list_objects[0][1]
        item_type = types_by_name.get(item_tname or "", {})

        if item_type.get("kind") == "INPUT_OBJECT":
            item_fields = _typed_fields_from_input_type(item_type, types_by_name)
            return ([], "", "", id_field, coll_fname, item_fields, coll_required)

    # ── FLAT pattern ─────────────────────────────────────────────────────────
    input_type_def = types_by_name[input_type]
    typed_fields = _typed_fields_from_input_type(input_type_def, types_by_name)
    return (typed_fields, "", "", "", "", [], False)


def _typed_fields_from_input_type(
    input_type_def: dict,
    types_by_name: dict,
) -> list[dict]:
    """
    Return a list of field dicts for a GraphQL INPUT_OBJECT type.

    Each dict:
      name          — field name
      python_type   — "str" | "bool" | "int" | "float" | "list"
      required      — bool
      description   — "" (filled in by overrides if needed)
      item_fields   — {fieldName: pythonType} for list fields (empty for scalars)
      item_required — [fieldName, ...] for required fields within each item
    """
    result = []
    for f in input_type_def.get("inputFields", []):
        fname = f["name"]
        ftype = f["type"]
        required = _is_non_null(ftype)
        is_list = _unwrap_list_type(ftype) is not None
        tname = _unwrap_named(ftype)
        item_tname = _unwrap_list_type(ftype) if is_list else None

        if is_list:
            # List field — determine item type
            item_type_def = types_by_name.get(item_tname or "", {})
            if item_type_def.get("kind") == "INPUT_OBJECT":
                item_fields_raw = _typed_fields_from_input_type(item_type_def, types_by_name)
                item_fields = {f2["name"]: f2["python_type"] for f2 in item_fields_raw}
                item_required = [f2["name"] for f2 in item_fields_raw if f2["required"]]
            else:
                item_fields = {}
                item_required = []
            result.append(
                {
                    "name": fname,
                    "python_type": "list",
                    "required": required,
                    "description": "",
                    "item_fields": item_fields,
                    "item_required": item_required,
                }
            )
        else:
            ref = types_by_name.get(tname or "", {})
            python_type = _SCALAR_TO_PYTHON.get(tname or "", "str")

            if ref.get("kind") == "INPUT_OBJECT":
                # Nested input object (not a list) — treat as opaque dict for now
                python_type = "dict"
            elif ref.get("kind") == "ENUM":
                # Mark as enum so serializer doesn't quote it
                python_type = "enum"

            result.append(
                {
                    "name": fname,
                    "python_type": python_type,
                    "required": required,
                    "description": "",
                    "item_fields": {},
                    "item_required": [],
                }
            )

    return result


def _build_entity_dict(
    snake: str,
    singular: str,
    query_name: str,
    mutation_update: str | None,
    mutation_create: str | None,
    input_update: str | None,
    input_create: str | None,
    scalar_fields: list,
    nested_fields: dict,
    typed_mutable: list,
    typed_create: list,
    update_id_field: str,
    update_data_fld: str,
    set_id_field: str,
    set_coll_field: str,
    set_item_fields: list,
    set_coll_required: bool,
    has_filter: bool,
    action_name: str | None = None,
    schema_description_query: str = "",
    schema_description_update: str = "",
    schema_description_create: str = "",
    is_list: bool = True,
) -> dict:
    """Assemble the full raw dict for one entity."""
    mutable_fields = [f["name"] for f in typed_mutable]
    create_required = [f["name"] for f in typed_create if f["required"]]
    create_optional = [f["name"] for f in typed_create if not f["required"]]

    d: dict = {
        "entity": snake,
        "snake_name": snake,
        "snake_singular": singular,
        "query": query_name,
        "mutation_update": mutation_update,
        "mutation_create": mutation_create,
        "input_type_update": input_update,
        "input_type_create": input_create,
        "scalar_fields": scalar_fields,
        "nested": nested_fields,
        "mutable_fields": mutable_fields,
        "create_required_fields": create_required,
        "create_optional_fields": create_optional,
        "typed_mutable_fields": typed_mutable,
        "typed_create_fields": typed_create,
        "update_identifier_field": update_id_field,
        "update_data_field": update_data_fld,
        "set_identifier_field": set_id_field,
        "set_collection_field": set_coll_field,
        "set_item_fields": set_item_fields,
        "set_collection_required": set_coll_required,
        "has_filter": has_filter,
        "schema_description": schema_description_query,
        "schema_description_update": schema_description_update,
        "schema_description_create": schema_description_create,
        "is_list": is_list,
    }
    if action_name:
        d["action_name"] = action_name
    return d


# ── Type classifier (for query return types) ──────────────────────────────────


def _classify_type_fields(
    obj_type: dict,
    types_by_name: dict[str, dict],
    depth: int,
    seen: set[str],
) -> tuple[list[str], dict]:
    scalars: list[str] = []
    nested: dict = {}

    for f in obj_type.get("fields") or []:
        fname = f["name"]
        tname = _unwrap_named(f["type"])
        if not tname:
            continue

        ref = types_by_name.get(tname, {})

        if tname in _SCALARS or ref.get("kind") in ("SCALAR", "ENUM"):
            scalars.append(fname)
        elif ref.get("kind") == "OBJECT":
            if depth >= _MAX_DEPTH or tname in seen:
                continue
            child_scalars, child_nested = _classify_type_fields(
                ref, types_by_name, depth + 1, seen | {tname}
            )
            entry: dict = {"fields": child_scalars}
            if child_nested:
                entry["nested"] = child_nested
            nested[fname] = entry

    return scalars, nested


# ── Helpers ───────────────────────────────────────────────────────────────────


def _unwrap_list_type(type_ref: dict) -> str | None:
    """Return the item type name if type_ref is a list, else None."""
    kind = type_ref.get("kind")
    inner = type_ref.get("ofType")
    if kind == "LIST":
        if inner:
            item = inner if inner.get("kind") != "NON_NULL" else inner.get("ofType", {})
            return item.get("name")
    if kind == "NON_NULL" and inner:
        return _unwrap_list_type(inner)
    return None


def _unwrap_named(type_ref: dict) -> str | None:
    """Unwrap all LIST/NON_NULL wrappers and return the innermost named type."""
    name = type_ref.get("name")
    inner = type_ref.get("ofType")
    if name:
        return name
    if inner:
        return _unwrap_named(inner)
    return None


def _is_non_null(type_ref: dict) -> bool:
    return type_ref.get("kind") == "NON_NULL"


def _find_mutation(mutation_fields: dict, name: str) -> str | None:
    return name if name in mutation_fields else None


def _input_type_for(mutation_fields: dict, mutation_name: str | None) -> str | None:
    if not mutation_name:
        return None
    for arg in mutation_fields.get(mutation_name, {}).get("args", []):
        if arg["name"] == "input":
            return _unwrap_named(arg["type"])
    return None


def _camel_to_snake(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


def _to_singular(plural: str) -> str:
    if plural.endswith("_ies"):
        return plural[:-4] + "_y"
    if plural.endswith("ies"):
        return plural[:-3] + "y"
    if plural.endswith("ses"):
        return plural[:-2]
    if plural.endswith("s") and not plural.endswith("ss"):
        return plural[:-1]
    return plural
