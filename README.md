# mcp-server-template

A production-shaped Model Context Protocol (MCP) server template, in
Python, with tools **auto-generated from a GraphQL schema** instead of
hand-written one at a time.

## What this is

Most public MCP examples stop at a hand-written tool that returns the
weather. This template ships the parts that make an MCP server survive
contact with a real schema:

- **Schema-driven tool generation** — introspect a GraphQL schema and
  generate `get_*` / `create_*` / `update_*` tools automatically, including
  nested relations exposed as dot-notation include paths.
- **Field awareness before the call** — an entity map rendered into the
  system prompt so the agent knows what fields exist *before* it calls a
  tool, instead of fetching defaults and re-fetching.
- **stdio and HTTP transports**, auth hook, middleware chain, tool registry.

`demo-schema.graphql` at the repo root is the schema this template ships
against — a generic project/task management domain, shaped specifically to
exercise every branch of the generator (list vs. single-object queries,
create/update pairs, update-only entities, read-only server fields,
list-of-object inputs, and a query hidden via an override).

## Status

Scaffolding only. Implementation in progress — see `docs/adr/` for design
decisions as they're made.

## License

MIT — see `LICENSE`.
