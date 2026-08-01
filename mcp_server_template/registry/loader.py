"""
Apply overrides to raw introspection dicts and convert to EntityDef objects.

overrides.json format (definitions/overrides.json):
  {
    "audit_entries":  { "skip": true },
    "projects":       { "description": "..." }
  }

Keys match the entity snake_name. Any key present in an override replaces
the corresponding value in the raw introspection dict.
No YAML, no extra dependencies — stdlib json only.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from mcp_server_template.registry.entity_def import EntityDef, MutableFieldDef, NestedDef

logger = logging.getLogger(__name__)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_typed_fields(data: list) -> list[MutableFieldDef]:
    result = []
    for item in (data or []):
        if isinstance(item, str):
            result.append(MutableFieldDef(name=item, python_type="str"))
        elif isinstance(item, dict):
            result.append(MutableFieldDef(
                name          = item["name"],
                python_type   = item.get("python_type", "str"),
                required      = item.get("required", False),
                description   = item.get("description", ""),
                item_fields   = item.get("item_fields", {}),
                item_required = item.get("item_required", []),
            ))
    return result


def _parse_nested(data: dict) -> dict[str, NestedDef]:
    result: dict[str, NestedDef] = {}
    for name, spec in (data or {}).items():
        result[name] = NestedDef(
            fields      = spec.get("fields", []),
            description = spec.get("description", ""),
            nested      = _parse_nested(spec.get("nested", {})),
        )
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def load_overrides(definitions_dir: Path) -> dict[str, dict]:
    """Load overrides.json from definitions_dir. Returns {} if not found."""
    path = definitions_dir / "overrides.json"
    if not path.exists():
        return {}
    try:
        overrides = json.loads(path.read_text(encoding="utf-8"))
        logger.debug("Loaded %d overrides from %s", len(overrides), path)
        return overrides
    except Exception as exc:
        logger.warning("Failed to load overrides.json: %s", exc)
        return {}


def raw_defs_to_entity_defs(
    raw_defs: dict[str, dict],
    overrides: dict[str, dict] | None = None,
) -> dict[str, EntityDef]:
    """
    Convert raw introspection dicts to EntityDef objects, applying overrides.

    overrides — optional dict keyed by snake_name with partial override fields.
    """
    overrides = overrides or {}
    result: dict[str, EntityDef] = {}

    for snake_name, raw in raw_defs.items():
        # Apply override (shallow merge — override values win)
        if snake_name in overrides:
            raw = {**raw, **overrides[snake_name]}

        # Skip flag — don't register any tools for this entity
        if raw.get("skip", False):
            logger.info("Skipping entity %s (skip=true in overrides)", snake_name)
            continue

        result[snake_name] = EntityDef(
            entity                   = raw.get("entity", snake_name),
            snake_name               = raw.get("snake_name", snake_name),
            snake_singular           = raw.get("snake_singular", snake_name),
            query                    = raw.get("query", ""),
            mutation_update          = raw.get("mutation_update"),
            mutation_create          = raw.get("mutation_create"),
            input_type_update        = raw.get("input_type_update"),
            input_type_create        = raw.get("input_type_create"),
            description              = raw.get("description", ""),
            schema_description       = raw.get("schema_description", ""),
            schema_description_update = raw.get("schema_description_update", ""),
            schema_description_create = raw.get("schema_description_create", ""),
            scalar_fields            = raw.get("scalar_fields", []),
            nested                   = _parse_nested(raw.get("nested", {})),
            mutable_fields           = raw.get("mutable_fields", []),
            create_required          = raw.get("create_required_fields", []),
            create_optional          = raw.get("create_optional_fields", []),
            exclude_scalar_fields    = raw.get("exclude_scalar_fields", []),
            action_name              = raw.get("action_name"),
            has_filter               = raw.get("has_filter", True),
            is_list                  = raw.get("is_list", True),
            skip                     = raw.get("skip", False),
            typed_mutable_fields     = _parse_typed_fields(raw.get("typed_mutable_fields", [])),
            typed_create_fields      = _parse_typed_fields(raw.get("typed_create_fields", [])),
            update_identifier_field  = raw.get("update_identifier_field", ""),
            update_data_field        = raw.get("update_data_field", ""),
            set_identifier_field     = raw.get("set_identifier_field", ""),
            set_collection_field     = raw.get("set_collection_field", ""),
            set_item_fields          = _parse_typed_fields(raw.get("set_item_fields", [])),
            set_collection_required  = raw.get("set_collection_required", False),
        )

    return result
