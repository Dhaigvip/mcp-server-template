from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NestedDef:
    fields: list[str]
    description: str = ""
    nested: dict[str, "NestedDef"] = field(default_factory=dict)


@dataclass
class MutableFieldDef:
    """Type info for one field in a mutation input type."""

    name: str
    python_type: str  # "str", "bool", "int", "float", "list"
    required: bool = False
    description: str = ""
    # For list fields: shape of each item {fieldName: pythonType}
    item_fields: dict[str, str] = field(default_factory=dict)
    # Required fields within each item (for list fields)
    item_required: list[str] = field(default_factory=list)


@dataclass
class EntityDef:
    entity: str
    snake_name: str  # plural  — get_{snake_name}
    snake_singular: str  # singular — update/create_{snake_singular}
    query: str  # GraphQL query field name; "" = no get_ tool
    mutation_update: str | None = None
    mutation_create: str | None = None
    input_type_update: str | None = None
    input_type_create: str | None = None
    description: str = ""  # manual override description (from overrides.json)
    schema_description: str = ""  # description from the GraphQL schema itself (query)
    schema_description_update: str = ""  # description from the GraphQL schema (update mutation)
    schema_description_create: str = ""  # description from the GraphQL schema (create mutation)
    scalar_fields: list[str] = field(default_factory=list)
    nested: dict[str, NestedDef] = field(default_factory=dict)
    # --- Flat field-list compatibility shape ---
    mutable_fields: list[str] = field(default_factory=list)
    create_required: list[str] = field(default_factory=list)
    create_optional: list[str] = field(default_factory=list)
    exclude_scalar_fields: list[str] = field(default_factory=list)
    action_name: str | None = None
    has_filter: bool = True
    # True when the GraphQL query field returns [Type] (a list); False for a bare Type
    # (e.g. currentWorkspace: Workspace). Default True preserves prior behavior for
    # every entity except the rare single-object query. See introspection.py's
    # `is_list` (computed via _unwrap_list_type) — this is where that value lands.
    is_list: bool = True
    # When true, skip this entity entirely — do not register any tools.
    # Use in overrides to hide queries that should not be exposed.
    skip: bool = False

    # --- Typed field definitions (supersede flat lists when present) ---
    # For update mutations, two shapes:
    #   FLAT:    Update<Entity>Input { uid: ID!, ...fields... }
    #            (what demo-schema.graphql uses — see ProjectUpdateInput)
    #   WRAPPER: Update<Entity>Input { <entity>Uid: String!, <entity>: <Entity>Fields! }
    #            → update_identifier_field = "<entity>Uid"
    #            → update_data_field        = "<entity>"
    #            → typed_mutable_fields     = fields from <Entity>Fields with types
    update_identifier_field: str = ""  # actual identifier field name in the input type
    update_data_field: str = ""  # nested data object field; "" = flat input

    # For set_* mutations (a parent identifier + a collection field it manages):
    #   Set<Entity>ItemsInput { <entity>Uid: String!, items: [Item!]! }
    #   → set_identifier_field = "<entity>Uid"
    #   → set_collection_field = "items"
    set_identifier_field: str = ""
    set_collection_field: str = ""
    set_collection_required: bool = (
        False  # True when schema marks the collection field as non-null (!)
    )

    # Typed mutable fields (used for update, create, and set tools when present)
    typed_mutable_fields: list[MutableFieldDef] = field(default_factory=list)
    typed_create_fields: list[MutableFieldDef] = field(default_factory=list)
    # For set_* tools: describes the shape of each item in the collection
    set_item_fields: list[MutableFieldDef] = field(default_factory=list)
