"""Entity map generator — compact data-model skeleton for the agent's system prompt.

Rendered from the introspected EntityDefs (the SAME objects tool generation uses), so it stays
in sync with the schema automatically. For each queryable entity it lists scalar fields plus one
level of nested relations (with their include path + fields). This gives the agent field awareness
BEFORE it calls a tool, so it can request e.g. `progress` in the first `get_projects` call instead
of fetching defaults and re-fetching (the double-call this map exists to prevent).

Lazy by nature: EntityDefs are built at schema-load time (startup for the file path, first
authenticated session for the dynamic path). Both paths call `set_entity_defs`; the entity-map
prompt renders from whatever is current when the client requests it.
"""
from __future__ import annotations

from mcp_server_template.registry.entity_def import EntityDef, NestedDef

# Process-level holder, populated by whichever schema-load path runs.
_ENTITY_DEFS: dict[str, EntityDef] = {}

# Keep the map compact: cap nested depth and fields-per-line.
_MAX_NESTED_FIELDS = 24


def set_entity_defs(defs: dict[str, EntityDef]) -> None:
    """Store the built EntityDefs so the entity-map prompt can render from them."""
    global _ENTITY_DEFS
    _ENTITY_DEFS = dict(defs or {})


def render_entity_map() -> str:
    """Render the current entity map (empty string until the schema has loaded)."""
    return build_entity_map(_ENTITY_DEFS)


def _fields_csv(fields: list[str] | None, limit: int = _MAX_NESTED_FIELDS) -> str:
    fields = fields or []
    if len(fields) > limit:
        return ", ".join(fields[:limit]) + ", …"
    return ", ".join(fields)


def build_entity_map(entity_defs: dict[str, EntityDef]) -> str:
    """Render queryable entities → scalar fields + one level of nested relations.

    Format (per entity):
        projects (get_projects): uid, code, name, active, priority, …
          - tasks  [include="tasks"]: uid, code, title, status, …
          - labels  [include="labels"]: uid, name, colour, …
    """
    lines: list[str] = []
    for _snake, d in entity_defs.items():
        if getattr(d, "skip", False) or not getattr(d, "query", ""):
            continue  # only queryable entities (those with a get_ tool)
        if not d.scalar_fields and not d.nested:
            continue  # nothing useful to show (e.g. get_current_workspace)
        lines.append(f"{d.snake_name} (get_{d.snake_name}): {_fields_csv(d.scalar_fields)}")
        for nested_name, nd in (d.nested or {}).items():
            fields = nd.fields if isinstance(nd, NestedDef) else (nd.get("fields") if isinstance(nd, dict) else [])
            lines.append(f'  - {nested_name}  [include="{nested_name}"]: {_fields_csv(fields)}')
    return "\n".join(lines)
