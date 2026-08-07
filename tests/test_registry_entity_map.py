"""Unit tests for registry/entity_map.py — pure rendering logic, no schema
file or FastMCP instance needed. See test_server_entity_map_resource.py for
the end-to-end check that this is actually wired to a fetchable resource.
"""
from __future__ import annotations

from mcp_server_template.registry.entity_def import EntityDef, NestedDef
from mcp_server_template.registry.entity_map import (
    build_entity_map,
    render_entity_map,
    set_entity_defs,
)


def _project_def(**overrides) -> EntityDef:
    base = dict(
        entity="Project",
        snake_name="projects",
        snake_singular="project",
        query="projects",
        scalar_fields=["uid", "code", "name", "active"],
        nested={"tasks": NestedDef(fields=["uid", "title", "status"])},
    )
    base.update(overrides)
    return EntityDef(**base)


def test_build_entity_map_renders_scalar_and_nested_fields():
    defs = {"project": _project_def()}
    out = build_entity_map(defs)

    assert "projects (get_projects): uid, code, name, active" in out
    assert '- tasks  [include="tasks"]: uid, title, status' in out


def test_build_entity_map_skips_entities_flagged_skip():
    defs = {"project": _project_def(skip=True)}
    assert build_entity_map(defs) == ""


def test_build_entity_map_skips_entities_with_no_query():
    # query="" means no get_ tool exists for it — nothing to show.
    defs = {"project": _project_def(query="")}
    assert build_entity_map(defs) == ""


def test_build_entity_map_skips_entities_with_nothing_useful():
    # No scalar fields and no nested relations — e.g. get_current_workspace.
    defs = {"org": _project_def(entity="Org", scalar_fields=[], nested={})}
    assert build_entity_map(defs) == ""


def test_build_entity_map_caps_long_field_lists():
    many_fields = [f"field{i}" for i in range(30)]
    defs = {"project": _project_def(scalar_fields=many_fields, nested={})}
    out = build_entity_map(defs)

    assert out.count(",") == 24  # 24 fields kept (_MAX_NESTED_FIELDS), 23 commas + the "…" join
    assert out.endswith("…")
    assert "field24" not in out  # past the cap, dropped


def test_build_entity_map_multiple_entities_each_get_a_line():
    defs = {
        "project": _project_def(),
        "task": _project_def(entity="Task", snake_name="tasks", query="tasks", nested={}),
    }
    out = build_entity_map(defs)
    lines = [l for l in out.split("\n") if l and not l.startswith("  ")]
    assert len(lines) == 2


def test_render_entity_map_reads_whatever_was_last_set():
    set_entity_defs({"project": _project_def()})
    assert "projects (get_projects)" in render_entity_map()

    set_entity_defs({})
    assert render_entity_map() == ""
